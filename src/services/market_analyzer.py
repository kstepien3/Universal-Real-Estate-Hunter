import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.models.listing import FilterResult, ListingSchema


@dataclass
class NegotiationAdvice:
    """Actionable negotiation intelligence for a property listing."""

    market_median_m2: float | None
    price_deviation_pct: float | None
    days_on_market: int
    negotiation_leverage: str  # "WYSOKA" | "ŚREDNIA" | "NISKA"
    fair_market_value: float | None
    suggested_opening_offer: float | None
    price_deviation_adjusted_pct: float | None = None
    arguments: list[str] = field(default_factory=list)


def resolve_local_median(
    medians: dict[str, float],
    city: str | None,
    district: str | None,
    category: str | None,
) -> float | None:
    """
    Finds the best matching market median:
    1. Exact match: (city, district, category)
    2. City fallback: (city, category)
    """
    city_clean = (city or "").strip().lower()
    dist_clean = (district or "").strip().lower()
    cat_clean = (category or "dom").strip().lower()

    if not city_clean:
        return None

    if dist_clean:
        key_dist = f"{city_clean}:{dist_clean}:{cat_clean}"
        if key_dist in medians:
            return medians[key_dist]

    key_city = f"{city_clean}::{cat_clean}"
    return medians.get(key_city)


def _prop(obj: Any, field: str, default: Any = None) -> Any:
    val = obj.get(field, default) if isinstance(obj, dict) else getattr(obj, field, default)
    if hasattr(val, "value"):
        return val.value
    return val if val is not None else default


def analyze_negotiation(
    listing: ListingSchema | Any,
    filter_result: FilterResult | None = None,
    market_median_m2: float | None = None,
    price_drop_amount: float = 0.0,
    price_drop_pct: float = 0.0,
    price_history_count: int = 1,
) -> NegotiationAdvice:
    """
    Synthesizes property characteristics, market median, price history,
    and spatial defects into concrete negotiation advice.
    """
    # 1. Days on market (uses first_seen_at when available to reflect true cumulative exposure)
    created_at = _prop(listing, "first_seen_at", None) or _prop(listing, "created_at", None)
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        days_on_market = max(1, (now - created_at).days)
    else:
        days_on_market = 1

    # 2. Price deviation from market median
    price_per_m2 = float(_prop(listing, "price_per_m2", 0.0) or 0.0)
    price = float(_prop(listing, "price", 0.0) or 0.0)
    area_home = float(_prop(listing, "area_home", 0.0) or 0.0)

    price_deviation_pct: float | None = None
    if market_median_m2 and market_median_m2 > 0 and price_per_m2 > 0:
        price_deviation_pct = round(((price_per_m2 - market_median_m2) / market_median_m2) * 100.0, 1)

    # 3. Fair Market Value (FMV) calculation
    fair_market_value: float | None = None
    finish_cond = str(_prop(listing, "finish_condition", "") or "").lower()
    road_type = str(_prop(listing, "access_road_type", "") or "").lower()
    sewer_type = str(_prop(listing, "sewerage", "") or "").lower()
    flood_zone = str(_prop(listing, "flood_risk_zone", "") or "").upper()
    subtype = str(_prop(listing, "segment_subtype", "") or "").lower()
    landslide_risk = str(_prop(listing, "landslide_risk", "") or "").upper()
    noise_level = _prop(listing, "noise_level_db", None)
    noise_zone = str(_prop(listing, "noise_zone", "") or "").upper()
    cemetery_buffer = str(_prop(listing, "cemetery_buffer_zone", "") or "")
    monument_zone = str(_prop(listing, "monument_zone", "") or "")
    egib_status = str(_prop(listing, "egib_building_status", "") or "").upper()
    egib_soil = str(_prop(listing, "egib_soil_class", "") or "").upper()

    # Finish-condition normalization factor (same basis as FMV adjustments):
    # unfinished listings are comparable to the market median only after adding
    # the finishing burden, turnkey listings get a premium.
    finish_adjustment = 1.0
    if any(f in finish_cond for f in ("deweloperski", "do_wykonczenia")):
        finish_adjustment -= 0.05
    elif any(f in finish_cond for f in ("remont", "surowy")):
        finish_adjustment -= 0.15
    elif "pod_klucz" in finish_cond or "zamieszkania" in finish_cond:
        finish_adjustment += 0.05

    price_deviation_adjusted_pct: float | None = None
    if market_median_m2 and market_median_m2 > 0 and price_per_m2 > 0:
        comparable_price_per_m2 = price_per_m2 / finish_adjustment
        price_deviation_adjusted_pct = round(
            ((comparable_price_per_m2 - market_median_m2) / market_median_m2) * 100.0, 1
        )

    if market_median_m2 and area_home > 0:
        # Base benchmark value
        base_fmv = market_median_m2 * area_home
        # Adjustments based on verified technical attributes
        adjustment_factor = 1.0

        if any(f in finish_cond for f in ("deweloperski", "do_wykonczenia")):
            adjustment_factor -= 0.05
        elif any(f in finish_cond for f in ("remont", "surowy")):
            adjustment_factor -= 0.15
        elif "pod_klucz" in finish_cond or "zamieszkania" in finish_cond:
            adjustment_factor += 0.05

        if any(r in road_type for r in ("nieutwardzona", "polna", "gruntowa")):
            adjustment_factor -= 0.03
        if any(s in sewer_type for s in ("szambo", "brak")):
            adjustment_factor -= 0.02
        if "POWODZ" in flood_zone:
            adjustment_factor -= 0.07

        # Tier 1 Spatial & Environmental adjustments
        if "OSUWISKO" in landslide_risk or "ZAGROŻENIE" in landslide_risk:
            adjustment_factor -= 0.15
        if (noise_level and float(noise_level) > 65.0) or "WYSOKI" in noise_zone:
            adjustment_factor -= 0.05
        if monument_zone:
            adjustment_factor -= 0.05
        if cemetery_buffer == "<50m":
            adjustment_factor -= 0.07
        elif cemetery_buffer == "50-150m":
            adjustment_factor -= 0.03

        front_w = _prop(listing, "parcel_front_width_m", None)
        slope = _prop(listing, "terrain_slope_pct", None)
        p_risk = str(_prop(listing, "power_lines_risk", "") or "").upper()
        b_status = str(_prop(listing, "broadband_status", "") or "").upper()

        if front_w is not None and float(front_w) < 16.0:
            adjustment_factor -= 0.05
        if slope is not None and float(slope) > 8.0:
            adjustment_factor -= 0.04
        if any(k in p_risk for k in ("LINIA", "400KV", "220KV", "110KV", "WN")):
            adjustment_factor -= 0.06
        if b_status in ("BRAK", "BRAK_ZASIĘGU"):
            adjustment_factor -= 0.02

        fair_market_value = round(base_fmv * adjustment_factor / 1000.0) * 1000.0

    # 4. Suggested Opening Offer
    if fair_market_value and fair_market_value > 0 and price > 0:
        # Target: 6% below Fair Market Value, capped to never exceed 95% of listing price
        opening_candidate = round((fair_market_value * 0.94) / 1000.0) * 1000.0
        suggested_opening = min(opening_candidate, round((price * 0.95) / 1000.0) * 1000.0)
        # Avoid absurd lowball below 70% of listing price
        suggested_opening = max(suggested_opening, round((price * 0.70) / 1000.0) * 1000.0)
    elif price > 0:
        # Fallback based on days on market and price drops
        discount_rate = 0.05
        if days_on_market >= 60:
            discount_rate += 0.05
        if price_drop_amount > 0:
            discount_rate += 0.03
        suggested_opening = round((price * (1.0 - discount_rate)) / 1000.0) * 1000.0
    else:
        suggested_opening = None

    # 5. Negotiation Leverage Scoring
    leverage_points = 0
    if days_on_market >= 60:
        leverage_points += 2
    elif days_on_market >= 30:
        leverage_points += 1

    if price_history_count >= 2 or price_drop_amount > 0:
        leverage_points += 2

    if price_deviation_adjusted_pct is not None and price_deviation_adjusted_pct >= 10.0:
        leverage_points += 2
    elif price_deviation_adjusted_pct is not None and price_deviation_adjusted_pct >= 4.0:
        leverage_points += 1

    if any(f in finish_cond for f in ("deweloperski", "do_wykonczenia", "remont", "surowy")):
        leverage_points += 1
    if any(r in road_type for r in ("nieutwardzona", "polna", "gruntowa")):
        leverage_points += 1
    if any(s in sewer_type for s in ("szambo", "brak")):
        leverage_points += 1
    if "POWODZ" in flood_zone:
        leverage_points += 2

    # Tier 1 Leverage points
    if "OSUWISKO" in landslide_risk or "ZAGROŻENIE" in landslide_risk:
        leverage_points += 3
    if (noise_level and float(noise_level) > 65.0) or "WYSOKI" in noise_zone:
        leverage_points += 1
    if monument_zone:
        leverage_points += 1
    if cemetery_buffer == "<50m":
        leverage_points += 2
    elif cemetery_buffer == "50-150m":
        leverage_points += 1
    if egib_status == "BRAK_W_EWIDENCJI":
        leverage_points += 2

    front_width = _prop(listing, "parcel_front_width_m", None)
    slope_pct = _prop(listing, "terrain_slope_pct", None)
    power_risk = str(_prop(listing, "power_lines_risk", "") or "").upper()
    broadband = str(_prop(listing, "broadband_status", "") or "").upper()

    if front_width is not None and float(front_width) < 16.0:
        leverage_points += 2
    if slope_pct is not None and float(slope_pct) > 8.0:
        leverage_points += 2
    if any(k in power_risk for k in ("LINIA", "400KV", "220KV", "110KV", "WN")):
        leverage_points += 2
    if broadband in ("BRAK", "BRAK_ZASIĘGU"):
        leverage_points += 1

    relist_count = int(_prop(listing, "relist_count", 0) or 0)
    initial_price = _prop(listing, "initial_price", None)
    if relist_count > 0:
        leverage_points += 3
    if days_on_market >= 90:
        leverage_points += 2

    if leverage_points >= 4:
        negotiation_leverage = "WYSOKA"
    elif leverage_points >= 2:
        negotiation_leverage = "ŚREDNIA"
    else:
        negotiation_leverage = "NISKA"

    # 6. Hard Negotiation Arguments
    arguments: list[str] = []

    if relist_count > 0:
        curr_price = float(_prop(listing, "price", 0.0) or 0.0)
        init_p = float(initial_price) if initial_price else None
        if init_p and init_p > curr_price:
            total_drop = init_p - curr_price
            drop_pct = (total_drop / init_p) * 100.0
            init_str = f"{init_p:,.0f}".replace(",", " ")
            drop_str = f"{total_drop:,.0f}".replace(",", " ")
            arguments.append(
                f"Wykryto pozorny re-listing ({relist_count}x): oferta na rynku łącznie {days_on_market} dni, "
                f"pierwotna cena: {init_str} zł (spadek o {drop_str} zł / -{drop_pct:.1f}%). "
                "Sprzedający jest pod silną presją czasu."
            )
        else:
            arguments.append(
                f"Wykryto re-listing ({relist_count}x): oferta była już wcześniej wycofywana z portalu "
                f"(łącznie {days_on_market} dni na rynku)."
            )

    if days_on_market >= 45 and relist_count == 0:
        arguments.append(f"Oferta znajduje się na rynku od {days_on_market} dni bez sprzedaży (presja czasowa).")

    if price_drop_amount > 0:
        arguments.append(
            f"Cena została już obniżona o {price_drop_amount:,.0f} zł (-{price_drop_pct:.1f}%), co świadczy o gotowości sprzedającego do ustępstw."
        )

    if price_deviation_adjusted_pct is not None and price_deviation_adjusted_pct >= 5.0 and market_median_m2:
        arguments.append(
            f"Cena ofertowa ({price_per_m2:,.0f} zł/m²) przewyższa lokalną medianę ({market_median_m2:,.0f} zł/m²) "
            f"o {price_deviation_adjusted_pct:+.1f}% po korekcie o stan wykończenia."
        )

    if any(f in finish_cond for f in ("deweloperski", "do_wykonczenia")):
        arguments.append(
            "Stan do wykończenia / deweloperski wymaga wniesienia natychmiastowego wkładu min. 1000–2500 zł/m² na wykończenie wnętrz."
        )
    elif any(f in finish_cond for f in ("remont", "surowy")):
        arguments.append("Stan surowy lub do remontu wymaga znacznego budżetu i rezerw na prace budowlane.")

    if any(r in road_type for r in ("nieutwardzona", "polna", "gruntowa")):
        arguments.append("Dojazd drogą nieutwardzoną generuje konieczność własnych nakładów na nawierzchnię.")

    if any(s in sewer_type for s in ("szambo", "brak")):
        arguments.append(
            "Brak kanalizacji miejskiej (zbiornik bezodpływowy / szambo oznacza wyższy koszt eksploatacji)."
        )

    if "POWODZ" in flood_zone:
        arguments.append("Lokalizacja w strefie zagrożenia powodziowego ISOK (wyższa składka ubezpieczenia i ryzyko).")

    # Tier 1 Spatial arguments
    if "OSUWISKO" in landslide_risk or "ZAGROŻENIE" in landslide_risk:
        arguments.append("Zidentyfikowano aktywne osuwisko lub strefę zagrożenia ruchami masowymi (PIG-PIB SOPO).")

    if (noise_level and float(noise_level) > 65.0) or "WYSOKI" in noise_zone:
        db_txt = f"{float(noise_level):.0f} dB" if noise_level else ">65 dB"
        arguments.append(f"Podwyższony poziom hałasu komunikacyjnego ({db_txt} Lden, sąsiedztwo trasy tranzytowej).")

    if monument_zone:
        arguments.append(
            f"Nieruchomość objęta ochroną konserwatorską ({monument_zone}) — wyższe koszty remontu i rygory prawne."
        )

    if cemetery_buffer == "<50m":
        arguments.append("Działka w bezpośredniej strefie sanitarnej cmentarza (<50m, zakaz rozbudowy i ujęć wody).")
    elif cemetery_buffer == "50-150m":
        arguments.append("Działka w strefie ochronnej cmentarza (50–150m, ograniczenia sanitarne).")

    if egib_status == "BRAK_W_EWIDENCJI":
        arguments.append(
            "Budynek nieujawniony w ewidencji gruntów i budynków EGiB (ryzyko formalnoprawne / brak odbioru)."
        )

    if egib_soil and re.search(r"\b(?:R|Ł|Ps|S)(?:I{1,3}[ab]?)\b", egib_soil, re.IGNORECASE):
        arguments.append(
            f"Grunt chroniony w ewidencji EGiB ({egib_soil}) — ustawowa ochrona rolna klas I-III utrudnia odrolnienie."
        )

    if front_width is not None and float(front_width) < 16.0:
        arguments.append(
            f"Wąska działka (front {float(front_width):.1f} m < 16 m) rygorystycznie ogranicza zabudowę i obniża płynność odsprzedaży."
        )

    if slope_pct is not None and float(slope_pct) > 8.0:
        arguments.append(
            f"Znaczne nachylenie terenu ({float(slope_pct):.1f}%) generuje konieczność wykonania kosztownych prac ziemnych i murów oporowych."
        )

    if any(k in power_risk for k in ("LINIA", "400KV", "220KV", "110KV", "WN")):
        arguments.append("Bezpośrednie sąsiedztwo napowietrznej linii wysokiego napięcia stanowi istotną wadę rynkową.")

    if broadband in ("BRAK", "BRAK_ZASIĘGU"):
        arguments.append(
            "Brak stacjonarnego dostępu do internetu szerokopasmowego (światłowodu) utrudnia pracę zdalną."
        )

    if "srodkowy" in subtype or "środkowy" in subtype:
        arguments.append("Segment środkowy szeregowca (brak bezpośredniego dostępu do ogrodu od frontu).")

    if filter_result and filter_result.cons:
        for c in filter_result.cons:
            if "Ukryty koszt" in c or "Ryzyko prawne" in c:
                clean_c = c.replace("⚠️", "").replace("⚖️", "").strip()
                if clean_c not in arguments:
                    arguments.append(clean_c)

    return NegotiationAdvice(
        market_median_m2=market_median_m2,
        price_deviation_pct=price_deviation_pct,
        price_deviation_adjusted_pct=price_deviation_adjusted_pct,
        days_on_market=days_on_market,
        negotiation_leverage=negotiation_leverage,
        fair_market_value=fair_market_value,
        suggested_opening_offer=suggested_opening,
        arguments=arguments[:6],
    )


