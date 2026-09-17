import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from src.filters import QualificationEngine
from src.models.enums import BuildingType, FinishCondition, HeatingType
from src.models.listing import ListingSchema
from src.services.air_quality import (
    AirQualityService,
    aqi_to_color,
    aqi_to_label,
    haversine_km,
)
from src.services.live_dashboard import LiveDashboardServer
from src.storage import ListingModel, get_session, init_db, safe_commit


def test_haversine_km():
    # Distance between Rzeszów and Kraków ~ 146-148 km
    dist = haversine_km(50.0412, 21.9991, 50.0647, 19.9450)
    assert 140.0 < dist < 155.0

    # Same point
    assert haversine_km(50.0, 22.0, 50.0, 22.0) == 0.0


def test_aqi_to_label():
    assert aqi_to_label(None) == "Brak danych"
    assert aqi_to_label(15) == "Bardzo dobry"
    assert aqi_to_label(35) == "Dobry"
    assert aqi_to_label(55) == "Umiarkowany"
    assert aqi_to_label(75) == "Dostateczny"
    assert aqi_to_label(95) == "Zły"
    assert aqi_to_label(120) == "Bardzo zły"


def test_aqi_to_color():
    assert aqi_to_color(None) == "#94a3b8"
    assert aqi_to_color(10) == "#22c55e"
    assert aqi_to_color(30) == "#84cc16"
    assert aqi_to_color(50) == "#eab308"
    assert aqi_to_color(70) == "#f97316"
    assert aqi_to_color(90) == "#ef4444"
    assert aqi_to_color(150) == "#7f1d1d"


def test_compute_seasonal_metrics():
    svc = AirQualityService()

    # Construct mock 12 months with 2 readings per month
    times: list[str] = []
    pm25: list[float] = []
    pm10: list[float] = []
    aqi: list[float] = []

    for m in range(1, 13):
        m_str = f"{m:02d}"
        times.extend([f"2024-{m_str}-05T12:00", f"2024-{m_str}-20T12:00"])
        # Winter months (1,2,3, 10,11,12): 40.0 µg/m³; Summer (4..9): 10.0 µg/m³
        val = 40.0 if m in (1, 2, 3, 10, 11, 12) else 10.0
        pm25.extend([val, val])
        pm10.extend([val * 1.5, val * 1.5])
        aqi.extend([val * 1.2, val * 1.2])

    hourly_data = {
        "time": times,
        "pm2_5": pm25,
        "pm10": pm10,
        "european_aqi": aqi,
    }

    heating_avg, summer_avg, smog_days, monthly = svc._compute_seasonal_metrics(hourly_data)

    assert heating_avg == 40.0
    assert summer_avg == 10.0
    # Winter days exceed 25 µg/m³: 6 winter months * 2 days = 12 exceedance days
    assert smog_days == 12
    assert len(monthly) == 12
    assert monthly[0]["is_heating_season"] is True
    assert monthly[3]["is_heating_season"] is False  # April


def test_find_nearest_station():
    svc = AirQualityService()
    stations = [
        {"id": 1, "name": "Kraków", "lat": 50.06, "lon": 19.94, "city": "Kraków"},
        {"id": 2, "name": "Rzeszów", "lat": 50.04, "lon": 22.00, "city": "Rzeszów"},
    ]

    # Target near Rzeszów
    nearest, dist = svc.find_nearest_station(50.05, 22.01, stations)
    assert nearest is not None
    assert nearest["name"] == "Rzeszów"
    assert dist < 5.0


@pytest.mark.asyncio
async def test_get_air_quality_audit_mocked():
    svc = AirQualityService()
    svc._cached_stations = None
    svc._stations_fetched_at = 0.0

    # Mock cached lookup to return None
    svc._get_cached = AsyncMock(return_value=None)
    svc._set_cached = AsyncMock()

    mock_cams_resp = MagicMock()
    mock_cams_resp.status_code = 200
    mock_cams_resp.json.return_value = {
        "current": {"european_aqi": 32, "pm10": 18.5, "pm2_5": 14.2},
        "hourly": {
            "time": ["2024-01-10T12:00", "2024-07-10T12:00"],
            "pm2_5": [38.0, 12.0],
            "pm10": [45.0, 15.0],
            "european_aqi": [40, 20],
        },
    }

    mock_gios_st_resp = MagicMock()
    mock_gios_st_resp.status_code = 200
    mock_gios_st_resp.json.return_value = {
        "Lista stacji pomiarowych": [
            {"Identyfikator stacji": 101, "Nazwa stacji": "Stacja Testowa", "WGS84 φ N": "50.04", "WGS84 λ E": "22.00"}
        ]
    }

    mock_gios_idx_resp = MagicMock()
    mock_gios_idx_resp.status_code = 200
    mock_gios_idx_resp.json.return_value = {"AqIndex": {"Nazwa kategorii indeksu": "Dobry"}}

    async def mock_get(url: str, **kwargs: Any) -> Any:
        if "air-quality-api" in url:
            return mock_cams_resp
        if "station/findAll" in url:
            return mock_gios_st_resp
        if "aqindex/getIndex" in url:
            return mock_gios_idx_resp
        return MagicMock(status_code=404)

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        result = await svc.get_air_quality_audit(50.04, 22.00, force_refresh=True)

    assert result["air_aqi"] == 32
    assert result["air_aqi_label"] == "Dobry"
    assert result["air_gios_station"] == "Stacja Testowa"
    assert result["air_gios_index"] == "Dobry"
    assert result["air_pm25_heating_avg"] == 38.0
    assert result["air_pm25_summer_avg"] == 12.0
    assert result["air_smog_risk"] == "WYSOKIE"


