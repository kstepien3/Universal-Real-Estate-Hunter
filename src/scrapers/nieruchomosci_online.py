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

NIERUCHOMOSCI_ONLINE_BASE = "https://www.nieruchomosci-online.pl/"


class NieruchomosciOnlineScraper(BaseScraper):
    """
    Nieruchomosci-online.pl Scraper:
    Extracts house listings from structural HTML cards (div.tile) and enriches them
    with detail pages: attributes table (#detailsTable ul.list-h), JSON-LD (House) and description.
    """

    def __init__(
        self,
        max_pages: int = 2,
        search_url: str | None = None,
        profile: Any | None = None,
        skip_detail_urls: set | None = None,
    ):
        super().__init__(name="NieruchomosciOnlineScraper")
        self.max_pages = max_pages
        self.search_url = search_url
        self.profile = profile
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
        v = val.lower()
        if "surowy otwarty" in v or "surowy_otwarty" in v or "sso" in v:
            return FinishCondition.SUROWY_OTWARTY
        if "surowy zamknięty" in v or "surowy zamkniety" in v or "ssz" in v:
            return FinishCondition.SUROWY_ZAMKNIETY
        if "do wykończenia" in v or "do wykonczenia" in v or "to_completion" in v:
            return FinishCondition.DO_WYKONCZENIA
        if "dewelopersk" in v or "developer" in v:
            return FinishCondition.DEWELOPERSKI
        if "do zamieszkania" in v or "zamieszkan" in v or "pod klucz" in v or "wykończon" in v:
            return FinishCondition.DO_ZAMIESZKANIA
        if "remont" in v:
            return FinishCondition.DO_REMONTU
        return FinishCondition.NIEOKRESLONY

    def _map_heating(self, val: str | None) -> HeatingType:
        if not val:
            return HeatingType.NIEZNANE
        v = val.lower()
        if "pompa" in v or "heat pump" in v:
            return HeatingType.POMPA_CIEPLA
        if "gaz" in v:
            return HeatingType.GAZOWE
        if any(w in v for w in ["węgiel", "wegiel", "pellet", "ekogrosz", "drewno", "paliwo stałe"]):
            return HeatingType.PELLET_WEGIEL
        if "elektrycz" in v:
            return HeatingType.ELEKTRYCZNE
        if "miejsk" in v or "sieciow" in v:
            return HeatingType.MIEJSKIE
        return HeatingType.NIEZNANE

    def _map_sewerage(self, val: str | None) -> SewerageType:
        if not val:
            return SewerageType.NIEZNANA
        v = val.lower()
        if "szambo" in v or "zbiornik" in v:
            return SewerageType.SZAMBO
        if "oczyszczaln" in v:
            return SewerageType.OCZYSZCZALNIA
        if "kanalizacj" in v or "kanaliza" in v:
            return SewerageType.MIEJSKA
        return SewerageType.NIEZNANA

    def _map_road_type(self, val: str | None) -> RoadType:
        if not val:
            return RoadType.NIEZNANA
        v = val.lower()
        if "asfalt" in v:
            return RoadType.ASFALT
        if "kostk" in v or "bruk" in v:
            return RoadType.KOSTKA
        if "utwardzon" in v:
            return RoadType.UTWARDZONA
        if "poln" in v or "gruntow" in v or "nieutwardzon" in v:
            return RoadType.POLNA
        return RoadType.NIEZNANA

    def _parse_detail(self, html: str) -> dict[str, Any]:
        """Parse detail page: attributes table rows, JSON-LD and description."""
        soup = BeautifulSoup(html, "html.parser")
        result: dict[str, Any] = {}

        rows: dict[str, str] = {}
        unlabeled: list[str] = []
        for li in soup.select("#detailsTable ul.list-h li"):
            label_el = li.find("strong")
            value_el = li.find("span")
            value = value_el.get_text(" ", strip=True).replace("\xa0", " ") if value_el else ""
            if label_el:
                label = label_el.get_text(" ", strip=True).lower().strip(" \t\n:")
                rows[label] = value
            else:
                unlabeled.append(value)

        # Finish condition: "Charakterystyka domu" row contains "stan: do wykończenia"
        for label, value in rows.items():
            if "charakterystyk" in label or "powierzchnia" in label:
                m_stan = re.search(r"stan\s*[:;]?\s*([^;]+)", value, re.IGNORECASE)
                if m_stan:
                    result["finish_condition"] = self._map_finish_condition(m_stan.group(1).strip())
                # Area fallback: "100 m², 4 pokoje..."
                m_area = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", value)
                if m_area and not result.get("area_home"):
                    try:
                        result["area_home"] = float(m_area.group(1).replace(",", "."))
                    except ValueError:
                        pass
                m_rooms = re.search(r"(\d+)\s*pok", value, re.IGNORECASE)
                if m_rooms and "rooms" not in result:
                    result["rooms"] = int(m_rooms.group(1))

        # Media row: sewerage + heating + fiber
        media_text = rows.get("media", "")
        if media_text:
            result["sewerage"] = self._map_sewerage(media_text)
            m_heat = re.search(r"ogrzewanie\s*[:;]?\s*([^,;]+)", media_text, re.IGNORECASE)
            if m_heat:
                result["heating"] = self._map_heating(m_heat.group(1).strip())
            if "światłowód" in media_text or "swiatlowod" in media_text:
                result["has_fiber"] = True

        # Building type & year: "Rodzaj domu" / "Budynek" row
        type_text = rows.get("rodzaj domu", "") or rows.get("budynek", "")
        if type_text:
            result["building_type"] = self._map_building_type(type_text)
            m_year = re.search(r"rok\s*budowy\s*[:;]?\s*(\d{4})", type_text, re.IGNORECASE)
            if m_year:
                result["year_built"] = int(m_year.group(1))

        # Plot area row
        plot_text = rows.get("działka", "") or rows.get("dzialka", "")
        if plot_text:
            m_plot = re.search(r"(\d+(?:\s?\d{3})*(?:[.,]\d+)?)\s*m²", plot_text)
            if m_plot:
                try:
                    result["area_plot"] = float(m_plot.group(1).replace(" ", "").replace(",", "."))
                except ValueError:
                    pass

        # Road access often in unlabeled rows ("dojazd drogą asfaltową, ...")
        for value in unlabeled:
            if "dojazd" in value or "droga" in value:
                result["access_road_type"] = self._map_road_type(value)

        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            candidates = data if isinstance(data, list) else [data]
            for obj in candidates:
                if not isinstance(obj, dict):
                    continue
                at = obj.get("@type")
                at_list = at if isinstance(at, list) else [at]
                if "House" not in at_list:
                    continue
                geo = obj.get("geo") or {}
                lat, lon = geo.get("latitude"), geo.get("longitude")
                if lat is not None and lon is not None:
                    try:
                        lat_f, lon_f = float(lat), float(lon)
                        if lat_f or lon_f:
                            result["coordinates"] = (lat_f, lon_f)
                    except (TypeError, ValueError):
                        pass
                if obj.get("yearBuilt"):
                    try:
                        result["year_built"] = int(obj["yearBuilt"])
                    except (TypeError, ValueError):
                        pass
                if obj.get("floorSize"):
                    try:
                        result["area_home"] = float(str(obj["floorSize"]).replace(",", "."))
                    except (TypeError, ValueError):
                        pass
                add_props = obj.get("additionalProperty") or []
                for prop in add_props:
                    name = str(prop.get("name", "")).lower()
                    if "septic" in name and prop.get("value"):
                        result["sewerage"] = SewerageType.SZAMBO
                if obj.get("image"):
                    img_data = obj["image"]
                    if isinstance(img_data, list):
                        result["gallery_images"] = [i for i in img_data if isinstance(i, str)]
                    elif isinstance(img_data, str):
                        result["gallery_images"] = [img_data]
                break

        # Description
        desc_parts = [p.get_text(" ", strip=True) for p in soup.select("#boxCustomDescWrapper p.body-md")]
        if desc_parts:
            result["description"] = "\n".join(desc_parts)

        # Photos from HTML & render checks
        gallery: list[str] = result.get("gallery_images", [])
        render_indicators = ("render", "wizualizac", "visualis", "koncepcj", "rzut", "projekt-3d")
        has_render = False

        for img in soup.select("div.gallery img, #gallery img, a.photo img, img.main-photo, div.slider img"):
            src_attr = img.get("src") or img.get("data-src") or img.get("data-lazy")
            src = str(src_attr) if src_attr else ""
            if src and src not in gallery:
                if src.startswith("//"):
                    src = f"https:{src}"
                gallery.append(src)
            meta_str = f"{src} {img.get('alt', '') or ''} {img.get('title', '') or ''}".lower()
            if any(ind in meta_str for ind in render_indicators):
                has_render = True

        result["gallery_images"] = gallery[:15]
        if has_render:
            result["has_visualisations"] = True

        return result

    async def fetch_detail_html(self, url: str) -> str | None:
        """Fetch an offer detail page."""
        return await self.fetch_html(url, referer=NIERUCHOMOSCI_ONLINE_BASE)

    def _parse_tile(self, tile: Any) -> ListingSchema | None:
        try:
            # Title & Link
            title_a = tile.select_one("h2 a") or tile.select_one("a.title")
            if not title_a:
                return None

            title = title_a.get_text(strip=True)
            url = title_a.get("href", "")
            if not url:
                return None
            if not url.startswith("http"):
                from src.services.config_manager import slugify_city

                profile_city = getattr(self.profile, "city", None) or ""
                city_slug = slugify_city(profile_city) if profile_city else ""
                if city_slug:
                    url = f"https://{city_slug}.nieruchomosci-online.pl{url}"
                else:
                    url = f"https://www.nieruchomosci-online.pl{url}"

            # Portal ID
            id_match = re.search(r"/(\d+)\.html", url)
            portal_id = id_match.group(1) if id_match else url

            # Price
            price_val = 0.0
            price_display = tile.select_one("p.primary-display")
            if price_display:
                price_text = price_display.get_text(strip=True)
                # Find price matching e.g. "850 000 zł"
                m_price = re.search(r"([\d\s]+)\s*zł", price_text.replace("\xa0", " "))
                if m_price:
                    clean_p = re.sub(r"[^\d]", "", m_price.group(1))
                    if clean_p:
                        price_val = float(clean_p)

            # Area Home
            area_home = 0.0
            area_span = tile.select_one("span.area")
            if area_span:
                m_area = re.search(r"([\d\s,]+)\s*m²", area_span.get_text(strip=True))
                if m_area:
                    clean_a = m_area.group(1).replace(",", ".").replace(" ", "")
                    try:
                        area_home = float(clean_a)
                    except ValueError:
                        pass

            # Price per m2
            price_per_m2 = 0.0
            if price_val > 0 and area_home > 0:
                price_per_m2 = round(price_val / area_home, 2)

            # Plot Area & Attributes
            area_plot = None
            attr_box = tile.select_one("div.attributes__box")
            attr_text = attr_box.get_text(" ", strip=True) if attr_box else ""
            m_plot = re.search(r"działki:\s*([\d\s,]+)\s*m²", attr_text, re.IGNORECASE)
            if m_plot:
                clean_pl = m_plot.group(1).replace(",", ".").replace(" ", "")
                try:
                    area_plot = float(clean_pl)
                except ValueError:
                    pass

            # Location
            prov_p = tile.select_one("p.province")
            default_city = getattr(self.profile, "city", None) or ""
            location_raw = prov_p.get_text(" ", strip=True).replace("\xa0", " ") if prov_p else default_city

            # Image
            img_tag = tile.select_one("img")
            main_image_url = None
            if img_tag:
                main_image_url = img_tag.get("src") or img_tag.get("data-src")
                if main_image_url and main_image_url.startswith("//"):
                    main_image_url = f"https:{main_image_url}"

            # Description teaser
            teaser_div = tile.select_one("div.tile-details-teaser")
            raw_description = teaser_div.get_text("\n", strip=True) if teaser_div else title

            # Street from title or location
            street = None
            m_st = re.search(r"ul\.\s*([A-Za-ząćęłńóśźż\s]+)", f"{title} {location_raw}", re.IGNORECASE)
            if m_st:
                street = m_st.group(1).strip().split(",")[0]

            building_type = self._map_building_type(f"{title} {attr_text}")

            # Rooms & Floor extraction
            rooms = None
            m_rooms = re.search(r"(\d+)\s*pok", attr_text, re.IGNORECASE)
            if m_rooms:
                try:
                    rooms = int(m_rooms.group(1))
                except ValueError:
                    pass

            floor = None
            if "parter" in attr_text.lower():
                floor = 0
            else:
                m_fl = re.search(r"piętro\s*(\d+)", attr_text, re.IGNORECASE)
                if m_fl:
                    try:
                        floor = int(m_fl.group(1))
                    except ValueError:
                        pass

            # Category
            from src.models.enums import PropertyCategory

            cat_str = self.profile.category if self.profile and hasattr(self.profile, "category") else "dom"
            try:
                category_enum = PropertyCategory(cat_str)
            except Exception:
                category_enum = PropertyCategory.DOM

            fp = generate_property_fingerprint(
                price=price_val,
                area_home=area_home,
                area_plot=area_plot,
                street=street,
                location_raw=location_raw,
                title=title,
                category=cat_str,
            )

            return ListingSchema(
                id=portal_id,
                portal="NieruchomosciOnline",
                title=title,
                url=url,
                price=price_val,
                price_per_m2=price_per_m2,
                area_home=area_home,
                area_plot=area_plot,
                category=category_enum,
                rooms=rooms,
                floor=floor,
                profile_id=getattr(self.profile, "id", None) if self.profile else None,
                profile_name=getattr(self.profile, "name", None) if self.profile else None,
                building_type=building_type,
                segment_subtype=SegmentSubtype.NIEOKRESLONY,
                location_raw=location_raw,
                street=street,
                access_road_type=RoadType.NIEZNANA,
                market=MarketType.NIEOKRESLONY,
                raw_description=raw_description,
                main_image_url=main_image_url,
                gallery_images=[main_image_url] if main_image_url else [],
                property_fingerprint=fp,
                created_at=datetime.now(UTC),
                scraped_at=datetime.now(UTC),
            )
        except Exception as e:
            logger.debug(f"[NieruchomosciOnline] Error parsing tile: {e}")
            return None

    def _apply_detail(self, listing: ListingSchema, detail: dict[str, Any]) -> None:
        """Enrich listing with detail-page data (structured detail wins over tile teasers)."""
        if detail.get("finish_condition"):
            listing.finish_condition = detail["finish_condition"]
        if detail.get("heating"):
            listing.heating = detail["heating"]
        if detail.get("sewerage"):
            listing.sewerage = detail["sewerage"]
        if detail.get("access_road_type"):
            listing.access_road_type = detail["access_road_type"]
        if detail.get("building_type"):
            listing.building_type = detail["building_type"]
        if detail.get("year_built"):
            listing.year_built = detail["year_built"]
        if detail.get("has_fiber"):
            listing.has_fiber = True
        if detail.get("coordinates"):
            listing.coordinates = detail["coordinates"]
        if detail.get("area_plot") and (listing.area_plot or 0) <= 0:
            listing.area_plot = detail["area_plot"]
        if detail.get("area_home") and listing.area_home <= 0:
            listing.area_home = detail["area_home"]
            if listing.price > 0:
                listing.price_per_m2 = round(listing.price / listing.area_home, 2)
        if detail.get("rooms") is not None and listing.rooms is None:
            listing.rooms = detail["rooms"]
        if detail.get("description"):
            listing.raw_description = detail["description"]
        if detail.get("gallery_images"):
            for g_img in detail["gallery_images"]:
                if g_img not in listing.gallery_images:
                    listing.gallery_images.append(g_img)
            listing.gallery_images = listing.gallery_images[:15]
            if not listing.main_image_url and listing.gallery_images:
                listing.main_image_url = listing.gallery_images[0]
        if detail.get("has_visualisations"):
            listing.has_visualisations = True

    async def _enrich_listing(self, listing: ListingSchema, semaphore: asyncio.Semaphore) -> ListingSchema:
        """Fetch detail page and apply data (skipped when fresh data exists)."""
        if listing.url in self.skip_detail_urls:
            listing.skip_detail = True
            return listing
        async with semaphore:
            await asyncio.sleep(self.delay(0.4))  # Politeness delay
            html = await self.fetch_detail_html(listing.url)
        if html:
            try:
                self._apply_detail(listing, self._parse_detail(html))
            except Exception as err:
                logger.debug(f"[{self.name}] Failed to enrich detail for {listing.url}: {err}")
        return listing

    async def scrape(self) -> list[ListingSchema]:
        """Scrape listings from Nieruchomosci-online.pl for configured location."""
        from src.services.config_manager import config_manager

        profile = self.profile or config_manager.get_profile()
        base_url = self.search_url or profile.get_nieruchomosci_online_url()
        city_name = profile.city
        category_name = getattr(profile, "category", "dom")

        logger.info(f"[{self.name}] Starting scrape for {city_name} ({category_name}) via: {base_url}")
        listings: list[ListingSchema] = []

        for page in range(1, self.max_pages + 1):
            if self.is_cancelled:
                logger.info(f"[{self.name}] Przerwano pobieranie stron - wykryto żądanie zatrzymania.")
                break
            join_char = "&" if "?" in base_url else "?"
            url = base_url if page == 1 else f"{base_url}{join_char}p={page}"
            logger.info(f"[{self.name}] Fetching page {page}: {url}")

            html = await self.fetch_html(url)
            if not html:
                break

            soup = BeautifulSoup(html, "html.parser")
            tiles = soup.select("div.tile")
            if not tiles:
                logger.info(f"[{self.name}] No tiles found on page {page}.")
                break

            logger.info(f"[{self.name}] Found {len(tiles)} tiles on page {page}.")
            parsed_items = [self._parse_tile(tile) for tile in tiles]
            items = [i for i in parsed_items if i is not None]

            await self._emit_progress(
                page=page,
                total_pages=self.max_pages,
                items_done=len(items),
                items_total=len(items),
                phase="search",
            )

            if settings.FETCH_DETAILS:
                semaphore = asyncio.Semaphore(settings.CONCURRENT_REQUESTS)
                tasks = [self._enrich_listing(i, semaphore) for i in items]
                enriched = []
                done = 0
                for coro in asyncio.as_completed(tasks):
                    enriched.append(await coro)
                    done += 1
                    if done % 5 == 0 or done == len(tasks):
                        await self._emit_progress(
                            page=page,
                            total_pages=self.max_pages,
                            items_done=done,
                            items_total=len(tasks),
                            phase="detail",
                        )
                items = enriched
            listings.extend(items)

            await asyncio.sleep(self.delay(1.0))

        logger.info(f"[{self.name}] Scrape finished. Total items: {len(listings)}")
        return listings
