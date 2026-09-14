from unittest.mock import AsyncMock

import pytest

from src.filters import QualificationEngine, Stage1Filter, Stage2SemanticFilter
from src.models.enums import (
    BuildingType,
    FinishCondition,
    HeatingType,
    MarketType,
    QualificationStatus,
    RoadType,
    SegmentSubtype,
    SewerageType,
)
from src.models.listing import ListingSchema


def create_sample_listing(**kwargs):
    defaults = {
        "id": "sample-1",
        "portal": "Otodom",
        "title": "Nowoczesny dom w Rzeszowie",
        "url": "https://otodom.pl/oferta/sample-1",
        "price": 1_100_000,
        "price_per_m2": 9_166,
        "area_home": 120.0,
        "area_plot": 350.0,
        "building_type": BuildingType.SZEREGOWIEC,
        "segment_subtype": SegmentSubtype.NIEOKRESLONY,
        "location_raw": "Rzeszów, Staromieście",
        "access_road_type": RoadType.ASFALT,
        "raw_description": "Piękny segment skrajny z garażem. Dojazd asfaltowy.",
    }
    defaults.update(kwargs)
    return ListingSchema(**defaults)


class PermissiveProfile:
    """Profile stub with permissive filter settings (config-independent tests)."""

    name = "test"
    category = "dom"
    min_price = 0.0
    max_price = 1_300_000.0
    min_price_per_m2 = None
    max_price_per_m2 = None
    min_area_home = 90.0
    max_area_home = 145.0
    min_area_plot = 250.0
    max_area_plot = None
    min_rooms = None
    max_rooms = None
    min_floor = None
    max_floor = None
    min_year_built = 1980
    max_year_built = None
    owner_type = "all"
    market_type = "all"
    blacklist_keywords: list[str] = []
    whitelist_areas: list[dict] = []
    building_types = ["szeregowiec", "bliźniak", "wolnostojący", "inny"]
    allowed_finish_conditions = ["all"]
    allow_visualisations = True
    reject_septic_tank = False
    allowed_heating_types = ["all"]


def test_stage1_budget_filter():
    f = Stage1Filter()

    # Budget OK
    listing_ok = create_sample_listing(price=1_250_000)
    passed, reasons, _ = f.evaluate(listing_ok)
    assert passed is True
    assert len(reasons) == 0

    # Budget exceeded (> 1.3M)
    listing_expensive = create_sample_listing(price=1_350_000)
    passed, reasons, _ = f.evaluate(listing_expensive)
    assert passed is False
    assert any("przekracza budżet" in r for r in reasons)


def test_stage1_area_home_filter():
    f = Stage1Filter(min_area_home=90.0, max_area_home=145.0)

    # Below 90m2
    listing_small = create_sample_listing(area_home=85.0)
    passed, reasons, _ = f.evaluate(listing_small)
    assert passed is False
    assert any("mniejszy niż wymagane 90" in r for r in reasons)

    # Above 145m2
    listing_large = create_sample_listing(area_home=150.0)
    passed, reasons, _ = f.evaluate(listing_large)
    assert passed is False
    assert any("większy niż dopuszczalne 145" in r for r in reasons)

    # Valid area
    listing_valid = create_sample_listing(area_home=120.0)
    passed, reasons, _ = f.evaluate(listing_valid)
    assert passed is True


def test_stage1_blacklist():
    blacklist_terms = [
        "matysówka",
        "matysowska",
        "tyczyn",
        "chmielnik",
        "biała",
        "zwięczyca",
        "kielanówka",
        "górna słocina",
        "św. rocha",
        "na skarpie",
        "teren osuwiskowy",
    ]
    f = Stage1Filter(blacklist=blacklist_terms)

    for term in blacklist_terms:
        listing = create_sample_listing(location_raw=f"Rzeszów okolice, {term}")
        passed, reasons, _ = f.evaluate(listing)
        assert passed is False, f"Expected rejection for blacklist term '{term}'"
        assert any("Blackliście" in r for r in reasons)


def test_stage1_whitelist():
    f = Stage1Filter()

    # Słocina Dolna (Paderewskiego) -> Whitelist matched
    listing_slocina = create_sample_listing(
        location_raw="Rzeszów, Słocina",
        street="Witolda",
        title="Dom na Słocinie ul. Witolda",
    )
    passed, _, matched = f.evaluate(listing_slocina)
    assert passed is True
    assert matched == "Słocina Dolna"

    # Górna Słocina -> Rejected by blacklist even if Słocina (explicit terms)
    f_bl = Stage1Filter(blacklist=["górna słocina", "św. rocha"])
    listing_gorna = create_sample_listing(
        location_raw="Rzeszów, Górna Słocina",
        street="św. Rocha",
    )
    passed, reasons, _ = f_bl.evaluate(listing_gorna)
    assert passed is False


