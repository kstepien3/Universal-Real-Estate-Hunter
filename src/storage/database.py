import asyncio
import os
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import event, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings

from .models import (
    Base,
    GeocacheModel,
    ListingModel,
    PriceHistoryModel,
    SpatialCacheModel,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_sqlite_write_lock: asyncio.Lock | None = None


def get_sqlite_write_lock() -> asyncio.Lock:
    global _sqlite_write_lock
    if _sqlite_write_lock is None:
        _sqlite_write_lock = asyncio.Lock()
    return _sqlite_write_lock


def is_sqlite_lock_error(exc: BaseException) -> bool:
    """Checks if an exception is a transient SQLite lock, busy, or readonly contention error."""
    msg = str(exc).lower()
    return any(
        k in msg
        for k in (
            "database is locked",
            "readonly database",
            "attempt to write a readonly database",
            "database is busy",
            "disk i/o error",
            "cantlock",
            "locked",
            "busy",
        )
    )


async def safe_commit(session: AsyncSession, max_retries: int = 7, initial_backoff: float = 0.25) -> None:
    """Commit with retry and write-lock protection for SQLite against locks & readonly contention."""
    is_sqlite = (
        session.bind.dialect.name == "sqlite"
        if session.bind
        else ("sqlite" in getattr(settings, "DATABASE_URL", "sqlite"))
    )
    if not is_sqlite:
        await session.commit()
        return

    write_lock = get_sqlite_write_lock()
    async with write_lock:
        for attempt in range(max_retries):
            try:
                await session.commit()
                return
            except Exception as exc:
                if is_sqlite_lock_error(exc) and attempt < max_retries - 1:
                    backoff = min(5.0, initial_backoff * (1.8**attempt))
                    logger.warning(
                        f"[Database] SQLite lock/readonly contention ({exc}) during commit, "
                        f"retrying in {backoff:.2f}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    await asyncio.sleep(backoff)
                else:
                    raise


def _migrate_legacy_root_db(db_path_str: str) -> str:
    """One-time move of the historical ``listings.db`` from repo root to ``data/``.

    Returns the (possibly unchanged) resolved DB path string.
    """
    try:
        p_new = Path(db_path_str)
        if not p_new.is_absolute():
            p_new = Path.cwd() / p_new
        if p_new.parent.name == "data" and p_new.name == "listings.db":
            legacy = p_new.parent.parent / "listings.db"
            if legacy.exists() and not p_new.exists():
                p_new.parent.mkdir(parents=True, exist_ok=True)
                try:
                    legacy.rename(p_new)
                    logger.info(f"[Database] Przeniesiono legacy DB {legacy} -> {p_new}")
                except OSError:
                    import shutil

                    shutil.copy2(legacy, p_new)
                    logger.info(f"[Database] Skopiowano legacy DB {legacy} -> {p_new}")
                for ext in ("-wal", "-shm"):
                    legacy_aux = Path(str(legacy) + ext)
                    new_aux = Path(str(p_new) + ext)
                    if legacy_aux.exists() and not new_aux.exists():
                        try:
                            legacy_aux.rename(new_aux)
                        except OSError:
                            pass
    except Exception as e:
        logger.debug(f"[Database] Legacy DB migration note: {e}")
    return db_path_str


def verify_and_repair_sqlite_permissions(db_path_str: str) -> None:
    """
    Proactively checks SQLite directory/file writability.
    Uses least-privilege modes (755/644) and never 777. If healing fails,
    logs an actionable diagnostic instead of silently broadening permissions.
    """
    db_path_str = _migrate_legacy_root_db(db_path_str)
    p_db = Path(db_path_str).resolve()
    directory = p_db.parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"[Database] Nie można utworzyć katalogu bazy '{directory}': {e}")
        return

    # Proactive write test to confirm WAL/SHM file creation is allowed
    test_file = directory / f".write_test_{os.getpid()}"
    can_write = False
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        can_write = True
    except (PermissionError, OSError) as e:
        logger.warning(f"[Database] Brak zapisu w '{directory}': {e}")

    if not can_write:
        logger.critical(
            f"[Database] 🚨 BŁĄD UPRAWNIEŃ: Katalog '{directory}' nie zezwala na zapis dla tego procesu!\n"
            f"SQLite nie będzie mógł zapisać bazy danych ani utworzyć plików WAL/SHM.\n"
            f"Rozwiązanie na maszynie hosta: upewnij się, że właściciel katalogu to bieżący "
            f"użytkownik/kontener (chown), zamiast chmod 777."
        )

    if p_db.exists():
        try:
            with p_db.open("r+b"):
                pass
        except (PermissionError, OSError):
            logger.error(
                f"[Database] Plik bazy '{p_db}' jest tylko do odczytu. "
                f"Rozwiązanie: chown do bieżącego użytkownika lub chmod 644."
            )


def is_postgres_available(host: str = "127.0.0.1", port: int = 5432, timeout_s: float = 0.5) -> bool:
    """Fast check if a PostgreSQL server port is actively listening and reachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except (OSError, TimeoutError):
        return False


def resolve_database_url() -> str:
    """Resolves active database URL.

    If DATABASE_URL is explicitly set to non-default or non-sqlite, returns it as-is.
    If default sqlite is configured, checks whether a PostgreSQL service is running
    locally (e.g. from docker compose up -d db) and automatically upgrades to it,
    enabling zero-config auto-migration from SQLite to Postgres.
    """
    configured_url = getattr(settings, "DATABASE_URL", "")
    # Only auto-upgrade if DATABASE_URL is the application's out-of-the-box default
    # ('sqlite+aiosqlite:///data/listings.db') and not running inside automated test isolation.
    default_urls = (
        "sqlite+aiosqlite:///data/listings.db",
        "sqlite+aiosqlite:///./data/listings.db",
        "sqlite+aiosqlite:////app/data/listings.db",
    )
    if configured_url not in default_urls:
        return configured_url

    if "PYTEST_CURRENT_TEST" in os.environ:
        return configured_url

    # Check if DATABASE_URL was explicitly provided in os.environ (user explicitly wants sqlite)
    if os.environ.get("DATABASE_URL") and "sqlite" in os.environ["DATABASE_URL"]:
        return configured_url

    # Check local PostgreSQL connection endpoint (default port 5432)
    pg_url = "postgresql+asyncpg://estate:estate_hunter_secret_pass@127.0.0.1:5432/estate_hunter"
    if is_postgres_available("127.0.0.1", 5432):
        logger.info(
            f"[Database] Wykryto aktywny serwer PostgreSQL na 127.0.0.1:5432. Przełączanie z SQLite na {pg_url}."
        )
        settings.DATABASE_URL = pg_url
        return pg_url

    return configured_url


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        db_url = resolve_database_url()
        is_sqlite = "sqlite" in db_url
        engine_kwargs: dict = {
            "echo": False,
            "future": True,
        }

        # For sqlite ensure directory exists if path is provided
        if is_sqlite:
            if db_url.startswith("sqlite+aiosqlite:///"):
                path = db_url.replace("sqlite+aiosqlite:///", "")
                verify_and_repair_sqlite_permissions(path)
            engine_kwargs["connect_args"] = {
                "timeout": 60.0,
            }
        else:
            # PostgreSQL / asyncpg connection pool tuning
            engine_kwargs["pool_pre_ping"] = True
            engine_kwargs["pool_size"] = 10
            engine_kwargs["max_overflow"] = 20

        _engine = create_async_engine(db_url, **engine_kwargs)

        if is_sqlite:

            @event.listens_for(_engine.sync_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA busy_timeout=60000")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA journal_mode=WAL")
                    row = cursor.fetchone()
                    mode = str(row[0]).upper() if row else ""
                    if mode != "WAL":
                        logger.warning(
                            f"[Database] WAL journal mode unsupported by filesystem ({mode}), falling back to TRUNCATE."
                        )
                        cursor.execute("PRAGMA journal_mode=TRUNCATE")
                except Exception as e:
                    logger.warning(f"[Database] SQLite pragma warning: {e}")
                finally:
                    cursor.close()

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        engine = get_engine()
        _sessionmaker = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager providing an isolated session with write-lock & retry protection for SQLite."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await safe_commit(session)
        except Exception:
            await session.rollback()
            raise


LISTINGS_SCHEMA_MIGRATIONS: list[tuple[str, str, str]] = [
    # (column_name, sqlite_type_def, postgresql_type_def)
    ("is_exact_coords", "BOOLEAN DEFAULT 1", "BOOLEAN DEFAULT TRUE"),
    ("user_status", "VARCHAR(30) DEFAULT 'NEW'", "VARCHAR(30) DEFAULT 'NEW'"),
    ("user_notes", "TEXT", "TEXT"),
    ("finish_condition", "VARCHAR(50) DEFAULT 'nieokreślony'", "VARCHAR(50) DEFAULT 'nieokreślony'"),
    ("has_visualisations", "BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE"),
    ("sewerage", "VARCHAR(50) DEFAULT 'nieznana'", "VARCHAR(50) DEFAULT 'nieznana'"),
    ("heating", "VARCHAR(50) DEFAULT 'nieznane'", "VARCHAR(50) DEFAULT 'nieznane'"),
    ("has_fiber", "BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE"),
    ("category", "VARCHAR(50) DEFAULT 'dom'", "VARCHAR(50) DEFAULT 'dom'"),
    ("rooms", "INTEGER", "INTEGER"),
    ("floor", "INTEGER", "INTEGER"),
    ("floors_in_building", "INTEGER", "INTEGER"),
    ("last_scraped_at", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
    ("is_private_owner", "BOOLEAN", "BOOLEAN"),
    ("profile_id", "VARCHAR(100)", "VARCHAR(100)"),
    ("profile_name", "VARCHAR(100)", "VARCHAR(100)"),
    ("gallery_images", "TEXT DEFAULT '[]'", "TEXT DEFAULT '[]'"),
    ("parcel_id", "VARCHAR(100)", "VARCHAR(100)"),
    ("cadastral_area", "FLOAT", "DOUBLE PRECISION"),
    ("geoportal_url", "VARCHAR(500)", "VARCHAR(500)"),
    ("ai_summary", "TEXT", "TEXT"),
    ("ai_questions", "TEXT DEFAULT '[]'", "TEXT DEFAULT '[]'"),
    ("contact_phone", "VARCHAR(50)", "VARCHAR(50)"),
    ("contact_person", "VARCHAR(150)", "VARCHAR(150)"),
    ("ai_verdict", "TEXT", "TEXT"),
    ("worth_interest", "BOOLEAN", "BOOLEAN"),
    ("mpzp_zone", "VARCHAR(250)", "VARCHAR(250)"),
    ("mpzp_status", "VARCHAR(50)", "VARCHAR(50)"),
    ("flood_risk_zone", "VARCHAR(100)", "VARCHAR(100)"),
    ("gesut_networks", "TEXT", "TEXT"),
    ("landslide_risk", "VARCHAR(100)", "VARCHAR(100)"),
    ("egib_building_status", "VARCHAR(100)", "VARCHAR(100)"),
    ("egib_soil_class", "VARCHAR(100)", "VARCHAR(100)"),
    ("noise_level_db", "FLOAT", "DOUBLE PRECISION"),
    ("noise_zone", "VARCHAR(100)", "VARCHAR(100)"),
    ("nature_protected_zone", "VARCHAR(250)", "VARCHAR(250)"),
    ("monument_zone", "VARCHAR(250)", "VARCHAR(250)"),
    ("cemetery_buffer_zone", "VARCHAR(100)", "VARCHAR(100)"),
    ("broadband_status", "VARCHAR(100)", "VARCHAR(100)"),
    ("broadband_details", "VARCHAR(250)", "VARCHAR(250)"),
    ("parcel_front_width_m", "FLOAT", "DOUBLE PRECISION"),
    ("parcel_length_m", "FLOAT", "DOUBLE PRECISION"),
    ("parcel_aspect_ratio", "FLOAT", "DOUBLE PRECISION"),
    ("parcel_shape_type", "VARCHAR(100)", "VARCHAR(100)"),
    ("terrain_slope_pct", "FLOAT", "DOUBLE PRECISION"),
    ("terrain_aspect", "VARCHAR(50)", "VARCHAR(50)"),
    ("walkability_pka_dist_m", "INTEGER", "INTEGER"),
    ("walkability_pka_name", "VARCHAR(150)", "VARCHAR(150)"),
    ("power_lines_risk", "VARCHAR(150)", "VARCHAR(150)"),
    ("solar_hours_per_year", "FLOAT", "DOUBLE PRECISION"),
    ("solar_energy_kwh_m2", "FLOAT", "DOUBLE PRECISION"),
    ("poi_counts", "TEXT", "TEXT"),
    ("nearest_poi", "TEXT", "TEXT"),
    ("geology_formation", "VARCHAR(250)", "VARCHAR(250)"),
    ("geology_risk_note", "TEXT", "TEXT"),
    ("stakeholder_questions", "TEXT DEFAULT '{}'", "TEXT DEFAULT '{}'"),
    ("documents_to_obtain", "TEXT DEFAULT '[]'", "TEXT DEFAULT '[]'"),
    ("structured_risks", "TEXT DEFAULT '[]'", "TEXT DEFAULT '[]'"),
    ("air_aqi", "INTEGER", "INTEGER"),
    ("air_aqi_label", "VARCHAR(50)", "VARCHAR(50)"),
    ("air_pm25_heating_avg", "FLOAT", "DOUBLE PRECISION"),
    ("air_pm25_summer_avg", "FLOAT", "DOUBLE PRECISION"),
    ("air_smog_days", "INTEGER", "INTEGER"),
    ("air_gios_station", "VARCHAR(150)", "VARCHAR(150)"),
    ("air_gios_dist_km", "FLOAT", "DOUBLE PRECISION"),
    ("air_gios_index", "VARCHAR(50)", "VARCHAR(50)"),
    ("air_smog_risk", "VARCHAR(50)", "VARCHAR(50)"),
    ("desc_hash", "VARCHAR(64)", "VARCHAR(64)"),
    ("llm_json", "TEXT", "TEXT"),
    ("llm_prompt_version", "VARCHAR(50)", "VARCHAR(50)"),
    ("llm_model", "VARCHAR(150)", "VARCHAR(150)"),
    ("filter_reasons", "TEXT DEFAULT '[]'", "TEXT DEFAULT '[]'"),
    ("pros", "TEXT DEFAULT '[]'", "TEXT DEFAULT '[]'"),
    ("cons", "TEXT DEFAULT '[]'", "TEXT DEFAULT '[]'"),
    ("physical_fingerprint", "VARCHAR(64)", "VARCHAR(64)"),
    ("listing_status", "VARCHAR(30) DEFAULT 'ACTIVE'", "VARCHAR(30) DEFAULT 'ACTIVE'"),
    ("first_seen_at", "DATETIME", "TIMESTAMP WITH TIME ZONE"),
    ("initial_price", "FLOAT", "DOUBLE PRECISION"),
    ("relist_count", "INTEGER DEFAULT 0", "INTEGER DEFAULT 0"),
    ("valuation_version", "VARCHAR(64)", "VARCHAR(64)"),
    ("valuation_capex_total", "FLOAT", "DOUBLE PRECISION"),
    ("valuation_market_median_m2", "FLOAT", "DOUBLE PRECISION"),
    ("valuation_price_deviation_pct", "FLOAT", "DOUBLE PRECISION"),
    ("valuation_price_deviation_adj_pct", "FLOAT", "DOUBLE PRECISION"),
    ("valuation_days_on_market", "INTEGER", "INTEGER"),
    ("valuation_negotiation_leverage", "VARCHAR(20)", "VARCHAR(20)"),
    ("valuation_fair_market_value", "FLOAT", "DOUBLE PRECISION"),
    ("valuation_opening_offer", "FLOAT", "DOUBLE PRECISION"),
]


async def _migrate_database_columns(conn) -> None:
    """Safely adds missing columns and indexes to existing SQLite or PostgreSQL tables."""

    def _do_migrate(sync_conn):
        is_sqlite = sync_conn.dialect.name == "sqlite"
        try:
            if is_sqlite:
                res = sync_conn.execute(text("PRAGMA table_info(listings)")).fetchall()
                existing_cols = {str(row[1]).lower() for row in res}
            else:
                res = sync_conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'listings'")
                ).fetchall()
                existing_cols = {str(row[0]).lower() for row in res}

            if not existing_cols:
                return  # Table does not exist yet

            for col_name, sqlite_def, pg_def in LISTINGS_SCHEMA_MIGRATIONS:
                col_name_lower = col_name.lower()
                if col_name_lower not in existing_cols:
                    logger.info(f"Migrating schema: adding '{col_name}' to listings table")
                    col_def = sqlite_def if is_sqlite else pg_def
                    if is_sqlite:
                        sync_conn.execute(text(f"ALTER TABLE listings ADD COLUMN {col_name} {col_def}"))
                    else:
                        sync_conn.execute(text(f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {col_name} {col_def}"))
                    existing_cols.add(col_name_lower)

            if "profile_id" in existing_cols:
                sync_conn.execute(text("UPDATE listings SET profile_id = 'default' WHERE profile_id IS NULL"))

            if not is_sqlite:
                tz_columns = [
                    ("listings", "last_scraped_at"),
                    ("listings", "notified_at"),
                    ("listings", "created_at"),
                    ("listings", "updated_at"),
                    ("listings", "first_seen_at"),
                    ("price_history", "recorded_at"),
                    ("geocache", "cached_at"),
                    ("spatial_cache", "created_at"),
                    ("spatial_cache", "expires_at"),
                ]
                for tbl, col in tz_columns:
                    try:
                        sync_conn.execute(
                            text(
                                f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE TIMESTAMPTZ USING {col} AT TIME ZONE 'UTC'"
                            )
                        )
                    except Exception as e:
                        logger.debug(f"[Database] Column {tbl}.{col} timestamptz migration note: {e}")

            # Invalidate stale air quality cache/columns caused by previous GIOŚ pagination bug (>60km)
            try:
                sync_conn.execute(
                    text(
                        "UPDATE listings SET air_gios_station = NULL, air_gios_dist_km = NULL, air_gios_index = NULL WHERE air_gios_dist_km > 60"
                    )
                )
                sync_conn.execute(text("DELETE FROM spatial_cache WHERE cache_key = 'gios:stations_list_v1'"))
                sync_conn.execute(text("DELETE FROM spatial_cache WHERE cache_key LIKE 'air_quality:%'"))
            except Exception as e:
                logger.debug(f"[Database] Air quality stale cleanup note: {e}")

            sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_profile_id ON listings (profile_id)"))
            sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_desc_hash ON listings (desc_hash)"))
            sync_conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_listings_perf ON listings (profile_id, is_qualified, qualification_score, created_at)"
                )
            )
        except Exception as e:
            logger.warning(f"Schema migration note: {e}")

    await conn.run_sync(_do_migrate)


async def _auto_migrate_sqlite_to_postgres(pg_engine: AsyncEngine) -> None:
    """If target DB is PostgreSQL and empty, automatically migrates data from existing SQLite DB if found."""
    # Find candidate SQLite file locations
    candidates = [
        Path("/app/data/listings.db"),
        Path("data/listings.db"),
        Path("listings.db"),
    ]
    sqlite_path: Path | None = None
    for cand in candidates:
        if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
            sqlite_path = cand.resolve()
            break

    if not sqlite_path:
        return

    # Check if target PostgreSQL already has data
    try:
        async with pg_engine.connect() as conn:
            res = await conn.execute(text("SELECT COUNT(*) FROM listings"))
            count = res.scalar() or 0
            if count > 0:
                return  # Target already populated, do not overwrite/duplicate
    except Exception as e:
        logger.debug(f"[Database] Could not check target listings count: {e}")
        return

    # Atomic lock: try renaming candidate to .migrating to prevent concurrent race conditions
    migrating_path = sqlite_path.with_name(sqlite_path.name + ".migrating")
    try:
        sqlite_path.rename(migrating_path)
    except Exception as lock_err:
        logger.debug(f"[Database] Could not acquire migration lock for {sqlite_path}: {lock_err}")
        return

    logger.info(
        f"[Database] Wykryto istniejącą bazę SQLite '{sqlite_path}'. Rozpoczynam automatyczną migrację do PostgreSQL..."
    )
    import sqlite3

    try:
        sync_conn = sqlite3.connect(str(migrating_path))
        sync_conn.row_factory = sqlite3.Row

        # Check existing tables in SQLite
        tbl_rows = sync_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        existing_tables = {row[0] for row in tbl_rows}

        if "listings" not in existing_tables:
            sync_conn.close()
            migrating_path.rename(sqlite_path)
            return

        async_session_maker = async_sessionmaker(bind=pg_engine, expire_on_commit=False)
        async with async_session_maker() as session:

            def _load_records(model_cls, table_name: str, dt_cols: tuple[str, ...]):
                if table_name not in existing_tables:
                    return []
                rows = sync_conn.execute(f"SELECT * FROM {table_name}").fetchall()
                col_names = [col[1] for col in sync_conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
                mapper = sa_inspect(model_cls)
                col_to_attr = {a.columns[0].name: a.key for a in mapper.column_attrs}
                items = []
                for r in rows:
                    row_dict = {col_to_attr[k]: r[k] for k in col_names if k in col_to_attr}
                    for dt in dt_cols:
                        attr_k = col_to_attr.get(dt, dt)
                        val = row_dict.get(attr_k)
                        if isinstance(val, str) and val:
                            try:
                                row_dict[attr_k] = datetime.fromisoformat(val)
                            except Exception:
                                row_dict[attr_k] = datetime.now(UTC) if dt in ("recorded_at", "cached_at") else None
                    items.append(model_cls(**row_dict))
                return items

            for model_cls, tbl, dt_fields in (
                (ListingModel, "listings", ("created_at", "updated_at", "last_scraped_at", "notified_at")),
                (PriceHistoryModel, "price_history", ("recorded_at",)),
                (GeocacheModel, "geocache", ("cached_at",)),
                (SpatialCacheModel, "spatial_cache", ("created_at", "expires_at")),
            ):
                if records := _load_records(model_cls, tbl, dt_fields):
                    session.add_all(records)
                    await session.flush()
                    logger.info(f"[Database] Zmigrowano {len(records)} wpisów z '{tbl}' do PostgreSQL.")

            await session.commit()

        # Update PostgreSQL serial sequences for tables with auto-increment IDs
        if pg_engine.dialect.name == "postgresql":
            async with pg_engine.begin() as conn:
                for tbl in ("listings", "price_history"):
                    await conn.execute(
                        text(f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), coalesce(max(id), 1)) FROM {tbl};")
                    )

        sync_conn.close()
        logger.info("[Database] ✅ Automatyczna migracja ze SQLite do PostgreSQL zakończona pomyślnie!")
        # Safely mark migrated database as backup
        try:
            backup_path = sqlite_path.with_name(sqlite_path.name + ".migrated_backup")
            if backup_path.exists():
                backup_path.unlink()
            migrating_path.rename(backup_path)
            logger.info(f"[Database] Zarchiwizowano stary plik SQLite do: {backup_path}")
        except Exception as e:
            logger.debug(f"[Database] Nie udało się zmienić nazwy pliku SQLite: {e}")
    except Exception as err:
        logger.warning(f"[Database] Błąd podczas automatycznej migracji SQLite -> PostgreSQL: {err}")
        # Restore original path on failure so next startup can retry
        if migrating_path.exists():
            try:
                migrating_path.rename(sqlite_path)
            except Exception:
                pass


async def init_db() -> None:
    """Initialize database tables and run lightweight migrations with contention retry."""
    engine = get_engine()
    logger.info("Initializing database tables...")
    is_sqlite = engine.dialect.name == "sqlite"
    max_retries = 5
    for attempt in range(max_retries):
        try:
            async with engine.begin() as conn:
                if is_sqlite:
                    await conn.execute(text("PRAGMA journal_mode=WAL;"))
                    await conn.execute(text("PRAGMA busy_timeout=60000;"))
                    await conn.execute(text("PRAGMA synchronous=NORMAL;"))
                await conn.run_sync(Base.metadata.create_all)
                await _migrate_database_columns(conn)
            if not is_sqlite:
                await _auto_migrate_sqlite_to_postgres(engine)
            logger.info("Database tables initialized and up-to-date.")
            return
        except Exception as exc:
            if is_sqlite_lock_error(exc) and attempt < max_retries - 1:
                backoff = 0.5 * (2**attempt)
                logger.warning(f"[Database] SQLite contention during init_db ({exc}), retrying in {backoff:.2f}s...")
                await asyncio.sleep(backoff)
            else:
                raise
