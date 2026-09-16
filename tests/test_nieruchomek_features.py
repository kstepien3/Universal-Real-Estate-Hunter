from unittest.mock import AsyncMock, MagicMock

import pytest

from src.filters import QualificationEngine
from src.models.listing import (
    FilterResult,
    FinishCondition,
    HeatingType,
    ListingSchema,
    QualificationStatus,
    SewerageType,
    restore_cached_details,
)
from src.services.geoportal import GeoportalService
from src.storage.repository import ListingRepository


@pytest.mark.asyncio
async def test_geoportal_solar_potential_parsing():
    service = GeoportalService()
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "outputs": {
            "totals": {
                "fixed": {
                    "H(i)_y": 1152.09,
                    "E_y": 905.67,
                }
            }
        }
    }
    mock_client.get.return_value = mock_resp

    res = await service.get_solar_potential(mock_client, 50.041, 21.999)
    assert res["solar_energy_kwh_m2"] == 1152.1
    assert res["solar_hours_per_year"] == round(1152.1 * 1.62)
    assert "Nasłonecznienie" in res["description"]


@pytest.mark.asyncio
async def test_geoportal_walkability_poi_audit():
    service = GeoportalService()
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "elements": [
            {
                "lat": 50.042,
                "lon": 22.001,
                "tags": {"shop": "supermarket", "name": "Biedronka"},
            },
            {
                "lat": 50.045,
                "lon": 22.005,
                "tags": {"amenity": "pharmacy", "name": "Apteka Słoneczna"},
            },
            {
                "lat": 50.0412,
                "lon": 22.0001,
                "tags": {"highway": "bus_stop", "name": "Przystanek Centrum"},
            },
            {
                "lat": 50.048,
                "lon": 22.010,
                "tags": {"amenity": "school", "name": "Szkoła Podstawowa nr 1"},
            },
            {
                "lat": 50.043,
                "lon": 22.002,
                "tags": {"leisure": "park", "name": "Park Miejski"},
            },
        ]
    }
    mock_client.post.return_value = mock_resp

    res = await service.get_walkability_poi_audit(mock_client, 50.041, 21.999, radius_m=1500)
    assert res["poi_counts"]["sklepy"] >= 1
    assert res["poi_counts"]["apteki"] >= 1
    assert res["poi_counts"]["transport"] >= 1
    assert res["poi_counts"]["edukacja"] >= 1
    assert res["poi_counts"]["rekreacja"] >= 1
    assert res["nearest_poi"]["sklepy"]["name"] == "Biedronka"
    assert res["nearest_poi"]["transport"]["name"] == "Przystanek Centrum"
    assert res["nearest_poi"]["transport"]["dist_m"] > 0
    assert res["nearest_poi"]["transport"]["walk_min"] >= 1


@pytest.mark.asyncio
async def test_geoportal_geology_audit():
    service = GeoportalService()
    mock_client = AsyncMock()

    # Case 1: Organic / wetland soil
    res_wetland = await service.get_geology_audit(mock_client, 50.0, 22.0, egib_soil="ŁIV", slope_pct=3.0)
    assert "organiczne" in res_wetland["geology_formation"].lower()
    assert "obniżonej nośności" in res_wetland["geology_risk_note"]

    # Case 2: Standard loam / arable soil with steep slope
    res_slope = await service.get_geology_audit(mock_client, 50.0, 22.0, egib_soil="RIIIa", slope_pct=14.5)
    assert "Gliny" in res_slope["geology_formation"]
    assert "Dobra nośność" in res_slope["geology_risk_note"]
    assert "znaczny spadek terenu" in res_slope["geology_risk_note"]