TERYT_VOIVODESHIPS = {
    "02": "dolnośląskie",
    "04": "kujawsko-pomorskie",
    "06": "lubelskie",
    "08": "lubuskie",
    "10": "łódzkie",
    "12": "małopolskie",
    "14": "mazowieckie",
    "16": "opolskie",
    "18": "podkarpackie",
    "20": "podlaskie",
    "22": "pomorskie",
    "24": "śląskie",
    "26": "świętokrzyskie",
    "28": "warmińsko-mazurskie",
    "30": "wielkopolskie",
    "32": "zachodniopomorskie",
}


def calculate_notary_and_court_fee(price: float) -> float:
    """
    Computes statutory notary maximum fee under Polish law (Rozporządzenie MS)
    plus 23% VAT plus standard court register entry fees (400 PLN).
    """
    if price <= 0:
        return 0.0
    if price <= 3000:
        base = 100.0
    elif price <= 10000:
        base = 100.0 + (price - 3000.0) * 0.03
    elif price <= 30000:
        base = 310.0 + (price - 10000.0) * 0.02
    elif price <= 60000:
        base = 710.0 + (price - 30000.0) * 0.01
    elif price <= 1000000:
        base = 1010.0 + (price - 60000.0) * 0.004
    elif price <= 2000000:
        base = 4770.0 + (price - 1000000.0) * 0.002
    else:
        base = 6770.0 + (price - 2000000.0) * 0.00125
    return float(round(base * 1.23 + 400.0))


