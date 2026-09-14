from datetime import UTC, datetime, timedelta

import pytest

from src.models.enums import BuildingType, FinishCondition, QualificationStatus, RoadType, SegmentSubtype, SewerageType
from src.models.listing import FilterResult, ListingSchema
from src.services.discord_notifier import DiscordNotifier
from src.services.market_analyzer import (
    NegotiationAdvice,
    PropertyValuationEngine,
    PropertyValuationIntelligence,
    analyze_land_and_utilities,
    analyze_negotiation,
    calculate_commute_audit,
    calculate_gesut_audit,
    calculate_notary_and_court_fee,
    calculate_risk_shield,
    calculate_tco_audit,
    resolve_local_median,
    valuation_engine,
)
from src.services.telegram_notifier import TelegramNotifier


def test_resolve_local_median():
    medians = {
        "rzeszow:slocina:dom": 7200.0,
        "rzeszow:zalesie:dom": 8100.0,
        "rzeszow::dom": 7500.0,
        "krakow::mieszkanie": 14000.0,
    }

    # 1. Exact match with district
    assert resolve_local_median(medians, "Rzeszow", "Slocina", "dom") == 7200.0
    # 2. Fallback to city when district not in medians
    assert resolve_local_median(medians, "Rzeszow", "Nowe Miasto", "dom") == 7500.0
    # 3. Fallback when district is None
    assert resolve_local_median(medians, "Rzeszow", None, "dom") == 7500.0
    # 4. Unknown city returns None
    assert resolve_local_median(medians, "Warszawa", "Mokotów", "dom") is None
    # 5. Empty city returns None
    assert resolve_local_median(medians, None, "Centrum", "dom") is None


def test_analyze_negotiation_high_leverage():
    created_date = datetime.now(UTC) - timedelta(days=75)
    listing = ListingSchema(
        id="test-neg-1",
        portal="Otodom",
        title="Dom wolnostojący do wykończenia",
        url="https://otodom.pl/oferta/test-neg-1",
        price=1_000_000,
        price_per_m2=10_000,
        area_home=100.0,
        area_plot=600.0,
        building_type=BuildingType.WOLNOSTOJACY,
        finish_condition=FinishCondition.DEWELOPERSKI,
        access_road_type=RoadType.POLNA,
        sewerage=SewerageType.SZAMBO,
        flood_risk_zone="ZAGROŻENIE_POWODZIOWE",
        location_raw="Rzeszów, Biała",
        city="Rzeszów",
        district="Biała",
        created_at=created_date,
    )

    filter_result = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=120.0,
        passed_stage1=True,
        passed_stage2=True,
        cons=["⚠️ [Ukryty koszt] Brak kanalizacji", "⚠️ [Ryzyko prawne] Służebność przejazdu"],
    )

    market_median_m2 = 8000.0  # Listing is 10,000 zl/m2 -> +25% vs market

    advice = analyze_negotiation(
        listing=listing,
        filter_result=filter_result,
        market_median_m2=market_median_m2,
        price_drop_amount=50_000,
        price_drop_pct=4.8,
        price_history_count=2,
    )

    assert advice.days_on_market >= 74
    assert advice.price_deviation_pct == 25.0
    # Deweloperski: comparable price = 10,000 / 0.95 = 10,526 -> +31.6% vs median
    assert advice.price_deviation_adjusted_pct == 31.6
    assert advice.negotiation_leverage == "WYSOKA"
    assert advice.fair_market_value is not None
    assert advice.suggested_opening_offer is not None
    # Opening offer should offer significant discount below 1,000,000
    assert advice.suggested_opening_offer < 950_000

    # Arguments should include days on market, price drop, deviation, and defects
    args_joined = " ".join(advice.arguments)
    assert "dni bez sprzedaży" in args_joined
    assert "obniżona" in args_joined
    assert "przewyższa lokalną medianę" in args_joined
    assert "po korekcie o stan wykończenia" in args_joined
    assert "wykończenie" in args_joined
    assert "nieutwardzoną" in args_joined


