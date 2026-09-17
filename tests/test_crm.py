import asyncio

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.enums import BuildingType, QualificationStatus, SegmentSubtype, UserCRMStatus
from src.models.listing import FilterResult, ListingSchema
from src.services.geocoder import NominatimGeocoder
from src.services.live_dashboard import LiveDashboardServer
from src.storage.models import Base
from src.storage.repository import ListingRepository


@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_crm_status_and_notes(test_session: AsyncSession):
    repo = ListingRepository(test_session)

    listing = ListingSchema(
        id="crm-test-1",
        portal="Otodom",
        title="Dom w Słocinie CRM Test",
        url="https://otodom.pl/oferta/crm-test-1",
        price=950_000,
        price_per_m2=8_500.0,
        area_home=112.0,
        area_plot=350.0,
        building_type=BuildingType.SZEREGOWIEC,
        segment_subtype=SegmentSubtype.SKRAJNY,
        location_raw="Rzeszów, Słocina",
        street="Słocińska",
    )
    filt_res = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED_WHITELIST,
        score=110.0,
        passed_stage1=True,
        passed_stage2=True,
    )

    model, is_new, _ = await repo.save_or_update(listing, filt_res)
    assert model.user_status == "NEW"
    assert model.user_notes is None

    # Test update status to FAVORITE
    updated = await repo.update_user_status(model.id, UserCRMStatus.FAVORITE.value)
    assert updated is not None
    assert updated.user_status == "FAVORITE"

    # Test update notes
    updated = await repo.update_user_notes(model.id, "Zadzwoń do agenta w poniedziałek o 10:00")
    assert updated is not None
    assert updated.user_notes == "Zadzwoń do agenta w poniedziałek o 10:00"

    # Test update status to TO_VISIT
    updated = await repo.update_user_status(model.id, UserCRMStatus.TO_VISIT.value)
    assert updated is not None
    assert updated.user_status == "TO_VISIT"


