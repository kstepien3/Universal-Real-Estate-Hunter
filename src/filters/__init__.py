import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.storage.models import ListingModel

from loguru import logger

from src.models.enums import (
    BuildingType,
    FinishCondition,
    HeatingType,
    PropertyCategory,
    QualificationStatus,
    SegmentSubtype,
    SewerageType,
)
from src.models.listing import FilterResult, ListingSchema

from .fingerprint import (
    compute_desc_hash,
    estimate_llm_tokens,
    extract_street_token,
    generate_physical_fingerprint,
)
from .llm_analyzer import PROMPT_VERSION, SUGGESTED_OLLAMA_MODELS, LLMAnalyzer, estimate_tokens, load_prompt_template
from .stage1_hard_rules import Stage1Filter
from .stage2_semantic import Stage2SemanticFilter
from .vision_analyzer import (
    SUGGESTED_OLLAMA_VISION_MODELS,
    declared_finish_label,
    is_local_vision_base,
    is_vision_model,
    resolve_vision_target,
)


class QualificationEngine:
    """
    Two-stage filtration and qualification engine:
    1. Stage I: Hard numerical thresholds (budget, house area, plot area) and geographic Whitelist/Blacklist.
    2. Stage II: Semantic description evaluation (corner vs middle segment, dirt roads, parking, slope/clay).
    3. Score computation and status assignment.
    """

    def __init__(self, llm_enabled: bool | None = None):
        self.stage1 = Stage1Filter()
        self.stage2 = Stage2SemanticFilter()
        self.llm = LLMAnalyzer(enabled=llm_enabled)
        self.llm_calls = 0
        self.llm_successes = 0
        self.llm_failures = 0
        # Reason the LLM was skipped for the most recently evaluated listing:
        # None (ran or skipped via caller cache) | "provider_error".
        # No per-cycle budget: every listing that passes Stage I+II is analyzed.
        self.last_skip_reason: str | None = None
        # Metadata of the last successful LLM call (model/prompt version/raw JSON).
        self.last_llm_model: str | None = None
        self.last_llm_prompt_version: str | None = None
        self.last_llm_json: dict[str, Any] | None = None

    def reset_llm_counters(self) -> None:
        self.llm_calls = 0
        self.llm_successes = 0
        self.llm_failures = 0
        self.last_skip_reason = None

    @staticmethod
    def estimate_tokens(text: str | None) -> int:
        from .fingerprint import estimate_llm_tokens

        return estimate_llm_tokens(text)

    @staticmethod
    def _build_filter_result(
        listing: ListingSchema,
        *,
        is_qualified: bool,
        status: QualificationStatus,
        score: float,
        passed_stage1: bool,
        stage1_reasons: list[str],
        passed_stage2: bool,
        stage2_reasons: list[str],
        pros: list[str],
        cons: list[str],
        is_corner: bool = False,
        has_parking_or_garage: bool = False,
        matched_whitelist_area: str | None = None,
        ai_summary: str | None = None,
        ai_verdict: str | None = None,
        worth_interest: bool | None = None,
        ai_questions: list[str] | None = None,
        contact_phone: str | None = None,
        contact_person: str | None = None,
        stakeholder_questions: dict[str, list[str]] | None = None,
        documents_to_obtain: list[str] | None = None,
        structured_risks: list[dict[str, str]] | None = None,
    ) -> FilterResult:
        """Central factory for FilterResult copying spatial/environmental fields directly from listing."""
        from src.models.listing import copy_spatial_fields

        res = FilterResult(
            is_qualified=is_qualified,
            status=status,
            score=score,
            passed_stage1=passed_stage1,
            stage1_reasons=stage1_reasons,
            passed_stage2=passed_stage2,
            stage2_reasons=stage2_reasons,
            pros=pros,
            cons=cons,
            is_corner=is_corner,
            has_parking_or_garage=has_parking_or_garage,
            matched_whitelist_area=matched_whitelist_area,
            finish_condition=listing.finish_condition,
            has_visualisations=listing.has_visualisations,
            sewerage=listing.sewerage,
            heating=listing.heating,
            has_fiber=listing.has_fiber,
            ai_summary=ai_summary,
            ai_verdict=ai_verdict,
            worth_interest=worth_interest,
            ai_questions=ai_questions or [],
            contact_phone=contact_phone,
            contact_person=contact_person,
            mpzp_zone=listing.mpzp_zone,
            flood_risk_zone=listing.flood_risk_zone,
            stakeholder_questions=stakeholder_questions or getattr(listing, "stakeholder_questions", {}),
            documents_to_obtain=documents_to_obtain or getattr(listing, "documents_to_obtain", []),
            structured_risks=structured_risks or getattr(listing, "structured_risks", []),
        )
        copy_spatial_fields(res, listing)
        return res

    def precheck_stage1(self, listing: ListingSchema, profile: Any | None = None) -> tuple[bool, list[str], str | None]:
        """Fast Stage 1 pre-check to decide whether expensive geocoding/spatial lookups should proceed."""
        p = profile
        if not p and getattr(listing, "profile_name", None):
            from src.services.config_manager import config_manager

            p = config_manager.get_profile(listing.profile_name)
        return self.stage1.evaluate(listing, profile=p)

    def apply_spatial_findings(
        self,
        listing: "ListingSchema | ListingModel",
        score: float,
        pros: list[str],
        cons: list[str],
        geo_audit: dict[str, Any] | None = None,
    ) -> tuple[float, list[str], list[str]]:
        """
        Applies spatial due diligence findings (MPZP, flood, SOPO, EGiB, noise, monuments, broadband, parcel geometry, slope, PKA)
        directly to the qualification scoring, pros, and cons.
        """
        if listing.mpzp_zone:
            if listing.mpzp_status == "OBOWIĄZUJĄCY":
                pros.append(f"Miejscowy Plan (MPZP): {listing.mpzp_zone}")
            elif listing.mpzp_status == "BRAK_PLANU_LUB_CYFRYZACJI":
                cons.append("⚠️ Brak cyfrowego MPZP w Geoportalu (wymaga weryfikacji WZ)")

        if listing.flood_risk_zone == "ZAGROŻENIE_POWODZIOWE":
            cons.append("⚠️ Zagrożenie powodziowe (ISOK): Działka w strefie ryzyka powodziowego")
            score -= 20.0

        # SOPO Landslide
        if listing.landslide_risk in ("OSUWISKO", "ZAGROŻENIE_OSUWISKIEM"):
            cons.append(
                "🚨 Aktywne osuwisko / Zagrożenie ruchami masowymi (PIG-PIB SOPO): "
                "Ryzyko naruszenia konstrukcji, odmowy ubezpieczenia lub kredytu"
            )
            score -= 50.0

        # EGiB Building disclosure & Soil class
        if listing.egib_building_status:
            cat = (
                listing.category.value
                if hasattr(listing.category, "value")
                else str(getattr(listing, "category", "dom"))
            ).lower()
            is_house = cat in ("dom", "segment", "blizniak", "szeregowiec") or listing.building_type in (
                BuildingType.WOLNOSTOJACY,
                BuildingType.BLIZNIAK,
                BuildingType.SZEREGOWIEC,
            )
            finish = (
                listing.finish_condition.value
                if hasattr(listing.finish_condition, "value")
                else str(listing.finish_condition or "")
            ).lower()
            is_developer = "deweloperski" in finish or (
                hasattr(listing, "market") and str(listing.market).lower() == "pierwotny"
            )

            if is_house and not is_developer:
                if listing.egib_building_status == "BRAK_W_EWIDENCJI":
                    cons.append(
                        "⚠️ Dom nieujawniony w ewidencji budynków EGiB: Ryzyko samowoli budowlanej, "
                        "braku odbioru końcowego lub problemu z kredytem hipotecznym"
                    )
                    score -= 15.0
                elif listing.egib_building_status == "UJAWNIONY":
                    pros.append("Budynek formalnie ujawniony w państwowej ewidencji budynków (EGiB użytek B/Br)")

        if listing.egib_soil_class and re.search(
            r"\b(?:R|Ł|Ps|S)(?:I{1,3}[ab]?)\b", listing.egib_soil_class, re.IGNORECASE
        ):
            cons.append(
                f"⚠️ Grunt chroniony w EGiB ({listing.egib_soil_class}): Klasa bonitacyjna podlega "
                "ustawowej ochronie rolnej (trudności z odrolnieniem i rozbudową)"
            )
            score -= 10.0

        # Acoustic Noise (>65 dB)
        if (listing.noise_level_db is not None and listing.noise_level_db > 65.0) or (
            listing.noise_zone and "WYSOKI" in listing.noise_zone
        ):
            db_str = f"{listing.noise_level_db:.0f}" if listing.noise_level_db is not None else ">65"
            cons.append(
                f"⚠️ Podwyższony poziom hałasu ({db_str} dB Lden): "
                "Przekroczenie progu uciążliwości akustycznej w sąsiedztwie korytarza tranzytowego"
            )
            score -= 15.0

        # GDOŚ Nature protection
        if listing.nature_protected_zone:
            cons.append(
                f"⚠️ Obszar chroniony przyrodniczo (GDOŚ: {listing.nature_protected_zone}): "
                "Rygory środowiskowe i ograniczenia inwestycyjne"
            )
            score -= 10.0

        # NID Monuments
        if listing.monument_zone:
            cons.append(
                f"🏛️ Obiekt w rejestrze zabytków / strefa konserwatorska (NID: {listing.monument_zone}): "
                "Wszelkie prace budowlane wymagają uzgodnień z Wojewódzkim Konserwatorem Zabytków (WKZ)"
            )
            score -= 15.0

        # Cemetery Buffer
        if listing.cemetery_buffer_zone:
            if listing.cemetery_buffer_zone == "<50m":
                cons.append(
                    "🚨 Bezpośrednia strefa sanitarna cmentarza (<50m): Ustawowy zakaz rozbudowy "
                    "i lokalizacji okien mieszkalnych (Rozporządzenie MZ)"
                )
                score -= 25.0
            elif listing.cemetery_buffer_zone == "50-150m":
                cons.append("⚠️ Strefa ochronna cmentarza (50–150m): Ograniczenia sanitarne i warunki ujęć wody")
                score -= 10.0

        # Broadband (SIDUSIS)
        if listing.broadband_status == "ŚWIATŁOWÓD_AKTYWNY":
            pros.append("🌐 Światłowód aktywny FTTH (potwierdzony w SIDUSIS internet.gov.pl)")
            score += 5.0
        elif listing.broadband_status in ("PLANOWANY_KPO", "PLANOWANY_KPO_FERC"):
            pros.append("📡 Planowana rozbudowa światłowodu (KPO / FERC)")
        elif listing.broadband_status in ("BRAK", "BRAK_ZASIĘGU"):
            cons.append(
                "⚠️ Brak stacjonarnego internetu szerokopasmowego (SIDUSIS): Konieczność łączności LTE/5G lub Starlink"
            )
            score -= 5.0

        # Parcel shape & Front width
        if listing.parcel_front_width_m is not None:
            if listing.parcel_front_width_m < 16.0:
                cons.append(
                    f"📐 Wąski front działki ({listing.parcel_front_width_m:.1f} m < 16 m): "
                    "Restrykcje odległościowe Prawa Budowlanego i utrudnienia w zagospodarowaniu"
                )
                score -= 15.0
            elif listing.parcel_shape_type == "REGULARNY" and listing.parcel_front_width_m >= 18.0:
                aspect_val = listing.parcel_aspect_ratio or 1.0
                pros.append(
                    f"📐 Foremna działka: szerokość frontu {listing.parcel_front_width_m:.0f} m "
                    f"(proporcje 1:{aspect_val:.1f})"
                )

        # Terrain slope & Aspect
        if listing.terrain_slope_pct is not None:
            if listing.terrain_slope_pct > 8.0:
                cons.append(
                    f"⛰️ Strome nachylenie terenu (spadek {listing.terrain_slope_pct:.1f}%, ekspozycja {listing.terrain_aspect or 'nieokreślona'}): "
                    "Ryzyko kosztownej niwelacji terenu, budowy murów oporowych i problemów ze spływem wód"
                )
                score -= 15.0
            elif (
                listing.terrain_aspect in ("POŁUDNIOWY", "POŁUDNIOWO-ZACHODNI", "POŁUDNIOWO-WSCHODNI")
                and listing.terrain_slope_pct >= 2.0
            ):
                pros.append(
                    f"☀️ Południowa ekspozycja stoku (spadek {listing.terrain_slope_pct:.1f}%) — doskonałe nasłonecznienie pod fotowoltaikę"
                )
            elif listing.terrain_slope_pct <= 3.0:
                pros.append(f"🟢 Płaski, bezpieczny teren (spadek {listing.terrain_slope_pct:.1f}%)")

        # High Voltage Power lines
        if listing.power_lines_risk and any(
            k in listing.power_lines_risk.upper() for k in ("LINIA", "400KV", "220KV", "110KV", "WN")
        ):
            cons.append(
                f"⚡ Sąsiedztwo napowietrznej linii wysokiego napięcia ({listing.power_lines_risk}): "
                "Pas technologiczny, pole elektromagnetyczne i obniżona wartość rynkowa"
            )
            score -= 20.0

        # Walkability (Kolej Aglomeracyjna)
        if listing.walkability_pka_dist_m is not None and listing.walkability_pka_dist_m <= 1500:
            walk_m = listing.walkability_pka_dist_m
            walk_min = max(1, round(walk_m / 80))
            city_str = (listing.city or "").strip()
            pka_dest = f" do centrum ({city_str})" if city_str else " do centrum"
            pka_name = listing.walkability_pka_name or "kolejowa"
            pros.append(
                f"🚆 Stacja kolejowa ({pka_name}: {walk_m} m, ~{walk_min} min pieszo) — szybki dojazd{pka_dest}"
            )
            score += 5.0

        # Air quality & smog risk (CAMS + GIOŚ)
        if (
            listing.air_smog_risk == "WYSOKIE"
            or (listing.air_pm25_heating_avg is not None and listing.air_pm25_heating_avg >= 35.0)
            or (listing.air_smog_days is not None and listing.air_smog_days >= 35)
        ):
            avg_str = (
                f"średnia zima PM2.5: {listing.air_pm25_heating_avg:.1f} µg/m³" if listing.air_pm25_heating_avg else ""
            )
            days_str = f", {listing.air_smog_days} dni smogowych w roku" if listing.air_smog_days else ""
            cons.append(
                f"🌫️ Wysokie ryzyko smogu w sezonie grzewczym ({avg_str}{days_str}): "
                "Chroniczne zanieczyszczenie niską emisją w sąsiedztwie"
            )
            score -= 15.0
        elif listing.air_smog_risk == "PODWYŻSZONE" or (
            listing.air_pm25_heating_avg is not None and listing.air_pm25_heating_avg >= 25.0
        ):
            avg_str = (
                f"średnia zima PM2.5: {listing.air_pm25_heating_avg:.1f} µg/m³" if listing.air_pm25_heating_avg else ""
            )
            cons.append(
                f"⚠️ Podwyższone stężenie pyłów w sezonie grzewczym ({avg_str}): "
                "Wskazana weryfikacja rodzaju ogrzewania w bezpośrednim sąsiedztwie"
            )
            score -= 5.0
        elif (
            listing.air_pm25_heating_avg is not None
            and listing.air_pm25_heating_avg <= 15.0
            and (listing.air_aqi is None or listing.air_aqi <= 35)
        ):
            pros.append(
                f"🍃 Czyste powietrze przez cały rok (średnia zima PM2.5: {listing.air_pm25_heating_avg:.1f} µg/m³)"
            )
            score += 5.0

        # Surrounding risks from geo_audit
        if geo_audit:
            risks = geo_audit.get("surrounding_risks", [])
            if risks:
                for r in risks:
                    if not any(
                        m in r
                        for m in (
                            "zagrożenia powodziowego",
                            "SOPO",
                            "osuwisk",
                            "hałas",
                            "GDOŚ",
                            "NID",
                            "cmentar",
                            "Gleba",
                            "EGiB",
                            "Wąska działka",
                            "szerokość frontu",
                            "SIDUSIS",
                            "Stroma działka",
                            "nachylenie",
                            "Linia elektroenergetyczna",
                        )
                    ):
                        cons.append(f"⚠️ Geoportal: {r}")
                if any("Ba" in r or "Bi" in r or "Tk" in r for r in risks):
                    score -= 25.0

            p_num = geo_audit.get("main_parcel_number")
            p_area = geo_audit.get("cadastral_area")
            if p_num and p_area:
                pros.append(f"Zidentyfikowano działkę w Geoportalu: nr {p_num} ({p_area} m²)")

        # GUNB building-permit intelligence (200 m radius)
        gunb_flags = list(getattr(listing, "gunb_risk_flags", None) or [])
        if gunb_flags:
            for flag in gunb_flags[:3]:
                cons.append(flag)
            score -= 20.0
        elif list(getattr(listing, "gunb_permits", None) or []):
            pros.append("🏛️ Rejestr GUNB/RWDZ: wyłącznie standardowe pozwolenia w promieniu 200 m")

        # Vision AI (Living Quarters verification from photos)
        if getattr(listing, "vision_is_render", None) is True:
            cons.append(
                "🖼️ [Vision AI] Zdjęcia ofertowe to wizualizacje 3D / rendery — stan faktyczny wymaga weryfikacji na żywo"
            )
            score -= 10.0
        vision_finish = getattr(listing, "vision_finish_condition", None)
        if vision_finish and vision_finish != "NIEZNANY":
            declared = declared_finish_label(listing) or ""
            if "ZAMIESZKANI" in declared.upper() and vision_finish in ("DO_WYKONCZENIA", "DEWELOPERSKI", "SUROWY"):
                cons.append(
                    f"🔍 [Vision AI] Niespójność stanu: deklarowano '{declared}', a zdjęcia wskazują '{vision_finish}'"
                )
                score -= 15.0
        for defect in list(getattr(listing, "vision_defects", None) or [])[:3]:
            cons.append(f"🔧 [Vision AI] Wada wizualna: {defect}")

        # Commute (OSRM)
        drive_min = getattr(listing, "commute_drive_min", None)
        if drive_min is not None and drive_min > 35:
            cons.append(f"🚗 Długi dojazd do centrum ({drive_min} min samochodem) — lokalizacja wymagająca samochodu")
            score -= 5.0

        # Developer / KRS background check
        dev_level = (getattr(listing, "developer_risk_level", None) or "").upper()
        dev_reasons = list(getattr(listing, "developer_risk_reasons", None) or [])
        if dev_level == "HIGH":
            for reason in dev_reasons[:3]:
                cons.append(f"⚖️ [KRS] {reason}")
            score -= 25.0
        elif dev_level == "MEDIUM":
            for reason in dev_reasons[:2]:
                cons.append(f"⚖️ [KRS] {reason}")
            score -= 10.0
        elif dev_level == "LOW" and dev_reasons:
            pros.append(f"✅ [KRS] Wiarygodny podmiot: {dev_reasons[-1].strip()}"[:200])

        # POI daily infrastructure (Overpass 15-minute city)
        nearest_poi = getattr(listing, "nearest_poi", None) or {}
        if isinstance(nearest_poi, dict) and nearest_poi:
            far_poi: list[str] = []
            poi_labels = {"edukacja": "szkoła/przedszkole", "sklepy": "market spożywczy", "transport": "przystanek"}
            for cat, label in poi_labels.items():
                near = nearest_poi.get(cat) or {}
                poi_walk_min = near.get("walk_min")
                if isinstance(poi_walk_min, (int, float)) and poi_walk_min > 20:
                    far_poi.append(f"{label} (~{int(poi_walk_min)} min pieszo)")
            if far_poi:
                cons.append(f"🏪 Odległa infrastruktura codzienna: {', '.join(far_poi)}")
                score -= 5.0

        return score, pros, cons

    async def evaluate_listing(
        self,
        listing: ListingSchema,
        profile: Any | None = None,
        skip_llm: bool = False,
        geo_audit: dict[str, Any] | None = None,
    ) -> FilterResult:
        """
        Runs the multi-stage qualification pipeline on a single listing.
        """
        self.last_skip_reason = None
        p = profile
        if not p and getattr(listing, "profile_name", None):
            from src.services.config_manager import config_manager

            p = config_manager.get_profile(listing.profile_name)

        # Step 1: Stage I (Hard rules & Geo)
        passed_stage1, stage1_reasons, matched_wl = self.stage1.evaluate(listing, profile=p)

        borderline_reasons: list[str] = []
        if not passed_stage1:
            borderline_reasons = self.stage1.borderline_margins(listing, profile=p)
            if not borderline_reasons:
                return FilterResult(
                    is_qualified=False,
                    status=QualificationStatus.REJECTED_STAGE1,
                    passed_stage1=False,
                    stage1_reasons=stage1_reasons,
                    passed_stage2=False,
                    finish_condition=listing.finish_condition,
                    has_visualisations=listing.has_visualisations,
                    sewerage=listing.sewerage,
                    heating=listing.heating,
                    has_fiber=listing.has_fiber,
                )

        # Step 2: Stage II (Semantic analysis) — runs before the LLM so that listings
        # rejected by cheap regex rules never consume LLM calls.
        res2 = self.stage2.analyze(listing, profile=p)
        passed_stage2 = res2.passed
        stage2_reasons = list(res2.rejection_reasons)
        pros = list(res2.pros)
        cons = list(res2.cons)
        is_corner = res2.is_corner
        has_parking = res2.has_parking_or_garage
        has_visualisations = res2.has_visualisations

        # Update listing subtype, finish condition, visualisations, and utilities
        if res2.detected_subtype != SegmentSubtype.NIEOKRESLONY:
            listing.segment_subtype = res2.detected_subtype
        if res2.detected_finish != FinishCondition.NIEOKRESLONY:
            listing.finish_condition = res2.detected_finish
        listing.has_visualisations = has_visualisations
        if res2.detected_sewerage != SewerageType.NIEZNANA:
            listing.sewerage = res2.detected_sewerage
        if res2.detected_heating != HeatingType.NIEZNANE:
            listing.heating = res2.detected_heating
        listing.has_fiber = res2.has_fiber

        # Borderline handling: an offer is borderline when it fails Stage I only by
        # small margins, or when its only Stage II failure is a "do remontu" finish.
        finish_only_borderline = False
        if not passed_stage2:
            finish_only_borderline = (
                res2.detected_finish == FinishCondition.DO_REMONTU
                and bool(stage2_reasons)
                and all(r.startswith("Stan wykończenia") for r in stage2_reasons)
            )

        # Hard Stage II failures (road access, terrain, plot size, visualisations)
        # reject the offer outright — no LLM call, even for Stage I borderline.
        if not passed_stage2 and not finish_only_borderline:
            return FilterResult(
                is_qualified=False,
                status=QualificationStatus.REJECTED_STAGE2,
                score=10.0,
                passed_stage1=passed_stage1,
                stage1_reasons=borderline_reasons,
                passed_stage2=False,
                stage2_reasons=stage2_reasons,
                pros=pros,
                cons=cons,
                is_corner=is_corner,
                has_parking_or_garage=has_parking,
                matched_whitelist_area=matched_wl,
                finish_condition=listing.finish_condition,
                has_visualisations=listing.has_visualisations,
                sewerage=listing.sewerage,
                heating=listing.heating,
                has_fiber=listing.has_fiber,
            )

        is_borderline = bool(borderline_reasons) or finish_only_borderline

        # Step 3: Optional LLM Enrichment (only for listings that passed Stage II or
        # are borderline — never for definitive rejections; no per-cycle budget,
        # every eligible listing is analyzed)
        ai_summary = None
        ai_verdict = None
        worth_interest = None
        ai_questions: list[str] = []
        stakeholder_questions: dict[str, list[str]] = {}
        documents_to_obtain: list[str] = []
        structured_risks: list[dict[str, str]] = []
        contact_phone = None
        contact_person = None

        if not skip_llm:
            from src.services.progress import global_tracker

            logger.info(f"🤖 [AI Audit] Weryfikacja LLM dla: '{listing.title[:45]}'")
            global_tracker.add_log(
                f"🤖 [AI Audit] Weryfikacja LLM dla: {listing.title[:32]}...",
                level="info",
                category="ai",
            )
            t_llm_start = time.perf_counter()
            self.llm_calls += 1
            llm_insights = await self.llm.analyze_description(listing)
            if llm_insights:
                self.llm_successes += 1
            else:
                self.llm_failures += 1
                self.last_skip_reason = "provider_error"
                logger.warning(
                    f"🤖 [AI Audit] Provider nie zwrócił analizy dla: '{listing.title[:45]}' "
                    f"(sprawdź klucz API / Ollama; licznik prób: {self.llm_calls})"
                )
            if llm_insights:
                llm_meta = getattr(self.llm, "last_model", None)
                llm_pv = getattr(self.llm, "last_prompt_version", None)
                llm_raw = getattr(self.llm, "last_result_json", None)
                self.last_llm_model = str(llm_meta) if llm_meta else None
                self.last_llm_prompt_version = str(llm_pv) if llm_pv else None
                self.last_llm_json = dict(llm_raw) if isinstance(llm_raw, dict) else dict(llm_insights)
            t_llm_sec = time.perf_counter() - t_llm_start
            if llm_insights:
                v_tag = (
                    "warty uwagi"
                    if llm_insights.get("worth_interest") is True
                    else ("nie warty" if llm_insights.get("worth_interest") is False else "zakończono")
                )
                logger.info(f"🤖 [AI Audit] Gotowe dla: '{listing.title[:45]}' ({t_llm_sec:.1f}s, werdykt: {v_tag})")
                global_tracker.add_log(
                    f"🤖 [AI Audit] Gotowe dla {listing.title[:28]} ({t_llm_sec:.1f}s, werdykt: {v_tag})",
                    level="info",
                    category="ai",
                )
                if llm_insights.get("is_corner"):
                    is_corner = True
                    listing.segment_subtype = SegmentSubtype.SKRAJNY
                elif llm_insights.get("is_middle"):
                    listing.segment_subtype = SegmentSubtype.SRODKOWY
                if llm_insights.get("road_is_bad") and passed_stage2:
                    passed_stage2 = False
                    stage2_reasons.append("LLM: Wykryto nieutwardzoną / polną drogę dojazdową")
                if llm_insights.get("has_parking_or_garage"):
                    has_parking = True
                if llm_insights.get("terrain_risk"):
                    cons.append("⚠️ [LLM] Wykryto ryzyko ukształtowania terenu (skarpa / osuwisko / podmokłość)")
                if llm_insights.get("extracted_plot_m2") and not listing.area_plot:
                    try:
                        listing.area_plot = float(llm_insights["extracted_plot_m2"])
                    except (ValueError, TypeError):
                        pass

                finish_raw = str(llm_insights.get("finish_condition") or "").lower()
                finish_map = {
                    "deweloperski": FinishCondition.DEWELOPERSKI,
                    "pod_klucz": FinishCondition.DO_ZAMIESZKANIA,
                    "do_wykonczenia": FinishCondition.DO_WYKONCZENIA,
                    "surowy_zamkniety": FinishCondition.SUROWY_ZAMKNIETY,
                    "surowy_otwarty": FinishCondition.SUROWY_OTWARTY,
                    "do_remontu": FinishCondition.DO_REMONTU,
                }
                if finish_raw in finish_map:
                    new_condition = finish_map[finish_raw]
                    if (
                        new_condition == FinishCondition.DO_ZAMIESZKANIA
                        and listing.finish_condition != FinishCondition.DO_ZAMIESZKANIA
                    ):
                        # Clean up any obsolete "Do wykończenia" con from stage 2 regex
                        cons = [c for c in cons if not c.startswith("Do wykończenia")]
                        if "Standard wykończenia: gotowy do zamieszkania / pod klucz" not in pros:
                            pros.append("Standard wykończenia: gotowy do zamieszkania / pod klucz")
                    listing.finish_condition = new_condition

                finish_note = str(llm_insights.get("finish_note") or "").strip()
                if finish_note:
                    if listing.finish_condition == FinishCondition.DO_ZAMIESZKANIA:
                        pros.append(f"✨ [Stan] {finish_note}")
                    else:
                        cons.append(f"🔧 [LLM] Stan: {finish_note}")

                if llm_insights.get("has_visualisations") and not has_visualisations:
                    listing.has_visualisations = True
                    has_visualisations = True
                    vis_note = str(llm_insights.get("visualisation_note") or "").strip()
                    cons.append(
                        "🖼️ [LLM] Wizualizacje / zdjęcia poglądowe"
                        + (f": {vis_note}" if vis_note else " (brak realnych zdjęć tej nieruchomości)")
                    )

                sewer_raw = str(llm_insights.get("sewerage") or "").lower()
                sewer_map = {
                    "miejska": SewerageType.MIEJSKA,
                    "szambo": SewerageType.SZAMBO,
                    "oczyszczalnia": SewerageType.OCZYSZCZALNIA,
                }
                if sewer_raw in sewer_map:
                    listing.sewerage = sewer_map[sewer_raw]
                elif sewer_raw == "brak":
                    cons.append("⚠️ [LLM] Brak przyłącza kanalizacyjnego na działce / w budynku")

                for hc in llm_insights.get("hidden_costs", []):
                    cons.append(f"⚠️ [Ukryty koszt] {hc}")
                for lr in llm_insights.get("legal_risks", []):
                    cons.append(f"⚖️ [Ryzyko prawne] {lr}")
                for d in llm_insights.get("discrepancies", []):
                    cons.append(f"🔍 [LLM] Rozbieżność portal vs opis: {d}")
                for p in llm_insights.get("pros", []):
                    if p not in pros:
                        pros.append(f"[LLM] {p}")
                for c in llm_insights.get("cons", []):
                    if c not in cons:
                        cons.append(f"[LLM] {c}")

                # AI Due Diligence fields
                ai_summary = llm_insights.get("summary") or None
                ai_verdict = llm_insights.get("verdict") or None
                worth_interest = llm_insights.get("worth_interest")
                if worth_interest is not None:
                    worth_interest = bool(worth_interest)
                ai_questions = llm_insights.get("questions_for_agent") or []
                raw_sq = llm_insights.get("stakeholder_questions")
                if isinstance(raw_sq, dict):
                    stakeholder_questions = {
                        str(k): [str(item) for item in v] if isinstance(v, list) else [] for k, v in raw_sq.items()
                    }
                raw_docs = llm_insights.get("documents_to_obtain")
                if isinstance(raw_docs, list):
                    documents_to_obtain = [str(d) for d in raw_docs]
                raw_risks = llm_insights.get("structured_risks")
                if isinstance(raw_risks, list):
                    structured_risks = [
                        {str(rk): str(rv) for rk, rv in r.items()} for r in raw_risks if isinstance(r, dict)
                    ]
                if not stakeholder_questions.get("seller") and ai_questions:
                    stakeholder_questions["seller"] = list(ai_questions)

                listing.stakeholder_questions = stakeholder_questions
                listing.documents_to_obtain = documents_to_obtain
                listing.structured_risks = structured_risks

                contact_phone = llm_insights.get("contact_phone") or None
                contact_person = llm_insights.get("contact_person") or None

        # Fallback: regex extraction for Polish phone numbers if LLM didn't find one
        if not contact_phone and listing.raw_description:
            phone_match = re.search(
                r"(?:\+?48[\s-]?)?([5-8]\d{2})[\s-]?(\d{3})[\s-]?(\d{3})",
                listing.raw_description,
            )
            if phone_match:
                digits = phone_match.group(1) + phone_match.group(2) + phone_match.group(3)
                contact_phone = f"+48{digits}"

        # Borderline offers land in a dedicated review category (no notifications).
        if is_borderline:
            return self._build_filter_result(
                listing,
                is_qualified=False,
                status=QualificationStatus.NEEDS_REVIEW_BORDERLINE,
                score=10.0,
                passed_stage1=passed_stage1,
                stage1_reasons=borderline_reasons if borderline_reasons else stage1_reasons,
                passed_stage2=passed_stage2,
                stage2_reasons=stage2_reasons,
                pros=pros,
                cons=cons,
                is_corner=is_corner,
                has_parking_or_garage=has_parking,
                matched_whitelist_area=matched_wl,
                ai_summary=ai_summary,
                ai_verdict=ai_verdict,
                worth_interest=worth_interest,
                ai_questions=ai_questions,
                contact_phone=contact_phone,
                contact_person=contact_person,
                stakeholder_questions=stakeholder_questions,
                documents_to_obtain=documents_to_obtain,
                structured_risks=structured_risks,
            )

        if not passed_stage2:
            return self._build_filter_result(
                listing,
                is_qualified=False,
                status=QualificationStatus.REJECTED_STAGE2,
                score=10.0,
                passed_stage1=True,
                stage1_reasons=[],
                passed_stage2=False,
                stage2_reasons=stage2_reasons,
                pros=pros,
                cons=cons,
                is_corner=is_corner,
                has_parking_or_garage=has_parking,
                matched_whitelist_area=matched_wl,
                ai_summary=ai_summary,
                ai_verdict=ai_verdict,
                worth_interest=worth_interest,
                ai_questions=ai_questions,
                contact_phone=contact_phone,
                contact_person=contact_person,
                stakeholder_questions=stakeholder_questions,
                documents_to_obtain=documents_to_obtain,
                structured_risks=structured_risks,
            )

        # Step 4: Scoring & Status resolution
        score = 50.0
        if matched_wl:
            score += 50.0
            pros.insert(0, f"Priorytetowa lokalizacja na Whiteliście: {matched_wl}")
        if is_corner:
            score += 25.0
        if has_parking:
            score += 15.0
        elif listing.category == PropertyCategory.DOM:
            score -= 10.0
        if listing.segment_subtype == SegmentSubtype.SRODKOWY:
            score -= 5.0
        if listing.area_plot and listing.area_plot >= 350.0:
            score += 10.0

        # Price per m2 gradient
        ppm2 = listing.price_per_m2
        if ppm2 > 0:
            if ppm2 <= 6000.0:
                score += 20.0
            elif ppm2 <= 7500.0:
                score += 10.0
            elif ppm2 > 9000.0:
                score -= 10.0

        # Budget bonus: well below the configured max price
        max_price = getattr(p, "max_price", None) if p else None
        if not max_price:
            max_price = getattr(self.stage1, "max_price", None)
        if max_price and 0 < listing.price <= 0.85 * max_price:
            score += 5.0

        # Finish condition scoring
        if listing.finish_condition == FinishCondition.DO_ZAMIESZKANIA:
            score += 15.0
        elif listing.finish_condition == FinishCondition.DEWELOPERSKI:
            score += 5.0
        elif listing.finish_condition == FinishCondition.SUROWY_ZAMKNIETY:
            score -= 10.0
        elif listing.finish_condition == FinishCondition.SUROWY_OTWARTY:
            score -= 20.0

        # Building type scoring
        if listing.building_type == BuildingType.WOLNOSTOJACY:
            score += 10.0
        elif listing.building_type == BuildingType.BLIZNIAK:
            score -= 5.0
        elif listing.building_type == BuildingType.SZEREGOWIEC:
            score -= 10.0

        # Year built scoring: 2 pts per decade below 2000 (capped at -25)
        if listing.year_built and listing.year_built < 2000:
            score -= min(25.0, (2000 - listing.year_built) // 10 * 2)

        # Visualisations penalty
        if listing.has_visualisations:
            score -= 10.0

        # Utilities scoring
        if listing.sewerage == SewerageType.MIEJSKA:
            score += 10.0
        elif listing.sewerage == SewerageType.OCZYSZCZALNIA:
            score += 5.0
        elif listing.sewerage == SewerageType.SZAMBO:
            score -= 10.0

        if listing.heating == HeatingType.POMPA_CIEPLA:
            score += 10.0
        elif listing.heating in (HeatingType.GAZOWE, HeatingType.MIEJSKIE):
            score += 5.0
        elif listing.heating == HeatingType.PELLET_WEGIEL:
            score -= 15.0
        elif listing.heating == HeatingType.ELEKTRYCZNE:
            score -= 5.0

        if listing.has_fiber:
            score += 5.0

        # Step 4.5: Spatial Due Diligence Integration (MPZP, Flood, SOPO, EGiB, Noise, Monuments, FTTH, Slope, etc.)
        score, pros, cons = self.apply_spatial_findings(
            listing=listing,
            score=score,
            pros=pros,
            cons=cons,
            geo_audit=geo_audit,
        )

        score = min(100.0, max(0.0, score))

        if matched_wl:
            status = QualificationStatus.QUALIFIED_WHITELIST
        elif (
            listing.area_plot is None
            or listing.area_plot == 0
            or (
                listing.finish_condition == FinishCondition.NIEOKRESLONY
                and listing.sewerage == SewerageType.NIEZNANA
                and listing.heating == HeatingType.NIEZNANE
            )
        ):
            status = QualificationStatus.NEEDS_REVIEW
        else:
            status = QualificationStatus.QUALIFIED

        return self._build_filter_result(
            listing,
            is_qualified=True,
            status=status,
            score=score,
            passed_stage1=True,
            stage1_reasons=[],
            passed_stage2=True,
            stage2_reasons=[],
            pros=pros,
            cons=cons,
            is_corner=is_corner,
            has_parking_or_garage=has_parking,
            matched_whitelist_area=matched_wl,
            ai_summary=ai_summary,
            ai_verdict=ai_verdict,
            worth_interest=worth_interest,
            ai_questions=ai_questions,
            contact_phone=contact_phone,
            contact_person=contact_person,
            stakeholder_questions=stakeholder_questions,
            documents_to_obtain=documents_to_obtain,
            structured_risks=structured_risks,
        )


__all__ = [
    "Stage1Filter",
    "Stage2SemanticFilter",
    "LLMAnalyzer",
    "QualificationEngine",
    "generate_physical_fingerprint",
    "extract_street_token",
    "compute_desc_hash",
    "estimate_llm_tokens",
    "PROMPT_VERSION",
    "SUGGESTED_OLLAMA_MODELS",
    "SUGGESTED_OLLAMA_VISION_MODELS",
    "declared_finish_label",
    "is_local_vision_base",
    "is_vision_model",
    "resolve_vision_target",
    "estimate_tokens",
    "load_prompt_template",
]