def test_analyze_negotiation_fair_price_low_leverage():
    created_date = datetime.now(UTC) - timedelta(days=5)
    listing = ListingSchema(
        id="test-neg-2",
        portal="Otodom",
        title="Dom pod klucz w dobrej cenie",
        url="https://otodom.pl/oferta/test-neg-2",
        price=800_000,
        price_per_m2=8_000,
        area_home=100.0,
        area_plot=500.0,
        building_type=BuildingType.SZEREGOWIEC,
        segment_subtype=SegmentSubtype.SKRAJNY,
        finish_condition=FinishCondition.DO_ZAMIESZKANIA,
        access_road_type=RoadType.ASFALT,
        sewerage=SewerageType.MIEJSKA,
        flood_risk_zone="BRAK_ZAGROŻENIA",
        location_raw="Rzeszów",
        city="Rzeszów",
        created_at=created_date,
    )

    market_median_m2 = 8000.0  # 0% deviation

    advice = analyze_negotiation(
        listing=listing,
        market_median_m2=market_median_m2,
    )

    assert advice.days_on_market <= 6
    assert advice.price_deviation_pct == 0.0
    # Pod klucz gets +5% premium: comparable = 8000 / 1.05 = 7619 -> -4.8%
    assert advice.price_deviation_adjusted_pct == -4.8
    assert advice.negotiation_leverage in ("NISKA", "ŚREDNIA")
    assert advice.fair_market_value is not None
    assert advice.suggested_opening_offer is not None


def test_discord_notifier_with_negotiation_advice():
    notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/fake/test")

    listing = ListingSchema(
        id="test-disc-1",
        portal="Otodom",
        title="Dom Rzeszów",
        url="https://otodom.pl/oferta/test-disc-1",
        price=900_000,
        price_per_m2=9_000,
        area_home=100.0,
        building_type=BuildingType.WOLNOSTOJACY,
        location_raw="Rzeszów",
        city="Rzeszów",
    )

    filter_result = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=130.0,
        passed_stage1=True,
        passed_stage2=True,
    )

    advice = NegotiationAdvice(
        market_median_m2=8000.0,
        price_deviation_pct=12.5,
        days_on_market=62,
        negotiation_leverage="WYSOKA",
        fair_market_value=820_000.0,
        suggested_opening_offer=770_000.0,
        arguments=["Oferta znajduje się na rynku od 62 dni."],
    )

    embed = notifier.format_embed(listing, filter_result, negotiation_advice=advice)
    field_names = [f["name"] for f in embed["fields"]]
    assert any("Wywiad negocjacyjny" in n for n in field_names)
    neg_field = next(f for f in embed["fields"] if "Wywiad negocjacyjny" in f["name"])
    assert "WYSOKA" in neg_field["value"]
    assert "8 000 zł/m²" in neg_field["value"]
    assert "770 000 zł" in neg_field["value"]


def test_telegram_notifier_with_negotiation_advice():
    notifier = TelegramNotifier(bot_token="fake_token", chat_id="12345")

    listing = ListingSchema(
        id="test-tg-1",
        portal="Otodom",
        title="Dom Rzeszów",
        url="https://otodom.pl/oferta/test-tg-1",
        price=900_000,
        price_per_m2=9_000,
        area_home=100.0,
        building_type=BuildingType.WOLNOSTOJACY,
        location_raw="Rzeszów",
        city="Rzeszów",
    )

    filter_result = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED,
        score=130.0,
        passed_stage1=True,
        passed_stage2=True,
    )

    advice = NegotiationAdvice(
        market_median_m2=8000.0,
        price_deviation_pct=12.5,
        days_on_market=62,
        negotiation_leverage="WYSOKA",
        fair_market_value=820_000.0,
        suggested_opening_offer=770_000.0,
        arguments=["Oferta znajduje się na rynku od 62 dni."],
    )

    msg = notifier.format_message(listing, filter_result, negotiation_advice=advice)
    assert "Negocjacje:" in msg
    assert "WYSOKA" in msg
    assert "8 000 zł/m²" in msg
    assert "770 000 zł" in msg


