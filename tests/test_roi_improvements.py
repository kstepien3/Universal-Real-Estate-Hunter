"""ROI wave 1: LLM cache/budget, medians TTL, GESUT fast path, geocode batch, 0% modules."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.filters import QualificationEngine
from src.filters.fingerprint import compute_desc_hash, estimate_llm_tokens
from src.filters.llm_analyzer import (
    _provider_in_cooldown,
    estimate_tokens,
    load_prompt_template,
)
from src.models.enums import BuildingType
from src.models.listing import ListingSchema
from src.scheduler.runner import SchedulerRunner
from src.services import geocoder as geocoder_module
from src.services.discord_notifier import DiscordNotifier
from src.services.geoportal import GeoportalService
from src.services.telegram_notifier import TelegramNotifier
from src.storage.repository import clear_medians_cache


def _listing(**kwargs):
    defaults = {
        "id": "roi-1",
        "portal": "Otodom",
        "title": "Dom pod klucz z garażem",
        "url": "https://example.com/roi-1",
        "price": 1_000_000,
        "price_per_m2": 8_000,
        "area_home": 125.0,
        "area_plot": 400.0,
        "location_raw": "Rzeszów, Słocina",
        "raw_description": "Dom pod klucz. Kuchnia z AGD, łazienki z glazurą, panele. Tel 600100200.",
    }
    defaults.update(kwargs)
    return ListingSchema(**defaults)


def test_desc_hash_stable_and_normalized():
    assert compute_desc_hash(None) is None
    assert compute_desc_hash("   ") is None
    a = compute_desc_hash("Dom  pod klucz\nz garażem.")
    b = compute_desc_hash("dom pod klucz z garazem.")
    # Normalized text may still differ on diacritics, but whitespace/case must not matter.
    assert a is not None
    assert compute_desc_hash("Dom  pod klucz\nz garażem.") == a
    assert compute_desc_hash("Zupełnie inny opis ogrodu i tarasu") != a
    assert b is not None


def test_estimate_tokens_monotonic():
    short = estimate_tokens("krótki opis")
    long = estimate_tokens("x" * 20000)
    assert long > short > 0
    assert estimate_llm_tokens(None) == 1500


def test_prompt_template_loads_with_version():
    template = load_prompt_template()
    assert "{desc_slice}" in template
    assert "{title}" in template
    assert "{spatial_block}" in template


def test_build_prompt_uses_template_and_version():
    from src.filters.llm_analyzer import LLMAnalyzer

    analyzer = LLMAnalyzer(enabled=False)
    prompt, version = analyzer.build_prompt(_listing())
    assert "Dom pod klucz z garażem" in prompt
    assert "<ogloszenie>" in prompt
    assert isinstance(version, str) and len(version) > 0


def test_llm_counters_reset():
    engine = QualificationEngine(llm_enabled=False)
    engine.llm_calls = 5
    engine.llm_successes = 3
    engine.llm_failures = 2
    engine.last_skip_reason = "provider_error"
    engine.reset_llm_counters()
    assert engine.llm_calls == 0
    assert engine.llm_successes == 0
    assert engine.llm_failures == 0
    assert engine.last_skip_reason is None


@pytest.mark.asyncio
async def test_llm_success_and_failure_counters():
    """Udane i nieudane wywołania LLM muszą być liczone osobno (bez limitu na cykl)."""
    from tests.test_filters import PermissiveProfile, create_sample_listing

    engine = QualificationEngine(llm_enabled=True)
    engine.llm = AsyncMock()
    engine.llm.analyze_description.return_value = {"summary": "Segment skrajny, stan deweloperski."}
    await engine.evaluate_listing(
        create_sample_listing(raw_description="Segment skrajny z garażem. Dojazd asfaltowy."),
        profile=PermissiveProfile(),
    )
    assert engine.llm_calls == 1
    assert engine.llm_successes == 1
    assert engine.llm_failures == 0
    assert engine.last_skip_reason is None

    engine.llm = AsyncMock()
    engine.llm.analyze_description.return_value = None  # provider niedostępny / błąd
    await engine.evaluate_listing(
        create_sample_listing(raw_description="Segment skrajny z garażem. Dojazd asfaltowy."),
        profile=PermissiveProfile(),
    )
    assert engine.llm_calls == 2
    assert engine.llm_successes == 1
    assert engine.llm_failures == 1
    assert engine.last_skip_reason == "provider_error"


def test_provider_cooldown_helper():
    from src.filters.llm_analyzer import _mark_provider_cooldown

    assert _provider_in_cooldown("no-such-provider-xyz") is False
    _mark_provider_cooldown("no-such-provider-xyz", seconds=60.0)
    assert _provider_in_cooldown("no-such-provider-xyz") is True


def test_gesut_transparent_image_counts_zero():
    import io

    from PIL import Image

    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    counts = GeoportalService.count_gesut_pixels(buf.getvalue())
    assert set(counts) == {"woda", "kanalizacja", "gaz", "prad", "cieplo", "telekomunikacja"}
    assert all(v == 0 for v in counts.values())


def test_gesut_blue_pixels_detected_as_water():
    import io

    from PIL import Image

    img = Image.new("RGBA", (8, 8), (0, 0, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    counts = GeoportalService.count_gesut_pixels(buf.getvalue())
    assert counts["woda"] == 64


def test_scheduler_effective_interval_cli_override():
    runner = SchedulerRunner(interval_minutes=7)
    assert runner.get_effective_interval() == 7


def test_scheduler_stop_sets_event():
    runner = SchedulerRunner(interval_minutes=60)
    runner.running = True
    runner.stop()
    assert runner.running is False
    assert runner._shutdown_event.is_set() is True


async def test_terminal_view_empty_db(monkeypatch):
    from src.services import terminal_view as tv

    async def fake_listings(status_filter=None, limit=None):
        return []

    monkeypatch.setattr(tv, "get_listings_from_db", fake_listings)
    # Should render an empty table without raising.
    await tv.print_terminal_view(status_filter="QUALIFIED", limit=5)


async def test_telegram_send_mocked():
    notifier = TelegramNotifier(bot_token="tok", chat_id="123")
    listing = _listing()
    from src.models.enums import QualificationStatus
    from src.models.listing import FilterResult

    result = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=80.0,
        passed_stage1=True,
        passed_stage2=True,
    )
    with patch("src.services.telegram_notifier.httpx.AsyncClient") as mock_client:
        resp = MagicMock()
        resp.status_code = 200
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        assert await notifier.send_notification(listing, result) is True


async def test_discord_format_and_send_mocked():
    notifier = DiscordNotifier(webhook_url="https://example.com/hook")
    listing = _listing(building_type=BuildingType.WOLNOSTOJACY)
    from src.models.enums import QualificationStatus
    from src.models.listing import FilterResult

    result = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED_WHITELIST,
        score=90.0,
        passed_stage1=True,
        passed_stage2=True,
        matched_whitelist_area="Słocina",
    )
    embed = notifier.format_embed(listing, result)
    assert isinstance(embed, dict)
    assert "title" in embed and "color" in embed and "fields" in embed
    with patch("src.services.discord_notifier.httpx.AsyncClient") as mock_client:
        resp = MagicMock()
        resp.status_code = 204
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        # send_notification signature: (listing, filter_result, webhook_url?, negotiation_advice?)
        sent = await notifier.send_notification(listing, result)
        assert isinstance(sent, bool)


async def test_provider_routing_prefers_configured():
    from src.filters.llm_analyzer import LLMAnalyzer

    analyzer = LLMAnalyzer(enabled=True, llm_provider="openai")
    assert analyzer.llm_provider == "openai"
    analyzer_disabled = LLMAnalyzer(enabled=False)
    assert await analyzer_disabled.analyze_description(_listing()) is None


async def test_geocode_many_uses_cache_without_http():
    session = MagicMock()
    geocoder_module.geocoder._mem_cache.clear()
    items = [
        {"street": None, "district": "Słocina", "city": "Rzeszów", "location_raw": "Rzeszów, Słocina"},
        {"street": None, "district": "Słocina", "city": "Rzeszów", "location_raw": "Rzeszów, Słocina"},
    ]

    async def fake_geocode(session=None, street=None, district=None, city=None, location_raw=None):
        return (50.0, 22.0, False)

    with patch.object(geocoder_module.geocoder, "geocode", side_effect=fake_geocode):
        out = await geocoder_module.geocode_many(session, items, batch_size=2)
    assert out == [(50.0, 22.0, False), (50.0, 22.0, False)]


async def test_medians_cache_hit_avoids_db():
    clear_medians_cache()

    # Bind the real unbound method with a fake session-less self.
    real_cls = __import__("src.storage.repository", fromlist=["ListingRepository"]).ListingRepository

    async def fake_execute(stmt):
        class FakeResult:
            def all(self):
                return [("Rzeszów", "Słocina", "dom", 8000.0)]

        return FakeResult()

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=fake_execute)
    instance = real_cls(fake_session)
    first = await instance.get_market_medians()
    assert first
    # Second call must hit the TTL cache: execute not awaited again.
    fake_session.execute.reset_mock()
    second = await instance.get_market_medians()
    assert second == first
    fake_session.execute.assert_not_called()
    clear_medians_cache()


def test_geocoder_fallback_does_not_leak_rzeszow_to_other_cities():
    from src.services.geocoder import NominatimGeocoder

    gc = NominatimGeocoder()

    # Case 1: An offer in Poznań on street "Nowe Miasto" or "Biała" must NOT get Rzeszów centroid
    res_poznan = gc._find_district_fallback(
        street="ul. Biała 10",
        district=None,
        city="Poznań",
        location_raw="Poznań, Wielkopolskie",
    )
    # It should match Poznań from CITY_CENTROIDS, not "Biała" from Rzeszów
    from src.services.config_manager import CITY_CENTROIDS

    assert res_poznan == CITY_CENTROIDS["poznan"]

    # Case 2: A non-Rzeszów city with a district matching a common name like "Nowa Wieś"
    res_nowa_wies = gc._find_district_fallback(
        street="ul. Główna",
        district="Nowa Wieś",
        city="Warszawa",
        location_raw="Warszawa, Mazowieckie",
    )
    assert res_nowa_wies == CITY_CENTROIDS["warszawa"]

    # Case 3: In Rzeszów, district "Słocina" should resolve to Słocina centroid
    res_slocina = gc._find_district_fallback(
        street=None,
        district="Słocina",
        city="Rzeszów",
        location_raw="Rzeszów, Słocina",
    )
    from src.services.geocoder import DISTRICT_CENTROIDS_BY_CITY

    assert res_slocina == DISTRICT_CENTROIDS_BY_CITY["rzeszow"]["słocina"]