PKA_STATIONS = [
    ("Rzeszów Główny", 50.0435, 22.0083),
    ("Rzeszów Zachodni", 50.0440, 21.9860),
    ("Rzeszów Staromieście", 50.0635, 22.0125),
    ("Rzeszów Pobitno", 50.0380, 22.0310),
    ("Rzeszów Załęże", 50.0520, 22.0450),
    ("Rzeszów Zwięczyca", 49.9920, 21.9560),
    ("Trzebownisko / Jasionka", 50.0980, 22.0350),
    ("Głogów Małopolski", 50.1510, 21.9610),
    ("Boguchwała", 49.9810, 21.9400),
    ("Strażów", 50.0580, 22.1150),
]

EXPRESSWAY_HUBS = [
    ("Węzeł Rzeszów Północ (A4 / S19)", 50.1039, 22.0125),
    ("Węzeł Rzeszów Wschód (A4 / S19)", 50.0886, 22.0839),
    ("Węzeł Rzeszów Zachód (A4 / S19)", 50.0911, 21.9056),
    ("Węzeł Rzeszów Południe (S19)", 50.0053, 21.9328),
    ("Węzeł Świlcza (S19 / DK94)", 50.0631, 21.9167),
    ("Węzeł Jasionka (S19)", 50.1172, 22.0558),
    ("Węzeł Łańcut (A4)", 50.0906, 22.2472),
]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(radius_km * c, 2)


def _resolve_capex_settings() -> dict[str, Any]:
    """Reads CAPEX assumptions from global SearchConfig, falling back to hardcoded defaults."""
    defaults: dict[str, Any] = {
        "developer_rate": 1800.0,
        "renovation_rate": 2200.0,
        "agency_fee_pct": 2.0,
        "pcc_exempt_first_home": False,
    }
    try:
        from src.services.config_manager import config_manager

        cap = config_manager.get_config().capex
        return {
            "developer_rate": float(cap.developer_rate),
            "renovation_rate": float(cap.renovation_rate),
            "agency_fee_pct": float(cap.agency_fee_pct),
            "pcc_exempt_first_home": bool(cap.pcc_exempt_first_home),
        }
    except Exception:
        return defaults


def calculate_tco_audit(
    listing: Any,
    market_median_m2: float | None = None,
) -> dict[str, Any]:
    _ = market_median_m2
    capex_cfg = _resolve_capex_settings()
    developer_rate = float(capex_cfg["developer_rate"])
    renovation_rate = float(capex_cfg["renovation_rate"])
    agency_fee_pct = float(capex_cfg["agency_fee_pct"])
    pcc_exempt = bool(capex_cfg["pcc_exempt_first_home"])

    price = float(_prop(listing, "price", 0.0) or 0.0)
    area = float(_prop(listing, "area_home", 0.0) or _prop(listing, "area_plot", 0.0) or 100.0)
    market = str(_prop(listing, "market", "") or "").lower()
    finish = str(_prop(listing, "finish_condition", "") or "").lower()
    clean_finish = finish.replace("_", " ")
    is_private = _prop(listing, "is_private_owner", None)
    sewerage = str(_prop(listing, "sewerage", "") or "").lower().replace("_", " ")
    road = str(_prop(listing, "access_road_type", "") or "").lower().replace("_", " ")

    # 1. Finishing cost estimation (rates configurable in Settings → Koszty CAPEX)
    if "deweloperski" in clean_finish or "do wykończenia" in clean_finish:
        rate = developer_rate
        finishing_cost = round(area * rate)
        finish_label = f"Stan deweloperski — adaptacja i wykończenie ({area:.0f} m² × {rate:,.0f} zł/m²)"
    elif "do remontu" in clean_finish:
        rate = renovation_rate
        finishing_cost = round(area * rate)
        finish_label = f"Do remontu generalnego — instalacje i wykończenie ({area:.0f} m² × {rate:,.0f} zł/m²)"
    elif "do zamieszkania" in clean_finish:
        rate = 0
        finishing_cost = 0
        finish_label = "Stan do zamieszkania — gotowy, brak nakładów na start"
    else:
        rate = 800
        finishing_cost = round(area * rate)
        finish_label = f"Stan nieokreślony — bufor ostrożnościowy na odświeżenie ({area:.0f} m² × 800 zł/m²)"

    # 2. PCC Tax (2% on secondary market, 0% on primary developer market or first-home exemption)
    if "pierwotny" in market or ("deweloper" in market):
        pcc_tax = 0.0
        pcc_label = "Rynek pierwotny — faktura VAT (0% PCC)"
    elif pcc_exempt:
        pcc_tax = 0.0
        pcc_label = "Zwolnienie z PCC (zakup pierwszego mieszkania/domu) — 0% PCC"
    else:
        pcc_tax = round(price * 0.02)
        pcc_label = "Rynek wtórny — podatek od czynności cywilnoprawnych (2% PCC)"

    # 3. Notary fee
    notary_fee = calculate_notary_and_court_fee(price)
    notary_label = "Taksa notarialna (MS) + 23% VAT + opłaty sądowe (KW, wpis hipoteki)"

    # 4. Agency fee (configurable % in Settings → Koszty CAPEX)
    if is_private is True:
        agency_fee = 0.0
        agency_label = "Oferta bezpośrednia od właściciela (0% prowizji agencji)"
    elif agency_fee_pct <= 0:
        agency_fee = 0.0
        agency_label = "Prowizja agencji wyłączona w ustawieniach CAPEX (0%)"
    else:
        agency_fee = round(price * agency_fee_pct / 100.0)
        agency_label = f"Pośrednik / Agencja — szacowana prowizja kupującego (~{agency_fee_pct:g}% brutto)"

    # 5. Infrastructure extra cost (sewerage/road)
    infra_cost = 0.0
    infra_details: list[str] = []
    if any(s in sewerage for s in ("szambo", "brak")):
        infra_cost += 18000.0
        infra_details.append(
            "Brak kanalizacji: montaż przydomowej oczyszczalni ścieków (~18 000 zł) lub roczny koszt szamba (~4 800 zł/rok)"
        )
    if any(r in road for r in ("nieutwardzona", "gruntowa", "polna")):
        infra_cost += 12000.0
        infra_details.append("Dojazd drogą gruntową: partycypacja w utwardzeniu / podbudowie (~12 000 zł)")

    total_cost = round(price + finishing_cost + pcc_tax + notary_fee + agency_fee + infra_cost)
    hidden_costs = total_cost - price
    hidden_pct = round((hidden_costs / price * 100), 1) if price > 0 else 0.0

    if hidden_pct >= 25.0:
        severity = "danger"
        verdict = f"WYSOKIE KOSZTY WEJŚCIA (+{hidden_costs:,.0f} zł, +{hidden_pct:.1f}%)"
    elif hidden_pct >= 10.0:
        severity = "warning"
        verdict = f"UMIARKOWANE NAKŁADY DODATKOWE (+{hidden_costs:,.0f} zł, +{hidden_pct:.1f}%)"
    else:
        severity = "success"
        verdict = f"NISKIE KOSZTY STARTOWE (+{hidden_costs:,.0f} zł, +{hidden_pct:.1f}%)"

    breakdown = [
        {
            "item": "Cena ofertowa nieruchomości",
            "amount": price,
            "desc": "Cena wywoławcza w ogłoszeniu",
            "is_base": True,
        },
        {"item": "Wykończenie / Adaptacja wnętrz", "amount": float(finishing_cost), "desc": finish_label},
        {"item": "Podatek PCC (2%)", "amount": float(pcc_tax), "desc": pcc_label},
        {"item": "Taksa notarialna i sądowa", "amount": float(notary_fee), "desc": notary_label},
        {"item": "Prowizja biura nieruchomości", "amount": float(agency_fee), "desc": agency_label},
    ]
    if infra_cost > 0:
        breakdown.append(
            {
                "item": "Infrastruktura (Kanalizacja / Droga)",
                "amount": float(infra_cost),
                "desc": "; ".join(infra_details),
            }
        )

    return {
        "purchase_price": price,
        "total_acquisition_cost": total_cost,
        "hidden_costs_total": hidden_costs,
        "hidden_costs_pct": hidden_pct,
        "finishing_cost": float(finishing_cost),
        "transaction_costs": float(hidden_costs - finishing_cost),
        "verdict": verdict,
        "severity": severity,
        "breakdown": breakdown,
    }