def test_stage1_year_built_threshold():
    # Explicit threshold (config-independent)
    f = Stage1Filter(min_year_built=1980)

    listing_old = create_sample_listing(year_built=1974)
    passed, reasons, _ = f.evaluate(listing_old)
    assert passed is False
    assert any("Rok budowy 1974" in r for r in reasons)

    listing_ok = create_sample_listing(year_built=1985)
    passed, reasons, _ = f.evaluate(listing_ok)
    assert passed is True

    listing_no_year = create_sample_listing(year_built=None)
    passed, reasons, _ = f.evaluate(listing_no_year)
    assert passed is True


def test_stage1_building_types_allowlist():
    f = Stage1Filter(building_types=["wolnostojący"])

    listing_blizniak = create_sample_listing(building_type=BuildingType.BLIZNIAK)
    passed, reasons, _ = f.evaluate(listing_blizniak)
    assert passed is False
    assert any("Typ budynku 'bliźniak'" in r for r in reasons)

    listing_wolny = create_sample_listing(building_type=BuildingType.WOLNOSTOJACY)
    passed, reasons, _ = f.evaluate(listing_wolny)
    assert passed is True


def test_stage2_corner_and_middle_segment():
    s2 = Stage2SemanticFilter()
    prof = PermissiveProfile()

    # Corner segment with good plot
    listing_corner = create_sample_listing(
        area_plot=250.0,
        raw_description="Sprzedam segment skrajny w zabudowie szeregowej. Garaż w bryle.",
    )
    passed, reasons, pros, cons, subtype, is_corner, has_parking, detected_finish, has_vis, sew, heat, fiber = (
        s2.analyze(listing_corner, profile=prof)
    )
    assert passed is True
    assert is_corner is True
    assert subtype == SegmentSubtype.SKRAJNY

    # Middle segment with small plot (< 200 m2) -> REJECT
    listing_middle_small = create_sample_listing(
        area_plot=160.0,
        raw_description="Środkowy segment szeregówki, działka 160 m2.",
    )
    passed, reasons, pros, cons, subtype, is_corner, has_parking, detected_finish, has_vis, sew, heat, fiber = (
        s2.analyze(listing_middle_small, profile=prof)
    )
    assert passed is False
    assert any("Segment środkowy z małą działką" in r for r in reasons)


def test_stage2_road_access_rejection():
    s2 = Stage2SemanticFilter()

    listing_dirt_road = create_sample_listing(
        raw_description="Dom jednorodzinny. Dojazd drogą polną, 300m od asfaltu.",
    )
    passed, reasons, *_ = s2.analyze(listing_dirt_road)
    assert passed is False
    assert any("Nieodpowiedni standard dojazdu" in r for r in reasons)


def test_stage2_plot_extraction_from_description():
    s2 = Stage2SemanticFilter()

    # Missing plot in header, but described in text
    listing_no_plot = create_sample_listing(
        area_plot=None,
        raw_description="Świetny dom, segment skrajny, działka o powierzchni 4 ary. Dojazd asfaltowy. Garaż.",
    )
    passed, reasons, pros, cons, subtype, is_corner, has_parking, detected_finish, has_vis, *_ = s2.analyze(
        listing_no_plot, profile=PermissiveProfile()
    )
    assert passed is True
    assert any("Wykryto metraż działki z opisu: ok. 400 m²" in p for p in pros)


def test_stage2_finish_condition_detection():
    s2 = Stage2SemanticFilter()

    # 1. Do zamieszkania / pod klucz
    l_ready = create_sample_listing(raw_description="Dom gotowy do zamieszkania, wykończony pod klucz.")
    _, _, pros_ready, _, _, _, _, finish_ready, *_ = s2.analyze(l_ready)
    assert finish_ready == FinishCondition.DO_ZAMIESZKANIA
    assert any("do zamieszkania / pod klucz" in p for p in pros_ready)

    # 2. Stan deweloperski
    l_dev = create_sample_listing(raw_description="Sprzedam dom w stanie deweloperskim podwyższonym.")
    _, _, pros_dev, _, _, _, _, finish_dev, *_ = s2.analyze(l_dev)
    assert finish_dev == FinishCondition.DEWELOPERSKI

    # 3. Stan surowy zamknięty (SSZ)
    l_ssz = create_sample_listing(raw_description="Inwestycja w stanie surowym zamkniętym (SSZ), okna trzyszybowe.")
    _, _, _, cons_ssz, _, _, _, finish_ssz, *_ = s2.analyze(l_ssz)
    assert finish_ssz == FinishCondition.SUROWY_ZAMKNIETY
    assert any("surowy zamknięty" in c for c in cons_ssz)

    # 4. Stan surowy otwarty (SSO)
    l_sso = create_sample_listing(raw_description="Budynek w stanie surowym otwartym, mury z pustaka.")
    _, _, _, cons_sso, _, _, _, finish_sso, *_ = s2.analyze(l_sso)
    assert finish_sso == FinishCondition.SUROWY_OTWARTY
    assert any("surowy otwarty" in c for c in cons_sso)

    # 5. Do remontu
    l_rem = create_sample_listing(raw_description="Starszy dom z lat 70., do generalnego remontu.")
    _, _, _, cons_rem, _, _, _, finish_rem, *_ = s2.analyze(l_rem)
    assert finish_rem == FinishCondition.DO_REMONTU


