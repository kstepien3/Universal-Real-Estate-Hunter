import asyncio
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
    PropertyCategory,
    RoadType,
    SegmentSubtype,
    SewerageType,
)
from src.models.listing import ListingSchema

from .base import BaseScraper

MORIZON_BASE = "https://www.morizon.pl/"


class MorizonScraper(BaseScraper):
    """
    Morizon.pl Scraper:
    Parses property listings from Morizon.pl search results using semantic data-cy card selectors.
    Covers real estate agency and direct owner listings from Morizon and Gratka network.
    """

    def __init__(
        self,
        max_pages: int = 2,
        search_url: str | None = None,
        profile: Any | None = None,
        delay_seconds: float = 1.0,
        skip_detail_urls: set | None = None,
    ):
        super().__init__(name="MorizonScraper")
        self.max_pages = max_pages
        self.search_url = search_url
        self.profile = profile
        self.delay_seconds = delay_seconds
        self.skip_detail_urls = skip_detail_urls or set()

    def _map_building_type(self, text: str) -> BuildingType:
        t = text.lower()
        if "szereg" in t or "segment" in t:
            return BuildingType.SZEREGOWIEC
        if "blizniak" in t or "bliźniak" in t:
            return BuildingType.BLIZNIAK
        if "wolnostoj" in t:
            return BuildingType.WOLNOSTOJACY
        return BuildingType.INNY

    def _map_finish_condition(self, val: str | None) -> FinishCondition:
        if not val:
            return FinishCondition.NIEOKRESLONY
        v = str(val).lower()
        if "dewelopersk" in v or "developer" in v:
            return FinishCondition.DEWELOPERSKI
        if "do zamieszkania" in v or "zamieszkan" in v or "pod klucz" in v:
            return FinishCondition.DO_ZAMIESZKANIA
        if "do wykończenia" in v or "do wykonczenia" in v:
            return FinishCondition.DO_WYKONCZENIA
        if "surowy zamknięty" in v or "surowy zamkniety" in v or "ssz" in v:
            return FinishCondition.SUROWY_ZAMKNIETY
        if "surowy otwarty" in v or "sso" in v:
            return FinishCondition.SUROWY_OTWARTY
        if "remont" in v:
            return FinishCondition.DO_REMONTU
        return FinishCondition.NIEOKRESLONY

    def _map_heating(self, val: str | None) -> HeatingType:
        if not val:
            return HeatingType.NIEZNANE
        v = str(val).lower()
        if "pompa" in v or "heat pump" in v:
            return HeatingType.POMPA_CIEPLA
        if "gaz" in v:
            return HeatingType.GAZOWE
        if any(w in v for w in ["węgiel", "wegiel", "pellet", "ekogrosz", "drewno", "stałe"]):
            return HeatingType.PELLET_WEGIEL
        if "elektrycz" in v:
            return HeatingType.ELEKTRYCZNE
        if "miejsk" in v:
            return HeatingType.MIEJSKIE
        return HeatingType.NIEZNANE

    def _map_sewerage(self, val: str | None) -> SewerageType:
        if not val:
            return SewerageType.NIEZNANA
        v = str(val).lower()
        if "szambo" in v or "zbiornik" in v:
            return SewerageType.SZAMBO
        if "oczyszczaln" in v:
            return SewerageType.OCZYSZCZALNIA
        if "kanalizacj" in v or "miejsk" in v:
            return SewerageType.MIEJSKA
        return SewerageType.NIEZNANA

    def _map_road_type(self, val: str | None) -> RoadType:
        if not val:
            return RoadType.NIEZNANA
        v = str(val).lower()
        if "asfalt" in v:
            return RoadType.ASFALT
        if "kostk" in v or "bruk" in v:
            return RoadType.KOSTKA
        if "utwardzon" in v:
            return RoadType.UTWARDZONA
        if "poln" in v or "gruntow" in v:
            return RoadType.POLNA
        return RoadType.NIEZNANA

    async def fetch_listing_detail(self, url: str) -> dict[str, Any]:
        """Fetch Morizon listing detail page for full description, table parameters and photos."""
        html = await self.fetch_html(url)
        if not html:
            return {}
        soup = BeautifulSoup(html, "html.parser")
        res: dict[str, Any] = {}

        # Description
        desc_el = (
            soup.select_one("section.property-description")
            or soup.select_one("div.description")
            or soup.select_one("div.show-more__content")
            or soup.select_one('[data-cy="propertyDescription"]')
        )
        if desc_el:
            res["description"] = desc_el.get_text(separator="\n").strip()

        # Parameters table/list
        params: dict[str, str] = {}
        for row in soup.select("tr, div.parameters__row, li.parameters__item, div.property-details__item"):
            txt = row.get_text(" ", strip=True).lower()
            if ":" in txt:
                k, v = txt.split(":", 1)
                params[k.strip()] = v.strip()
            elif "stan" in txt:
                params["stan"] = txt
            elif "ogrzewan" in txt:
                params["ogrzewanie"] = txt
            elif "kanalizac" in txt or "ściek" in txt:
                params["kanalizacja"] = txt
            elif "dojazd" in txt:
                params["dojazd"] = txt

        for k, v in params.items():
            if any(term in k for term in ["stan", "wykończeni"]):
                res["finish_condition"] = self._map_finish_condition(v)
            elif "ogrzewan" in k:
                res["heating"] = self._map_heating(v)
            elif "kanalizac" in k:
                res["sewerage"] = self._map_sewerage(v)
            elif "dojazd" in k:
                res["access_road_type"] = self._map_road_type(v)

        # Photos
        imgs: list[str] = []
        for img in soup.select("img"):
            src_attr = img.get("src") or img.get("data-src")
            src = str(src_attr) if src_attr else ""
            if src and any(domain in src for domain in ["morizon", "gratka"]) and src not in imgs:
                if src.startswith("//"):
                    src = f"https:{src}"
                imgs.append(src)
        if imgs:
            res["gallery_images"] = imgs[:15]

        return res

    def _parse_card(self, card: Any) -> ListingSchema | None:
        try:
            # URL & Portal ID
            link = (
                card.select_one('[data-cy="propertyUrl"]')
                or card.select_one("a.property-card__link")
                or card.select_one('a[href*="/oferta/"]')
            )
            if not link:
                return None

            href = link.get("href", "")
            if not href or "/oferta/" not in href:
                return None

            url = href if href.startswith("http") else f"https://www.morizon.pl{href}"

            # Portal ID extraction
            id_match = re.search(r"-([a-zA-Z0-9]+)$", url)
            portal_id = id_match.group(1) if id_match else url

            # Title
            title_el = card.select_one('[data-cy="propertyCardTitle"]') or card.select_one(".property-card__title")
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

            # Price
            price_val = 0.0
            price_el = card.select_one('[data-cy="propertyCardPrice"]') or card.select_one(
                ".property-card__price--main"
            )
            if price_el:
                clean_p = re.sub(r"[^\d]", "", price_el.get_text(strip=True))
                if clean_p:
                    price_val = float(clean_p)

            # Price per m2
            price_m2_val = 0.0
            price_m2_el = card.select_one('[data-cy="offerPricePerM2"]') or card.select_one(
                ".property-card__price--perM2"
            )
            if price_m2_el:
                clean_m2 = re.sub(r"[^\d]", "", price_m2_el.get_text(strip=True))
                if clean_m2:
                    price_m2_val = float(clean_m2)

            # Area
            area_val = 0.0
            area_el = card.select_one('[data-cy="cardPropertyInfoArea"]')
            if area_el:
                m_a = re.search(r"([\d\s,]+)\s*m", area_el.get_text(strip=True))
                if m_a:
                    clean_a = m_a.group(1).replace(",", ".").replace(" ", "")
                    try:
                        area_val = float(clean_a)
                    except ValueError:
                        pass

            if price_m2_val == 0.0 and price_val > 0 and area_val > 0:
                price_m2_val = round(price_val / area_val, 2)

            # Rooms
            rooms = None
            rooms_el = card.select_one('[data-cy="cardPropertyInfoRooms"]')
            if rooms_el:
                m_r = re.search(r"(\d+)", rooms_el.get_text(strip=True))
                if m_r:
                    try:
                        rooms = int(m_r.group(1))
                    except ValueError:
                        pass

            # Location & Street
            loc_el = card.select_one('[data-cy="propertyCardLocation"]') or card.select_one(".property-card__location")
            default_city = getattr(self.profile, "city", None) or ""
            location_raw = loc_el.get_text(" ", strip=True) if loc_el else default_city

            street = None
            district = None
            city = None
            parts = [p.strip() for p in location_raw.split(",") if p.strip()]
            if len(parts) >= 3:
                street = parts[0]
                district = parts[1]
                city = parts[2]
            elif len(parts) == 2:
                district = parts[0]
                city = parts[1]

            # Description
            desc_el = (
                card.select_one(".description__content")
                or card.select_one(".description")
                or card.select_one(".show-more__content")
            )
            raw_description = desc_el.get_text("\n", strip=True) if desc_el else title

            # Category
            cat_str = self.profile.category if self.profile and hasattr(self.profile, "category") else "dom"
            try:
                category_enum = PropertyCategory(cat_str)
            except Exception:
                category_enum = PropertyCategory.DOM

            # Area Plot & Home mapping
            area_home = 0.0
            area_plot = None
            if cat_str == "dzialka":
                area_plot = area_val
                area_home = area_val
            elif cat_str == "dom":
                area_home = area_val if area_val > 0 else 100.0
                m_ogrod = re.search(
                    r"(?:działk[ai]|ogród|ogrod(?:em)?|posesj[ai])\s*(?:o\s*pow\.?)?\s*([\d\s,]+)\s*(ara?|ar[oó]w|m2|m²)",
                    raw_description,
                    re.IGNORECASE,
                )
                if m_ogrod:
                    val = float(m_ogrod.group(1).replace(",", ".").replace(" ", ""))
                    unit = m_ogrod.group(2).lower()
                    if "ar" in unit:
                        area_plot = round(val * 100.0, 2)
                    else:
                        area_plot = round(val, 2)
            else:  # mieszkanie
                area_home = area_val if area_val > 0 else 50.0

            # Images
            gallery_images: list[str] = []
            has_visualisations_from_meta = False
            render_indicators = ("render", "wizualizac", "visualis", "koncepcj", "rzut", "projekt-3d")

            for img in card.select('[data-cy="gallerySliderImgThumbnail"], img.gallery-slider__img, img'):
                src = img.get("src") or img.get("data-src")
                if src:
                    if src.startswith("//"):
                        src = f"https:{src}"
                    if src not in gallery_images:
                        gallery_images.append(src)
                meta_str = f"{src or ''} {img.get('alt', '')} {img.get('title', '')}".lower()
                if any(ind in meta_str for ind in render_indicators):
                    has_visualisations_from_meta = True

            main_image_url = gallery_images[0] if gallery_images else None
            gallery_images = gallery_images[:15]

            # Market & Owner
            full_text = f"{title} {raw_description}".lower()
            if "pierwotny" in full_text or "deweloper" in full_text:
                market = MarketType.PIERWOTNY
            elif "wtórny" in full_text:
                market = MarketType.WTORNY
            else:
                market = MarketType.NIEOKRESLONY

            is_private = None
            if "bezpośrednio" in full_text or "oferta bezpośrednia" in full_text:
                is_private = True
            elif "biuro nieruchomości" in full_text or "agencja" in full_text or "pośrednik" in full_text:
                is_private = False

            building_type = self._map_building_type(f"{title} {raw_description}")

            physical_fp = generate_physical_fingerprint(
                area_home=area_home,
                area_plot=area_plot,
                street=street,
                city=city,
                location_raw=location_raw,
                title=title,
                category=cat_str,
            )

            return ListingSchema(
                id=portal_id,
                portal="Morizon",
                title=title,
                url=url,
                price=price_val,
                price_per_m2=price_m2_val,
                area_home=area_home,
                area_plot=area_plot,
                category=category_enum,
                rooms=rooms,
                floor=None,
                profile_id=getattr(self.profile, "id", None) if self.profile else None,
                profile_name=getattr(self.profile, "name", None) if self.profile else None,
                building_type=building_type,
                segment_subtype=SegmentSubtype.NIEOKRESLONY,
                location_raw=location_raw,
                street=street,
                district=district,
                city=city,
                access_road_type=RoadType.NIEZNANA,
                market=market,
                finish_condition=FinishCondition.NIEOKRESLONY,
                has_visualisations=has_visualisations_from_meta,
                is_private_owner=is_private,
                raw_description=raw_description,
                main_image_url=main_image_url,
                gallery_images=gallery_images,
                physical_fingerprint=physical_fp,
                created_at=datetime.now(UTC),
                scraped_at=datetime.now(UTC),
            )
        except Exception as e:
            logger.debug(f"[Morizon] Error parsing card: {e}")
            return None

    async def scrape(self) -> list[ListingSchema]:
        """Scrape listings from Morizon.pl for configured location and category."""
        from src.services.config_manager import config_manager

        profile = self.profile or config_manager.get_profile()
        base_url = self.search_url or profile.get_morizon_url()
        city_name = profile.city
        category_name = getattr(profile, "category", "dom")

        logger.info(f"[{self.name}] Starting scrape for {city_name} ({category_name}) via: {base_url}")
        listings: list[ListingSchema] = []

        for page in range(1, self.max_pages + 1):
            if self.is_cancelled:
                logger.info(f"[{self.name}] Przerwano pobieranie stron - wykryto żądanie zatrzymania.")
                break
            join_char = "&" if "?" in base_url else "?"
            url = base_url if page == 1 else f"{base_url}{join_char}page={page}"
            logger.info(f"[{self.name}] Fetching page {page}: {url}")

            html = await self.fetch_html(url)
            if not html:
                break

            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select("div.card")
            valid_cards = [c for c in cards if c.select_one('a[href*="/oferta/"]')]

            if not valid_cards:
                logger.info(f"[{self.name}] No offer cards found on page {page}.")
                break

            logger.info(f"[{self.name}] Found {len(valid_cards)} offer cards on page {page}.")
            await self._emit_progress(
                page=page, total_pages=self.max_pages, items_done=len(valid_cards), items_total=len(valid_cards)
            )

            semaphore = asyncio.Semaphore(settings.CONCURRENT_REQUESTS)

            async def process_card(card, sem=semaphore):
                item = self._parse_card(card)
                if not item:
                    return None
                if settings.FETCH_DETAILS and item.url not in self.skip_detail_urls:
                    async with sem:
                        await asyncio.sleep(self.delay(0.3))
                        try:
                            det = await self.fetch_listing_detail(item.url)
                            if det:
                                if det.get("description"):
                                    item.raw_description = det["description"]
                                if det.get("finish_condition"):
                                    item.finish_condition = det["finish_condition"]
                                if det.get("heating"):
                                    item.heating = det["heating"]
                                if det.get("sewerage"):
                                    item.sewerage = det["sewerage"]
                                if det.get("access_road_type"):
                                    item.access_road_type = det["access_road_type"]
                                if det.get("gallery_images"):
                                    for g_img in det["gallery_images"]:
                                        if g_img not in item.gallery_images:
                                            item.gallery_images.append(g_img)
                                    item.gallery_images = item.gallery_images[:15]
                                    if not item.main_image_url and item.gallery_images:
                                        item.main_image_url = item.gallery_images[0]
                        except Exception as err:
                            logger.debug(f"[{self.name}] Detail enrichment failed for {item.url}: {err}")
                return item

            results = await asyncio.gather(*[process_card(card) for card in valid_cards])
            listings.extend(item for item in results if item is not None)

            await asyncio.sleep(self.delay(self.delay_seconds))

        logger.info(f"[{self.name}] Scrape finished. Total items: {len(listings)}")
        return listings
