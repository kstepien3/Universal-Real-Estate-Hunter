from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.filters.hardware import SUGGESTED_OLLAMA_MODELS
from src.filters.llm_analyzer import LLMAnalyzer
from src.services.config_manager import SearchConfig, config_manager


def test_suggested_models_schema() -> None:
    assert len(SUGGESTED_OLLAMA_MODELS) >= 5
    ids = [m["id"] for m in SUGGESTED_OLLAMA_MODELS]
    assert "bielik:11b-v2.3-instruct" in ids
    assert "qwen2.5:7b" in ids
    assert "llama3.2:3b" in ids
    for m in SUGGESTED_OLLAMA_MODELS:
        assert "name" in m
        assert "size_gb" in m
        assert "badge" in m


def test_config_manager_ollama_parameters_roundtrip() -> None:
    cfg = SearchConfig(ollama_temperature=0.2, ollama_num_ctx=4096)
    dumped = cfg.model_dump()
    assert dumped["ollama_temperature"] == 0.2
    assert dumped["ollama_num_ctx"] == 4096

    updated = config_manager.update_config(
        {
            "ollama_temperature": 0.15,
            "ollama_num_ctx": 16384,
        }
    )
    assert updated.ollama_temperature == 0.15
    assert updated.ollama_num_ctx == 16384

    # Reset back to default
    config_manager.update_config(
        {
            "ollama_temperature": 0.0,
            "ollama_num_ctx": 8192,
        }
    )


@pytest.mark.asyncio
async def test_ollama_speed_metrics_and_connection_payload() -> None:
    analyzer = LLMAnalyzer(
        enabled=True,
        ollama_model="qwen2.5:7b",
        ollama_temperature=0.1,
        ollama_num_ctx=4096,
    )
    assert analyzer.ollama_temperature == 0.1
    assert analyzer.ollama_num_ctx == 4096

    mock_tags_resp = MagicMock(status_code=200)
    mock_tags_resp.json.return_value = {"models": [{"name": "qwen2.5:7b"}]}

    mock_gen_resp = MagicMock(status_code=200)
    mock_gen_resp.json.return_value = {
        "response": "OK",
        "eval_count": 50,
        "eval_duration": 1_000_000_000,  # 1 second -> 50 tok/s
    }

    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
    ):
        mock_get.return_value = mock_tags_resp
        mock_post.return_value = mock_gen_resp

        res = await analyzer.test_ollama()
        assert res["status"] == "ok"
        assert res["tokens_per_second"] == 50.0
        assert analyzer.last_measured_tok_per_sec == 50.0
        assert "prędkość: ~50.0 tok/s" in res["message"]

        # Also verify full test_connection returns suggested models and providers
        conn_res = await analyzer.test_connection()
        assert "suggested_models" in conn_res
        assert "local_openai" in conn_res["providers"]


def test_config_manager_local_openai_roundtrip() -> None:
    cfg = SearchConfig(
        local_llm_base_url="http://localhost:1234/v1",
        local_llm_model="qwen2.5-7b-instruct",
        local_llm_api_key="lm-studio-key",
        local_llm_timeout_seconds=90.0,
    )
    dumped = cfg.model_dump()
    assert dumped["local_llm_base_url"] == "http://localhost:1234/v1"
    assert dumped["local_llm_model"] == "qwen2.5-7b-instruct"
    assert dumped["local_llm_api_key"] == "lm-studio-key"
    assert dumped["local_llm_timeout_seconds"] == 90.0

    updated = config_manager.update_config(
        {
            "local_llm_base_url": "http://localhost:8000/v1",
            "local_llm_model": "vllm-model",
            "local_llm_api_key": "vllm-key",
            "local_llm_timeout_seconds": 60.0,
        }
    )
    assert updated.local_llm_base_url == "http://localhost:8000/v1"
    assert updated.local_llm_model == "vllm-model"
    assert updated.local_llm_api_key == "vllm-key"
    assert updated.local_llm_timeout_seconds == 60.0

    # Reset back to default
    config_manager.update_config(
        {
            "local_llm_base_url": "http://localhost:1234/v1",
            "local_llm_model": "",
            "local_llm_api_key": "not-needed",
            "local_llm_timeout_seconds": 120.0,
        }
    )


