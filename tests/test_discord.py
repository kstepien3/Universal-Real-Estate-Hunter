from src.models.enums import BuildingType, QualificationStatus, RoadType, SegmentSubtype
from src.models.listing import FilterResult, ListingSchema
from src.services.discord_notifier import DiscordNotifier


def test_discord_embed_formatting():
    notifier = DiscordNotifier(webhook_url="https://discord.com/api/webhooks/fake/test")

    listing = ListingSchema(
        id="test-123",
        portal="Otodom",
        title="Wyjątkowy szereg skrajny Słocina Dolna",
        url="https://otodom.pl/oferta/test-123",
        price=1_150_000,
        price_per_m2=8_846,
        area_home=130.0,
        area_plot=380.0,
        building_type=BuildingType.SZEREGOWIEC,
        segment_subtype=SegmentSubtype.SKRAJNY,
        location_raw="Rzeszów, Słocina",
        street="Witolda",
        district="Słocina",
        city="Rzeszów",
        access_road_type=RoadType.ASFALT,
        main_image_url="https://img.example.com/photo.jpg",
        physical_fingerprint="phys_test123",
    )

    filter_result = FilterResult(
        is_qualified=True,
        status=QualificationStatus.QUALIFIED_WHITELIST,
        score=145.0,
        passed_stage1=True,
        passed_stage2=True,
        pros=["Skrajny z dużą działką", "Garaż w bryle", "Dojazd asfaltowy"],
        cons=["Do odświeżenia"],
        is_corner=True,
        has_parking_or_garage=True,
        matched_whitelist_area="Słocina Dolna",
    )

    embed = notifier.format_embed(listing, filter_result)

    assert "Wyjątkowy szereg skrajny" in embed["title"]
    assert embed["url"] == "https://otodom.pl/oferta/test-123"
    assert embed["color"] == DiscordNotifier.COLOR_WHITELIST
    assert "WHITELIST PRIORYTET" in embed["description"]
    assert embed["image"]["url"] == "https://img.example.com/photo.jpg"

    # Check fields
    field_names = [f["name"] for f in embed["fields"]]
    assert any("Cena" in n for n in field_names)
    assert any("Typ budynku" in n for n in field_names)
    assert any("Kluczowe zalety" in n for n in field_names)
    assert any("Wykryte minusy" in n for n in field_names)
