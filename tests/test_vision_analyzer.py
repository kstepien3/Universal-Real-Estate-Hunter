from unittest.mock import AsyncMock, MagicMock

import pytest

from src.filters.vision_analyzer import VisionAnalyzer, is_vision_model


def test_is_vision_model_matches_suggested_and_families():
    assert is_vision_model("qwen2.5vl:7b") is True
    assert is_vision_model("llama3.2-vision:11b") is True
    assert is_vision_model("moondream:latest") is True
    assert is_vision_model("llava:13b") is True
    assert is_vision_model("qwen2.5vl") is True
    assert is_vision_model("custom-vision-model:1b") is True


def test_is_vision_model_rejects_text_models():
    assert is_vision_model("qwen2.5:7b") is False
    assert is_vision_model("bielik:11b-v2.3-instruct") is False
    assert is_vision_model("llama3.1:8b") is False
    assert is_vision_model("") is False
    assert is_vision_model(None) is False


def test_build_openai_vision_payload():
    analyzer = VisionAnalyzer()
    images = [
        "https://example.com/salon.jpg",
        "https://example.com/kuchnia.jpg",
        "https://example.com/rzut.jpg",
    ]
    payload = analyzer.build_openai_vision_payload(images, declared_finish="do zamieszkania", model_name="gpt-4o-mini")
    assert payload["model"] == "gpt-4o-mini"
    assert len(payload["messages"]) == 1
    content = payload["messages"][0]["content"]
    assert len(content) == 4  # 1 text prompt + 3 images
    assert content[0]["type"] == "text"
    assert "do zamieszkania" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "https://example.com/salon.jpg"


def test_parse_vision_response_valid():
    analyzer = VisionAnalyzer()
    raw = """```json
    {
      "is_render": false,
      "render_confidence": 0.02,
      "visual_finish_condition": "DO_ZAMIESZKANIA",
      "has_floorplan": true,
      "floorplan_details": {
        "orientation": "Ogród od południa",
        "usability_score": 9,
        "room_layout_notes": "Ustawne sypialnie"
      },
      "defects": [],
      "summary": "Dom w pełni urządzony i zamieszkany."
    }
    ```"""
    res = analyzer.parse_vision_response(raw, declared_finish="do zamieszkania")
    assert res["vision_is_render"] is False
    assert res["vision_finish_condition"] == "DO_ZAMIESZKANIA"
    assert res["discrepancy_detected"] is False
    assert res["vision_floorplan_details"]["orientation"] == "Ogród od południa"
    assert "zamieszkany" in res["vision_summary"]


def test_parse_vision_response_discrepancy():
    analyzer = VisionAnalyzer()
    raw = """{
      "is_render": false,
      "render_confidence": 0.0,
      "visual_finish_condition": "DEWELOPERSKI",
      "has_floorplan": false,
      "defects": ["Brak białego montażu w łazience", "Gołe wylewki w salonie"],
      "summary": "Wnętrze w stanie deweloperskim, brak podłóg i kuchni."
    }"""
    # Seller declared "do zamieszkania", but photos show "DEWELOPERSKI"
    res = analyzer.parse_vision_response(raw, declared_finish="do zamieszkania")
    assert res["vision_finish_condition"] == "DEWELOPERSKI"
    assert res["discrepancy_detected"] is True
    assert "deklaruje stan 'do zamieszkania'" in res["discrepancy_note"]
    assert len(res["vision_defects"]) == 2


@pytest.mark.asyncio
async def test_audit_images_mocked():
    analyzer = VisionAnalyzer()
    client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {"message": {"content": '{"is_render": true, "visual_finish_condition": "DEWELOPERSKI", "defects": []}'}}
        ]
    }
    client.post.return_value = mock_resp

    res = await analyzer.audit_images(
        client,
        image_urls=["https://example.com/render1.jpg"],
        declared_finish="deweloperski",
        api_base="https://mock-api.com/v1",
        api_key="mock-test-key",
    )

    assert res["vision_is_render"] is True
    assert res["vision_finish_condition"] == "DEWELOPERSKI"


@pytest.mark.asyncio
async def test_fetch_image_as_data_uri_success():
    from io import BytesIO

    from PIL import Image

    # Create dummy 1600x1200 test image
    img = Image.new("RGB", (1600, 1200), color="blue")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    raw_bytes = buf.getvalue()

    analyzer = VisionAnalyzer()
    client = AsyncMock()
    mock_get = MagicMock()
    mock_get.status_code = 200
    mock_get.content = raw_bytes
    mock_get.headers = {"content-type": "image/jpeg"}
    client.get.return_value = mock_get

    data_uri = await analyzer.fetch_image_as_data_uri(client, "https://images.example.com/photo.jpg", max_dimension=800)
    assert data_uri is not None
    assert data_uri.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_fetch_image_as_data_uri_passthrough():
    analyzer = VisionAnalyzer()
    client = AsyncMock()
    existing_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    res = await analyzer.fetch_image_as_data_uri(client, existing_uri)
    assert res == existing_uri
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_image_as_data_uri_http_error():
    analyzer = VisionAnalyzer()
    client = AsyncMock()
    mock_get = MagicMock()
    mock_get.status_code = 404
    mock_get.content = b""
    client.get.return_value = mock_get

    res = await analyzer.fetch_image_as_data_uri(client, "https://example.com/nonexistent.jpg")
    assert res is None


def test_parse_vision_response_resilient_markdown_and_chatter():
    analyzer = VisionAnalyzer()
    raw = """Oto wynik analizy zdjęć ofertowych:
    {
      "is_render": false,
      "render_confidence": 0.01,
      "visual_finish_condition": "DO_ZAMIESZKANIA",
      "has_floorplan": false,
      "defects": [],
      "summary": "Prawdziwe zdjęcia wykończonego domu."
    }
    Mam nadzieję, że to pomoże!"""
    res = analyzer.parse_vision_response(raw)
    assert res["vision_is_render"] is False
    assert res["vision_finish_condition"] == "DO_ZAMIESZKANIA"
    assert "Prawdziwe zdjęcia" in res["vision_summary"]


