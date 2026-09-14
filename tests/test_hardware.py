from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.filters.hardware import SUGGESTED_OLLAMA_MODELS, detect_hardware_profile
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


def test_hardware_profile_real_system() -> None:
    profile = detect_hardware_profile()
    assert "system" in profile
    assert "device_label" in profile
    assert "recommended_model" in profile
    assert "recommendation_reason" in profile
    assert profile["suggested_models"] == SUGGESTED_OLLAMA_MODELS
    assert profile["recommended_model"] in [m["id"] for m in SUGGESTED_OLLAMA_MODELS]


@patch("src.filters.hardware.platform.system", return_value="Darwin")
@patch("src.filters.hardware.platform.machine", return_value="arm64")
@patch("src.filters.hardware._get_total_ram_bytes", return_value=32 * 1024**3)
@patch("src.filters.hardware._get_nvidia_gpu", return_value=None)
def test_hardware_apple_silicon_32gb(
    mock_gpu: MagicMock,
    mock_ram: MagicMock,
    mock_machine: MagicMock,
    mock_sys: MagicMock,
) -> None:
    profile = detect_hardware_profile()
    assert profile["is_apple_silicon"] is True
    assert profile["system_display"] == "macOS"
    assert profile["recommended_model"] == "bielik:11b-v2.3-instruct"
    assert "Apple Silicon" in profile["device_label"]


@patch("src.filters.hardware.platform.system", return_value="Windows")
@patch("src.filters.hardware.platform.machine", return_value="AMD64")
@patch("src.filters.hardware._get_total_ram_bytes", return_value=16 * 1024**3)
@patch(
    "src.filters.hardware._get_nvidia_gpu",
    return_value={"name": "NVIDIA GeForce RTX 4070 Ti", "vram_mb": 12288},
)
def test_hardware_windows_high_vram(
    mock_gpu: MagicMock,
    mock_ram: MagicMock,
    mock_machine: MagicMock,
    mock_sys: MagicMock,
) -> None:
    profile = detect_hardware_profile()
    assert profile["is_apple_silicon"] is False
    assert profile["recommended_model"] == "bielik:11b-v2.3-instruct"
    assert "RTX 4070 Ti" in profile["device_label"]
    assert "12 GB VRAM" in profile["device_label"]


@patch("src.filters.hardware.platform.system", return_value="Windows")
@patch("src.filters.hardware.platform.machine", return_value="AMD64")
@patch("src.filters.hardware._get_total_ram_bytes", return_value=16 * 1024**3)
@patch(
    "src.filters.hardware._get_nvidia_gpu",
    return_value={"name": "NVIDIA GeForce GTX 1660 Ti", "vram_mb": 6144},
)
def test_hardware_windows_mid_vram(
    mock_gpu: MagicMock,
    mock_ram: MagicMock,
    mock_machine: MagicMock,
    mock_sys: MagicMock,
) -> None:
    profile = detect_hardware_profile()
    assert profile["is_apple_silicon"] is False
    assert profile["recommended_model"] == "qwen2.5:7b"
    assert "1660 Ti" in profile["device_label"]


@patch("src.filters.hardware.platform.system", return_value="Linux")
@patch("src.filters.hardware.platform.machine", return_value="x86_64")
@patch("src.filters.hardware._get_total_ram_bytes", return_value=8 * 1024**3)
@patch("src.filters.hardware._get_nvidia_gpu", return_value=None)
def test_hardware_linux_low_ram_cpu_only(
    mock_gpu: MagicMock,
    mock_ram: MagicMock,
    mock_machine: MagicMock,
    mock_sys: MagicMock,
) -> None:
    profile = detect_hardware_profile()
    assert profile["is_apple_silicon"] is False
    assert profile["recommended_model"] == "llama3.2:3b"
    assert "Tylko procesor CPU" in profile["device_label"]


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

        # Also verify full test_connection returns hardware profile and suggested models
        conn_res = await analyzer.test_connection()
        assert "hardware_profile" in conn_res
        assert "suggested_models" in conn_res
        assert conn_res["hardware_profile"]["recommended_model"] is not None
