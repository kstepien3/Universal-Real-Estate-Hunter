import pytest

from src.filters import QualificationEngine
from src.models.enums import BuildingType, FinishCondition, HeatingType
from src.models.listing import ListingSchema


def make_base_listing(**overrides):
    data = {
        "id": "ext-1",
        "portal": "Otodom",
        "title": "Dom testowy",
        "url": "https://otodom.pl/test-ext-1",
        "price": 900_000,
        "price_per_m2": 7500.0,
        "area_home": 120.0,
        "building_type": BuildingType.WOLNOSTOJACY,
        "finish_condition": FinishCondition.DO_ZAMIESZKANIA,
        "heating": HeatingType.GAZOWE,
        "location_raw": "Rzeszów",
    }
    data.update(overrides)
    return ListingSchema(**data)


def test_gunb_risk_flags_penalize_score():
    engine = QualificationEngine()
    listing = make_base_listing(
        gunb_risk_flags=["⚠️ Pozwolenie w rejonie: Hala magazynowa — test"],
        gunb_permits=[{"numer_decyzji": "1/2024", "nazwa_zamierzenia": "Hala"}],
    )
    score, pros, cons = engine.apply_spatial_findings(listing, score=100.0, pros=[], cons=[], geo_audit=None)
    assert score == 80.0
    assert any("Hala magazynowa" in c for c in cons)


def test_gunb_standard_permits_add_pro():
    engine = QualificationEngine()
    listing = make_base_listing(
        gunb_risk_flags=[],
        gunb_permits=[{"numer_decyzji": "2/2024", "nazwa_zamierzenia": "Dom jednorodzinny"}],
    )
    score, pros, cons = engine.apply_spatial_findings(listing, score=100.0, pros=[], cons=[], geo_audit=None)
    assert any("GUNB" in p for p in pros)


def test_vision_render_and_discrepancy():
    engine = QualificationEngine()
    listing = make_base_listing(
        vision_is_render=True,
        vision_finish_condition="DEWELOPERSKI",
        vision_defects=["Brak białego montażu"],
    )
    score, pros, cons = engine.apply_spatial_findings(listing, score=100.0, pros=[], cons=[], geo_audit=None)
    # -10 render, -15 discrepancy, defects listed
    assert score == 75.0
    assert any("wizualizacje 3D" in c for c in cons)
    assert any("Niespójność stanu" in c for c in cons)
    assert any("Wada wizualna" in c for c in cons)


def test_commute_and_pedestrian_scoring():
    engine = QualificationEngine()
    listing = make_base_listing(
        commute_drive_min=45,
        pedestrian_sidewalk=False,
        pedestrian_safety_note="brak chodnika",
    )
    score, pros, cons = engine.apply_spatial_findings(listing, score=100.0, pros=[], cons=[], geo_audit=None)
    assert score == 90.0
    assert any("Długi dojazd" in c for c in cons)
    assert any("chodnika" in c for c in cons)


def test_developer_high_risk_penalty():
    engine = QualificationEngine()
    listing = make_base_listing(
        developer_risk_level="HIGH",
        developer_risk_reasons=["🚨 Spółka jest W LIKWIDACJI"],
    )
    score, pros, cons = engine.apply_spatial_findings(listing, score=100.0, pros=[], cons=[], geo_audit=None)
    assert score == 75.0
    assert any("KRS" in c for c in cons)


def test_poi_far_infrastructure_penalty():
    engine = QualificationEngine()
    listing = make_base_listing(
        nearest_poi={
            "edukacja": {"name": "SP 1", "dist_m": 2000, "walk_min": 25},
            "sklepy": {"name": "Market", "dist_m": 300, "walk_min": 4},
            "transport": {"name": "Przystanek", "dist_m": 2500, "walk_min": 30},
        }
    )
    score, pros, cons = engine.apply_spatial_findings(listing, score=100.0, pros=[], cons=[], geo_audit=None)
    assert score == 95.0
    assert any("infrastruktura codzienna" in c for c in cons)


