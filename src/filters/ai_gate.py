"""AI Gate — concurrency control and request serialization for local model engines (Ollama, LM Studio).

Ensures that heavy local AI models (such as Vision LLMs and text LLMs) sharing
the same GPU or local host do not execute concurrently, preventing VRAM exhaustion,
model thrashing, and HTTP request timeouts.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from loguru import logger

_LOCAL_LOCKS: dict[str, asyncio.Lock] = {}
_MODULE_LOCK = asyncio.Lock()


def canonical_engine_key(url: str | None) -> str | None:
    """Returns a canonical identifier for an engine host/port to group shared hardware.

    Normalizes localhost, 127.0.0.1, host.docker.internal, and 0.0.0.0 on the same port
    to a common key (e.g. 'local:11434'). Remote LAN IPs are identified by host:port.
    """
    if not url:
        return None
    cleaned = str(url).strip()
    if not cleaned:
        return None
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"http://{cleaned}"
    try:
        parsed = urlsplit(cleaned)
        host = (parsed.hostname or "").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if host in ("localhost", "127.0.0.1", "host.docker.internal", "0.0.0.0"):
            return f"local:{port}"
        return f"{host}:{port}"
    except Exception:
        return cleaned.lower()


def is_local_engine(url: str | None) -> bool:
    """True if the endpoint URL points to a local or private-network model engine."""
    if not url:
        return False
    low = url.lower().strip()
    local_tokens = (
        "localhost",
        "127.0.0.1",
        "host.docker.internal",
        "0.0.0.0",
        ":11434",
        ":1234",
        "ollama",
        ".local",
        "192.168.",
        "10.",
    )
    if any(tok in low for tok in local_tokens):
        return True
    # Check 172.16.0.0 - 172.31.255.255 private range
    return bool(re.search(r"172\.(1[6-9]|2[0-9]|3[0-1])\.", low))


async def _get_lock_for_key(key: str) -> asyncio.Lock:
    if key in _LOCAL_LOCKS:
        return _LOCAL_LOCKS[key]
    async with _MODULE_LOCK:
        if key not in _LOCAL_LOCKS:
            _LOCAL_LOCKS[key] = asyncio.Lock()
        return _LOCAL_LOCKS[key]


@asynccontextmanager
async def local_ai_gate(
    target_url: str | None,
    task_name: str = "Zadanie AI",
) -> AsyncIterator[None]:
    """Serializes execution for requests targeting local engines on the same host.

    If target_url is a local engine (Ollama, LM Studio, LAN host), callers wait in a FIFO
    queue so that only one request is dispatched to the engine at a time.
    Cloud APIs (OpenRouter, OpenAI) bypass the lock and run without added latency.
    """
    if not is_local_engine(target_url):
        yield
        return

    key = canonical_engine_key(target_url) or "default_local"
    lock = await _get_lock_for_key(key)

    if lock.locked():
        logger.info(
            f"[AIGate] ⏳ Kolejkowanie: {task_name} oczekuje na zwolnienie slotu w lokalnym silniku AI ({key})..."
        )

    async with lock:
        logger.debug(f"[AIGate] 🟢 Rozpoczęto: {task_name} na silniku ({key}).")
        try:
            yield
        finally:
            logger.debug(f"[AIGate] 🏁 Zakończono: {task_name} na silniku ({key}).")
