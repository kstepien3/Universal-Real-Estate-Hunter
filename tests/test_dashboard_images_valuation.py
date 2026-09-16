"""Tests for server-side image derivatives, image cache hygiene, valuation DB cache."""

from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from src.models.enums import QualificationStatus
from src.models.listing import FilterResult, ListingSchema
from src.services.live_dashboard import (
    _IMG_DERIVATIVES,
    LiveDashboardServer,
    _is_allowed_image_host,
    _sweep_img_cache,
)
from src.storage import ListingRepository, get_session, init_db


@pytest.mark.parametrize(
    "url",
    [
        "https://ireland.apollo.olxcdn.com/v1/files/x/image;s=1200x801",
        "https://ireland.apollo.olxcdn.com:443/v1/files/x/image;s=1200x801",
        "https://i.st-nieruchomosci-online.pl/kdg5vqc/dom-rzeszow.jpg",
        "https://img1.staticmorizon.com.pl/thumb/x",
        "https://img.otodom.pl/x.jpg",
    ],
)
def test_image_proxy_allows_real_portal_cdns(url: str) -> None:
    assert _is_allowed_image_host(url) is True


@pytest.mark.parametrize(
    "url",
    ["https://evil.example.com/x.jpg", "https://olxcdn.com.evil.com/x.jpg", "not-a-url", ""],
)
def test_image_proxy_blocks_unlisted_hosts(url: str) -> None:
    assert _is_allowed_image_host(url) is False


def _make_image(path: Path, size: tuple[int, int], color: tuple[int, int, int] = (90, 120, 160)) -> Path:
    img = Image.new("RGB", size, color)
    img.save(path, "JPEG", quality=90)
    return path


def test_render_derivative_card_exact_16x9(tmp_path: Path) -> None:
    """Card derivatives are exact 640x360 regardless of source ratio (no client stretch)."""
    server = LiveDashboardServer(port=8099)
    for src_size in [(1000, 400), (400, 1000), (640, 360)]:
        orig = _make_image(tmp_path / f"orig_{src_size[0]}x{src_size[1]}.jpg", src_size)
        deriv = tmp_path / f"card_{src_size[0]}.jpg"
        meta = Path(str(deriv) + ".ct")
        assert server._render_derivative(orig, deriv, meta, "card") is True
        with Image.open(deriv) as out:
            assert out.size == (_IMG_DERIVATIVES["card"]["width"], _IMG_DERIVATIVES["card"]["height"])
            assert out.format == "JPEG"
        assert meta.read_text(encoding="utf-8") == "image/jpeg"


def test_render_derivative_thumb_and_large(tmp_path: Path) -> None:
    server = LiveDashboardServer(port=8099)
    orig = _make_image(tmp_path / "orig.jpg", (2000, 1500))
    thumb = tmp_path / "t.jpg"
    assert server._render_derivative(orig, thumb, Path(str(thumb) + ".ct"), "thumb") is True
    with Image.open(thumb) as out:
        assert out.size == (_IMG_DERIVATIVES["thumb"]["width"], _IMG_DERIVATIVES["thumb"]["height"])

    large = tmp_path / "l.jpg"
    assert server._render_derivative(orig, large, Path(str(large) + ".ct"), "large") is True
    with Image.open(large) as out:
        assert max(out.size) <= _IMG_DERIVATIVES["large"]["max_edge"]


def test_render_derivative_large_passthrough_small_original(tmp_path: Path) -> None:
    """Small originals pass through byte-identical for the lightbox (no quality loss)."""
    server = LiveDashboardServer(port=8099)
    orig = _make_image(tmp_path / "small.jpg", (800, 600))
    assert orig.stat().st_size <= _IMG_DERIVATIVES["large"]["passthrough_bytes"]
    large = tmp_path / "large.jpg"
    assert server._render_derivative(orig, large, Path(str(large) + ".ct"), "large") is True
    assert large.read_bytes() == orig.read_bytes()


def test_render_derivative_rejects_non_image(tmp_path: Path) -> None:
    server = LiveDashboardServer(port=8099)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"definitely not an image")
    deriv = tmp_path / "out.jpg"
    assert server._render_derivative(bad, deriv, Path(str(deriv) + ".ct"), "card") is False
    assert not deriv.exists()