def test_apply_spatial_findings_air_quality_scoring():
    engine = QualificationEngine()

    base_listing = ListingSchema(
        id="listing-aqi-1",
        portal="Otodom",
        title="Dom w Rzeszowie",
        url="https://otodom.pl/test-aqi-1",
        price=900_000,
        price_per_m2=7500.0,
        area_home=120.0,
        building_type=BuildingType.WOLNOSTOJACY,
        finish_condition=FinishCondition.DO_ZAMIESZKANIA,
        heating=HeatingType.GAZOWE,
        location_raw="Rzeszów",
    )

    # 1. Chronic winter smog: high risk
    base_listing.air_smog_risk = "WYSOKIE"
    base_listing.air_pm25_heating_avg = 36.5
    base_listing.air_smog_days = 42

    score1, pros1, cons1 = engine.apply_spatial_findings(base_listing, score=100.0, pros=[], cons=[], geo_audit=None)
    assert score1 == 85.0  # 100 - 15
    assert any("Wysokie ryzyko smogu" in c for c in cons1)

    # 2. Elevated smog
    base_listing.air_smog_risk = "PODWYŻSZONE"
    base_listing.air_pm25_heating_avg = 27.0
    base_listing.air_smog_days = 10
    score2, pros2, cons2 = engine.apply_spatial_findings(base_listing, score=100.0, pros=[], cons=[], geo_audit=None)
    assert score2 == 95.0  # 100 - 5
    assert any("Podwyższone stężenie" in c for c in cons2)

    # 3. Clean air
    base_listing.air_smog_risk = "NISKIE"
    base_listing.air_pm25_heating_avg = 11.0
    base_listing.air_smog_days = 2
    base_listing.air_aqi = 20
    score3, pros3, cons3 = engine.apply_spatial_findings(base_listing, score=100.0, pros=[], cons=[], geo_audit=None)
    assert score3 == 105.0  # 100 + 5
    assert any("Czyste powietrze" in p for p in pros3)


class AirQualityDashboardApiTest(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        await init_db()
        server = LiveDashboardServer()
        return server.app

    async def test_get_air_quality_endpoint_success(self) -> None:
        now_dt = datetime.now(UTC)
        uid = uuid.uuid4().hex[:8]
        async with get_session() as session:
            model = ListingModel(
                title="Dom z audytem powietrza",
                portal="Otodom",
                portal_id=f"aqi-portal-{uid}",
                url=f"https://otodom.pl/aqi-portal-{uid}",
                price=850_000,
                price_per_m2=7083,
                area_home=120.0,
                category="dom",
                latitude=50.0412,
                longitude=22.0001,
                is_qualified=True,
                qualification_score=100.0,
                qualification_status="QUALIFIED",
                created_at=now_dt,
                last_scraped_at=now_dt,
            )
            session.add(model)
            await safe_commit(session)
            listing_id = model.id

        mock_audit = {
            "air_aqi": 25,
            "air_aqi_label": "Dobry",
            "air_pm25_heating_avg": 22.0,
            "air_pm25_summer_avg": 9.5,
            "air_smog_days": 8,
            "air_smog_risk": "NISKIE",
            "air_gios_station": "Rzeszów-Piłsudskiego",
            "monthly_averages": [
                {"month": 1, "month_name": "Sty", "pm2_5": 24.0, "is_heating_season": True},
            ],
        }

        with patch(
            "src.services.air_quality.air_quality_service.get_air_quality_audit", AsyncMock(return_value=mock_audit)
        ):
            resp = await self.client.get(f"/api/listings/{listing_id}/air-quality")
            assert resp.status == 200
            data = await resp.json()
            assert data["air_aqi"] == 25
            assert data["air_gios_station"] == "Rzeszów-Piłsudskiego"
            assert len(data["monthly_averages"]) == 1

    async def test_get_air_quality_endpoint_no_coords(self) -> None:
        now_dt = datetime.now(UTC)
        uid = uuid.uuid4().hex[:8]
        async with get_session() as session:
            model = ListingModel(
                title="Dom bez GPS",
                portal="Otodom",
                portal_id=f"aqi-portal-nogps-{uid}",
                url=f"https://otodom.pl/aqi-portal-nogps-{uid}",
                price=850_000,
                price_per_m2=7083,
                area_home=120.0,
                category="dom",
                latitude=None,
                longitude=None,
                is_qualified=True,
                created_at=now_dt,
                last_scraped_at=now_dt,
            )
            session.add(model)
            await safe_commit(session)
            listing_id = model.id

        resp = await self.client.get(f"/api/listings/{listing_id}/air-quality")
        assert resp.status == 400
        data = await resp.json()
        assert "Brak współrzędnych" in data["error"]
