import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.models.enums import QualificationStatus
from src.models.listing import FilterResult, ListingSchema
from src.services.live_dashboard import LiveDashboardServer
from src.services.pipeline import ScraperPipeline
from src.services.progress import global_tracker
from src.storage import ListingRepository, get_session, init_db


@pytest.mark.asyncio
async def test_global_tracker_cancellation_flow(monkeypatch, tmp_path):
    status_file = str(tmp_path / "status.json")
    cancel_file = str(tmp_path / ".cancel")
    monkeypatch.setattr("src.services.progress.get_shared_status_file", lambda: status_file)
    monkeypatch.setattr("src.services.progress.get_shared_cancel_file", lambda: cancel_file)

    global_tracker.reset()
    try:
        global_tracker.start_session(total_portals=2)
        assert global_tracker.is_running is True
        assert global_tracker.is_cancelled() is False

        global_tracker.request_cancel()
        assert global_tracker.is_cancelled() is True
        assert global_tracker.cancel_requested is True

        payload = global_tracker.get_status_payload()
        assert payload["is_running"] is True
        assert payload["cancel_requested"] is True
        assert any("🛑" in l["message"] for l in payload["logs"])

        global_tracker.cancel_session()
        assert global_tracker.is_running is False
        assert global_tracker.is_cancelled() is False

        final_payload = global_tracker.get_status_payload()
        assert final_payload["is_running"] is False
        assert "Zatrzymano" in final_payload["current_step"]
    finally:
        global_tracker.reset()


@pytest.mark.asyncio
async def test_pipeline_run_cycle_cooperative_cancellation():
    mock_scraper = AsyncMock()
    mock_scraper.name = "MockPortal"
    mock_scraper.max_pages = 1

    async def mock_scrape():
        global_tracker.request_cancel()
        return [
            ListingSchema(
                id="canc-1",
                portal="MockPortal",
                title="Test Cancellation",
                url="https://example.com/canc-1",
                price=500_000,
                price_per_m2=5_000,
                area_home=100.0,
                location_raw="Rzeszów",
            )
        ]

    mock_scraper.scrape = mock_scrape
    mock_scraper.close = AsyncMock()

    pipeline = ScraperPipeline(scrapers=[mock_scraper])

    with patch.object(pipeline, "process_listing", new_callable=AsyncMock) as mock_proc:
        summary = await pipeline.run_cycle()
        assert summary.get("cancelled") is True
        assert global_tracker.is_running is False
        mock_proc.assert_not_called()


@pytest.mark.asyncio
async def test_live_dashboard_scrape_endpoints_and_cancellation(monkeypatch, tmp_path):
    status_file = str(tmp_path / "status.json")
    cancel_file = str(tmp_path / ".cancel")
    monkeypatch.setattr("src.services.progress.get_shared_status_file", lambda: status_file)
    monkeypatch.setattr("src.services.progress.get_shared_cancel_file", lambda: cancel_file)
    global_tracker.reset()

    await init_db()
    server = LiveDashboardServer(port=8089)

    async with TestClient(TestServer(server.app)) as client:
        # 1. Test scrape status when idle
        st_resp = await client.get("/api/scrape/status")
        assert st_resp.status == 200
        st_data = await st_resp.json()
        assert "is_running" in st_data
        assert "cancel_requested" in st_data
        assert "logs" in st_data

        # 2. Mock pipeline.run_cycle to simulate running background work
        async def slow_cycle(*args, **kwargs):
            global_tracker.start_session(total_portals=2)
            while not global_tracker.is_cancelled():
                await asyncio.sleep(0.05)
            global_tracker.cancel_session()
            return {"cancelled": True}

        with patch.object(ScraperPipeline, "run_cycle", side_effect=slow_cycle):
            # Trigger scrape -> should return 200 started immediately (non-blocking!)
            trig_resp = await client.post("/api/scrape")
            assert trig_resp.status == 200
            trig_data = await trig_resp.json()
            assert trig_data["status"] == "started"

            await asyncio.sleep(0.06)

            # Check status while running
            running_st = await client.get("/api/scrape/status")
            running_data = await running_st.json()
            assert running_data["is_running"] is True

            # Triggering again while running -> 409 already_running
            conflict_resp = await client.post("/api/scrape")
            assert conflict_resp.status == 409
            conflict_data = await conflict_resp.json()
            assert conflict_data["status"] == "already_running"

            # Cancel scrape
            cancel_resp = await client.post("/api/scrape/cancel")
            assert cancel_resp.status == 200
            cancel_data = await cancel_resp.json()
            assert cancel_data["status"] == "cancelling"

            # Wait for background task to complete cooperative cancellation
            await asyncio.sleep(0.12)
            after_cancel_st = await client.get("/api/scrape/status")
            after_data = await after_cancel_st.json()
            assert after_data["is_running"] is False
    global_tracker.reset()


