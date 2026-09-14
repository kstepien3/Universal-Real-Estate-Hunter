import asyncio
import os
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
    is_sqlite = "sqlite" in settings.DATABASE_URL
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


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        db_url = settings.DATABASE_URL
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


async def _migrate_sqlite_columns(conn) -> None:
    """Safely adds missing columns to existing SQLite tables."""

    def _do_migrate(sync_conn):
        try:
            res = sync_conn.execute(text("PRAGMA table_info(listings)")).fetchall()
            existing_cols = {row[1] for row in res}
            if existing_cols:  # table exists
                if "is_exact_coords" not in existing_cols:
                    logger.info("Migrating schema: adding 'is_exact_coords' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN is_exact_coords BOOLEAN DEFAULT 1"))
                if "user_status" not in existing_cols:
                    logger.info("Migrating schema: adding 'user_status' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN user_status VARCHAR(30) DEFAULT 'NEW'"))
                if "user_notes" not in existing_cols:
                    logger.info("Migrating schema: adding 'user_notes' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN user_notes TEXT"))
                if "finish_condition" not in existing_cols:
                    logger.info("Migrating schema: adding 'finish_condition' to listings table")
                    sync_conn.execute(
                        text("ALTER TABLE listings ADD COLUMN finish_condition VARCHAR(50) DEFAULT 'nieokreślony'")
                    )
                if "has_visualisations" not in existing_cols:
                    logger.info("Migrating schema: adding 'has_visualisations' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN has_visualisations BOOLEAN DEFAULT 0"))
                if "sewerage" not in existing_cols:
                    logger.info("Migrating schema: adding 'sewerage' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN sewerage VARCHAR(50) DEFAULT 'nieznana'"))
                if "heating" not in existing_cols:
                    logger.info("Migrating schema: adding 'heating' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN heating VARCHAR(50) DEFAULT 'nieznane'"))
                if "has_fiber" not in existing_cols:
                    logger.info("Migrating schema: adding 'has_fiber' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN has_fiber BOOLEAN DEFAULT 0"))
                if "category" not in existing_cols:
                    logger.info("Migrating schema: adding 'category' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN category VARCHAR(50) DEFAULT 'dom'"))
                if "rooms" not in existing_cols:
                    logger.info("Migrating schema: adding 'rooms' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN rooms INTEGER"))
                if "floor" not in existing_cols:
                    logger.info("Migrating schema: adding 'floor' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN floor INTEGER"))
                if "floors_in_building" not in existing_cols:
                    logger.info("Migrating schema: adding 'floors_in_building' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN floors_in_building INTEGER"))
                if "last_scraped_at" not in existing_cols:
                    logger.info("Migrating schema: adding 'last_scraped_at' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN last_scraped_at DATETIME"))
                if "is_private_owner" not in existing_cols:
                    logger.info("Migrating schema: adding 'is_private_owner' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN is_private_owner BOOLEAN"))
                if "profile_id" not in existing_cols:
                    logger.info("Migrating schema: adding 'profile_id' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN profile_id VARCHAR(100)"))
                    sync_conn.execute(
                        text("CREATE INDEX IF NOT EXISTS ix_listings_profile_id ON listings (profile_id)")
                    )
                    sync_conn.execute(text("UPDATE listings SET profile_id = 'default' WHERE profile_id IS NULL"))
                if "profile_name" not in existing_cols:
                    logger.info("Migrating schema: adding 'profile_name' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN profile_name VARCHAR(100)"))
                if "gallery_images" not in existing_cols:
                    logger.info("Migrating schema: adding 'gallery_images' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN gallery_images TEXT DEFAULT '[]'"))
                if "parcel_id" not in existing_cols:
                    logger.info("Migrating schema: adding 'parcel_id' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN parcel_id VARCHAR(100)"))
                if "cadastral_area" not in existing_cols:
                    logger.info("Migrating schema: adding 'cadastral_area' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN cadastral_area FLOAT"))
                if "geoportal_url" not in existing_cols:
                    logger.info("Migrating schema: adding 'geoportal_url' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN geoportal_url VARCHAR(500)"))
                if "ai_summary" not in existing_cols:
                    logger.info("Migrating schema: adding 'ai_summary' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN ai_summary TEXT"))
                if "ai_questions" not in existing_cols:
                    logger.info("Migrating schema: adding 'ai_questions' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN ai_questions TEXT DEFAULT '[]'"))
                if "contact_phone" not in existing_cols:
                    logger.info("Migrating schema: adding 'contact_phone' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN contact_phone VARCHAR(50)"))
                if "contact_person" not in existing_cols:
                    logger.info("Migrating schema: adding 'contact_person' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN contact_person VARCHAR(150)"))
                if "ai_verdict" not in existing_cols:
                    logger.info("Migrating schema: adding 'ai_verdict' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN ai_verdict TEXT"))
                if "worth_interest" not in existing_cols:
                    logger.info("Migrating schema: adding 'worth_interest' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN worth_interest BOOLEAN"))
                if "mpzp_zone" not in existing_cols:
                    logger.info("Migrating schema: adding 'mpzp_zone' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN mpzp_zone VARCHAR(250)"))
                if "mpzp_status" not in existing_cols:
                    logger.info("Migrating schema: adding 'mpzp_status' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN mpzp_status VARCHAR(50)"))
                if "flood_risk_zone" not in existing_cols:
                    logger.info("Migrating schema: adding 'flood_risk_zone' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN flood_risk_zone VARCHAR(100)"))
                if "gesut_networks" not in existing_cols:
                    logger.info("Migrating schema: adding 'gesut_networks' to listings table")
                    sync_conn.execute(text("ALTER TABLE listings ADD COLUMN gesut_networks TEXT"))
                tier1_cols = [
                    ("landslide_risk", "VARCHAR(100)"),
                    ("egib_building_status", "VARCHAR(100)"),
                    ("egib_soil_class", "VARCHAR(100)"),
                    ("noise_level_db", "FLOAT"),
                    ("noise_zone", "VARCHAR(100)"),
                    ("nature_protected_zone", "VARCHAR(250)"),
                    ("monument_zone", "VARCHAR(250)"),
                    ("cemetery_buffer_zone", "VARCHAR(100)"),
                    ("broadband_status", "VARCHAR(100)"),
                    ("broadband_details", "VARCHAR(250)"),
                    ("parcel_front_width_m", "FLOAT"),
                    ("parcel_length_m", "FLOAT"),
                    ("parcel_aspect_ratio", "FLOAT"),
                    ("parcel_shape_type", "VARCHAR(100)"),
                    ("terrain_slope_pct", "FLOAT"),
                    ("terrain_aspect", "VARCHAR(50)"),
                    ("walkability_pka_dist_m", "INTEGER"),
                    ("walkability_pka_name", "VARCHAR(150)"),
                    ("power_lines_risk", "VARCHAR(150)"),
                    ("air_aqi", "INTEGER"),
                    ("air_aqi_label", "VARCHAR(50)"),
                    ("air_pm25_heating_avg", "FLOAT"),
                    ("air_pm25_summer_avg", "FLOAT"),
                    ("air_smog_days", "INTEGER"),
                    ("air_gios_station", "VARCHAR(150)"),
                    ("air_gios_dist_km", "FLOAT"),
                    ("air_gios_index", "VARCHAR(50)"),
                    ("air_smog_risk", "VARCHAR(50)"),
                ]
                for col_name, col_type in tier1_cols:
                    if col_name not in existing_cols:
                        logger.info(f"Migrating schema: adding '{col_name}' to listings table")
                        sync_conn.execute(text(f"ALTER TABLE listings ADD COLUMN {col_name} {col_type}"))
                llm_cache_cols = [
                    ("desc_hash", "VARCHAR(64)"),
                    ("llm_json", "TEXT"),
                    ("llm_prompt_version", "VARCHAR(50)"),
                    ("llm_model", "VARCHAR(150)"),
                ]
                for col_name, col_type in llm_cache_cols:
                    if col_name not in existing_cols:
                        logger.info(f"Migrating schema: adding '{col_name}' to listings table")
                        sync_conn.execute(text(f"ALTER TABLE listings ADD COLUMN {col_name} {col_type}"))
                sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listings_desc_hash ON listings (desc_hash)"))

                # spatial_cache table + expires index come from SpatialCacheModel via Base.metadata.create_all.
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

    logger.info(
        f"[Database] Wykryto istniejącą bazę SQLite '{sqlite_path}'. Rozpoczynam automatyczną migrację do PostgreSQL..."
    )
    import sqlite3

    try:
        sync_conn = sqlite3.connect(str(sqlite_path))
        sync_conn.row_factory = sqlite3.Row

        # Check existing tables in SQLite
        tbl_rows = sync_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        existing_tables = {row[0] for row in tbl_rows}

        if "listings" not in existing_tables:
            sync_conn.close()
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
    except Exception as err:
        logger.warning(f"[Database] Błąd podczas automatycznej migracji SQLite -> PostgreSQL: {err}")


async def init_db() -> None:
    """Initialize database tables and run lightweight migrations with contention retry."""
    engine = get_engine()
    logger.info("Initializing database tables...")
    is_sqlite = "sqlite" in settings.DATABASE_URL
    max_retries = 5
    for attempt in range(max_retries):
        try:
            async with engine.begin() as conn:
                if is_sqlite:
                    await conn.execute(text("PRAGMA journal_mode=WAL;"))
                    await conn.execute(text("PRAGMA busy_timeout=60000;"))
                    await conn.execute(text("PRAGMA synchronous=NORMAL;"))
                await conn.run_sync(Base.metadata.create_all)
                if is_sqlite:
                    await _migrate_sqlite_columns(conn)
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