def test_stage2_finish_vocabulary_expansion():
    s2 = Stage2SemanticFilter()

    phrases = [
        "Dom częściowo wykończony, do dokończenia na poddaszu.",
        "Do doprowadzenia do stanu używalności, media na działce.",
        "Sprzedam dom w trakcie wykończenia.",
        "Wymaga dokończenia prac wykończeniowych.",
        "Stan do wykończenia we własnym zakresie.",
    ]
    for desc in phrases:
        listing = create_sample_listing(raw_description=desc)
        _, _, _, cons, _, _, _, finish, *_ = s2.analyze(listing)
        assert finish == FinishCondition.DO_WYKONCZENIA, f"Expected DO_WYKONCZENIA for: {desc}"


def test_stage2_finish_conflict_structured_wins():
    s2 = Stage2SemanticFilter()

    # Portal says DO_WYKONCZENIA, description says 'pod klucz' -> conflict flagged, structured wins
    listing = create_sample_listing(
        finish_condition=FinishCondition.DO_WYKONCZENIA,
        raw_description="Dom wykończony pod klucz, gotowy do zamieszkania.",
    )
    _, _, _, cons, _, _, _, finish, *_ = s2.analyze(listing)
    assert finish == FinishCondition.DO_WYKONCZENIA
    assert any("Rozbieżność stanu wykończenia" in c for c in cons)

    # Portal deceptively says DO_ZAMIESZKANIA, description mentions remont -> description facts override
    listing2 = create_sample_listing(
        finish_condition=FinishCondition.DO_ZAMIESZKANIA,
        raw_description="Dom wymaga generalnego remontu.",
    )
    _, _, _, cons2, _, _, _, finish2, *_ = s2.analyze(listing2)
    assert finish2 == FinishCondition.DO_REMONTU
    assert any("Skorygowano stan wykończenia" in c for c in cons2)

    # Portal silent -> description wins
    listing3 = create_sample_listing(
        finish_condition=FinishCondition.NIEOKRESLONY,
        raw_description="Dom do wykończenia.",
    )
    _, _, _, _, _, _, _, finish3, *_ = s2.analyze(listing3)
    assert finish3 == FinishCondition.DO_WYKONCZENIA


def test_stage2_visualisations_detection():
    s2 = Stage2SemanticFilter()

    l_vis = create_sample_listing(
        raw_description="Nowa inwestycja. Zdjęcia poglądowe, zamieszczone wizualizacje przedstawiają przykładową aranżację."
    )
    passed, reasons, pros, cons, subtype, is_corner, has_parking, detected_finish, has_vis, *_ = s2.analyze(l_vis)
    assert has_vis is True
    assert any("Oferta zawiera wizualizacje" in c for c in cons)


def test_stage2_utilities_detection():
    s2 = Stage2SemanticFilter()

    # Sewerage: City vs Septic vs Treatment plant
    l_sew_city = create_sample_listing(raw_description="Wszystkie media miejskie, kanalizacja miejska, gaz, woda.")
    assert s2.detect_sewerage(l_sew_city.raw_description) == SewerageType.MIEJSKA

    l_sew_szambo = create_sample_listing(raw_description="Dojazd asfaltowy, szambo 10m3, woda ze studni.")
    assert s2.detect_sewerage(l_sew_szambo.raw_description) == SewerageType.SZAMBO

    l_sew_oczyszcz = create_sample_listing(
        raw_description="Ekologiczne rozwiązania, przydomowa biologiczna oczyszczalnia ścieków."
    )
    assert s2.detect_sewerage(l_sew_oczyszcz.raw_description) == SewerageType.OCZYSZCZALNIA

    # Heating: Heat pump vs Gas vs Solid fuel
    l_heat_pump = create_sample_listing(raw_description="Nowoczesna powietrzna pompa ciepła z ogrzewaniem podłogowym.")
    assert s2.detect_heating(l_heat_pump.raw_description) == HeatingType.POMPA_CIEPLA

    l_heat_gas = create_sample_listing(raw_description="Piec gazowy dwufunkcyjny firmy Viessmann, gaz w budynku.")
    assert s2.detect_heating(l_heat_gas.raw_description) == HeatingType.GAZOWE

    l_heat_solid = create_sample_listing(raw_description="Kotłownia z piecem na pellet i ekogroszek 5 klasy.")
    assert s2.detect_heating(l_heat_solid.raw_description) == HeatingType.PELLET_WEGIEL

    # Fiber optic
    l_fiber = create_sample_listing(raw_description="W ulicy szybki internet światłowodowy, światłowód podłączony.")
    assert s2.detect_fiber(l_fiber.raw_description) is True

    l_no_fiber = create_sample_listing(raw_description="Dom pod lasem, cisza i spokój.")
    assert s2.detect_fiber(l_no_fiber.raw_description) is False