def test_sweep_img_cache_ttl_and_orphans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    import src.services.live_dashboard as dash

    monkeypatch.setattr(dash, "_IMG_CACHE_TTL_SECONDS", 10)
    fresh = tmp_path / "aa"
    fresh.write_bytes(b"x" * 100)
    (tmp_path / "aa.ct").write_text("image/jpeg", encoding="utf-8")
    old = tmp_path / "bb"
    old.write_bytes(b"y" * 100)
    (tmp_path / "bb.ct").write_text("image/jpeg", encoding="utf-8")
    ancient = time.time() - 3600
    import os

    os.utime(old, (ancient, ancient))
    os.utime(tmp_path / "bb.ct", (ancient, ancient))
    orphan = tmp_path / "zz.ct"
    orphan.write_text("image/jpeg", encoding="utf-8")

    removed = _sweep_img_cache(tmp_path)
    assert removed >= 3
    assert fresh.is_file()
    assert not old.is_file()
    assert not (tmp_path / "bb.ct").is_file()
    assert not orphan.is_file()


def test_sweep_img_cache_size_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import time

    import src.services.live_dashboard as dash

    monkeypatch.setattr(dash, "_IMG_CACHE_MAX_BYTES", 300)
    for i in range(5):
        p = tmp_path / f"f{i}"
        p.write_bytes(b"z" * 100)
        ts = time.time() - i
        os.utime(p, (ts, ts))
    removed = _sweep_img_cache(tmp_path)
    assert removed > 0
    total = sum(p.stat().st_size for p in tmp_path.iterdir() if p.is_file())
    assert total <= 300


async def test_image_proxy_rejects_bad_size_and_host() -> None:
    await init_db()
    server = LiveDashboardServer(port=8098)
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.get("/img", params={"url": "https://img.otodom.pl/x.jpg", "size": "bogus"})
        assert resp.status == 400
        resp = await client.get("/img", params={"url": "not-a-url"})
        assert resp.status == 400
        resp = await client.get("/img", params={"url": "https://evil.example.com/x.jpg"})
        assert resp.status == 403