def test_build_prompt_includes_extended_intelligence():
    from src.filters.llm_analyzer import LLMAnalyzer

    analyzer = LLMAnalyzer(enabled=False)
    listing = make_base_listing(
        gunb_risk_flags=["⚠️ Pozwolenie w rejonie: Hala magazynowa"],
        gunb_url="https://wyszukiwarka.gunb.gov.pl/?dzialka=X",
        vision_finish_condition="DEWELOPERSKI",
        vision_is_render=False,
        vision_defects=["Gołe wylewki"],
        commute_drive_min=22,
        commute_drive_km=11.5,
        pedestrian_safety_note="brak chodnika",
        developer_name="Test Dev Sp. z o.o.",
        developer_risk_level="MEDIUM",
        developer_risk_reasons=["⚠️ Minimalny kapitał"],
        nearest_poi={"edukacja": {"name": "SP 1", "dist_m": 400, "walk_min": 5}},
    )
    prompt, _ = analyzer.build_prompt(listing)
    assert "GUNB" in prompt
    assert "Vision AI" in prompt
    assert "Dojazd do centrum (OSRM)" in prompt
    assert "Dostęp pieszy (OSM)" in prompt
    assert "Deweloper/KRS" in prompt
    assert "Infrastruktura piesza (OSM)" in prompt


def test_build_prompt_omits_extended_intelligence_when_absent():
    from src.filters.llm_analyzer import LLMAnalyzer

    analyzer = LLMAnalyzer(enabled=False)
    prompt, _ = analyzer.build_prompt(make_base_listing())
    assert "GUNB" not in prompt
    assert "Vision AI" not in prompt
    assert "Deweloper/KRS" not in prompt


def test_suggested_vision_models_schema():
    from src.filters.vision_analyzer import SUGGESTED_OLLAMA_VISION_MODELS

    assert len(SUGGESTED_OLLAMA_VISION_MODELS) >= 4
    ids = [m["id"] for m in SUGGESTED_OLLAMA_VISION_MODELS]
    assert "qwen2.5vl:7b" in ids
    assert "llama3.2-vision:11b" in ids
    for m in SUGGESTED_OLLAMA_VISION_MODELS:
        assert "name" in m
        assert "size_gb" in m
        assert "badge" in m