@pytest.mark.asyncio
async def test_repository_get_market_medians():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from src.storage.models import Base, ListingModel
    from src.storage.repository import ListingRepository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        # Add 3 listings in Rzeszów Słocina (dom) and 1 in Rzeszów Zalesie (dom)
        l1 = ListingModel(
            portal="Otodom",
            portal_id="1",
            url="https://example.com/1",
            property_fingerprint="fp1",
            title="Dom 1",
            price=700_000,
            price_per_m2=7000.0,
            area_home=100.0,
            city="Rzeszów",
            district="Słocina",
            category="dom",
        )
        l2 = ListingModel(
            portal="Otodom",
            portal_id="2",
            url="https://example.com/2",
            property_fingerprint="fp2",
            title="Dom 2",
            price=800_000,
            price_per_m2=8000.0,
            area_home=100.0,
            city="Rzeszów",
            district="Słocina",
            category="dom",
        )
        l3 = ListingModel(
            portal="Otodom",
            portal_id="3",
            url="https://example.com/3",
            property_fingerprint="fp3",
            title="Dom 3",
            price=900_000,
            price_per_m2=9000.0,
            area_home=100.0,
            city="Rzeszów",
            district="Słocina",
            category="dom",
        )
        l4 = ListingModel(
            portal="Otodom",
            portal_id="4",
            url="https://example.com/4",
            property_fingerprint="fp4",
            title="Dom 4",
            price=600_000,
            price_per_m2=6000.0,
            area_home=100.0,
            city="Rzeszów",
            district="Zalesie",
            category="dom",
        )
        session.add_all([l1, l2, l3, l4])
        await session.commit()

        repo = ListingRepository(session)
        medians = await repo.get_market_medians()

        # Słocina median of [7000, 8000, 9000] should be 8000
        assert medians["rzeszów:słocina:dom"] == 8000.0
        # Zalesie median of [6000] should be 6000
        assert medians["rzeszów:zalesie:dom"] == 6000.0
        # City-wide median of [6000, 7000, 8000, 9000] should be 7500.0
        assert medians["rzeszów::dom"] == 7500.0

    await engine.dispose()


def test_analyze_land_and_utilities_teryt_parsing_and_packet():
    listing = ListingSchema(
        id="audit-test-1",
        portal="Otodom",
        title="Dom z działką Warszawa",
        url="https://otodom.pl/oferta/audit-test-1",
        price=1_500_000,
        price_per_m2=10_000,
        area_home=150.0,
        area_plot=850.0,
        city="Warszawa",
        district="Wilanów",
        street="Przyczółkowa",
        location_raw="Warszawa, Wilanów",
        parcel_id="141201_1.0001.123/4",
        cadastral_area=850.0,
    )

    audit = analyze_land_and_utilities(listing)
    packet = audit["search_packet"]

    assert packet["voivodeship"] == "Mazowieckie"
    assert packet["parcel_short"] == "123/4"
    assert packet["obreb"] == "0001"
    assert packet["cadastral_area"] == 850.0
    assert "Numer działki: 123/4" in packet["clipboard_text"]
    assert "Mazowieckie" in packet["clipboard_text"]
    assert "141201_1.0001.123/4" in packet["clipboard_text"]


def test_calculate_notary_and_court_fee():
    # Rozporządzenie Ministra Sprawiedliwości:
    # Over 1,000,000 to 2,000,000: 4,770 zł + 0.2% over 1,000,000 + 23% VAT + 400 PLN KW fee
    fee_1m = calculate_notary_and_court_fee(1_000_000)
    # base = 4770, vat = 4770 * 0.23 = 1097.10, court = 400 -> ~6267.10
    assert 6200 <= fee_1m <= 6300

    fee_500k = calculate_notary_and_court_fee(500_000)
    # Over 60,000 to 1,000,000: 1010 + 0.4% of (500k - 60k) = 1010 + 1760 = 2770 -> +23% VAT + 400 = 3407.10 + 400 = 3807.10
    assert 3700 <= fee_500k <= 3900


def test_calculate_tco_audit_developer_state():
    listing = {
        "price": 600_000,
        "area_home": 100.0,
        "market": "pierwotny",
        "finish_condition": "deweloperski",
        "sewerage": "szambo",
        "access_road_type": "polna",
    }
    tco = calculate_tco_audit(listing, market_median_m2=6500.0)

    # 1. Purchase price = 600,000
    assert tco["purchase_price"] == 600_000.0
    # 2. Developer market -> PCC = 0
    # 3. Finishing cost = 100 m² * 1800 zł = 180,000 zł
    # 4. Infra cost = 18,000 (szambo) + 12,000 (droga) = 30,000 zł
    # 5. Hidden costs total > 200,000 zł
    assert tco["hidden_costs_total"] >= 210_000.0
    assert tco["total_acquisition_cost"] == tco["purchase_price"] + tco["hidden_costs_total"]
    assert tco["finishing_cost"] == 180_000.0
    assert tco["transaction_costs"] == round(tco["hidden_costs_total"] - 180_000.0, 0)
    assert tco["severity"] in ("danger", "warning")
    items = [b["item"] for b in tco["breakdown"]]
    assert any("Wykończenie" in i for i in items)
    assert "Infrastruktura (Kanalizacja / Droga)" in items


