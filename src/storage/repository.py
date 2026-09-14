import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import delete, desc, or_, select, update
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.listing import SPATIAL_FIELDS, FilterResult, ListingSchema, apply_if_present

from .database import safe_commit
from .models import ListingModel, PriceHistoryModel


def _val(v: Any) -> str:
    return getattr(v, "value", str(v) if v is not None else "")


def _apply_ai_fields(model: ListingModel, result: FilterResult) -> None:
    """Copy AI Due Diligence fields, keeping saved values when the new result has none."""
    if result.ai_summary:
        model.ai_summary = result.ai_summary
    if result.ai_verdict:
        model.ai_verdict = result.ai_verdict
    if result.worth_interest is not None:
        model.worth_interest = result.worth_interest
    if result.ai_questions:
        model.ai_questions = result.ai_questions
    if result.contact_phone:
        model.contact_phone = result.contact_phone
    if result.contact_person:
        model.contact_person = result.contact_person


def _apply_llm_cache_fields(
    model: ListingModel,
    llm_cache: dict[str, Any] | None,
) -> None:
    if not isinstance(llm_cache, dict):
        return
    if desc_hash := llm_cache.get("desc_hash"):
        model.desc_hash = str(desc_hash)
    if isinstance(llm_json := llm_cache.get("json"), dict):
        model.llm_json_data = llm_json
    if prompt_version := llm_cache.get("prompt_version"):
        model.llm_prompt_version = str(prompt_version)
    if llm_model := llm_cache.get("model"):
        model.llm_model = str(llm_model)


# In-process medians cache (TTL) to avoid a full-table scan every cycle.
_MEDIANS_CACHE: dict[str, float] = {}
_MEDIANS_CACHE_TS: float = 0.0


def clear_medians_cache() -> None:
    global _MEDIANS_CACHE, _MEDIANS_CACHE_TS
    _MEDIANS_CACHE = {}
    _MEDIANS_CACHE_TS = 0.0


class ListingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, listing_id: int) -> ListingModel | None:
        stmt = select(ListingModel).where(ListingModel.id == listing_id)
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def get_by_portal_id(self, portal: str, portal_id: str) -> ListingModel | None:
        stmt = select(ListingModel).where(
            ListingModel.portal == portal,
            ListingModel.portal_id == portal_id,
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def get_by_url(self, url: str) -> ListingModel | None:
        stmt = select(ListingModel).where(ListingModel.url == url)
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def get_fresh_urls(self, portals: list[str], within_hours: int = 24) -> list[str]:
        """URLs of listings with complete, recently scraped detail data."""
        cutoff = datetime.now(UTC) - timedelta(hours=within_hours)
        stmt = select(ListingModel.url).where(
            ListingModel.portal.in_(portals),
            ListingModel.last_scraped_at.isnot(None),
            ListingModel.last_scraped_at >= cutoff,
            ListingModel.raw_description != "",
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def find_duplicate_by_fingerprint(
        self,
        fingerprint: str,
        within_days: int = 45,
    ) -> ListingModel | None:
        """Check if an identical house was already listed (cross-portal/multi-agency deduplication)."""
        cutoff = datetime.now(UTC) - timedelta(days=within_days)
        stmt = (
            select(ListingModel)
            .where(
                ListingModel.property_fingerprint == fingerprint,
                ListingModel.created_at >= cutoff,
            )
            .order_by(desc(ListingModel.created_at))
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def find_relist_by_physical_fingerprint(
        self,
        physical_fingerprint: str,
        exclude_url: str | None = None,
        within_days: int = 365,
    ) -> ListingModel | None:
        """Find previous appearances of the same physical property (re-listings / multi-agency)."""
        cutoff = datetime.now(UTC) - timedelta(days=within_days)
        conditions = [
            ListingModel.physical_fingerprint == physical_fingerprint,
            ListingModel.created_at >= cutoff,
        ]
        if exclude_url:
            conditions.append(ListingModel.url != exclude_url)
        stmt = select(ListingModel).where(*conditions).order_by(ListingModel.created_at.asc())
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def mark_passive_delisted(
        self,
        inactive_days: int = 7,
        profile_id: str | None = None,
    ) -> int:
        """Passively marks listings as DELISTED if not seen on portals within inactive_days."""
        cutoff = datetime.now(UTC) - timedelta(days=inactive_days)
        conditions = [
            ListingModel.listing_status == "ACTIVE",
            ListingModel.last_scraped_at.isnot(None),
            ListingModel.last_scraped_at < cutoff,
        ]
        if profile_id:
            conditions.append(ListingModel.profile_id == profile_id)
        stmt = update(ListingModel).where(*conditions).values(listing_status="DELISTED")
        res = await self.session.execute(stmt)
        return int(getattr(res, "rowcount", 0))

    async def save_or_update(
        self,
        listing: ListingSchema,
        filter_result: FilterResult,
        is_exact_coords: bool = True,
        llm_cache: dict[str, Any] | None = None,
    ) -> tuple[ListingModel, bool, bool]:
        """
        Saves new listing or updates existing.
        Returns (listing_model, is_new, price_changed).
        """
        existing = await self.get_by_portal_id(listing.portal, listing.id)
        if not existing:
            existing = await self.get_by_url(listing.url)

        lat, lon = (None, None)
        if listing.coordinates:
            lat, lon = listing.coordinates

        if existing:
            # Check price change
            price_changed = abs(existing.price - listing.price) >= 1.0
            old_price = existing.price

            existing.title = listing.title
            existing.price = listing.price
            existing.price_per_m2 = listing.price_per_m2
            existing.area_home = listing.area_home
            existing.area_plot = listing.area_plot
            existing.category = _val(listing.category)
            if listing.rooms is not None:
                existing.rooms = listing.rooms
            if listing.floor is not None:
                existing.floor = listing.floor
            if listing.floors_in_building is not None:
                existing.floors_in_building = listing.floors_in_building
            if listing.is_private_owner is not None:
                existing.is_private_owner = listing.is_private_owner
            if listing.profile_id:
                existing.profile_id = listing.profile_id
            if listing.profile_name:
                existing.profile_name = listing.profile_name
            existing.building_type = listing.building_type.value
            existing.segment_subtype = listing.segment_subtype.value
            existing.location_raw = listing.location_raw
            existing.street = listing.street or existing.street
            existing.district = listing.district or existing.district
            existing.city = listing.city or existing.city
            if lat and lon:
                existing.latitude = lat
                existing.longitude = lon
            existing.access_road_type = _val(listing.access_road_type)
            existing.market = _val(listing.market)
            existing.finish_condition = _val(listing.finish_condition)
            # Sticky-True: False from a scraper means "not detected", not
            # "confirmed absent" (e.g. Otodom never detects fiber). Only
            # positive evidence flips the flag to True.
            if listing.has_visualisations:
                existing.has_visualisations = True
            existing.sewerage = _val(listing.sewerage)
            existing.heating = _val(listing.heating)
            if listing.has_fiber:
                existing.has_fiber = True
            if listing.year_built:
                existing.year_built = listing.year_built
            if listing.main_image_url:
                existing.main_image_url = listing.main_image_url
            if listing.gallery_images:
                existing.gallery_images = listing.gallery_images
            if listing.raw_description:
                existing.raw_description = listing.raw_description
            if listing.parcel_id:
                existing.parcel_id = listing.parcel_id
            if listing.cadastral_area:
                existing.cadastral_area = listing.cadastral_area
            if listing.geoportal_url:
                existing.geoportal_url = listing.geoportal_url
            if listing.mpzp_zone:
                existing.mpzp_zone = listing.mpzp_zone
            if listing.mpzp_status:
                existing.mpzp_status = listing.mpzp_status
            if listing.flood_risk_zone:
                existing.flood_risk_zone = listing.flood_risk_zone
            if listing.gesut_networks:
                existing.gesut_networks_data = listing.gesut_networks
            apply_if_present(existing, listing, SPATIAL_FIELDS)

            # Update qualification
            existing.is_qualified = filter_result.is_qualified
            existing.qualification_status = filter_result.status.value
            existing.qualification_score = filter_result.score
            existing.filter_reasons = filter_result.stage1_reasons + filter_result.stage2_reasons
            existing.pros = filter_result.pros
            existing.cons = filter_result.cons
            _apply_ai_fields(existing, filter_result)
            _apply_llm_cache_fields(existing, llm_cache)
            if listing.physical_fingerprint:
                existing.physical_fingerprint = listing.physical_fingerprint
            if listing.relist_count > 0:
                existing.relist_count = max(existing.relist_count or 0, listing.relist_count)
            existing.first_seen_at = listing.first_seen_at or existing.first_seen_at or existing.created_at
            existing.initial_price = existing.initial_price or listing.initial_price or existing.price
            existing.listing_status = "ACTIVE"
            existing.updated_at = datetime.now(UTC)
            existing.last_scraped_at = datetime.now(UTC)

            if price_changed:
                logger.info(
                    f"Price changed for [{existing.portal}] {existing.title}: {old_price:,.0f} -> {listing.price:,.0f} PLN"
                )
                history_entry = PriceHistoryModel(
                    listing_id=existing.id,
                    price=listing.price,
                    price_per_m2=listing.price_per_m2,
                    recorded_at=datetime.now(UTC),
                )
                self.session.add(history_entry)

            await self.session.flush()
            return existing, False, price_changed

        # Brand new listing
        new_model = ListingModel(
            portal=listing.portal,
            portal_id=listing.id,
            url=listing.url,
            property_fingerprint=listing.property_fingerprint or "unknown",
            physical_fingerprint=listing.physical_fingerprint,
            listing_status=listing.listing_status or "ACTIVE",
            first_seen_at=listing.first_seen_at or datetime.now(UTC),
            initial_price=listing.initial_price or listing.price,
            relist_count=listing.relist_count or 0,
            title=listing.title,
            price=listing.price,
            price_per_m2=listing.price_per_m2,
            area_home=listing.area_home,
            area_plot=listing.area_plot,
            category=_val(listing.category),
            rooms=listing.rooms,
            floor=listing.floor,
            floors_in_building=listing.floors_in_building,
            is_private_owner=listing.is_private_owner,
            profile_id=listing.profile_id,
            profile_name=listing.profile_name,
            building_type=listing.building_type.value,
            segment_subtype=listing.segment_subtype.value,
            location_raw=listing.location_raw,
            street=listing.street,
            district=listing.district,
            city=listing.city,
            latitude=lat,
            longitude=lon,
            is_exact_coords=is_exact_coords,
            parcel_id=listing.parcel_id,
            cadastral_area=listing.cadastral_area,
            geoportal_url=listing.geoportal_url,
            mpzp_zone=listing.mpzp_zone,
            mpzp_status=listing.mpzp_status,
            flood_risk_zone=listing.flood_risk_zone,
            landslide_risk=listing.landslide_risk,
            egib_building_status=listing.egib_building_status,
            egib_soil_class=listing.egib_soil_class,
            noise_level_db=listing.noise_level_db,
            noise_zone=listing.noise_zone,
            nature_protected_zone=listing.nature_protected_zone,
            monument_zone=listing.monument_zone,
            cemetery_buffer_zone=listing.cemetery_buffer_zone,
            broadband_status=listing.broadband_status,
            broadband_details=listing.broadband_details,
            parcel_front_width_m=listing.parcel_front_width_m,
            parcel_length_m=listing.parcel_length_m,
            parcel_aspect_ratio=listing.parcel_aspect_ratio,
            parcel_shape_type=listing.parcel_shape_type,
            terrain_slope_pct=listing.terrain_slope_pct,
            terrain_aspect=listing.terrain_aspect,
            walkability_pka_dist_m=listing.walkability_pka_dist_m,
            walkability_pka_name=listing.walkability_pka_name,
            power_lines_risk=listing.power_lines_risk,
            air_aqi=listing.air_aqi,
            air_aqi_label=listing.air_aqi_label,
            air_pm25_heating_avg=listing.air_pm25_heating_avg,
            air_pm25_summer_avg=listing.air_pm25_summer_avg,
            air_smog_days=listing.air_smog_days,
            air_gios_station=listing.air_gios_station,
            air_gios_dist_km=listing.air_gios_dist_km,
            air_gios_index=listing.air_gios_index,
            air_smog_risk=listing.air_smog_risk,
            access_road_type=_val(listing.access_road_type),
            market=_val(listing.market),
            finish_condition=_val(listing.finish_condition),
            has_visualisations=bool(listing.has_visualisations),
            sewerage=_val(listing.sewerage),
            heating=_val(listing.heating),
            has_fiber=bool(listing.has_fiber),
            year_built=listing.year_built,
            raw_description=listing.raw_description,
            main_image_url=listing.main_image_url,
            is_qualified=filter_result.is_qualified,
            qualification_status=filter_result.status.value,
            qualification_score=filter_result.score,
            created_at=listing.created_at,
            updated_at=listing.created_at,
            last_scraped_at=datetime.now(UTC),
        )
        new_model.filter_reasons = filter_result.stage1_reasons + filter_result.stage2_reasons
        new_model.pros = filter_result.pros
        new_model.cons = filter_result.cons
        _apply_ai_fields(new_model, filter_result)
        _apply_llm_cache_fields(new_model, llm_cache)
        if listing.gallery_images:
            new_model.gallery_images = listing.gallery_images
        if listing.gesut_networks:
            new_model.gesut_networks_data = listing.gesut_networks

        self.session.add(new_model)
        try:
            await self.session.flush()
        except sa_exc.IntegrityError:
            # Another concurrent task already inserted this URL — roll back the
            # failed INSERT and fall through to an UPDATE on the winner row.
            await self.session.rollback()
            logger.warning(f"Race condition on INSERT for URL {listing.url!r} — retrying as UPDATE")
            existing = await self.get_by_url(listing.url)
            if existing is None:
                existing = await self.get_by_portal_id(listing.portal, listing.id)
            if existing is None:
                raise  # unexpected — re-raise so the caller sees it

            existing.title = new_model.title
            existing.price = new_model.price
            existing.price_per_m2 = new_model.price_per_m2
            existing.area_home = new_model.area_home
            existing.area_plot = new_model.area_plot
            existing.is_qualified = new_model.is_qualified
            existing.qualification_status = new_model.qualification_status
            existing.qualification_score = new_model.qualification_score
            existing.filter_reasons = filter_result.stage1_reasons + filter_result.stage2_reasons
            existing.pros = filter_result.pros
            existing.cons = filter_result.cons
            _apply_ai_fields(existing, filter_result)
            _apply_llm_cache_fields(existing, llm_cache)
            existing.updated_at = datetime.now(UTC)
            existing.last_scraped_at = datetime.now(UTC)
            await self.session.flush()
            return existing, False, False

        # Add initial price history entry
        initial_history = PriceHistoryModel(
            listing_id=new_model.id,
            price=listing.price,
            price_per_m2=listing.price_per_m2,
            recorded_at=listing.created_at,
        )
        self.session.add(initial_history)
        await self.session.flush()

        return new_model, True, False

    async def mark_as_notified(self, listing_id: int) -> None:
        stmt = select(ListingModel).where(ListingModel.id == listing_id)
        res = await self.session.execute(stmt)
        item = res.scalars().first()
        if item:
            item.notified_at = datetime.now(UTC)
            await self.session.flush()

    async def get_unnotified_qualified(self, limit: int = 50) -> list[ListingModel]:
        stmt = (
            select(ListingModel)
            .where(
                ListingModel.is_qualified.is_(True),
                ListingModel.notified_at.is_(None),
            )
            .order_by(desc(ListingModel.qualification_score), desc(ListingModel.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def update_user_status(self, listing_id: int, status: str) -> ListingModel | None:
        stmt = select(ListingModel).where(ListingModel.id == listing_id)
        res = await self.session.execute(stmt)
        listing = res.scalars().first()
        if listing:
            listing.user_status = status
            listing.updated_at = datetime.now(UTC)
            await self.session.flush()
        return listing

    async def update_user_notes(self, listing_id: int, notes: str | None) -> ListingModel | None:
        stmt = select(ListingModel).where(ListingModel.id == listing_id)
        res = await self.session.execute(stmt)
        listing = res.scalars().first()
        if listing:
            listing.user_notes = notes
            listing.updated_at = datetime.now(UTC)
            await self.session.flush()
        return listing

    async def delete_all_listings(self) -> int:
        """Delete all listings and their price histories (full database reset)."""
        stmt = select(ListingModel.id)
        res = await self.session.execute(stmt)
        listing_ids = list(res.scalars().all())
        count = len(listing_ids)
        if listing_ids:
            await self.session.execute(delete(PriceHistoryModel).where(PriceHistoryModel.listing_id.in_(listing_ids)))
            await self.session.execute(delete(ListingModel).where(ListingModel.id.in_(listing_ids)))
        await safe_commit(self.session)
        logger.warning(f"[ListingRepository] Full reset: deleted {count} listings.")
        return count

    async def delete_by_profile(self, profile_id: str, profile_name: str | None = None) -> int:
        """Delete all listings and their price histories associated with a given profile ID or profile name."""
        conditions = [ListingModel.profile_id == profile_id]
        if profile_name:
            conditions.append(ListingModel.profile_name == profile_name)
        conditions.append(ListingModel.profile_name == profile_id)
        if profile_id == "default":
            conditions.append(ListingModel.profile_id.is_(None))

        stmt = select(ListingModel.id).where(or_(*conditions))
        res = await self.session.execute(stmt)
        listing_ids = list(res.scalars().all())
        if not listing_ids:
            return 0

        # Delete related price histories first
        await self.session.execute(delete(PriceHistoryModel).where(PriceHistoryModel.listing_id.in_(listing_ids)))
        # Delete listings
        await self.session.execute(delete(ListingModel).where(ListingModel.id.in_(listing_ids)))
        await safe_commit(self.session)
        logger.info(f"[ListingRepository] Deleted {len(listing_ids)} listings associated with profile '{profile_id}'")
        return len(listing_ids)

    async def get_market_medians(self, force_refresh: bool = False) -> dict[str, float]:
        """
        Computes median price per m2 aggregated by:
        - city:district:category -> float
        - city::category -> float
        Returns a dictionary mapping composite keys to median price/m2.
        Results are cached in-process for MEDIANS_CACHE_TTL_MINUTES.
        """
        global _MEDIANS_CACHE, _MEDIANS_CACHE_TS
        try:
            from config import settings as _settings

            ttl_min = int(getattr(_settings, "MEDIANS_CACHE_TTL_MINUTES", 30) or 30)
        except Exception:
            ttl_min = 30
        import time as _time

        now = _time.monotonic()
        if not force_refresh and _MEDIANS_CACHE and (now - _MEDIANS_CACHE_TS) < ttl_min * 60:
            return dict(_MEDIANS_CACHE)
        stmt = select(
            ListingModel.city,
            ListingModel.district,
            ListingModel.category,
            ListingModel.price_per_m2,
        ).where(
            ListingModel.price_per_m2 > 0,
            ListingModel.city.isnot(None),
        )
        res = await self.session.execute(stmt)
        rows = res.all()

        district_buckets: dict[str, list[float]] = defaultdict(list)
        city_buckets: dict[str, list[float]] = defaultdict(list)

        for city, district, category, price_m2 in rows:
            city_clean = (city or "").strip().lower()
            dist_clean = (district or "").strip().lower()
            cat_clean = (category or "dom").strip().lower()
            if not city_clean or price_m2 <= 0:
                continue

            val = float(price_m2)
            city_buckets[f"{city_clean}::{cat_clean}"].append(val)
            if dist_clean:
                district_buckets[f"{city_clean}:{dist_clean}:{cat_clean}"].append(val)

        medians: dict[str, float] = {}
        for k, vals in district_buckets.items():
            if vals:
                medians[k] = round(float(statistics.median(vals)), 1)
        for k, vals in city_buckets.items():
            if vals:
                medians[k] = round(float(statistics.median(vals)), 1)

        _MEDIANS_CACHE = dict(medians)
        _MEDIANS_CACHE_TS = now
        return medians