@pytest.mark.asyncio
async def test_full_qualification_engine():
    engine = QualificationEngine(llm_enabled=False)

    # Perfect house in Whitelist with 'pod klucz', heat pump, city sewer, and fiber
    listing = create_sample_listing(
        title="Segment skrajny, Słocina ul. Paderewskiego",
        location_raw="Rzeszów, Słocina",
        street="Paderewskiego",
        price=1_190_000,
        area_home=125.0,
        area_plot=380.0,
        raw_description=(
            "Segment skrajny z dużym ogrodem. Garaż w bryle, 2 miejsca postojowe. "
            "Dojazd asfaltowy. Wykończony pod klucz. Pompa ciepła, kanalizacja miejska, podłączony światłowód."
        ),
    )
    res = await engine.evaluate_listing(listing)
    assert res.is_qualified is True
    assert res.status == QualificationStatus.QUALIFIED_WHITELIST
    assert res.is_corner is True
    assert res.has_parking_or_garage is True
    assert res.finish_condition == FinishCondition.DO_ZAMIESZKANIA
    assert res.sewerage == SewerageType.MIEJSKA
    assert res.heating == HeatingType.POMPA_CIEPLA
    assert res.has_fiber is True
    assert res.has_visualisations is False
    assert res.score == 100.0  # capped at 100


@pytest.mark.asyncio
async def test_qualification_engine_scoring_penalties_and_bonuses():
    engine = QualificationEngine(llm_enabled=False)

    base_args = {
        "title": "Dom w Rzeszowie",
        "location_raw": "Rzeszów, Staromieście",
        "price": 1_000_000,
        "area_home": 110.0,
        "area_plot": 300.0,
    }

    # Ready to move in with heat pump + city sewer
    l_ready = create_sample_listing(
        **base_args,
        raw_description="Dom wykończony pod klucz, garaż. Pompa ciepła, kanalizacja miejska.",
    )
    res_ready = await engine.evaluate_listing(l_ready)

    # Raw open with visualisations, szambo, and solid fuel heating
    l_raw_vis = create_sample_listing(
        **base_args,
        raw_description="Stan surowy otwarty, mury. Wizualizacje przedstawiają projekt, zdjęcia poglądowe. Szambo, piec na węgiel.",
    )
    res_raw_vis = await engine.evaluate_listing(l_raw_vis)

    # l_ready should score massively higher than l_raw_vis:
    # finish (+15 vs -20), vis (0 vs -10), sewerage (+10 vs -10), heating (+10 vs -15)
    assert res_ready.score > res_raw_vis.score + 50.0
    assert res_raw_vis.has_visualisations is True
    assert res_raw_vis.finish_condition == FinishCondition.SUROWY_OTWARTY
    assert res_raw_vis.sewerage == SewerageType.SZAMBO
    assert res_raw_vis.heating == HeatingType.PELLET_WEGIEL
    assert res_ready.sewerage == SewerageType.MIEJSKA
    assert res_ready.heating == HeatingType.POMPA_CIEPLA


@pytest.mark.asyncio
async def test_qualification_engine_building_type_and_year_scoring():
    engine = QualificationEngine(llm_enabled=False)

    base_args = {
        "title": "Dom w Rzeszowie",
        "location_raw": "Rzeszów, Staromieście",
        "price": 1_000_000,
        "area_home": 110.0,
        "area_plot": 300.0,
        "raw_description": "Dom wykończony pod klucz, garaż. Pompa ciepła, kanalizacja miejska.",
    }

    l_detached_new = create_sample_listing(**base_args, building_type=BuildingType.WOLNOSTOJACY, year_built=2018)
    res_detached = await engine.evaluate_listing(l_detached_new)

    l_ribbon_old = create_sample_listing(**base_args, building_type=BuildingType.SZEREGOWIEC, year_built=1965)
    res_ribbon = await engine.evaluate_listing(l_ribbon_old)

    # wolnostojący +10 vs szeregowiec -10; 1965 -> -(2000-1965)//10*2 = -6
    assert res_detached.score - res_ribbon.score >= 23.0


@pytest.mark.asyncio
async def test_qualification_engine_price_gradient():
    engine = QualificationEngine(llm_enabled=False)

    def make(ppm2):
        return create_sample_listing(
            title="Dom testowy",
            location_raw="Rzeszów, Staromieście",
            price=600_000,
            price_per_m2=ppm2,
            area_home=100.0,
            area_plot=400.0,
            building_type=BuildingType.WOLNOSTOJACY,
            raw_description="Dom do sprzedania.",
        )

    res_cheap = await engine.evaluate_listing(make(5000.0), profile=PermissiveProfile())
    res_mid = await engine.evaluate_listing(make(7000.0), profile=PermissiveProfile())
    res_expensive = await engine.evaluate_listing(make(10000.0), profile=PermissiveProfile())

    assert res_cheap.score > res_mid.score > res_expensive.score
    assert res_cheap.score <= 100.0


