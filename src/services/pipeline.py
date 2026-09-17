import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select

from config import settings
from src.filters.fingerprint import generate_physical_fingerprint
from src.models.listing import (
    AIR_FIELDS,
    GEO_FIELDS,
    ListingSchema,
    apply_if_present,
    copy_spatial_fields,
    restore_cached_details,
)
from src.scrapers import BaseScraper, MorizonScraper, NieruchomosciOnlineScraper, OLXScraper, OtodomScraper
from src.services.config_manager import SearchProfile
from src.services.discord_notifier import DiscordNotifier
from src.services.market_analyzer import valuation_engine
from src.services.progress import global_tracker
from src.services.telegram_notifier import TelegramNotifier
from src.storage import ListingModel, ListingRepository, get_session, init_db, safe_commit


def _should_notify(*, is_qualified: bool, is_new: bool, price_changed: bool) -> bool:
    return is_qualified and (is_new or price_changed)


async def audit_and_apply_spatial_data(target: Any) -> dict[str, Any] | None:
    """Audit location via Geoportal and Air Quality, mapping returned fields onto target.

    Supports both ListingSchema and ListingModel objects.
    Returns the geo_audit dict if successful, or None.
    """
    coords = getattr(target, "coordinates", None)
    if not coords and getattr(target, "latitude", None) is not None and getattr(target, "longitude", None) is not None:
        coords = (target.latitude, target.longitude)
    if not coords:
        return None

    from src.services.air_quality import air_quality_service
    from src.services.commute import commute_service
    from src.services.developer_verifier import developer_verifier
    from src.services.geoportal import geoportal_service
    from src.services.gunb import gunb_service

    category = getattr(target, "category", "dom")
    if hasattr(category, "value"):
        category = category.value

    city = getattr(target, "city", None)
    raw_desc = getattr(target, "raw_description", "") or ""

    geo_coro = geoportal_service.audit_location(
        coords[0],
        coords[1],
        radius_meters=120,
        category=category or "dom",
    )
    aq_coro = air_quality_service.get_air_quality_audit(
        coords[0],
        coords[1],
    )
    commute_coro = commute_service.audit_commute_and_pedestrian(
        lat=coords[0],
        lon=coords[1],
        city=city,
    )
    dev_coro = developer_verifier.audit_developer(
        description=raw_desc,
    )

    geo_res: Any
    aq_res: Any
    commute_res: Any
    dev_res: Any
    geo_res, aq_res, commute_res, dev_res = await asyncio.gather(
        geo_coro, aq_coro, commute_coro, dev_coro, return_exceptions=True
    )

    geo_audit: dict[str, Any] | None = None
    if isinstance(geo_res, dict):
        geo_audit = geo_res
        main_pid = geo_audit.get("main_parcel_id")
        if main_pid:
            if not getattr(target, "parcel_id", None):
                target.parcel_id = main_pid
            target.cadastral_area = geo_audit.get("cadastral_area")
            target.geoportal_url = geo_audit.get("geoportal_url")
            target.mpzp_zone = geo_audit.get("mpzp_zone")
            target.mpzp_status = geo_audit.get("mpzp_status")
            target.flood_risk_zone = geo_audit.get("flood_risk_zone")

        apply_if_present(target, geo_audit, GEO_FIELDS)

        if (v := geo_audit.get("gesut_networks")) is not None:
            if hasattr(target, "gesut_networks"):
                target.gesut_networks = v
            if hasattr(target, "gesut_networks_data"):
                target.gesut_networks_data = v

        # Audit GUNB building permits around detected cadastral centroid (200 m radius)
        cx, cy = geo_audit.get("centroid") or (None, None)
        try:
            gunb_res = await gunb_service.audit_gunb_permits(
                cx=cx,
                cy=cy,
                parcel_id=main_pid or getattr(target, "parcel_id", None),
                radius_m=200.0,
            )
            if isinstance(gunb_res, dict):
                from src.models.listing import GUNB_FIELDS

                apply_if_present(target, gunb_res, GUNB_FIELDS)
        except Exception as e:
            logger.debug(f"[Pipeline] GUNB audit note: {e}")

    if isinstance(aq_res, dict):
        apply_if_present(target, aq_res, AIR_FIELDS)
        if hasattr(target, "air_smog_risk") and getattr(target, "air_smog_risk", None) is None:
            target.air_smog_risk = "NIEZNANE"

    if isinstance(commute_res, dict):
        from src.models.listing import COMMUTE_FIELDS

        apply_if_present(target, commute_res, COMMUTE_FIELDS)

    if isinstance(dev_res, dict):
        from src.models.listing import DEVELOPER_FIELDS

        apply_if_present(target, dev_res, DEVELOPER_FIELDS)

    # Multimodal Vision AI audit (needs no coordinates — photos only).
    await audit_vision_data(target)

    return geo_audit