def calculate_commute_audit(listing: Any) -> dict[str, Any]:
    lat = _prop(listing, "latitude", None)
    lon = _prop(listing, "longitude", None)
    city = str(_prop(listing, "city", "") or "").strip()
    district = str(_prop(listing, "district", "") or "").strip()

    if lat is None or lon is None or (float(lat) == 0.0 and float(lon) == 0.0):
        loc = f"{city} ({district})" if city and district else (city or "Obszar poszukiwań")
        return {
            "has_coords": False,
            "verdict": "LOKALIZACJA PRZYBLIŻONA",
            "severity": "info",
            "desc": f"Brak precyzyjnych współrzędnych GPS. Lokalizacja ogólna: {loc}.",
            "dist_center_km": None,
            "commute_time_min": None,
            "nearest_pka": None,
            "nearest_expressway": None,
            "findings": [
                {
                    "badge": "📍 Przybliżony Adres",
                    "title": f"Lokalizacja: {loc}",
                    "desc": "Dokładne odległości do centrum i węzłów komunikacyjnych zostaną wyliczone po podaniu ulicy lub numeru działki.",
                    "severity": "info",
                }
            ],
        }

    flat = float(lat)
    flon = float(lon)

    # 1. Resolve Target City Center (never fabricate a centroid for an unknown city)
    from src.services.config_manager import CITY_CENTROIDS, slugify_city

    city_slug = slugify_city(city) if city else ""
    city_center_coords: tuple[float, float] | None = None
    city_display: str | None = None
    if city_slug in CITY_CENTROIDS:
        city_center_coords = CITY_CENTROIDS[city_slug]
        city_display = city
    else:
        combined_slug = slugify_city(f"{city} {district} {_prop(listing, 'location_raw', '')}")
        for c_slug, coords in CITY_CENTROIDS.items():
            if re.search(rf"\b{c_slug}\b", combined_slug):
                city_center_coords = coords
                city_display = c_slug.capitalize()
                break

    # If still not resolved, check target city from user search profile (e.g. suburban listing in search radius)
    if city_center_coords is None:
        try:
            from src.services.config_manager import config_manager

            cfg = config_manager.get_config()
            profile_name = _prop(listing, "profile_name", None)
            target_city = None
            if profile_name and cfg.profiles:
                for p in cfg.profiles:
                    if p.name == profile_name and p.city:
                        target_city = p.city
                        break
            if not target_city and cfg.city:
                target_city = cfg.city
            if target_city:
                t_slug = slugify_city(target_city)
                if t_slug in CITY_CENTROIDS:
                    c_cand = CITY_CENTROIDS[t_slug]
                    # Only anchor if within realistic metropolitan commuting range (50 km)
                    if haversine_km(flat, flon, c_cand[0], c_cand[1]) <= 50.0:
                        city_center_coords = c_cand
                        city_display = target_city
        except Exception:
            pass

    dist_center: float | None = None
    commute_min: int | None = None
    osrm_km = _prop(listing, "commute_drive_km", None)
    osrm_min = _prop(listing, "commute_drive_min", None)
    if osrm_km is not None and osrm_min is not None:
        dist_center = float(osrm_km)
        commute_min = int(osrm_min)
    elif city_center_coords is not None:
        dist_center = haversine_km(flat, flon, city_center_coords[0], city_center_coords[1])
        commute_min = max(5, round(dist_center * 1.5 + 4))

    nearest_pka_name: str | None = None
    nearest_pka_dist: float | None = None
    nearest_hub_name: str | None = None
    nearest_hub_dist: float | None = None

    # Check rail proximity
    pka_distances = [(name, haversine_km(flat, flon, plat, plon)) for name, plat, plon in PKA_STATIONS]
    pka_distances.sort(key=lambda x: x[1])
    if pka_distances[0][1] <= 15.0:
        nearest_pka_name, nearest_pka_dist = pka_distances[0]

    # Check walkability_pka from spatial audit if closer or if PKA_STATIONS had no match
    pka_m = _prop(listing, "walkability_pka_dist_m", None)
    if pka_m is not None:
        dist_km = round(float(pka_m) / 1000.0, 1)
        if nearest_pka_dist is None or dist_km < nearest_pka_dist:
            nearest_pka_name = str(_prop(listing, "walkability_pka_name", "") or "Stacja kolejowa")
            nearest_pka_dist = dist_km

    # Check expressway proximity
    hub_distances = [(name, haversine_km(flat, flon, hlat, hlon)) for name, hlat, hlon in EXPRESSWAY_HUBS]
    hub_distances.sort(key=lambda x: x[1])
    if hub_distances[0][1] <= 25.0:
        nearest_hub_name, nearest_hub_dist = hub_distances[0]

    findings: list[dict[str, str]] = []

    # Precision indicator finding
    is_exact = _prop(listing, "is_exact_coords", None)
    if is_exact:
        findings.append(
            {
                "badge": "📍 Precyzyjna Lokalizacja",
                "title": f"Punkt adresowy / działka ({flat:.4f}, {flon:.4f})",
                "desc": "Współrzędne potwierdzone w ewidencji gruntów (EGiB / Geoportal). Odległości i czasy dojazdu wyliczone precyzyjnie.",
                "severity": "success",
            }
        )
    elif lat is not None and lon is not None:
        findings.append(
            {
                "badge": "📍 Przybliżony Rejon",
                "title": "Lokalizacja orientacyjna (dzielnica/rejon)",
                "desc": "Ogłoszenie zawiera przybliżony rejon bez numeru domu/działki. Odległości orientacyjne.",
                "severity": "info",
            }
        )

    # Center
    if dist_center is None:
        findings.append(
            {
                "badge": "📍 Nieznane miasto",
                "title": "Brak punktu odniesienia dojazdu",
                "desc": "Nie rozpoznano miasta oferty — nie wyliczono odległości do centrum, aby nie wprowadzać w błąd.",
                "severity": "info",
            }
        )
    else:
        center_label = f"Centrum ({city_display})"
        if dist_center <= 5.0:
            findings.append(
                {
                    "badge": "🏙️ Blisko Centrum",
                    "title": f"{center_label}: {dist_center:.1f} km (~{commute_min} min)",
                    "desc": "Doskonały czas dojazdu do śródmieścia, szkół i punktów usługowych bez konieczności długich dojazdów.",
                    "severity": "success",
                }
            )
        elif dist_center <= 12.0:
            findings.append(
                {
                    "badge": "🚗 Strefa Podmiejska",
                    "title": f"{center_label}: {dist_center:.1f} km (~{commute_min} min)",
                    "desc": "Standardowy czas dojazdu w aglomeracji miejskiej. Dogodne połączenie drogowe.",
                    "severity": "info",
                }
            )
        else:
            findings.append(
                {
                    "badge": "⏱️ Dłuższy Dojazd",
                    "title": f"{center_label}: {dist_center:.1f} km (~{commute_min} min)",
                    "desc": "Lokalizacja poza bezpośrednią aglomeracją miejską, wymagająca codziennego dłuższego dojazdu samochodem.",
                    "severity": "warning",
                }
            )

    # Rail / Aglomeracja
    if nearest_pka_dist is not None and nearest_pka_name is not None:
        dest_center = f" do centrum ({city_display})" if city_display else " do centrum"
        if nearest_pka_dist <= 1.5:
            findings.append(
                {
                    "badge": "🚆 Kolej Aglomeracyjna < 1.5 km",
                    "title": f"Stacja: {nearest_pka_name} ({nearest_pka_dist:.1f} km)",
                    "desc": f"Dojście pieszo lub rowerem do stacji! Szybki transport{dest_center} w 10–15 min bez stania w korkach. Kluczowy atut podnoszący wartość nieruchomości.",
                    "severity": "success",
                }
            )
        elif nearest_pka_dist <= 3.5:
            findings.append(
                {
                    "badge": "🚆 Stacja Kolejowa (Park & Ride)",
                    "title": f"Stacja: {nearest_pka_name} ({nearest_pka_dist:.1f} km)",
                    "desc": "Dojazd autem 3–5 min do stacji kolejowej. Wygodna opcja codziennego dojazdu pociągiem aglomeracyjnym.",
                    "severity": "info",
                }
            )
        else:
            findings.append(
                {
                    "badge": "🚌 Brak Bliskiej Kolei",
                    "title": f"Najbliższa stacja: {nearest_pka_name} ({nearest_pka_dist:.1f} km)",
                    "desc": "Brak bezpośredniego pieszego dostępu do stacji kolejowej. Komunikacja oparta głównie na transporcie kołowym.",
                    "severity": "info",
                }
            )

    # Expressway
    if nearest_hub_dist is not None and nearest_hub_name is not None:
        if nearest_hub_dist < 0.45:
            findings.append(
                {
                    "badge": "⚠️ Bliskość Węzła Szybkich Dróg (<450m)",
                    "title": f"{nearest_hub_name} ({nearest_hub_dist * 1000:.0f} m)",
                    "desc": "Bardzo bliskie sąsiedztwo trasy szybkiego ruchu. Ryzyko uciążliwego hałasu komunikacyjnego i spalin.",
                    "severity": "warning",
                }
            )
        elif nearest_hub_dist <= 5.0:
            findings.append(
                {
                    "badge": "🛣️ Wygodny Wylot na Trasę Szybkiego Ruchu",
                    "title": f"{nearest_hub_name} ({nearest_hub_dist:.1f} km)",
                    "desc": "Szybki wjazd na obwodnicę i trasę szybkiego ruchu w kilka minut bez wjeżdżania do zatłoczonego centrum.",
                    "severity": "success",
                }
            )

    if dist_center is None:
        commute_verdict = "BRAK PUNKTU ODNIESIENIA DOJAZDU"
        commute_sev = "info"
    elif nearest_pka_dist is not None:
        if dist_center <= 6.0 and nearest_pka_dist <= 2.0:
            commute_verdict = "WYBITNA KOMUNIKACJA I DOSTĘPNOŚĆ"
            commute_sev = "success"
        elif dist_center <= 14.0 or nearest_pka_dist <= 2.5:
            commute_verdict = "DOBRA KOMUNIKACJA AGLOMERACYJNA"
            commute_sev = "info"
        else:
            commute_verdict = "LOKALIZACJA WYMAGAJĄCA SAMOCHODU"
            commute_sev = "warning"
    else:
        if dist_center <= 6.0:
            commute_verdict = "WYBITNA KOMUNIKACJA I DOSTĘPNOŚĆ"
            commute_sev = "success"
        elif dist_center <= 15.0:
            commute_verdict = "DOBRA KOMUNIKACJA AGLOMERACYJNA"
            commute_sev = "info"
        else:
            commute_verdict = "LOKALIZACJA WYMAGAJĄCA SAMOCHODU"
            commute_sev = "warning"

    return {
        "has_coords": True,
        "dist_center_km": dist_center,
        "commute_time_min": commute_min,
        "nearest_pka": {"name": nearest_pka_name, "distance_km": nearest_pka_dist} if nearest_pka_name else None,
        "nearest_expressway": {"name": nearest_hub_name, "distance_km": nearest_hub_dist} if nearest_hub_name else None,
        "verdict": commute_verdict,
        "severity": commute_sev,
        "findings": findings,
    }


