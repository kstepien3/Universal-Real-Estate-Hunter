from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.filters.fingerprint import generate_physical_fingerprint
from src.models.enums import BuildingType, QualificationStatus, SegmentSubtype
from src.models.listing import FilterResult, ListingSchema
from src.storage.models import Base, ListingModel
from src.storage.repository import ListingRepository


@pytest_asyncio.fixture
async def async_session():
    # In-memory SQLite for testing
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_save_and_price_history(async_session: AsyncSession):
    repo = ListingRepository(async_session)

    fp = generate_physical_fingerprint(
        area_home=120.0,
        area_plot=300.0,
        rooms=None,
        street="Witolda",
    )

    listing = ListingSchema(
        id="otodom-100",
        portal="Otodom",
        title="Dom na Witolda",
        url="https://otodom.pl/oferta/otodom-100",
        price=1_100_000,
        price_per_m2=9_166.67,
        area_home=120.0,
        area_plot=300.0,
        building_type=BuildingType.SZEREGOWIEC,
        segment_subtype=SegmentSubtype.SKRAJNY,
        location_raw="Rzeszów, Słocina",
        street="Witolda",
        physical_fingerprint=fp,
    )

    filt_res = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED_WHITELIST,
        score=120.0,
        passed_stage1=True,
        passed_stage2=True,
        pros=["Skrajny", "Garaż"],
        cons=[],
    )

    # 1. Insert new listing
    model, is_new, price_changed = await repo.save_or_update(listing, filt_res)
    assert is_new is True
    assert price_changed is False
    assert model.id is not None
    assert model.title == "Dom na Witolda"
    assert model.is_qualified is True

    # Check re-listing detection by physical fingerprint
    assert fp is not None
    relist = await repo.find_relist_by_physical_fingerprint(fp, exclude_url="https://otodom.pl/oferta/different-url")
    assert relist is not None
    assert relist.id == model.id

    # 2. Update with price drop
    listing.price = 1_050_000
    listing.price_per_m2 = 8_750.0
    updated_model, is_new2, price_changed2 = await repo.save_or_update(listing, filt_res)

    assert is_new2 is False
    assert price_changed2 is True
    assert updated_model.price == 1_050_000

    # 3. Verify notification mark
    assert updated_model.notified_at is None
    await repo.mark_as_notified(updated_model.id)
    assert updated_model.notified_at is not None


@pytest.mark.asyncio
async def test_get_fresh_urls(async_session: AsyncSession):
    repo = ListingRepository(async_session)

    now = datetime.now(UTC)
    async_session.add_all(
        [
            ListingModel(
                portal="Otodom",
                portal_id="a",
                url="https://otodom.pl/x/fresh",
                title="t",
                price=1,
                price_per_m2=1,
                area_home=100,
                raw_description="pełny opis",
                last_scraped_at=now,
            ),
            ListingModel(
                portal="Otodom",
                portal_id="b",
                url="https://otodom.pl/x/stale",
                title="t",
                price=1,
                price_per_m2=1,
                area_home=100,
                raw_description="pełny opis",
                last_scraped_at=now - timedelta(hours=48),
            ),
            ListingModel(
                portal="OLX",
                portal_id="c",
                url="https://olx.pl/x/other-portal",
                title="t",
                price=1,
                price_per_m2=1,
                area_home=100,
                raw_description="pełny opis",
                last_scraped_at=now,
            ),
            ListingModel(
                portal="Otodom",
                portal_id="d",
                url="https://otodom.pl/x/empty-desc",
                title="t",
                price=1,
                price_per_m2=1,
                area_home=100,
                raw_description="",
                last_scraped_at=now,
            ),
        ]
    )
    await async_session.flush()

    urls = await repo.get_fresh_urls(["Otodom", "NieruchomosciOnline"], within_hours=24)

    assert "https://otodom.pl/x/fresh" in urls
    assert "https://otodom.pl/x/stale" not in urls
    assert "https://olx.pl/x/other-portal" not in urls
    assert "https://otodom.pl/x/empty-desc" not in urls