async def audit_vision_data(target: Any) -> dict[str, Any] | None:
    """Vision AI audit of listing photos (Living Quarters verification).

    Coordinate-independent: runs on gallery/main images alone. Gracefully
    degrades to NIEZNANY when offline, keyless, or imageless.
    Returns the vision result dict, or None when skipped.
    """
    try:
        from src.filters.vision_analyzer import declared_finish_label, vision_analyzer

        gallery = list(getattr(target, "gallery_images", None) or [])
        main_img = getattr(target, "main_image_url", None)
        if main_img and main_img not in gallery:
            gallery = [main_img, *gallery]
        if not gallery:
            return None
        if getattr(target, "vision_finish_condition", None) is not None:
            return None
        import httpx

        async with httpx.AsyncClient() as vision_client:
            vision_res = await vision_analyzer.audit_images(
                vision_client,
                image_urls=gallery[:6],
                declared_finish=declared_finish_label(target),
            )
        if isinstance(vision_res, dict):
            from src.models.listing import VISION_FIELDS

            vision_payload = {
                "vision_is_render": vision_res.get("vision_is_render"),
                "vision_finish_condition": vision_res.get("vision_finish_condition"),
                "vision_floorplan_details": vision_res.get("vision_floorplan_details"),
                "vision_defects": vision_res.get("vision_defects"),
            }
            apply_if_present(target, vision_payload, VISION_FIELDS)
            return vision_res
    except Exception as e:
        logger.debug(f"[Pipeline] Vision audit note: {e}")
    return None


def _log_spatial_summary(target: Any, prefix: str | None = None) -> None:
    parts = []
    pid = getattr(target, "parcel_id", None)
    if pid:
        parts.append(f"działka {pid.rsplit('.', 1)[-1]}")
    bb = getattr(target, "broadband_status", None)
    if bb:
        parts.append(f"FTTH: {bb}")
    slope = getattr(target, "terrain_slope_pct", None)
    if slope is not None:
        parts.append(f"stok: {slope:.1f}%")
    pka = getattr(target, "walkability_pka_name", None)
    if pka:
        parts.append(f"PKA: {pka}")
    aqi = getattr(target, "air_aqi", None)
    if aqi is not None:
        parts.append(f"AQI: {aqi}")
    pm25 = getattr(target, "air_pm25_heating_avg", None)
    if pm25 is not None:
        parts.append(f"PM2.5 zima: {pm25:.0f} µg/m³")

    title = getattr(target, "title", "") or ""
    header = prefix or f"🏛️ [Rejestry] {title[:25]}"
    if parts:
        global_tracker.add_log(
            f"{header}: " + " | ".join(parts),
            level="info",
            category="geo",
        )


