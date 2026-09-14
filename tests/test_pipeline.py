from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.enums import BuildingType, QualificationStatus, SegmentSubtype
from src.models.listing import FilterResult, ListingSchema
from src.services.pipeline import ScraperPipeline
from src.storage.models import Base
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


def make_listing() -> ListingSchema:
    return ListingSchema(
        id="llm-1",
        portal="Otodom",
        title="Dom testowy",
        url="https://otodom.pl/oferta/llm-1",
        price=1_000_000,
        price_per_m2=8_000,
        area_home=125.0,
        area_plot=350.0,
        building_type=BuildingType.WOLNOSTOJACY,
        segment_subtype=SegmentSubtype.NIEOKRESLONY,
        location_raw="Rzeszów, Słocina",
        coordinates=(50.04, 22.0),
        raw_description="Dom z garażem, dojazd asfaltowy, kanalizacja miejska.",
    )


def make_qualified_result(**ai_fields) -> FilterResult:
    score = ai_fields.pop("score", 60.0)
    return FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=score,
        passed_stage1=True,
        passed_stage2=True,
        **ai_fields,
    )


def make_unqualified_result(**ai_fields) -> FilterResult:
    score = ai_fields.pop("score", 10.0)
    return FilterResult(
        is_qualified=False,
        status=QualificationStatus.REJECTED_STAGE2,
        score=score,
        passed_stage1=True,
        passed_stage2=False,
        **ai_fields,
    )


def make_pipeline(llm_enabled: bool, engine_mock) -> ScraperPipeline:
    pipeline = ScraperPipeline(
        scrapers=[MagicMock()],
        discord_notifier=AsyncMock(),
        telegram_notifier=AsyncMock(),
    )
    pipeline.llm_analysis_enabled = llm_enabled
    pipeline.engine = engine_mock
    return pipeline


@pytest.mark.asyncio
async def test_pipeline_backfills_ai_analysis_for_existing_listing(async_session):
    """Existing listing without AI analysis must be sent to the LLM once (backfill)."""
    repo = ListingRepository(async_session)
    listing = make_listing()

    model, is_new, _ = await repo.save_or_update(listing, make_qualified_result())
    assert is_new is True
    assert model.ai_summary is None

    engine_mock = AsyncMock()
    engine_mock.evaluate_listing.return_value = FilterResult(
        is_qualified=False,
        status=QualificationStatus.REJECTED_STAGE2,
        score=10.0,
        passed_stage1=True,
        passed_stage2=False,
        ai_summary="TL;DR oferty.",
        ai_questions=["Pytanie do agenta?"],
        contact_phone="+48600123456",
        contact_person="Jan Kowalski",
    )
    pipeline = make_pipeline(llm_enabled=True, engine_mock=engine_mock)

    result = await pipeline.process_listing(listing, repo)

    engine_mock.evaluate_listing.assert_awaited_once()
    assert engine_mock.evaluate_listing.call_args.kwargs["skip_llm"] is False
    assert result["is_new"] is False
    assert model.ai_summary == "TL;DR oferty."
    assert model.ai_questions == ["Pytanie do agenta?"]
    assert model.contact_phone == "+48600123456"
    assert model.contact_person == "Jan Kowalski"


@pytest.mark.asyncio
async def test_pipeline_skips_llm_when_analysis_already_present(async_session):
    """Existing listing with AI analysis and unchanged description must skip the LLM."""
    repo = ListingRepository(async_session)
    listing = make_listing()

    model, is_new, _ = await repo.save_or_update(
        listing,
        make_qualified_result(
            ai_summary="Istniejące TL;DR.",
            ai_questions=["Stare pytanie?"],
        ),
    )
    assert is_new is True
    assert model.ai_summary == "Istniejące TL;DR."

    engine_mock = AsyncMock()
    engine_mock.evaluate_listing.return_value = make_unqualified_result(ai_summary="", ai_questions=[])
    pipeline = make_pipeline(llm_enabled=True, engine_mock=engine_mock)

    await pipeline.process_listing(listing, repo)

    engine_mock.evaluate_listing.assert_awaited_once()
    assert engine_mock.evaluate_listing.call_args.kwargs["skip_llm"] is True
    # Preserved from DB (skip path keeps existing AI fields)
    assert model.ai_summary == "Istniejące TL;DR."
    assert model.ai_questions == ["Stare pytanie?"]


