import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger

from config import settings
from src.filters.fingerprint import generate_property_fingerprint
from src.models.enums import (
    BuildingType,
    FinishCondition,
    HeatingType,
    MarketType,
    RoadType,
    SegmentSubtype,
    SewerageType,
)
from src.models.listing import ListingSchema

from .base import BaseScraper

OTODOM_BASE_URL = "https://www.otodom.pl/"


class OtodomScraper(BaseScraper):
    """
    Otodom.pl Scraper:
    1. Extracts search results from <script id="__NEXT_DATA__">.
    2. Supports pagination.
    3. Fetches detail pages for comprehensive description, coordinates, building type, and road access.
    4. Handles errors gracefully and falls back to listing preview data when detail request fails.
    """

    def __init__(
        self,
        max_pages: int = 3,
        search_url: str | None = None,
        profile: Any | None = None,
        skip_detail_urls: set | None = None,
    ):
        super().__init__(name="OtodomScraper")
        self.max_pages = max_pages
        self.search_url = search_url
        self.profile = profile
        self.skip_detail_urls = skip_detail_urls or set()

    def _extract_next_data(self, html: str) -> dict[str, Any] | None:
        """Find and parse __NEXT_DATA__ JSON from HTML."""
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            # Fallback to BeautifulSoup search
            soup = BeautifulSoup(html, "html.parser")
            script = soup.find("script", id="__NEXT_DATA__")
            if script and script.string:
                try:
                    return json.loads(script.string)
                except Exception as e:
                    logger.error(f"Failed to parse __NEXT_DATA__ JSON: {e}")
            return None

        try:
            return json.loads(match.group(1))
        except Exception as e:
            logger.error(f"Failed to parse __NEXT_DATA__ JSON regex match: {e}")
            return None

    def _clean_html_description(self, raw_html: str) -> str:
        """Strip HTML tags and normalize description text."""
        if not raw_html:
            return ""
        soup = BeautifulSoup(raw_html, "html.parser")
        # Replace block tags with newline markers
        for tag in soup.find_all(["p", "br", "div", "li", "tr"]):
            tag.append("\n")
        text = soup.get_text(separator=" ")
        # Clean extra whitespace and fix punctuation spacing
        lines = []
        for line in text.splitlines():
            line = re.sub(r"[ \t]+", " ", line).strip()
            line = re.sub(r"\s+([.,!?:;])", r"\1", line)
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _map_building_type(self, raw_type: str | None) -> BuildingType:
        if not raw_type:
            return BuildingType.INNY
        val = raw_type.lower()
        if "ribbon" in val or "szereg" in val or "segment" in val:
            return BuildingType.SZEREGOWIEC
        if "semi_detached" in val or "blizniak" in val or "bliźniak" in val:
            return BuildingType.BLIZNIAK
        if "detached" in val or "wolnostoj" in val:
            return BuildingType.WOLNOSTOJACY
        return BuildingType.INNY

    def _map_road_type(self, access_val: str | None) -> RoadType:
        if not access_val:
            return RoadType.NIEZNANA
        val = access_val.lower()
        if "asphalt" in val or "asfalt" in val:
            return RoadType.ASFALT
        if "paving_stone" in val or "kostk" in val or "bruk" in val:
            return RoadType.KOSTKA
        if "hard_surfaced" in val or "utwardzon" in val:
            return RoadType.UTWARDZONA
        if "dirt" in val or "poln" in val or "nieutwardzon" in val:
            return RoadType.POLNA
        return RoadType.NIEZNANA

    def _map_market(self, market_val: str | None) -> MarketType:
        if not market_val:
            return MarketType.NIEOKRESLONY
        val = market_val.lower()
        if "primary" in val or "pierwotny" in val:
            return MarketType.PIERWOTNY
        if "secondary" in val or "wtorny" in val or "wtórny" in val:
            return MarketType.WTORNY
        return MarketType.NIEOKRESLONY

    def _map_finish_condition(self, val: str | None) -> FinishCondition:
        if not val:
            return FinishCondition.NIEOKRESLONY
        v = val.lower()
        if "to_completion" in v or "do_wykonczenia" in v or "do wykończenia" in v:
            return FinishCondition.DO_WYKONCZENIA
        if "developer" in v or "dewelopersk" in v:
            return FinishCondition.DEWELOPERSKI
        if "ready_to_use" in v or "zamieszkan" in v or "klucz" in v:
            return FinishCondition.DO_ZAMIESZKANIA
        if "unfinished_close" in v or "surowy_zamkniety" in v or "surowy zamknięty" in v:
            return FinishCondition.SUROWY_ZAMKNIETY
        if "unfinished_open" in v or "surowy_otwarty" in v or "surowy otwarty" in v:
            return FinishCondition.SUROWY_OTWARTY
        if "to_renovation" in v or "remont" in v:
            return FinishCondition.DO_REMONTU
        return FinishCondition.NIEOKRESLONY

    def _map_sewerage(self, val: Any) -> SewerageType:
        if not val:
            return SewerageType.NIEZNANA
        v = str(val).lower()
        if "sewerage" in v or "sewage" in v or "miejsk" in v or "canalization" in v or "gminn" in v or "sieciow" in v:
            return SewerageType.MIEJSKA
        if "treatment_plant" in v or "oczyszczaln" in v:
            return SewerageType.OCZYSZCZALNIA
        if "septic_tank" in v or "szamb" in v or "cesspool" in v:
            return SewerageType.SZAMBO
        return SewerageType.NIEZNANA

    def _map_heating(self, val: Any) -> HeatingType:
        if not val:
            return HeatingType.NIEZNANE
        v = str(val).lower()
        if "heat_pump" in v or "pompa" in v:
            return HeatingType.POMPA_CIEPLA
        if "gas" in v or "gaz" in v:
            return HeatingType.GAZOWE
        if "coal" in v or "solid_fuel" in v or "pellet" in v or "ekogrosz" in v:
            return HeatingType.PELLET_WEGIEL
        if "electric" in v or "elektrycz" in v:
            return HeatingType.ELEKTRYCZNE
        if "urban" in v or "miejsk" in v:
            return HeatingType.MIEJSKIE
        return HeatingType.NIEZNANE

    def _get_characteristic(self, detail_data: dict[str, Any], key: str) -> Any | None:
        """Get a value from detail characteristics list (e.g. 'construction_status')."""
        for entry in detail_data.get("characteristics") or []:
            if isinstance(entry, dict) and entry.get("key") == key and entry.get("value"):
                return entry["value"]
        return None

    def _collect_ai_params(self, detail_data: dict[str, Any]) -> dict[str, list[str]]:
        """Flatten enrichment.aiParamsList into {key: [values]}, skipping rejected params."""
        params: dict[str, list[str]] = {}
        enrichment = detail_data.get("enrichment") or {}
        for entry in enrichment.get("aiParamsList") or []:
            if not isinstance(entry, dict) or entry.get("rejected"):
                continue
            key, value = entry.get("key"), entry.get("value")
            if key and value:
                params.setdefault(str(key), []).append(str(value))
        return params

    def _get_additional_info(self, detail_data: dict[str, Any], key_fragment: str) -> Any | None:
        """Get values from the 'Informacje dodatkowe' table items by label fragment."""
        for item in detail_data.get("additionalInformation") or []:
            if not isinstance(item, dict):
                continue
            if key_fragment in str(item.get("label", "")) and item.get("values"):
                return item["values"]
        return None

    async def fetch_listing_detail(self, slug_or_url: str) -> dict[str, Any]:
        """Fetch detail page to acquire full description and characteristics."""
        if slug_or_url.startswith("http"):
            url = slug_or_url
        else:
            url = f"https://www.otodom.pl/pl/oferta/{slug_or_url}"

        html = await self.fetch_html(url, referer=OTODOM_BASE_URL)
        if not html:
            return {}

        next_data = self._extract_next_data(html)
        if not next_data:
            return {}

        return next_data.get("props", {}).get("pageProps", {}).get("ad", {})

    async def parse_search_item(
        self,
        item: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> ListingSchema | None:
        """Parse search item and enrich with detail data if configured."""
        try:
            item_id = str(item.get("id"))
            slug = item.get("slug", "")
            title = item.get("title", "").strip()
            if slug:
                url = f"https://www.otodom.pl/pl/oferta/{slug}"
            elif item.get("href"):
                raw_href = str(item["href"]).replace("[lang]/ad/", "pl/oferta/").replace("[lang]/", "pl/oferta/")
                if raw_href.startswith("/"):
                    url = f"https://www.otodom.pl{raw_href}"
                else:
                    url = f"https://www.otodom.pl/{raw_href}"
            else:
                url = f"https://www.otodom.pl/pl/oferta/id{item_id}"

            # Basic pricing and metrics
            total_price_obj = item.get("totalPrice") or {}
            price = float(total_price_obj.get("value", 0.0))

            price_per_m2_obj = item.get("pricePerSquareMeter") or {}
            price_per_m2 = float(price_per_m2_obj.get("value", 0.0))

            area_home = float(item.get("areaInSquareMeters", 0.0))
            area_plot = item.get("terrainAreaInSquareMeters")
            area_plot = float(area_plot) if area_plot and float(area_plot) > 0 else None

            # Location from search item
            location_obj = item.get("location") or {}
            address_obj = location_obj.get("address") or {}
            street_obj = address_obj.get("street") or {}
            street_name = street_obj.get("name") if isinstance(street_obj, dict) else None

            city_obj = address_obj.get("city") or {}
            city_name = city_obj.get("name") if isinstance(city_obj, dict) else None

            district_obj = address_obj.get("district") or {}
            district_name = district_obj.get("name") if isinstance(district_obj, dict) else None

            reverse_geocoding = (location_obj.get("reverseGeocoding") or {}).get("locations", [])
            geo_parts = [loc.get("name") for loc in reverse_geocoding if loc.get("name")]
            fallback_city = getattr(self.profile, "city", None) or ""
            location_raw = ", ".join(geo_parts) or f"{city_name or fallback_city}, {district_name or ''}".strip(", ")

            # Images
            images = item.get("images", [])
            main_image_url = images[0].get("large") or images[0].get("medium") if images else None
            gallery_images: list[str] = []
            has_visualisations_from_meta = False

            short_desc = item.get("shortDescription") or ""
            raw_description = short_desc

            # Coordinates
            coordinates = None
            coords_dict = location_obj.get("coordinates")
            if coords_dict and coords_dict.get("latitude") and coords_dict.get("longitude"):
                coordinates = (float(coords_dict["latitude"]), float(coords_dict["longitude"]))

            building_type = BuildingType.INNY
            road_type = RoadType.NIEZNANA
            market = MarketType.NIEOKRESLONY
            finish_condition = FinishCondition.NIEOKRESLONY
            sewerage = SewerageType.NIEZNANA
            heating = HeatingType.NIEZNANE
            has_fiber = False
            year_built = None

            # Fetch detail if enabled (skip when we have fresh data from a recent cycle)
            detail_skipped = False
            detail_data: dict[str, Any] = {}
            if settings.FETCH_DETAILS and slug:
                if url in self.skip_detail_urls:
                    detail_skipped = True
                else:
                    async with semaphore:
                        await asyncio.sleep(self.delay(0.4))  # Politeness delay
                        detail_data = await self.fetch_listing_detail(slug)

                if detail_data:
                    # Full description
                    full_desc_html = detail_data.get("description", "")
                    if full_desc_html:
                        raw_description = self._clean_html_description(full_desc_html)

                    # Target dictionary
                    target = detail_data.get("target", {})
                    ai_params = self._collect_ai_params(detail_data)
                    if target:
                        # Building type
                        b_types = target.get("Building_type") or []
                        if b_types and isinstance(b_types, list):
                            building_type = self._map_building_type(b_types[0])

                        # Road type
                        roads = target.get("Access_types") or []
                        if roads and isinstance(roads, list):
                            road_type = self._map_road_type(roads[0])

                        # Market
                        market_val = target.get("MarketType")
                        if market_val:
                            market = self._map_market(market_val)

                        # Year built
                        year_val = target.get("Build_year")
                        if year_val:
                            try:
                                year_built = int(year_val)
                            except ValueError:
                                pass

                        # Area & Plot overrides if missing in search
                        if not area_plot and target.get("Terrain_area"):
                            try:
                                area_plot = float(target["Terrain_area"])
                            except ValueError:
                                pass

                    # Finish condition / Construction status:
                    # target -> characteristics -> aiParams -> 'Informacje dodatkowe' table
                    c_status = target.get("Construction_status") if target else None
                    if not c_status:
                        c_status = self._get_characteristic(detail_data, "construction_status")
                    if not c_status:
                        c_status = ai_params.get("construction_status")
                    if not c_status:
                        c_status = self._get_additional_info(detail_data, "construction_status")
                    if c_status:
                        c_val = c_status[0] if isinstance(c_status, list) else c_status
                        finish_condition = self._map_finish_condition(c_val)

                    # Heating: target.Heating_types -> aiParams -> 'Informacje dodatkowe' table
                    heat_val = (target.get("Heating_types") or target.get("Heating")) if target else None
                    if not heat_val:
                        heat_val = ai_params.get("heating_types")
                    if not heat_val:
                        heat_val = self._get_additional_info(detail_data, "heating_types")
                    if heat_val:
                        heating = self._map_heating(heat_val[0] if isinstance(heat_val, list) else heat_val)

                    # Sewerage / Media: target.Media_types -> aiParams -> 'Informacje dodatkowe' table
                    media_val = target.get("Media_types") if target else None
                    if not media_val:
                        media_val = ai_params.get("media_types")
                    if not media_val:
                        media_val = self._get_additional_info(detail_data, "media_types")
                    if media_val:
                        sewerage = self._map_sewerage(media_val)

                    # Detail coordinates if not found in search
                    if not coordinates:
                        det_coords = (detail_data.get("location") or {}).get("coordinates")
                        if det_coords and det_coords.get("latitude") and det_coords.get("longitude"):
                            coordinates = (float(det_coords["latitude"]), float(det_coords["longitude"]))

                    # Collect images and check metadata
                    det_images = detail_data.get("images", [])
                    if det_images and not main_image_url:
                        main_image_url = det_images[0].get("large") or det_images[0].get("medium")

            # Finalize gallery images and inspect metadata for visualisations
            raw_imgs = (detail_data.get("images", []) if detail_data else []) or images
            render_indicators = ("render", "wizualizac", "visualis", "koncepcj", "rzut", "projekt-3d")
            for img_obj in raw_imgs:
                if not isinstance(img_obj, dict):
                    continue
                u = img_obj.get("large") or img_obj.get("medium") or img_obj.get("thumbnail")
                if u and u not in gallery_images:
                    gallery_images.append(u)
                meta_str = f"{u or ''} {img_obj.get('alt', '')} {img_obj.get('caption', '')} {img_obj.get('label', '')}".lower()
                if any(ind in meta_str for ind in render_indicators):
                    has_visualisations_from_meta = True
            if gallery_images and not main_image_url:
                main_image_url = gallery_images[0]
            gallery_images = gallery_images[:15]

            # Calculate price per m2 if missing
            if (not price_per_m2 or price_per_m2 <= 0) and price > 0 and area_home > 0:
                price_per_m2 = round(price / area_home, 2)

            # Date created (first discovered/scraped)
            created_at = datetime.now(UTC)

            # Rooms extraction
            rooms = None
            raw_rooms = item.get("roomsNumber") or (
                target.get("Rooms_num") if "target" in locals() and target else None
            )
            if raw_rooms:
                r_str = str(raw_rooms[0] if isinstance(raw_rooms, list) else raw_rooms).strip()
                r_match = re.search(r"\d+", r_str)
                if r_match:
                    try:
                        rooms = int(r_match.group(0))
                    except ValueError:
                        pass

            # Floor extraction
            floor = None
            floors_in_building = None
            if "target" in locals() and target:
                raw_floor = target.get("Floor_no")
                if raw_floor:
                    f_val = str(raw_floor[0] if isinstance(raw_floor, list) else raw_floor).lower()
                    if "ground" in f_val or "parter" in f_val:
                        floor = 0
                    else:
                        f_match = re.search(r"\d+", f_val)
                        if f_match:
                            try:
                                floor = int(f_match.group(0))
                            except ValueError:
                                pass
                raw_b_floors = target.get("Building_floors_num")
                if raw_b_floors:
                    bf_match = re.search(r"\d+", str(raw_b_floors))
                    if bf_match:
                        try:
                            floors_in_building = int(bf_match.group(0))
                        except ValueError:
                            pass

            # Private owner extraction
            is_private_owner = None
            raw_owner = item.get("ownerType") or (
                target.get("Advertiser_type") if "target" in locals() and target else None
            )
            if raw_owner:
                o_str = str(raw_owner[0] if isinstance(raw_owner, list) else raw_owner).upper()
                if "PRIVATE" in o_str or "PRYWATN" in o_str:
                    is_private_owner = True
                elif "AGENCY" in o_str or "BIURO" in o_str or "DEVELOPER" in o_str or "DEWELOPER" in o_str:
                    is_private_owner = False

            # Category
            from src.models.enums import PropertyCategory

            cat_str = self.profile.category if self.profile and hasattr(self.profile, "category") else "dom"
            try:
                category_enum = PropertyCategory(cat_str)
            except Exception:
                category_enum = PropertyCategory.DOM

            fingerprint = generate_property_fingerprint(
                price=price,
                area_home=area_home,
                area_plot=area_plot,
                street=street_name,
                district=district_name,
                location_raw=location_raw,
                title=title,
                category=cat_str,
            )

            return ListingSchema(
                id=item_id,
                portal="Otodom",
                title=title,
                url=url,
                price=price,
                price_per_m2=price_per_m2,
                area_home=area_home,
                area_plot=area_plot,
                category=category_enum,
                rooms=rooms,
                floor=floor,
                floors_in_building=floors_in_building,
                is_private_owner=is_private_owner,
                profile_id=getattr(self.profile, "id", None) if self.profile else None,
                profile_name=getattr(self.profile, "name", None) if self.profile else None,
                building_type=building_type,
                segment_subtype=SegmentSubtype.NIEOKRESLONY,  # Analyzed in Stage II
                location_raw=location_raw,
                street=street_name,
                district=district_name,
                city=city_name,
                coordinates=coordinates,
                access_road_type=road_type,
                market=market,
                finish_condition=finish_condition,
                has_visualisations=has_visualisations_from_meta,
                sewerage=sewerage,
                heating=heating,
                has_fiber=has_fiber,
                year_built=year_built,
                raw_description=raw_description,
                main_image_url=main_image_url,
                gallery_images=gallery_images,
                property_fingerprint=fingerprint,
                created_at=created_at,
                scraped_at=datetime.now(UTC),
                skip_detail=detail_skipped,
            )
        except Exception as err:
            logger.error(f"[OtodomScraper] Failed to parse item: {err}")
            return None

    async def scrape(self) -> list[ListingSchema]:
        """Execute full scrape of Otodom houses for configured location."""
        from src.services.config_manager import config_manager

        profile = self.profile or config_manager.get_profile()
        base_url = self.search_url or profile.get_otodom_url()
        city_name = profile.city
        category_name = getattr(profile, "category", "dom")

        logger.info(f"[{self.name}] Starting scrape for {city_name} ({category_name}) via: {base_url}")
        semaphore = asyncio.Semaphore(settings.CONCURRENT_REQUESTS)
        all_listings: list[ListingSchema] = []

        for page in range(1, self.max_pages + 1):
            if self.is_cancelled:
                logger.info(f"[{self.name}] Przerwano pobieranie stron - wykryto żądanie zatrzymania.")
                break
            join_char = "&" if "?" in base_url else "?"
            url = f"{base_url}{join_char}page={page}"
            logger.info(f"[{self.name}] Fetching search page {page}/{self.max_pages}: {url}")

            html = await self.fetch_html(url)
            if not html:
                logger.warning(f"[{self.name}] Empty response for page {page}, stopping pagination.")
                break

            next_data = self._extract_next_data(html)
            if not next_data:
                logger.error(f"[{self.name}] __NEXT_DATA__ tag not found on page {page}.")
                break

            page_props = next_data.get("props", {}).get("pageProps", {})
            search_ads = page_props.get("data", {}).get("searchAds", {})
            items = search_ads.get("items", [])

            if not items:
                logger.info(f"[{self.name}] No items found on page {page}. Terminating pagination.")
                break

            logger.info(f"[{self.name}] Page {page} returned {len(items)} listings. Processing...")

            total_pages_hint = self.max_pages
            pagination = search_ads.get("pagination", {})
            discovered = pagination.get("totalPages")
            if discovered:
                total_pages_hint = int(discovered)

            await self._emit_progress(
                page=page,
                total_pages=total_pages_hint,
                items_done=len(items),
                items_total=len(items),
                phase="search",
            )

            tasks = [self.parse_search_item(item, semaphore) for item in items]
            if settings.FETCH_DETAILS:
                results = []
                done = 0
                for coro in asyncio.as_completed(tasks):
                    if self.is_cancelled:
                        logger.info(f"[{self.name}] Przerwano pobieranie szczegółów - wykryto żądanie zatrzymania.")
                        break
                    results.append(await coro)
                    done += 1
                    if done % 3 == 0 or done == len(tasks):
                        await self._emit_progress(
                            page=page,
                            total_pages=total_pages_hint,
                            items_done=done,
                            items_total=len(tasks),
                            phase="detail",
                        )
            else:
                results = await asyncio.gather(*tasks)

            valid_items = [r for r in results if r is not None]
            all_listings.extend(valid_items)
            logger.info(f"[{self.name}] Page {page}: extracted {len(valid_items)} valid listings.")

            # Check if there are more pages
            if page >= total_pages_hint:
                logger.info(f"[{self.name}] Reached last page ({page}/{total_pages_hint}).")
                break

            await asyncio.sleep(self.delay(1.0))

        logger.info(f"[{self.name}] Completed scrape. Total listings gathered: {len(all_listings)}")
        return all_listings