def test_calculate_tco_audit_secondary_market():
    listing = {
        "price": 500_000,
        "area_home": 70.0,
        "market": "wtórny",
        "finish_condition": "do_zamieszkania",
        "sewerage": "miejska",
        "access_road_type": "asfaltowa",
    }
    tco = calculate_tco_audit(listing)

    # Secondary market -> 2% PCC = 10,000 zł
    pcc_entry = next((b for b in tco["breakdown"] if "PCC" in b["item"]), None)
    assert pcc_entry is not None
    assert pcc_entry["amount"] == 10_000.0

    # Ready to move in -> 0 zł finishing cost
    fin_entry = next((b for b in tco["breakdown"] if "Wykończenie" in b["item"]), None)
    assert fin_entry is not None
    assert fin_entry["amount"] == 0.0
    assert "brak nakładów" in fin_entry["desc"]
    assert tco["finishing_cost"] == 0.0
    # No finishing -> all hidden costs are transaction costs (PCC + notary + agency)
    assert tco["transaction_costs"] == tco["hidden_costs_total"]
    assert tco["transaction_costs"] >= 10_000.0


def test_calculate_commute_audit_with_coords():
    # Near Rzeszów Rynek (50.0375, 22.0047)
    listing = {
        "latitude": 50.0375,
        "longitude": 22.0047,
        "city": "Rzeszów",
        "district": "Śródmieście",
    }
    commute = calculate_commute_audit(listing)
    assert commute["has_coords"] is True
    assert commute["dist_center_km"] < 1.0
    assert commute["nearest_pka"]["name"] is not None
    assert commute["nearest_expressway"]["name"] is not None
    assert commute["verdict"] == "WYBITNA KOMUNIKACJA I DOSTĘPNOŚĆ"
    assert commute["severity"] == "success"


def test_calculate_commute_audit_without_coords():
    listing = {
        "latitude": None,
        "longitude": None,
        "city": "Krasne",
        "district": "",
    }
    commute = calculate_commute_audit(listing)
    assert commute["has_coords"] is False
    assert commute["verdict"] == "LOKALIZACJA PRZYBLIŻONA"
    assert commute["dist_center_km"] is None


def test_calculate_commute_audit_unknown_city_no_reference():
    # City outside CITY_CENTROIDS with no recognizable location text must not
    # fabricate a centroid (previously fell back to the nearest known city).
    listing = {
        "latitude": 52.1150,
        "longitude": 21.2640,
        "city": "Nieistniejowo",
        "district": "",
        "location_raw": "Nieistniejowo",
    }
    commute = calculate_commute_audit(listing)
    assert commute["has_coords"] is True
    assert commute["dist_center_km"] is None
    assert commute["commute_time_min"] is None
    assert commute["verdict"] == "BRAK PUNKTU ODNIESIENIA DOJAZDU"
    titles = " ".join(f["title"] for f in commute["findings"])
    assert "Centrum" not in titles
    assert "Rzesz" not in titles
    assert "Brak punktu odniesienia dojazdu" in titles


def test_calculate_commute_audit_no_false_anchor_from_inflected_text():
    # "ul. Krakowska" contains the substring "krakow", but must NOT anchor to Kraków.
    listing = {
        "latitude": 49.2992,
        "longitude": 19.9496,
        "city": "Zakopane",
        "district": "",
        "location_raw": "Zakopane, ul. Krakowska",
    }
    commute = calculate_commute_audit(listing)
    assert commute["dist_center_km"] is None
    assert commute["verdict"] == "BRAK PUNKTU ODNIESIENIA DOJAZDU"