def test_resolve_vision_target_precedence(monkeypatch):
    from config import settings
    from src.filters.vision_analyzer import resolve_vision_target

    monkeypatch.setattr(settings, "VISION_MODEL", "qwen2.5vl:7b")
    monkeypatch.setattr(settings, "VISION_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "VISION_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    # Explicit args win over settings
    base, _, model = resolve_vision_target(api_base="https://api.openai.com/v1", api_key="sk-x", model_name="gpt-4o")
    assert base == "https://api.openai.com/v1"
    assert model == "gpt-4o"

    # VISION_* settings win over OPENAI_*
    base, _, model = resolve_vision_target()
    assert base == "http://localhost:11434/v1"
    assert model == "qwen2.5vl:7b"


def _stub_app_config(monkeypatch, **overrides):
    """Deterministic app-config stub (avoids depending on the local search_config.json)."""
    from types import SimpleNamespace

    from src.services import config_manager as cm_module

    data = {
        "llm_provider": "auto",
        "local_llm_base_url": "http://localhost:11434",
        "ollama_base_url": "http://localhost:11434",
        "vision_model": "",
        "vision_base_url": "",
    }
    data.update(overrides)
    monkeypatch.setattr(cm_module.config_manager, "get_config", lambda: SimpleNamespace(**data))


def test_resolve_vision_auto_model_per_backend(monkeypatch):
    from config import settings
    from src.filters.vision_analyzer import resolve_vision_target

    _stub_app_config(monkeypatch)
    monkeypatch.setattr(settings, "VISION_MODEL", None)

    _, _, local_model = resolve_vision_target(api_base="http://localhost:11434/v1")
    assert local_model == "qwen2.5vl:7b"

    monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-4o")
    _, _, cloud_model = resolve_vision_target(api_base="https://api.openai.com/v1")
    assert cloud_model == "gpt-4o"


def test_resolve_vision_base_follows_local_provider(monkeypatch):
    from config import settings
    from src.filters.vision_analyzer import resolve_vision_target

    _stub_app_config(
        monkeypatch,
        llm_provider="local",
        local_llm_base_url="http://localhost:11434",
    )
    monkeypatch.setattr(settings, "VISION_BASE_URL", None)
    monkeypatch.setattr(settings, "VISION_MODEL", None)

    base, _, model = resolve_vision_target()
    assert base == "http://localhost:11434/v1"
    assert model == "qwen2.5vl:7b"


def test_resolve_vision_target_appends_v1_to_bare_host(monkeypatch):
    from config import settings
    from src.filters.vision_analyzer import resolve_vision_target

    monkeypatch.setattr(settings, "VISION_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(settings, "VISION_API_KEY", None)
    base, _, _ = resolve_vision_target()
    assert base == "http://localhost:11434/v1"

    # Custom proxy paths are left untouched
    monkeypatch.setattr(settings, "VISION_BASE_URL", "https://proxy.local/openai")
    base, _, _ = resolve_vision_target()
    assert base == "https://proxy.local/openai"


def test_declared_finish_label_unwraps():
    from src.filters.vision_analyzer import declared_finish_label
    from src.models.enums import FinishCondition

    assert declared_finish_label(make_base_listing()) == FinishCondition.DO_ZAMIESZKANIA.value

    class FakeTarget:
        finish_condition = "do wykończenia"

    assert declared_finish_label(FakeTarget()) == "do wykończenia"

    class NoFinish:
        pass

    assert declared_finish_label(NoFinish()) is None
    assert declared_finish_label(None) is None


def test_vision_config_roundtrip_and_resolution():
    from src.filters.vision_analyzer import resolve_vision_target
    from src.services.config_manager import config_manager

    updated = config_manager.update_config(
        {
            "vision_model": "qwen2.5vl:7b",
            "vision_base_url": "http://localhost:11434/v1",
        }
    )
    assert updated.vision_model == "qwen2.5vl:7b"
    assert updated.vision_base_url == "http://localhost:11434/v1"
    # Vision keys are top-level: they must not leak into the search profile
    assert "vision_model" not in (updated.profiles[0].model_dump() if updated.profiles else {})

    try:
        base, _, model = resolve_vision_target()
        assert base == "http://localhost:11434/v1"
        assert model == "qwen2.5vl:7b"
    finally:
        config_manager.update_config({"vision_model": "", "vision_base_url": ""})

    reset = config_manager.get_config()
    assert reset.vision_model == ""
    assert reset.vision_base_url == ""


@pytest.mark.asyncio
async def test_connection_serves_vision_suggestions():
    from unittest.mock import patch

    from src.filters.llm_analyzer import LLMAnalyzer

    analyzer = LLMAnalyzer(enabled=True, llm_provider="auto", local_llm_preset="ollama")
    with (
        patch.object(analyzer, "test_openrouter", return_value={"status": "not_configured"}),
        patch.object(analyzer, "test_openai", return_value={"status": "not_configured"}),
        patch.object(
            analyzer,
            "test_ollama",
            return_value={
                "status": "ok",
                "installed_models": ["qwen2.5:7b", "qwen2.5vl:7b", "llama3.1:8b", "moondream:latest"],
            },
        ),
    ):
        conn = await analyzer.test_connection()
    assert "suggested_vision_models" in conn
    assert any(m["id"] == "qwen2.5vl:7b" for m in conn["suggested_vision_models"])
    # Installed models are filtered to vision-capable ones through the shared mechanism
    assert set(conn["installed_vision_models"]) == {"qwen2.5vl:7b", "moondream:latest"}


@pytest.mark.asyncio
async def test_standalone_vision_runs_without_coordinates(monkeypatch):
    from src.filters import vision_analyzer as vision_module
    from src.services import pipeline as pipeline_module

    async def fake_audit_images(client=None, image_urls=None, declared_finish=None, **kwargs):
        return {
            "vision_is_render": False,
            "vision_finish_condition": "DO_ZAMIESZKANIA",
            "vision_floorplan_details": {},
            "vision_defects": [],
            "discrepancy_detected": False,
            "discrepancy_note": None,
            "vision_summary": "Gotowe do zamieszkania.",
        }

    monkeypatch.setattr(vision_module.vision_analyzer, "audit_images", fake_audit_images)
    listing = make_base_listing(
        gallery_images=["https://example.com/salon.jpg"],
        main_image_url="https://example.com/main.jpg",
    )
    assert listing.coordinates is None
    res = await pipeline_module.audit_vision_data(listing)
    assert res is not None
    assert listing.vision_finish_condition == "DO_ZAMIESZKANIA"
    assert res["vision_summary"] == "Gotowe do zamieszkania."