def calculate_risk_shield(listing: Any) -> dict[str, Any]:
    mpzp_status = str(_prop(listing, "mpzp_status", "") or "").upper()
    mpzp_zone = str(_prop(listing, "mpzp_zone", "") or "")
    flood_zone = str(_prop(listing, "flood_risk_zone", "") or "").upper()
    cons = _prop(listing, "cons", []) or []
    cadastral_area = _prop(listing, "cadastral_area", None)
    area_plot = _prop(listing, "area_plot", None)

    findings: list[dict[str, str]] = []

    # 1. MPZP
    if mpzp_status == "OBOWIĄZUJĄCY" and mpzp_zone:
        findings.append(
            {
                "badge": "🛡️ Ochrona Planistyczna MPZP",
                "title": f"Plan Miejscowy Obowiązujący (Strefa: {mpzp_zone})",
                "desc": "Teren objęty uchwalonym MPZP. Gwarancja stabilności otoczenia — sąsiad nie wybuduje obiektu sprzecznego z przeznaczeniem w planie.",
                "severity": "success",
            }
        )
    elif mpzp_status == "NIEZNANY" and not mpzp_zone:
        findings.append(
            {
                "badge": "❓ Status Planistyczny Nieustalony",
                "title": "Nie udało się pobrać danych MPZP z Geoportalu",
                "desc": "Serwis planistyczny nie zwrócił jednoznacznych danych dla tej lokalizacji. Zweryfikuj plan ręcznie w Geoportalu Krajowym lub w urzędzie gminy.",
                "severity": "info",
            }
        )
    else:
        findings.append(
            {
                "badge": "⚠️ Brak Planu Miejscowego (Ryzyko WZ)",
                "title": "Brak MPZP — Zagrożenie niekontrolowaną zabudową sąsiedzką",
                "desc": "Brak planu oznacza, że sąsiedzi mogą w każdej chwili wystąpić o Warunki Zabudowy (WZ) na uciążliwą inwestycję (np. gęste szeregowce, warsztat, myjnię, maszt GSM).",
                "severity": "warning",
            }
        )

    # 2. Flood Risk (ISOK)
    if "POWODZ" in flood_zone or "ZAGROŻENIE_POWODZIOWE" in flood_zone:
        findings.append(
            {
                "badge": "🚨 Strefa Zagrożenia Powodziowego (ISOK)",
                "title": "Wysokie ryzyko zalania wodami 100-letnimi",
                "desc": "Nieruchomość zlokalizowana w strefie zalewowej wyznaczonej przez Wody Polskie. Poważne trudności z uzyskaniem kredytu hipotecznego i drastycznie wyższe koszty ubezpieczenia.",
                "severity": "danger",
            }
        )
    else:
        findings.append(
            {
                "badge": "🌊 Teren Bezpieczny Hydrologicznie",
                "title": "Brak zagrożenia powodziowego (ISOK Hydroportal)",
                "desc": "Działka leży całkowicie poza strefami bezpośredniego i szczególnego zagrożenia powodziowego.",
                "severity": "success",
            }
        )

    # 3. Cadastral Area Discrepancy (Oferta vs EGiB)
    if cadastral_area and area_plot and float(area_plot) > 0:
        c_area = float(cadastral_area)
        o_area = float(area_plot)
        diff = abs(c_area - o_area)
        pct = diff / o_area
        if pct >= 0.05 and diff >= 15.0:
            sev = "danger" if pct >= 0.15 else "warning"
            findings.append(
                {
                    "badge": f"⚠️ Rozbieżność Powierzchni Działki ({diff:.0f} m²)",
                    "title": "Różnica między ogłoszeniem a państwowym katastrem (EGiB)",
                    "desc": f"W ogłoszeniu podano {o_area:.0f} m², a w oficjalnej ewidencji gruntów działka ma {c_area:.0f} m² (różnica: {diff:.0f} m², {pct * 100:.1f}%). Może to wynikać z wliczenia udziału w drodze wewnętrznej lub błędu pośrednika.",
                    "severity": sev,
                }
            )

    # 4. Industrial & Environmental neighborhood
    has_industrial_risk = any(
        ("Ba" in c or "Bi" in c or "przemysłow" in c.lower() or "kolej" in c.lower()) for c in cons
    )
    if has_industrial_risk:
        findings.append(
            {
                "badge": "🚨 Sąsiedztwo Przemysłowe / Ba / Bi",
                "title": "Wykryto tereny komercyjne lub uciążliwe w promieniu 120m",
                "desc": "W bezpośrednim sąsiedztwie zidentyfikowano działki o przeznaczeniu przemysłowym, składowym lub kolejowym.",
                "severity": "danger",
            }
        )

    # 5. Landslide Risk (SOPO PIG-PIB)
    landslide_risk = str(_prop(listing, "landslide_risk", "") or "").upper()
    if "OSUWISKO" in landslide_risk or "ZAGROŻENIE" in landslide_risk:
        findings.append(
            {
                "badge": "🚨 Zagrożenie Osuwiskowe (PIG-PIB SOPO)",
                "title": "Aktywne osuwisko lub strefa ruchów masowych",
                "desc": "Działka w strefie osuwiskowej zarejestrowanej w SOPO PIG-PIB. Ryzyko uszkodzenia fundamentów, odmowy ubezpieczenia oraz braku możliwości odsprzedaży.",
                "severity": "danger",
            }
        )

    # 6. EGiB Building Status & Protected Soil
    egib_status = str(_prop(listing, "egib_building_status", "") or "").upper()
    egib_soil = str(_prop(listing, "egib_soil_class", "") or "")
    if egib_status == "BRAK_W_EWIDENCJI":
        findings.append(
            {
                "badge": "⚠️ Dom Nieujawniony w EGiB",
                "title": "Brak obrysu budynku w ewidencji gruntów i budynków",
                "desc": "Budynek nie figuruje w państwowej kartotece budynków (brak użytku B). Ryzyko samowoli budowlanej, braku formalnego odbioru lub odmowy kredytu hipotecznego.",
                "severity": "danger",
            }
        )
    elif egib_status == "UJAWNIONY":
        findings.append(
            {
                "badge": "🏛️ Budynek Ujawniony w EGiB",
                "title": "Budynek formalnie zaewidencjonowany w katastrze",
                "desc": "Działka posiada ujawnioną zabudowę mieszkaniową (użytek B/Br) w rejestrze EGiB.",
                "severity": "success",
            }
        )

    if egib_soil and re.search(r"\b(?:R|Ł|Ps|S)(?:I{1,3}[ab]?)\b", egib_soil, re.IGNORECASE):
        findings.append(
            {
                "badge": f"🌾 Grunt Chroniony w EGiB ({egib_soil})",
                "title": "Ustawowa ochrona gruntów rolnych (klasy I-III)",
                "desc": f"Działka oznaczona klasą bonitacyjną {egib_soil}. Zmiana przeznaczenia lub rozbudowa wymaga kosztownej i sformalizowanej procedury wyłączenia z produkcji rolniczej.",
                "severity": "warning",
            }
        )

    # 7. Acoustic Noise (EHAŁAS / GIOŚ)
    noise_level = _prop(listing, "noise_level_db", None)
    noise_zone = str(_prop(listing, "noise_zone", "") or "").upper()
    if (noise_level and float(noise_level) > 65.0) or "WYSOKI" in noise_zone:
        db_disp = f"{float(noise_level):.0f} dB" if noise_level else ">65 dB"
        findings.append(
            {
                "badge": f"🔊 Podwyższony Hałas ({db_disp} Lden)",
                "title": "Przekroczenie norm uciążliwości akustycznej (EHAŁAS / GIOŚ)",
                "desc": f"Nieruchomość w strefie podwyższonego hałasu komunikacyjnego ({db_disp} Lden). Obniżony komfort życia i konieczność inwestycji w okna dźwiękoszczelne.",
                "severity": "danger",
            }
        )

    # 8. GDOŚ Nature Protection
    nature_zone = _prop(listing, "nature_protected_zone", None)
    if nature_zone:
        findings.append(
            {
                "badge": f"🌿 Obszar Chroniony GDOŚ ({nature_zone})",
                "title": "Lokalizacja w strefie ochrony przyrody (Natura 2000 / Park Krajobrazowy)",
                "desc": f"Działka objęta reżimem ochrony środowiskowej ({nature_zone}). Możliwe obostrzenia dotyczące wycinki drzew, instalacji i uciążliwości inwestycji.",
                "severity": "warning",
            }
        )

    # 9. NID Monuments & Conservator
    monument_zone = _prop(listing, "monument_zone", None)
    if monument_zone:
        findings.append(
            {
                "badge": f"🏛️ Zabytek / Strefa Konserwatorska ({monument_zone})",
                "title": "Wpis do rejestru zabytków lub strefa ochrony WKZ (NID)",
                "desc": f"Obiekt lub działka podlegają nadzorowi Wojewódzkiego Konserwatora Zabytków ({monument_zone}). Każdy remont, wymiana stolarki czy termomodernizacja wymaga zgody WKZ.",
                "severity": "danger",
            }
        )

    # 10. Cemetery Buffer Zone
    cemetery_zone = str(_prop(listing, "cemetery_buffer_zone", "") or "")
    if cemetery_zone == "<50m":
        findings.append(
            {
                "badge": "🚨 Strefa Sanitarna Cmentarza (<50m)",
                "title": "Bezpośrednie sąsiedztwo cmentarza (Rozp. Ministra Zdrowia)",
                "desc": "W odległości poniżej 50m od granic cmentarza obowiązuje ustawowy zakaz wznoszenia budynków mieszkalnych oraz ograniczenia lokalizacji okien.",
                "severity": "danger",
            }
        )
    elif cemetery_zone == "50-150m":
        findings.append(
            {
                "badge": "⚠️ Strefa Ochronna Cmentarza (50-150m)",
                "title": "Odległość 50–150m od terenu cmentarza",
                "desc": "Nieruchomość w strefie ograniczeń ujęć wody i rygorów sanitarnych. Wymagane obowiązkowe podłączenie do sieci wodociągowej (zakaz studni pitnych).",
                "severity": "warning",
            }
        )

    # 11. Broadband Internet (SIDUSIS / internet.gov.pl)
    broadband_status = str(_prop(listing, "broadband_status", "") or "").upper()
    if broadband_status == "ŚWIATŁOWÓD_AKTYWNY":
        findings.append(
            {
                "badge": "🌐 Światłowód FTTH (internet.gov.pl)",
                "title": "Potwierdzony zasięg stacjonarnego internetu światłowodowego",
                "desc": "Budynek w oficjalnym zasięgu sieci światłowodowej zarejestrowanej w SIDUSIS. Idealne warunki do pracy zdalnej.",
                "severity": "success",
            }
        )
    elif broadband_status in ("PLANOWANY_KPO", "PLANOWANY_KPO_FERC"):
        findings.append(
            {
                "badge": "📡 Planowany Światłowód (KPO / FERC)",
                "title": "Adres objęty dofinansowaniem budowy sieci szerokopasmowej",
                "desc": "Nieruchomość znajduje się w planie inwestycyjnym KPO/FERC z gwarancją doprowadzenia łącza światłowodowego.",
                "severity": "info",
            }
        )
    elif broadband_status in ("BRAK", "BRAK_ZASIĘGU"):
        findings.append(
            {
                "badge": "⚠️ Brak Światłowodu (SIDUSIS)",
                "title": "Brak stacjonarnego internetu szerokopasmowego w rejestrze państwowym",
                "desc": "Konieczność korzystania z internetu mobilnego LTE/5G lub instalacji satelitarnej (Starlink).",
                "severity": "warning",
            }
        )

    # 12. Parcel Geometry & Front Width
    front_width = _prop(listing, "parcel_front_width_m", None)
    aspect_ratio = _prop(listing, "parcel_aspect_ratio", None)
    shape_type = str(_prop(listing, "parcel_shape_type", "") or "")
    if front_width is not None and float(front_width) < 16.0:
        max_b_width = max(4.0, float(front_width) - 8.0)
        findings.append(
            {
                "badge": f"📐 Wąski Front Działki ({float(front_width):.1f} m)",
                "title": "Szerokość działki poniżej 16 metrów (tzw. kiszka / sznurówka)",
                "desc": f"Zgodnie z Prawem Budowlanym (min. 3m/4m od granicy) wąska parcela drastycznie ogranicza szerokość budynku do max {max_b_width:.1f} m.",
                "severity": "danger",
            }
        )
    elif shape_type == "REGULARNY" and front_width is not None and float(front_width) >= 18.0:
        findings.append(
            {
                "badge": f"📐 Foremna Działka (front {float(front_width):.0f} m)",
                "title": "Ustawna parcela o regularnych proporcjach",
                "desc": f"Szerokość frontu {float(front_width):.0f} m (proporcje 1:{float(aspect_ratio or 1.0):.1f}) umożliwia swobodny wybór projektu domu i zagospodarowanie ogrodu.",
                "severity": "success",
            }
        )

    # 13. Terrain Slope & Aspect (NMT GUGiK)
    slope_pct = _prop(listing, "terrain_slope_pct", None)
    aspect_dir = str(_prop(listing, "terrain_aspect", "") or "")
    if slope_pct is not None and float(slope_pct) > 8.0:
        findings.append(
            {
                "badge": f"⛰️ Strome Nachylenie Terenu (spadek {float(slope_pct):.1f}%)",
                "title": f"Znaczne nachylenie stoku ({aspect_dir or 'brak danych'})",
                "desc": f"Nachylenie terenu {float(slope_pct):.1f}% wiąże się z ryzykiem spływu wód opadowych, koniecznością budowy kosztownych murów oporowych i utrudnionym podjazdem zimą.",
                "severity": "danger",
            }
        )
    elif (
        aspect_dir in ("POŁUDNIOWY", "POŁUDNIOWO-ZACHODNI", "POŁUDNIOWO-WSCHODNI")
        and slope_pct is not None
        and float(slope_pct) >= 2.0
    ):
        findings.append(
            {
                "badge": "☀️ Południowa Ekspozycja Stoku (NMT)",
                "title": f"Stok o nachyleniu {float(slope_pct):.1f}% skierowany na {aspect_dir}",
                "desc": "Doskonałe warunki nasłonecznienia, optymalna efektywność paneli fotowoltaicznych i naturalne dogrzewanie budynku zimą.",
                "severity": "success",
            }
        )
    elif slope_pct is not None and float(slope_pct) <= 3.0:
        findings.append(
            {
                "badge": "🟢 Płaski Teren (NMT GUGiK)",
                "title": f"Bezpieczny, płaski teren (nachylenie {float(slope_pct):.1f}%)",
                "desc": "Brak konieczności skomplikowanych prac ziemnych i niwelacji. Optymalne warunki posadowienia fundamentów.",
                "severity": "success",
            }
        )

    # 14. High Voltage Power Lines
    power_risk = str(_prop(listing, "power_lines_risk", "") or "")
    if any(k in power_risk.upper() for k in ("LINIA", "400KV", "220KV", "110KV", "WN")):
        findings.append(
            {
                "badge": "⚡ Linia Wysokiego Napięcia (<150m)",
                "title": "Bezpośrednie sąsiedztwo napowietrznej linii przesyłowej WN",
                "desc": "Nieruchomość w strefie oddziaływania linii elektroenergetycznej wysokiego napięcia. Pas technologiczny, uciążliwość akustyczna i spadek płynności odsprzedaży.",
                "severity": "danger",
            }
        )
    elif power_risk == "BEZPIECZNIE":
        findings.append(
            {
                "badge": "⚡ Bezpieczna Odległość od Linii WN",
                "title": "Brak napowietrznych linii przesyłowych w buforze 200m",
                "desc": "W promieniu 200m nie zidentyfikowano magistralnych linii 110 kV, 220 kV ani 400 kV.",
                "severity": "success",
            }
        )

    # 15. Walkability & PKA Station Proximity
    pka_dist_m = _prop(listing, "walkability_pka_dist_m", None)
    pka_name = _prop(listing, "walkability_pka_name", None)
    if pka_dist_m is not None and int(pka_dist_m) <= 1500:
        walk_min = max(1, round(int(pka_dist_m) / 80))
        city_name = str(_prop(listing, "city", "") or "").strip()
        dest = f"z centrum ({city_name})" if city_name else "z centrum"
        findings.append(
            {
                "badge": f"🚆 Stacja Kolejowa w Zasięgu Spaceru ({pka_dist_m}m)",
                "title": f"Piesze dojście do stacji {pka_name or 'kolejowej'} (~{walk_min} min)",
                "desc": f"Znakomita dostępność komunikacyjna ({pka_dist_m} m pieszo). Szybkie połączenie szynobusowe / kolejowe {dest} bez stania w korkach.",
                "severity": "success",
            }
        )

    has_danger = any(f["severity"] == "danger" for f in findings)
    has_warn = any(f["severity"] == "warning" for f in findings)
    if has_danger:
        risk_verdict = "WYKRYTO POWAŻNE RYZYKO ŚRODOWISKOWE LUB PRAWNE"
        risk_sev = "danger"
    elif has_warn:
        risk_verdict = "WYMAGA UWAGI (OGRANICZENIA PLANISTYCZNE LUB ŚRODOWISKOWE)"
        risk_sev = "warning"
    else:
        risk_verdict = "TEREN BEZPIECZNY PLANISTYCZNIE I ŚRODOWISKOWO"
        risk_sev = "success"

    return {
        "verdict": risk_verdict,
        "severity": risk_sev,
        "findings": findings,
    }