def test_calculate_commute_audit_word_boundary_anchor():
    # A district name containing a known city should still anchor correctly.
    listing = {
        "latitude": 52.2317,
        "longitude": 21.0183,
        "city": "Mokotów",
        "district": "",
        "location_raw": "Warszawa, Mokotów",
    }
    commute = calculate_commute_audit(listing)
    assert commute["dist_center_km"] is not None
    assert commute["dist_center_km"] < 5.0
    titles = " ".join(f["title"] for f in commute["findings"])
    assert "Warszawa" in titles


def test_calculate_risk_shield_danger_cases():
    listing = {
        "mpzp_status": "BRAK",
        "flood_risk_zone": "ZAGROŻENIE_POWODZIOWE",
        "area_plot": 500.0,
        "cadastral_area": 800.0,
        "cons": ["Sąsiedztwo Ba/Bi tereny przemysłowe"],
    }
    risk = calculate_risk_shield(listing)
    assert risk["severity"] == "danger"
    titles = [f["title"] for f in risk["findings"]]
    assert any("Brak MPZP" in t for t in titles)
    assert any("ryzyko zalania wodami 100-letnimi" in t for t in titles)
    assert any("Różnica między ogłoszeniem a państwowym katastrem" in t for t in titles)
    assert any("Wykryto tereny komercyjne" in t for t in titles)


def test_analyze_land_and_utilities_comprehensive():
    listing = {
        "price": 750_000,
        "area_home": 120.0,
        "area_plot": 700.0,
        "cadastral_area": 700.0,
        "market": "pierwotny",
        "finish_condition": "deweloperski",
        "sewerage": "szambo",
        "access_road_type": "polna",
        "latitude": 50.0400,
        "longitude": 22.0100,
        "mpzp_status": "OBOWIĄZUJĄCY",
        "mpzp_zone": "MN",
        "parcel_id": "186301_1.0001.555/2",
        "city": "Rzeszów",
    }
    audit = analyze_land_and_utilities(listing, market_median_m2=7000.0)

    assert "tco_audit" in audit
    assert "commute_audit" in audit
    assert "risk_shield" in audit
    assert "gesut_audit" in audit
    assert "cadastral_packet" in audit

    # Cadastral packet
    assert audit["cadastral_packet"]["parcel_short"] == "555/2"
    assert audit["cadastral_packet"]["voivodeship"] == "Podkarpackie"

    # TCO has finishing and infra
    assert audit["tco_audit"]["total_acquisition_cost"] > 750_000.0

    # Commute has coordinates and PKA
    assert audit["commute_audit"]["has_coords"] is True
    assert audit["commute_audit"]["nearest_pka"] is not None

    # GESUT detects szambo
    assert any("Bieżące koszty asenizacyjne" in f["title"] for f in audit["gesut_audit"]["findings"])


def test_analyze_land_and_utilities_safe_listing():
    listing = {
        "price": 600_000,
        "market": "wtórny",
        "finish_condition": "do_zamieszkania",
        "year_built": 2018,
        "sewerage": "miejska",
        "access_road_type": "asfaltowa",
        "has_fiber": True,
        "mpzp_status": "OBOWIĄZUJĄCY",
        "mpzp_zone": "MN",
        "area_plot": 500.0,
        "cadastral_area": 500.0,
        "city": "Gdańsk",
        "parcel_id": "226101_1.0002.50/1",
    }

    audit = analyze_land_and_utilities(listing)

    assert audit["risk_shield"]["severity"] == "success"
    assert audit["gesut_audit"]["severity"] == "success"

    gesut_titles = [f["title"] for f in audit["gesut_audit"]["findings"]]
    assert any("Pełen komfort sanitarny" in t for t in gesut_titles)
    assert any("Dostęp do infrastruktury drogowej" in t for t in gesut_titles)
    assert any("Szybki internet na działce" in t for t in gesut_titles)