@pytest.mark.asyncio
async def test_qualification_engine_budget_bonus():
    engine = QualificationEngine(llm_enabled=False)

    base = {
        "title": "Dom testowy",
        "location_raw": "Rzeszów, Staromieście",
        "price_per_m2": 7000.0,
        "area_home": 110.0,
        "area_plot": 300.0,
        "building_type": BuildingType.WOLNOSTOJACY,
        "raw_description": "Dom do sprzedania.",
    }
    res_budget = await engine.evaluate_listing(
        create_sample_listing(**base, price=800_000), profile=PermissiveProfile()
    )
    res_full = await engine.evaluate_listing(
        create_sample_listing(**base, price=1_200_000), profile=PermissiveProfile()
    )

    assert res_budget.score == res_full.score + 5.0


@pytest.mark.asyncio
async def test_qualification_engine_needs_review_when_state_unknown():
    engine = QualificationEngine(llm_enabled=False)

    listing = create_sample_listing(
        title="Dom bez danych",
        location_raw="Rzeszów, Staromieście",
        price=900_000,
        area_home=120.0,
        area_plot=400.0,
        raw_description="Do sprzedania dom.",
    )
    res = await engine.evaluate_listing(listing, profile=PermissiveProfile())
    assert res.is_qualified is True
    assert res.status == QualificationStatus.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_qualification_engine_no_double_counting_for_szambo():
    engine = QualificationEngine(llm_enabled=False)

    base = {
        "title": "Dom testowy",
        "location_raw": "Rzeszów, Staromieście",
        "price": 900_000,
        "area_home": 110.0,
        "area_plot": 300.0,
        "building_type": BuildingType.WOLNOSTOJACY,
    }
    res_none = await engine.evaluate_listing(
        create_sample_listing(**base, raw_description="Dom do sprzedania."),
        profile=PermissiveProfile(),
    )
    res_szambo = await engine.evaluate_listing(
        create_sample_listing(**base, raw_description="Dom do sprzedania. Szambo."),
        profile=PermissiveProfile(),
    )

    # szambo penalty is exactly -10 (typed scoring), not -10 -5 from the con
    assert res_szambo.score == res_none.score - 10.0
    assert res_szambo.sewerage == SewerageType.SZAMBO


@pytest.mark.asyncio
async def test_qualification_engine_year_built_rejects_old_house():
    engine = QualificationEngine(llm_enabled=False)

    listing_old = create_sample_listing(
        title="Stary dom",
        location_raw="Rzeszów, Staromieście",
        year_built=1960,
    )
    res = await engine.evaluate_listing(listing_old)
    assert res.is_qualified is False
    assert res.status == QualificationStatus.REJECTED_STAGE1
    assert any("Rok budowy" in r for r in res.stage1_reasons)


def test_stage1_blacklist_negation_and_transit():
    f1 = Stage1Filter(blacklist=["osuwisko", "skarpie", "na skarpie", "tyczyn"])

    # Negated hazard words: should NOT trigger blacklist
    listing_negated = create_sample_listing(
        title="Dom parterowy",
        location_raw="Rzeszów, Słocina",
        raw_description="Działka sucha, płaska, bez osuwisk, brak skarp. Spokojna okolica.",
    )
    assert f1.check_blacklist(listing_negated) is None

    # Transit directional mention: should NOT trigger blacklist when property is in Rzeszów
    listing_transit = create_sample_listing(
        title="Nowoczesny bliźniak",
        location_raw="Rzeszów, Staromieście",
        raw_description="Świetna lokalizacja, szybki dojazd do Tyczyna w 5 minut.",
    )
    assert f1.check_blacklist(listing_transit) is None

    # Actual hazard word without negation: should trigger blacklist
    listing_hazard = create_sample_listing(
        title="Dom na skarpie",
        location_raw="Rzeszów",
        raw_description="Dom posadowiony na stromej skarpie z pięknym widokiem.",
    )
    assert f1.check_blacklist(listing_hazard) is not None

    # Actual blacklisted location: should trigger blacklist
    listing_blacklisted_loc = create_sample_listing(
        title="Dom w Tyczynie",
        location_raw="Tyczyn, ul. Kościuszki",
        raw_description="Piękny dom blisko centrum.",
    )
    assert f1.check_blacklist(listing_blacklisted_loc) is not None


def test_stage2_under_construction_and_turnkey_option():
    s2 = Stage2SemanticFilter()

    # 'Możliwość wykończenia pod klucz' should classify as DEWELOPERSKI, not DO_ZAMIESZKANIA
    listing_dev = create_sample_listing(
        finish_condition=FinishCondition.NIEOKRESLONY,
        raw_description="Nowy segment w stanie deweloperskim, możliwość wykończenia pod klucz za dopłatą.",
    )
    _, _, _, _, _, _, _, finish, *_ = s2.analyze(listing_dev)
    assert finish == FinishCondition.DEWELOPERSKI

    # Construction timeline indicates DEWELOPERSKI
    listing_constr = create_sample_listing(
        finish_condition=FinishCondition.NIEOKRESLONY,
        raw_description="Rozpoczęcie prac budowlanych, planowany termin oddania w IV kwartale 2026.",
    )
    _, _, _, _, _, _, _, finish2, *_ = s2.analyze(listing_constr)
    assert finish2 == FinishCondition.DEWELOPERSKI