@pytest.mark.asyncio
async def test_pipeline_skips_llm_when_analysis_disabled(async_session):
    """LLM must never run when llm_analysis_enabled is False."""
    repo = ListingRepository(async_session)
    listing = make_listing()

    engine_mock = AsyncMock()
    engine_mock.evaluate_listing.return_value = make_unqualified_result()
    pipeline = make_pipeline(llm_enabled=False, engine_mock=engine_mock)

    await pipeline.process_listing(listing, repo)

    engine_mock.evaluate_listing.assert_awaited_once()
    assert engine_mock.evaluate_listing.call_args.kwargs["skip_llm"] is True


@pytest.mark.asyncio
async def test_pipeline_integrates_geoportal_spatial_findings(async_session, monkeypatch):
    """Pipeline audits Geoportal for exact coords and applies MPZP and flood risk."""
    from src.services.geoportal import geoportal_service

    mock_audit = AsyncMock(
        return_value={
            "main_parcel_id": "186301_1.0221.2296/2",
            "main_parcel_number": "2296/2",
            "cadastral_area": 500.0,
            "geoportal_url": "https://mapy.geoportal.gov.pl/?identifyParcel=186301_1.0221.2296/2",
            "mpzp_zone": "MN: tereny mieszkaniowe",
            "mpzp_status": "OBOWIĄZUJĄCY",
            "flood_risk_zone": "ZAGROŻENIE_POWODZIOWE",
            "surrounding_risks": ["Działka w strefie zagrożenia powodziowego"],
            "surrounding_parcels_count": 4,
        }
    )
    monkeypatch.setattr(geoportal_service, "audit_location", mock_audit)

    repo = ListingRepository(async_session)
    listing = make_listing()
    listing.coordinates = (50.04, 22.0)

    engine_mock = AsyncMock()
    engine_mock.evaluate_listing.return_value = make_qualified_result(score=60.0)
    pipeline = make_pipeline(llm_enabled=False, engine_mock=engine_mock)

    await pipeline.process_listing(listing, repo)

    model = await repo.get_by_url(listing.url)
    assert model is not None
    assert model.parcel_id == "186301_1.0221.2296/2"
    assert model.mpzp_zone == "MN: tereny mieszkaniowe"
    assert model.flood_risk_zone == "ZAGROŻENIE_POWODZIOWE"
    assert any("Zagrożenie powodziowe" in c for c in model.cons)
    assert any("Miejscowy Plan" in p for p in model.pros)
    # Score penalty -20 applied for flood risk
    assert model.qualification_score == 40.0


@pytest.mark.asyncio
async def test_pipeline_integrates_tier1_spatial_penalties(async_session, monkeypatch):
    """Pipeline applies penalties for SOPO (-50), missing EGiB building (-15), protected soil (-10), and noise >65 dB (-15)."""
    from src.services.geoportal import geoportal_service

    mock_audit = AsyncMock(
        return_value={
            "main_parcel_id": "186301_1.0221.2296/2",
            "main_parcel_number": "2296/2",
            "cadastral_area": 600.0,
            "geoportal_url": "https://mapy.geoportal.gov.pl/?identifyParcel=186301_1.0221.2296/2",
            "mpzp_zone": "MN",
            "mpzp_status": "OBOWIĄZUJĄCY",
            "flood_risk_zone": "BRAK",
            "landslide_risk": "OSUWISKO",
            "egib_building_status": "BRAK_W_EWIDENCJI",
            "egib_soil_class": "RIIIa",
            "noise_level_db": 68.0,
            "noise_zone": "WYSOKI_HAŁAS (>65 dB)",
            "nature_protected_zone": "Natura 2000",
            "monument_zone": "Dworek",
            "cemetery_buffer_zone": "<50m",
            "surrounding_risks": [],
            "surrounding_parcels_count": 2,
        }
    )
    monkeypatch.setattr(geoportal_service, "audit_location", mock_audit)

    repo = ListingRepository(async_session)
    listing = make_listing()
    listing.coordinates = (50.04, 22.0)
    listing.finish_condition = "pod_klucz"

    engine_mock = AsyncMock()
    # Starting score 100.0
    engine_mock.evaluate_listing.return_value = make_qualified_result(score=100.0)
    pipeline = make_pipeline(llm_enabled=False, engine_mock=engine_mock)

    await pipeline.process_listing(listing, repo)

    model = await repo.get_by_url(listing.url)
    assert model is not None
    assert model.landslide_risk == "OSUWISKO"
    assert model.egib_building_status == "BRAK_W_EWIDENCJI"
    assert model.egib_soil_class == "RIIIa"
    assert model.noise_level_db == 68.0
    assert model.cemetery_buffer_zone == "<50m"

    # Cons check
    assert any("Aktywne osuwisko" in c for c in model.cons)
    assert any("Dom nieujawniony w ewidencji" in c for c in model.cons)
    assert any("Grunt chroniony w EGiB" in c for c in model.cons)
    assert any("Podwyższony poziom hałasu" in c for c in model.cons)
    assert any("strefa sanitarna cmentarza" in c.lower() for c in model.cons)

    # Score penalty calculation:
    # 100 - 50 (SOPO) - 15 (EGiB building) - 10 (soil) - 15 (noise) - 10 (GDOŚ) - 15 (NID) - 25 (cemetery <50m) = 0.0 (clamped to 0.0)
    assert model.qualification_score == 0.0