@pytest.mark.asyncio
async def test_update_does_not_wipe_detected_flags(async_session: AsyncSession):
    repo = ListingRepository(async_session)

    fp = generate_physical_fingerprint(area_home=110.0, area_plot=300.0)
    listing = ListingSchema(
        id="otodom-200",
        portal="Otodom",
        title="Dom z włóknem",
        url="https://otodom.pl/oferta/otodom-200",
        price=900_000,
        price_per_m2=8_000,
        area_home=110.0,
        area_plot=300.0,
        location_raw="Rzeszów",
        physical_fingerprint=fp,
        has_fiber=True,
        has_visualisations=True,
    )
    filt_res = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=60.0,
        passed_stage1=True,
        passed_stage2=True,
    )
    model, is_new, _ = await repo.save_or_update(listing, filt_res)
    assert is_new is True
    assert model.has_fiber is True
    assert model.has_visualisations is True

    # Re-scrape with fields not detected this time (e.g. Otodom never detects fiber)
    listing2 = listing.model_copy(update={"has_fiber": False, "has_visualisations": False})
    model2, is_new2, _ = await repo.save_or_update(listing2, filt_res)
    assert is_new2 is False
    assert model2.has_fiber is True
    assert model2.has_visualisations is True


@pytest.mark.asyncio
async def test_repository_gallery_images(async_session: AsyncSession):
    repo = ListingRepository(async_session)
    fp = generate_physical_fingerprint(
        area_home=120.0,
        area_plot=250.0,
        street="Krakowska",
    )
    test_gallery = [
        "https://example.com/photo1.jpg",
        "https://example.com/photo2.jpg",
        "https://example.com/photo3.jpg",
    ]
    listing = ListingSchema(
        id="gal_123",
        portal="Otodom",
        url="https://otodom.pl/oferta/gal-123",
        title="Dom z galerią zdjęć",
        price=850_000,
        price_per_m2=7_083,
        area_home=120.0,
        area_plot=250.0,
        location_raw="Rzeszów",
        physical_fingerprint=fp,
        gallery_images=test_gallery,
    )
    filt_res = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=75.0,
        passed_stage1=True,
        passed_stage2=True,
    )

    model, is_new, _ = await repo.save_or_update(listing, filt_res)
    assert is_new is True
    assert model.gallery_images == test_gallery

    # Verify reload from DB
    loaded = await repo.get_by_url(listing.url)
    assert loaded is not None
    assert loaded.gallery_images == test_gallery

    # Update with new gallery
    new_gallery = test_gallery + ["https://example.com/photo4.jpg"]
    listing_updated = listing.model_copy(update={"gallery_images": new_gallery})
    model_up, is_new_up, _ = await repo.save_or_update(listing_updated, filt_res)
    assert is_new_up is False
    assert len(model_up.gallery_images) == 4


@pytest.mark.asyncio
async def test_repository_delete_all_listings(async_session: AsyncSession):
    repo = ListingRepository(async_session)

    listings = [
        ListingModel(
            portal="Otodom",
            portal_id=f"reset-{i}",
            url=f"https://otodom.pl/x/reset-{i}",
            title=f"Dom {i}",
            price=1_000_000 - i,
            price_per_m2=8_000,
            area_home=100,
        )
        for i in range(3)
    ]
    async_session.add_all(listings)
    await async_session.flush()

    deleted = await repo.delete_all_listings()
    assert deleted == 3

    from sqlalchemy import func, select

    res = await async_session.execute(select(func.count(ListingModel.id)))
    assert res.scalar() == 0


@pytest.mark.asyncio
async def test_repository_delete_all_listings_empty(async_session: AsyncSession):
    repo = ListingRepository(async_session)
    deleted = await repo.delete_all_listings()
    assert deleted == 0