def test_stage2_visualisation_gallery_urls_and_correlation():
    s2 = Stage2SemanticFilter()

    # Visualisation detected from gallery image URL
    listing_render_url = create_sample_listing(
        raw_description="Dom do sprzedaży w Rzeszowie.",
        gallery_images=["https://img.example.com/photos/render_livingroom_3d.jpg"],
    )
    _, _, _, _, _, _, _, _, has_vis, *_ = s2.analyze(listing_render_url)
    assert has_vis is True

    # Primary market + future year + turnkey claim -> correlation triggers has_visualisations
    listing_primary_future = create_sample_listing(
        market=MarketType.PIERWOTNY,
        year_built=2026,
        raw_description="Gotowy do zamieszkania pod klucz dom wg najnowszego projektu.",
    )
    _, _, _, _, _, _, _, _, has_vis2, *_ = s2.analyze(listing_primary_future)
    assert has_vis2 is True


def test_stage2_area_plot_writeback_to_model():
    s2 = Stage2SemanticFilter()

    listing = create_sample_listing(
        area_plot=None,
        raw_description="Dom parterowy. Powierzchnia działki wynosi 4,5 ara. Ogrodzona.",
    )
    assert listing.area_plot is None
    s2.analyze(listing)
    assert listing.area_plot == 450.0


@pytest.mark.asyncio
async def test_qualification_engine_ai_due_diligence_fields_from_llm():
    engine = QualificationEngine(llm_enabled=False)
    engine.llm = AsyncMock()
    engine.llm.analyze_description.return_value = {
        "summary": "Segment skrajny w stanie deweloperskim. Główne ryzyko: brak info o odbiorze budynku.",
        "questions_for_agent": [
            "Czy w cenie jest kocioł gazowy i grzejniki?",
            "Jaki jest stan prawny drogi dojazdowej?",
        ],
        "contact_phone": "+48 600 123 456",
        "contact_person": "Jan Kowalski",
    }
    listing = create_sample_listing(
        raw_description="Segment skrajny z garażem. Dojazd asfaltowy. Opiekun oferty: Jan Kowalski.",
    )
    res = await engine.evaluate_listing(listing, profile=PermissiveProfile())

    engine.llm.analyze_description.assert_awaited_once()
    assert res.ai_summary == "Segment skrajny w stanie deweloperskim. Główne ryzyko: brak info o odbiorze budynku."
    assert res.ai_questions == [
        "Czy w cenie jest kocioł gazowy i grzejniki?",
        "Jaki jest stan prawny drogi dojazdowej?",
    ]
    assert res.contact_phone == "+48 600 123 456"
    assert res.contact_person == "Jan Kowalski"


@pytest.mark.asyncio
async def test_qualification_engine_llm_discrepancies_and_sewerage_brak():
    engine = QualificationEngine(llm_enabled=False)
    engine.llm = AsyncMock()
    engine.llm.analyze_description.return_value = {
        "sewerage": "brak",
        "discrepancies": ["Portal podaje ogrzewanie gazowe, ale opis wymienia piec na pellet."],
    }

    listing = create_sample_listing(
        raw_description="Segment skrajny z garażem. Dojazd asfaltowy.",
    )
    res = await engine.evaluate_listing(listing, profile=PermissiveProfile())

    assert any("Brak przyłącza kanalizacyjnego" in c for c in res.cons)
    assert any("Rozbieżność portal vs opis" in c for c in res.cons)


@pytest.mark.asyncio
async def test_qualification_engine_llm_verdict_and_finish_note():
    engine = QualificationEngine(llm_enabled=False)
    engine.llm = AsyncMock()
    engine.llm.analyze_description.return_value = {
        "summary": "Dom 120 m² w Rzeszowie za 820 000 zł (6 833 zł/m²), stan deweloperski.",
        "worth_interest": True,
        "verdict": "Tak — 6 833 zł/m² jest poniżej średniej rynkowej, ale dolicz ok. 200 tys. zł na wykończenie.",
        "finish_condition": "do_wykonczenia",
        "finish_note": "Wykonane: instalacje, okna, elewacja. Do zrobienia: wylewki, tynki, całe wykończenie.",
    }

    listing = create_sample_listing(
        raw_description="Dom w stanie deweloperskim do własnego wykończenia. Wykonane instalacje i okna.",
    )
    res = await engine.evaluate_listing(listing, profile=PermissiveProfile())

    assert (
        res.ai_verdict == "Tak — 6 833 zł/m² jest poniżej średniej rynkowej, ale dolicz ok. 200 tys. zł na wykończenie."
    )
    assert res.worth_interest is True
    assert listing.finish_condition == FinishCondition.DO_WYKONCZENIA
    assert any("Wykonane: instalacje, okna, elewacja" in c for c in res.cons)


