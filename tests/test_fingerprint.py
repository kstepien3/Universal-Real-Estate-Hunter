from src.filters.fingerprint import generate_property_fingerprint


def test_property_fingerprint_tolerance():
    # Listing 1 from Agency A: 1,190,000 PLN, 120m2, plot 350m2, Paderewskiego
    fp1 = generate_property_fingerprint(
        price=1_190_000,
        area_home=120.0,
        area_plot=350.0,
        street="Paderewskiego",
        title="Dom szeregowy skrajny na sprzedaż",
    )

    # Listing 2 from Agency B for the SAME house:
    # Slightly rounded area: 121.2 m2 (+/- 2m2)
    # Slightly rounded plot: 354 m2 (+/- 10m2)
    # Slightly different title and price: 1,194,000 PLN (rounds to 1.19M bucket)
    fp2 = generate_property_fingerprint(
        price=1_194_000,
        area_home=121.2,
        area_plot=354.0,
        street="ul. Ignacego Paderewskiego",
        title="Okazja! Piękny szereg skrajny Paderewskiego",
    )

    assert fp1 == fp2, f"Expected identical fingerprint for duplicate offer, got {fp1} vs {fp2}"


def test_property_fingerprint_differentiation():
    # House on Paderewskiego
    fp1 = generate_property_fingerprint(
        price=1_190_000,
        area_home=120.0,
        area_plot=350.0,
        street="Paderewskiego",
    )

    # Completely different house on Lubelska with different size and price
    fp2 = generate_property_fingerprint(
        price=950_000,
        area_home=95.0,
        area_plot=200.0,
        street="Lubelska",
    )

    assert fp1 != fp2, "Different houses must have distinct fingerprints"


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


def test_physical_fingerprint_differentiation():
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
