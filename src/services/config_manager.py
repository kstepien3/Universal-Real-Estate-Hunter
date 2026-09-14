import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from config import settings

CONFIG_FILE_PATH = Path(os.getenv("SEARCH_CONFIG_PATH", "search_config.json"))

POLISH_CHAR_MAP = {
    "ą": "a",
    "ć": "c",
    "ę": "e",
    "ł": "l",
    "ń": "n",
    "ó": "o",
    "ś": "s",
    "ź": "z",
    "ż": "z",
    "Ą": "a",
    "Ć": "c",
    "Ę": "e",
    "Ł": "l",
    "Ń": "n",
    "Ó": "o",
    "Ś": "s",
    "Ź": "z",
    "Ż": "z",
}

# Major Polish cities mapping for Otodom canonical paths
OTODOM_CITY_PATHS = {
    "warszawa": "mazowieckie/warszawa/warszawa/warszawa",
    "krakow": "malopolskie/krakow/krakow/krakow",
    "lodz": "lodzkie/lodz/lodz/lodz",
    "wroclaw": "dolnoslaskie/wroclaw/wroclaw/wroclaw",
    "poznan": "wielkopolskie/poznan/poznan/poznan",
    "gdansk": "pomorskie/gdansk/gdansk/gdansk",
    "gdynia": "pomorskie/gdynia/gdynia/gdynia",
    "sopot": "pomorskie/sopot/sopot/sopot",
    "szczecin": "zachodniopomorskie/szczecin/szczecin/szczecin",
    "bydgoszcz": "kujawsko-pomorskie/bydgoszcz/bydgoszcz/bydgoszcz",
    "torun": "kujawsko-pomorskie/torun/torun/torun",
    "lublin": "lubelskie/lublin/lublin/lublin",
    "bialystok": "podlaskie/bialystok/bialystok/bialystok",
    "katowice": "slaskie/katowice/katowice/katowice",
    "czestochowa": "slaskie/czestochowa/czestochowa/czestochowa",
    "radom": "mazowieckie/radom/radom/radom",
    "sosnowiec": "slaskie/sosnowiec/sosnowiec/sosnowiec",
    "rzeszow": "podkarpackie/rzeszow/rzeszow/rzeszow",
    "kielce": "swietokrzyskie/kielce/kielce/kielce",
    "gliwice": "slaskie/gliwice/gliwice/gliwice",
    "zabrze": "slaskie/zabrze/zabrze/zabrze",
    "olsztyn": "warminsko-mazurskie/olsztyn/olsztyn/olsztyn",
    "bielsko-biala": "slaskie/bielsko-biala/bielsko-biala/bielsko-biala",
    "bytom": "slaskie/bytom/bytom/bytom",
    "zielona-gora": "lubuskie/zielona-gora/zielona-gora/zielona-gora",
    "gorzow-wielkopolski": "lubuskie/gorzow-wielkopolski/gorzow-wielkopolski/gorzow-wielkopolski",
    "rybnik": "slaskie/rybnik/rybnik/rybnik",
    "ruda-slaska": "slaskie/ruda-slaska/ruda-slaska/ruda-slaska",
    "opole": "opolskie/opole/opole/opole",
    "tychy": "slaskie/tychy/tychy/tychy",
    "dabrowa-gornicza": "slaskie/dabrowa-gornicza/dabrowa-gornicza/dabrowa-gornicza",
    "plock": "mazowieckie/plock/plock/plock",
    "elblag": "warminsko-mazurskie/elblag/elblag/elblag",
    "walbrzych": "dolnoslaskie/walbrzych/walbrzych/walbrzych",
    "wloclawek": "kujawsko-pomorskie/wloclawek/wloclawek/wloclawek",
    "tarnow": "malopolskie/tarnow/tarnow/tarnow",
    "chorzow": "slaskie/chorzow/chorzow/chorzow",
    "koszalin": "zachodniopomorskie/koszalin/koszalin/koszalin",
    "kalisz": "wielkopolskie/kalisz/kalisz/kalisz",
    "legnica": "dolnoslaskie/legnica/legnica/legnica",
    "grudziadz": "kujawsko-pomorskie/grudziadz/grudziadz/grudziadz",
    "jaworzno": "slaskie/jaworzno/jaworzno/jaworzno",
    "slupsk": "pomorskie/slupsk/slupsk/slupsk",
    "jastrzebie-zdroj": "slaskie/jastrzebie-zdroj/jastrzebie-zdroj/jastrzebie-zdroj",
    "nowy-sacz": "malopolskie/nowy-sacz/nowy-sacz/nowy-sacz",
    "jelenia-gora": "dolnoslaskie/jelenia-gora/jelenia-gora/jelenia-gora",
    "siedlce": "mazowieckie/siedlce/siedlce/siedlce",
    "myslowice": "slaskie/myslowice/myslowice/myslowice",
    "konin": "wielkopolskie/konin/konin/konin",
    "pila": "wielkopolskie/pilski/pila/pila",
    "piotrkow-trybunalski": "lodzkie/piotrkow-trybunalski/piotrkow-trybunalski/piotrkow-trybunalski",
    "inowroclaw": "kujawsko-pomorskie/inowroclawski/inowroclaw/inowroclaw",
    "lubin": "dolnoslaskie/lubinski/lubin/lubin",
    "ostrow-wielkopolski": "wielkopolskie/ostrowski/ostrow-wielkopolski/ostrow-wielkopolski",
    "suwalki": "podlaskie/suwalki/suwalki/suwalki",
    "stargard": "zachodniopomorskie/stargardzki/stargard/stargard",
    "gniezno": "wielkopolskie/gnieznienski/gniezno/gniezno",
    "ostroleka": "mazowieckie/ostroleka/ostroleka/ostroleka",
    "zamosc": "lubelskie/zamosc/zamosc/zamosc",
    "leszno": "wielkopolskie/leszno/leszno/leszno",
    "chelm": "lubelskie/chelm/chelm/chelm",
    "lomza": "podlaskie/lomza/lomza/lomza",
    "stalowa-wola": "podkarpackie/stalowowolski/stalowa-wola/stalowa-wola",
    "przemysl": "podkarpackie/przemysl/przemysl/przemysl",
    "kedzierzyn-kozle": "opolskie/kedzierzynsko-kozielski/kedzierzyn-kozle/kedzierzyn-kozle",
    "mielec": "podkarpackie/mielecki/mielec",
    "krosno": "podkarpackie/krosno/krosno/krosno",
    "tarnobrzeg": "podkarpackie/tarnobrzeg/tarnobrzeg/tarnobrzeg",
    "debica": "podkarpackie/debicki/debica/debica",
    "jaroslaw": "podkarpackie/jaroslawski/jaroslaw/jaroslaw",
    "sanok": "podkarpackie/sanocki/sanok/sanok",
    "jaslo": "podkarpackie/jasielski/jaslo/jaslo",
    "lancut": "podkarpackie/lancucki/lancut",
}