@pytest.mark.asyncio
async def test_qualification_engine_llm_visualisations_detection():
    engine = QualificationEngine(llm_enabled=False)
    engine.llm = AsyncMock()
    engine.llm.analyze_description.return_value = {
        "has_visualisations": True,
        "visualisation_note": "Zdjęcia przedstawiają wizualizacje przykładowej aranżacji.",
    }

    listing = create_sample_listing(
        raw_description="Nowa inwestycja. Dojazd asfaltowy.",
    )
    assert listing.has_visualisations is False
    res = await engine.evaluate_listing(listing, profile=PermissiveProfile())

    assert listing.has_visualisations is True
    assert res.has_visualisations is True
    assert any("Wizualizacje" in c for c in res.cons)


def test_llm_slice_description_head_and_tail():
    from src.filters.llm_analyzer import LLMAnalyzer

    desc = "A" * 4000 + "MIDDLE-CONTENT-THAT-SHOULD-BE-DROPPED" + "B" * 1500
    sliced = LLMAnalyzer._slice_description(desc)
    assert "A" * 4000 in sliced
    assert "B" * 1500 in sliced
    assert "MIDDLE-CONTENT" not in sliced
    assert "[...]" in sliced


def test_llm_slice_description_short_unchanged():
    from src.filters.llm_analyzer import LLMAnalyzer

    desc = "Krótki opis ogłoszenia."
    assert LLMAnalyzer._slice_description(desc) == desc


@pytest.mark.asyncio
async def test_qualification_engine_contact_phone_regex_fallback():
    engine = QualificationEngine(llm_enabled=False)

    listing = create_sample_listing(
        raw_description="Piękny segment skrajny z garażem. Dojazd asfaltowy. Opiekun oferty: tel. 600-123-456.",
    )
    res = await engine.evaluate_listing(listing, profile=PermissiveProfile(), skip_llm=True)

    assert res.contact_phone == "+48600123456"
    assert res.contact_person is None
    assert res.ai_summary is None
    assert res.ai_questions == []


@pytest.mark.asyncio
async def test_qualification_engine_ai_fields_empty_without_phone_or_llm():
    engine = QualificationEngine(llm_enabled=False)

    listing = create_sample_listing(raw_description="Piękny segment skrajny z garażem. Dojazd asfaltowy.")
    res = await engine.evaluate_listing(listing, profile=PermissiveProfile(), skip_llm=True)

    assert res.contact_phone is None
    assert res.contact_person is None
    assert res.ai_summary is None
    assert res.ai_questions == []


@pytest.mark.asyncio
async def test_qualification_engine_llm_disabled_flag():
    engine = QualificationEngine(llm_enabled=False)
    assert engine.llm.enabled is False

    listing = create_sample_listing(
        raw_description="Segment skrajny z garażem. Dojazd asfaltowy. Opiekun oferty: tel. 600-123-456.",
    )
    res = await engine.evaluate_listing(listing, profile=PermissiveProfile())

    assert res.ai_summary is None
    assert res.ai_questions == []
    assert res.contact_phone == "+48600123456"  # regex fallback still active


@pytest.mark.asyncio
async def test_qualification_engine_llm_overrides_portal_do_wykonczenia_to_pod_klucz():
    from unittest.mock import AsyncMock

    engine = QualificationEngine(llm_enabled=True)

    # Mock LLM output simulating the forensic due diligence prompt analysis
    engine.llm.analyze_description = AsyncMock(
        return_value={
            "summary": "Wieliczka, 110 m², 790 000 zł (7 181 zł/m²). Dom w pełni wykończony pod klucz, do wykończenia jedynie taras.",
            "worth_interest": True,
            "verdict": "Tak — 7 181 zł/m² przy standardzie gotowym do zamieszkania to bardzo atrakcyjna oferta.",
            "questions_for_agent": [
                "Czy taras wymaga jedynie ułożenia deski kompozytowej, czy również wylewki?",
                "Jaka jest powierzchnia działki przynależnej do segmentu?",
            ],
            "contact_phone": "+48501234567",
            "contact_person": "Jan Kowalski",
            "finish_condition": "pod_klucz",
            "finish_note": "Wnętrze mieszkalne w pełni wykończone i umeblowane; do wykończenia jedynie taras i ogród.",
            "has_visualisations": False,
            "visualisation_note": None,
            "is_corner": True,
            "is_middle": False,
            "has_parking_or_garage": True,
            "road_is_bad": False,
            "terrain_risk": False,
            "sewerage": "miejska",
            "extracted_plot_m2": 320.0,
            "hidden_costs": [],
            "legal_risks": [],
            "discrepancies": [
                "Portal oznacza stan jako 'do wykończenia', podczas gdy opis potwierdza w pełni wykończone wnętrze mieszkalne (kuchnia, łazienki, podłogi gotowe)."
            ],
            "pros": ["Wykończone wnętrze pod klucz", "Klimatyzacja w salonie i sypialni"],
            "cons": [],
        }
    )

    listing = create_sample_listing(
        finish_condition=FinishCondition.DO_WYKONCZENIA,
        raw_description=(
            "Segment skrajny, ul. Parkowa, Wieliczka. "
            "Dom całkowicie wykończony z materiałów premium, zamieszkały od 2 lat. "
            "W pełni umeblowana kuchnia ze sprzętem Siemens, 2 wykończone łazienki, dębowy parkiet. "
            "Do wykończenia pozostał jedynie taras (deska) oraz ogród. Garaż w bryle budynku. Dojazd asfaltowy."
        ),
    )

    res = await engine.evaluate_listing(listing, profile=PermissiveProfile())

    # Ground truth: finish condition must be resolved to DO_ZAMIESZKANIA
    assert res.finish_condition == FinishCondition.DO_ZAMIESZKANIA
    # Obsolete "Do wykończenia" con must be purged
    assert not any(c.startswith("Do wykończenia") for c in res.cons)
    # Finish note for DO_ZAMIESZKANIA must be appended to pros with ✨ [Stan]
    assert any("✨ [Stan] Wnętrze mieszkalne w pełni wykończone" in p for p in res.pros)
    # Discrepancy logged
    assert any("Rozbieżność portal vs opis" in c for c in res.cons)
    # Standard finish pro added
    assert any("Standard wykończenia: gotowy do zamieszkania / pod klucz" in p for p in res.pros)
    # Contact phone and AI verdict extracted
    assert res.contact_phone == "+48501234567"
    assert res.contact_person == "Jan Kowalski"
    assert res.worth_interest is True