@pytest.mark.asyncio
async def test_repository_ai_due_diligence_fields(async_session: AsyncSession):
    repo = ListingRepository(async_session)
    fp = generate_physical_fingerprint(area_home=115.0, area_plot=300.0)
    listing = ListingSchema(
        id="otodom-ai-1",
        portal="Otodom",
        title="Dom z analizą AI",
        url="https://otodom.pl/oferta/ai-1",
        price=950_000,
        price_per_m2=8_260,
        area_home=115.0,
        area_plot=300.0,
        location_raw="Rzeszów",
        physical_fingerprint=fp,
    )
    filt_res = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=70.0,
        passed_stage1=True,
        passed_stage2=True,
        ai_summary="TL;DR oferty.",
        ai_verdict="Tak — 8 260 zł/m² to poniżej rynku w tej lokalizacji.",
        worth_interest=True,
        ai_questions=["Pytanie 1?", "Pytanie 2?"],
        contact_phone="+48600123456",
        contact_person="Anna Nowak",
    )

    model, is_new, _ = await repo.save_or_update(listing, filt_res)
    assert is_new is True
    assert model.ai_summary == "TL;DR oferty."
    assert model.ai_verdict == "Tak — 8 260 zł/m² to poniżej rynku w tej lokalizacji."
    assert model.worth_interest is True
    assert model.ai_questions == ["Pytanie 1?", "Pytanie 2?"]
    assert model.contact_phone == "+48600123456"
    assert model.contact_person == "Anna Nowak"

    # Round-trip from DB
    loaded = await repo.get_by_url(listing.url)
    assert loaded is not None
    assert loaded.ai_summary == "TL;DR oferty."
    assert loaded.ai_verdict == "Tak — 8 260 zł/m² to poniżej rynku w tej lokalizacji."
    assert loaded.worth_interest is True
    assert loaded.ai_questions == ["Pytanie 1?", "Pytanie 2?"]
    assert loaded.contact_phone == "+48600123456"
    assert loaded.contact_person == "Anna Nowak"

    # Update path preserves AI fields when the new result has none
    filt_res_empty = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=70.0,
        passed_stage1=True,
        passed_stage2=True,
    )
    model_up, is_new_up, _ = await repo.save_or_update(listing, filt_res_empty)
    assert is_new_up is False
    assert model_up.ai_summary == "TL;DR oferty."
    assert model_up.ai_verdict == "Tak — 8 260 zł/m² to poniżej rynku w tej lokalizacji."
    assert model_up.worth_interest is True
    assert model_up.ai_questions == ["Pytanie 1?", "Pytanie 2?"]
    assert model_up.contact_phone == "+48600123456"
    assert model_up.contact_person == "Anna Nowak"


@pytest.mark.asyncio
async def test_repository_spatial_fields(async_session):
    repo = ListingRepository(async_session)
    listing = ListingSchema(
        id="spatial-1",
        portal="Otodom",
        title="Działka budowlana",
        url="https://otodom.pl/oferta/spatial-1",
        price=300_000,
        price_per_m2=300,
        area_home=0.0,
        area_plot=1000.0,
        location_raw="Rzeszów",
    )
    listing.parcel_id = "186301_1.0221.2296/2"
    listing.cadastral_area = 550.0
    listing.geoportal_url = "https://mapy.geoportal.gov.pl/imap/Imgp_2.html?identifyParcel=186301_1.0221.2296/2"
    listing.mpzp_zone = "4.MN: tereny zabudowy mieszkaniowej"
    listing.mpzp_status = "OBOWIĄZUJĄCY"
    listing.flood_risk_zone = "BRAK"

    filt_res = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=75.0,
        passed_stage1=True,
        passed_stage2=True,
        mpzp_zone=listing.mpzp_zone,
        flood_risk_zone=listing.flood_risk_zone,
    )

    model, is_new, _ = await repo.save_or_update(listing, filt_res)
    assert is_new is True
    assert model.parcel_id == "186301_1.0221.2296/2"
    assert model.cadastral_area == 550.0
    assert model.mpzp_zone == "4.MN: tereny zabudowy mieszkaniowej"
    assert model.mpzp_status == "OBOWIĄZUJĄCY"
    assert model.flood_risk_zone == "BRAK"

    loaded = await repo.get_by_url(listing.url)
    assert loaded is not None
    assert loaded.mpzp_zone == "4.MN: tereny zabudowy mieszkaniowej"
    assert loaded.mpzp_status == "OBOWIĄZUJĄCY"
    assert loaded.flood_risk_zone == "BRAK"


@pytest.mark.asyncio
async def test_auto_migrate_sqlite_to_postgres(tmp_path, monkeypatch):
    import sqlite3

    from src.storage.database import _auto_migrate_sqlite_to_postgres

    # Create dummy source SQLite DB
    sqlite_file = tmp_path / "test_listings.db"
    conn = sqlite3.connect(str(sqlite_file))
    conn.execute("""
        CREATE TABLE listings (
            id INTEGER PRIMARY KEY,
            portal TEXT,
            portal_id TEXT,
            url TEXT,
            property_fingerprint TEXT,
            title TEXT,
            price REAL,
            price_per_m2 REAL,
            area_home REAL,
            created_at TEXT
        )
    """)
    conn.execute("""
        INSERT INTO listings (id, portal, portal_id, url, property_fingerprint, title, price, price_per_m2, area_home, created_at)
        VALUES (1, 'Otodom', '101', 'https://otodom.pl/101', 'fp101', 'Super Dom', 750000, 7500, 100, '2026-09-11T12:00:00')
    """)
    conn.commit()
    conn.close()

    # Target engine (SQLite acting as target for testing migration logic)
    target_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/target.db", echo=False)
    async with target_engine.begin() as tconn:
        await tconn.run_sync(Base.metadata.create_all)

    # Monkeypatch candidates to use our temp sqlite file
    from pathlib import Path

    monkeypatch.setattr("src.storage.database.Path", lambda p: sqlite_file if "listings.db" in str(p) else Path(p))

    # Run auto migration
    await _auto_migrate_sqlite_to_postgres(target_engine)

    # Verify target has the listing
    async with target_engine.connect() as tconn:
        res = await tconn.execute(text("SELECT id, title, price FROM listings WHERE id = 1"))
        row = res.fetchone()
        assert row is not None
        assert row[1] == "Super Dom"
        assert row[2] == 750000.0

    await target_engine.dispose()


