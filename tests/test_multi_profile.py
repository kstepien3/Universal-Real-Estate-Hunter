from src.filters.stage1_hard_rules import Stage1Filter
from src.filters.stage2_semantic import Stage2SemanticFilter
from src.models.enums import (
    BuildingType,
    PropertyCategory,
    SegmentSubtype,
)
from src.models.listing import ListingSchema
from src.services.config_manager import (
    ConfigManager,
    SearchConfig,
    SearchProfile,
)


def test_legacy_config_migration():
    """Verify legacy single-city configuration is smoothly migrated to SearchProfile."""
    legacy_dict = {
        "city": "Rzeszów",
        "distance_radius": 15,
        "max_price": 1_250_000,
        "min_price": 500_000,
        "min_area_home": 90,
        "max_area_home": 160,
        "min_area_plot": 300,
        "market_type": "wtórny",
        "blacklist_keywords": ["osuwisko", "zalewowe"],
    }
    cfg = ConfigManager._migrate_legacy_dict(legacy_dict)
    assert len(cfg.profiles) == 1

    prof = cfg.profiles[0]
    assert prof.city == "Rzeszów"
    assert prof.distance_radius == 15
    assert prof.max_price == 1_250_000
    assert prof.category == PropertyCategory.DOM
    assert prof.enabled is True

    # Check SearchConfig wrapper proxy backward compatibility
    assert cfg.city == "Rzeszów"
    assert cfg.distance_radius == 15
    assert cfg.max_price == 1_250_000


def test_search_config_llm_analysis_default_follows_env(monkeypatch):
    """Verify llm_analysis_enabled defaults to USE_LLM_ANALYSIS and can be toggled."""
    from config import settings

    monkeypatch.setattr(settings, "USE_LLM_ANALYSIS", True)
    cfg = SearchConfig()
    assert cfg.llm_analysis_enabled is True

    monkeypatch.setattr(settings, "USE_LLM_ANALYSIS", False)
    cfg_off = SearchConfig()
    assert cfg_off.llm_analysis_enabled is False


def test_update_config_llm_analysis_toggle(tmp_path):
    """Verify update_config persists llm_analysis_enabled without leaking into profiles."""
    config_file = tmp_path / "search_config.json"
    mgr = ConfigManager(config_path=str(config_file))

    updated = mgr.update_config({"llm_analysis_enabled": True})
    assert updated.llm_analysis_enabled is True
    assert not hasattr(updated.profiles[0], "llm_analysis_enabled")

    # Reload from disk to confirm persistence
    mgr_reloaded = ConfigManager(config_path=str(config_file))
    assert mgr_reloaded.get_config().llm_analysis_enabled is True

    # Toggle back off
    mgr_reloaded.update_config({"llm_analysis_enabled": False})
    assert mgr_reloaded.get_config().llm_analysis_enabled is False


def test_update_config_ollama_connection_settings(tmp_path):
    """Verify ollama_base_url/timeout persist via update_config and reload from disk."""
    config_file = tmp_path / "search_config.json"
    mgr = ConfigManager(config_path=str(config_file))

    updated = mgr.update_config({"ollama_base_url": "http://192.168.1.10:11434/", "ollama_timeout_seconds": 300})
    assert updated.ollama_base_url == "http://192.168.1.10:11434"
    assert updated.ollama_timeout_seconds == 300.0

    mgr_reloaded = ConfigManager(config_path=str(config_file))
    assert mgr_reloaded.get_config().ollama_base_url == "http://192.168.1.10:11434"
    assert mgr_reloaded.get_config().ollama_timeout_seconds == 300.0


def test_llm_analyzer_uses_gui_ollama_settings(monkeypatch):
    """LLMAnalyzer must prefer search_config.json ollama url/timeout over ENV."""
    from src.filters.llm_analyzer import LLMAnalyzer
    from src.services.config_manager import config_manager

    cfg = SearchConfig(ollama_base_url="http://gui-host:11434", ollama_timeout_seconds=42.0)
    monkeypatch.setattr(config_manager, "get_config", lambda: cfg)
    analyzer = LLMAnalyzer(enabled=False)
    assert analyzer.ollama_url == "http://gui-host:11434"
    assert analyzer.ollama_timeout_seconds == 42.0