@pytest.mark.asyncio
async def test_qualification_engine_precheck_stage1():
    """QualificationEngine exposes precheck_stage1 as a clean seam."""
    engine = QualificationEngine(llm_enabled=False)
    valid_listing = create_sample_listing(price=700_000.0, area_home=120.0)
    passed, reasons, matched_wl = engine.precheck_stage1(valid_listing, profile=PermissiveProfile())
    assert passed is True
    assert reasons == []

    expensive_listing = create_sample_listing(price=5_000_000.0, area_home=120.0)
    passed_exp, reasons_exp, _ = engine.precheck_stage1(expensive_listing, profile=PermissiveProfile())
    assert passed_exp is False
    assert len(reasons_exp) > 0


@pytest.mark.asyncio
async def test_qualification_engine_deep_spatial_evaluation():
    """QualificationEngine directly applies spatial risk deductions and pros/cons."""
    engine = QualificationEngine(llm_enabled=False)
    listing = create_sample_listing(
        price=700_000.0,
        area_home=120.0,
        raw_description="Dom jednorodzinny wolnostojący, pod klucz, dojazd asfaltowy, kanalizacja miejska, pompa ciepła.",
    )
    # Attach spatial findings
    listing.mpzp_zone = "MN: tereny mieszkaniowe"
    listing.mpzp_status = "OBOWIĄZUJĄCY"
    listing.flood_risk_zone = "ZAGROŻENIE_POWODZIOWE"
    listing.landslide_risk = "OSUWISKO"
    listing.noise_level_db = 68.0
    listing.broadband_status = "ŚWIATŁOWÓD_AKTYWNY"
    listing.parcel_front_width_m = 14.0
    listing.terrain_slope_pct = 12.0
    listing.walkability_pka_dist_m = 600
    listing.walkability_pka_name = "Rzeszów Główny"

    geo_audit = {
        "surrounding_risks": ["Działka 123 ma użytek komercyjny/składowy (Bi): Bi"],
        "main_parcel_number": "100/2",
        "cadastral_area": 950.0,
    }

    res = await engine.evaluate_listing(listing, profile=PermissiveProfile(), geo_audit=geo_audit)

    assert res.is_qualified is True
    assert res.mpzp_zone == "MN: tereny mieszkaniowe"
    assert res.flood_risk_zone == "ZAGROŻENIE_POWODZIOWE"
    assert res.landslide_risk == "OSUWISKO"
    assert res.noise_level_db == 68.0
    assert res.broadband_status == "ŚWIATŁOWÓD_AKTYWNY"
    assert res.parcel_front_width_m == 14.0
    assert res.terrain_slope_pct == 12.0
    assert res.walkability_pka_name == "Rzeszów Główny"

    # Verify pros and cons
    assert any("Miejscowy Plan" in p for p in res.pros)
    assert any("Światłowód aktywny FTTH" in p for p in res.pros)
    assert any("Stacja kolejowa" in p for p in res.pros)
    assert any("Zidentyfikowano działkę w Geoportalu" in p for p in res.pros)

    assert any("Zagrożenie powodziowe" in c for c in res.cons)
    assert any("Aktywne osuwisko" in c for c in res.cons)
    assert any("Podwyższony poziom hałasu" in c for c in res.cons)
    assert any("Wąski front działki" in c for c in res.cons)
    assert any("Strome nachylenie terenu" in c for c in res.cons)
    assert any("Geoportal" in c for c in res.cons)
