import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _normalize_defect_entry(d: Any) -> str:
    """Safely normalizes a defect item (string, dict, or primitive) to human-readable string."""
    if not d:
        return ""
    if isinstance(d, str):
        return d.strip()
    if isinstance(d, dict):
        desc = (
            d.get("description")
            or d.get("defect")
            or d.get("defect_type")
            or d.get("wada")
            or d.get("note")
            or d.get("name")
            or d.get("text")
            or ""
        )
        photo = (
            d.get("photo_id")
            if d.get("photo_id") is not None
            else (
                d.get("photo_index")
                if d.get("photo_index") is not None
                else (d.get("image_index") if d.get("image_index") is not None else d.get("image_id"))
            )
        )
        prefix = f"[Zdjęcie {photo}] " if photo is not None else ""
        if desc:
            return f"{prefix}{desc}".strip()
        val_strs = [str(v) for v in d.values() if v is not None and not isinstance(v, (dict, list))]
        return f"{prefix}{' — '.join(val_strs)}".strip() if val_strs else json.dumps(d, ensure_ascii=False)
    return str(d).strip()


class ListingModel(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portal: Mapped[str] = mapped_column(String(50), nullable=False)
    portal_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True, index=True)
    physical_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    listing_status: Mapped[str] = mapped_column(String(30), default="ACTIVE", index=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    initial_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    relist_count: Mapped[int] = mapped_column(Integer, default=0)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    price_per_m2: Mapped[float] = mapped_column(Float, nullable=False)
    area_home: Mapped[float] = mapped_column(Float, nullable=False)
    area_plot: Mapped[float | None] = mapped_column(Float, nullable=True)
    category: Mapped[str] = mapped_column(String(50), default="dom", index=True)
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floors_in_building: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_private_owner: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    profile_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    building_type: Mapped[str] = mapped_column(String(50), default="inny")
    segment_subtype: Mapped[str] = mapped_column(String(50), default="nieokreślony")

    location_raw: Mapped[str] = mapped_column(String(500), default="")
    street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    district: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    access_road_type: Mapped[str] = mapped_column(String(50), default="nieznana")
    market: Mapped[str] = mapped_column(String(50), default="nieokreślony")
    finish_condition: Mapped[str] = mapped_column(String(50), default="nieokreślony")
    has_visualisations: Mapped[bool] = mapped_column(Boolean, default=False)
    sewerage: Mapped[str] = mapped_column(String(50), default="nieznana")
    heating: Mapped[str] = mapped_column(String(50), default="nieznane")
    has_fiber: Mapped[bool] = mapped_column(Boolean, default=False)
    year_built: Mapped[int | None] = mapped_column(Integer, nullable=True)

    raw_description: Mapped[str] = mapped_column(Text, default="")
    main_image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Location precision & Geoportal data
    is_exact_coords: Mapped[bool] = mapped_column(Boolean, default=True)
    parcel_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cadastral_area: Mapped[float | None] = mapped_column(Float, nullable=True)
    geoportal_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mpzp_zone: Mapped[str | None] = mapped_column(String(250), nullable=True)
    mpzp_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    flood_risk_zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gesut_networks: Mapped[str | None] = mapped_column(Text, nullable=True)
    landslide_risk: Mapped[str | None] = mapped_column(String(100), nullable=True)
    egib_building_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    egib_soil_class: Mapped[str | None] = mapped_column(String(100), nullable=True)
    noise_level_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    noise_zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nature_protected_zone: Mapped[str | None] = mapped_column(String(250), nullable=True)
    monument_zone: Mapped[str | None] = mapped_column(String(250), nullable=True)
    cemetery_buffer_zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    broadband_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    broadband_details: Mapped[str | None] = mapped_column(String(250), nullable=True)
    parcel_front_width_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    parcel_length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    parcel_aspect_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    parcel_shape_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    terrain_slope_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    terrain_aspect: Mapped[str | None] = mapped_column(String(50), nullable=True)
    walkability_pka_dist_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    walkability_pka_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    power_lines_risk: Mapped[str | None] = mapped_column(String(150), nullable=True)
    solar_hours_per_year: Mapped[float | None] = mapped_column(Float, nullable=True)
    solar_energy_kwh_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    _poi_counts: Mapped[str | None] = mapped_column("poi_counts", Text, nullable=True)
    _nearest_poi: Mapped[str | None] = mapped_column("nearest_poi", Text, nullable=True)
    geology_formation: Mapped[str | None] = mapped_column(String(250), nullable=True)
    geology_risk_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Air quality & smog intelligence (CAMS + GIOŚ)
    air_aqi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    air_aqi_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    air_pm25_heating_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    air_pm25_summer_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    air_smog_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    air_gios_station: Mapped[str | None] = mapped_column(String(150), nullable=True)
    air_gios_dist_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    air_gios_index: Mapped[str | None] = mapped_column(String(50), nullable=True)
    air_smog_risk: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Extended Intelligence: GUNB (Building Permits)
    _gunb_permits: Mapped[str] = mapped_column("gunb_permits", Text, default="[]")
    _gunb_risk_flags: Mapped[str] = mapped_column("gunb_risk_flags", Text, default="[]")
    gunb_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gunb_status: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Extended Intelligence: Vision AI
    vision_is_render: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vision_finish_condition: Mapped[str | None] = mapped_column(String(50), nullable=True)
    _vision_floorplan_details: Mapped[str] = mapped_column("vision_floorplan_details", Text, default="{}")
    _vision_defects: Mapped[str] = mapped_column("vision_defects", Text, default="[]")
    vision_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    vision_discrepancy_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extended Intelligence: Commute & Pedestrian Safety
    commute_drive_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commute_drive_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    commute_station_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    _commute_custom: Mapped[str] = mapped_column("commute_custom", Text, default="{}")
    pedestrian_sidewalk: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pedestrian_lit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pedestrian_surface: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pedestrian_safety_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extended Intelligence: Developer & KRS Background Check
    developer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    developer_nip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    developer_krs: Mapped[str | None] = mapped_column(String(20), nullable=True)
    developer_capital_pln: Mapped[float | None] = mapped_column(Float, nullable=True)
    developer_registration_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    developer_risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    _developer_risk_reasons: Mapped[str] = mapped_column("developer_risk_reasons", Text, default="[]")

    # CRM User Actions & Status
    user_status: Mapped[str] = mapped_column(String(30), default="NEW", index=True)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    _user_tags: Mapped[str] = mapped_column("user_tags", Text, default="[]")

    # Qualification & Filtering Pipeline status
    is_qualified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    qualification_status: Mapped[str] = mapped_column(String(50), default="NEEDS_REVIEW", index=True)
    qualification_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Serialized JSON lists of analysis insights
    _filter_reasons: Mapped[str] = mapped_column("filter_reasons", Text, default="[]")
    _pros: Mapped[str] = mapped_column("pros", Text, default="[]")
    _cons: Mapped[str] = mapped_column("cons", Text, default="[]")
    _gallery_images: Mapped[str] = mapped_column("gallery_images", Text, default="[]")

    # AI Due Diligence & Contact Info
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    worth_interest: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    _ai_questions: Mapped[str] = mapped_column("ai_questions", Text, default="[]")
    _stakeholder_questions: Mapped[str] = mapped_column("stakeholder_questions", Text, default="{}")
    _documents_to_obtain: Mapped[str] = mapped_column("documents_to_obtain", Text, default="[]")
    _structured_risks: Mapped[str] = mapped_column("structured_risks", Text, default="[]")
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # LLM cache: stable hash of normalized description + raw JSON + prompt/model version
    desc_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    _llm_json: Mapped[str | None] = mapped_column("llm_json", Text, nullable=True)
    llm_prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(150), nullable=True)

    # Dashboard valuation cache: card-used scalars from valuation_engine.evaluate,
    # persisted so /api/listings serves them without recomputing per request.
    # `valuation_version` stamps the inputs (see VALUATION_CACHE_CODE_VERSION);
    # a mismatch means recompute + refresh.
    valuation_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valuation_capex_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation_market_median_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation_price_deviation_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation_price_deviation_adj_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation_days_on_market: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valuation_negotiation_leverage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    valuation_fair_market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation_opening_offer: Mapped[float | None] = mapped_column(Float, nullable=True)

    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    price_history: Mapped[list["PriceHistoryModel"]] = relationship(
        "PriceHistoryModel",
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="PriceHistoryModel.recorded_at.desc()",
    )

    __table_args__ = (
        Index("ix_portal_and_portal_id", "portal", "portal_id"),
        Index("ix_qualification_notified", "is_qualified", "notified_at"),
    )

    @property
    def filter_reasons(self) -> list[str]:
        try:
            return json.loads(self._filter_reasons)
        except Exception:
            return []

    @filter_reasons.setter
    def filter_reasons(self, value: list[str]):
        self._filter_reasons = json.dumps(value or [], ensure_ascii=False)

    @property
    def pros(self) -> list[str]:
        try:
            return json.loads(self._pros)
        except Exception:
            return []

    @pros.setter
    def pros(self, value: list[str]):
        self._pros = json.dumps(value or [], ensure_ascii=False)

    @property
    def cons(self) -> list[str]:
        try:
            return json.loads(self._cons)
        except Exception:
            return []

    @cons.setter
    def cons(self, value: list[str]):
        self._cons = json.dumps(value or [], ensure_ascii=False)

    @property
    def gallery_images(self) -> list[str]:
        try:
            return json.loads(self._gallery_images)
        except Exception:
            return []

    @gallery_images.setter
    def gallery_images(self, value: list[str]):
        self._gallery_images = json.dumps(value or [], ensure_ascii=False)

    @property
    def user_tags(self) -> list[str]:
        try:
            return json.loads(self._user_tags)
        except Exception:
            return []

    @user_tags.setter
    def user_tags(self, value: list[str]):
        self._user_tags = json.dumps(value or [], ensure_ascii=False)

    @property
    def commute_custom(self) -> dict[str, dict[str, float]]:
        try:
            return json.loads(self._commute_custom) if self._commute_custom else {}
        except Exception:
            return {}

    @commute_custom.setter
    def commute_custom(self, value: dict[str, dict[str, float]] | None):
        self._commute_custom = json.dumps(value or {}, ensure_ascii=False)

    @property
    def ai_questions(self) -> list[str]:
        try:
            return json.loads(self._ai_questions)
        except Exception:
            return []

    @ai_questions.setter
    def ai_questions(self, value: list[str]):
        self._ai_questions = json.dumps(value or [], ensure_ascii=False)

    @property
    def stakeholder_questions(self) -> dict[str, list[str]]:
        try:
            return json.loads(self._stakeholder_questions) if self._stakeholder_questions else {}
        except Exception:
            return {}

    @stakeholder_questions.setter
    def stakeholder_questions(self, value: dict[str, list[str]] | None):
        self._stakeholder_questions = json.dumps(value or {}, ensure_ascii=False)

    @property
    def documents_to_obtain(self) -> list[str]:
        try:
            return json.loads(self._documents_to_obtain) if self._documents_to_obtain else []
        except Exception:
            return []

    @documents_to_obtain.setter
    def documents_to_obtain(self, value: list[str] | None):
        self._documents_to_obtain = json.dumps(value or [], ensure_ascii=False)

    @property
    def structured_risks(self) -> list[dict[str, str]]:
        try:
            return json.loads(self._structured_risks) if self._structured_risks else []
        except Exception:
            return []

    @structured_risks.setter
    def structured_risks(self, value: list[dict[str, str]] | None):
        self._structured_risks = json.dumps(value or [], ensure_ascii=False)

    @property
    def poi_counts(self) -> dict[str, int] | None:
        try:
            return json.loads(self._poi_counts) if self._poi_counts else None
        except Exception:
            return None

    @poi_counts.setter
    def poi_counts(self, value: dict[str, int] | None):
        self._poi_counts = json.dumps(value, ensure_ascii=False) if value else None

    @property
    def nearest_poi(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._nearest_poi) if self._nearest_poi else None
        except Exception:
            return None

    @nearest_poi.setter
    def nearest_poi(self, value: dict[str, Any] | None):
        self._nearest_poi = json.dumps(value, ensure_ascii=False) if value else None

    @property
    def gesut_networks_data(self) -> dict | None:
        try:
            return json.loads(self.gesut_networks) if self.gesut_networks else None
        except Exception:
            return None

    @gesut_networks_data.setter
    def gesut_networks_data(self, value: dict | None):
        self.gesut_networks = json.dumps(value, ensure_ascii=False) if value else None

    @property
    def llm_json_data(self) -> dict | None:
        try:
            return json.loads(self._llm_json) if self._llm_json else None
        except Exception:
            return None

    @llm_json_data.setter
    def llm_json_data(self, value: dict | None):
        self._llm_json = json.dumps(value, ensure_ascii=False) if value else None

    @property
    def gunb_permits(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self._gunb_permits) if self._gunb_permits else []
        except Exception:
            return []

    @gunb_permits.setter
    def gunb_permits(self, value: list[dict[str, Any]] | None):
        self._gunb_permits = json.dumps(value or [], ensure_ascii=False)

    @property
    def gunb_risk_flags(self) -> list[str]:
        try:
            return json.loads(self._gunb_risk_flags) if self._gunb_risk_flags else []
        except Exception:
            return []

    @gunb_risk_flags.setter
    def gunb_risk_flags(self, value: list[str] | None):
        self._gunb_risk_flags = json.dumps(value or [], ensure_ascii=False)

    @property
    def vision_floorplan_details(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._vision_floorplan_details) if self._vision_floorplan_details else None
        except Exception:
            return None

    @vision_floorplan_details.setter
    def vision_floorplan_details(self, value: dict[str, Any] | None):
        self._vision_floorplan_details = json.dumps(value or {}, ensure_ascii=False)

    @property
    def vision_defects(self) -> list[str]:
        try:
            raw = json.loads(self._vision_defects) if self._vision_defects else []
            if not isinstance(raw, list):
                raw = [raw]
            res: list[str] = []
            seen: set[str] = set()
            for x in raw:
                norm = _normalize_defect_entry(x)
                if norm and norm not in seen:
                    seen.add(norm)
                    res.append(norm)
            return res
        except Exception:
            return []

    @vision_defects.setter
    def vision_defects(self, value: list[Any] | None):
        res: list[str] = []
        seen: set[str] = set()
        for x in value or []:
            norm = _normalize_defect_entry(x)
            if norm and norm not in seen:
                seen.add(norm)
                res.append(norm)
        self._vision_defects = json.dumps(res, ensure_ascii=False)

    @property
    def developer_risk_reasons(self) -> list[str]:
        try:
            return json.loads(self._developer_risk_reasons) if self._developer_risk_reasons else []
        except Exception:
            return []

    @developer_risk_reasons.setter
    def developer_risk_reasons(self, value: list[str] | None):
        self._developer_risk_reasons = json.dumps(value or [], ensure_ascii=False)


class PriceHistoryModel(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    price_per_m2: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    listing: Mapped["ListingModel"] = relationship("ListingModel", back_populates="price_history")


class GeocacheModel(Base):
    __tablename__ = "geocache"

    query: Mapped[str] = mapped_column(String(300), primary_key=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SpatialCacheModel(Base):
    __tablename__ = "spatial_cache"

    cache_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