@pytest.mark.asyncio
async def test_qualification_engine_stakeholder_and_structured_risks_integration():
    engine = QualificationEngine()
    engine.llm_analysis_enabled = True
    engine.llm.enabled = True

    # Mock analyze_description output with stakeholder questions, documents, and structured risks
    mock_llm_result = {
        "summary": "Dom jednorodzinny w Wawrze w cenie 1.2M zł.",
        "worth_interest": True,
        "verdict": "Tak — atrakcyjna cena poniżej mediany rynkowej.",
        "questions_for_agent": ["Jaki jest numer księgi wieczystej?"],
        "stakeholder_questions": {
            "seller": ["Jaki jest numer KW?", "Czy instalacje są po odbiorze?"],
            "community": [],
            "notary": ["Jakie obciążenia widnieją w dziale III i IV?"],
            "municipality": ["Jaki jest termin przebudowy ulicy dojazdowej?"],
        },
        "documents_to_obtain": [
            "Odpis z księgi wieczystej",
            "Wypis i wyrys z MPZP",
            "Świadectwo charakterystyki energetycznej",
        ],
        "structured_risks": [
            {
                "severity": "KRYTYCZNE",
                "title": "Brak numeru KW w ogłoszeniu",
                "description": "Nieznany stan prawny i ewentualne hipoteki",
                "action": "Zażądaj numeru KW przed podpisaniem umowy przedwstępnej",
            },
            {
                "severity": "SREDNIE",
                "title": "Ulica dojazdowa w trakcie modernizacji",
                "description": "Możliwe utrudnienia komunikacyjne",
                "action": "Sprawdź harmonogram robót w urzędzie gminy",
            },
        ],
        "finish_condition": "pod_klucz",
        "finish_note": "W pełni wykończony dom gotowy do zamieszkania.",
        "has_visualisations": False,
        "is_corner": False,
        "is_middle": False,
        "has_parking_or_garage": True,
        "road_is_bad": False,
        "terrain_risk": False,
        "sewerage": "miejska",
        "extracted_plot_m2": 600.0,
        "hidden_costs": [],
        "legal_risks": ["Brak numeru KW"],
        "discrepancies": [],
        "pros": ["Świetna lokalizacja", "Gotowy do zamieszkania"],
        "cons": [],
    }

    engine.llm.analyze_description = AsyncMock(return_value=mock_llm_result)

    listing = ListingSchema(
        id="test-nieruchomek-1",
        portal="otodom",
        title="Wykończony dom z ogrodem",
        url="https://otodom.pl/oferta/test-nieruchomek-1",
        price=1200000.0,
        price_per_m2=8000.0,
        area_home=150.0,
        area_plot=600.0,
        location_raw="Warszawa Wawer",
        finish_condition=FinishCondition.DO_ZAMIESZKANIA,
        sewerage=SewerageType.MIEJSKA,
        heating=HeatingType.GAZOWE,
        solar_hours_per_year=2880.0,
        solar_energy_kwh_m2=1110.0,
        poi_counts={"sklepy": 12, "transport": 8},
        geology_formation="Gliny zwałowe",
        geology_risk_note="Dobra nośność podłoża",
    )

    result = await engine.evaluate_listing(listing)
    assert result.is_qualified is True
    assert result.stakeholder_questions["seller"] == ["Jaki jest numer KW?", "Czy instalacje są po odbiorze?"]
    assert result.stakeholder_questions["notary"] == ["Jakie obciążenia widnieją w dziale III i IV?"]
    assert len(result.documents_to_obtain) == 3
    assert len(result.structured_risks) == 2
    assert result.structured_risks[0]["severity"] == "KRYTYCZNE"
    assert result.structured_risks[0]["action"] == "Zażądaj numeru KW przed podpisaniem umowy przedwstępnej"
    assert result.solar_hours_per_year == 2880.0
    assert result.solar_energy_kwh_m2 == 1110.0
    assert result.poi_counts == {"sklepy": 12, "transport": 8}


@pytest.mark.asyncio
async def test_storage_and_restoration_of_new_fields():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from src.storage.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        repo = ListingRepository(session)

        listing = ListingSchema(
            id="store-test-1",
            portal="morizon",
            title="Działka budowlana pod lasem",
            url="https://morizon.pl/oferta/store-test-1",
            price=350000.0,
            price_per_m2=350.0,
            area_home=1.0,
            area_plot=1000.0,
            location_raw="Rzeszów Zalesie",
            solar_hours_per_year=1850.0,
            solar_energy_kwh_m2=1140.5,
            poi_counts={"sklepy": 4, "transport": 2},
            nearest_poi={"transport": {"name": "Przystanek", "dist_m": 250, "walk_min": 3}},
            geology_formation="Piaski eoliczne",
            geology_risk_note="Zalecane badanie geotechniczne",
        )

        filter_res = FilterResult(
            is_qualified=True,
            status=QualificationStatus.QUALIFIED,
            score=75.0,
            passed_stage1=True,
            passed_stage2=True,
            stakeholder_questions={"seller": ["Czy działka ma KW?"], "municipality": ["Czy droga jest gminna?"]},
            documents_to_obtain=["Odpis KW", "Wypis MPZP"],
            structured_risks=[
                {
                    "severity": "WYSOKIE",
                    "title": "Brak badań nośności gruntu",
                    "description": "Piaski eoliczne mogą wymagać posadowienia specjalnego",
                    "action": "Zleć odwierty geotechniczne",
                }
            ],
            solar_hours_per_year=1850.0,
            solar_energy_kwh_m2=1140.5,
            poi_counts={"sklepy": 4, "transport": 2},
            nearest_poi={"transport": {"name": "Przystanek", "dist_m": 250, "walk_min": 3}},
            geology_formation="Piaski eoliczne",
            geology_risk_note="Zalecane badanie geotechniczne",
        )

        model, created, _ = await repo.save_or_update(listing, filter_res)
        assert created is True
        assert model.solar_hours_per_year == 1850.0
        assert model.solar_energy_kwh_m2 == 1140.5
        assert model.geology_formation == "Piaski eoliczne"
        assert model.geology_risk_note == "Zalecane badanie geotechniczne"
        assert model.stakeholder_questions["seller"] == ["Czy działka ma KW?"]
        assert model.documents_to_obtain == ["Odpis KW", "Wypis MPZP"]
        assert model.structured_risks[0]["severity"] == "WYSOKIE"
        assert model.poi_counts == {"sklepy": 4, "transport": 2}
        assert model.nearest_poi["transport"]["dist_m"] == 250

        # Test restore_cached_details
        restored_listing = ListingSchema(
            id="store-test-1",
            portal="morizon",
            title="Działka budowlana pod lasem",
            url="https://morizon.pl/oferta/store-test-1",
            price=350000.0,
            price_per_m2=350.0,
            area_home=1.0,
            location_raw="Rzeszów Zalesie",
        )
        restore_cached_details(restored_listing, model)
        assert restored_listing.solar_hours_per_year == 1850.0
        assert restored_listing.solar_energy_kwh_m2 == 1140.5
        assert restored_listing.geology_formation == "Piaski eoliczne"
        assert restored_listing.geology_risk_note == "Zalecane badanie geotechniczne"
        assert restored_listing.poi_counts == {"sklepy": 4, "transport": 2}
        assert restored_listing.nearest_poi["transport"]["dist_m"] == 250

    await engine.dispose()