async def test_image_proxy_serves_cached_derivative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-rendered derivative is served without any upstream fetch."""
    await init_db()
    server = LiveDashboardServer(port=8097)
    monkeypatch.setattr(server, "_img_cache_dir", tmp_path)

    import hashlib

    url = "https://img.otodom.pl/abc123.jpg"
    key = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest() + ".card"
    orig = _make_image(tmp_path / "orig.jpg", (900, 500))
    assert server._render_derivative(orig, tmp_path / key, Path(str(tmp_path / key) + ".ct"), "card") is True

    async with TestClient(TestServer(server.app)) as client:
        resp = await client.get("/img", params={"url": url, "size": "card"})
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "image/jpeg"
        body = await resp.read()
        assert body[:2] == b"\xff\xd8"  # JPEG magic


async def test_listings_lean_payload_and_gallery_count() -> None:
    await init_db()
    server = LiveDashboardServer(port=8096)
    gallery = [f"https://img.otodom.pl/photo{i}.jpg" for i in range(10)]
    listing = ListingSchema(
        id="lean-1",
        portal="Otodom",
        title="Dom lean payload",
        url="https://otodom.pl/lean-1",
        price=800_000,
        price_per_m2=6_400.0,
        area_home=125.0,
        area_plot=700.0,
        location_raw="Rzeszów",
        gallery_images=gallery,
    )
    async with get_session() as session:
        repo = ListingRepository(session)
        filt = FilterResult(
            is_qualified=True, status=QualificationStatus.QUALIFIED, passed_stage1=True, passed_stage2=True
        )
        await repo.save_or_update(listing, filt)

    async with TestClient(TestServer(server.app)) as client:
        data = await (await client.get("/api/listings")).json()
        target = next(i for i in data if i.get("portal_id") == "lean-1")
        assert target["gallery_count"] == 10
        assert len(target["gallery_images"]) <= 6
        assert len(target.get("pros", [])) <= 3
        assert len(target.get("cons", [])) <= 2
        detail = await (await client.get(f"/api/listings/{target['id']}")).json()
        assert len(detail["gallery_images"]) == 10


async def test_valuation_cache_hit_avoids_recompute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second identical /api/listings must serve valuation scalars without evaluate()."""
    await init_db()
    server = LiveDashboardServer(port=8095)
    listing = ListingSchema(
        id="valcache-1",
        portal="Otodom",
        title="Dom valuation cache",
        url="https://otodom.pl/valcache-1",
        price=900_000,
        price_per_m2=7_200.0,
        area_home=125.0,
        area_plot=600.0,
        location_raw="Rzeszów",
    )
    async with get_session() as session:
        repo = ListingRepository(session)
        filt = FilterResult(
            is_qualified=True, status=QualificationStatus.QUALIFIED, passed_stage1=True, passed_stage2=True
        )
        await repo.save_or_update(listing, filt)

    async with TestClient(TestServer(server.app)) as client:
        first = await (await client.get("/api/listings")).json()
        target = next(i for i in first if i.get("portal_id") == "valcache-1")
        assert "capex_total" in target
        assert "fair_market_value" in target

        async with get_session() as session:
            repo = ListingRepository(session)
            stored = await repo.get_by_id(target["id"])
            assert stored is not None and stored.valuation_version

        import src.services.live_dashboard as dash

        def _boom(*args: object, **kwargs: object) -> object:
            raise AssertionError("evaluate() must not run on a warm valuation cache")

        monkeypatch.setattr(dash.valuation_engine, "evaluate", _boom)
        second = await (await client.get("/api/listings")).json()
        target2 = next(i for i in second if i.get("portal_id") == "valcache-1")
        assert target2["capex_total"] == target["capex_total"]
        assert target2["fair_market_value"] == target["fair_market_value"]


async def test_invalid_ids_return_400_not_500() -> None:
    await init_db()
    server = LiveDashboardServer(port=8094)
    async with TestClient(TestServer(server.app)) as client:
        for path in (
            "/api/listings/abc/price-history",
            "/api/listings/abc",
        ):
            resp = await client.get(path)
            assert resp.status in (400, 404)
        resp = await client.patch("/api/listings/abc/status", json={"status": "FAVORITE"})
        assert resp.status == 400
        resp = await client.patch("/api/listings/abc/notes", json={"notes": "x"})
        assert resp.status == 400


async def _seed_overview_listings() -> None:
    async with get_session() as session:
        repo = ListingRepository(session)
        filt = FilterResult(
            is_qualified=True, status=QualificationStatus.QUALIFIED, passed_stage1=True, passed_stage2=True
        )
        await repo.save_or_update(
            ListingSchema(
                id="ov-1",
                portal="Otodom",
                title="Dom overview 1",
                url="https://otodom.pl/ov-1",
                price=800_000,
                price_per_m2=6_400.0,
                area_home=125.0,
                location_raw="Rzeszów",
            ),
            filt,
        )
        await repo.save_or_update(
            ListingSchema(
                id="ov-2",
                portal="OLX",
                title="Dom overview 2",
                url="https://olx.pl/ov-2",
                price=900_000,
                price_per_m2=7_200.0,
                area_home=125.0,
                location_raw="Kraków",
            ),
            filt,
        )


