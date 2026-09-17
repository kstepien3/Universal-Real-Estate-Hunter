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
async def test_get_pedestrian_safety_audit_success():
    svc = CommuteService()
    client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "elements": [
            {
                "type": "way",
                "tags": {
                    "highway": "residential",
                    "sidewalk": "both",
                    "lit": "yes",
                    "surface": "asphalt",
                },
            }
        ]
    }
    client.post.return_value = mock_resp

    res = await svc.get_pedestrian_safety_audit(client, 50.04, 22.00)
    assert res["pedestrian_sidewalk"] is True
    assert res["pedestrian_lit"] is True
    assert res["pedestrian_surface"] == "asphalt"
    assert "chodnik" in res["pedestrian_safety_note"]
    assert "oświetlenie" in res["pedestrian_safety_note"]


@pytest.mark.asyncio
async def test_pedestrian_audit_negative_cache_on_error():
    svc = CommuteService()
    client = AsyncMock()
    client.post.side_effect = Exception("boom")

    first = await svc.get_pedestrian_safety_audit(client, 51.1099, 23.1099)
    assert first["pedestrian_sidewalk"] is None
    assert "Brak precyzyjnych danych" in first["pedestrian_safety_note"]
    # Overpass client timeout must clear the server-side [timeout:5] with margin
    assert client.post.call_args.kwargs["timeout"] == 8.0

    second = await svc.get_pedestrian_safety_audit(client, 51.1099, 23.1099)
    assert second["pedestrian_sidewalk"] is None
    # All mirrors tried once, then served from short negative cache, not hammered again
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_pedestrian_audit_falls_through_to_mirror():
    from unittest.mock import MagicMock

    svc = CommuteService()
    client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "elements": [{"type": "way", "tags": {"highway": "footway", "lit": "yes", "surface": "paving_stones"}}]
    }
    client.post.side_effect = [Exception("ConnectTimeout: "), mock_resp]

    res = await svc.get_pedestrian_safety_audit(client, 51.1199, 23.1199)
    assert res["pedestrian_sidewalk"] is True
    assert res["pedestrian_lit"] is True
    assert client.post.await_count == 2
    tried_urls = [call.args[0] for call in client.post.call_args_list]
    assert tried_urls[0] == "https://overpass-api.de/api/interpreter"
    assert tried_urls[1] == "https://overpass.kumi.systems/api/interpreter"


@pytest.mark.asyncio
async def test_pedestrian_audit_circuit_breaker_trips():
    svc = CommuteService()
    client = AsyncMock()
    client.post.side_effect = Exception("ConnectTimeout: ")

    for i in range(3):
        res = await svc.get_pedestrian_safety_audit(client, 52.2001 + i * 0.001, 24.2001)
        assert res["pedestrian_sidewalk"] is None
    assert client.post.await_count == 6

    # Breaker open: fourth coords served immediately without touching the network
    res = await svc.get_pedestrian_safety_audit(client, 52.2009, 24.2009)
    assert res["pedestrian_sidewalk"] is None
    assert client.post.await_count == 6


@pytest.mark.asyncio
async def test_pedestrian_audit_success_resets_breaker():
    from unittest.mock import MagicMock

    svc = CommuteService()
    client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"elements": []}

    client.post.side_effect = Exception("ConnectTimeout: ")
    await svc.get_pedestrian_safety_audit(client, 52.2101, 24.2101)
    await svc.get_pedestrian_safety_audit(client, 52.2102, 24.2102)
    assert client.post.await_count == 4

    client.post.side_effect = None
    client.post.return_value = mock_resp
    res = await svc.get_pedestrian_safety_audit(client, 52.2103, 24.2103)
    assert res["pedestrian_sidewalk"] is False  # no ways = no sidewalk, but a real answer

    client.post.side_effect = Exception("ConnectTimeout: ")
    await svc.get_pedestrian_safety_audit(client, 52.2104, 24.2104)
    # Counter was reset by the success: 4 + 1 + 2 posts, breaker still closed
    assert client.post.await_count == 7


@pytest.mark.asyncio
async def test_audit_commute_and_pedestrian_consolidated():
    svc = CommuteService()
    client = AsyncMock()

    # Mock OSRM
    mock_osrm = MagicMock()
    mock_osrm.status_code = 200
    mock_osrm.json.return_value = {"routes": [{"distance": 8200.0, "duration": 720.0}]}
    client.get.return_value = mock_osrm

    # Mock Overpass
    mock_overpass = MagicMock()
    mock_overpass.status_code = 200
    mock_overpass.json.return_value = {
        "elements": [
            {"type": "way", "tags": {"highway": "tertiary", "sidewalk": "no", "lit": "no", "surface": "paved"}}
        ]
    }
    client.post.return_value = mock_overpass

    res = await svc.audit_commute_and_pedestrian(client, 50.06, 22.02, city="Rzeszów")
    assert res["commute_drive_km"] == 8.2
    assert res["commute_drive_min"] == 12
    assert res["pedestrian_sidewalk"] is False
    assert res["pedestrian_lit"] is False
    assert res["pedestrian_surface"] == "paved"
    assert "brak wydzielonego chodnika" in res["pedestrian_safety_note"]