@pytest.mark.asyncio
async def test_pipeline_integrates_advanced_spatial_features(async_session, monkeypatch):
    """Pipeline applies pros/cons and score modifications for FTTH, parcel front, slope, power lines, and PKA."""
    from src.services.geoportal import geoportal_service

    mock_audit = AsyncMock(
        return_value={
            "main_parcel_id": "186301_1.0221.100/1",
            "main_parcel_number": "100/1",
            "cadastral_area": 800.0,
            "geoportal_url": "https://mapy.geoportal.gov.pl/?identifyParcel=186301_1.0221.100/1",
            "mpzp_zone": "MN",
            "mpzp_status": "OBOWIĄZUJĄCY",
            "flood_risk_zone": "BRAK",
            "landslide_risk": "BRAK",
            "egib_building_status": "UJAWNIONY",
            "egib_soil_class": "RIVa",
            "noise_level_db": 52.0,
            "noise_zone": "KOMFORT_AKUSTYCZNY",
            "nature_protected_zone": None,
            "monument_zone": None,
            "cemetery_buffer_zone": "BRAK",
            "broadband_status": "ŚWIATŁOWÓD_AKTYWNY",
            "broadband_details": "Orange FTTH",
            "parcel_front_width_m": 13.5,
            "parcel_length_m": 60.0,
            "parcel_aspect_ratio": 4.4,
            "parcel_shape_type": "WĄSKA_SZNUROWKA",
            "terrain_slope_pct": 10.2,
            "terrain_aspect": "POŁUDNIOWY",
            "walkability_pka_dist_m": 850.0,
            "walkability_pka_name": "Rzeszów Załęże",
            "power_lines_risk": "Korytarz linii 400kV",
            "surrounding_risks": [],
            "surrounding_parcels_count": 0,
        }
    )
    monkeypatch.setattr(geoportal_service, "audit_location", mock_audit)

    repo = ListingRepository(async_session)
    listing = make_listing()
    listing.coordinates = (50.04, 22.0)

    engine_mock = AsyncMock()
    # Starting score 80.0
    engine_mock.evaluate_listing.return_value = make_qualified_result(score=80.0)
    pipeline = make_pipeline(llm_enabled=False, engine_mock=engine_mock)

    await pipeline.process_listing(listing, repo)

    model = await repo.get_by_url(listing.url)
    assert model is not None
    assert model.broadband_status == "ŚWIATŁOWÓD_AKTYWNY"
    assert model.parcel_front_width_m == 13.5
    assert model.parcel_shape_type == "WĄSKA_SZNUROWKA"
    assert model.terrain_slope_pct == 10.2
    assert model.terrain_aspect == "POŁUDNIOWY"
    assert model.walkability_pka_name == "Rzeszów Załęże"
    assert model.power_lines_risk == "Korytarz linii 400kV"

    # Pros checks
    assert any("Światłowód aktywny" in p for p in model.pros)
    assert any("Stacja kolejowa" in p for p in model.pros)

    # Cons checks
    assert any("Wąski front działki" in c for c in model.cons)
    assert any("Strome nachylenie" in c for c in model.cons)
    assert any("wysokiego napięcia" in c for c in model.cons)

    # Score adjustment:
    # 80 + 5 (FTTH) - 15 (front < 16m) - 15 (slope > 8%) - 20 (power lines) + 5 (PKA) = 40.0
    assert model.qualification_score == 40.0


