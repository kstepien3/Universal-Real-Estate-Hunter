from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.filters.fingerprint import generate_physical_fingerprint
from src.models.enums import BuildingType, QualificationStatus, SegmentSubtype
from src.models.listing import FilterResult, ListingSchema
from src.services.market_analyzer import analyze_negotiation
from src.storage.models import Base, ListingModel
from src.storage.repository import ListingRepository


@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_find_relist_by_physical_fingerprint(async_session: AsyncSession):
    repo = ListingRepository(async_session)

    phys_fp = generate_physical_fingerprint(
        area_home=130.0,
        area_plot=450.0,
        rooms=5,
        street="Sikorskiego",
        city="Rzeszów",
    )
    assert phys_fp is not None

    listing_old = ListingSchema(
        id="old123",
        portal="Otodom",
        title="Dom Sikorskiego okazyjnie",
        url="https://otodom.pl/oferta/old123",
        price=950_000,
        price_per_m2=7307,
        area_home=130.0,
        area_plot=450.0,
        building_type=BuildingType.WOLNOSTOJACY,
        segment_subtype=SegmentSubtype.NIEOKRESLONY,
        location_raw="Rzeszów, Sikorskiego",
        street="Sikorskiego",
        city="Rzeszów",
        rooms=5,
        physical_fingerprint=phys_fp,
        initial_price=950_000,
    )

    filter_res = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=85.0,
        passed_stage1=True,
        passed_stage2=True,
    )

    saved_old, _, _ = await repo.save_or_update(listing_old, filter_res)
    # Simulate old listing was created 60 days ago
    saved_old.created_at = datetime.now(UTC) - timedelta(days=60)
    saved_old.first_seen_at = saved_old.created_at
    await async_session.commit()

    # Now a "new" listing appears on OLX with price dropped to 870k and new URL
    relist_found = await repo.find_relist_by_physical_fingerprint(
        physical_fingerprint=phys_fp,
        exclude_url="https://olx.pl/oferta/new456",
    )
    assert relist_found is not None
    assert relist_found.id == saved_old.id
    assert relist_found.initial_price == 950_000
    assert relist_found.first_seen_at is not None


@pytest.mark.asyncio
async def test_mark_passive_delisted(async_session: AsyncSession):
    repo = ListingRepository(async_session)

    listing_active = ListingModel(
        portal="Otodom",
        portal_id="act1",
        url="https://otodom.pl/act1",
        title="Dom aktywny",
        price=700_000,
        price_per_m2=7000,
        area_home=100.0,
        listing_status="ACTIVE",
        last_scraped_at=datetime.now(UTC) - timedelta(days=2),  # Seen 2 days ago
    )
    listing_stale = ListingModel(
        portal="Otodom",
        portal_id="stale1",
        url="https://otodom.pl/stale1",
        title="Dom wycofany",
        price=800_000,
        price_per_m2=8000,
        area_home=100.0,
        listing_status="ACTIVE",
        last_scraped_at=datetime.now(UTC) - timedelta(days=10),  # Not seen in 10 days
    )
    async_session.add_all([listing_active, listing_stale])
    await async_session.commit()

    delisted_count = await repo.mark_passive_delisted(inactive_days=7)
    assert delisted_count == 1

    await async_session.refresh(listing_active)
    await async_session.refresh(listing_stale)
    assert listing_active.listing_status == "ACTIVE"
    assert listing_stale.listing_status == "DELISTED"


def test_market_analyzer_relisting_leverage_and_arguments():
    # Simulate a listing that has relist_count=2 and price drop from 1,000,000 to 880,000
    first_seen = datetime.now(UTC) - timedelta(days=120)
    listing = {
        "price": 880_000,
        "price_per_m2": 7333,
        "area_home": 120.0,
        "initial_price": 1_000_000,
        "first_seen_at": first_seen,
        "created_at": datetime.now(UTC) - timedelta(days=5),
        "relist_count": 2,
        "finish_condition": "do_wykonczenia",
        "sewerage": "miejska",
        "access_road_type": "asfaltowa",
    }

    advice = analyze_negotiation(
        listing=listing,
        market_median_m2=7500,
    )

    # Days on market should be ~120, not 5
    assert advice.days_on_market >= 119
    # Leverage should be elevated to WYSOKA due to relist_count + days_on_market >= 90
    assert advice.negotiation_leverage == "WYSOKA"
    # Arguments must clearly mention the relisting and initial price
    assert any("re-listing" in a.lower() and "1 000 000" in a for a in advice.arguments)
