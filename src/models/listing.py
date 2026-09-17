from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    BuildingType,
    FinishCondition,
    HeatingType,
    MarketType,
    PropertyCategory,
    QualificationStatus,
    RoadType,
    SegmentSubtype,
    SewerageType,
)

GEO_FIELDS: tuple[str, ...] = (
    "landslide_risk",
    "egib_building_status",
    "egib_soil_class",
    "noise_level_db",
    "noise_zone",
    "nature_protected_zone",
    "monument_zone",
    "cemetery_buffer_zone",
    "broadband_status",
    "broadband_details",
    "parcel_front_width_m",
    "parcel_length_m",
    "parcel_aspect_ratio",
    "parcel_shape_type",
    "terrain_slope_pct",
    "terrain_aspect",
    "walkability_pka_dist_m",
    "walkability_pka_name",
    "power_lines_risk",
    "solar_hours_per_year",
    "solar_energy_kwh_m2",
    "poi_counts",
    "nearest_poi",
    "geology_formation",
    "geology_risk_note",
)

AIR_FIELDS: tuple[str, ...] = (
    "air_aqi",
    "air_aqi_label",
    "air_pm25_heating_avg",
    "air_pm25_summer_avg",
    "air_smog_days",
    "air_gios_station",
    "air_gios_dist_km",
    "air_gios_index",
    "air_smog_risk",
)

GUNB_FIELDS: tuple[str, ...] = (
    "gunb_permits",
    "gunb_risk_flags",
    "gunb_url",
    "gunb_status",
)

VISION_FIELDS: tuple[str, ...] = (
    "vision_is_render",
    "vision_finish_condition",
    "vision_floorplan_details",
    "vision_defects",
    "vision_summary",
    "vision_discrepancy_note",
)

COMMUTE_FIELDS: tuple[str, ...] = (
    "commute_drive_min",
    "commute_drive_km",
    "commute_station_min",
    "pedestrian_sidewalk",
    "pedestrian_lit",
    "pedestrian_surface",
    "pedestrian_safety_note",
)

DEVELOPER_FIELDS: tuple[str, ...] = (
    "developer_name",
    "developer_nip",
    "developer_krs",
    "developer_capital_pln",
    "developer_registration_year",
    "developer_risk_level",
    "developer_risk_reasons",
)

EXTENDED_INTELLIGENCE_FIELDS: tuple[str, ...] = GUNB_FIELDS + VISION_FIELDS + COMMUTE_FIELDS + DEVELOPER_FIELDS

SPATIAL_FIELDS: tuple[str, ...] = GEO_FIELDS + AIR_FIELDS + EXTENDED_INTELLIGENCE_FIELDS


def apply_if_present(target: Any, source: Any, fields: tuple[str, ...], *, fill_missing: bool = False) -> None:
    """Copy non-None values from ``source`` (dict or object) onto ``target`` (object)."""
    for field in fields:
        value = source.get(field) if isinstance(source, dict) else getattr(source, field, None)
        if value is None:
            continue
        if fill_missing and getattr(target, field, None) is not None:
            continue
        setattr(target, field, value)


def copy_spatial_fields(target: Any, source: Any, *, fill_missing: bool = False) -> None:
    """Consolidated helper to copy all spatial & environmental fields in one call."""
    apply_if_present(target, source, SPATIAL_FIELDS, fill_missing=fill_missing)


_E = TypeVar("_E")