@pytest.mark.asyncio
async def test_live_dashboard_listings_includes_gunb_and_gesut():
    await init_db()
    async with get_session() as session:
        repo = ListingRepository(session)
        listing = ListingSchema(
            id="geo-gunb-test-1",
            portal="Otodom",
            title="Działka budowlana GUNB test",
            url="https://otodom.pl/oferta/geo-gunb-test-1",
            price=350_000,
            price_per_m2=350.0,
            area_home=100.0,
            area_plot=1000.0,
            latitude=50.0375,
            longitude=22.0047,
            location_raw="Rzeszów",
        )
        filt = FilterResult(
            is_qualified=True, status=QualificationStatus.QUALIFIED, passed_stage1=True, passed_stage2=True
        )
        model, _, _ = await repo.save_or_update(listing, filt)
        now_dt = datetime.now(UTC)
        model.created_at = now_dt
        model.last_scraped_at = now_dt
        model.parcel_id = "181609_2.0001.2643/7"
        model.cadastral_area = 1000.0
        model.geoportal_url = "https://mapy.geoportal.gov.pl/imap/Imgp_2.html?identifyParcel=181609_2.0001.2643/7"
        await session.commit()

    server = LiveDashboardServer(port=8091)
    async with TestClient(TestServer(server.app)) as client:
        resp = await client.get("/api/listings")
        assert resp.status == 200
        data = await resp.json()
        item = next((x for x in data if x.get("portal_id") == "geo-gunb-test-1"), None)
        assert item is not None
        assert item["is_new_cycle"] is True
        assert item["is_updated_cycle"] is False
        assert "identifyParcel=181609_2.0001.2643/7" in item["geoportal_url"]
        assert item["parcel_id"] == "181609_2.0001.2643/7"
        assert "land_audit" not in item
        assert item["capex_total"] >= 350_000
        detail = await (await client.get(f"/api/listings/{item['id']}")).json()
        assert "land_audit" in detail
        assert detail["land_audit"]["search_packet"]["voivodeship"] == "Podkarpackie"
        assert detail["land_audit"]["search_packet"]["parcel_short"] == "2643/7"
        assert "tco_audit" in detail["land_audit"]
        assert "commute_audit" in detail["land_audit"]
        assert "risk_shield" in detail["land_audit"]
        assert "gesut_audit" in detail["land_audit"]
        assert detail["land_audit"]["tco_audit"]["total_acquisition_cost"] >= 350_000