def test_calculate_gesut_audit_with_real_gesut_data():
    listing = {
        "sewerage": "miejska",
        "heating": "gazowe",
        "has_fiber": True,
        "gesut_networks": {
            "coverage": True,
            "checked_radius_m": 20.0,
            "sources": ["Miasto Rzeszów"],
            "networks": {
                "woda": True,
                "kanalizacja": True,
                "gaz": False,
                "prad": True,
                "cieplo": False,
                "telekomunikacja": False,
            },
        },
    }
    gesut = calculate_gesut_audit(listing)

    assert gesut["data_driven"] is True
    assert gesut["severity"] == "warning"
    assert "Miasto Rzeszów" in gesut["source"]
    titles = [f["title"] for f in gesut["findings"]]
    assert any("Sieć wodociągowa wykryta" in t for t in titles)
    assert any("Sieć elektroenergetyczna wykryta" in t for t in titles)
    assert any("Sieć gazowa niewykryta" in t for t in titles)
    assert any("Sieć telekomunikacyjna niewykryta" in t for t in titles)
    # Gaz absent + gazowe heating -> warning, telecom absent + fiber claim -> warning
    warn = [f for f in gesut["findings"] if f["severity"] == "warning"]
    assert any("Sieć gazowa niewykryta" in f["title"] for f in warn)
    assert any("Sieć telekomunikacyjna niewykryta" in f["title"] for f in warn)
    # Source of data is present
    assert any("Źródło danych" in f["badge"] for f in gesut["findings"])


def test_calculate_gesut_audit_fallback_without_gesut_data():
    listing = {
        "sewerage": "miejska",
        "access_road_type": "asfaltowa",
        "heating": "gazowe",
        "has_fiber": True,
    }
    gesut = calculate_gesut_audit(listing)
    assert gesut["data_driven"] is False
    titles = [f["title"] for f in gesut["findings"]]
    assert any("Pełen komfort sanitarny" in t for t in titles)


def test_analyze_negotiation_with_tier1_factors():
    listing = {
        "price": 1_000_000,
        "price_per_m2": 10_000,
        "area_home": 100.0,
        "finish_condition": "pod_klucz",
        "landslide_risk": "OSUWISKO",
        "noise_level_db": 70.0,
        "noise_zone": "WYSOKI_HAŁAS (>65 dB)",
        "monument_zone": "Pałacyk",
        "cemetery_buffer_zone": "<50m",
        "egib_building_status": "BRAK_W_EWIDENCJI",
        "egib_soil_class": "RIIIa",
    }

    advice = analyze_negotiation(listing, market_median_m2=10_000.0)
    assert advice.negotiation_leverage == "WYSOKA"
    assert advice.fair_market_value is not None
    # Base FMV was 100 * 10,000 = 1,000,000.
    # Adjustments: pod_klucz (+0.05), landslide (-0.15), noise (-0.05), monument (-0.05), cemetery (-0.07) -> factor 0.73
    # 1,000,000 * 0.73 = 730,000
    assert advice.fair_market_value <= 750_000

    args_joined = " ".join(advice.arguments)
    assert "osuwisko" in args_joined.lower()
    assert "hałasu" in args_joined.lower()
    assert "cmentarza" in args_joined.lower()


def test_calculate_risk_shield_with_tier1_factors():
    listing = {
        "mpzp_status": "OBOWIĄZUJĄCY",
        "mpzp_zone": "MN",
        "flood_risk_zone": "BRAK",
        "landslide_risk": "OSUWISKO",
        "egib_building_status": "BRAK_W_EWIDENCJI",
        "egib_soil_class": "RIIIa",
        "noise_level_db": 69.0,
        "noise_zone": "WYSOKI_HAŁAS (>65 dB)",
        "nature_protected_zone": "Natura 2000",
        "monument_zone": "Kościółek",
        "cemetery_buffer_zone": "<50m",
    }

    shield = calculate_risk_shield(listing)
    assert shield["severity"] == "danger"
    badges = [f["badge"] for f in shield["findings"]]
    assert any("Zagrożenie Osuwiskowe" in b for b in badges)
    assert any("Dom Nieujawniony w EGiB" in b for b in badges)
    assert any("Grunt Chroniony w EGiB" in b for b in badges)
    assert any("Podwyższony Hałas" in b for b in badges)
    assert any("Obszar Chroniony GDOŚ" in b for b in badges)
    assert any("Zabytek" in b for b in badges)
    assert any("Strefa Sanitarna Cmentarza" in b for b in badges)