def test_is_local_vision_base_recognition():
    from src.filters.vision_analyzer import is_local_vision_base

    assert is_local_vision_base("http://localhost:11434") is True
    assert is_local_vision_base("http://127.0.0.1:11434/v1") is True
    assert is_local_vision_base("http://host.docker.internal:11434") is True
    assert is_local_vision_base("http://ollama:11434/v1") is True
    assert is_local_vision_base("http://192.168.1.50:1234/v1") is True
    assert is_local_vision_base("https://api.openai.com/v1") is False
    assert is_local_vision_base("https://openrouter.ai/api/v1") is False


def test_resolve_vision_target_openrouter_fallback(monkeypatch):
    from config import settings
    from src.filters.vision_analyzer import resolve_vision_target
    from src.services.config_manager import config_manager

    monkeypatch.setattr(settings, "VISION_BASE_URL", None)
    monkeypatch.setattr(settings, "VISION_API_KEY", None)
    monkeypatch.setattr(settings, "VISION_MODEL", None)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "mock-openrouter-key")
    monkeypatch.setattr(settings, "OPENROUTER_MODEL", "meta-llama/llama-3-8b")  # text-only

    cfg = config_manager.get_config()
    monkeypatch.setattr(cfg, "openrouter_model", "")

    base, key, model = resolve_vision_target()
    assert "openrouter.ai" in base
    assert key == "mock-openrouter-key"
    assert model == "google/gemini-2.5-flash"


def test_resolve_vision_target_retires_gemini_2_0(monkeypatch):
    from config import settings
    from src.filters.vision_analyzer import resolve_vision_target

    monkeypatch.setattr(settings, "VISION_BASE_URL", None)
    monkeypatch.setattr(settings, "VISION_API_KEY", None)
    monkeypatch.setattr(settings, "VISION_MODEL", "google/gemini-2.0-flash-001")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "mock-key")

    base, key, model = resolve_vision_target()
    assert model == "google/gemini-2.5-flash"


def test_parse_vision_response_text_fallback():
    from src.filters.vision_analyzer import vision_analyzer

    raw = "The image features a large finished white house, fully furnished with ready kitchen and modern rooms."
    res = vision_analyzer.parse_vision_response(raw)
    assert res["vision_finish_condition"] == "DO_ZAMIESZKANIA"
    assert res["vision_is_render"] is False
    assert res["audit_success"] is True


def test_resolve_vision_target_mix_openrouter_text_and_ollama_vision(monkeypatch):
    """When text provider is OpenRouter, but Vision model is set to Ollama (qwen2.5vl:7b),

    it must automatically route vision to Ollama without requiring an API key.
    """
    from config import settings
    from src.filters.vision_analyzer import resolve_vision_target
    from src.services.config_manager import config_manager

    # Text provider configured as OpenRouter with key
    monkeypatch.setattr(settings, "VISION_BASE_URL", None)
    monkeypatch.setattr(settings, "VISION_API_KEY", None)
    monkeypatch.setattr(settings, "VISION_MODEL", None)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "mock-secret-key")

    cfg = config_manager.get_config()
    monkeypatch.setattr(cfg, "llm_provider", "openrouter")
    monkeypatch.setattr(cfg, "vision_model", "qwen2.5vl:7b")
    monkeypatch.setattr(cfg, "vision_base_url", "")

    base, key, model = resolve_vision_target()
    assert "11434" in base or "localhost" in base
    assert key == ""  # Local Ollama needs no key
    assert model == "qwen2.5vl:7b"


def test_resolve_vision_target_gemini_overrides_stale_ollama_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a user specifies a Gemini model, even with a leftover Ollama base URL,

    resolve_vision_target must route to OpenRouter with the normalized model name.
    """
    from config import settings
    from src.filters.vision_analyzer import resolve_vision_target

    monkeypatch.setattr(settings, "VISION_BASE_URL", None)
    monkeypatch.setattr(settings, "VISION_API_KEY", None)
    monkeypatch.setattr(settings, "VISION_MODEL", None)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-v1-testkey")

    base, key, model = resolve_vision_target(
        api_base="http://host.docker.internal:11434",
        model_name="gemini 2.5 flash lite",
    )
    assert base == "https://openrouter.ai/api/v1"
    assert key == "sk-or-v1-testkey"
    assert model == "google/gemini-2.5-flash-lite:nitro"


def test_normalize_vision_defects() -> None:
    from src.filters.vision_analyzer import normalize_vision_defects

    # Raw list of dicts from Vision LLMs
    raw = [
        {"photo_id": 0, "description": "Słup wysokiego napięcia widoczny na zdjęciu."},
        {"photo_id": 2, "description": "Słup wysokiego napięcia widoczny na zdjęciu."},
        {"photo_index": 3, "defect": "Brak balustrad na schodach"},
        {"image_index": 4, "defect_type": "Wystające przewody"},
        "Prosty ciąg tekstowy",
        {"wada": "Wilgoć w piwnicy"},
    ]
    res = normalize_vision_defects(raw)
    assert res == [
        "[Zdjęcie 0] Słup wysokiego napięcia widoczny na zdjęciu.",
        "[Zdjęcie 2] Słup wysokiego napięcia widoczny na zdjęciu.",
        "[Zdjęcie 3] Brak balustrad na schodach",
        "[Zdjęcie 4] Wystające przewody",
        "Prosty ciąg tekstowy",
        "Wilgoć w piwnicy",
    ]