# Centroids for Polish cities to position Leaflet map
CITY_CENTROIDS = {
    "warszawa": (52.2297, 21.0122),
    "krakow": (50.0647, 19.9450),
    "lodz": (51.7592, 19.4560),
    "wroclaw": (51.1079, 17.0385),
    "poznan": (52.4064, 16.9252),
    "gdansk": (54.3520, 18.6466),
    "gdynia": (54.5189, 18.5305),
    "sopot": (54.4418, 18.5600),
    "szczecin": (53.4285, 14.5528),
    "bydgoszcz": (53.1235, 18.0084),
    "torun": (53.0138, 18.5984),
    "lublin": (51.2465, 22.5684),
    "bialystok": (53.1325, 23.1688),
    "katowice": (50.2649, 19.0238),
    "czestochowa": (50.8118, 19.1203),
    "radom": (51.4027, 21.1471),
    "sosnowiec": (50.2863, 19.1041),
    "rzeszow": (50.0375, 22.0047),
    "kielce": (50.8661, 20.6286),
    "gliwice": (50.2945, 18.6714),
    "zabrze": (50.3249, 18.7857),
    "olsztyn": (53.7784, 20.4801),
    "bielsko-biala": (49.8224, 19.0444),
    "bytom": (50.3480, 18.9328),
    "zielona-gora": (51.9356, 15.5062),
    "gorzow-wielkopolski": (52.7368, 15.2288),
    "rybnik": (50.1022, 18.5463),
    "ruda-slaska": (50.2574, 18.8546),
    "opole": (50.6751, 17.9213),
    "tychy": (50.1233, 18.9868),
    "dabrowa-gornicza": (50.3279, 19.1866),
    "plock": (52.5463, 19.7065),
    "elblag": (54.1522, 19.4088),
    "walbrzych": (50.7813, 16.2845),
    "wloclawek": (52.6483, 19.0678),
    "tarnow": (50.0121, 20.9858),
    "chorzow": (50.2975, 18.9546),
    "koszalin": (54.1944, 16.1722),
    "kalisz": (51.7673, 18.0853),
    "legnica": (51.2070, 16.1554),
    "grudziadz": (53.4841, 18.7536),
    "jaworzno": (50.2052, 19.2748),
    "slupsk": (54.4641, 17.0287),
    "jastrzebie-zdroj": (49.9532, 18.5772),
    "nowy-sacz": (49.6218, 20.6971),
    "jelenia-gora": (50.9044, 15.7194),
    "siedlce": (52.1677, 22.2901),
    "myslowice": (50.2415, 19.1387),
    "konin": (52.2230, 18.2512),
    "pila": (53.1514, 16.7381),
    "piotrkow-trybunalski": (51.4052, 19.7032),
    "inowroclaw": (52.7989, 18.2639),
    "lubin": (51.3980, 16.2010),
    "ostrow-wielkopolski": (51.6550, 17.8066),
    "suwalki": (54.1118, 22.9309),
    "stargard": (53.3367, 15.0450),
    "gniezno": (52.5348, 17.5826),
    "ostroleka": (53.0847, 21.5750),
    "zamosc": (50.7231, 23.2520),
    "leszno": (51.8403, 16.5749),
    "chelm": (51.1444, 23.4717),
    "lomza": (53.1781, 22.0594),
    "stalowa-wola": (50.5828, 22.0534),
    "przemysl": (49.7839, 22.7678),
    "kedzierzyn-kozle": (50.3475, 18.2144),
    "mielec": (50.2872, 21.4239),
    "krosno": (49.6887, 21.7706),
    "tarnobrzeg": (50.5739, 21.6795),
    "debica": (50.0515, 21.4114),
    "jaroslaw": (50.0163, 22.6806),
    "sanok": (49.5609, 22.2064),
    "jaslo": (49.7451, 21.4725),
    "lancut": (50.0690, 22.2310),
}


