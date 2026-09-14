import json
from datetime import UTC, datetime

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


class ListingModel(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portal: Mapped[str] = mapped_column(String(50), nullable=False)
    portal_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True, index=True)
    property_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

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

    # CRM User Actions & Status
    user_status: Mapped[str] = mapped_column(String(30), default="NEW", index=True)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # LLM cache: stable hash of normalized description + raw JSON + prompt/model version
    desc_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    _llm_json: Mapped[str | None] = mapped_column("llm_json", Text, nullable=True)
    llm_prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(150), nullable=True)

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
    def ai_questions(self) -> list[str]:
        try:
            return json.loads(self._ai_questions)
        except Exception:
            return []

    @ai_questions.setter
    def ai_questions(self, value: list[str]):
        self._ai_questions = json.dumps(value or [], ensure_ascii=False)

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