def test_calculate_risk_shield_with_shape_slope_power_fiber_pka():
    listing = {
        "mpzp_status": "OBOWIĄZUJĄCY",
        "mpzp_zone": "MN",
        "flood_risk_zone": "BRAK",
        "parcel_front_width_m": 12.0,
        "parcel_shape_type": "WĄSKA_SZNUROWKA",
        "parcel_length_m": 80.0,
        "parcel_aspect_ratio": 6.7,
        "terrain_slope_pct": 10.5,
        "terrain_aspect": "PÓŁNOCNY",
        "power_lines_risk": "Kolizja z korytarzem linii WN 400kV",
        "broadband_status": "BRAK_ZASIĘGU",
        "walkability_pka_dist_m": 900.0,
        "walkability_pka_name": "Rzeszów Załęże",
    }

    shield = calculate_risk_shield(listing)
    assert shield["severity"] == "danger"
    badges = [f["badge"] for f in shield["findings"]]
    assert any("Wąski Front Działki" in b for b in badges)
    assert any("Strome Nachylenie Terenu" in b for b in badges)
    assert any("Linia Wysokiego Napięcia" in b for b in badges)
    assert any("Brak Światłowodu" in b for b in badges)
    assert any("Stacja Kolejowa w Zasięgu Spaceru" in b for b in badges)


def test_analyze_negotiation_with_shape_slope_power_factors():
    listing = {
        "price": 1_000_000,
        "price_per_m2": 10_000,
        "area_home": 100.0,
        "finish_condition": "pod_klucz",
        "parcel_front_width_m": 13.5,
        "terrain_slope_pct": 9.2,
        "power_lines_risk": "Linia WN 400kV w odległości 80m",
        "broadband_status": "BRAK_ZASIĘGU",
    }

    advice = analyze_negotiation(listing, market_median_m2=10_000.0)
    assert advice.negotiation_leverage == "WYSOKA"
    assert advice.fair_market_value is not None
    # Adjustments: pod_klucz (+0.05), narrow front (-0.05), slope (-0.04), power line (-0.06), no broadband (-0.02)
    # Total factor = 1.0 + 0.05 - 0.17 = 0.88 -> FMV ~880,000
    assert advice.fair_market_value <= 900_000

    args_joined = " ".join(advice.arguments)
    assert "front" in args_joined.lower()
    assert "nachylenie" in args_joined.lower() or "spadek" in args_joined.lower()
    assert "wysokiego napięcia" in args_joined.lower()
    assert "światłowodu" in args_joined.lower()


def test_property_valuation_engine_evaluate():
    listing = {
        "city": "Rzeszów",
        "district": "Słocina",
        "category": "dom",
        "price": 800_000,
        "price_per_m2": 8_000,
        "area_home": 100.0,
        "finish_condition": "pod_klucz",
        "latitude": 50.02,
        "longitude": 22.04,
        "access_road_type": "asfaltowa",
        "sewerage": "miejska",
    }
    medians = {"rzeszów:słocina:dom": 7500.0}

    intel = valuation_engine.evaluate(
        listing=listing,
        market_medians=medians,
        price_drop_amount=20_000,
        price_drop_pct=2.4,
        price_history_count=2,
    )

    assert isinstance(intel, PropertyValuationIntelligence)
    assert intel.local_median_m2 == 7500.0
    assert intel.negotiation.market_median_m2 == 7500.0
    assert intel.negotiation.price_deviation_pct == 6.7
    assert "tco_audit" in intel.land_and_utilities
    assert "commute_audit" in intel.land_and_utilities
    assert "risk_shield" in intel.land_and_utilities
    assert "gesut_audit" in intel.land_and_utilities

    # Helper properties
    assert intel.tco_audit is intel.land_and_utilities["tco_audit"]
    assert intel.commute_audit is intel.land_and_utilities["commute_audit"]
    assert intel.risk_shield is intel.land_and_utilities["risk_shield"]
    assert intel.gesut_audit is intel.land_and_utilities["gesut_audit"]
    assert intel.cadastral_packet is intel.land_and_utilities["cadastral_packet"]

    # Dashboard dict format
    d = intel.to_dashboard_dict()
    assert d["market_median_m2"] == 7500.0
    assert d["price_deviation_pct"] == 6.7
    assert d["fair_market_value"] is not None
    assert d["suggested_opening_offer"] is not None
    assert "land_audit" in d
    assert d["land_audit"] == intel.land_and_utilities


def test_property_valuation_engine_custom_medians():
    engine = PropertyValuationEngine(market_medians={"krakow::mieszkanie": 15000.0})
    assert engine.resolve_median("Krakow", None, "mieszkanie") == 15000.0
    assert engine.resolve_median("Warszawa", None, "mieszkanie") is None