@pytest.mark.asyncio
async def test_local_openai_connection_and_routing() -> None:
    from src.models.enums import FinishCondition, PropertyCategory
    from src.models.listing import ListingSchema

    analyzer = LLMAnalyzer(
        enabled=True,
        llm_provider="local_openai",
        local_llm_base_url="http://localhost:1234/v1",
        local_llm_model="qwen2.5-7b-instruct",
    )

    # 1. Unreachable server
    with patch("httpx.AsyncClient.get", side_effect=Exception("Connection refused")):
        res = await analyzer.test_local_openai()
        assert res["status"] == "unreachable"
        assert res["configured"] is True

    # 2. Server ok with models
    mock_models_resp = MagicMock(status_code=200)
    mock_models_resp.json.return_value = {"data": [{"id": "qwen2.5-7b-instruct"}, {"id": "bielik-11b"}]}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_models_resp
        res = await analyzer.test_local_openai()
        assert res["status"] == "ok"
        assert "qwen2.5-7b-instruct" in res["installed_models"]
        assert "bielik-11b" in res["installed_models"]
        assert res["model"] == "qwen2.5-7b-instruct"

        # Verify test_connection picks local_openai as active provider
        with (
            patch.object(analyzer, "test_openrouter", return_value={"status": "not_configured"}),
            patch.object(analyzer, "test_openai", return_value={"status": "not_configured"}),
            patch.object(analyzer, "test_ollama", return_value={"status": "unreachable"}),
        ):
            conn_res = await analyzer.test_connection()
            assert conn_res["active_provider"]["id"] == "local_openai"
            assert conn_res["has_working_provider"] is True

    # 3. Execution routing to _call_local_openai
    mock_completion_result = {
        "finish_condition": "do zamieszkania",
        "worth_interest": True,
        "summary": "Wykończony segment.",
    }
    with patch.object(analyzer, "_call_local_openai", return_value=mock_completion_result) as mock_call:
        dummy_listing = ListingSchema(
            id="test-local-llm-1",
            portal="Otodom",
            title="Ładny dom pod klucz",
            price=800000,
            price_per_m2=6500,
            area_home=120,
            location_raw="Rzeszów",
            category=PropertyCategory.DOM,
            finish_condition=FinishCondition.DO_ZAMIESZKANIA,
            url="https://otodom.pl/test-local-llm-1",
            raw_description="Kuchnia w zabudowie, parkiety dębowe, gotowy do wprowadzenia.",
        )
        result = await analyzer.analyze_description(dummy_listing)
        assert result == mock_completion_result
        assert mock_call.called


def test_unified_local_and_cloud_config_roundtrip() -> None:
    # Test setting local_llm_* updates ollama_* and vice versa
    updated = config_manager.update_config(
        {
            "local_llm_preset": "vllm",
            "local_llm_base_url": "http://localhost:8000/v1",
            "local_llm_model": "qwen2.5:14b",
            "local_llm_timeout_seconds": 210.0,
            "local_llm_num_ctx": 16384,
            "cloud_llm_timeout_seconds": 45.0,
        }
    )
    assert updated.local_llm_preset == "vllm"
    assert updated.local_llm_base_url == "http://localhost:8000/v1"
    assert updated.local_llm_model == "qwen2.5:14b"
    assert updated.local_llm_timeout_seconds == 210.0
    assert updated.local_llm_num_ctx == 16384
    assert updated.cloud_llm_timeout_seconds == 45.0

    # Synchronization with ollama_*
    assert updated.ollama_model == "qwen2.5:14b"
    assert updated.ollama_timeout_seconds == 210.0
    assert updated.ollama_num_ctx == 16384

    # Test setting ollama_* also syncs to local_llm_*
    updated2 = config_manager.update_config(
        {
            "ollama_model": "bielik:11b-v2.3-instruct",
            "ollama_timeout_seconds": 150.0,
        }
    )
    assert updated2.ollama_model == "bielik:11b-v2.3-instruct"
    assert updated2.local_llm_model == "bielik:11b-v2.3-instruct"
    assert updated2.local_llm_timeout_seconds == 150.0

    # Reset
    config_manager.update_config(
        {
            "local_llm_preset": "ollama",
            "local_llm_base_url": "http://localhost:11434",
            "local_llm_model": "qwen2.5:7b",
            "local_llm_timeout_seconds": 180.0,
            "local_llm_num_ctx": 8192,
            "cloud_llm_timeout_seconds": 30.0,
            "ollama_model": "qwen2.5:7b",
            "ollama_base_url": "http://localhost:11434",
            "ollama_timeout_seconds": 180.0,
            "ollama_num_ctx": 8192,
        }
    )


@pytest.mark.asyncio
async def test_cloud_llm_timeout_applied_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings
    from src.models.enums import FinishCondition, PropertyCategory
    from src.models.listing import ListingSchema

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")

    analyzer = LLMAnalyzer(
        enabled=True,
        llm_provider="openrouter",
        cloud_llm_timeout_seconds=42.0,
    )
    assert analyzer.cloud_llm_timeout_seconds == 42.0

    dummy_listing = ListingSchema(
        id="test-cloud-timeout",
        portal="Otodom",
        title="Dom wolnostojący",
        price=950000,
        price_per_m2=7000,
        area_home=135,
        location_raw="Kraków",
        category=PropertyCategory.DOM,
        finish_condition=FinishCondition.DO_ZAMIESZKANIA,
        url="https://otodom.pl/test-cloud-timeout",
        raw_description="Kompletnie umeblowany i gotowy.",
    )

    captured_timeout = None

    class DummyOpenAI:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            nonlocal captured_timeout
            captured_timeout = kwargs.get("timeout")
            self.chat = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = (
                '{"finish_condition": "do zamieszkania", "worth_interest": true, "summary": "Super"}'
            )
            mock_response = MagicMock(choices=[mock_choice])
            self.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("openai.AsyncOpenAI", side_effect=DummyOpenAI):
        res = await analyzer._call_openrouter("test prompt", dummy_listing)
        assert res is not None
        assert res["finish_condition"] == "do zamieszkania"
        assert captured_timeout == 42.0
