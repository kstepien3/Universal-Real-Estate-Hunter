from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.commute import CommuteService


@pytest.mark.asyncio
async def test_get_osrm_route_success():
    svc = CommuteService()
    client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "routes": [
            {
                "distance": 14500.0,  # 14.5 km
                "duration": 1080.0,  # 18 min
            }
        ]
    }
    client.get.return_value = mock_resp

    km, mins = await svc.get_osrm_route(client, 50.05, 22.01, 50.0375, 22.0047)
    assert km == 14.5
    assert mins == 18


@pytest.mark.asyncio
async def test_get_osrm_route_fallback_on_network_error():
    svc = CommuteService()
    client = AsyncMock()
    client.get.side_effect = Exception("OSRM timeout")

    km, mins = await svc.get_osrm_route(client, 50.05, 22.01, 50.0375, 22.0047)
    assert km > 0
    assert mins > 0


@pytest.mark.asyncio
async def test_get_pedestrian_safety_audit_disabled():
    svc = CommuteService()
    res = await svc.get_pedestrian_safety_audit(None, 50.04, 22.00)
    assert res["pedestrian_sidewalk"] is None
    assert res["pedestrian_lit"] is None
    assert res["pedestrian_surface"] is None
    assert res["pedestrian_safety_note"] is None


@pytest.mark.asyncio
async def test_audit_commute_and_pedestrian_consolidated():
    svc = CommuteService()
    client = AsyncMock()

    # Mock OSRM
    mock_osrm = MagicMock()
    mock_osrm.status_code = 200
    mock_osrm.json.return_value = {"routes": [{"distance": 8200.0, "duration": 720.0}]}
    client.get.return_value = mock_osrm

    res = await svc.audit_commute_and_pedestrian(client, 50.06, 22.02, city="Rzeszów")
    assert res["commute_drive_km"] == 8.2
    assert res["commute_drive_min"] == 12
    assert res["pedestrian_sidewalk"] is None
    assert res["pedestrian_safety_note"] is None


@pytest.mark.asyncio
async def test_get_custom_commutes():
    svc = CommuteService()
    client = AsyncMock()

    mock_osrm = MagicMock()
    mock_osrm.status_code = 200
    mock_osrm.json.return_value = {"routes": [{"distance": 12000.0, "duration": 900.0}]}
    client.get.return_value = mock_osrm

    destinations = [
        {"label": "Praca", "latitude": 50.02, "longitude": 22.00},
        {"label": "Szkoła", "latitude": 50.03, "longitude": 22.01},
        {"label": "", "latitude": 50.04, "longitude": 22.02},  # empty label -> skipped
        {"label": "Złe współrzędne", "latitude": 999.0, "longitude": 22.02},  # invalid -> skipped
    ]
    res = await svc.get_custom_commutes(client, 50.06, 22.02, destinations)
    assert set(res.keys()) == {"Praca", "Szkoła"}
    assert res["Praca"]["min"] == 15
    assert res["Praca"]["km"] == 12.0
    assert res["Szkoła"]["min"] == 15


@pytest.mark.asyncio
async def test_audit_commute_and_pedestrian_with_custom_destinations():
    svc = CommuteService()
    client = AsyncMock()

    mock_osrm = MagicMock()
    mock_osrm.status_code = 200
    mock_osrm.json.return_value = {"routes": [{"distance": 8200.0, "duration": 720.0}]}
    client.get.return_value = mock_osrm

    res = await svc.audit_commute_and_pedestrian(
        client,
        50.06,
        22.02,
        city="Rzeszów",
        custom_destinations=[{"label": "Praca", "latitude": 50.01, "longitude": 21.99}],
    )
    assert res["commute_custom"] == {"Praca": {"min": 12.0, "km": 8.2}}
    assert res["commute_drive_min"] == 12