def test_profile_url_generation():
    """Verify portal URL construction for different categories and filters."""
    # 1. Apartment in Kraków
    apt_profile = SearchProfile(
        id="apt_krakow",
        name="Kraków Mieszkania",
        category=PropertyCategory.MIESZKANIE,
        city="Kraków",
        distance_radius=10,
        min_price=400_000,
        max_price=850_000,
        min_area_home=45,
        max_area_home=75,
        min_rooms=2,
        max_rooms=3,
        market_type="wtórny",
        owner_type="private",
    )

    otodom_url = apt_profile.get_otodom_url()
    assert "/sprzedaz/mieszkanie/" in otodom_url
    assert "krakow" in otodom_url
    assert "priceMin=400000" in otodom_url
    assert "priceMax=850000" in otodom_url
    assert "areaMin=45" in otodom_url
    assert "areaMax=75" in otodom_url
    assert "roomsNumber=%5BTWO" in otodom_url
    assert "market=SECONDARY" in otodom_url or "market=%5BSECONDARY%5D" in otodom_url
    assert "ownerTypeSingleSelect=PRIVATE" in otodom_url or "ownerTypeSingleSelect=BY_PRIVATE" in otodom_url

    olx_url = apt_profile.get_olx_url()
    assert "/nieruchomosci/mieszkania/sprzedaz/krakow/" in olx_url
    assert "filter_float_price%3Afrom" in olx_url and "400000" in olx_url
    assert "filter_float_price%3Ato" in olx_url and "850000" in olx_url
    assert "filter_float_m%3Afrom" in olx_url and "45" in olx_url
    assert "filter_enum_rooms" in olx_url

    no_url = apt_profile.get_nieruchomosci_online_url()
    assert "/mieszkania,sprzedaz/" in no_url
    assert "krakow" in no_url
    assert "cena_od=400000" in no_url
    assert "cena_do=850000" in no_url
    assert "liczba-pokoi_od=2" in no_url
    assert "liczba-pokoi_do=3" in no_url

    # 2. Plot in Rzeszów
    plot_profile = SearchProfile(
        id="plot_rzeszow",
        name="Działki Rzeszów",
        category=PropertyCategory.DZIALKA,
        city="Rzeszów",
        distance_radius=15,
        min_price=100_000,
        max_price=400_000,
        min_area_plot=800,
        max_area_plot=2500,
    )
    plot_otodom = plot_profile.get_otodom_url()
    assert "/sprzedaz/dzialka/" in plot_otodom
    assert "rzeszow" in plot_otodom
    assert "areaMin=800" in plot_otodom
    assert "areaMax=2500" in plot_otodom
    assert "/nieruchomosci/dzialki/sprzedaz/rzeszow/" in plot_profile.get_olx_url()
    assert "/dzialki,sprzedaz/" in plot_profile.get_nieruchomosci_online_url()


def test_stage1_apartment_rules():
    """Verify Stage 1 checks apartment rooms, area, and ignores plot requirements."""
    profile = SearchProfile(
        id="apt_test",
        name="Mieszkania Test",
        category=PropertyCategory.MIESZKANIE,
        city="Rzeszów",
        min_price=300_000,
        max_price=700_000,
        min_area_home=40,
        max_area_home=80,
        min_rooms=2,
        max_rooms=4,
        min_floor=1,
        max_floor=5,
    )
    rules = Stage1Filter(profile=profile)

    # Valid apartment (has no plot area, floor 3, 3 rooms)
    valid_apt = ListingSchema(
        id="apt-1",
        portal="Otodom",
        title="Przestronne 3-pokojowe mieszkanie z balkonem",
        url="https://otodom.pl/oferta/apt-1",
        price=550_000,
        price_per_m2=10_000,
        area_home=55.0,
        area_plot=0.0,
        category=PropertyCategory.MIESZKANIE,
        rooms=3,
        floor=3,
        floors_in_building=6,
        building_type=BuildingType.INNY,
        segment_subtype=SegmentSubtype.NIEOKRESLONY,
        location_raw="Rzeszów, Centrum",
        city="Rzeszów",
    )
    passed, reasons, _ = rules.evaluate(valid_apt)
    assert passed is True, f"Expected pass, got reasons: {reasons}"

    # Invalid apartment: only 1 room (min_rooms is 2)
    too_small_rooms = valid_apt.model_copy(update={"rooms": 1})
    passed, reasons, _ = rules.evaluate(too_small_rooms)
    assert passed is False
    assert any("pokoi" in r for r in reasons)

    # Invalid apartment: floor 8 (max_floor is 5)
    too_high_floor = valid_apt.model_copy(update={"floor": 8})
    passed, reasons, _ = rules.evaluate(too_high_floor)
    assert passed is False
    assert any("piętro" in r.lower() for r in reasons)