@pytest.mark.asyncio
async def test_migrate_database_columns_sqlite(tmp_path):
    """Test that _migrate_database_columns adds missing columns to existing SQLite tables."""
    import sqlite3

    from src.storage.database import _migrate_database_columns

    db_file = tmp_path / "legacy_migration_test.db"
    conn = sqlite3.connect(str(db_file))
    # Create minimal legacy table without air_aqi, desc_hash, etc.
    conn.execute("""
        CREATE TABLE listings (
            id INTEGER PRIMARY KEY,
            portal TEXT,
            portal_id TEXT,
            url TEXT,
            property_fingerprint TEXT,
            title TEXT,
            price REAL,
            price_per_m2 REAL,
            area_home REAL
        )
    """)
    conn.commit()
    conn.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", echo=False)
    async with engine.begin() as aconn:
        await _migrate_database_columns(aconn)

    # Verify that new columns were added
    async with engine.connect() as aconn:
        cols = await aconn.run_sync(
            lambda sc: {r[1] for r in sc.execute(text("PRAGMA table_info(listings)")).fetchall()}
        )
        assert "air_aqi" in cols
        assert "air_aqi_label" in cols
        assert "air_pm25_heating_avg" in cols
        assert "air_smog_risk" in cols
        assert "desc_hash" in cols
        assert "profile_id" in cols
        assert "user_status" in cols
        assert "physical_fingerprint" in cols
        assert "property_fingerprint" not in cols

    await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_database_columns_postgres_simulation():
    """Test that _migrate_database_columns generates ALTER TABLE IF NOT EXISTS on postgres dialect."""
    from unittest.mock import AsyncMock, MagicMock

    from src.storage.database import _migrate_database_columns

    executed_sqls = []

    mock_conn = MagicMock()
    mock_conn.dialect.name = "postgresql"

    def mock_execute(sql, *args, **kwargs):
        sql_str = str(sql)
        executed_sqls.append(sql_str)
        mock_res = MagicMock()
        if "information_schema.columns" in sql_str:
            # Simulate an existing table with legacy property_fingerprint column
            mock_res.fetchall.return_value = [("id",), ("title",), ("price",), ("property_fingerprint",)]
        else:
            mock_res.fetchall.return_value = []
        return mock_res

    mock_conn.execute = mock_execute

    async_conn_mock = MagicMock()
    async_conn_mock.run_sync = AsyncMock(side_effect=lambda fn: fn(mock_conn))

    await _migrate_database_columns(async_conn_mock)

    # Verify ALTER TABLE ADD COLUMN IF NOT EXISTS air_aqi INTEGER was executed
    air_aqi_statements = [
        s for s in executed_sqls if "ALTER TABLE listings ADD COLUMN IF NOT EXISTS air_aqi INTEGER" in s
    ]
    assert len(air_aqi_statements) == 1

    # Verify obsolete property_fingerprint was dropped
    drop_statements = [
        s for s in executed_sqls if "ALTER TABLE listings DROP COLUMN IF EXISTS property_fingerprint" in s
    ]
    assert len(drop_statements) == 1


def test_resolve_database_url_auto_detect(monkeypatch):
    """Test that resolve_database_url automatically selects PostgreSQL when available."""
    from src.storage.database import resolve_database_url

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # When postgres port is not available, keeps default sqlite
    monkeypatch.setattr("src.storage.database.is_postgres_available", lambda host, port: False)
    monkeypatch.setattr("config.settings.DATABASE_URL", "sqlite+aiosqlite:///data/listings.db")
    assert "sqlite" in resolve_database_url()

    # When postgres port is reachable, auto upgrades
    monkeypatch.setattr("src.storage.database.is_postgres_available", lambda host, port: True)
    upgraded = resolve_database_url()
    assert "postgresql" in upgraded
    assert "estate_hunter" in upgraded