GESUT_NETWORK_LABELS = {
    "woda": "Sieć wodociągowa",
    "kanalizacja": "Sieć kanalizacyjna",
    "gaz": "Sieć gazowa",
    "prad": "Sieć elektroenergetyczna",
    "cieplo": "Sieć ciepłownicza",
    "telekomunikacja": "Sieć telekomunikacyjna",
}

GESUT_ABSENT_GUIDANCE = {
    "woda": "Brak wodociągu w pobliżu — konieczna studnia wiercona (koszt ~15–25 tys. zł) lub dalsze przyłącze.",
    "kanalizacja": "Brak sieci kanalizacyjnej w zasięgu — wymagana przydomowa oczyszczalnia (~18 tys. zł) lub szambo.",
    "gaz": "Brak gazu sieciowego w zasięgu — ogrzewanie gazowe wymaga zbiornika LPG (dzierżawa lub zakup ~6–12 tys. zł).",
    "prad": "Brak sieci energetycznej wykrytej w promieniu — konieczny przyłącz (opłaty zależne od dystrybutora, często 3–15 tys. zł).",
    "cieplo": "Brak sieci ciepłowniczej w zasięgu (typowe poza centrum — ogrzewanie własne).",
    "telekomunikacja": "Brak sieci telekomunikacyjnej w zasięgu — sprawdź dostępność światłowodu u operatorów.",
}

