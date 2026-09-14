from pathlib import Path

import pytest

from src.services.progress import global_tracker


@pytest.fixture(autouse=True)
def isolate_shared_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Ensure tests never write scrape status or cancel files to workspace disk."""
    status_file = str(tmp_path / "scrape_status.json")
    cancel_file = str(tmp_path / ".scrape_cancel")
    monkeypatch.setattr("src.services.progress.get_shared_status_file", lambda: status_file)
    monkeypatch.setattr("src.services.progress.get_shared_cancel_file", lambda: cancel_file)
    global_tracker.reset()
    yield
    global_tracker.reset()


@pytest.fixture(autouse=True)
def mock_default_air_quality(monkeypatch: pytest.MonkeyPatch):
    """Prevent unexpected external CAMS/GIOŚ network calls during tests."""
    from typing import Any
    from unittest.mock import AsyncMock

    from src.services.air_quality import air_quality_service

    default_aq: dict[str, Any] = {
        "air_aqi": None,
        "air_aqi_label": None,
        "air_aqi_color": None,
        "air_pm25_heating_avg": None,
        "air_pm25_summer_avg": None,
        "air_smog_days": None,
        "air_smog_risk": "NIEZNANE",
        "air_gios_station": None,
        "air_gios_dist_km": None,
        "air_gios_index": None,
        "monthly_averages": [],
        "attribution": "Open-Meteo + GIOŚ",
    }
    monkeypatch.setattr(air_quality_service, "get_air_quality_audit", AsyncMock(return_value=default_aq))
