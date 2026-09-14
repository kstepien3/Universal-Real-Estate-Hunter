from datetime import UTC, datetime
from typing import Any

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

SPATIAL_FIELDS: tuple[str, ...] = GEO_FIELDS + AIR_FIELDS


def apply_if_present(target: Any, source: Any, fields: tuple[str, ...], *, fill_missing: bool = False) -> None:
    """Copy non-None values from ``source`` (dict or object) onto ``target`` (object)."""
    for field in fields:
        value = source.get(field) if isinstance(source, dict) else getattr(source, field, None)
        if value is None:
            continue
        if fill_missing and getattr(target, field, None) is not None:
            continue
        setattr(target, field, value)


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
    property_fingerprint: str | None = None
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