@pytest.mark.asyncio
async def test_geocoder_fallback(test_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import AsyncMock

    geocoder = NominatimGeocoder()
    # Mock network query to fail, guaranteeing test runs offline and validates centroid/district fallback
    monkeypatch.setattr(geocoder, "_rate_limited_query", AsyncMock(return_value=None))

    # Test centroid/geocoding matching for Słocina
    lat, lon, is_exact = await geocoder.geocode(
        session=test_session,
        district="Słocina",
        city="Rzeszów",
    )
    assert lat is not None and lon is not None
    assert is_exact is False
    assert abs(lat - 50.02) < 0.05
    assert abs(lon - 22.05) < 0.05

    # Test centroid/geocoding matching for Krasne
    lat, lon, is_exact = await geocoder.geocode(
        session=test_session,
        city="Krasne",
    )
    assert lat is not None and lon is not None
    assert is_exact is False
    assert abs(lat - 50.04) < 0.05
    assert abs(lon - 22.08) < 0.05


@pytest.mark.asyncio
async def test_geocoder_concurrent_cache_write():
    from src.storage.database import init_db

    await init_db()
    geocoder = NominatimGeocoder()
    geocoder._mem_cache.clear()

    async def write_cache(idx: int):
        query = f"street:budziwój {idx % 3}, budziwój, polska"
        await geocoder.set_cache(
            session=None,
            query_key=query,
            lat=49.9668 + idx * 0.001,
            lon=21.9797 + idx * 0.001,
            display_name=f"Budziwój {idx}",
        )

    await asyncio.gather(*[write_cache(i) for i in range(10)])

    res = await geocoder.get_cached(session=None, query_key="street:budziwój 0, budziwój, polska")
    assert res is not None
    assert abs(res[0] - 49.9668) < 0.05


@pytest.mark.asyncio
async def test_live_dashboard_crm_endpoints():
    from src.storage.database import init_db

    await init_db()
    server = LiveDashboardServer(port=8089)
    async with TestClient(TestServer(server.app)) as client:
        # Test GET /api/listings
        resp = await client.get("/api/listings")
        assert resp.status == 200
        listings = await resp.json()
        assert isinstance(listings, list)

        if len(listings) > 0:
            target_id = listings[0]["id"]
            # Test PATCH /api/listings/{id}/status
            patch_resp = await client.patch(f"/api/listings/{target_id}/status", json={"status": "FAVORITE"})
            assert patch_resp.status == 200
            patch_data = await patch_resp.json()
            assert patch_data["success"] is True
            assert patch_data["user_status"] == "FAVORITE"

            # Test PATCH /api/listings/{id}/notes
            notes_resp = await client.patch(
                f"/api/listings/{target_id}/notes", json={"notes": "Notatka testowa z poziomu API"}
            )
            assert notes_resp.status == 200
            notes_data = await notes_resp.json()
            assert notes_data["success"] is True
            assert notes_data["user_notes"] == "Notatka testowa z poziomu API"


@pytest.mark.asyncio
async def test_live_dashboard_config_endpoints():
    server = LiveDashboardServer(port=8088)
    async with TestClient(TestServer(server.app)) as client:
        # Test GET /api/config
        resp = await client.get("/api/config")
        assert resp.status == 200
        cfg = await resp.json()
        assert "city" in cfg
        assert "distance_radius" in cfg

        # Test POST /api/config
        update_resp = await client.post(
            "/api/config",
            json={
                "city": "Kraków",
                "distance_radius": 25,
                "max_price": 1_400_000.0,
            },
        )
        assert update_resp.status == 200
        new_cfg = await update_resp.json()
        assert new_cfg["city"] == "Kraków"
        assert new_cfg["distance_radius"] == 25
        assert new_cfg["max_price"] == 1_400_000.0

        # Restore to default Rzeszów
        await client.post(
            "/api/config",
            json={
                "city": "Rzeszów",
                "distance_radius": 15,
                "max_price": 1_300_000.0,
            },
        )


@pytest.mark.asyncio
async def test_live_dashboard_listings_includes_gallery():
    from src.storage.database import init_db

    await init_db()
    server = LiveDashboardServer(port=8087)
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.get("/api/listings")
        assert resp.status == 200
        listings = await resp.json()
        assert isinstance(listings, list)
        if len(listings) > 0:
            assert "gallery_images" in listings[0]
            assert isinstance(listings[0]["gallery_images"], list)


@pytest.mark.asyncio
async def test_live_dashboard_listings_lean_payload_and_cache():
    from src.storage.database import get_session, init_db

    await init_db()
    async with get_session() as session:
        repo = ListingRepository(session)
        listing = ListingSchema(
            id="lean-cache-test-1",
            portal="Otodom",
            title="Dom lean/cache test",
            url="https://otodom.pl/oferta/lean-cache-test-1",
            price=800_000,
            price_per_m2=7_500.0,
            area_home=107.0,
            area_plot=400.0,
            building_type=BuildingType.SZEREGOWIEC,
            segment_subtype=SegmentSubtype.SKRAJNY,
            location_raw="Rzeszów",
            street="Testowa",
            city="Rzeszów",
        )
        filt_res = FilterResult(
            is_qualified=True,
            status=QualificationStatus.QUALIFIED_WHITELIST,
            score=120.0,
            passed_stage1=True,
            passed_stage2=True,
        )
        model, _, _ = await repo.save_or_update(listing, filt_res)
        model.ai_summary = "TL;DR oferty."
        model.ai_verdict = "OK"
        model.worth_interest = True
        model.contact_phone = "600100200"
        model.contact_person = "Agent"
        model.walkability_pka_dist_m = 250
        model.air_pm25_summer_avg = 11.5
        model.gallery_images = ["https://example.com/a.jpg", "https://example.com/b.jpg"]
        model.gesut_networks_data = {"water": {"distance_m": 12}}
        await session.commit()

    server = LiveDashboardServer(port=8086)
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.get("/api/listings")
        assert resp.status == 200
        data = await resp.json()
        item = next((x for x in data if x.get("portal_id") == "lean-cache-test-1"), None)
        assert item is not None

        # List omits detail-only fields (Due Diligence re-fetches them via /api/listings/{id})
        for key in (
            "worth_interest",
            "ai_summary",
            "ai_verdict",
            "contact_phone",
            "contact_person",
            "walkability_pka_dist_m",
            "air_pm25_summer_avg",
            "land_audit",
            "negotiation_arguments",
        ):
            assert key not in item
        # Card-used fields remain
        assert "gallery_images" in item
        assert "gesut_networks" in item

        # Detail endpoint still returns everything
        detail = await (await client.get(f"/api/listings/{item['id']}")).json()
        assert detail["worth_interest"] is True
        assert detail["ai_summary"] == "TL;DR oferty."
        assert detail["contact_phone"] == "600100200"
        assert detail["walkability_pka_dist_m"] == 250

        # Unchanged data + If-None-Match -> served from cache (304)
        etag = resp.headers["ETag"]
        cached = await client.get("/api/listings", headers={"If-None-Match": etag})
        assert cached.status == 304

        # A status change bumps updated_at -> fingerprint changes -> fresh 200 with new data
        await client.patch(f"/api/listings/{item['id']}/status", json={"status": "FAVORITE"})
        refreshed = await client.get("/api/listings", headers={"If-None-Match": etag})
        assert refreshed.status == 200
        refreshed_item = next((x for x in await refreshed.json() if x.get("portal_id") == "lean-cache-test-1"), None)
        assert refreshed_item["user_status"] == "FAVORITE"


@pytest.mark.asyncio
async def test_image_proxy_validation_and_cache(monkeypatch: pytest.MonkeyPatch):
    from urllib.parse import quote

    from src.storage.database import init_db

    await init_db()
    server = LiveDashboardServer(port=8085)

    async def fake_fetch(self, url, cache_path, meta_path):
        cache_path.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
        meta_path.write_text("image/jpeg", encoding="utf-8")

    monkeypatch.setattr(LiveDashboardServer, "_fetch_image", fake_fetch)

    good_url = "https://ireland.apollo.olxcdn.com/v1/files/abc/image;s=480x360;q=70"
    encoded = quote(good_url, safe="")

    async with TestClient(TestServer(server.app)) as client:
        # Missing url -> 400
        assert (await client.get("/img")).status == 400

        # Disallowed host -> 403
        assert (await client.get("/img?url=" + quote("http://evil.example.com/x.jpg", safe=""))).status == 403

        # Allowed host -> proxied + cached, correct content type and immutable cache headers
        resp = await client.get("/img?url=" + encoded)
        assert resp.status == 200
        assert resp.content_type == "image/jpeg"
        assert (await resp.read()).startswith(b"\xff\xd8\xff\xe0")
        assert resp.headers["Cache-Control"].startswith("public, max-age=")

        # Repeated request with If-None-Match -> served from disk cache (304)
        etag = resp.headers["ETag"]
        cached = await client.get("/img?url=" + encoded, headers={"If-None-Match": etag})
        assert cached.status == 304


@pytest.mark.asyncio
async def test_delete_by_profile(test_session: AsyncSession):
    repo = ListingRepository(test_session)

    # Listing 1 in profile_A
    l1 = ListingSchema(
        id="prof-test-1",
        portal="Otodom",
        title="Dom A",
        url="https://otodom.pl/oferta/prof-test-1",
        price=800_000,
        price_per_m2=8_000.0,
        area_home=100.0,
        location_raw="Rzeszów",
        profile_id="profile_A",
        profile_name="Profil A",
    )
    # Listing 2 in profile_B
    l2 = ListingSchema(
        id="prof-test-2",
        portal="Otodom",
        title="Dom B",
        url="https://otodom.pl/oferta/prof-test-2",
        price=900_000,
        price_per_m2=9_000.0,
        area_home=100.0,
        location_raw="Kraków",
        profile_id="profile_B",
        profile_name="Profil B",
    )
    filt = FilterResult(is_qualified=True, status=QualificationStatus.QUALIFIED, passed_stage1=True, passed_stage2=True)
    await repo.save_or_update(l1, filt)
    await repo.save_or_update(l2, filt)

    # Verify both exist
    item1 = await repo.get_by_portal_id("Otodom", "prof-test-1")
    item2 = await repo.get_by_portal_id("Otodom", "prof-test-2")
    assert item1 is not None
    assert item2 is not None

    # Delete profile_A listings
    deleted_cnt = await repo.delete_by_profile(profile_id="profile_A", profile_name="Profil A")
    assert deleted_cnt == 1

    # Verify profile_A is gone, profile_B remains
    assert await repo.get_by_portal_id("Otodom", "prof-test-1") is None
    assert await repo.get_by_portal_id("Otodom", "prof-test-2") is not None


@pytest.mark.asyncio
async def test_live_dashboard_profile_filter_and_deletion(tmp_path):
    from src.services.config_manager import config_manager
    from src.storage.database import get_session, init_db

    await init_db()

    # Create dummy profile in config
    test_prof_id = "test_prof_del"
    config_manager.add_or_update_profile(
        {
            "id": test_prof_id,
            "name": "Profil Do Usunięcia",
            "category": "dom",
            "city": "Rzeszów",
        }
    )

    async with get_session() as session:
        repo = ListingRepository(session)
        listing = ListingSchema(
            id="del-test-100",
            portal="Otodom",
            title="Dom tymczasowy profil",
            url="https://otodom.pl/oferta/del-test-100",
            price=750_000,
            price_per_m2=7_500,
            area_home=100.0,
            location_raw="Rzeszów",
            profile_id=test_prof_id,
            profile_name="Profil Do Usunięcia",
        )
        filt = FilterResult(
            is_qualified=True, status=QualificationStatus.QUALIFIED, passed_stage1=True, passed_stage2=True
        )
        await repo.save_or_update(listing, filt)

    server = LiveDashboardServer(port=8086)
    async with TestClient(TestServer(server.app)) as client:
        # 1. Filter by profile in API
        resp = await client.get(f"/api/listings?profile={test_prof_id}")
        assert resp.status == 200
        data = await resp.json()
        assert any(item["id"] == listing.id or item["portal_id"] == "del-test-100" for item in data)

        # 2. Delete profile via DELETE endpoint
        del_resp = await client.delete(f"/api/profiles/{test_prof_id}")
        assert del_resp.status == 200
        del_data = await del_resp.json()
        assert del_data["success"] is True
        assert del_data["deleted_listings"] >= 1

        # 3. Verify listing is gone
        check_resp = await client.get(f"/api/listings?profile={test_prof_id}")
        check_data = await check_resp.json()
        assert len(check_data) == 0


@pytest.mark.asyncio
async def test_live_dashboard_reset_data_endpoint():
    from src.storage.database import get_session, init_db

    await init_db()

    test_prof_id = "test_prof_reset"
    async with get_session() as session:
        repo = ListingRepository(session)
        listing = ListingSchema(
            id="reset-api-1",
            portal="Otodom",
            title="Dom do resetu API",
            url="https://otodom.pl/oferta/reset-api-1",
            price=650_000,
            price_per_m2=6_500,
            area_home=100.0,
            location_raw="Rzeszów",
            profile_id=test_prof_id,
            profile_name=test_prof_id,
        )
        filt = FilterResult(
            is_qualified=True, status=QualificationStatus.QUALIFIED, passed_stage1=True, passed_stage2=True
        )
        await repo.save_or_update(listing, filt)

    server = LiveDashboardServer(port=8085)
    async with TestClient(TestServer(server.app)) as client:
        # Without confirmation -> 400
        guard_resp = await client.post("/api/data/reset", json={"confirm": False})
        assert guard_resp.status == 400

        # Profile-scoped reset -> success, only test listings removed
        reset_resp = await client.post("/api/data/reset", json={"confirm": True, "profile": test_prof_id})
        assert reset_resp.status == 200
        reset_data = await reset_resp.json()
        assert reset_data["success"] is True
        assert reset_data["deleted_listings"] >= 1

        async with get_session() as session:
            repo = ListingRepository(session)
            assert await repo.get_by_url("https://otodom.pl/oferta/reset-api-1") is None


@pytest.mark.asyncio
async def test_live_dashboard_serves_split_assets():
    server = LiveDashboardServer(port=8084)
    async with TestClient(TestServer(server.app)) as client:
        # Index is markup-only and links to the split assets (versioned + deferred)
        index_resp = await client.get("/")
        assert index_resp.status == 200
        index_html = await index_resp.text()
        assert 'href="/assets/dashboard.css?v=' in index_html
        assert 'src="/assets/js/transport.js?v=' in index_html
        assert 'src="/assets/js/dashboard.js?v=' in index_html
        assert "defer" in index_html
        assert "<style>" not in index_html

        # CSS asset with correct content type and ETag
        css_resp = await client.get("/assets/dashboard.css")
        assert css_resp.status == 200
        assert css_resp.content_type == "text/css"
        assert css_resp.headers.get("ETag")
        css_body = await css_resp.text()
        assert ":root" in css_body
        etag = css_resp.headers["ETag"]

        # Conditional request returns 304
        cached_resp = await client.get("/assets/dashboard.css", headers={"If-None-Match": etag})
        assert cached_resp.status == 304

        # JS assets served with application/javascript
        for js_path in ("/assets/js/transport.js", "/assets/js/dashboard.js"):
            js_resp = await client.get(js_path)
            assert js_resp.status == 200
            assert js_resp.content_type == "application/javascript"
            js_body = await js_resp.text()
            assert "Transport" in js_body or "allListings" in js_body
            if "dashboard.js" in js_path:
                assert "getVisibleParent" in js_body
                assert ".map-cluster" in js_body
        assert ".map-cluster.pin-highlighted" in css_body

        # Missing and path-traversal requests are rejected
        missing_resp = await client.get("/assets/nope.css")
        assert missing_resp.status == 404
        traversal_resp = await client.get("/assets/%2e%2e/pyproject.toml")
        assert traversal_resp.status == 404
