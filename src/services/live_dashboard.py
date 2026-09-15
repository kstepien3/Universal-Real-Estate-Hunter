import asyncio
import hashlib
import json
import re
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from aiohttp import web
from loguru import logger
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import selectinload

from src.services.config_manager import config_manager
from src.services.market_analyzer import valuation_engine
from src.services.pipeline import ScraperPipeline
from src.storage import (
    ListingModel,
    ListingRepository,
    PriceHistoryModel,
    clear_medians_cache,
    get_session,
    init_db,
    safe_commit,
)


def _as_utc(dt: "datetime | None") -> "datetime | None":
    """Return dt with UTC tzinfo, normalising naive datetimes stored by SQLite."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
ASSET_DIR = Path(__file__).parent / "templates" / "assets"

ASSET_CONTENT_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

if TEMPLATE_PATH.exists():
    INDEX_HTML = TEMPLATE_PATH.read_text(encoding="utf-8")
else:
    INDEX_HTML = "<!DOCTYPE html><html><body><h1>Dashboard template not found</h1></body></html>"


_ASSET_URL_RE = re.compile(r'(src|href)="(/assets/[^"]+)"')


def _asset_version(rel_path: str) -> str:
    """Return a short content hash for an asset file (empty if missing)."""
    try:
        asset = (ASSET_DIR / rel_path).resolve()
    except (OSError, ValueError):
        return ""
    if not asset.is_relative_to(ASSET_DIR.resolve()) or not asset.is_file():
        return ""
    return hashlib.md5(asset.read_bytes(), usedforsecurity=False).hexdigest()[:12]


# Fields the list endpoint omits (cards/filters never read them; the Due Diligence
# drawer re-fetches the full record via /api/listings/{id}). Keeps /api/listings lean.
_LIST_OMIT_FIELDS = (
    "ai_summary",
    "ai_verdict",
    "ai_questions",
    "worth_interest",
    "contact_phone",
    "contact_person",
    "air_pm25_summer_avg",
    "air_gios_station",
    "air_gios_dist_km",
    "air_gios_index",
    "terrain_slope_pct",
    "terrain_aspect",
    "walkability_pka_dist_m",
    "walkability_pka_name",
    "power_lines_risk",
    "broadband_details",
    "nature_protected_zone",
    "monument_zone",
    "cemetery_buffer_zone",
    "noise_level_db",
    "noise_zone",
    "egib_building_status",
    "egib_soil_class",
    "cadastral_area",
    "mpzp_status",
    "initial_price",
    "price_history_count",
    "first_seen_at",
)


# Image proxy: host allow-list (suffix match) prevents open-proxy / SSRF abuse.
_ALLOWED_IMAGE_HOST_SUFFIXES = (
    "olxcdn.com",
    "staticmorizon.com.pl",
    "nieruchomosci-online.pl",
    "otodom.pl",
    "otodomcdn.com",
    "unsplash.com",
)
_IMG_MAX_BYTES = 8 * 1024 * 1024
_IMG_FETCH_TIMEOUT_SECONDS = 12.0


def _img_cache_dir() -> Path:
    """Resolve the on-disk image cache next to the SQLite DB (data/img_cache)."""
    from config import settings

    url = settings.DATABASE_URL or ""
    if url.startswith("sqlite"):
        db_path = url.split(":///", 1)[-1].strip("/")
        if db_path:
            parent = Path(db_path).parent
            if str(parent) not in (".", ""):
                return parent / "img_cache"
    return Path("data") / "img_cache"


def _is_allowed_image_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_IMAGE_HOST_SUFFIXES)


def _infer_image_content_type(upstream_ct: str | None) -> str:
    ct = (upstream_ct or "").split(";")[0].strip().lower()
    return ct if ct.startswith("image/") else "image/jpeg"


class LiveDashboardServer:
    @staticmethod
    @web.middleware
    async def _compression_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
        resp = await handler(request)
        if isinstance(resp, web.Response) and resp.body and not (resp.content_type or "").startswith("image/"):
            resp.enable_compression()
        return resp

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.app = web.Application(middlewares=[self._compression_middleware])
        self._active_scrape_task: asyncio.Task[Any] | None = None
        self._listings_cache: dict[str, tuple[str, str, bytes]] = {}
        self._http: aiohttp.ClientSession | None = None
        self._img_inflight: dict[str, asyncio.Task[Any]] = {}
        self._img_cache_dir = _img_cache_dir()
        self._img_cache_dir.mkdir(parents=True, exist_ok=True)
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/img", self.handle_image_proxy)
        self.app.router.add_get("/assets/{path:.*}", self.handle_assets)
        self.app.router.add_get("/api/listings", self.handle_get_listings)
        self.app.router.add_get("/api/listings/{id}", self.handle_get_listing_detail)
        self.app.router.add_get("/api/listings/{id}/price-history", self.handle_get_price_history)
        self.app.router.add_get("/api/listings/{id}/air-quality", self.handle_get_air_quality)
        self.app.router.add_patch("/api/listings/{id}/status", self.handle_update_status)
        self.app.router.add_patch("/api/listings/{id}/notes", self.handle_update_notes)
        self.app.router.add_post("/api/listings/{id}/ai-audit", self.handle_generate_ai_audit)
        self.app.router.add_post("/api/geocode/backfill", self.handle_backfill_coords)
        self.app.router.add_get("/api/scrape/status", self.handle_scrape_status)
        self.app.router.add_post("/api/scrape", self.handle_trigger_scrape)
        self.app.router.add_post("/api/scrape/cancel", self.handle_cancel_scrape)
        self.app.router.add_get("/api/config", self.handle_get_config)
        self.app.router.add_post("/api/config", self.handle_update_config)
        self.app.router.add_get("/api/profiles", self.handle_get_profiles)
        self.app.router.add_post("/api/profiles", self.handle_add_or_update_profile)
        self.app.router.add_delete("/api/profiles/{id}", self.handle_delete_profile)
        self.app.router.add_post("/api/scrapers", self.handle_update_scrapers)
        self.app.router.add_get("/api/scheduler", self.handle_get_scheduler)
        self.app.router.add_post("/api/scheduler", self.handle_update_scheduler)
        self.app.router.add_get("/api/llm/status", self.handle_get_llm_status)
        self.app.router.add_post("/api/llm/test", self.handle_test_llm_connection)
        self.app.router.add_post("/api/data/reset", self.handle_reset_data)

    async def handle_get_config(self, request: web.Request) -> web.Response:
        cfg = config_manager.get_config()
        data = cfg.model_dump()
        active = cfg.get_active_profile()
        for k, v in active.model_dump().items():
            if k not in data:
                data[k] = v
        return web.json_response(data)

    async def handle_update_config(self, request: web.Request) -> web.Response:
        data = await request.json()
        old_cfg = config_manager.get_config()
        old_profiles = {p.id: p for p in old_cfg.profiles}

        updated = config_manager.update_config(data)

        # If profiles array was provided, clean up listings for any deleted profiles
        if "profiles" in data and isinstance(data["profiles"], list):
            new_ids = {p.get("id") for p in data["profiles"] if p.get("id")}
            removed_ids = set(old_profiles.keys()) - new_ids
            if removed_ids:
                async with get_session() as session:
                    repo = ListingRepository(session)
                    for rid in removed_ids:
                        r_name = old_profiles[rid].name if rid in old_profiles else None
                        del_cnt = await repo.delete_by_profile(profile_id=rid, profile_name=r_name)
                        logger.info(f"[LiveDashboard] Usunięto profil '{rid}' oraz {del_cnt} powiązanych ofert.")

        price_display = f"{updated.max_price:,.0f} PLN" if updated.max_price is not None else "brak limitu"
        logger.info(
            f"[LiveDashboard] Updated search config: city={updated.city}, "
            f"radius={updated.distance_radius}km, max_price={price_display}"
        )
        resp_data = updated.model_dump()
        active = updated.get_active_profile()
        for k, v in active.model_dump().items():
            if k not in resp_data:
                resp_data[k] = v
        return web.json_response(resp_data)

    async def handle_get_profiles(self, request: web.Request) -> web.Response:
        profiles = [p.model_dump() for p in config_manager.get_config().profiles]
        return web.json_response(profiles)

    async def handle_add_or_update_profile(self, request: web.Request) -> web.Response:
        data = await request.json()
        saved = config_manager.add_or_update_profile(data)
        return web.json_response(saved.model_dump())

    async def handle_delete_profile(self, request: web.Request) -> web.Response:
        profile_id = request.match_info.get("id", "")
        cfg = config_manager.get_config()
        target_name = None
        for p in cfg.profiles:
            if p.id == profile_id:
                target_name = p.name
                break

        ok = config_manager.delete_profile(profile_id)
        deleted_count = 0
        if ok:
            async with get_session() as session:
                repo = ListingRepository(session)
                deleted_count = await repo.delete_by_profile(profile_id=profile_id, profile_name=target_name)
                logger.info(
                    f"[LiveDashboard] Usunięto profil '{profile_id}' oraz {deleted_count} powiązanych ofert z bazy."
                )

        return web.json_response({"success": ok, "deleted_listings": deleted_count})

    async def handle_update_scrapers(self, request: web.Request) -> web.Response:
        data = await request.json()
        cfg = config_manager.update_config({"scrapers": data})
        return web.json_response(cfg.scrapers.model_dump())

    async def handle_get_scheduler(self, request: web.Request) -> web.Response:
        cfg = config_manager.get_config()
        return web.json_response(cfg.scheduler.model_dump())

    async def handle_update_scheduler(self, request: web.Request) -> web.Response:
        data = await request.json()
        saved = config_manager.update_scheduler(data)
        logger.info(f"[LiveDashboard] Zaktualizowano konfigurację harmonogramu: {saved.model_dump()}")
        return web.json_response(saved.model_dump())

    async def handle_get_llm_status(self, request: web.Request) -> web.Response:
        from src.filters.llm_analyzer import LLMAnalyzer

        cfg = config_manager.get_config()
        analyzer = LLMAnalyzer(
            enabled=cfg.llm_analysis_enabled,
            ollama_model=getattr(cfg, "ollama_model", None),
            ollama_base_url=getattr(cfg, "ollama_base_url", None),
            ollama_timeout_seconds=getattr(cfg, "ollama_timeout_seconds", None),
            ollama_temperature=getattr(cfg, "ollama_temperature", None),
            ollama_num_ctx=getattr(cfg, "ollama_num_ctx", None),
            local_llm_base_url=getattr(cfg, "local_llm_base_url", None),
            local_llm_model=getattr(cfg, "local_llm_model", None),
            local_llm_api_key=getattr(cfg, "local_llm_api_key", None),
            local_llm_temperature=getattr(cfg, "local_llm_temperature", None),
            local_llm_timeout_seconds=getattr(cfg, "local_llm_timeout_seconds", None),
            local_llm_preset=getattr(cfg, "local_llm_preset", None),
            local_llm_num_ctx=getattr(cfg, "local_llm_num_ctx", None),
            cloud_llm_timeout_seconds=getattr(cfg, "cloud_llm_timeout_seconds", None),
            openrouter_model=getattr(cfg, "openrouter_model", None),
            llm_provider=getattr(cfg, "llm_provider", None),
        )
        res = await analyzer.test_connection()
        return web.json_response(res)

    async def handle_test_llm_connection(self, request: web.Request) -> web.Response:
        from src.filters.llm_analyzer import LLMAnalyzer

        cfg = config_manager.get_config()
        ollama_model = None
        ollama_base_url = None
        ollama_timeout_seconds = None
        ollama_temperature = None
        ollama_num_ctx = None
        local_llm_base_url = None
        local_llm_model = None
        local_llm_api_key = None
        local_llm_temperature = None
        local_llm_timeout_seconds = None
        local_llm_preset = None
        local_llm_num_ctx = None
        cloud_llm_timeout_seconds = None
        openrouter_model = None
        llm_provider = None
        if request.can_read_body and (request.content_length or 0) > 0:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    if body.get("ollama_model"):
                        ollama_model = str(body["ollama_model"]).strip()
                    if body.get("ollama_base_url"):
                        ollama_base_url = str(body["ollama_base_url"]).strip().rstrip("/")
                    if body.get("ollama_timeout_seconds"):
                        try:
                            ollama_timeout_seconds = max(10.0, float(body["ollama_timeout_seconds"]))
                        except (TypeError, ValueError):
                            pass
                    if body.get("ollama_temperature") is not None:
                        try:
                            ollama_temperature = max(0.0, min(1.0, float(body["ollama_temperature"])))
                        except (TypeError, ValueError):
                            pass
                    if body.get("ollama_num_ctx"):
                        try:
                            ollama_num_ctx = max(2048, int(body["ollama_num_ctx"]))
                        except (TypeError, ValueError):
                            pass
                    if body.get("local_llm_base_url"):
                        local_llm_base_url = str(body["local_llm_base_url"]).strip().rstrip("/")
                    if body.get("local_llm_model") is not None:
                        local_llm_model = str(body["local_llm_model"]).strip()
                    if body.get("local_llm_api_key") is not None:
                        local_llm_api_key = str(body["local_llm_api_key"]).strip()
                    if body.get("local_llm_preset") is not None:
                        local_llm_preset = str(body["local_llm_preset"]).strip()
                    if body.get("local_llm_temperature") is not None:
                        try:
                            local_llm_temperature = max(0.0, min(1.0, float(body["local_llm_temperature"])))
                        except (TypeError, ValueError):
                            pass
                    if body.get("local_llm_timeout_seconds"):
                        try:
                            local_llm_timeout_seconds = max(10.0, float(body["local_llm_timeout_seconds"]))
                        except (TypeError, ValueError):
                            pass
                    if body.get("local_llm_num_ctx"):
                        try:
                            local_llm_num_ctx = max(2048, int(body["local_llm_num_ctx"]))
                        except (TypeError, ValueError):
                            pass
                    if body.get("cloud_llm_timeout_seconds"):
                        try:
                            cloud_llm_timeout_seconds = max(5.0, float(body["cloud_llm_timeout_seconds"]))
                        except (TypeError, ValueError):
                            pass
                    if body.get("openrouter_model"):
                        openrouter_model = str(body["openrouter_model"]).strip()
                    if body.get("llm_provider"):
                        llm_provider = str(body["llm_provider"]).strip()
            except Exception:
                pass

        analyzer = LLMAnalyzer(
            enabled=cfg.llm_analysis_enabled,
            ollama_model=ollama_model or getattr(cfg, "ollama_model", None),
            ollama_base_url=ollama_base_url or getattr(cfg, "ollama_base_url", None),
            ollama_timeout_seconds=ollama_timeout_seconds or getattr(cfg, "ollama_timeout_seconds", None),
            ollama_temperature=ollama_temperature
            if ollama_temperature is not None
            else getattr(cfg, "ollama_temperature", None),
            ollama_num_ctx=ollama_num_ctx if ollama_num_ctx is not None else getattr(cfg, "ollama_num_ctx", None),
            local_llm_base_url=local_llm_base_url or getattr(cfg, "local_llm_base_url", None),
            local_llm_model=local_llm_model if local_llm_model is not None else getattr(cfg, "local_llm_model", None),
            local_llm_api_key=local_llm_api_key
            if local_llm_api_key is not None
            else getattr(cfg, "local_llm_api_key", None),
            local_llm_temperature=local_llm_temperature
            if local_llm_temperature is not None
            else getattr(cfg, "local_llm_temperature", None),
            local_llm_timeout_seconds=local_llm_timeout_seconds or getattr(cfg, "local_llm_timeout_seconds", None),
            local_llm_preset=local_llm_preset or getattr(cfg, "local_llm_preset", None),
            local_llm_num_ctx=local_llm_num_ctx
            if local_llm_num_ctx is not None
            else getattr(cfg, "local_llm_num_ctx", None),
            cloud_llm_timeout_seconds=cloud_llm_timeout_seconds or getattr(cfg, "cloud_llm_timeout_seconds", None),
            openrouter_model=openrouter_model or getattr(cfg, "openrouter_model", None),
            llm_provider=llm_provider or getattr(cfg, "llm_provider", None),
        )
        res = await analyzer.test_connection()
        return web.json_response(res)

    async def handle_reset_data(self, request: web.Request) -> web.Response:
        data: dict[str, Any] = {}
        if request.can_read_body:
            try:
                data = await request.json()
            except Exception:
                data = {}
        if data.get("confirm") is not True:
            return web.json_response(
                {"error": 'Wymagane potwierdzenie: {"confirm": true}'},
                status=400,
            )

        profile_scope = str(data.get("profile") or "").strip()
        async with get_session() as session:
            repo = ListingRepository(session)
            if profile_scope and profile_scope.upper() != "ALL":
                target_name = None
                for p in config_manager.get_config().profiles:
                    if p.id == profile_scope or p.name.lower() == profile_scope.lower():
                        target_name = p.name
                        break
                deleted = await repo.delete_by_profile(profile_id=profile_scope, profile_name=target_name)
            else:
                deleted = await repo.delete_all_listings()

        scope_label = profile_scope or "ALL"
        logger.warning(f"[LiveDashboard] Reset danych: usunięto {deleted} ofert (zakres: {scope_label}).")
        return web.json_response({"success": True, "deleted_listings": deleted, "scope": scope_label})

    async def handle_scrape_status(self, request: web.Request) -> web.Response:
        from src.services.progress import global_tracker, read_shared_status

        payload = global_tracker.get_status_payload()
        shared = read_shared_status()

        if payload.get("is_running"):
            return web.json_response(payload)

        if shared and shared.get("is_running"):
            return web.json_response(shared)

        if (
            shared
            and (shared.get("logs") or shared.get("items_scraped", 0) > 0)
            and (not payload.get("logs") or len(shared.get("logs", [])) >= len(payload.get("logs", [])))
        ):
            return web.json_response(shared)

        return web.json_response(payload)

    async def handle_index(self, request: web.Request) -> web.Response:
        content = INDEX_HTML
        if TEMPLATE_PATH.exists():
            try:
                content = TEMPLATE_PATH.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Could not read template dynamically: {e}")
        content = _ASSET_URL_RE.sub(self._version_asset_ref, content)
        return web.Response(text=content, content_type="text/html", charset="utf-8")

    @staticmethod
    def _version_asset_ref(match: re.Match[str]) -> str:
        attr = match.group(1)
        path = match.group(2)
        version = _asset_version(path.removeprefix("/assets/"))
        if not version:
            return match.group(0)
        return f'{attr}="{path}?v={version}"'

    async def handle_assets(self, request: web.Request) -> web.Response:
        rel = str(request.match_info.get("path", ""))
        asset_path = (ASSET_DIR / rel).resolve()
        assets_root = ASSET_DIR.resolve()
        if not asset_path.is_relative_to(assets_root) or not asset_path.is_file():
            return web.Response(status=404, text="Not found")
        content = asset_path.read_bytes()
        etag = f'"{hashlib.md5(content, usedforsecurity=False).hexdigest()}"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304)
        ctype = ASSET_CONTENT_TYPES.get(asset_path.suffix.lower(), "application/octet-stream")
        return web.Response(
            body=content,
            content_type=ctype,
            charset=None,
            headers={"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"},
        )

    async def handle_image_proxy(self, request: web.Request) -> web.Response:
        """Proxy + on-disk cache for listing images (removes third-party cookies, unifies caching)."""
        raw_url = request.query.get("url", "")
        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return web.Response(status=400, text="Invalid image URL")
        if not _is_allowed_image_host(raw_url):
            return web.Response(status=403, text="Image host not allowed")

        cache_key = hashlib.md5(raw_url.encode("utf-8"), usedforsecurity=False).hexdigest()
        cache_path = self._img_cache_dir / cache_key
        meta_path = self._img_cache_dir / f"{cache_key}.ct"

        if cache_path.is_file():
            return self._serve_image_file(cache_path, meta_path, request)

        # Single-flight: concurrent requests for the same image share one fetch.
        task = self._img_inflight.get(cache_key)
        if task is None:
            task = asyncio.create_task(self._fetch_image(raw_url, cache_path, meta_path))
            self._img_inflight[cache_key] = task
            task.add_done_callback(lambda _t: self._img_inflight.pop(cache_key, None))
        try:
            await asyncio.shield(task)
        except Exception:
            pass

        if cache_path.is_file():
            return self._serve_image_file(cache_path, meta_path, request)
        return web.Response(status=502, text="Image fetch failed")

    @staticmethod
    def _serve_image_file(cache_path: Path, meta_path: Path, request: web.Request) -> web.Response:
        content = cache_path.read_bytes()
        etag = f'"{cache_path.stem}"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304)
        content_type = "image/jpeg"
        try:
            content_type = meta_path.read_text(encoding="utf-8").strip() or content_type
        except OSError:
            pass
        return web.Response(
            body=content,
            content_type=content_type,
            headers={"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"},
        )

    async def _fetch_image(self, url: str, cache_path: Path, meta_path: Path) -> None:
        try:
            if self._http is None:
                self._http = aiohttp.ClientSession()
            timeout = aiohttp.ClientTimeout(total=_IMG_FETCH_TIMEOUT_SECONDS)
            async with self._http.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    return
                content_type = resp.headers.get("Content-Type")
                data = await resp.read()
            if len(data) > _IMG_MAX_BYTES:
                return
            final_ct = _infer_image_content_type(content_type)
            tmp_path = cache_path.with_name(cache_path.name + ".tmp")
            tmp_path.write_bytes(data)
            tmp_path.replace(cache_path)
            meta_path.write_text(final_ct, encoding="utf-8")
        except Exception:
            return

    async def handle_get_price_history(self, request: web.Request) -> web.Response:
        listing_id = int(request.match_info["id"])
        async with get_session() as session:
            stmt = (
                select(PriceHistoryModel)
                .where(PriceHistoryModel.listing_id == listing_id)
                .order_by(PriceHistoryModel.recorded_at.desc())
            )
            res = await session.execute(stmt)
            items = res.scalars().all()
            data = [
                {
                    "price": h.price,
                    "price_per_m2": h.price_per_m2,
                    "date": h.recorded_at.isoformat() if h.recorded_at else None,
                }
                for h in items
            ]
            return web.json_response(data)

    async def handle_get_air_quality(self, request: web.Request) -> web.Response:
        try:
            listing_id = int(request.match_info["id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Nieprawidłowy identyfikator oferty"}, status=400)

        async with get_session() as session:
            repo = ListingRepository(session)
            item = await repo.get_by_id(listing_id)
            if not item:
                return web.json_response({"error": "Listing not found"}, status=404)
            if not item.latitude or not item.longitude:
                return web.json_response({"error": "Brak współrzędnych GPS dla tej oferty"}, status=400)

            from src.services.air_quality import air_quality_service

            aq_data = await air_quality_service.get_air_quality_audit(item.latitude, item.longitude)
            return web.json_response(aq_data)

    @staticmethod
    def _clean_image_urls(urls: list[str]) -> list[str]:
        """Drop non-photo assets (logos, SVGs) that leak into listing galleries."""
        cleaned: list[str] = []
        for u in urls or []:
            if not isinstance(u, str) or not u:
                continue
            low = u.lower()
            if low.endswith(".svg") or "/nuxt-assets/" in low:
                continue
            cleaned.append(u)
        return cleaned

    def _build_listing_dict(
        self,
        item: ListingModel,
        *,
        market_medians: dict[str, Any],
        now_utc: datetime,
        max_scraped_at: datetime | None,
        detail: bool,
    ) -> dict[str, Any]:
        """Serialize a listing. `detail=False` omits heavy audit blobs (land_audit,
        negotiation_arguments) and exposes only the `capex_total` scalar used by cards."""
        price_drop_amount = None
        price_drop_pct = None
        initial_price = None
        ph = item.price_history or []
        if len(ph) >= 2:
            oldest = ph[-1]
            initial_price = oldest.price
            if initial_price and initial_price > item.price:
                price_drop_amount = round(initial_price - item.price)
                price_drop_pct = round((price_drop_amount / initial_price) * 100, 1)

        created_utc = _as_utc(item.created_at)
        updated_utc = _as_utc(item.updated_at)

        is_new_cycle = bool(
            created_utc
            and (
                (max_scraped_at is not None and (max_scraped_at - created_utc).total_seconds() <= 10800)
                or (now_utc - created_utc).total_seconds() <= 86400
            )
        )

        is_updated_cycle = bool(
            not is_new_cycle
            and (
                len(ph) >= 2
                or (
                    updated_utc
                    and created_utc
                    and (updated_utc - created_utc).total_seconds() > 300
                    and (
                        (max_scraped_at is not None and (max_scraped_at - updated_utc).total_seconds() <= 10800)
                        or (now_utc - updated_utc).total_seconds() <= 86400
                    )
                )
            )
        )

        valuation = valuation_engine.evaluate(
            listing=item,
            market_medians=market_medians,
            price_drop_amount=float(price_drop_amount or 0.0),
            price_drop_pct=float(price_drop_pct or 0.0),
            price_history_count=len(ph),
        )
        vd = valuation.to_dashboard_dict()
        land_audit = vd.get("land_audit") or {}
        capex_total = (land_audit.get("tco_audit") or {}).get("total_acquisition_cost")

        data: dict[str, Any] = {
            "id": item.id,
            "portal": item.portal,
            "portal_id": item.portal_id,
            "url": item.url,
            "title": item.title,
            "price": item.price,
            "price_per_m2": item.price_per_m2,
            "area_home": item.area_home,
            "area_plot": item.area_plot,
            "building_type": item.building_type,
            "segment_subtype": item.segment_subtype,
            "location_raw": item.location_raw,
            "street": item.street,
            "district": item.district,
            "city": item.city,
            "latitude": item.latitude,
            "longitude": item.longitude,
            "is_exact_coords": item.is_exact_coords,
            "parcel_id": item.parcel_id,
            "cadastral_area": item.cadastral_area,
            "geoportal_url": item.geoportal_url,
            "mpzp_zone": item.mpzp_zone,
            "mpzp_status": item.mpzp_status,
            "flood_risk_zone": item.flood_risk_zone,
            "landslide_risk": item.landslide_risk,
            "egib_building_status": item.egib_building_status,
            "egib_soil_class": item.egib_soil_class,
            "noise_level_db": item.noise_level_db,
            "noise_zone": item.noise_zone,
            "nature_protected_zone": item.nature_protected_zone,
            "monument_zone": item.monument_zone,
            "cemetery_buffer_zone": item.cemetery_buffer_zone,
            "broadband_status": item.broadband_status,
            "broadband_details": item.broadband_details,
            "parcel_front_width_m": item.parcel_front_width_m,
            "parcel_length_m": item.parcel_length_m,
            "parcel_aspect_ratio": item.parcel_aspect_ratio,
            "parcel_shape_type": item.parcel_shape_type,
            "terrain_slope_pct": item.terrain_slope_pct,
            "terrain_aspect": item.terrain_aspect,
            "walkability_pka_dist_m": item.walkability_pka_dist_m,
            "walkability_pka_name": item.walkability_pka_name,
            "power_lines_risk": item.power_lines_risk,
            "gesut_networks": item.gesut_networks_data,
            "air_aqi": item.air_aqi,
            "air_aqi_label": item.air_aqi_label,
            "air_pm25_heating_avg": item.air_pm25_heating_avg,
            "air_pm25_summer_avg": item.air_pm25_summer_avg,
            "air_smog_days": item.air_smog_days,
            "air_gios_station": item.air_gios_station,
            "air_gios_dist_km": item.air_gios_dist_km,
            "air_gios_index": item.air_gios_index,
            "air_smog_risk": item.air_smog_risk,
            "user_status": item.user_status or "NEW",
            "user_notes": item.user_notes or "",
            "access_road_type": item.access_road_type,
            "market": item.market,
            "finish_condition": item.finish_condition or "nieokreślony",
            "has_visualisations": item.has_visualisations,
            "sewerage": item.sewerage,
            "heating": item.heating,
            "has_fiber": item.has_fiber,
            "year_built": item.year_built,
            "category": item.category or "dom",
            "rooms": item.rooms,
            "floor": item.floor,
            "floors_in_building": item.floors_in_building,
            "is_private_owner": item.is_private_owner,
            "profile_id": item.profile_id or "default",
            "profile_name": item.profile_name,
            "main_image_url": item.main_image_url,
            "gallery_images": self._clean_image_urls(item.gallery_images),
            "is_qualified": item.is_qualified,
            "qualification_status": item.qualification_status,
            "qualification_score": item.qualification_score,
            "filter_reasons": item.filter_reasons,
            "pros": item.pros,
            "cons": item.cons,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            "last_scraped_at": item.last_scraped_at.isoformat() if item.last_scraped_at else None,
            "is_new_cycle": is_new_cycle,
            "is_updated_cycle": is_updated_cycle,
            "ai_summary": item.ai_summary,
            "ai_verdict": item.ai_verdict,
            "worth_interest": item.worth_interest,
            "ai_questions": item.ai_questions,
            "contact_phone": item.contact_phone,
            "contact_person": item.contact_person,
            "listing_status": getattr(item, "listing_status", None) or "ACTIVE",
            "relist_count": getattr(item, "relist_count", 0) or 0,
            "first_seen_at": item.first_seen_at.isoformat() if item.first_seen_at is not None else None,
            "price_drop_amount": price_drop_amount,
            "price_drop_pct": price_drop_pct,
            "initial_price": getattr(item, "initial_price", None) or initial_price,
            "price_history_count": len(ph),
            "capex_total": capex_total,
        }

        # Spread scalar valuation fields (skip the heavy detail-only blobs).
        for key, val in vd.items():
            if key not in ("land_audit", "negotiation_arguments"):
                data[key] = val

        if detail:
            data["land_audit"] = land_audit
            data["negotiation_arguments"] = vd.get("negotiation_arguments") or []
        else:
            for key in _LIST_OMIT_FIELDS:
                data.pop(key, None)

        return data

    async def handle_get_listing_detail(self, request: web.Request) -> web.Response:
        try:
            listing_id = int(request.match_info["id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Nieprawidłowy identyfikator oferty"}, status=400)

        async with get_session() as session:
            stmt = (
                select(ListingModel)
                .options(selectinload(ListingModel.price_history))
                .where(ListingModel.id == listing_id)
            )
            res = await session.execute(stmt)
            item = res.scalars().one_or_none()
            if not item:
                return web.json_response({"error": "Listing not found"}, status=404)

            repo = ListingRepository(session)
            market_medians = await repo.get_market_medians()
            now_utc = datetime.now(UTC)
            data = self._build_listing_dict(
                item,
                market_medians=market_medians,
                now_utc=now_utc,
                max_scraped_at=_as_utc(item.last_scraped_at),
                detail=True,
            )
            return web.json_response(data)

    async def handle_get_listings(self, request: web.Request) -> web.Response:
        prof_filter = request.query.get("profile")
        cache_key = (prof_filter or "").strip().lower()

        # Cheap global dirty-check: count + max(updated_at) change on any add/update/delete,
        # so a 45s poll can be answered from cache without re-querying or re-serializing.
        async with get_session() as session:
            row = (await session.execute(select(func.count(), func.max(ListingModel.updated_at)))).one()
        fingerprint = f"{int(row[0] or 0)}:{row[1]}"

        cached = self._listings_cache.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            _, etag, body = cached
            if request.headers.get("If-None-Match") == etag:
                return web.Response(status=304)
            return web.Response(body=body, content_type="application/json", headers={"ETag": etag})

        async with get_session() as session:
            stmt = select(ListingModel).options(selectinload(ListingModel.price_history))
            if prof_filter and prof_filter.upper() != "ALL":
                cfg = config_manager.get_config()
                matched_prof = None
                for p in cfg.profiles:
                    if p.id == prof_filter or p.name.lower() == prof_filter.lower():
                        matched_prof = p
                        break
                conds = [ListingModel.profile_id == prof_filter, ListingModel.profile_name == prof_filter]
                if matched_prof:
                    conds.append(ListingModel.profile_id == matched_prof.id)
                    conds.append(ListingModel.profile_name == matched_prof.name)
                # Legacy records without a real profile assignment are matched by the profile's city
                if matched_prof:
                    legacy_cond = or_(ListingModel.profile_id.is_(None), ListingModel.profile_id == "default")
                    if matched_prof.city:
                        conds.append(and_(legacy_cond, ListingModel.city == matched_prof.city))
                    else:
                        conds.append(legacy_cond)
                stmt = stmt.where(or_(*conds))

            stmt = stmt.order_by(
                desc(ListingModel.is_qualified),
                desc(ListingModel.qualification_score),
                desc(ListingModel.created_at),
            )
            res = await session.execute(stmt)
            items = res.scalars().all()

            repo = ListingRepository(session)
            market_medians = await repo.get_market_medians()

            now_utc = datetime.now(UTC)
            max_scraped_at = None
            for it in items:
                if (sa := _as_utc(it.last_scraped_at)) and (max_scraped_at is None or sa > max_scraped_at):
                    max_scraped_at = sa

            data: list[dict[str, Any]] = []
            for item in items:
                data.append(
                    self._build_listing_dict(
                        item,
                        market_medians=market_medians,
                        now_utc=now_utc,
                        max_scraped_at=max_scraped_at,
                        detail=False,
                    )
                )

            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            etag = f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"'
            self._listings_cache[cache_key] = (fingerprint, etag, body)
            if request.headers.get("If-None-Match") == etag:
                return web.Response(status=304)
            return web.json_response(body=body, headers={"ETag": etag})

    async def handle_update_status(self, request: web.Request) -> web.Response:
        listing_id = int(request.match_info["id"])
        data = await request.json()
        new_status = data.get("status", "NEW")

        async with get_session() as session:
            repo = ListingRepository(session)
            item = await repo.update_user_status(listing_id, new_status)
            if not item:
                return web.json_response({"error": "Listing not found"}, status=404)
            await safe_commit(session)
            logger.info(f"[LiveDashboard] Listing #{listing_id} status updated to: {new_status}")
            return web.json_response({"success": True, "id": listing_id, "user_status": new_status})

    async def handle_update_notes(self, request: web.Request) -> web.Response:
        listing_id = int(request.match_info["id"])
        data = await request.json()
        notes = data.get("notes", "")

        async with get_session() as session:
            repo = ListingRepository(session)
            item = await repo.update_user_notes(listing_id, notes)
            if not item:
                return web.json_response({"error": "Listing not found"}, status=404)
            await safe_commit(session)
            logger.info(f"[LiveDashboard] Listing #{listing_id} notes updated.")
            return web.json_response({"success": True, "id": listing_id, "user_notes": notes})

    async def handle_generate_ai_audit(self, request: web.Request) -> web.Response:
        try:
            listing_id = int(request.match_info["id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "Nieprawidłowy identyfikator oferty"}, status=400)

        async with get_session() as session:
            repo = ListingRepository(session)
            item = await repo.get_by_id(listing_id)
            if not item:
                return web.json_response({"error": "Nie znaleziono oferty w bazie danych"}, status=404)

            if not item.raw_description or not item.raw_description.strip():
                return web.json_response({"error": "Oferta nie posiada opisu do analizy przez AI"}, status=400)

            from src.filters.llm_analyzer import LLMAnalyzer
            from src.models.enums import (
                BuildingType,
                FinishCondition,
                HeatingType,
                MarketType,
                PropertyCategory,
                RoadType,
                SegmentSubtype,
                SewerageType,
            )
            from src.models.listing import ListingSchema

            coords = (
                (item.latitude, item.longitude) if item.latitude is not None and item.longitude is not None else None
            )

            finish_map = {
                "do zamieszkania": FinishCondition.DO_ZAMIESZKANIA,
                "pod_klucz": FinishCondition.DO_ZAMIESZKANIA,
                "do wykończenia": FinishCondition.DO_WYKONCZENIA,
                "do_wykonczenia": FinishCondition.DO_WYKONCZENIA,
                "deweloperski": FinishCondition.DEWELOPERSKI,
                "do remontu": FinishCondition.DO_REMONTU,
                "do_remontu": FinishCondition.DO_REMONTU,
                "surowy zamknięty": FinishCondition.SUROWY_ZAMKNIETY,
                "surowy_zamkniety": FinishCondition.SUROWY_ZAMKNIETY,
                "surowy otwarty": FinishCondition.SUROWY_OTWARTY,
                "surowy_otwarty": FinishCondition.SUROWY_OTWARTY,
            }
            finish_condition_enum = finish_map.get(
                str(item.finish_condition or "").lower(), FinishCondition.NIEOKRESLONY
            )

            try:
                prop_category = PropertyCategory(item.category)
            except (ValueError, TypeError):
                prop_category = PropertyCategory.DOM

            try:
                market_type = MarketType(item.market)
            except (ValueError, TypeError):
                market_type = MarketType.NIEOKRESLONY

            try:
                b_type = BuildingType(item.building_type)
            except (ValueError, TypeError):
                b_type = BuildingType.INNY

            try:
                s_subtype = SegmentSubtype(item.segment_subtype)
            except (ValueError, TypeError):
                s_subtype = SegmentSubtype.NIEOKRESLONY

            try:
                road_type = RoadType(item.access_road_type)
            except (ValueError, TypeError):
                road_type = RoadType.NIEZNANA

            try:
                sewerage_type = SewerageType(item.sewerage)
            except (ValueError, TypeError):
                sewerage_type = SewerageType.NIEZNANA

            try:
                heating_type = HeatingType(item.heating)
            except (ValueError, TypeError):
                heating_type = HeatingType.NIEZNANE

            schema = ListingSchema(
                id=str(item.portal_id or item.id),
                portal=item.portal,
                title=item.title,
                url=item.url,
                price=item.price,
                price_per_m2=item.price_per_m2,
                area_home=item.area_home,
                area_plot=item.area_plot,
                category=prop_category,
                rooms=item.rooms,
                floor=item.floor,
                floors_in_building=item.floors_in_building,
                is_private_owner=item.is_private_owner,
                building_type=b_type,
                segment_subtype=s_subtype,
                location_raw=item.location_raw or "",
                street=item.street,
                district=item.district,
                city=item.city,
                coordinates=coords,
                access_road_type=road_type,
                market=market_type,
                finish_condition=finish_condition_enum,
                has_visualisations=bool(item.has_visualisations),
                sewerage=sewerage_type,
                heating=heating_type,
                has_fiber=bool(item.has_fiber),
                year_built=item.year_built,
                raw_description=item.raw_description,
                main_image_url=item.main_image_url,
                parcel_id=item.parcel_id,
                cadastral_area=item.cadastral_area,
                geoportal_url=item.geoportal_url,
                mpzp_zone=item.mpzp_zone,
                mpzp_status=item.mpzp_status,
                flood_risk_zone=item.flood_risk_zone,
            )

            analyzer = LLMAnalyzer(enabled=True)
            logger.info(f"[LiveDashboard] Generowanie audytu AI na żądanie dla #{item.id} '{item.title[:35]}'")
            insights = await analyzer.analyze_description(schema)
            if not insights:
                return web.json_response(
                    {
                        "error": "Model AI nie zwrócił odpowiedzi. Sprawdź klucz API (np. OPENROUTER_API_KEY) lub Ollama."
                    },
                    status=502,
                )

            item.ai_summary = insights.get("summary")
            item.ai_verdict = insights.get("verdict")
            item.worth_interest = insights.get("worth_interest")
            q_list = insights.get("questions_for_agent") or []
            item._ai_questions = json.dumps(q_list, ensure_ascii=False)

            if insights.get("contact_phone") and not item.contact_phone:
                item.contact_phone = str(insights.get("contact_phone"))
            if insights.get("contact_person") and not item.contact_person:
                item.contact_person = str(insights.get("contact_person"))

            finish_raw = str(insights.get("finish_condition") or "").lower()
            if finish_raw in finish_map:
                item.finish_condition = finish_map[finish_raw].value

            if insights.get("has_visualisations") is not None:
                item.has_visualisations = bool(insights.get("has_visualisations"))

            item_pros = list(item.pros or [])
            for p in insights.get("pros") or []:
                llm_p = f"[LLM] {p}" if not p.startswith("[LLM]") else p
                if llm_p not in item_pros:
                    item_pros.append(llm_p)
            item.pros = item_pros

            item_cons = list(item.cons or [])
            for c in insights.get("cons") or []:
                llm_c = f"[LLM] {c}" if not c.startswith("[LLM]") else c
                if llm_c not in item_cons:
                    item_cons.append(llm_c)
            for c in insights.get("hidden_costs") or []:
                cost_c = f"⚠️ [Ukryty koszt] {c}"
                if cost_c not in item_cons:
                    item_cons.append(cost_c)
            for r in insights.get("legal_risks") or []:
                risk_c = f"⚖️ [Ryzyko prawne] {r}"
                if risk_c not in item_cons:
                    item_cons.append(risk_c)
            item.cons = item_cons

            item.updated_at = datetime.now(UTC)
            await safe_commit(session)

            logger.info(f"[LiveDashboard] Pomyślnie zapisano audyt AI dla #{item.id}")

            return web.json_response(
                {
                    "id": item.id,
                    "ai_summary": item.ai_summary,
                    "ai_verdict": item.ai_verdict,
                    "worth_interest": item.worth_interest,
                    "ai_questions": q_list,
                    "contact_phone": item.contact_phone,
                    "contact_person": item.contact_person,
                    "finish_condition": item.finish_condition,
                    "has_visualisations": item.has_visualisations,
                    "pros": item.pros,
                    "cons": item.cons,
                }
            )

    async def handle_backfill_coords(self, request: web.Request) -> web.Response:
        from src.services.geocoder import backfill_missing_coordinates
        from src.services.pipeline import ScraperPipeline
        from src.storage.database import get_session
        from src.storage.repository import ListingRepository

        coords_count = await backfill_missing_coordinates()
        spatial_count = 0
        try:
            pipeline = ScraperPipeline()
            async with get_session() as session:
                repo = ListingRepository(session)
                spatial_count = await pipeline.backfill_existing_spatial_data(session, repo)
        except Exception as e:
            logger.warning(f"[LiveDashboard] Spatial backfill error: {e}")

        return web.json_response(
            {
                "success": True,
                "coords_updated": coords_count,
                "spatial_updated": spatial_count,
                "updated": coords_count + spatial_count,
            }
        )

    async def _run_scrape_background(self, target_profile: str | None = None) -> None:
        from src.services.progress import global_tracker

        try:
            pipeline = ScraperPipeline()
            await pipeline.run_cycle(target_profile=target_profile)
        except asyncio.CancelledError:
            logger.info("[LiveDashboard] Background scrape task cancelled.")
            global_tracker.cancel_session()
        except Exception as e:
            logger.error(f"[LiveDashboard] Background scrape error: {e}", exc_info=True)
            global_tracker.add_log(f"Błąd krytyczny scrapingu: {e}", level="error")
            global_tracker.complete_session({"error": str(e)})
        finally:
            self._active_scrape_task = None
            clear_medians_cache()
            self._listings_cache.clear()

    async def handle_trigger_scrape(self, request: web.Request) -> web.Response:
        from src.services.progress import global_tracker
        from src.services.scrape_lock import get_scrape_lock

        lock = get_scrape_lock()
        if global_tracker.is_running or lock.is_locked():
            return web.json_response(
                {"status": "already_running", "message": "Scraping jest już w toku (w tle)."},
                status=409,
            )

        target_profile = request.query.get("profile")
        if not target_profile and request.can_read_body and (request.content_length or 0) > 0:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    target_profile = body.get("profile")
            except Exception:
                pass

        if target_profile and isinstance(target_profile, str):
            target_profile = target_profile.strip()
            if target_profile.upper() in ("ALL", "NULL", "NONE", ""):
                target_profile = None

        logger.info(f"[LiveDashboard] Manual scrape triggered via Web UI (target_profile: {target_profile}).")
        self._active_scrape_task = asyncio.create_task(self._run_scrape_background(target_profile=target_profile))
        return web.json_response({"status": "started", "message": "Scraping uruchomiony w tle."})

    async def handle_cancel_scrape(self, request: web.Request) -> web.Response:
        from src.services.progress import (
            global_tracker,
            read_shared_status,
            signal_shared_cancellation,
            write_shared_status,
        )
        from src.services.scrape_lock import get_scrape_lock

        logger.info("[LiveDashboard] Stop scrape requested via Web UI.")
        shared = read_shared_status()
        lock = get_scrape_lock()
        is_running = bool(
            global_tracker.is_running
            or (shared and shared.get("is_running"))
            or lock.is_locked()
            or (self._active_scrape_task and not self._active_scrape_task.done())
        )

        if not is_running:
            if shared and (
                shared.get("is_running")
                or shared.get("current_step") not in ("Bezczynny", "Zatrzymano", "Zakończono pomyślnie!")
            ):
                shared["is_running"] = False
                shared["current_step"] = "Zatrzymano"
                write_shared_status(shared)
            global_tracker.reset()
            return web.json_response(
                {"status": "not_running", "message": "Scraping nie jest obecnie uruchomiony."},
                status=200,
            )

        global_tracker.request_cancel()
        signal_shared_cancellation()
        if shared:
            shared["cancel_requested"] = True
            shared["current_step"] = "Zatrzymywanie procesu..."
            write_shared_status(shared)

        if self._active_scrape_task and not self._active_scrape_task.done():
            self._active_scrape_task.cancel()
        return web.json_response({"status": "cancelling", "message": "Zażądano zatrzymania scrapingu."})

    async def run(self, auto_open: bool = True):
        await init_db()
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        try:
            await site.start()
        except OSError as e:
            if getattr(e, "errno", None) in (10048, 48, 98):  # WinError 10048 / EADDRINUSE
                logger.error(
                    f"Port {self.port} jest już zajęty przez inny proces (np. działający w tle serwer dashboardu). "
                    f"Zatrzymaj poprzedni proces lub wybierz inny port, np.: python main.py dashboard --port {self.port + 1}"
                )
                await runner.cleanup()
                return
            raise

        display_host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        display_url = f"http://{display_host}:{self.port}"
        logger.success(
            f"🚀 Live Preview Dashboard aktywny pod adresem: {display_url} (nasłuch na {self.host}:{self.port})"
        )
        print("\n========================================================")
        print("  🏡 LIVE UNIVERSAL DASHBOARD & CRM DZIAŁA:")
        print(f"     {display_url}")
        print("  (Naciśnij Ctrl+C aby zatrzymać serwer)")
        print("========================================================\n")

        if auto_open:
            try:
                webbrowser.open(display_url)
            except Exception as e:
                logger.debug(f"Could not open browser automatically: {e}")

        try:
            while True:
                await asyncio.sleep(3600)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await runner.cleanup()
            if self._http is not None:
                await self._http.close()
            logger.info("[LiveDashboard] Serwer zatrzymany.")
