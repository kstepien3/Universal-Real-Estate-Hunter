import re
from typing import Any

from src.models.listing import ListingSchema
from src.services.market_analyzer import haversine_km


class Stage1Filter:
    """
    Stage I: Hard numerical, category, and geographical rules.
    - Category-aware evaluation:
      * MIESZKANIE: Budget, m² area, price/m², rooms count, floor, owner type, market type.
      * DOM: Budget, m² area, plot area, building types, owner type, market type.
      * DZIALKA: Budget, plot area, price/m², owner type.
    - Blacklist: absolute rejection if any blacklisted keyword is found in title, location, or description.
    - Whitelist: checks if the offer belongs to prioritized whitelist areas.
    """

    def __init__(
        self,
        min_price: float | None = None,
        max_price: float | None = None,
        min_area_home: float | None = None,
        max_area_home: float | None = None,
        min_area_plot: float | None = None,
        max_area_plot: float | None = None,
        min_price_per_m2: float | None = None,
        max_price_per_m2: float | None = None,
        min_rooms: int | None = None,
        max_rooms: int | None = None,
        min_floor: int | None = None,
        max_floor: int | None = None,
        blacklist: list[str] | None = None,
        whitelist_areas: list[dict] | None = None,
        building_types: list[str] | None = None,
        min_year_built: int | None = None,
        max_year_built: int | None = None,
        profile: Any | None = None,
    ):
        from src.services.config_manager import config_manager

        self.profile = profile
        cfg = profile or config_manager.get_profile()

        self.min_price = min_price if min_price is not None else getattr(cfg, "min_price", 0.0)
        self.max_price = max_price if max_price is not None else getattr(cfg, "max_price", 1_300_000.0)
        self.min_price_per_m2 = (
            min_price_per_m2 if min_price_per_m2 is not None else getattr(cfg, "min_price_per_m2", None)
        )
        self.max_price_per_m2 = (
            max_price_per_m2 if max_price_per_m2 is not None else getattr(cfg, "max_price_per_m2", None)
        )
        self.min_area_home = min_area_home if min_area_home is not None else getattr(cfg, "min_area_home", None)
        self.max_area_home = max_area_home if max_area_home is not None else getattr(cfg, "max_area_home", None)
        self.min_area_plot = min_area_plot if min_area_plot is not None else getattr(cfg, "min_area_plot", None)
        self.max_area_plot = max_area_plot if max_area_plot is not None else getattr(cfg, "max_area_plot", None)
        self.min_rooms = min_rooms if min_rooms is not None else getattr(cfg, "min_rooms", None)
        self.max_rooms = max_rooms if max_rooms is not None else getattr(cfg, "max_rooms", None)
        self.min_floor = min_floor if min_floor is not None else getattr(cfg, "min_floor", None)
        self.max_floor = max_floor if max_floor is not None else getattr(cfg, "max_floor", None)
        self.blacklist = blacklist if blacklist is not None else (getattr(cfg, "blacklist_keywords", None) or [])
        if whitelist_areas is not None:
            self.whitelist_areas = whitelist_areas
        elif getattr(cfg, "whitelist_areas", None) is not None:
            self.whitelist_areas = cfg.whitelist_areas
        else:
            self.whitelist_areas = []
        self.category = getattr(cfg, "category", "dom")
        self.owner_type = getattr(cfg, "owner_type", "all")
        self.market_type = getattr(cfg, "market_type", "all")
        self.min_year_built = (
            min_year_built if min_year_built is not None else (getattr(cfg, "min_year_built", None) or 1980)
        )
        self.max_year_built = max_year_built if max_year_built is not None else getattr(cfg, "max_year_built", None)
        self.building_types = (
            building_types if building_types is not None else (getattr(cfg, "building_types", None) or [])
        )
        self.reject_flood_risk = bool(getattr(cfg, "reject_flood_risk", False))
        self.reject_landslide_risk = bool(getattr(cfg, "reject_landslide_risk", False))
        self.reject_high_voltage = bool(getattr(cfg, "reject_high_voltage", False))
        self.min_parcel_front_m = self._parse_front(getattr(cfg, "min_parcel_front_m", None))

    RE_NEGATION_PREFIX = re.compile(
        r"(?:bez|brak|woln[yae]\s+od|nie\s+ma|poza\s+terenem|nie\s+leży\s+na|nie\s+lezy\s+na|brak\s+ryzyka|nie\s+jest\s+to|nie\s+znajduje\s+się\s+na|działka\s+płaska\s*,?\s*bez)(?:\s+ryzyka)?(?:\s+\w+){0,3}\s*$",
        re.IGNORECASE,
    )
    RE_TRANSIT_PREFIX = re.compile(
        r"(?:dojazd\s+do|w\s+stron[eę]|kierunek|w\s+kierunku|minut\s+do|min\s+od|min\s+do|km\s+od|km\s+do|kilometrów\s+od|blisko\s+granicy\s+z|w\s+pobliżu\s+granicy\s+z|graniczy\s+z|zaledwie\s+\d+\s+min\w*\s+od)(?:\s+\w+){0,2}\s*$",
        re.IGNORECASE,
    )

    def _is_negated_or_transit(self, full_text: str, match_start: int, match_end: int) -> bool:
        """Check if the matched term is preceded by a negation or transit marker."""
        prefix_start = max(0, match_start - 60)
        prefix = full_text[prefix_start:match_start]
        if self.RE_NEGATION_PREFIX.search(prefix):
            return True
        return bool(self.RE_TRANSIT_PREFIX.search(prefix))

    def check_blacklist(self, listing: ListingSchema, blacklist_words: list[str] | None = None) -> str | None:
        """Check if any blacklisted term is present in title, location, or description with negation awareness."""
        words = blacklist_words if blacklist_words is not None else self.blacklist
        if not words:
            return None

        location_fields_text = f"{listing.location_raw} {listing.street or ''} {listing.district or ''}".lower()
        title_text = listing.title.lower()
        desc_text = listing.raw_description.lower()

        for term in words:
            t = term.lower()
            escaped = re.escape(t)
            pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)

            # 1. Location fields (address, district, raw location): hard match
            if pattern.search(location_fields_text) or t in location_fields_text:
                return term

            # 2. Title: check match with negation/transit check
            m_title = pattern.search(title_text)
            if m_title and not self._is_negated_or_transit(title_text, m_title.start(), m_title.end()):
                return term

            # 3. Description: check all matches and ensure they are not negated or transit references
            spans: list[tuple[int, int]] = []
            for m in pattern.finditer(desc_text):
                spans.append((m.start(), m.end()))
            if not spans and t in desc_text:
                idx = desc_text.find(t)
                while idx != -1:
                    spans.append((idx, idx + len(t)))
                    idx = desc_text.find(t, idx + 1)

            if spans:
                has_real_violation = False
                for start_idx, end_idx in spans:
                    if not self._is_negated_or_transit(desc_text, start_idx, end_idx):
                        has_real_violation = True
                        break
                if has_real_violation:
                    return term

        return None

    @staticmethod
    def _parse_front(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def check_safety_filters(self, listing: ListingSchema, profile: Any | None = None) -> list[str]:
        """Hard safety rejects based on public spatial registers. Unknown data never rejects."""
        p = profile or self.profile
        reject_flood = bool(getattr(p, "reject_flood_risk", self.reject_flood_risk) if p else self.reject_flood_risk)
        reject_slide = bool(
            getattr(p, "reject_landslide_risk", self.reject_landslide_risk) if p else self.reject_landslide_risk
        )
        reject_hv = bool(getattr(p, "reject_high_voltage", self.reject_high_voltage) if p else self.reject_high_voltage)
        min_front_f = self._parse_front(
            getattr(p, "min_parcel_front_m", self.min_parcel_front_m) if p else self.min_parcel_front_m
        )

        reasons: list[str] = []
        if reject_flood and "POWODZ" in str(listing.flood_risk_zone or "").upper():
            reasons.append("Odrzucono: teren zalewowy ISOK (strefa zagrożenia powodziowego)")
        if reject_slide:
            slide = str(listing.landslide_risk or "").upper()
            if "OSUWISKO" in slide or "ZAGROŻENIE" in slide or "RYZYKO" in slide:
                reasons.append("Odrzucono: strefa zagrożenia osuwiskowego SOPO (PIG-PIB)")
        if reject_hv:
            hv = str(listing.power_lines_risk or "").upper()
            if any(k in hv for k in ("LINIA", "400KV", "220KV", "110KV", "WN", "WYSOKIEGO NAPIĘCIA")):
                reasons.append("Odrzucono: bezpośrednie sąsiedztwo napowietrznej linii wysokiego napięcia")
        front_f = self._parse_front(listing.parcel_front_width_m)
        if min_front_f and min_front_f > 0 and front_f is not None and front_f < min_front_f:
            reasons.append(f"Front działki {front_f:.1f} m węższy niż wymagane min. {min_front_f:.0f} m")
        return reasons

    def check_whitelist(self, listing: ListingSchema, areas: list[dict] | None = None) -> str | None:
        """
        Check if listing matches prioritized whitelist areas.
        Returns the matching whitelist area name or None.
        """
        combined_text = (
            f"{listing.title} {listing.location_raw} {listing.street or ''} "
            f"{listing.district or ''} {listing.raw_description}"
        ).lower()

        wl_areas = areas if areas is not None else self.whitelist_areas
        for area in wl_areas:
            area_name = area["name"]
            req_parent = area.get("required_parent")
            keywords = area.get("keywords", [])

            if req_parent and req_parent.lower() not in combined_text:
                continue

            for kw in keywords:
                escaped = re.escape(kw.lower())
                if re.search(rf"\b{escaped}\b", combined_text) or kw.lower() in combined_text:
                    return area_name

        return None

    @staticmethod
    def _profile_city_center(profile: Any | None) -> tuple[float, float] | None:
        """Resolve the target city centre coordinates from the profile's city name."""
        city = getattr(profile, "city", None)
        if not city:
            return None
        from src.services.config_manager import CITY_CENTROIDS, slugify_city

        return CITY_CENTROIDS.get(slugify_city(city))

    def _distance_violation(self, listing: ListingSchema, profile: Any | None = None) -> str | None:
        """Return a rejection reason when the listing is resolved beyond the profile's
        search radius from the target city centre, or None otherwise."""
        if not listing.coordinates:
            return None
        p = profile or self.profile
        radius = getattr(p, "distance_radius", None) if p else None
        if not radius or radius <= 0:
            return None
        center = self._profile_city_center(p)
        if not center:
            return None
        lat, lon = listing.coordinates
        dist = haversine_km(lat, lon, center[0], center[1])
        tolerance = 3.0
        if dist > radius + tolerance:
            city = getattr(p, "city", "") or ""
            return f"Odległość ~{dist:.0f} km od {city} przekracza promień {radius} km"
        return None

    def evaluate(self, listing: ListingSchema, profile: Any | None = None) -> tuple[bool, list[str], str | None]:
        """
        Runs Stage I filtration according to property category and criteria.
        Returns: (passed: bool, rejection_reasons: list[str], matched_whitelist_area: Optional[str])
        """
        p = profile or self.profile
        if not p and listing.profile_name:
            from src.services.config_manager import config_manager

            p = config_manager.get_profile(listing.profile_name)

        min_p = getattr(p, "min_price", self.min_price) if p else self.min_price
        max_p = getattr(p, "max_price", self.max_price) if p else self.max_price
        min_p_m2 = getattr(p, "min_price_per_m2", self.min_price_per_m2) if p else self.min_price_per_m2
        max_p_m2 = getattr(p, "max_price_per_m2", self.max_price_per_m2) if p else self.max_price_per_m2
        min_area: float | None = getattr(p, "min_area_home", self.min_area_home) if p else self.min_area_home
        max_area: float | None = getattr(p, "max_area_home", self.max_area_home) if p else self.max_area_home
        min_plot = getattr(p, "min_area_plot", self.min_area_plot) if p else self.min_area_plot
        max_plot = getattr(p, "max_area_plot", self.max_area_plot) if p else self.max_area_plot
        min_rooms = getattr(p, "min_rooms", self.min_rooms) if p else self.min_rooms
        max_rooms = getattr(p, "max_rooms", self.max_rooms) if p else self.max_rooms
        min_floor = getattr(p, "min_floor", self.min_floor) if p else self.min_floor
        max_floor = getattr(p, "max_floor", self.max_floor) if p else self.max_floor
        owner_type = getattr(p, "owner_type", self.owner_type) if p else self.owner_type
        market_type = getattr(p, "market_type", self.market_type) if p else self.market_type
        category = getattr(p, "category", self.category) if p else self.category
        if hasattr(listing, "category") and listing.category:
            cat_val = listing.category.value if hasattr(listing.category, "value") else str(listing.category)
            if cat_val in ("dom", "mieszkanie", "dzialka"):
                category = cat_val

        bl_words = getattr(p, "blacklist_keywords", self.blacklist) if p else self.blacklist
        if p and getattr(p, "whitelist_areas", None) is not None:
            wl_areas = p.whitelist_areas
        else:
            wl_areas = self.whitelist_areas
        min_year = getattr(p, "min_year_built", self.min_year_built) if p else self.min_year_built
        max_year = getattr(p, "max_year_built", self.max_year_built) if p else self.max_year_built
        bt_allowed = getattr(p, "building_types", self.building_types) if p else self.building_types

        reasons: list[str] = []

        # 1. Blacklist check (Absolute rejection)
        matched_bl = self.check_blacklist(listing, bl_words)
        if matched_bl:
            reasons.append(f"Odrzucono na Blackliście lokalizacji: wykryto frazę '{matched_bl}'")
            return False, reasons, None

        # 2. Budget checks
        if (min_p or 0) > 0 and listing.price < min_p:
            reasons.append(f"Cena {listing.price:,.0f} zł mniejsza niż wymagane minimum {min_p:,.0f} zł")
        if (max_p or 0) > 0 and listing.price > max_p:
            reasons.append(f"Cena {listing.price:,.0f} zł przekracza budżet {max_p:,.0f} zł")

        if listing.price_per_m2 and listing.price_per_m2 > 0:
            if min_p_m2 and listing.price_per_m2 < min_p_m2:
                reasons.append(f"Cena/m² {listing.price_per_m2:,.0f} zł/m² poniżej minimum {min_p_m2:,.0f} zł/m²")
            if max_p_m2 and listing.price_per_m2 > max_p_m2:
                reasons.append(f"Cena/m² {listing.price_per_m2:,.0f} zł/m² powyżej maksimum {max_p_m2:,.0f} zł/m²")

        # 3. Owner type check
        if owner_type == "private" and listing.is_private_owner is False:
            reasons.append("Oferta agencyjna/deweloperska (wymagane wyłącznie od osób prywatnych)")
        elif owner_type == "developer" and listing.is_private_owner is True:
            reasons.append("Oferta od osoby prywatnej (wymagane od dewelopera)")

        # 4. Market type check
        if market_type in ("pierwotny", "wtórny"):
            l_market = listing.market.value if hasattr(listing.market, "value") else str(listing.market)
            if l_market not in ("nieokreślony", market_type):
                reasons.append(f"Rynek '{l_market}' niezgodny z wymaganym '{market_type}'")

        # 4b. Year built check (houses)
        if category == "dom" and listing.year_built:
            if min_year and listing.year_built < min_year:
                reasons.append(f"Rok budowy {listing.year_built} wcześniejszy niż wymagane min. {min_year}")
            if max_year and listing.year_built > max_year:
                reasons.append(f"Rok budowy {listing.year_built} późniejszy niż dopuszczalne max. {max_year}")

        # 4c. Building type allow-list
        if category in ("dom", "mieszkanie") and bt_allowed:
            b_val = (
                listing.building_type.value if hasattr(listing.building_type, "value") else str(listing.building_type)
            )
            if b_val not in bt_allowed:
                reasons.append(f"Typ budynku '{b_val}' poza dozwolonymi w konfiguracji")

        # 5. Category-specific criteria
        if category == "mieszkanie":
            # Apartment area check
            if min_area is not None and min_area > 0 and listing.area_home < min_area:
                reasons.append(f"Metraż mieszkania {listing.area_home:.1f} m² mniejszy niż wymagane {min_area} m²")
            elif max_area is not None and max_area > 0 and listing.area_home > max_area:
                reasons.append(f"Metraż mieszkania {listing.area_home:.1f} m² większy niż dopuszczalne {max_area} m²")

            # Rooms check
            if listing.rooms is not None:
                if min_rooms and listing.rooms < min_rooms:
                    reasons.append(f"Liczba pokoi {listing.rooms} mniejsza niż wymagane {min_rooms}")
                if max_rooms and listing.rooms > max_rooms:
                    reasons.append(f"Liczba pokoi {listing.rooms} większa niż maksymalne {max_rooms}")

            # Floor check
            if listing.floor is not None:
                if min_floor is not None and listing.floor < min_floor:
                    reasons.append(f"Piętro {listing.floor} poniżej wymaganego min. {min_floor}")
                if max_floor is not None and listing.floor > max_floor:
                    reasons.append(f"Piętro {listing.floor} powyżej dopuszczalnego max. {max_floor}")

        elif category == "dom":
            # House area check
            if min_area is not None and min_area > 0 and listing.area_home < min_area:
                reasons.append(f"Metraż domu {listing.area_home:.1f} m² mniejszy niż wymagane {min_area} m²")
            elif max_area is not None and max_area > 0 and listing.area_home > max_area:
                reasons.append(f"Metraż domu {listing.area_home:.1f} m² większy niż dopuszczalne {max_area} m²")

            # Plot area check
            if listing.area_plot is not None and listing.area_plot > 0:
                if listing.area_plot < 200.0:
                    reasons.append(f"Działka {listing.area_plot:.0f} m² mniejsza niż bezwzględne min. 200 m²")
                elif min_plot and listing.area_plot < min_plot:
                    # If between 200 and min_plot, Stage II checks corner/middle segment
                    pass
                if max_plot and listing.area_plot > max_plot:
                    reasons.append(f"Działka {listing.area_plot:.0f} m² większa niż dopuszczalne max. {max_plot} m²")

        elif category == "dzialka":
            # Plot area is the main metric for land/plots
            effective_plot = listing.area_plot or listing.area_home
            if min_plot and effective_plot < min_plot:
                reasons.append(f"Powierzchnia działki {effective_plot:.0f} m² mniejsza niż wymagane {min_plot} m²")
            if max_plot and effective_plot > max_plot:
                reasons.append(f"Powierzchnia działki {effective_plot:.0f} m² większa niż dopuszczalne {max_plot} m²")

        # 6. Safety filters from public spatial registers (hard reject, unknown data never rejects)
        reasons.extend(self.check_safety_filters(listing, profile=p))

        # 7. Distance gate: reject listings resolved beyond the profile's search
        # radius from the target city centre. Whitelisted areas are exempt, since
        # they may intentionally lie beyond the nominal radius.
        matched_wl = self.check_whitelist(listing, wl_areas)
        if not matched_wl and listing.coordinates and (violation := self._distance_violation(listing, p)) is not None:
            reasons.append(violation)

        if reasons:
            return False, reasons, None

        return True, [], matched_wl

    def borderline_margins(self, listing: ListingSchema, profile: Any | None = None) -> list[str]:
        """
        Detect Stage I "borderline" failures: rules violated only by small margins.
        Returns human-readable borderline reasons, or an empty list when any hard
        rule fails (blacklist, budget floor, owner/market type, building type, or
        margins exceeded). Called only when evaluate() has already rejected the listing.
        """
        p = profile or self.profile
        if not p and listing.profile_name:
            from src.services.config_manager import config_manager

            p = config_manager.get_profile(listing.profile_name)

        min_p = getattr(p, "min_price", self.min_price) if p else self.min_price
        max_p = getattr(p, "max_price", self.max_price) if p else self.max_price
        min_p_m2 = getattr(p, "min_price_per_m2", self.min_price_per_m2) if p else self.min_price_per_m2
        max_p_m2 = getattr(p, "max_price_per_m2", self.max_price_per_m2) if p else self.max_price_per_m2
        min_area: float | None = getattr(p, "min_area_home", self.min_area_home) if p else self.min_area_home
        max_area: float | None = getattr(p, "max_area_home", self.max_area_home) if p else self.max_area_home
        min_plot = getattr(p, "min_area_plot", self.min_area_plot) if p else self.min_area_plot
        max_plot = getattr(p, "max_area_plot", self.max_area_plot) if p else self.max_area_plot
        min_rooms = getattr(p, "min_rooms", self.min_rooms) if p else self.min_rooms
        max_rooms = getattr(p, "max_rooms", self.max_rooms) if p else self.max_rooms
        min_floor = getattr(p, "min_floor", self.min_floor) if p else self.min_floor
        max_floor = getattr(p, "max_floor", self.max_floor) if p else self.max_floor
        owner_type = getattr(p, "owner_type", self.owner_type) if p else self.owner_type
        market_type = getattr(p, "market_type", self.market_type) if p else self.market_type
        category = getattr(p, "category", self.category) if p else self.category
        if hasattr(listing, "category") and listing.category:
            cat_val = listing.category.value if hasattr(listing.category, "value") else str(listing.category)
            if cat_val in ("dom", "mieszkanie", "dzialka"):
                category = cat_val

        bl_words = getattr(p, "blacklist_keywords", self.blacklist) if p else self.blacklist
        min_year = getattr(p, "min_year_built", self.min_year_built) if p else self.min_year_built
        max_year = getattr(p, "max_year_built", self.max_year_built) if p else self.max_year_built
        bt_allowed = getattr(p, "building_types", self.building_types) if p else self.building_types

        borderline: list[str] = []

        if self.check_blacklist(listing, bl_words):
            return []

        # Safety rejects are never borderline — hard environmental failures stay rejected.
        if self.check_safety_filters(listing, profile=p):
            return []

        if (min_p or 0) > 0 and listing.price < min_p:
            return []
        if (max_p or 0) > 0 and listing.price > max_p:
            if listing.price <= 1.1 * max_p:
                borderline.append(f"Cena {listing.price:,.0f} zł do 10% powyżej budżetu {max_p:,.0f} zł")
            else:
                return []

        if listing.price_per_m2 and listing.price_per_m2 > 0:
            if min_p_m2 and listing.price_per_m2 < min_p_m2:
                return []
            if max_p_m2 and listing.price_per_m2 > max_p_m2:
                return []

        if (
            owner_type == "private"
            and listing.is_private_owner is False
            or owner_type == "developer"
            and listing.is_private_owner is True
        ):
            return []

        if market_type in ("pierwotny", "wtórny"):
            l_market = listing.market.value if hasattr(listing.market, "value") else str(listing.market)
            if l_market not in ("nieokreślony", market_type):
                return []

        if category == "dom" and listing.year_built:
            if min_year and listing.year_built < min_year:
                year_floor = min_year - 5
                if listing.year_built >= year_floor:
                    borderline.append(f"Rok budowy {listing.year_built} do 5 lat poniżej minimum {min_year}")
                else:
                    return []
            if max_year and listing.year_built > max_year:
                return []

        if category in ("dom", "mieszkanie") and bt_allowed:
            b_val = (
                listing.building_type.value if hasattr(listing.building_type, "value") else str(listing.building_type)
            )
            if b_val not in bt_allowed:
                return []

        if category == "mieszkanie":
            if min_area is not None and min_area > 0 and listing.area_home < min_area:
                if listing.area_home >= 0.9 * min_area:
                    borderline.append(
                        f"Metraż mieszkania {listing.area_home:.1f} m² do 10% poniżej minimum {min_area} m²"
                    )
                else:
                    return []
            elif max_area is not None and max_area > 0 and listing.area_home > max_area:
                if listing.area_home <= 1.1 * max_area:
                    borderline.append(
                        f"Metraż mieszkania {listing.area_home:.1f} m² do 10% powyżej maksimum {max_area} m²"
                    )
                else:
                    return []

            if listing.rooms is not None:
                if min_rooms and listing.rooms < min_rooms:
                    return []
                if max_rooms and listing.rooms > max_rooms:
                    return []
            if listing.floor is not None:
                if min_floor is not None and listing.floor < min_floor:
                    return []
                if max_floor is not None and listing.floor > max_floor:
                    return []

        elif category == "dom":
            if min_area is not None and min_area > 0 and listing.area_home < min_area:
                if listing.area_home >= 0.9 * min_area:
                    borderline.append(f"Metraż domu {listing.area_home:.1f} m² do 10% poniżej minimum {min_area} m²")
                else:
                    return []
            elif max_area is not None and max_area > 0 and listing.area_home > max_area:
                if listing.area_home <= 1.1 * max_area:
                    borderline.append(f"Metraż domu {listing.area_home:.1f} m² do 10% powyżej maksimum {max_area} m²")
                else:
                    return []

            if listing.area_plot is not None and listing.area_plot > 0:
                if listing.area_plot < 200.0:
                    return []
                if max_plot and listing.area_plot > max_plot:
                    return []

        elif category == "dzialka":
            effective_plot = listing.area_plot or listing.area_home
            if min_plot and effective_plot < min_plot:
                return []
            if max_plot and effective_plot > max_plot:
                return []

        return borderline