def slugify_city(text: str) -> str:
    """Converts a Polish city name into a clean URL slug (e.g. 'Głogów Małopolski' -> 'glogow-malopolski')."""
    if not text:
        return ""
    result = []
    for ch in text.strip():
        result.append(POLISH_CHAR_MAP.get(ch, ch))
    slug = "".join(result).lower()
    return re.sub(r"[^a-z0-9]+", "-", slug).strip("-")


class ScraperConfig(BaseModel):
    enabled: bool = True
    max_pages: int = 2
    delay_seconds: float = 1.0


class ScrapersSettings(BaseModel):
    otodom: ScraperConfig = Field(default_factory=lambda: ScraperConfig(enabled=True, max_pages=3, delay_seconds=1.0))
    olx: ScraperConfig = Field(default_factory=lambda: ScraperConfig(enabled=True, max_pages=2, delay_seconds=1.0))
    nieruchomosci_online: ScraperConfig = Field(
        default_factory=lambda: ScraperConfig(enabled=True, max_pages=2, delay_seconds=1.5)
    )
    morizon: ScraperConfig = Field(default_factory=lambda: ScraperConfig(enabled=True, max_pages=2, delay_seconds=1.0))


class SchedulerSettings(BaseModel):
    enabled: bool = True
    interval_minutes: int = 20
    night_mode: bool = True
    night_interval_minutes: int = 60
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"

    def get_current_interval_minutes(self) -> int:
        """Returns effective interval considering current time and quiet hours."""
        if not self.night_mode:
            return max(1, self.interval_minutes)
        try:
            now = datetime.now().time()
            start = datetime.strptime(self.quiet_hours_start, "%H:%M").time()
            end = datetime.strptime(self.quiet_hours_end, "%H:%M").time()
            if start <= end:
                is_night = start <= now <= end
            else:
                is_night = now >= start or now <= end
            return max(1, self.night_interval_minutes if is_night else self.interval_minutes)
        except Exception:
            return max(1, self.interval_minutes)