GESUT_PRESENT_DESC = {
    "woda": "Wodociąg biegnie w bezpośrednim sąsiedztwie działki — przyłącze ~150–300 zł/mb.",
    "kanalizacja": "Kolektor kanalizacyjny w zasięgu działki — możliwe przyłącze do sieci miejskiej.",
    "gaz": "Gazociąg w zasięgu działki — możliwe podłączenie i ogrzewanie gazem ziemnym.",
    "prad": "Linia elektroenergetyczna w sąsiedztwie — niski koszt przyłącza.",
    "cieplo": "Sieć ciepłownicza w zasięgu (rzadkość poza centrum).",
    "telekomunikacja": "Kabel telekomunikacyjny w sąsiedztwie — dobre perspektywy na światłowód.",
}


def calculate_gesut_audit(listing: Any, gesut_networks: dict | None = None) -> dict[str, Any]:
    if gesut_networks is None:
        gesut_networks = _prop(listing, "gesut_networks_data", None) or _prop(listing, "gesut_networks", None)

    if isinstance(gesut_networks, dict) and gesut_networks.get("coverage"):
        return _calculate_gesut_from_real_data(listing, gesut_networks)
    return _calculate_gesut_descriptive(listing)


def _calculate_gesut_from_real_data(listing: Any, gesut_networks: dict) -> dict[str, Any]:
    networks: dict[str, bool] = gesut_networks.get("networks") or {}
    radius = float(gesut_networks.get("checked_radius_m") or 20.0)
    sources = gesut_networks.get("sources") or []

    sewerage = str(_prop(listing, "sewerage", "") or "").lower().replace("_", " ")
    heating = str(_prop(listing, "heating", "") or "").lower().replace("_", " ")
    has_fiber = bool(_prop(listing, "has_fiber", False))

    findings: list[dict[str, str]] = []
    for key, label in GESUT_NETWORK_LABELS.items():
        present = bool(networks.get(key))
        if present:
            findings.append(
                {
                    "badge": f"✅ {label} w zasięgu",
                    "title": f"{label} wykryta w promieniu {radius:.0f} m od działki",
                    "desc": GESUT_PRESENT_DESC[key],
                    "severity": "success",
                }
            )
            continue

        severity = "info"
        if key == "woda" and sewerage and "brak" not in sewerage:
            severity = "warning"
        if key == "kanalizacja" and any(s in sewerage for s in ("szambo", "brak")):
            severity = "warning"
        if key == "gaz" and "gazowe" in heating:
            severity = "warning"
        if key == "telekomunikacja" and has_fiber:
            severity = "warning"
        if key == "prad":
            severity = "warning"
        findings.append(
            {
                "badge": f"⚠️ Brak w zasięgu: {label}",
                "title": f"{label} niewykryta w promieniu {radius:.0f} m",
                "desc": GESUT_ABSENT_GUIDANCE[key],
                "severity": severity,
            }
        )

    source_txt = (
        f"Dane GESUT (powiatowe ewidencje WMS: {', '.join(sources)}) — pomiar w promieniu {radius:.0f} m. "
        "Detekcja na podstawie rysunku sieci na mapie ewidencyjnej; przyłącza wymagają potwierdzenia w starostwie."
    )
    findings.append(
        {
            "badge": "🗂️ Źródło danych",
            "title": "Rzeczywiste dane ewidencyjne GESUT",
            "desc": source_txt,
            "severity": "info",
        }
    )

    has_warn = any(f["severity"] == "warning" for f in findings)
    present_count = sum(1 for v in networks.values() if v)
    if has_warn:
        verdict = f"CZĘŚCIOWE UZBROJENIE ({present_count}/6 sieci w zasięgu)"
        severity = "warning"
    else:
        verdict = "KOMPLETNE UZBROJENIE TERENU (GESUT)"
        severity = "success"

    return {
        "verdict": verdict,
        "severity": severity,
        "findings": findings,
        "source": f"GESUT WMS ({', '.join(sources)}) — promień {radius:.0f} m",
        "data_driven": True,
    }


def _calculate_gesut_descriptive(listing: Any) -> dict[str, Any]:
    gesut_findings: list[dict[str, str]] = []
    sewerage = str(_prop(listing, "sewerage", "") or "").lower()
    road = str(_prop(listing, "access_road_type", "") or "").lower()
    heating = str(_prop(listing, "heating", "") or "").lower()
    has_fiber = bool(_prop(listing, "has_fiber", False))

    # A) Sewerage
    if any(s in sewerage for s in ("szambo", "brak")):
        gesut_findings.append(
            {
                "badge": "⚠️ Szambo / Brak Kanalizacji",
                "title": "Bieżące koszty asenizacyjne (~300–500 zł/mc)",
                "desc": "Brak podłączenia do sieci miejskiej. Konieczność wywozu ścieków co 2–3 tyg. Sprawdź na mapie GESUT (brązowa linia 'ks'), czy w drodze biegnie kolektor i jaki byłby koszt przyłącza (ok. 150–300 zł/mb).",
                "severity": "warning",
            }
        )
    elif "miejska" in sewerage:
        gesut_findings.append(
            {
                "badge": "✅ Sieć Kanalizacji Miejskiej",
                "title": "Pełen komfort sanitarny",
                "desc": "Nieruchomość włączona do sieci miejskiej. Brak konieczności zamawiania wywozu nieczystości i niższe koszty ścieków.",
                "severity": "success",
            }
        )
    elif "przydomowa" in sewerage:
        gesut_findings.append(
            {
                "badge": "🌱 Przydomowa Oczyszczalnia Ścieków",
                "title": "Niskie koszty bieżące",
                "desc": "Niski koszt utrzymania (~200 zł/rok). Weryfikuj na mapie GESUT odległość od ewentualnej studni i granic działki.",
                "severity": "success",
            }
        )
    else:
        gesut_findings.append(
            {
                "badge": "❓ Nieznany Status Kanalizacji",
                "title": "Brak deklaracji w ofercie",
                "desc": "Sprawdź w Geoportalu na warstwie GESUT obecność sieci sanitarnej w drodze lub zapytaj sprzedawcę o rodzaj odprowadzania ścieków.",
                "severity": "info",
            }
        )

    # B) Road access
    if any(r in road for r in ("nieutwardzona", "polna", "gruntowa")):
        gesut_findings.append(
            {
                "badge": "⚠️ Droga Nieutwardzona",
                "title": "Ryzyko braku uzbrojenia w pasie drogowym",
                "desc": "Dojazd drogą gruntową utrudnia doprowadzenie mediów i może wymagać własnych nakładów finansowych na przyłącza oraz utwardzenie nawierzchni.",
                "severity": "warning",
            }
        )
    elif any(r in road for r in ("asfaltowa", "kostka", "utwardzona")):
        gesut_findings.append(
            {
                "badge": "✅ Dojazd Utwardzony",
                "title": "Dostęp do infrastruktury drogowej",
                "desc": "Dojazd drogą o twardej nawierzchni. Główne sieci GESUT (woda, gaz, prąd) z reguły biegną w pasie drogowym.",
                "severity": "success",
            }
        )

    # C) Heating / Gas
    if "gazowe" in heating:
        gesut_findings.append(
            {
                "badge": "🔥 Ogrzewanie Gazowe",
                "title": "Weryfikacja sieci vs butla LPG",
                "desc": "Sprawdź na warstwie GESUT obecność gazociągu sieciowego (żółta linia 'g'). Jeśli w drodze brak gazu, ogrzewanie bazuje na zbiorniku naziemnym/podziemnym na działce.",
                "severity": "info",
            }
        )
    elif "pompa" in heating:
        gesut_findings.append(
            {
                "badge": "⚡ Pompa Ciepła",
                "title": "Zapotrzebowanie na moc przyłączeniową",
                "desc": "Wymaga stabilnego przyłącza elektroenergetycznego (GESUT / dystrybutor energii — rekomendowane min. 14–17 kW).",
                "severity": "info",
            }
        )

    # D) Fiber
    if has_fiber:
        gesut_findings.append(
            {
                "badge": "✅ Światłowód / Szerokopasmowy",
                "title": "Szybki internet na działce",
                "desc": "Obecność łącza światłowodowego (pomarańczowa linia 't' w GESUT). Istotna zaleta przy pracy zdalnej.",
                "severity": "success",
            }
        )
    else:
        gesut_findings.append(
            {
                "badge": "📶 Brak Potwierdzonego Światłowodu",
                "title": "Weryfikacja zasięgu telekomunikacyjnego",
                "desc": "Sprawdź w GESUT sieć telekomunikacyjną lub w rejestrze SIDUSIS (gov.pl) planowane inwestycje z dofinansowań unijnych.",
                "severity": "info",
            }
        )

    # E) Transit pipes / Technical collision guide
    gesut_findings.append(
        {
            "badge": "ℹ️ Przewodnik Kolizji w GESUT",
            "title": "Strefy ochronne wyłączające pas gruntu z zabudowy",
            "desc": "Na mapie Geoportalu sprawdź kolizje: linie elektroenergetyczne (czerwone 'e' — strefa ochronna 3–15m bez prawa zabudowy) oraz gazociągi podwyższonych ciśnień (żółte 'g' — strefa kontrolowana z zakazem budowy).",
            "severity": "info",
        }
    )

    has_danger_gesut = any(f["severity"] == "danger" for f in gesut_findings)
    has_warn_gesut = any(f["severity"] == "warning" for f in gesut_findings)
    if has_danger_gesut:
        gesut_verdict = "WYKRYTO ISTOTNE RYZYKA TECHNICZNE"
        gesut_severity = "danger"
    elif has_warn_gesut:
        gesut_verdict = "CZĘŚCIOWE UZBROJENIE / WYMAGA WERYFIKACJI"
        gesut_severity = "warning"
    else:
        gesut_verdict = "KOMPLETNE UZBROJENIE TERENU"
        gesut_severity = "success"

    return {
        "verdict": gesut_verdict,
        "severity": gesut_severity,
        "findings": gesut_findings,
        "source": "Brak danych GESUT — analiza na podstawie deklaracji z ogłoszenia",
        "data_driven": False,
    }


