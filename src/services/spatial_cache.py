"""Shared SpatialCacheModel adapter for spatial intelligence services.

Single source of truth for the memory-first + DB-backed cache previously
copy-pasted across gunb/commute/developer_verifier (and mirrored in
geoportal/air_quality). Keys are service-namespaced by convention
(e.g. "gunb:…", "osrm:…", "mf:nip:…"), so one shared memory dict is safe.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from src.storage import SpatialCacheModel, get_session

_memory_cache: dict[str, Any] = {}


async def get_spatial_cache(key: str) -> Any | None:
    """Memory-first lookup with DB fallback; None on miss, expiry, or error."""
    if key in _memory_cache:
        return _memory_cache[key]
    try:
        async with get_session() as session:
            item = await session.get(SpatialCacheModel, key)
            if item:
                now = datetime.now(UTC)
                exp = item.expires_at
                if exp and exp.tzinfo is None:
                    exp = exp.replace(tzinfo=UTC)
                if exp is None or exp > now:
                    val = json.loads(item.data_json)
                    _memory_cache[key] = val
                    return val
    except Exception:
        pass
    return None


async def set_spatial_cache(key: str, value: Any, ttl_days: int = 30) -> None:
    """Upserts a cache entry in memory and DB; silently no-ops on error."""
    _memory_cache[key] = value
    try:
        data_str = json.dumps(value, ensure_ascii=False)
        exp = datetime.now(UTC) + timedelta(days=ttl_days)
        async with get_session() as session:
            existing = await session.get(SpatialCacheModel, key)
            if existing:
                existing.data_json = data_str
                existing.expires_at = exp
            else:
                session.add(SpatialCacheModel(cache_key=key, data_json=data_str, expires_at=exp))
            await session.commit()
    except Exception:
        pass
