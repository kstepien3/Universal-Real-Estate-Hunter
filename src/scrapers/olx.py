import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger

from config import settings
from src.filters.fingerprint import generate_physical_fingerprint
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

OLX_BASE_URL = "https://www.olx.pl/"


class OLXScraper(BaseScraper):
    """
    OLX.pl Scraper:
    1. Extracts ads from OLX category 'domy na sprzedaż' for configured location.
    2. Decodes window.__PRERENDERED_STATE__ JSON or HTML fallback.
    3. Maps attributes and normalizes into ListingSchema.
    """

    def __init__(
        self,
        max_pages: int = 2,
        search_url: str | None = None,
        profile: Any | None = None,
        skip_detail_urls: set | None = None,
    ):
        super().__init__(name="OLXScraper")
        self.max_pages = max_pages
        self.search_url = search_url
        self.profile = profile
        self.skip_detail_urls = skip_detail_urls or set()

    def _extract_prerendered_state(self, html: str) -> dict[str, Any] | None:
        """Extract window.__PRERENDERED_STATE__ from OLX pages."""
        match = re.search(
            r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*"|\{.*?\});',
            html,
            re.DOTALL,
        )
        if match:
            raw = match.group(1)
            try:
                if raw.startswith('"'):
                    decoded = json.loads(raw)  # First decode: JSON string -> JSON text
                    if isinstance(decoded, str):
                        return json.loads(decoded)
                    return decoded
                return json.loads(raw)
            except Exception as e:
                logger.debug(f"[OLXScraper] Error parsing __PRERENDERED_STATE__: {e}")

        # Try alternative script tag
        match_script = re.search(r'<script id="olx-init-config"[^>]*>(.*?)</script>', html, re.DOTALL)
        if match_script:
            try:
                return json.loads(match_script.group(1))
            except Exception:
                pass

        return None

    async def fetch_ad_detail(self, url: str) -> dict[str, Any] | None:
        """Fetch full OLX listing detail page for comprehensive description and attributes."""
        html = await self.fetch_html(url)
        if not html:
            return None
        state = self._extract_prerendered_state(html)
        if state and isinstance(state, dict):
            ad_obj = state.get("ad", {}).get("ad") or state.get("ad")
            if ad_obj and isinstance(ad_obj, dict):
                return ad_obj
        # Fallback to BeautifulSoup parsing
        soup = BeautifulSoup(html, "html.parser")
        desc_el = (
            soup.select_one('div[data-cy="ad_description"]')
            or soup.select_one('[data-testid="ad-description"]')
            or soup.select_one(".css-bgzo2k")
        )
        desc_text = desc_el.get_text(separator="\n").strip() if desc_el else ""
        imgs = []
        for img in soup.select('img[src*="olxcdn.com"], img[src*="apollo.olxcdn.com"]'):
            src = img.get("src")
            if src and src not in imgs:
                imgs.append(src)
        return {"description": desc_text, "photos": [{"link": src} for src in imgs]}

    def _parse_params_dict(self, params: list[dict[str, Any]]) -> dict[str, Any]:
        """Convert OLX parameter list into key-value map, preferring normalizedValue (EN keys)."""
        res: dict[str, Any] = {}
        for p in params:
            key = p.get("key")
            if not isinstance(key, str):
                continue
            val = p.get("value")
            if isinstance(val, dict):
                val = val.get("key") or val.get("label") or val.get("value")
            norm = p.get("normalizedValue")
            if norm:
                res[key] = norm
            elif val:
                res[key] = val
        return res

    def _map_building_type(self, val: str | None) -> BuildingType:
        if not val:
            return BuildingType.INNY
        v = str(val).lower()
        if "szereg" in v or "segment" in v or "ribbon" in v:
            return BuildingType.SZEREGOWIEC
        if "blizniak" in v or "bliźniak" in v:
            return BuildingType.BLIZNIAK
        if "wolnostoj" in v:
            return BuildingType.WOLNOSTOJACY
        return BuildingType.INNY

    def _map_market(self, val: str | None) -> MarketType:
        if not val:
            return MarketType.NIEOKRESLONY
        v = str(val).lower()
        if "pierwotny" in v or "primary" in v:
            return MarketType.PIERWOTNY
        if "wtorny" in v or "wtórny" in v or "secondary" in v:
            return MarketType.WTORNY
        return MarketType.NIEOKRESLONY

    def _map_finish_condition(self, val: str | None) -> FinishCondition:
        if not val:
            return FinishCondition.NIEOKRESLONY
        v = str(val).lower()
        if "dewelopersk" in v or "to_completion" in v:
            return FinishCondition.DEWELOPERSKI
        if "zamieszkan" in v or "ready_to_use" in v or "klucz" in v:
            return FinishCondition.DO_ZAMIESZKANIA
        if "unfinished_close" in v or "surowy_zamkniety" in v or "surowy zamknięty" in v or "ssz" in v:
            return FinishCondition.SUROWY_ZAMKNIETY
        if "unfinished_open" in v or "surowy_otwarty" in v or "surowy otwarty" in v or "sso" in v:
            return FinishCondition.SUROWY_OTWARTY
        if "remont" in v or "to_renovation" in v:
            return FinishCondition.DO_REMONTU
        return FinishCondition.NIEOKRESLONY

    def _map_heating(self, val: str | None) -> HeatingType:
        if not val:
            return HeatingType.NIEZNANE
        v = str(val).lower()
        if "pompa" in v or "heat_pump" in v:
            return HeatingType.POMPA_CIEPLA
        if "gaz" in v or "gas" in v:
            return HeatingType.GAZOWE
        if any(w in v for w in ["wegiel", "węgiel", "pellet", "piec", "solid_fuel", "drewno"]):
            return HeatingType.PELLET_WEGIEL
        if "elektr" in v or "electric" in v:
            return HeatingType.ELEKTRYCZNE
        if "miejsk" in v or "urban" in v:
            return HeatingType.MIEJSKIE
        return HeatingType.NIEZNANE

    def _map_sewerage(self, val: Any) -> SewerageType:
        if not val:
            return SewerageType.NIEZNANA
        v = str(val).lower()
        if "szambo" in v or "septic" in v:
            return SewerageType.SZAMBO
        if "oczyszczalnia" in v or "treatment" in v:
            return SewerageType.OCZYSZCZALNIA
        if "kanalizacja" in v or "miejsk" in v or "sewage" in v:
            return SewerageType.MIEJSKA
        return SewerageType.NIEZNANA

    async def parse_ad(self, ad: dict[str, Any]) -> ListingSchema | None:
        try:
            ad_id = str(ad.get("id"))
            title = ad.get("title", "").strip()
            url = ad.get("url", "")
            if not url.startswith("http"):
                url = f"https://www.olx.pl{url}"

            cat_str = self.profile.category if self.profile and hasattr(self.profile, "category") else "dom"

            # Pricing
            price_info = ad.get("price", {})
            price = 0.0
            if isinstance(price_info, dict):
                regular = price_info.get("regularPrice") or {}
                if isinstance(regular, dict):
                    price = float(regular.get("value", 0.0) or 0.0)

            params_map = self._parse_params_dict(ad.get("params", []))

            # Home Area ('m' = dwelling area)
            area_raw = params_map.get("m")
            area_home = 0.0
            if area_raw:
                try:
                    area_home = float(str(area_raw).replace(",", ".").replace("m²", "").replace("m2", "").strip())
                except ValueError:
                    pass

            # Plot Area ('area' for houses; plot_area/terrain_area otherwise)
            plot_raw = params_map.get("plot_area") or params_map.get("terrain_area")
            if cat_str == "dom":
                plot_raw = plot_raw or params_map.get("area")
            area_plot = None
            if plot_raw:
                try:
                    area_plot = float(
                        str(plot_raw).replace(",", ".").replace("m²", "").replace("m2", "").replace("ar", "").strip()
                    )
                except ValueError:
                    pass

            # Calculate price per m2
            price_per_m2 = 0.0
            if price > 0 and area_home > 0:
                price_per_m2 = round(price / area_home, 2)

            # Location
            location_obj = ad.get("location", {})
            default_city = getattr(self.profile, "city", None) or ""
            city_name = location_obj.get("cityName", default_city)
            district_name = location_obj.get("districtName")
            location_raw = f"{city_name}, {district_name}" if district_name else city_name

            # Coordinates
            coordinates = None
            map_data = ad.get("map", {})
            if map_data.get("lat") and map_data.get("lon"):
                coordinates = (float(map_data["lat"]), float(map_data["lon"]))

            # Photos
            photos = ad.get("photos", [])
            main_image_url = None
            gallery_images: list[str] = []
            has_visualisations_from_meta = False
            render_indicators = ("render", "wizualizac", "visualis", "koncepcj", "rzut", "projekt-3d")

            if photos and isinstance(photos, list):
                for p in photos:
                    u = None
                    if isinstance(p, dict):
                        u = p.get("link") or p.get("url")
                    elif isinstance(p, str) and p.startswith("http"):
                        u = p
                    if u and u not in gallery_images:
                        gallery_images.append(u)
                        if any(ind in u.lower() for ind in render_indicators):
                            has_visualisations_from_meta = True
            if gallery_images:
                main_image_url = gallery_images[0]
            gallery_images = gallery_images[:15]

            # Description
            description = ad.get("description", "")
            soup = BeautifulSoup(description, "html.parser")
            clean_desc = soup.get_text(separator="\n").strip()

            # Building type & Market
            building_type = self._map_building_type(
                params_map.get("builttype") or params_map.get("build_type") or params_map.get("type")
            )
            market = self._map_market(params_map.get("market"))
            finish_condition = self._map_finish_condition(
                params_map.get("stan_wykonczenia")
                or params_map.get("furnishing")
                or params_map.get("construction_status")
            )
            heating = self._map_heating(params_map.get("heating") or params_map.get("ogrzewanie"))
            sewerage = self._map_sewerage(
                params_map.get("sewerage") or params_map.get("kanalizacja") or params_map.get("media")
            )

            # Rooms extraction
            rooms = None
            raw_rooms = params_map.get("rooms") or params_map.get("liczba_pokoi")
            if raw_rooms:
                r_map = {"one": 1, "two": 2, "three": 3, "four_more": 4, "cztery_i_wiecej": 4}
                val = str(raw_rooms).lower()
                if val in r_map:
                    rooms = r_map[val]
                else:
                    m = re.search(r"\d+", val)
                    if m:
                        try:
                            rooms = int(m.group(0))
                        except ValueError:
                            pass

            # Floor extraction ('floor_select' = "Liczba pięter" for houses, "Poziom" for flats)
            floor = None
            raw_floor = params_map.get("floor_select") or params_map.get("pietro")
            if raw_floor:
                f_val = str(raw_floor).lower()
                if cat_str != "dom":
                    if "parter" in f_val or "ground" in f_val:
                        floor = 0
                    else:
                        m = re.search(r"\d+", f_val)
                        if m:
                            try:
                                floor = int(m.group(0))
                            except ValueError:
                                pass

            floors_in_building = None
            raw_bf = params_map.get("number_of_floors_select") or params_map.get("liczba_pieter")
            if cat_str == "dom" and raw_floor:
                # floor_select for houses = "Liczba pięter" (floor_0 = parterowy = 1 kondygnacja)
                m = re.search(r"\d+", str(raw_floor))
                if m:
                    floors_in_building = int(m.group(0)) + 1
            elif raw_bf:
                m = re.search(r"\d+", str(raw_bf))
                if m:
                    try:
                        floors_in_building = int(m.group(0))
                    except ValueError:
                        pass

            # Private owner
            is_private_owner = None
            if "is_business" in ad:
                is_private_owner = not bool(ad.get("is_business"))
            elif "user" in ad and isinstance(ad["user"], dict) and "is_business" in ad["user"]:
                is_private_owner = not bool(ad["user"].get("is_business"))
            elif "private_business" in params_map:
                pb = str(params_map["private_business"]).lower()
                is_private_owner = "private" in pb

            # Category
            from src.models.enums import PropertyCategory

            try:
                category_enum = PropertyCategory(cat_str)
            except Exception:
                category_enum = PropertyCategory.DOM

            physical_fp = generate_physical_fingerprint(
                area_home=area_home,
                area_plot=area_plot,
                rooms=rooms,
                district=district_name,
                city=city_name,
                location_raw=location_raw,
                title=title,
                category=cat_str,
            )

            return ListingSchema(
                id=ad_id,
                portal="OLX",
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
                segment_subtype=SegmentSubtype.NIEOKRESLONY,
                location_raw=location_raw,
                district=district_name,
                city=city_name,
                coordinates=coordinates,
                access_road_type=RoadType.NIEZNANA,
                market=market,
                finish_condition=finish_condition,
                has_visualisations=has_visualisations_from_meta,
                sewerage=sewerage,
                heating=heating,
                raw_description=clean_desc,
                main_image_url=main_image_url,
                gallery_images=gallery_images,
                physical_fingerprint=physical_fp,
                created_at=datetime.now(UTC),
                scraped_at=datetime.now(UTC),
            )
        except Exception as e:
            logger.error(f"[OLXScraper] Error parsing ad: {e}")
            return None

    async def scrape(self) -> list[ListingSchema]:
        """Scrape OLX listings for configured location."""
        from src.services.config_manager import config_manager

        profile = self.profile or config_manager.get_profile()
        base_url = self.search_url or profile.get_olx_url()
        city_name = profile.city
        category_name = getattr(profile, "category", "dom")

        logger.info(f"[{self.name}] Scraping OLX listings for {city_name} ({category_name}) via: {base_url}")
        listings: list[ListingSchema] = []

        for page in range(1, self.max_pages + 1):
            if self.is_cancelled:
                logger.info(f"[{self.name}] Przerwano pobieranie stron - wykryto żądanie zatrzymania.")
                break
            join_char = "&" if "?" in base_url else "?"
            url = f"{base_url}{join_char}page={page}"
            logger.info(f"[{self.name}] Fetching page {page}: {url}")
            html = await self.fetch_html(url)
            if not html:
                break

            state = self._extract_prerendered_state(html)
            if state:
                ads = state.get("listing", {}).get("listing", {}).get("ads", []) or state.get("adList", {}).get(
                    "ads", []
                )
                logger.info(f"[{self.name}] Extracted {len(ads)} ads from state.")
                await self._emit_progress(
                    page=page, total_pages=self.max_pages, items_done=len(ads), items_total=len(ads)
                )
                for ad in ads:
                    if self.is_cancelled:
                        break
                    parsed = await self.parse_ad(ad)
                    if parsed:
                        listings.append(parsed)
            else:
                # Fallback: parse listing cards via BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select('div[data-cy="l-card"]')
                logger.info(f"[{self.name}] Parsed {len(cards)} cards via DOM selectors.")
                await self._emit_progress(
                    page=page, total_pages=self.max_pages, items_done=len(cards), items_total=len(cards)
                )

                semaphore = asyncio.Semaphore(settings.CONCURRENT_REQUESTS)

                async def parse_card(card, sem=semaphore):
                    try:
                        link_tag = card.select_one("a")
                        title_tag = card.select_one("h6") or card.select_one("h4")
                        price_tag = card.select_one('p[data-testid="ad-price"]')
                        if not link_tag or not title_tag:
                            return None

                        rel_url = str(link_tag.get("href") or "")
                        abs_url = f"https://www.olx.pl{rel_url}" if rel_url.startswith("/") else rel_url
                        title_txt = title_tag.get_text(strip=True)
                        price_txt = price_tag.get_text(strip=True) if price_tag else "0"

                        # Extract price
                        price_val = 0.0
                        clean_p = re.sub(r"[^\d]", "", price_txt)
                        if clean_p:
                            price_val = float(clean_p)

                        ad_id_match = re.search(r"-ID([a-zA-Z0-9]+)\.html", abs_url) or re.search(r"(\d+)", abs_url)
                        ad_id = str(ad_id_match.group(1)) if ad_id_match else abs_url

                        # Extract area from title or subtitle
                        desc_p = card.select_one('span[data-testid="location-date"]')
                        fallback_city = getattr(self.profile, "city", None) or ""
                        loc_txt = desc_p.get_text(strip=True) if desc_p else fallback_city

                        physical_fp = generate_physical_fingerprint(
                            area_home=0.0,  # unknown in HTML fallback
                            area_plot=None,
                            city=getattr(self.profile, "city", None),
                            location_raw=loc_txt,
                            title=title_txt,
                        )

                        item = ListingSchema(
                            id=ad_id,
                            portal="OLX",
                            title=title_txt,
                            url=abs_url,
                            price=price_val,
                            price_per_m2=0.0,
                            area_home=0.0,
                            location_raw=loc_txt,
                            physical_fingerprint=physical_fp,
                            created_at=datetime.now(UTC),
                            scraped_at=datetime.now(UTC),
                        )

                        if settings.FETCH_DETAILS and abs_url not in self.skip_detail_urls:
                            async with sem:
                                await asyncio.sleep(self.delay(0.3))
                                det_ad = await self.fetch_ad_detail(abs_url)
                                if det_ad and isinstance(det_ad, dict):
                                    if det_ad.get("params"):
                                        det_params = self._parse_params_dict(det_ad.get("params", []))
                                        if det_params.get("m"):
                                            try:
                                                item.area_home = float(
                                                    str(det_params["m"])
                                                    .replace(",", ".")
                                                    .replace("m²", "")
                                                    .replace("m2", "")
                                                    .strip()
                                                )
                                                if item.price > 0 and item.area_home > 0:
                                                    item.price_per_m2 = round(item.price / item.area_home, 2)
                                            except ValueError:
                                                pass
                                        if det_params.get("stan_wykonczenia") or det_params.get("construction_status"):
                                            item.finish_condition = self._map_finish_condition(
                                                det_params.get("stan_wykonczenia")
                                                or det_params.get("construction_status")
                                            )
                                    if det_ad.get("description"):
                                        item.raw_description = (
                                            BeautifulSoup(det_ad["description"], "html.parser")
                                            .get_text(separator="\n")
                                            .strip()
                                        )
                                    if det_ad.get("photos"):
                                        det_gallery = [
                                            str(p.get("link") or p.get("url"))
                                            for p in det_ad["photos"]
                                            if isinstance(p, dict) and (p.get("link") or p.get("url"))
                                        ]
                                        item.gallery_images = det_gallery[:15]
                                        if det_gallery and not item.main_image_url:
                                            item.main_image_url = det_gallery[0]

                        return item
                    except Exception as e:
                        logger.debug(f"[OLXScraper] Failed to parse card: {e}")
                        return None

                results = await asyncio.gather(*[parse_card(card) for card in cards])
                listings.extend(item for item in results if item is not None)

            await asyncio.sleep(self.delay(1.0))

        logger.info(f"[{self.name}] Scrape finished. Total items: {len(listings)}")
        return listings