class ScraperPipeline:
    """
    End-to-end analytical pipeline:
    1. Scrapes targets (Otodom, OLX, Nieruchomosci-online).
    2. Broadcasts live progress to terminal and web dashboard.
    3. Detects re-listings and multi-agency duplicates via physical_fingerprint.
    4. Executes Two-Stage filtration and semantic qualification.
    5. Persists data, price history, and qualification status via SQLAlchemy 2.0.
    6. Dispatches notifications for new matching offers and price drops.
    """

    def __init__(
        self,
        scrapers: list[BaseScraper] | None = None,
        discord_notifier: DiscordNotifier | None = None,
        telegram_notifier: TelegramNotifier | None = None,
    ):
        self._custom_scrapers = scrapers is not None
        self.scrapers = scrapers or [
            OtodomScraper(max_pages=2),
            OLXScraper(max_pages=1),
            NieruchomosciOnlineScraper(max_pages=1),
            MorizonScraper(max_pages=1),
        ]
        self.discord = discord_notifier or DiscordNotifier()
        self.telegram = telegram_notifier or TelegramNotifier()
        self.llm_analysis_enabled = settings.USE_LLM_ANALYSIS
        from src.filters import QualificationEngine

        self.engine = QualificationEngine(llm_enabled=self.llm_analysis_enabled)

    async def process_listing(
        self,
        listing: ListingSchema,
        repo: ListingRepository,
        profile: Any | None = None,
        market_medians: dict[str, float] | None = None,
        defer_save: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "is_new": False,
            "price_changed": False,
            "qualified": False,
            "notified": False,
            "llm_skipped": False,
            "llm_skip_reason": None,
        }

        if profile:
            if not listing.profile_id:
                listing.profile_id = profile.id
            if not listing.profile_name:
                listing.profile_name = profile.name

        # 1. Look up existing record in database
        existing_model = await repo.get_by_url(listing.url) or await repo.get_by_portal_id(listing.portal, listing.id)

        # 1.1 Compute physical fingerprint & check duplicate / re-listing
        if not listing.physical_fingerprint:
            listing.physical_fingerprint = generate_physical_fingerprint(
                area_home=listing.area_home,
                area_plot=listing.area_plot,
                rooms=listing.rooms,
                street=listing.street,
                district=listing.district,
                city=listing.city,
                location_raw=listing.location_raw,
                title=listing.title,
                category=listing.category,
            )

        if listing.physical_fingerprint and not existing_model:
            relist_model = await repo.find_relist_by_physical_fingerprint(
                listing.physical_fingerprint,
                exclude_url=listing.url,
            )
            if relist_model:
                listing.first_seen_at = relist_model.first_seen_at or relist_model.created_at
                listing.initial_price = relist_model.initial_price or relist_model.price
                listing.relist_count = (relist_model.relist_count or 0) + 1
                listing.listing_status = "RELISTED"
                logger.info(
                    f"[Pipeline] 🔁 Wykryto re-listing dla '{listing.title[:40]}' "
                    f"(pierwotnie ID #{relist_model.id} z {relist_model.portal}, "
                    f"pierwotna cena {listing.initial_price:,.0f} zł)."
                )
                global_tracker.add_log(
                    f"🔁 [Re-listing] {listing.title[:30]}: powrót #{relist_model.id} ({relist_model.portal}), "
                    f"pierwotnie {listing.initial_price:,.0f} zł",
                    level="warning",
                    category="info",
                )

        # 1.2 Restore stored detail data when detail was skipped or already cached in DB
        if existing_model:
            restore_cached_details(listing, existing_model)

        # 1.5. Stage 1 pre-check & Geoportal spatial audit before LLM
        is_exact_coords = True
        try:
            s1_res = self.engine.precheck_stage1(listing, profile=profile)
            stage1_passed = bool(s1_res[0])
        except Exception as e:
            logger.debug(f"[Pipeline] Stage 1 pre-check error: {e}")
            stage1_passed = True

        geo_audit = None
        if stage1_passed:
            # Resolve coordinates if missing (skip if already resolved in existing_model)
            if not listing.coordinates:
                from src.services.geocoder import geocoder

                lat, lon, is_exact = await geocoder.geocode(
                    session=repo.session,
                    street=listing.street,
                    district=listing.district,
                    city=listing.city,
                    location_raw=listing.location_raw,
                )
                if lat and lon:
                    listing.coordinates = (lat, lon)
                    is_exact_coords = is_exact
                    acc_tag = "precyzyjny punkt" if is_exact else "centroid / rejon"
                    global_tracker.add_log(
                        f"📍 [Geokoder] {listing.title[:30]}: ({lat:.4f}, {lon:.4f}) [{acc_tag}]",
                        level="info",
                        category="geo",
                    )
            elif existing_model and existing_model.is_exact_coords is not None:
                is_exact_coords = bool(existing_model.is_exact_coords)

            # Audit location in Geoportal if coords are exact and spatial metrics missing
            # (extended intelligence fields included so legacy rows get GUNB/Vision/Commute/KRS backfilled)
            needs_spatial_audit = (
                not listing.parcel_id
                or getattr(listing, "broadband_status", None) is None
                or getattr(listing, "parcel_front_width_m", None) is None
                or getattr(listing, "terrain_slope_pct", None) is None
                or getattr(listing, "air_pm25_heating_avg", None) is None
                or getattr(listing, "gunb_status", None) is None
                or getattr(listing, "commute_drive_min", None) is None
                or getattr(listing, "developer_risk_level", None) is None
                or (
                    bool(getattr(listing, "gallery_images", None) or getattr(listing, "main_image_url", None))
                    and getattr(listing, "vision_finish_condition", None) is None
                )
            )
            if listing.coordinates and is_exact_coords and needs_spatial_audit:
                try:
                    geo_audit = await audit_and_apply_spatial_data(listing)
                    if geo_audit:
                        _log_spatial_summary(listing)
                except Exception as e:
                    logger.debug(f"[Pipeline] Geoportal/AirQuality audit skipped: {e}")
            else:
                # Vision needs no coordinates: audit photos even when location is approximate
                # (audit_vision_data no-ops without gallery or with vision already set).
                try:
                    await audit_vision_data(listing)
                except Exception as e:
                    logger.debug(f"[Pipeline] Standalone vision audit skipped: {e}")

        # Check if LLM can be skipped because this listing was already analyzed.
        # Uses stable desc_hash (normalized text) + prompt version instead of raw
        # strip() comparison, so formatting-only edits do not burn LLM calls, while
        # a prompt upgrade forces exactly one re-audit per listing.
        from src.filters.fingerprint import compute_desc_hash

        try:
            prompt_version = str(getattr(settings, "LLM_PROMPT_VERSION", "v1.0") or "v1.0")
        except Exception:
            prompt_version = "v1.0"
        current_desc_hash = compute_desc_hash(listing.raw_description)
        skip_llm = not self.llm_analysis_enabled
        if self.llm_analysis_enabled and existing_model and existing_model.qualification_status:
            cached = getattr(existing_model, "llm_json_data", None)
            has_ai = bool(cached or existing_model.ai_summary or existing_model.ai_questions)
            hash_match = bool(
                current_desc_hash
                and getattr(existing_model, "desc_hash", None) == current_desc_hash
                and getattr(existing_model, "llm_prompt_version", None) == prompt_version
                and has_ai
            )
            if not hash_match and getattr(existing_model, "desc_hash", None) is None and has_ai:
                # Legacy rows saved before desc_hash existed: fall back to
                # the previous strip() comparison so we don't re-bill them.
                desc_unchanged = not listing.raw_description or (
                    existing_model.raw_description
                    and listing.raw_description.strip() == existing_model.raw_description.strip()
                )
                hash_match = bool(desc_unchanged)
            if hash_match:
                skip_llm = True
        result["llm_skipped"] = skip_llm

        # 2. Run two-stage qualification engine (LLM now receives spatial context in listing!)
        filter_result = await self.engine.evaluate_listing(
            listing, profile=profile, skip_llm=skip_llm, geo_audit=geo_audit
        )
        # The engine may fail to get an answer from the provider — propagate that
        # so cycle summaries stay honest.
        engine_skip_reason = getattr(self.engine, "last_skip_reason", None)
        if engine_skip_reason == "provider_error":
            result["llm_skipped"] = True
            result["llm_skip_reason"] = engine_skip_reason
        last_json = getattr(self.engine, "last_llm_json", None)
        estimate_fn = getattr(self.engine, "estimate_tokens", None)
        if not skip_llm and isinstance(last_json, dict):
            result["llm_model"] = getattr(self.engine, "last_llm_model", None)
            result["llm_prompt_version"] = getattr(self.engine, "last_llm_prompt_version", None)
            result["llm_json"] = last_json
            result["desc_hash"] = current_desc_hash
            if callable(estimate_fn):
                try:
                    result["llm_tokens_est"] = estimate_fn(listing.raw_description)
                except Exception:
                    pass

        # If LLM was skipped, preserve previously saved LLM pros/cons and AI fields
        if skip_llm and existing_model:
            if existing_model.pros:
                for p in existing_model.pros:
                    if p.startswith("[LLM]") and p not in filter_result.pros:
                        filter_result.pros.append(p)
            if existing_model.cons:
                for c in existing_model.cons:
                    if (
                        c.startswith("[LLM]")
                        or c.startswith("🔧 [LLM]")
                        or c.startswith("🖼️ [LLM]")
                        or c.startswith("🔍 [LLM]")
                        or c.startswith("⚠️ [Ukryty koszt]")
                        or c.startswith("⚖️ [Ryzyko prawne]")
                    ) and c not in filter_result.cons:
                        filter_result.cons.append(c)
            # Preserve AI Due Diligence fields for the in-memory result used by notifiers
            for field in ("ai_summary", "ai_verdict", "ai_questions", "contact_phone", "contact_person"):
                if not getattr(filter_result, field) and getattr(existing_model, field, None):
                    setattr(filter_result, field, getattr(existing_model, field))
            if filter_result.worth_interest is None and getattr(existing_model, "worth_interest", None) is not None:
                filter_result.worth_interest = existing_model.worth_interest

        # Apply spatial findings if not already reflected on qualified result
        # (e.g. when engine is mocked in tests or evaluate_listing was bypassed)
        if filter_result.is_qualified and not filter_result.mpzp_zone and (geo_audit or listing.mpzp_zone):
            new_score, new_pros, new_cons = self.engine.apply_spatial_findings(
                listing=listing,
                score=filter_result.score,
                pros=filter_result.pros,
                cons=filter_result.cons,
                geo_audit=geo_audit,
            )
            filter_result.score = min(100.0, max(0.0, new_score))
            filter_result.pros = new_pros
            filter_result.cons = new_cons
            copy_spatial_fields(filter_result, listing)
            filter_result.mpzp_zone = listing.mpzp_zone
            filter_result.flood_risk_zone = listing.flood_risk_zone

        result["qualified"] = filter_result.is_qualified

        if not filter_result.is_qualified:
            reasons = filter_result.stage1_reasons + filter_result.stage2_reasons
            if reasons:
                reason_str = reasons[0]
                if len(reasons) > 1:
                    reason_str += f" (+{len(reasons) - 1} więcej)"
            else:
                reason_str = "Odrzucono przez reguły"
            global_tracker.add_log(
                f"❌ [Odrzucono] {listing.title[:60]}: {reason_str}",
                level="info",
                category="rejected",
            )
            logger.info(f"[Pipeline] Odrzucono '{listing.title[:60]}': {reason_str}")
        else:
            global_tracker.add_log(
                f"⭐ [Zakwalifikowano] {listing.title[:35]} ({listing.price:,.0f} zł) — Wynik: {filter_result.score:.0f} pkt",
                level="success",
                category="success",
            )
            logger.info(
                f"[Pipeline] Zakwalifikowano '{listing.title[:40]}' ({listing.price:,.0f} zł, {filter_result.score:.0f} pkt)"
            )

        # 4. Save or update in database (deferred in batch mode — see _persist_batch,
        # which persists the whole batch with a single commit instead of one per listing)
        llm_bundle = None
        if result.get("llm_json") and current_desc_hash:
            llm_bundle = {
                "desc_hash": current_desc_hash,
                "json": result.get("llm_json"),
                "prompt_version": result.get("llm_prompt_version"),
                "model": result.get("llm_model"),
            }

        if defer_save:
            result["_deferred_save"] = {
                "listing": listing,
                "filter_result": filter_result,
                "is_exact_coords": is_exact_coords,
                "llm": llm_bundle,
            }
            return result

        db_model, is_new, price_changed = await repo.save_or_update(
            listing, filter_result, is_exact_coords=is_exact_coords, llm_cache=llm_bundle
        )
        result["is_new"] = is_new
        result["price_changed"] = price_changed

        # 5. Dispatch notification if qualified and unnotified
        should_notify = _should_notify(
            is_qualified=filter_result.is_qualified,
            is_new=is_new,
            price_changed=price_changed,
        )

        if should_notify and db_model.notified_at is None:
            if market_medians is None:
                market_medians = await repo.get_market_medians()
            valuation_intel = valuation_engine.evaluate(
                listing=listing,
                filter_result=filter_result,
                market_medians=market_medians,
            )
            webhook_url = getattr(profile, "discord_webhook_url", None)
            notified = await self._send_listing_notifications(
                listing=listing,
                filter_result=filter_result,
                advice=valuation_intel.negotiation,
                webhook_url=webhook_url,
            )
            if notified:
                await repo.mark_as_notified(db_model.id)
                result["notified"] = True

        return result

    async def _persist_batch(
        self,
        pending: list[tuple[Any, dict[str, Any]]],
        profile: Any | None,
        market_medians: dict[str, float] | None,
    ) -> None:
        """Persist one batch of analyzed listings with a single commit.

        `pending` holds (item, res) pairs from process_listing(..., defer_save=True).
        Fills is_new / price_changed / notified in each res dict. Notification
        delivery stays concurrent (gathered); only the notified_at marking joins
        the batch commit.
        """
        notify_jobs: list[dict[str, Any]] = []
        async with get_session() as session:
            repo = ListingRepository(session)
            medians = market_medians if market_medians is not None else await repo.get_market_medians()
            for _item, res in pending:
                bundle = res.pop("_deferred_save", None)
                if bundle is None:
                    continue
                listing = bundle["listing"]
                filt = bundle["filter_result"]
                db_model, is_new, price_changed = await repo.save_or_update(
                    listing,
                    filt,
                    is_exact_coords=bundle["is_exact_coords"],
                    llm_cache=bundle.get("llm"),
                )
                res["is_new"] = is_new
                res["price_changed"] = price_changed
                should_notify = _should_notify(
                    is_qualified=filt.is_qualified,
                    is_new=is_new,
                    price_changed=price_changed,
                )
                if should_notify and db_model.notified_at is None:
                    valuation_intel = valuation_engine.evaluate(
                        listing=listing,
                        filter_result=filt,
                        market_medians=medians,
                    )
                    advice = valuation_intel.negotiation
                    notify_jobs.append(
                        {
                            "res": res,
                            "listing": listing,
                            "filt": filt,
                            "advice": advice,
                            "webhook_url": getattr(profile, "discord_webhook_url", None),
                            "db_id": db_model.id,
                        }
                    )
        if not notify_jobs:
            return
        sent = await asyncio.gather(
            *[
                self._send_listing_notifications(
                    job["listing"], job["filt"], job["advice"], webhook_url=job["webhook_url"]
                )
                for job in notify_jobs
            ]
        )
        marked_ids: list[int] = []
        for job, ok in zip(notify_jobs, sent, strict=True):
            if ok:
                job["res"]["notified"] = True
                marked_ids.append(job["db_id"])
        if marked_ids:
            async with get_session() as session:
                repo = ListingRepository(session)
                for listing_id in marked_ids:
                    await repo.mark_as_notified(listing_id)

    async def _send_listing_notifications(
        self,
        listing: Any,
        filter_result: Any,
        advice: Any,
        webhook_url: str | None = None,
    ) -> bool:
        """Deliver Discord + Telegram alerts for one qualified listing."""
        logger.info(
            f"[Pipeline] Alerting on qualified offer: {listing.title} "
            f"[{filter_result.status.value}] (Score: {filter_result.score:.1f})"
        )
        discord_ok = await self.discord.send_notification(
            listing, filter_result, webhook_url=webhook_url, negotiation_advice=advice
        )
        telegram_ok = await self.telegram.send_notification(listing, filter_result, negotiation_advice=advice)
        return bool(discord_ok or telegram_ok)

    async def run_cycle(self, target_profile: str | None = None) -> dict:
        """Run a complete scraping and processing cycle across active profiles with inter-process lock protection."""
        from src.services.scrape_lock import get_scrape_lock

        lock = get_scrape_lock()
        if not lock.acquire(metadata={"profile": target_profile}):
            holder = lock.get_lock_info() or {}
            logger.warning(
                f"[Pipeline] Cykl scrapingu jest już uruchomiony w innym procesie/kontenerze "
                f"(pid={holder.get('pid')}, host={holder.get('host')}). Pomijanie podwójnego uruchomienia."
            )
            return {
                "total_scraped": 0,
                "new_listings": 0,
                "duplicate_fingerprints": 0,
                "price_changes": 0,
                "qualified": 0,
                "notified": 0,
                "skipped_reason": "already_running",
            }

        # Signal the tracker that a cycle is starting right now — before any
        # heavy startup work (init_db, fresh URLs, medians). Otherwise the
        # dashboard would briefly report the previous cycle's stale
        # "completed 100%" state and stop polling.
        global_tracker.mark_starting()

        try:
            return await self._do_run_cycle(target_profile=target_profile)
        finally:
            lock.release()

    async def _do_run_cycle(self, target_profile: str | None = None) -> dict:
        """Internal execution of scraping cycle."""
        cycle_started = time.perf_counter()
        logger.info("=== Starting Scraper Pipeline Cycle ===")
        from src.services.config_manager import config_manager

        cfg = config_manager.get_config()
        self.llm_analysis_enabled = bool(getattr(cfg, "llm_analysis_enabled", settings.USE_LLM_ANALYSIS))
        from src.filters import QualificationEngine

        self.engine = QualificationEngine(llm_enabled=self.llm_analysis_enabled)
        if self.llm_analysis_enabled:
            logger.info(
                f"[Pipeline] AI LLM analysis enabled (bez limitu na cykl, "
                f"prompt: {getattr(settings, 'LLM_PROMPT_VERSION', 'v1.0')})."
            )
        await init_db()

        total_scraped = 0
        total_new = 0
        total_duplicates = 0
        total_price_changes = 0
        total_qualified = 0
        total_notified = 0
        total_llm_skipped = 0
        total_llm_failed = 0

        if self._custom_scrapers:
            execution_plan: list[tuple[SearchProfile | None, list[BaseScraper]]] = [(None, self.scrapers)]
            fresh_urls: set = set()
        else:
            profiles = cfg.get_active_profiles(target_profile)
            if not profiles:
                logger.warning("[Pipeline] No active search profiles found to scrape.")
                return {
                    "total_scraped": 0,
                    "new_listings": 0,
                    "duplicate_fingerprints": 0,
                    "price_changes": 0,
                    "qualified": 0,
                    "notified": 0,
                }
            async with get_session() as session:
                repo = ListingRepository(session)
                fresh_urls = set(
                    await repo.get_fresh_urls(
                        ["Otodom", "NieruchomosciOnline"],
                        within_hours=settings.DETAIL_REFRESH_HOURS,
                    )
                )
            logger.info(f"[Pipeline] {len(fresh_urls)} ogłoszeń ma świeże dane - szczegóły zostaną pominięte.")

            execution_plan = []
            for prof in profiles:
                scs: list[BaseScraper] = []
                p_portals = prof.enabled_portals
                if cfg.scrapers.otodom.enabled and (not p_portals or "otodom" in p_portals):
                    scs.append(
                        OtodomScraper(
                            max_pages=cfg.scrapers.otodom.max_pages, profile=prof, skip_detail_urls=fresh_urls
                        )
                    )
                if cfg.scrapers.olx.enabled and (not p_portals or "olx" in p_portals):
                    scs.append(OLXScraper(max_pages=cfg.scrapers.olx.max_pages, profile=prof))
                if cfg.scrapers.nieruchomosci_online.enabled and (not p_portals or "nieruchomosci_online" in p_portals):
                    scs.append(
                        NieruchomosciOnlineScraper(
                            max_pages=cfg.scrapers.nieruchomosci_online.max_pages,
                            profile=prof,
                            skip_detail_urls=fresh_urls,
                        )
                    )
                if cfg.scrapers.morizon.enabled and (not p_portals or "morizon" in p_portals):
                    scs.append(MorizonScraper(max_pages=cfg.scrapers.morizon.max_pages, profile=prof))
                execution_plan.append((prof, scs))

        flat_scrapers = []
        for profile, scrapers in execution_plan:
            prof_name = profile.name if profile else "Default"
            for scraper in scrapers:
                flat_scrapers.append((profile, prof_name, scraper))

        total_steps = len(flat_scrapers) or 1
        global_tracker.start_session(total_portals=total_steps)

        # Market medians are computed once per cycle (single full-table pass) and
        # shared across all portal batches.
        async with get_session() as session:
            medians_repo = ListingRepository(session)
            cycle_medians = await medians_repo.get_market_medians()
        t_medians = time.perf_counter() - cycle_started
        logger.debug(f"[Pipeline] Market medians computed in {t_medians:.2f}s.")

        # 1. Scrape all portals in parallel
        scrape_portal_times: list[tuple[str, float]] = []

        async def scrape_portal(prof: Any, prof_name: str, scraper: Any) -> tuple[Any, str, str, list, Any]:
            t_start = time.perf_counter()
            if global_tracker.is_cancelled():
                return prof, prof_name, scraper.name, [], None
            global_tracker.update_portal(f"{scraper.name} ({prof_name})", 1, getattr(scraper, "max_pages", 1))
            scraper.progress_cb = global_tracker.update_portal_page
            try:
                listings = await scraper.scrape()
                if global_tracker.is_cancelled():
                    return prof, prof_name, scraper.name, [], None
                global_tracker.record_items(count=len(listings))
                global_tracker.add_log(f"[{scraper.name} - {prof_name}] Pobrano {len(listings)} ogłoszeń.")
                logger.info(f"[{scraper.name} - {prof_name}] Scraped {len(listings)} listings.")
                return prof, prof_name, scraper.name, listings, None
            except Exception as e:
                logger.error(
                    "[Pipeline] Error running {} for {}: {}",
                    scraper.name,
                    prof_name,
                    e,
                    exc_info=True,
                )
                global_tracker.add_log(f"[{scraper.name} - {prof_name}] Błąd: {e}", level="error")
                return prof, prof_name, scraper.name, [], e
            finally:
                elapsed = time.perf_counter() - t_start
                scrape_portal_times.append((scraper.name, elapsed))
                global_tracker.record_portal_done()
                logger.info(f"[Pipeline] [{scraper.name} - {prof_name}] scrape took {elapsed:.1f}s.")
                try:
                    await scraper.close()
                except Exception:
                    pass

        scrape_results = await asyncio.gather(*[scrape_portal(p, pn, sc) for p, pn, sc in flat_scrapers])
        t_scrape = time.perf_counter() - cycle_started - t_medians
        logger.info(f"[Pipeline] Scraping stage took {t_scrape:.1f}s total.")

        # 2. Concurrently process listings with Semaphore
        concurrency = getattr(settings, "CONCURRENT_REQUESTS", 3) or 3
        sem = asyncio.Semaphore(concurrency)

        t_process_start = time.perf_counter()
        step_idx = 0
        for prof, _prof_name, _sc_name, listings, _err in scrape_results:
            if global_tracker.is_cancelled():
                logger.info("[Pipeline] Cancellation detected before processing batch, breaking loop.")
                break

            step_idx += 1
            total_scraped += len(listings)
            global_tracker.set_processing_fraction(step_idx, total_steps)

            if not listings:
                continue

            processed = 0

            empty_cancel_res = {
                "is_new": False,
                "price_changed": False,
                "qualified": False,
                "notified": False,
            }

            async def safe_process(
                item: Any,
                prof: Any = prof,
                total_listings: int = len(listings),
                medians: dict[str, float] = cycle_medians,
                cancel_res: dict[str, Any] = empty_cancel_res,
                _step_idx: int = step_idx,
                _total_steps: int = total_steps,
            ) -> tuple[Any, dict[str, Any]]:
                nonlocal processed
                if global_tracker.is_cancelled():
                    return item, cancel_res
                async with sem:
                    if global_tracker.is_cancelled():
                        return item, cancel_res
                    async with get_session() as session:
                        repo = ListingRepository(session)
                        res = await self.process_listing(
                            item, repo, profile=prof, market_medians=medians, defer_save=True
                        )
                    processed += 1
                    if processed % 5 == 0 or processed == total_listings:
                        global_tracker.set_processing_fraction(_step_idx, _total_steps, processed, total_listings)
                    return item, res

            results = await asyncio.gather(*[safe_process(item) for item in listings])

            # Single-commit batch persist: 1 commit per batch instead of per listing.
            # (Per-item sessions above are effectively read-only + geocoder cache.)
            await self._persist_batch(list(results), prof, cycle_medians)

            for _item, res in results:
                if res["is_new"]:
                    total_new += 1
                if res["price_changed"]:
                    total_price_changes += 1
                if res["qualified"]:
                    total_qualified += 1
                if res["notified"]:
                    total_notified += 1
                if res.get("llm_skipped"):
                    total_llm_skipped += 1
                    if res.get("llm_skip_reason") == "provider_error":
                        total_llm_failed += 1

                global_tracker.record_items(
                    count=1,
                    qualified=1 if res["qualified"] and res["is_new"] else 0,
                    duplicates=0,
                )

            global_tracker.set_processing_fraction(step_idx, total_steps, 1, 1)

        # Backfill spatial audit for existing database listings missing new metrics
        if not global_tracker.is_cancelled():
            try:
                async with get_session() as session:
                    repo = ListingRepository(session)
                    backfilled_cnt = await self.backfill_existing_spatial_data(session, repo)
                    if backfilled_cnt > 0:
                        logger.info(
                            f"[Pipeline] Pomyślnie zaktualizowano dane przestrzenne dla {backfilled_cnt} istniejących ofert w bazie."
                        )
            except Exception as e:
                logger.debug(f"[Pipeline] Spatial backfill error: {e}")

        # Passive delisting sweep: mark listings not seen on portals for > 7 days as DELISTED
        if not global_tracker.is_cancelled():
            try:
                async with get_session() as session:
                    delist_repo = ListingRepository(session)
                    delisted_cnt = await delist_repo.mark_passive_delisted(
                        inactive_days=7,
                        profile_id=target_profile,
                    )
                    if delisted_cnt > 0:
                        logger.info(
                            f"[Pipeline] Pasywnie oznaczono {delisted_cnt} ofert jako wycofane/zakończone (brak na portalu >7 dni)."
                        )
                        global_tracker.add_log(
                            f"📉 [Płynność] Oznaczono {delisted_cnt} nieaktywnych ofert jako wycofane/sprzedane",
                            level="info",
                            category="info",
                        )
            except Exception as e:
                logger.debug(f"[Pipeline] Passive delisting check error: {e}")

        t_process = time.perf_counter() - t_process_start
        portal_timings = ", ".join(f"{name}={elapsed:.1f}s" for name, elapsed in scrape_portal_times)
        logger.info(
            f"[Pipeline] Cycle timing: medians={t_medians:.1f}s, scrape={t_scrape:.1f}s "
            f"({portal_timings}), processing={t_process:.1f}s, "
            f"total={time.perf_counter() - cycle_started:.1f}s"
        )

        summary = {
            "total_scraped": total_scraped,
            "new_listings": total_new,
            "duplicate_fingerprints": total_duplicates,
            "price_changes": total_price_changes,
            "qualified": total_qualified,
            "notified": total_notified,
            "llm_calls": self.engine.llm_calls,
            "llm_successes": self.engine.llm_successes,
            "llm_failures": self.engine.llm_failures,
            "llm_skipped": total_llm_skipped,
            "llm_failed": total_llm_failed,
            "llm_enabled": self.llm_analysis_enabled,
            "cancelled": global_tracker.is_cancelled(),
        }

        if global_tracker.is_cancelled():
            global_tracker.cancel_session()
            logger.info("=== Cycle Cancelled by User ===")
            return summary

        global_tracker.complete_session(summary)

        logger.info(
            f"=== Cycle Finished ===\n"
            f"Scraped: {total_scraped} | New: {total_new} | Duplicates: {total_duplicates} | "
            f"Price changes: {total_price_changes} | Qualified: {total_qualified} | Notified: {total_notified} | "
            f"LLM: {self.engine.llm_calls} prób ({self.engine.llm_successes} udanych, "
            f"{self.engine.llm_failures} nieudanych), pominiętych: {total_llm_skipped} "
            f"(błędy providera: {total_llm_failed}, cache/reguły: {total_llm_skipped - total_llm_failed})"
        )
        return summary

    async def backfill_existing_spatial_data(
        self,
        session: Any,
        repo: ListingRepository,
        limit: int = 200,
    ) -> int:
        """
        Enriches existing listings in the database with the latest spatial due diligence data
        (SIDUSIS FTTH, ULDK OBB parcel geometry, GUGiK NMT slope/aspect, PKA walkability, power lines).
        Also geocodes any listings missing coordinates.
        """
        from src.services.geocoder import geocoder

        # 1. Geocode listings missing coordinates
        missing_coords_stmt = (
            select(ListingModel)
            .where((ListingModel.latitude.is_(None)) | (ListingModel.longitude.is_(None)))
            .limit(limit)
        )
        res_coords = await session.execute(missing_coords_stmt)
        coords_items = list(res_coords.scalars().all())
        total_coords = len(coords_items)
        if total_coords > 0:
            global_tracker.add_log(f"📍 Rozpoczęto geokodowanie {total_coords} ofert bez współrzędnych...", level="geo")

        for idx, item in enumerate(coords_items, start=1):
            if global_tracker.is_cancelled():
                logger.info("[Pipeline] Cancellation requested during geocoding backfill.")
                break
            global_tracker.current_step = f"Geokodowanie adresów: {idx}/{total_coords}..."
            global_tracker._sync_shared_status()

            lat, lon, is_exact = await geocoder.geocode(
                session=session,
                street=item.street,
                district=item.district,
                city=item.city,
                location_raw=item.location_raw,
            )
            if lat and lon:
                item.latitude = lat
                item.longitude = lon
                item.is_exact_coords = is_exact

        # 2. Find listings with exact coordinates that lack spatial metrics or air quality
        stmt = (
            select(ListingModel)
            .where(
                ListingModel.latitude.isnot(None),
                ListingModel.longitude.isnot(None),
                ListingModel.is_exact_coords.is_(True),
                (
                    ListingModel.broadband_status.is_(None)
                    | ListingModel.parcel_front_width_m.is_(None)
                    | ListingModel.terrain_slope_pct.is_(None)
                    | ListingModel.parcel_id.is_(None)
                    | ListingModel.air_smog_risk.is_(None)
                    | ListingModel.solar_energy_kwh_m2.is_(None)
                    | ListingModel.geology_formation.is_(None)
                    | ListingModel.gunb_status.is_(None)
                    | ListingModel.commute_drive_min.is_(None)
                    | ListingModel.developer_risk_level.is_(None)
                    # Vision only when photos exist — imageless rows could never
                    # fill it and would loop forever.
                    | (
                        ListingModel.vision_finish_condition.is_(None)
                        & ((ListingModel._gallery_images != "[]") | (ListingModel.main_image_url.isnot(None)))
                    )
                ),
            )
            .limit(limit)
        )
        res = await session.execute(stmt)
        items = list(res.scalars().all())
        if not items or global_tracker.is_cancelled():
            return 0

        total_spatial = len(items)
        global_tracker.add_log(
            f"🗺️ Uzupełnianie rejestrów Geoportal/SIDUSIS/CAMS dla {total_spatial} ofert...", level="geo"
        )
        updated_count = 0
        for idx, item in enumerate(items, start=1):
            if global_tracker.is_cancelled():
                logger.info("[Pipeline] Cancellation requested during spatial backfill.")
                break
            global_tracker.current_step = f"Audyt Geoportal/rejestry: {idx}/{total_spatial}..."
            global_tracker._sync_shared_status()

            try:
                geo_audit = await audit_and_apply_spatial_data(item) or {}

                score, item.pros, item.cons = self.engine.apply_spatial_findings(
                    listing=item,
                    score=float(item.qualification_score or 50.0),
                    pros=list(item.pros or []),
                    cons=list(item.cons or []),
                    geo_audit=geo_audit,
                )
                if item.qualification_score is not None:
                    item.qualification_score = min(100.0, max(0.0, score))

                item.updated_at = datetime.now(UTC)
                updated_count += 1
                await safe_commit(session)
                _log_spatial_summary(item, prefix=f"🏛️ [Backfill] #{item.id}")
            except Exception as e:
                logger.debug(f"[Pipeline] Backfill spatial audit error for #{item.id}: {e}")
                try:
                    await session.rollback()
                except Exception:
                    pass

        if updated_count > 0:
            logger.info(f"[Pipeline] Backfilled spatial due diligence for {updated_count} existing listings.")
        return updated_count