def _enum_or_none(cls: Callable[[Any], _E], val: Any) -> _E | None:
    try:
        return cls(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def restore_cached_details(listing: "ListingSchema", existing_model: Any) -> None:
    """Restore cached description, enums, flags, and spatial fields from a previously saved model."""
    if not listing.raw_description and existing_model.raw_description:
        listing.raw_description = existing_model.raw_description
    if listing.finish_condition == FinishCondition.NIEOKRESLONY and (
        fc := _enum_or_none(FinishCondition, existing_model.finish_condition)
    ):
        listing.finish_condition = fc
    if listing.sewerage == SewerageType.NIEZNANA and (st := _enum_or_none(SewerageType, existing_model.sewerage)):
        listing.sewerage = st
    if listing.heating == HeatingType.NIEZNANE and (ht := _enum_or_none(HeatingType, existing_model.heating)):
        listing.heating = ht
    if not listing.has_fiber:
        listing.has_fiber = bool(existing_model.has_fiber)
    if not listing.has_visualisations:
        listing.has_visualisations = bool(existing_model.has_visualisations)
    if not listing.year_built:
        listing.year_built = existing_model.year_built
    if not listing.coordinates and existing_model.latitude and existing_model.longitude:
        listing.coordinates = (existing_model.latitude, existing_model.longitude)
    if listing.building_type == BuildingType.INNY and (
        bt := _enum_or_none(BuildingType, getattr(existing_model, "building_type", None))
    ):
        listing.building_type = bt
    if listing.access_road_type == RoadType.NIEZNANA and (
        rt := _enum_or_none(RoadType, getattr(existing_model, "access_road_type", None))
    ):
        listing.access_road_type = rt
    if listing.market == MarketType.NIEOKRESLONY and (
        mt := _enum_or_none(MarketType, getattr(existing_model, "market", None))
    ):
        listing.market = mt
    if not listing.parcel_id and existing_model.parcel_id:
        listing.parcel_id = existing_model.parcel_id
        listing.cadastral_area = existing_model.cadastral_area
        listing.geoportal_url = existing_model.geoportal_url
        listing.mpzp_zone = getattr(existing_model, "mpzp_zone", None)
        listing.mpzp_status = getattr(existing_model, "mpzp_status", None)
        listing.flood_risk_zone = getattr(existing_model, "flood_risk_zone", None)
        listing.gesut_networks = getattr(existing_model, "gesut_networks_data", None)
    if not listing.physical_fingerprint and getattr(existing_model, "physical_fingerprint", None):
        listing.physical_fingerprint = existing_model.physical_fingerprint
    if getattr(existing_model, "first_seen_at", None):
        listing.first_seen_at = existing_model.first_seen_at
    if getattr(existing_model, "initial_price", None):
        listing.initial_price = existing_model.initial_price
    if getattr(existing_model, "relist_count", None):
        listing.relist_count = existing_model.relist_count

    copy_spatial_fields(listing, existing_model, fill_missing=True)


class Coordinates(BaseModel):
    latitude: float
    longitude: float


class FilterResult(BaseModel):
    is_qualified: bool
    status: QualificationStatus
    score: float = 0.0
    passed_stage1: bool
    stage1_reasons: list[str] = Field(default_factory=list)
    passed_stage2: bool
    stage2_reasons: list[str] = Field(default_factory=list)
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    is_corner: bool = False
    has_parking_or_garage: bool = False
    matched_whitelist_area: str | None = None
    finish_condition: FinishCondition = FinishCondition.NIEOKRESLONY
    has_visualisations: bool = False
    sewerage: SewerageType = SewerageType.NIEZNANA
    heating: HeatingType = HeatingType.NIEZNANE
    has_fiber: bool = False
    # AI Due Diligence
    ai_summary: str | None = None
    ai_verdict: str | None = None
    worth_interest: bool | None = None
    ai_questions: list[str] = Field(default_factory=list)
    contact_phone: str | None = None
    contact_person: str | None = None
    # Stakeholder questions & document checklist
    stakeholder_questions: dict[str, list[str]] = Field(default_factory=dict)
    documents_to_obtain: list[str] = Field(default_factory=list)
    structured_risks: list[dict[str, str]] = Field(default_factory=list)
    # Spatial due diligence
    mpzp_zone: str | None = None
    flood_risk_zone: str | None = None
    landslide_risk: str | None = None
    egib_building_status: str | None = None
    egib_soil_class: str | None = None
    noise_level_db: float | None = None
    noise_zone: str | None = None
    nature_protected_zone: str | None = None
    monument_zone: str | None = None
    cemetery_buffer_zone: str | None = None
    broadband_status: str | None = None
    broadband_details: str | None = None
    parcel_front_width_m: float | None = None
    parcel_length_m: float | None = None
    parcel_aspect_ratio: float | None = None
    parcel_shape_type: str | None = None
    terrain_slope_pct: float | None = None
    terrain_aspect: str | None = None
    walkability_pka_dist_m: int | None = None
    walkability_pka_name: str | None = None
    power_lines_risk: str | None = None
    solar_hours_per_year: float | None = None
    solar_energy_kwh_m2: float | None = None
    poi_counts: dict[str, int] | None = None
    nearest_poi: dict[str, Any] | None = None
    geology_formation: str | None = None
    geology_risk_note: str | None = None
    # Air quality & smog intelligence (CAMS + GIOŚ)
    air_aqi: int | None = None
    air_aqi_label: str | None = None
    air_pm25_heating_avg: float | None = None
    air_pm25_summer_avg: float | None = None
    air_smog_days: int | None = None
    air_gios_station: str | None = None
    air_gios_dist_km: float | None = None
    air_gios_index: str | None = None
    air_smog_risk: str | None = None
    # Extended Intelligence: GUNB (Building Permits)
    gunb_permits: list[dict[str, Any]] | None = None
    gunb_risk_flags: list[str] | None = None
    gunb_url: str | None = None
    gunb_status: str | None = None
    # Extended Intelligence: Vision AI (Living Quarters & Renders)
    vision_is_render: bool | None = None
    vision_finish_condition: str | None = None
    vision_floorplan_details: dict[str, Any] | None = None
    vision_defects: list[str] | None = None
    vision_summary: str | None = None
    vision_discrepancy_note: str | None = None
    # Extended Intelligence: Commute & Pedestrian Safety
    commute_drive_min: int | None = None
    commute_drive_km: float | None = None
    commute_station_min: int | None = None
    pedestrian_sidewalk: bool | None = None
    pedestrian_lit: bool | None = None
    pedestrian_surface: str | None = None
    pedestrian_safety_note: str | None = None
    # Extended Intelligence: Developer & KRS Background Check
    developer_name: str | None = None
    developer_nip: str | None = None
    developer_krs: str | None = None
    developer_capital_pln: float | None = None
    developer_registration_year: int | None = None
    developer_risk_level: str | None = None
    developer_risk_reasons: list[str] | None = None

    @property
    def verdict_icon(self) -> str:
        if self.worth_interest is True:
            return "✅"
        if self.worth_interest is False:
            return "❌"
        return "❓"


class ListingSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    portal: str
    title: str
    url: str
    price: float
    price_per_m2: float
    area_home: float
    area_plot: float | None = None
    building_type: BuildingType = BuildingType.INNY
    segment_subtype: SegmentSubtype = SegmentSubtype.NIEOKRESLONY
    location_raw: str
    street: str | None = None
    district: str | None = None
    city: str | None = None
    coordinates: tuple[float, float] | None = None
    access_road_type: RoadType = RoadType.NIEZNANA
    market: MarketType = MarketType.NIEOKRESLONY
    finish_condition: FinishCondition = FinishCondition.NIEOKRESLONY
    has_visualisations: bool = False
    sewerage: SewerageType = SewerageType.NIEZNANA
    heating: HeatingType = HeatingType.NIEZNANE
    has_fiber: bool = False
    category: PropertyCategory = PropertyCategory.DOM
    rooms: int | None = None
    floor: int | None = None
    floors_in_building: int | None = None
    is_private_owner: bool | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    year_built: int | None = None
    raw_description: str = ""
    main_image_url: str | None = None
    gallery_images: list[str] = Field(default_factory=list)
    physical_fingerprint: str | None = None
    listing_status: str = "ACTIVE"
    first_seen_at: datetime | None = None
    initial_price: float | None = None
    relist_count: int = 0
    parcel_id: str | None = None
    cadastral_area: float | None = None
    geoportal_url: str | None = None
    mpzp_zone: str | None = None
    mpzp_status: str | None = None
    flood_risk_zone: str | None = None
    gesut_networks: dict[str, Any] | None = None
    landslide_risk: str | None = None
    egib_building_status: str | None = None
    egib_soil_class: str | None = None
    noise_level_db: float | None = None
    noise_zone: str | None = None
    nature_protected_zone: str | None = None
    monument_zone: str | None = None
    cemetery_buffer_zone: str | None = None
    broadband_status: str | None = None
    broadband_details: str | None = None
    parcel_front_width_m: float | None = None
    parcel_length_m: float | None = None
    parcel_aspect_ratio: float | None = None
    parcel_shape_type: str | None = None
    terrain_slope_pct: float | None = None
    terrain_aspect: str | None = None
    walkability_pka_dist_m: int | None = None
    walkability_pka_name: str | None = None
    power_lines_risk: str | None = None
    solar_hours_per_year: float | None = None
    solar_energy_kwh_m2: float | None = None
    poi_counts: dict[str, int] | None = None
    nearest_poi: dict[str, Any] | None = None
    geology_formation: str | None = None
    geology_risk_note: str | None = None
    # Stakeholder questions & document checklist
    stakeholder_questions: dict[str, list[str]] = Field(default_factory=dict)
    documents_to_obtain: list[str] = Field(default_factory=list)
    structured_risks: list[dict[str, str]] = Field(default_factory=list)
    # Air quality & smog intelligence (CAMS + GIOŚ)
    air_aqi: int | None = None
    air_aqi_label: str | None = None
    air_pm25_heating_avg: float | None = None
    air_pm25_summer_avg: float | None = None
    air_smog_days: int | None = None
    air_gios_station: str | None = None
    air_gios_dist_km: float | None = None
    air_gios_index: str | None = None
    air_smog_risk: str | None = None
    # Extended Intelligence: GUNB (Building Permits)
    gunb_permits: list[dict[str, Any]] | None = None
    gunb_risk_flags: list[str] | None = None
    gunb_url: str | None = None
    gunb_status: str | None = None
    # Extended Intelligence: Vision AI (Living Quarters & Renders)
    vision_is_render: bool | None = None
    vision_finish_condition: str | None = None
    vision_floorplan_details: dict[str, Any] | None = None
    vision_defects: list[str] | None = None
    vision_summary: str | None = None
    vision_discrepancy_note: str | None = None
    # Extended Intelligence: Commute & Pedestrian Safety
    commute_drive_min: int | None = None
    commute_drive_km: float | None = None
    commute_station_min: int | None = None
    pedestrian_sidewalk: bool | None = None
    pedestrian_lit: bool | None = None
    pedestrian_surface: str | None = None
    pedestrian_safety_note: str | None = None
    # Extended Intelligence: Developer & KRS Background Check
    developer_name: str | None = None
    developer_nip: str | None = None
    developer_krs: str | None = None
    developer_capital_pln: float | None = None
    developer_registration_year: int | None = None
    developer_risk_level: str | None = None
    developer_risk_reasons: list[str] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    skip_detail: bool = False

    @field_validator("price", "price_per_m2", "area_home", mode="before")
    @classmethod
    def parse_float_values(cls, v):
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            cleaned = (
                v.replace("zł", "")
                .replace("PLN", "")
                .replace("m²", "")
                .replace("m2", "")
                .replace(" ", "")
                .replace("\xa0", "")
                .replace(",", ".")
                .strip()
            )
            try:
                return float(cleaned)
            except ValueError:
                return 0.0
        return 0.0

    @field_validator("area_plot", mode="before")
    @classmethod
    def parse_area_plot(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v) if v > 0 else None
        if isinstance(v, str):
            cleaned = (
                v.replace("m²", "")
                .replace("m2", "")
                .replace("ar", "")
                .replace(" ", "")
                .replace("\xa0", "")
                .replace(",", ".")
                .strip()
            )
            try:
                val = float(cleaned)
                return val if val > 0 else None
            except ValueError:
                return None
        return None


class RawListing(BaseModel):
    """Raw intermediate schema before normalization."""

    portal: str
    portal_id: str
    title: str
    url: str
    price_val: float | None = None
    price_per_m2_val: float | None = None
    area_home_val: float | None = None
    area_plot_val: float | None = None
    location_str: str = ""
    street_str: str | None = None
    district_str: str | None = None
    city_str: str | None = None
    lat: float | None = None
    lon: float | None = None
    building_type_str: str | None = None
    access_road_str: str | None = None
    market_str: str | None = None
    finish_condition_str: str | None = None
    has_visualisations_val: bool | None = None
    sewerage_str: str | None = None
    heating_str: str | None = None
    has_fiber_val: bool | None = None
    year_built_val: int | None = None
    description_html: str = ""
    images: list[str] = Field(default_factory=list)
    created_at_dt: datetime | None = None