class CapexSettings(BaseModel):
    """Configurable assumptions for the CAPEX / TCO calculator (editable in Settings)."""

    developer_rate: float = 1800.0
    renovation_rate: float = 2200.0
    agency_fee_pct: float = 2.0
    pcc_exempt_first_home: bool = False


class SearchProfile(BaseModel):
    id: str = "default"
    name: str = "Domy Rzeszów"
    enabled: bool = True
    category: str = "dom"  # "dom", "mieszkanie", "dzialka"
    city: str = "Rzeszów"
    distance_radius: int | None = 15
    min_price: float | None = 0.0
    max_price: float | None = 1_300_000.0
    min_price_per_m2: float | None = None
    max_price_per_m2: float | None = None
    min_area_home: float | None = 90.0
    max_area_home: float | None = 145.0
    min_area_plot: float | None = 250.0
    max_area_plot: float | None = None
    min_rooms: int | None = None
    max_rooms: int | None = None
    min_floor: int | None = None
    max_floor: int | None = None
    min_year_built: int | None = None
    max_year_built: int | None = None
    owner_type: str = "all"  # "all", "private", "agency", "developer"
    market_type: str = "all"  # "all", "pierwotny", "wtórny"
    allowed_finish_conditions: list[str] = Field(default_factory=lambda: ["all"])
    allow_visualisations: bool = True
    reject_septic_tank: bool = False
    allowed_heating_types: list[str] = Field(default_factory=lambda: ["all"])
    building_types: list[str] = Field(default_factory=lambda: ["szeregowiec", "bliźniak", "wolnostojący", "inny"])
    whitelist_areas: list[dict] = Field(default_factory=list)
    blacklist_keywords: list[str] = Field(default_factory=list)
    enabled_portals: list[str] | None = None
    discord_webhook_url: str | None = None
    otodom_path: str | None = None
    # Safety filters based on public spatial registers (ISOK / SOPO / power lines / parcel geometry)
    reject_flood_risk: bool = False
    reject_landslide_risk: bool = False
    reject_high_voltage: bool = False
    min_parcel_front_m: float | None = None

    @property
    def city_slug(self) -> str:
        return slugify_city(self.city)

    def get_otodom_url(self) -> str:
        slug = self.city_slug
        path = self.otodom_path.strip("/") if self.otodom_path else OTODOM_CITY_PATHS.get(slug, slug)
        cat = {"dom": "dom", "mieszkanie": "mieszkanie", "dzialka": "dzialka"}.get(self.category, "dom")
        base = f"https://www.otodom.pl/pl/wyniki/sprzedaz/{cat}/{path}"

        radius = self.distance_radius or 15
        params = [f"distanceRadius={radius}", "limit=36"]
        if self.min_price is not None and self.min_price > 0:
            params.append(f"priceMin={int(self.min_price)}")
        if self.max_price is not None and self.max_price > 0:
            params.append(f"priceMax={int(self.max_price)}")

        if self.category == "dzialka":
            if self.min_area_plot is not None and self.min_area_plot > 0:
                params.append(f"areaMin={int(self.min_area_plot)}")
            if self.max_area_plot is not None and self.max_area_plot > 0:
                params.append(f"areaMax={int(self.max_area_plot)}")
        else:
            if self.min_area_home is not None and self.min_area_home > 0:
                params.append(f"areaMin={int(self.min_area_home)}")
            if self.max_area_home is not None and self.max_area_home > 0:
                params.append(f"areaMax={int(self.max_area_home)}")
            if self.category == "dom":
                if self.min_area_plot is not None and self.min_area_plot > 0:
                    params.append(f"terrainAreaMin={int(self.min_area_plot)}")
                if self.max_area_plot is not None and self.max_area_plot > 0:
                    params.append(f"terrainAreaMax={int(self.max_area_plot)}")

        if self.market_type == "pierwotny":
            params.append("market=PRIMARY")
        elif self.market_type == "wtórny":
            params.append("market=SECONDARY")
        if self.owner_type == "private":
            params.append("ownerTypeSingleSelect=PRIVATE")
        elif self.owner_type in ("agency", "developer"):
            params.append(f"ownerTypeSingleSelect={self.owner_type.upper()}")

        if self.min_rooms or self.max_rooms:
            room_names = {1: "ONE", 2: "TWO", 3: "THREE", 4: "FOUR", 5: "FIVE", 6: "SIX_OR_MORE"}
            min_r = self.min_rooms or 1
            max_r = self.max_rooms or 6
            selected = [room_names[r] for r in range(min_r, min(max_r, 6) + 1) if r in room_names]
            if selected:
                params.append(f"roomsNumber=%5B{','.join(selected)}%5D")

        return f"{base}?{'&'.join(params)}"

    def get_olx_url(self) -> str:
        slug = self.city_slug
        cat = {"dom": "domy", "mieszkanie": "mieszkania", "dzialka": "dzialki"}.get(self.category, "domy")
        base = f"https://www.olx.pl/nieruchomosci/{cat}/sprzedaz/{slug}/"

        radius = self.distance_radius or 15
        params = [f"search%5Bdist%5D={radius}"]
        if self.min_price is not None and self.min_price > 0:
            params.append(f"search%5Bfilter_float_price%3Afrom%5D={int(self.min_price)}")
        if self.max_price is not None and self.max_price > 0:
            params.append(f"search%5Bfilter_float_price%3Ato%5D={int(self.max_price)}")

        if self.category == "dzialka":
            if self.min_area_plot is not None and self.min_area_plot > 0:
                params.append(f"search%5Bfilter_float_m%3Afrom%5D={int(self.min_area_plot)}")
            if self.max_area_plot is not None and self.max_area_plot > 0:
                params.append(f"search%5Bfilter_float_m%3Ato%5D={int(self.max_area_plot)}")
        else:
            if self.min_area_home is not None and self.min_area_home > 0:
                params.append(f"search%5Bfilter_float_m%3Afrom%5D={int(self.min_area_home)}")
            if self.max_area_home is not None and self.max_area_home > 0:
                params.append(f"search%5Bfilter_float_m%3Ato%5D={int(self.max_area_home)}")

        if self.market_type == "pierwotny":
            params.append("search%5Bfilter_enum_market%5D%5B0%5D=primary")
        elif self.market_type == "wtórny":
            params.append("search%5Bfilter_enum_market%5D%5B0%5D=secondary")
        if self.owner_type == "private":
            params.append("search%5Bprivate_business%5D=private")
        elif self.owner_type in ("agency", "developer"):
            params.append("search%5Bprivate_business%5D=business")

        if self.min_rooms or self.max_rooms:
            olx_rooms = {1: "one", 2: "two", 3: "three", 4: "four_more"}
            min_r = self.min_rooms or 1
            max_r = self.max_rooms or 4
            idx = 0
            for r in range(min_r, min(max_r, 4) + 1):
                if r in olx_rooms:
                    params.append(f"search%5Bfilter_enum_rooms%5D%5B{idx}%5D={olx_rooms[r]}")
                    idx += 1

        return f"{base}?{'&'.join(params)}"

    def get_nieruchomosci_online_url(self) -> str:
        slug = self.city_slug
        cat = {"dom": "domy", "mieszkanie": "mieszkania", "dzialka": "dzialki"}.get(self.category, "domy")
        base = f"https://{slug}.nieruchomosci-online.pl/{cat},sprzedaz/"

        params = []
        if self.min_price is not None and self.min_price > 0:
            params.append(f"cena_od={int(self.min_price)}")
        if self.max_price is not None and self.max_price > 0:
            params.append(f"cena_do={int(self.max_price)}")

        if self.category == "dzialka":
            if self.min_area_plot is not None and self.min_area_plot > 0:
                params.append(f"powierzchnia_od={int(self.min_area_plot)}")
            if self.max_area_plot is not None and self.max_area_plot > 0:
                params.append(f"powierzchnia_do={int(self.max_area_plot)}")
        else:
            if self.min_area_home is not None and self.min_area_home > 0:
                params.append(f"powierzchnia_od={int(self.min_area_home)}")
            if self.max_area_home is not None and self.max_area_home > 0:
                params.append(f"powierzchnia_do={int(self.max_area_home)}")

        if self.min_rooms is not None and self.min_rooms > 0:
            params.append(f"liczba-pokoi_od={int(self.min_rooms)}")
        if self.max_rooms is not None and self.max_rooms > 0:
            params.append(f"liczba-pokoi_do={int(self.max_rooms)}")

        if params:
            return f"{base}?{'&'.join(params)}"
        return base

    def get_morizon_url(self) -> str:
        slug = self.city_slug
        cat = {"dom": "domy", "mieszkanie": "mieszkania", "dzialka": "dzialki"}.get(self.category, "domy")
        base = f"https://www.morizon.pl/{cat}/{slug}/"

        params = []
        if self.min_price is not None and self.min_price > 0:
            params.append(f"ps%5Bprice_from%5D={int(self.min_price)}")
        if self.max_price is not None and self.max_price > 0:
            params.append(f"ps%5Bprice_to%5D={int(self.max_price)}")

        if self.category == "dzialka":
            if self.min_area_plot is not None and self.min_area_plot > 0:
                params.append(f"ps%5Bliving_area_from%5D={int(self.min_area_plot)}")
            if self.max_area_plot is not None and self.max_area_plot > 0:
                params.append(f"ps%5Bliving_area_to%5D={int(self.max_area_plot)}")
        else:
            if self.min_area_home is not None and self.min_area_home > 0:
                params.append(f"ps%5Bliving_area_from%5D={int(self.min_area_home)}")
            if self.max_area_home is not None and self.max_area_home > 0:
                params.append(f"ps%5Bliving_area_to%5D={int(self.max_area_home)}")

        if params:
            return f"{base}?{'&'.join(params)}"
        return base

    def get_city_center(self) -> tuple[float, float]:
        slug = self.city_slug
        return CITY_CENTROIDS.get(slug, (52.0693, 19.4803))