@pytest.mark.asyncio
async def test_pipeline_backfill_existing_spatial_data(async_session, monkeypatch):
    """backfill_existing_spatial_data retroactively enriches existing DB listings missing spatial metrics."""
    from src.services.geoportal import geoportal_service
    from src.storage.models import ListingModel

    # Create existing listing in database without spatial metrics
    existing = ListingModel(
        portal="otodom",
        portal_id="backfill_test_123",
        url="https://www.otodom.pl/pl/oferta/backfill-123",
        property_fingerprint="fp_backfill_123",
        title="Dom pod miastem",
        price=600000.0,
        price_per_m2=4285.7,
        area_home=140.0,
        latitude=50.04,
        longitude=22.0,
        is_exact_coords=True,
        qualification_score=90.0,
        pros=["Ogród"],
        cons=["Do odświeżenia"],
    )
    async_session.add(existing)
    await async_session.commit()

    mock_audit = AsyncMock(
        return_value={
            "main_parcel_id": "186301_1.0221.999",
            "broadband_status": "ŚWIATŁOWÓD_AKTYWNY",
            "parcel_front_width_m": 22.0,
            "parcel_length_m": 45.0,
            "parcel_aspect_ratio": 2.05,
            "parcel_shape_type": "REGULARNY",
            "terrain_slope_pct": 3.0,
            "terrain_aspect": "POŁUDNIOWY",
            "walkability_pka_dist_m": 1200.0,
            "walkability_pka_name": "Rzeszów Główny",
            "power_lines_risk": "BRAK",
        }
    )
    monkeypatch.setattr(geoportal_service, "audit_location", mock_audit)

    repo = ListingRepository(async_session)
    pipeline = make_pipeline(llm_enabled=False, engine_mock=AsyncMock())

    updated_count = await pipeline.backfill_existing_spatial_data(async_session, repo)
    assert updated_count == 1

    # Fetch updated model from DB
    updated_model = await repo.get_by_portal_id("otodom", "backfill_test_123")
    assert updated_model is not None
    assert updated_model.parcel_id == "186301_1.0221.999"
    assert updated_model.broadband_status == "ŚWIATŁOWÓD_AKTYWNY"
    assert updated_model.parcel_front_width_m == 22.0
    assert updated_model.parcel_shape_type == "REGULARNY"
    assert updated_model.terrain_slope_pct == 3.0
    assert updated_model.terrain_aspect == "POŁUDNIOWY"
    assert updated_model.walkability_pka_dist_m == 1200.0

    # Pros should have FTTH, regular shape, south slope, and PKA
    assert any("Światłowód aktywny" in p for p in updated_model.pros)
    assert any("Foremna działka" in p for p in updated_model.pros)
    assert any("Południowa ekspozycja" in p for p in updated_model.pros)
    assert any("Stacja kolejowa" in p for p in updated_model.pros)

    # Initial 90.0 + 5 (FTTH) + 5 (PKA) = 100.0
    assert updated_model.qualification_score == 100.0

    # Second run should find 0 listings needing backfill
    updated_again = await pipeline.backfill_existing_spatial_data(async_session, repo)
    assert updated_again == 0


@pytest.mark.asyncio
async def test_persist_batch_single_commit_for_many_listings(async_session, monkeypatch):
    """Deferred batch persist must write N listings with O(1) sessions, not one session per listing."""
    import src.services.pipeline as pipeline_module
    from src.services.geoportal import geoportal_service

    monkeypatch.setattr(geoportal_service, "audit_location", AsyncMock(return_value={}))

    session_calls: list[int] = []

    @asynccontextmanager
    async def fake_get_session():
        session_calls.append(1)
        yield async_session

    monkeypatch.setattr(pipeline_module, "get_session", fake_get_session)

    engine_mock = AsyncMock()
    engine_mock.evaluate_listing.return_value = make_qualified_result(score=70.0)
    pipeline = make_pipeline(llm_enabled=False, engine_mock=engine_mock)

    repo = ListingRepository(async_session)
    listings = []
    for i in ("batch-1", "batch-2"):
        listing = make_listing()
        listing.id = i
        listing.url = f"https://otodom.pl/oferta/{i}"
        listings.append(listing)

    pending = []
    for listing in listings:
        res = await pipeline.process_listing(listing, repo, defer_save=True)
        assert "_deferred_save" in res
        # Nothing written yet — analyze phase is read-only
        assert await repo.get_by_url(listing.url) is None
        pending.append((listing, res))

    await pipeline._persist_batch(pending, None, {})

    for listing, res in pending:
        assert "_deferred_save" not in res
        assert res["is_new"] is True
        assert res["notified"] is True
        model = await repo.get_by_url(listing.url)
        assert model is not None
        assert model.notified_at is not None

    # One session for the batch write + one for notified_at marking (not N per listing)
    assert len(session_calls) == 2
    assert pipeline.discord.send_notification.await_count == 2
    assert pipeline.telegram.send_notification.await_count == 2