def analyze_land_and_utilities(
    listing: Any,
    market_median_m2: float | None = None,
    gesut_networks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Automated high-ROI intelligence synthesis (100% automated, zero manual lookups):
    1. TCO & True Acquisition Cost Calculator (finishing, PCC, notary, agency, infrastructure)
    2. Commute & Proximity Matrix (Center, PKA rail, S19/A4 hubs)
    3. Risk Shield (MPZP protection, ISOK flood, Cadastral discrepancy, industrial neighbors)
    4. GESUT Utilities Audit (water, sewerage, heating/gas, fiber, road access)
    """
    parcel_id = str(_prop(listing, "parcel_id", "") or "").strip()
    city = str(_prop(listing, "city", "") or "").strip()
    district = str(_prop(listing, "district", "") or "").strip()
    street = str(_prop(listing, "street", "") or "").strip()
    cadastral_area = _prop(listing, "cadastral_area", None)

    voivodeship = ""
    short_nr = ""
    obreb = ""
    if parcel_id:
        p_parts = parcel_id.split(".")
        short_nr = p_parts[-1] if p_parts else ""
        obreb = p_parts[-2] if len(p_parts) >= 2 else ""
        teryt_prefix = parcel_id[:2]
        voivodeship = TERYT_VOIVODESHIPS.get(teryt_prefix, "")

    v_cap = voivodeship.capitalize() if voivodeship else ""
    clipboard_text = (
        f"Numer działki: {short_nr}\nWojewództwo: {v_cap}\nIdentyfikator TERYT: {parcel_id}" if parcel_id else ""
    )

    cadastral_packet = {
        "voivodeship": v_cap,
        "city": city,
        "district": district,
        "street": street,
        "parcel_id": parcel_id or None,
        "parcel_short": short_nr or None,
        "obreb": obreb or None,
        "cadastral_area": cadastral_area,
        "clipboard_text": clipboard_text,
    }

    tco = calculate_tco_audit(listing, market_median_m2=market_median_m2)
    commute = calculate_commute_audit(listing)
    risk = calculate_risk_shield(listing)
    gesut = calculate_gesut_audit(listing, gesut_networks=gesut_networks)

    return {
        "cadastral_packet": cadastral_packet,
        "search_packet": cadastral_packet,  # backwards compatibility
        "tco_audit": tco,
        "commute_audit": commute,
        "risk_shield": risk,
        "gesut_audit": gesut,
    }


@dataclass
class PropertyValuationIntelligence:
    """Consolidated valuation and spatial due diligence intelligence."""

    local_median_m2: float | None
    negotiation: NegotiationAdvice
    land_and_utilities: dict[str, Any]

    @property
    def cadastral_packet(self) -> dict[str, Any]:
        return self.land_and_utilities.get("cadastral_packet", {})

    @property
    def tco_audit(self) -> dict[str, Any]:
        return self.land_and_utilities.get("tco_audit", {})

    @property
    def commute_audit(self) -> dict[str, Any]:
        return self.land_and_utilities.get("commute_audit", {})

    @property
    def risk_shield(self) -> dict[str, Any]:
        return self.land_and_utilities.get("risk_shield", {})

    @property
    def gesut_audit(self) -> dict[str, Any]:
        return self.land_and_utilities.get("gesut_audit", {})

    def to_dashboard_dict(self) -> dict[str, Any]:
        """Provides the exact key-value mapping expected by live dashboard API."""
        return {
            "market_median_m2": self.negotiation.market_median_m2,
            "price_deviation_pct": self.negotiation.price_deviation_pct,
            "price_deviation_adjusted_pct": self.negotiation.price_deviation_adjusted_pct,
            "days_on_market": self.negotiation.days_on_market,
            "negotiation_leverage": self.negotiation.negotiation_leverage,
            "fair_market_value": self.negotiation.fair_market_value,
            "suggested_opening_offer": self.negotiation.suggested_opening_offer,
            "negotiation_arguments": self.negotiation.arguments,
            "land_audit": self.land_and_utilities,
        }


class PropertyValuationEngine:
    """Consolidated valuation and financial intelligence engine.

    Synthesizes:
    - Market median resolution across city/district tiers
    - Negotiation leverage, FMV, and strategic opening offer
    - True Cost of Ownership (TCO) including finishing capex, notary, and taxes
    - Commute matrix (PKA rail, S19/A4, city center)
    - Spatial Risk Shield (flood, landslides, monuments, noise, high-voltage)
    - GESUT infrastructure audit (network proximity, water, sewer, fiber)
    """

    def __init__(
        self,
        market_medians: dict[str, float] | None = None,
        capex_settings: dict[str, Any] | None = None,
    ) -> None:
        self.market_medians = market_medians or {}
        self.capex_settings = capex_settings

    def resolve_median(
        self,
        city: str | None,
        district: str | None,
        category: str | None,
        override_medians: dict[str, float] | None = None,
    ) -> float | None:
        medians = override_medians if override_medians is not None else self.market_medians
        return resolve_local_median(medians, city, district, category)

    def evaluate(
        self,
        listing: Any,
        filter_result: FilterResult | None = None,
        market_medians: dict[str, float] | None = None,
        price_drop_amount: float = 0.0,
        price_drop_pct: float = 0.0,
        price_history_count: int = 1,
        gesut_networks: dict[str, Any] | None = None,
    ) -> PropertyValuationIntelligence:
        medians = market_medians if market_medians is not None else self.market_medians
        city = _prop(listing, "city", None)
        district = _prop(listing, "district", None)
        category = _prop(listing, "category", "dom")

        local_median = resolve_local_median(medians, city, district, category)

        neg_advice = analyze_negotiation(
            listing=listing,
            filter_result=filter_result,
            market_median_m2=local_median,
            price_drop_amount=price_drop_amount,
            price_drop_pct=price_drop_pct,
            price_history_count=price_history_count,
        )

        land_audit = analyze_land_and_utilities(
            listing=listing,
            market_median_m2=local_median,
            gesut_networks=gesut_networks,
        )

        return PropertyValuationIntelligence(
            local_median_m2=local_median,
            negotiation=neg_advice,
            land_and_utilities=land_audit,
        )


# Singleton valuation engine instance
valuation_engine = PropertyValuationEngine()