class SearchConfig(BaseModel):
    """
    Root configuration managing search profiles and scrapers settings.
    Maintains full backward compatibility with legacy single-profile callers.
    """

    profiles: list[SearchProfile] = Field(default_factory=list)
    scrapers: ScrapersSettings = Field(default_factory=ScrapersSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    llm_analysis_enabled: bool = Field(default_factory=lambda: settings.USE_LLM_ANALYSIS)
    llm_provider: str = Field(default="auto")
    ollama_model: str = Field(default_factory=lambda: settings.OLLAMA_MODEL)
    ollama_base_url: str = Field(default_factory=lambda: settings.OLLAMA_BASE_URL)
    ollama_timeout_seconds: float = 180.0
    ollama_temperature: float = 0.0
    ollama_num_ctx: int = 8192
    openrouter_model: str = Field(default_factory=lambda: settings.OPENROUTER_MODEL)
    capex: CapexSettings = Field(default_factory=CapexSettings)

    def __getattr__(self, item: str) -> Any:
        # Transparent proxy to active/first profile for backward compatibility
        if (
            item
            not in (
                "profiles",
                "scrapers",
                "scheduler",
                "llm_analysis_enabled",
                "llm_provider",
                "ollama_model",
                "ollama_base_url",
                "ollama_timeout_seconds",
                "openrouter_model",
                "capex",
            )
            and hasattr(self, "profiles")
            and self.profiles
        ):
            active = self.get_active_profile()
            if hasattr(active, item):
                return getattr(active, item)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{item}'")

    def get_active_profile(self) -> SearchProfile:
        for p in self.profiles:
            if p.enabled:
                return p
        return self.profiles[0] if self.profiles else SearchProfile()

    def get_active_profiles(self, target_name: str | None = None) -> list[SearchProfile]:
        if target_name:
            target_clean = target_name.strip().lower()
            matched = [p for p in self.profiles if p.id.lower() == target_clean or p.name.lower() == target_clean]
            if matched:
                return matched
        return [p for p in self.profiles if p.enabled]


class ConfigManager:
    """Manages active search criteria, location, and filtration rules."""

    def __init__(self, config_path: Path | str = CONFIG_FILE_PATH):
        self.config_path = Path(config_path) if isinstance(config_path, str) else config_path
        self._config: SearchConfig | None = None
        self.load_config()

    def _get_default_profile(self) -> SearchProfile:
        return SearchProfile(
            id="rzeszow_domy",
            name="Domy Rzeszów",
            enabled=True,
            category="dom",
            city="Rzeszów",
            distance_radius=15,
            min_price=0.0,
            max_price=settings.MAX_PRICE,
            min_area_home=settings.MIN_AREA_HOME,
            max_area_home=settings.MAX_AREA_HOME,
            min_area_plot=settings.MIN_AREA_PLOT,
            market_type="all",
            allowed_finish_conditions=["all"],
            allow_visualisations=True,
            building_types=["szeregowiec", "bliźniak", "wolnostojący", "inny"],
            whitelist_areas=settings.WHITELIST_AREAS,
            blacklist_keywords=[],
        )

    def _get_default_config(self) -> SearchConfig:
        return SearchConfig(
            profiles=[self._get_default_profile()],
            scrapers=ScrapersSettings(),
            scheduler=SchedulerSettings(),
        )

    @classmethod
    def _migrate_legacy_dict(cls, data: dict[str, Any]) -> SearchConfig:
        """Migrate legacy flat dictionary into modern profiles structure."""
        if "profiles" in data and isinstance(data["profiles"], list) and len(data["profiles"]) > 0:
            return SearchConfig(**data)

        logger.info("[ConfigManager] Wykryto starszy format konfiguracji, migruję do formatu z profilami...")
        # Extract profile-specific fields from flat structure
        profile_fields = {k: v for k, v in data.items() if k in SearchProfile.model_fields}
        if "name" not in profile_fields:
            city_name = data.get("city", "Rzeszów")
            profile_fields["name"] = f"Domy {city_name}"
        if "id" not in profile_fields:
            profile_fields["id"] = "default"
        if "category" not in profile_fields:
            profile_fields["category"] = "dom"
        if "enabled" not in profile_fields:
            profile_fields["enabled"] = True

        profile = SearchProfile(**profile_fields)
        scrapers = ScrapersSettings()
        if "scrapers" in data and isinstance(data["scrapers"], dict):
            try:
                scrapers = ScrapersSettings(**data["scrapers"])
            except Exception:
                pass

        return SearchConfig(profiles=[profile], scrapers=scrapers)

    def load_config(self) -> SearchConfig:
        if self.config_path.exists():
            try:
                with self.config_path.open(encoding="utf-8") as f:
                    data = json.load(f)
                    self._config = self._migrate_legacy_dict(data)
                    logger.info(f"[ConfigManager] Załadowano konfigurację ({len(self._config.profiles)} profili).")
                    # Save back migrated format
                    self.save_config()
                    return self._config
            except Exception as e:
                logger.warning(f"[ConfigManager] Błąd odczytu {self.config_path}, przywracam domyślne: {e}")

        # Check fallback root configs (e.g. inside Docker image when volume config_path doesn't exist yet)
        for fallback_name in ("search_config.json", "search_config.example.json"):
            fallback = Path(fallback_name)
            if not fallback.exists() or fallback.resolve() == self.config_path.resolve():
                continue
            try:
                with fallback.open(encoding="utf-8") as f:
                    data = json.load(f)
                self._config = self._migrate_legacy_dict(data)
                logger.info(
                    f"[ConfigManager] Zainicjalizowano konfigurację z szablonu {fallback} do {self.config_path}"
                )
                self.save_config()
                return self._config
            except Exception as e:
                logger.warning(f"[ConfigManager] Błąd odczytu fallback {fallback}: {e}")

        self._config = self._get_default_config()
        self.save_config()
        return self._config

    def save_config(self) -> None:
        if self._config is None:
            self._config = self._get_default_config()
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with self.config_path.open("w", encoding="utf-8", newline="\n") as f:
                json.dump(self._config.model_dump(), f, ensure_ascii=False, indent=2)
                f.write("\n")
            logger.info(f"[ConfigManager] Zapisano konfigurację do {self.config_path}")
        except Exception as e:
            logger.error(f"[ConfigManager] Nie udało się zapisać konfiguracji: {e}")

    def get_config(self) -> SearchConfig:
        if self._config is None:
            self.load_config()
        assert self._config is not None
        return self._config

    def update_config(self, updates: dict[str, Any]) -> SearchConfig:
        """Update top-level configuration (e.g. scrapers, scheduler or full dictionary)."""
        current_dict = self.get_config().model_dump()
        if "profiles" in updates:
            current_dict["profiles"] = updates["profiles"]
        if "scrapers" in updates:
            current_dict["scrapers"] = updates["scrapers"]
        if "scheduler" in updates:
            current_dict["scheduler"] = updates["scheduler"]
        if "capex" in updates and isinstance(updates["capex"], dict):
            merged = dict(current_dict.get("capex") or {})
            merged.update(updates["capex"])
            current_dict["capex"] = merged
        if "llm_analysis_enabled" in updates:
            current_dict["llm_analysis_enabled"] = bool(updates["llm_analysis_enabled"])
        if "llm_provider" in updates and updates["llm_provider"]:
            current_dict["llm_provider"] = str(updates["llm_provider"]).strip().lower()
        if "ollama_model" in updates and updates["ollama_model"]:
            current_dict["ollama_model"] = str(updates["ollama_model"]).strip()
        if "ollama_base_url" in updates and updates["ollama_base_url"]:
            current_dict["ollama_base_url"] = str(updates["ollama_base_url"]).strip().rstrip("/")
        if "ollama_timeout_seconds" in updates and updates["ollama_timeout_seconds"]:
            try:
                current_dict["ollama_timeout_seconds"] = max(10.0, float(updates["ollama_timeout_seconds"]))
            except (TypeError, ValueError):
                pass
        if "openrouter_model" in updates and updates["openrouter_model"]:
            current_dict["openrouter_model"] = str(updates["openrouter_model"]).strip()
        if "ollama_temperature" in updates and updates["ollama_temperature"] is not None:
            try:
                current_dict["ollama_temperature"] = max(0.0, min(1.0, float(updates["ollama_temperature"])))
            except (TypeError, ValueError):
                pass
        if "ollama_num_ctx" in updates and updates["ollama_num_ctx"] is not None:
            try:
                current_dict["ollama_num_ctx"] = max(1024, int(updates["ollama_num_ctx"]))
            except (TypeError, ValueError):
                pass

        # Support updating first/active profile directly if flat keys were provided
        flat_keys = {
            k: v
            for k, v in updates.items()
            if k
            not in (
                "profiles",
                "scrapers",
                "scheduler",
                "capex",
                "llm_analysis_enabled",
                "llm_provider",
                "ollama_model",
                "ollama_base_url",
                "ollama_timeout_seconds",
                "ollama_temperature",
                "ollama_num_ctx",
                "openrouter_model",
            )
        }
        if flat_keys and current_dict.get("profiles"):
            current_dict["profiles"][0].update(flat_keys)

        self._config = SearchConfig(**current_dict)
        self.save_config()
        return self._config

    def update_scheduler(self, scheduler_data: dict[str, Any]) -> SchedulerSettings:
        cfg = self.get_config()
        current_dict = cfg.scheduler.model_dump()
        current_dict.update(scheduler_data)
        cfg.scheduler = SchedulerSettings(**current_dict)
        self.save_config()
        return cfg.scheduler

    def get_profile(self, profile_id_or_name: str | None = None) -> SearchProfile:
        cfg = self.get_config()
        if profile_id_or_name:
            target = profile_id_or_name.strip().lower()
            for p in cfg.profiles:
                if p.id.lower() == target or p.name.lower() == target:
                    return p
        return cfg.get_active_profile()

    def add_or_update_profile(self, profile_data: dict[str, Any]) -> SearchProfile:
        cfg = self.get_config()
        prof_id = profile_data.get("id") or profile_data.get("name", "profile").lower().replace(" ", "_")
        profile_data["id"] = prof_id

        existing_idx = None
        for i, p in enumerate(cfg.profiles):
            if p.id == prof_id:
                existing_idx = i
                break

        new_profile = SearchProfile(**profile_data)
        if existing_idx is not None:
            cfg.profiles[existing_idx] = new_profile
        else:
            cfg.profiles.append(new_profile)

        self.save_config()
        return new_profile

    def delete_profile(self, profile_id: str) -> bool:
        cfg = self.get_config()
        initial_len = len(cfg.profiles)
        cfg.profiles = [p for p in cfg.profiles if p.id != profile_id]
        if len(cfg.profiles) < initial_len:
            if not cfg.profiles:
                cfg.profiles.append(self._get_default_profile())
            self.save_config()
            return True
        return False


# Singleton config manager
config_manager = ConfigManager()