@pytest.mark.asyncio
async def test_live_dashboard_generate_ai_audit_endpoint():
    await init_db()
    async with get_session() as session:
        repo = ListingRepository(session)
        listing = ListingSchema(
            id="ai-audit-test-1",
            portal="Otodom",
            title="Dom do audytu AI",
            url="https://otodom.pl/oferta/ai-audit-test-1",
            price=600_000,
            price_per_m2=6_000.0,
            area_home=100.0,
            location_raw="Rzeszów",
            raw_description="Dom wykończony pod klucz z umeblowaną kuchnią. Do zrobienia taras. Tel: 500600700.",
        )
        filt = FilterResult(
            is_qualified=True, status=QualificationStatus.QUALIFIED, passed_stage1=True, passed_stage2=True
        )
        model, _, _ = await repo.save_or_update(listing, filt)
        model.ai_summary = None
        model.ai_verdict = None
        await session.commit()
        listing_id = model.id

    mock_insights = {
        "summary": "Wykończony dom 100m² w Rzeszowie za 600 tys. zł.",
        "verdict": "Tak — 6 000 zł/m² to atrakcyjna oferta.",
        "worth_interest": True,
        "questions_for_agent": ["Czy taras wymaga pozwolenia?", "Kiedy odebrano budynek?"],
        "contact_phone": "500600700",
        "contact_person": "Jan Kowalski",
        "finish_condition": "pod_klucz",
        "has_visualisations": False,
        "pros": ["Wykończona kuchnia"],
        "cons": ["Do zrobienia taras"],
    }

    server = LiveDashboardServer(port=8092)
    with patch("src.filters.llm_analyzer.LLMAnalyzer.analyze_description", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = mock_insights
        async with TestClient(TestServer(server.app)) as client:
            resp = await client.post(f"/api/listings/{listing_id}/ai-audit")
            assert resp.status == 200
            data = await resp.json()
            assert data["id"] == listing_id
            assert data["ai_summary"] == mock_insights["summary"]
            assert data["ai_verdict"] == mock_insights["verdict"]
            assert data["worth_interest"] is True
            assert len(data["ai_questions"]) == 2
            assert data["contact_phone"] == "500600700"
            assert data["finish_condition"] == "do zamieszkania"

            # Check 404 for nonexistent id
            resp_404 = await client.post("/api/listings/999999/ai-audit")
            assert resp_404.status == 404

            # Check 502 when AI fails to respond
            mock_ai.return_value = None
            resp_502 = await client.post(f"/api/listings/{listing_id}/ai-audit")
            assert resp_502.status == 502


@pytest.mark.asyncio
async def test_live_dashboard_llm_status_and_test_endpoints():
    """Verify /api/llm/status and /api/llm/test diagnostic endpoints and scenarios."""
    server = LiveDashboardServer(port=8093)
    mock_diag = {
        "enabled": True,
        "has_working_provider": True,
        "active_provider": {
            "id": "ollama",
            "name": "Ollama (lokalny)",
            "model": "llama3.1:8b",
            "label": "Ollama (llama3.1:8b)",
        },
        "providers": {
            "openrouter": {"status": "not_configured", "configured": False},
            "openai": {"status": "not_configured", "configured": False},
            "ollama": {
                "status": "ok",
                "configured": True,
                "model": "llama3.1:8b",
                "installed_models": ["llama3.1:8b"],
                "latency_ms": 120,
            },
        },
    }

    with patch("src.filters.llm_analyzer.LLMAnalyzer.test_connection", new_callable=AsyncMock) as mock_test:
        mock_test.return_value = mock_diag
        async with TestClient(TestServer(server.app)) as client:
            resp = await client.get("/api/llm/status")
            assert resp.status == 200
            data = await resp.json()
            assert data["enabled"] is True
            assert data["has_working_provider"] is True
            assert data["active_provider"]["id"] == "ollama"
            assert data["providers"]["ollama"]["status"] == "ok"

            # Test POST /api/llm/test with custom model
            resp_post = await client.post("/api/llm/test", json={"ollama_model": "qwen2.5:3b"})
            assert resp_post.status == 200
            data_post = await resp_post.json()
            assert data_post["has_working_provider"] is True


@pytest.mark.asyncio
async def test_llm_analyzer_test_connection_ollama_states():
    """Verify test_ollama returns appropriate status for missing model and unreachable server."""
    from src.filters.llm_analyzer import LLMAnalyzer

    analyzer = LLMAnalyzer(enabled=True, ollama_model="nonexistent:model")

    # 1. Unreachable server
    with patch("httpx.AsyncClient.get", side_effect=Exception("Connection refused")):
        res = await analyzer.test_ollama()
        assert res["status"] == "unreachable"

    # 2. Server ok, but model missing
    mock_tags_resp = MagicMock(status_code=200)
    mock_tags_resp.json.return_value = {"models": [{"name": "llama3.1:8b"}]}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_tags_resp
        res = await analyzer.test_ollama()
        assert res["status"] == "model_missing"
        assert "llama3.1:8b" in res["installed_models"]


@pytest.mark.asyncio
async def test_global_tracker_logging_categories_and_retention(monkeypatch, tmp_path):
    """Verify log categorization, retention, and payload formatting."""
    status_file = str(tmp_path / "status.json")
    cancel_file = str(tmp_path / ".cancel")
    monkeypatch.setattr("src.services.progress.get_shared_status_file", lambda: status_file)
    monkeypatch.setattr("src.services.progress.get_shared_cancel_file", lambda: cancel_file)

    from src.services.progress import ProgressTracker

    tracker = ProgressTracker()
    tracker.start_session(total_portals=1)

    tracker.add_log("📍 [Geokoder] Słocina: (50.04, 22.01) [precyzyjny punkt]", level="info", category="geo")
    tracker.add_log("🏛️ [Rejestry] Dom: działka 123/4 | FTTH: TAK", level="info", category="geo")
    tracker.add_log("❌ [Odrzucono] Działka: cena za wysoka", level="warning", category="rejected")
    tracker.add_log("🤖 [AI Audit] Gotowe dla Dom w 2.1s", level="info", category="ai")
    tracker.add_log("⭐ [Zakwalifikowano] Super dom (750 000 zł) — Wynik: 95 pkt", level="success", category="success")

    payload = tracker.get_status_payload()
    logs = payload["logs"]
    assert len(logs) >= 5

    categories = [l.get("category") for l in logs]
    assert "geo" in categories
    assert "rejected" in categories
    assert "ai" in categories
    assert "success" in categories


@pytest.mark.asyncio
async def test_sqlite_get_session_retry_on_locked(monkeypatch):
    """Verify get_session retries on SQLite database is locked error before succeeding."""
    from src.storage.database import get_session

    attempts = 0

    async def mock_commit():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            import sqlite3

            raise sqlite3.OperationalError("database is locked")

    async with get_session() as session:
        monkeypatch.setattr(session, "commit", mock_commit)

    # Commits twice failing with 'database is locked', then succeeds on 3rd attempt
    assert attempts == 3


@pytest.mark.asyncio
async def test_sqlite_safe_commit_retry_on_readonly_database(monkeypatch):
    """Verify safe_commit retries on SQLite 'attempt to write a readonly database' error."""
    from src.storage.database import get_session

    attempts = 0

    async def mock_commit():
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            import sqlite3

            raise sqlite3.OperationalError("attempt to write a readonly database")

    async with get_session() as session:
        monkeypatch.setattr(session, "commit", mock_commit)

    assert attempts == 2


def test_is_sqlite_lock_error_detection():
    """Verify is_sqlite_lock_error identifies all SQLite contention variants."""
    import sqlite3

    from src.storage.database import is_sqlite_lock_error

    err_locked = sqlite3.OperationalError("database is locked")
    err_readonly = sqlite3.OperationalError("attempt to write a readonly database")
    err_busy = sqlite3.OperationalError("database is busy")
    err_other = sqlite3.OperationalError("no such table: listings")

    assert is_sqlite_lock_error(err_locked) is True
    assert is_sqlite_lock_error(err_readonly) is True
    assert is_sqlite_lock_error(err_busy) is True
    assert is_sqlite_lock_error(err_other) is False


@pytest.mark.asyncio
async def test_scrape_lock_and_pipeline_skips_when_locked(tmp_path, monkeypatch):
    """Verify ScrapeLock prevents concurrent execution and pipeline.run_cycle skips cleanly."""
    from src.services.pipeline import ScraperPipeline
    from src.services.scrape_lock import ScrapeLock

    lock_file = str(tmp_path / "test_scrape.lock")
    lock1 = ScrapeLock(lock_file)
    lock2 = ScrapeLock(lock_file)

    assert lock1.acquire({"test": 1}) is True
    assert lock1.is_locked() is True
    # Second acquisition fails
    assert lock2.acquire({"test": 2}) is False

    # Mock get_scrape_lock in pipeline to return lock2
    monkeypatch.setattr("src.services.scrape_lock.get_scrape_lock", lambda: lock2)

    pipeline = ScraperPipeline()
    summary = await pipeline.run_cycle()
    assert summary.get("skipped_reason") == "already_running"

    lock1.release()
    assert lock1.is_locked() is False
    assert lock2.acquire({"test": 2}) is True
    lock2.release()


def test_shared_status_and_cancellation_lifecycle(tmp_path, monkeypatch):
    """Verify progress tracker writes shared status file and shared cancellation flags."""
    from src.services.progress import (
        check_shared_cancellation,
        clear_shared_cancellation,
        read_shared_status,
        signal_shared_cancellation,
        write_shared_status,
    )

    status_file = str(tmp_path / "status.json")
    cancel_file = str(tmp_path / ".cancel")

    monkeypatch.setattr("src.services.progress.get_shared_status_file", lambda: status_file)
    monkeypatch.setattr("src.services.progress.get_shared_cancel_file", lambda: cancel_file)

    clear_shared_cancellation()
    assert check_shared_cancellation() is False

    signal_shared_cancellation()
    assert check_shared_cancellation() is True

    clear_shared_cancellation()
    assert check_shared_cancellation() is False

    write_shared_status({"is_running": True, "percentage": 42})
    payload = read_shared_status()
    assert payload is not None
    assert payload["percentage"] == 42
    assert payload["is_running"] is True


def test_verify_and_repair_sqlite_permissions(tmp_path):
    """Verify verify_and_repair_sqlite_permissions creates dir and confirms write ability."""
    from src.storage.database import verify_and_repair_sqlite_permissions

    db_file = tmp_path / "sub" / "test.db"
    verify_and_repair_sqlite_permissions(str(db_file))
    assert (tmp_path / "sub").exists()
    assert (tmp_path / "sub").is_dir()


@pytest.mark.asyncio
async def test_llm_provider_selection_logic():
    """Verify that test_connection selects the active provider according to llm_provider preference."""
    from src.filters.llm_analyzer import LLMAnalyzer

    analyzer = LLMAnalyzer(enabled=True, llm_provider="ollama", ollama_model="llama3.1:8b")

    mock_openrouter = {"status": "ok", "configured": True, "model": "gemini"}
    mock_openai = {"status": "ok", "configured": True, "model": "gpt-4o-mini"}
    mock_ollama = {"status": "ok", "configured": True, "model": "llama3.1:8b"}

    with (
        patch.object(analyzer, "test_openrouter", new_callable=AsyncMock) as m_or,
        patch.object(analyzer, "test_openai", new_callable=AsyncMock) as m_oa,
        patch.object(analyzer, "test_ollama", new_callable=AsyncMock) as m_ol,
    ):
        m_or.return_value = mock_openrouter
        m_oa.return_value = mock_openai
        m_ol.return_value = mock_ollama

        # 1. Preferred is ollama -> active_provider must be ollama even if openrouter is ok
        res = await analyzer.test_connection()
        assert res["active_provider"]["id"] == "ollama"
        assert res["configured_provider"] == "ollama"

        # 2. Preferred is openrouter
        analyzer.llm_provider = "openrouter"
        res2 = await analyzer.test_connection()
        assert res2["active_provider"]["id"] == "openrouter"

        # 3. Preferred is auto -> default order: openrouter -> openai -> ollama
        analyzer.llm_provider = "auto"
        res3 = await analyzer.test_connection()
        assert res3["active_provider"]["id"] == "openrouter"

        # 4. Auto with openrouter down -> falls back to openai
        m_or.return_value = {"status": "error"}
        res4 = await analyzer.test_connection()
        assert res4["active_provider"]["id"] == "openai"


@pytest.mark.asyncio
async def test_llm_provider_analyze_routing():
    """Verify that analyze_description routes only to the chosen provider when not 'auto'."""
    from src.filters.llm_analyzer import LLMAnalyzer

    analyzer = LLMAnalyzer(enabled=True, llm_provider="ollama", ollama_model="llama3.1:8b")
    analyzer.openrouter_key = "test-key"

    listing = ListingSchema(
        id="test-routing-1",
        portal="Otodom",
        title="Dom w Rzeszowie",
        url="https://otodom.pl/1",
        price=500_000,
        price_per_m2=5_000.0,
        area_home=100.0,
        location_raw="Rzeszów",
        raw_description="Dom pod klucz.",
    )

    with (
        patch.object(analyzer, "_call_openrouter", new_callable=AsyncMock) as m_or,
        patch.object(analyzer, "_call_ollama", new_callable=AsyncMock) as m_ol,
    ):
        m_ol.return_value = {"summary": "Zbadano przez Ollama"}
        res = await analyzer.analyze_description(listing)

        assert res == {"summary": "Zbadano przez Ollama"}
        assert m_ol.called
        assert not m_or.called  # OpenRouter must not be called when provider is strictly ollama


def test_config_manager_llm_settings_roundtrip():
    """Verify SearchConfig accepts and persists llm_provider, ollama_model, and openrouter_model."""
    from src.services.config_manager import SearchConfig, config_manager

    cfg = SearchConfig(
        llm_provider="ollama",
        ollama_model="qwen2.5:7b",
        openrouter_model="deepseek/deepseek-chat",
    )
    assert cfg.llm_provider == "ollama"
    assert cfg.ollama_model == "qwen2.5:7b"
    assert cfg.openrouter_model == "deepseek/deepseek-chat"

    # Test update_config
    updated = config_manager.update_config(
        {
            "llm_provider": "openrouter",
            "ollama_model": "llama3.1:8b",
            "openrouter_model": "google/gemini-2.5-flash-lite:nitro",
        }
    )
    assert updated.llm_provider == "openrouter"
    assert updated.ollama_model == "llama3.1:8b"
    assert updated.openrouter_model == "google/gemini-2.5-flash-lite:nitro"


@pytest.mark.asyncio
async def test_live_dashboard_listings_endpoint_contains_full_data():
    """Verify GET /api/listings returns full data including on-demand audit, arguments and description."""
    await init_db()
    server = LiveDashboardServer(port=8091)

    listing = ListingSchema(
        id="test-details-ux-1",
        portal="Otodom",
        title="Dom z działką i geometrią",
        url="https://otodom.pl/test-details-ux-1",
        price=750_000,
        price_per_m2=6_000.0,
        area_home=125.0,
        area_plot=800.0,
        location_raw="Rzeszów, Słocina",
        parcel_id="186301_1.0001.123/4",
        raw_description="Pełny opis forensic z analizą stanu prawnego i wykończenia.",
    )

    async with get_session() as session:
        repo = ListingRepository(session)
        filt = FilterResult(
            is_qualified=True, status=QualificationStatus.QUALIFIED, passed_stage1=True, passed_stage2=True
        )
        await repo.save_or_update(listing, filt)

    async with TestClient(TestServer(server.app)) as client:
        resp = await client.get("/api/listings")
        assert resp.status == 200
        listings_data = await resp.json()
        target = next((item for item in listings_data if item.get("portal_id") == "test-details-ux-1"), None)
        assert target is not None
        assert target["portal_id"] == "test-details-ux-1"
        assert "capex_total" in target
        assert "fair_market_value" in target
        detail = await (await client.get(f"/api/listings/{target['id']}")).json()
        assert "land_audit" in detail
        assert "negotiation_arguments" in detail