def _mock_update_check(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    from src.services import live_dashboard as dash

    async def _fake(self: object) -> dict:
        return dict(payload)

    monkeypatch.setattr(dash.LiveDashboardServer, "_check_for_updates", _fake)


async def test_overview_endpoint_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.version import __version__

    _mock_update_check(
        monkeypatch,
        {"status": "available", "latest_version": "9.9.9", "url": "https://example.com/r", "checked_at": None},
    )
    await init_db()
    await _seed_overview_listings()
    server = LiveDashboardServer(port=8093)
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.get("/api/overview")
        assert resp.status == 200
        ov = await resp.json()
        assert ov["version"] == __version__
        assert ov["environment"] in ("docker", "local")
        assert ov["database"]["backend"] in ("sqlite", "postgresql")
        assert ov["listings"]["total"] >= 2
        assert ov["listings"]["qualified"] >= 2
        assert isinstance(ov["listings"]["by_profile"], list)
        assert ov["images"]["cap_bytes"] > 0
        assert ov["images"]["ttl_days"] > 0
        assert ov["sync"]["last_sync_at"] is None or isinstance(ov["sync"]["last_sync_at"], str)
        assert "config" in ov and "scheduler" in ov["config"] and "llm" in ov["config"]
        assert ov["update"]["status"] == "available"
        assert ov["update"]["latest_version"] == "9.9.9"


async def test_overview_counts_favorites_and_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_update_check(monkeypatch, {"status": "current", "latest_version": None, "url": None, "checked_at": None})
    await init_db()
    await _seed_overview_listings()
    server = LiveDashboardServer(port=8092)
    async with TestClient(TestServer(server.app)) as client:
        data = await (await client.get("/api/listings")).json()
        target = next(i for i in data if i.get("portal_id") == "ov-1")
        resp = await client.patch(f"/api/listings/{target['id']}/status", json={"status": "FAVORITE"})
        assert resp.status == 200
        ov = await (await client.get("/api/overview")).json()
        assert ov["listings"]["favorites"] >= 1


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v1.12.1", (1, 12, 1)),
        ("1.13.0", (1, 13, 0)),
        ("V2.0", (2, 0, 0)),
        ("v1.12", (1, 12, 0)),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_version_tag(tag: str | None, expected: tuple[int, int, int] | None) -> None:
    from src.services.live_dashboard import _parse_version_tag

    assert _parse_version_tag(tag) == expected


class _FakeUpdateResp:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status = status
        self._payload = payload or {}

    async def __aenter__(self) -> "_FakeUpdateResp":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def json(self) -> dict:
        return dict(self._payload)


class _FakeUpdateSession:
    def __init__(self, status: int = 200, payload: dict | None = None, fail: bool = False) -> None:
        self.calls = 0
        self._status = status
        self._payload = payload
        self._fail = fail

    def get(self, *args: object, **kwargs: object) -> _FakeUpdateResp:
        self.calls += 1
        if self._fail:
            raise ConnectionError("offline")
        return _FakeUpdateResp(self._status, self._payload)


def _update_server(
    payload: dict | None, status: int = 200, fail: bool = False
) -> tuple[LiveDashboardServer, _FakeUpdateSession]:
    server = LiveDashboardServer(port=8091)
    fake: Any = _FakeUpdateSession(status=status, payload=payload, fail=fail)
    server._http = fake
    return server, fake


async def test_update_check_detects_newer_release() -> None:
    server, fake = _update_server({"tag_name": "v99.0.0", "html_url": "https://example.com/r/v99.0.0"})
    first = await server._check_for_updates()
    assert first["status"] == "available"
    assert first["latest_version"] == "99.0.0"
    assert first["url"] == "https://example.com/r/v99.0.0"
    second = await server._check_for_updates()
    assert second == first
    assert fake.calls == 1  # served from the 12h cache


async def test_update_check_current_when_same_version() -> None:
    from src.version import __version__

    server, _ = _update_server({"tag_name": f"v{__version__}", "html_url": "https://example.com/r"})
    result = await server._check_for_updates()
    assert result["status"] == "current"


@pytest.mark.parametrize("status", [404, 500])
async def test_update_check_unknown_on_bad_status(status: int) -> None:
    server, _ = _update_server({}, status=status)
    result = await server._check_for_updates()
    assert result["status"] == "unknown"


async def test_update_check_unknown_when_offline() -> None:
    server, _ = _update_server(None, fail=True)
    result = await server._check_for_updates()
    assert result["status"] == "unknown"


async def test_update_endpoint_uses_cached_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_update_check(monkeypatch, {"status": "current", "latest_version": None, "url": None, "checked_at": None})
    await init_db()
    server = LiveDashboardServer(port=8090)
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.get("/api/update")
        assert resp.status == 200
        assert (await resp.json())["status"] == "current"
