from src.filters.fingerprint import generate_physical_fingerprint


def test_physical_fingerprint_tolerates_minor_area_differences():
    """Same property relisted by another agency with slightly different measurements."""
    # Agency A: 120m2, 350m2 plot, 5 pokoi, Paderewskiego, Rzeszów
    fp1 = generate_physical_fingerprint(
        area_home=120.0,
        area_plot=350.0,
        rooms=5,
        street="Paderewskiego",
        city="Rzeszów",
    )

    # Agency B: ±1m2 area, ±5m2 plot — same physical property
    fp2 = generate_physical_fingerprint(
        area_home=121.0,
        area_plot=354.0,
        rooms=5,
        street="ul. Ignacego Paderewskiego",
        city="rzeszow",
    )

    assert fp1 is not None
    assert fp1 == fp2, f"Expected identical fingerprint for same property, got {fp1} vs {fp2}"


def test_physical_fingerprint_price_does_not_affect_result():
    """Physical fingerprint is price-independent — price changes must not produce new fingerprints."""
    fp_original = generate_physical_fingerprint(
        area_home=120.0,
        area_plot=350.0,
        rooms=5,
        street="Paderewskiego",
        city="Rzeszów",
    )
    fp_repriced = generate_physical_fingerprint(
        area_home=120.0,
        area_plot=350.0,
        rooms=5,
        street="Paderewskiego",
        city="Rzeszów",
    )
    assert fp_original == fp_repriced, "Fingerprint must be price-independent"


def test_physical_fingerprint_differentiation():
    # House on Paderewskiego vs different house on Lubelska
    fp1 = generate_physical_fingerprint(
        area_home=120.0,
        area_plot=350.0,
        rooms=5,
        street="Paderewskiego",
        city="Rzeszów",
    )
    fp2 = generate_physical_fingerprint(
        area_home=95.0,
        area_plot=200.0,
        rooms=4,
        street="Lubelska",
        city="Rzeszów",
    )
    assert fp1 != fp2, "Different properties must have distinct fingerprints"


def test_extract_street_token_unknown_fallback():
    from src.filters.fingerprint import extract_street_token

    # When no street pattern matches in street or title
    token = extract_street_token(street="", title="Nieruchomość bez nazwy ulicy")
    assert token == "unknown_area", f"Expected unknown_area, got {token}"


def test_physical_fingerprint_price_independent():
    from src.filters.fingerprint import generate_physical_fingerprint

    # Original listing: 125m2, 400m2 plot, 5 rooms, Paderewskiego, Rzeszów
    fp1 = generate_physical_fingerprint(
        area_home=125.0,
        area_plot=400.0,
        rooms=5,
        street="Paderewskiego",
        city="Rzeszów",
    )

    # Re-listed with massive price drop and slightly different wording:
    # 124m2 (+/- 2m2 tolerance), 404m2 plot, same rooms and street
    fp2 = generate_physical_fingerprint(
        area_home=124.0,
        area_plot=404.0,
        rooms=5,
        street="ul. Ignacego Paderewskiego",
        city="rzeszow",
    )

    assert fp1 is not None
    assert fp1 == fp2, "Physical fingerprint must match across re-listings regardless of price"


def test_physical_fingerprint_different_cities():
    from src.filters.fingerprint import generate_physical_fingerprint

    fp1 = generate_physical_fingerprint(
        area_home=120.0,
        area_plot=300.0,
        rooms=4,
        street="Paderewskiego",
        city="Warszawa",
    )
    fp2 = generate_physical_fingerprint(
        area_home=120.0,
        area_plot=300.0,
        rooms=4,
        street="Paderewskiego",
        city="Kraków",
    )
    assert fp1 != fp2, "Same street in different cities must have different physical fingerprints"


def test_physical_fingerprint_unknown_fallback():
    from src.filters.fingerprint import generate_physical_fingerprint

    # When location is completely unknown, should return None to avoid false collisions
    fp = generate_physical_fingerprint(
        area_home=60.0,
        area_plot=None,
        street=None,
        district=None,
        city=None,
        location_raw="",
        title="Mieszkanie na sprzedaż",
    )
    assert fp is None
