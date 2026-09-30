import asyncio

import pytest

from src.filters.ai_gate import canonical_engine_key, is_local_engine, local_ai_gate


def test_is_local_engine():
    assert is_local_engine("http://localhost:11434") is True
    assert is_local_engine("http://127.0.0.1:11434/v1") is True
    assert is_local_engine("http://host.docker.internal:11434") is True
    assert is_local_engine("http://ollama:11434/v1") is True
    assert is_local_engine("http://192.168.50.153:11434") is True
    assert is_local_engine("http://192.168.1.50:1234/v1") is True
    assert is_local_engine("http://10.0.1.20:11434") is True
    assert is_local_engine("http://172.20.0.5:11434") is True
    assert is_local_engine("http://server.local:11434") is True

    # Cloud endpoints
    assert is_local_engine("https://openrouter.ai/api/v1") is False
    assert is_local_engine("https://api.openai.com/v1") is False
    assert is_local_engine("") is False
    assert is_local_engine(None) is False


def test_canonical_engine_key():
    # Localhost variants on same port normalize to same key
    assert canonical_engine_key("http://localhost:11434") == "local:11434"
    assert canonical_engine_key("http://127.0.0.1:11434") == "local:11434"
    assert canonical_engine_key("http://host.docker.internal:11434/v1") == "local:11434"

    # Remote IP with or without /v1 normalizes to same key
    assert canonical_engine_key("http://192.168.50.153:11434/v1") == "192.168.50.153:11434"
    assert canonical_engine_key("http://192.168.50.153:11434") == "192.168.50.153:11434"

    # Different port gives different key
    assert canonical_engine_key("http://localhost:1234") == "local:1234"


@pytest.mark.asyncio
async def test_local_ai_gate_serializes_local_requests():
    target = "http://192.168.50.153:11434"
    execution_order = []
    active_count = 0
    max_active = 0

    async def worker(task_id: int, duration: float):
        nonlocal active_count, max_active
        async with local_ai_gate(target, task_name=f"Task {task_id}"):
            active_count += 1
            if active_count > max_active:
                max_active = active_count
            execution_order.append(f"start_{task_id}")
            await asyncio.sleep(duration)
            execution_order.append(f"end_{task_id}")
            active_count -= 1

    # Launch two workers targeting the same local Ollama concurrently
    await asyncio.gather(
        worker(1, 0.05),
        worker(2, 0.05),
    )

    # Concurrency must be strictly 1 (no overlap)
    assert max_active == 1
    assert execution_order == ["start_1", "end_1", "start_2", "end_2"] or execution_order == [
        "start_2",
        "end_2",
        "start_1",
        "end_1",
    ]


@pytest.mark.asyncio
async def test_local_ai_gate_bypasses_cloud_endpoints():
    target = "https://openrouter.ai/api/v1"
    active_count = 0
    max_active = 0

    async def worker(duration: float):
        nonlocal active_count, max_active
        async with local_ai_gate(target, task_name="Cloud task"):
            active_count += 1
            if active_count > max_active:
                max_active = active_count
            await asyncio.sleep(duration)
            active_count -= 1

    # Both workers should run concurrently for cloud APIs
    await asyncio.gather(
        worker(0.05),
        worker(0.05),
    )

    assert max_active == 2


@pytest.mark.asyncio
async def test_local_ai_gate_releases_lock_on_exception():
    target = "http://localhost:11434"

    with pytest.raises(RuntimeError):
        async with local_ai_gate(target, task_name="Failing task"):
            raise RuntimeError("Engine failure")

    # Subsequent task must be able to acquire the lock immediately
    acquired = False
    async with local_ai_gate(target, task_name="Subsequent task"):
        acquired = True
    assert acquired is True