def test_stage2_apartment_and_plot_semantics():
    """Verify Stage 2 detects apartment attributes (balcony, elevator) and doesn't penalize for middle segment."""
    profile = SearchProfile(
        id="apt_sem",
        name="Mieszkania Semantyka",
        category=PropertyCategory.MIESZKANIE,
        city="Rzeszów",
    )
    analysis = Stage2SemanticFilter()

    desc = """
    Oferujemy na sprzedaż nowoczesne mieszkanie na 2. piętrze w bloku z windą.
    Do lokalu przynależy przestronny balkon 8 m2 oraz miejsce postojowe w garażu podziemnym.
    Ogrzewanie miejskie, budynek z 2022 roku.
    """

    apt_listing = ListingSchema(
        id="apt-sem-1",
        portal="Otodom",
        title="Komfortowe mieszkanie 3 pokoje z windą i dużym balkonem",
        url="https://otodom.pl/oferta/apt-sem-1",
        price=600_000,
        price_per_m2=10_000,
        area_home=60.0,
        category=PropertyCategory.MIESZKANIE,
        building_type=BuildingType.INNY,
        segment_subtype=SegmentSubtype.NIEOKRESLONY,
        location_raw="Rzeszów, Drabinianka",
        city="Rzeszów",
        raw_description=desc,
    )

    passed, rej, pros, cons, *rest = analysis.analyze(apt_listing, profile=profile)
    assert passed is True
    # Verify detected features
    assert any("Balkon" in p or "taras" in p.lower() for p in pros)
    assert any("Winda" in p for p in pros)
    assert any("Miejsce postojowe" in p or "garaż" in p.lower() for p in pros)


def test_config_manager_profile_crud(tmp_path):
    """Verify adding, updating, and deleting profiles in ConfigManager."""
    config_file = tmp_path / "search_config.json"
    mgr = ConfigManager(config_path=str(config_file))

    # Initial default profile created
    cfg = mgr.get_config()
    assert len(cfg.profiles) >= 1

    # Add a new profile
    new_p_data = {
        "id": "dzialki_podkarpacie",
        "name": "Działki Budowlane",
        "category": "dzialka",
        "city": "Łańcut",
        "distance_radius": 20,
        "min_price": 50_000,
        "max_price": 300_000,
        "min_area_plot": 1000,
        "max_area_plot": 3000,
        "enabled": True,
    }
    saved_prof = mgr.add_or_update_profile(new_p_data)
    assert saved_prof.id == "dzialki_podkarpacie"
    assert saved_prof.city == "Łańcut"
    assert saved_prof.category == PropertyCategory.DZIALKA

    # Check that it persisted
    mgr_reloaded = ConfigManager(config_path=str(config_file))
    assert len(mgr_reloaded.get_config().profiles) == 2
    p = mgr_reloaded.get_profile("dzialki_podkarpacie")
    assert p is not None
    assert p.name == "Działki Budowlane"

    # Delete profile
    deleted = mgr_reloaded.delete_profile("dzialki_podkarpacie")
    assert deleted is True
    assert len(mgr_reloaded.get_config().profiles) == 1


def test_profile_with_null_and_none_fields(tmp_path):
    """Verify that null/None values for max_area_home, min_price, etc. are valid and don't crash."""
    config_file = tmp_path / "search_config.json"
    mgr = ConfigManager(config_path=str(config_file))

    # Update config with explicit None values (as sent by frontend JSON)
    payload = {
        "profiles": [
            {
                "id": "plot_or_unlimited",
                "name": "Działki bez limitu metrażu domu",
                "category": "dzialka",
                "city": "Rzeszów",
                "distance_radius": None,
                "min_price": None,
                "max_price": None,
                "min_area_home": None,
                "max_area_home": None,
                "min_area_plot": 500,
                "max_area_plot": None,
                "enabled": True,
            }
        ]
    }
    cfg = mgr.update_config(payload)
    prof = cfg.profiles[0]
    assert prof.max_area_home is None
    assert prof.min_area_home is None
    assert prof.max_price is None

    # Test all URL generators work with None values
    otodom_url = prof.get_otodom_url()
    assert "https://www.otodom.pl" in otodom_url
    assert "areaMin=" in otodom_url
    assert "areaMax=" not in otodom_url

    olx_url = prof.get_olx_url()
    assert "https://www.olx.pl" in olx_url

    no_url = prof.get_nieruchomosci_online_url()
    assert "https://" in no_url

    morizon_url = prof.get_morizon_url()
    assert "https://" in morizon_url

    # Test Stage 1 filter evaluates cleanly
    stage1 = Stage1Filter(profile=prof)
    sample_listing = ListingSchema(
        id="listing-null-test",
        portal="Otodom",
        title="Działka budowlana w Rzeszowie",
        url="https://otodom.pl/oferta/d1",
        price=200_000,
        price_per_m2=200.0,
        area_home=0.0,
        area_plot=1000.0,
        category=PropertyCategory.DZIALKA,
        location_raw="Rzeszów",
        city="Rzeszów",
        raw_description="Piękna działka pod budowę",
    )
    passed, reasons, wl = stage1.evaluate(sample_listing)
    assert passed is True
    assert len(reasons) == 0
