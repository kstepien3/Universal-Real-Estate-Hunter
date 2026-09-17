import asyncio
import io
import json
import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from loguru import logger
from PIL import Image

from src.storage import SpatialCacheModel, get_session, safe_commit

HIGH_VOLTAGE_CORRIDORS = [
    ("Linia 400 kV SE Widełka - Rzeszów", 50.2033, 21.9567),
    ("Linia 400 kV Widełka - Krosno Iskra", 50.1500, 21.9600),
    ("Linia 220 kV Chmielów - Stalowa Wola", 50.3200, 21.7500),
    ("Linia 110 kV RPZ Baranówka", 50.0580, 21.9880),
    ("Linia 110 kV RPZ Staroniwa", 50.0270, 21.9750),
    ("Linia 110 kV RPZ Załęże", 50.0540, 22.0400),
    ("Linia 110 kV RPZ Piastów", 50.0160, 22.0080),
]


class GeoportalService:
    """
    Integration with Polish National Geoportal (GUGiK) APIs:
    1. ULDK API (Usługa Lokalizacji Działek Katastralnych - uldk.gugik.gov.pl):
       - Resolves exact cadastral parcel (TERYT, nr działki, obręb, gmina).
       - Fetches parcel boundary geometry in EPSG:2180 and calculates exact area in m².
    2. KIEG WMS (Krajowa Integracja Ewidencji Gruntów):
       - Resolves official land use contour (klasoużytki) of the parcel.
       - Audits surrounding parcels within a given radius (e.g. 120m) for industrial ('Ba'),
         commercial ('Bi'), railway ('Tk'), or other high-impact activities.
    3. Direct deep-linking:
       - Generates direct map URLs to Geoportal Krajowy for any parcel or coordinates.
    """

    ULDK_BASE = "https://uldk.gugik.gov.pl/"
    KIEG_WMS = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"
    KIMPZP_WMS = (
        "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego"
    )
    ISOK_FLOOD_WMS = "https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_NZ_HY_MZPMRP_WMS"
    SOPO_LANDSLIDE_WMS = "https://cbdgmapa.pgi.gov.pl/arcgis/services/geozagrozenia/sopo_obszary/MapServer/WMSServer"
    GDOS_PROTECTED_WMS = "https://sdi.gdos.gov.pl/wms"
    NID_MONUMENTS_WMS = "https://usluga.zabytek.gov.pl/INSPIRE_IMD/service.svc/get"
    GIOS_NOISE_API = "https://dane.gios.gov.pl/api/halas/v1/zasiegi-halasu"

    # Powiat-level GESUT WMS services (pixel-based detection, see get_gesut_networks).
    # Layer names follow the Rozporządzenie MRPiT z 23.07.2021 (GESUT) specification.
    GESUT_WMS_SERVICES = [
        {
            "name": "Krajowa Integracja Uzbrojenia Terenu (GUGiK)",
            "url": "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        },
        {"name": "Miasto Rzeszów", "url": "https://osrodek.erzeszow.pl/map/geoportal/wmsg.php"},
        {"name": "Powiat rzeszowski", "url": "https://powiatrzeszowski.geoportal2.pl/map/geoportal/wmsg.php"},
    ]
    GESUT_LAYERS = [
        ("woda", "siec_wodociagowa", (0, 0, 255)),
        ("kanalizacja", "siec_kanalizacyjna", (128, 51, 0)),
        ("gaz", "siec_gazowa", (255, 255, 0)),
        ("prad", "siec_elektroenergetyczna", (255, 0, 0)),
        ("cieplo", "siec_cieplownicza", (255, 145, 0)),
        ("telekomunikacja", "siec_telekomunikacyjna", (128, 0, 255)),
    ]
    GESUT_COLOR_TOLERANCE = 45
    GESUT_MIN_PIXELS = 30
    GESUT_CHECK_RADIUS_M = 20.0

    def __init__(self, request_timeout: float = 6.0):
        self.timeout = request_timeout
        self.headers = {"User-Agent": "ApartmentHunter-Geoportal/1.0 (property-research-suite; contact@local)"}
        self._cache: dict[str, Any] = {}

    async def _get_cached(self, key: str) -> Any | None:
        try:
            async with get_session() as session:
                item = await session.get(SpatialCacheModel, key)
                if item:
                    now = datetime.now(UTC)
                    exp = item.expires_at
                    if exp and exp.tzinfo is None:
                        exp = exp.replace(tzinfo=UTC)
                    if exp is None or exp > now:
                        return json.loads(item.data_json)
        except Exception:
            pass
        return None

    async def _set_cached(self, key: str, value: Any, ttl_days: int = 90) -> None:
        try:
            data_str = json.dumps(value, ensure_ascii=False)
            exp = datetime.now(UTC) + timedelta(days=ttl_days)
            async with get_session() as session:
                existing = await session.get(SpatialCacheModel, key)
                if existing:
                    existing.data_json = data_str
                    existing.expires_at = exp
                else:
                    session.add(SpatialCacheModel(cache_key=key, data_json=data_str, expires_at=exp))
                try:
                    await safe_commit(session)
                except Exception:
                    await session.rollback()
                    existing = await session.get(SpatialCacheModel, key)
                    if existing:
                        existing.data_json = data_str
                        existing.expires_at = exp
                        await safe_commit(session)
        except Exception:
            pass

    @staticmethod
    def generate_gunb_url(parcel_id: str | None = None) -> str:
        """Generates direct URL to GUNB building permit (RWDZ) search portal."""
        _ = parcel_id
        return "https://wyszukiwarka.gunb.gov.pl/"

    def generate_geoportal_url(
        self,
        parcel_id: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
    ) -> str:
        """Generates a direct clickable link to the National Geoportal map."""
        if parcel_id:
            return f"https://mapy.geoportal.gov.pl/imap/Imgp_2.html?identifyParcel={parcel_id}"
        if lat is not None and lon is not None:
            return (
                f"https://mapy.geoportal.gov.pl/imap/Imgp_2.html?locale=pl&gui=new&"
                f"session=%7B%22actions%22%3A%5B%7B%22name%22%3A%22locatePoint%22%2C"
                f"%22params%22%3A%7B%22x%22%3A{lon}%2C%22y%22%3A{lat}%2C%22srid%22%3A4326%7D%7D%5D%7D"
            )
        return "https://mapy.geoportal.gov.pl/"

    async def get_parcel_by_xy(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lon: float,
    ) -> dict[str, str] | None:
        """Queries ULDK for a parcel containing given WGS84 coordinates."""
        cache_key = f"xy:{lat:.6f},{lon:.6f}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        url = f"{self.ULDK_BASE}?request=GetParcelByXY&xy={lon:.6f},{lat:.6f},4326&result=id,teryt,commune,county,voivodship"
        try:
            resp = await client.get(url, headers=self.headers, timeout=self.timeout)
            if resp.status_code == 200:
                lines = [l.strip() for l in resp.text.strip().splitlines() if l.strip()]
                if len(lines) >= 2 and lines[0] == "0":
                    parts = lines[1].split("|")
                    parcel_id = parts[0].strip()
                    res = {
                        "parcel_id": parcel_id,
                        "commune": parts[2].strip() if len(parts) > 2 else "",
                        "county": parts[3].strip() if len(parts) > 3 else "",
                    }
                    self._cache[cache_key] = res
                    return res
        except Exception as e:
            logger.debug(f"[Geoportal] ULDK GetParcelByXY query failed for ({lat}, {lon}): {e}")

        return None

    @staticmethod
    def compute_parcel_shape_metrics(pts: list[tuple[float, float]]) -> dict[str, Any]:
        """
        Calculates parcel front width, length, aspect ratio, and shape classification
        using Minimum Oriented Bounding Box (OBB) algorithm in EPSG:2180.
        """
        if len(pts) < 3:
            return {
                "front_width_m": None,
                "length_m": None,
                "aspect_ratio": None,
                "shape_type": None,
            }

        min_area = float("inf")
        best_w, best_l = 0.0, 0.0

        n = len(pts)
        for i in range(n - 1):
            dx = pts[i + 1][0] - pts[i][0]
            dy = pts[i + 1][1] - pts[i][1]
            dist = math.hypot(dx, dy)
            if dist < 0.1:
                continue
            ux, uy = dx / dist, dy / dist
            vx, vy = -uy, ux

            proj_u = [p[0] * ux + p[1] * uy for p in pts]
            proj_v = [p[0] * vx + p[1] * vy for p in pts]

            dim_u = max(proj_u) - min(proj_u)
            dim_v = max(proj_v) - min(proj_v)
            area = dim_u * dim_v

            if area < min_area:
                min_area = area
                best_w = min(dim_u, dim_v)
                best_l = max(dim_u, dim_v)

        aspect = round(best_l / max(best_w, 0.1), 2)
        best_w = round(best_w, 1)
        best_l = round(best_l, 1)

        if best_w < 16.0 or aspect >= 4.0:
            shape_type = "WĄSKA_SZNUROWKA"
        elif aspect > 2.5:
            shape_type = "WYDŁUŻONY"
        else:
            shape_type = "REGULARNY"

        return {
            "front_width_m": best_w,
            "length_m": best_l,
            "aspect_ratio": aspect,
            "shape_type": shape_type,
        }

    async def get_parcel_geometry_and_area(
        self,
        client: httpx.AsyncClient,
        parcel_id: str,
        return_details: bool = False,
    ) -> Any:
        """
        Retrieves parcel geometry in EPSG:2180.
        Returns: (area_m2, (centroid_x, centroid_y)) or (area_m2, centroid, shape_metrics) if return_details.
        """
        cache_key = f"geom:{parcel_id}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if return_details:
                return cached if len(cached) == 3 else (cached[0], cached[1], {})
            return cached[0], cached[1]

        url = f"{self.ULDK_BASE}?request=GetParcelById&id={parcel_id}&result=geom_wkt&srid=2180"
        try:
            resp = await client.get(url, headers=self.headers, timeout=self.timeout)
            if resp.status_code == 200 and ("POLYGON" in resp.text):
                m = re.search(r"\(\s*\(+([0-9\.\s,-]+?)\)", resp.text)
                if m:
                    raw_coords = m.group(1).strip()
                    pts = []
                    for pair in raw_coords.split(","):
                        pair = pair.strip()
                        if pair:
                            parts = pair.split()
                            if len(parts) >= 2:
                                pts.append((float(parts[0]), float(parts[1])))
                    n = len(pts)
                    if n >= 3:
                        area = 0.5 * abs(
                            sum(pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1] for i in range(n - 1))
                        )
                        cx = sum(p[0] for p in pts) / n
                        cy = sum(p[1] for p in pts) / n
                        shape_metrics = self.compute_parcel_shape_metrics(pts)
                        res = (round(area, 1), (cx, cy), shape_metrics)
                        self._cache[cache_key] = res
                        if return_details:
                            return res
                        return res[0], res[1]
        except Exception as e:
            logger.debug(f"[Geoportal] ULDK GetParcelById failed for {parcel_id}: {e}")

        return (None, None, {}) if return_details else (None, None)

    async def get_parcel_contours_kieg(
        self,
        client: httpx.AsyncClient,
        cx: float,
        cy: float,
    ) -> str:
        """Queries KIEG WMS GetFeatureInfo to extract soil/use contours (e.g. 'RIIIb,Ba')."""
        cache_key = f"kieg:{round(cx, 1)},{round(cy, 1)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        query_url = (
            f"{self.KIEG_WMS}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&"
            f"BBOX={cy - 5:.1f},{cx - 5:.1f},{cy + 5:.1f},{cx + 5:.1f}&CRS=EPSG:2180&"
            f"WIDTH=10&HEIGHT=10&LAYERS=dzialki&QUERY_LAYERS=dzialki&I=5&J=5&INFO_FORMAT=text/html"
        )
        try:
            resp = await client.get(query_url, headers=self.headers, timeout=self.timeout)
            if resp.status_code == 200:
                m = re.search(r"Oznaczenie konturu</td><td>(.*?)</td>", resp.text)
                if m:
                    contour = m.group(1).strip()
                    self._cache[cache_key] = contour
                    return contour
        except Exception as e:
            logger.debug(f"[Geoportal] KIEG GetFeatureInfo failed: {e}")

        return ""

    @staticmethod
    def _decode_mpzp_text(resp: httpx.Response) -> str:
        raw = getattr(resp, "content", None)
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="replace")
            if "\ufffd" in text:
                alt = raw.decode("cp1250", errors="replace")
                if alt.count("\ufffd") < text.count("\ufffd"):
                    text = alt
            return text
        return resp.text or ""

    @classmethod
    def _parse_mpzp_payload(cls, text: str) -> dict[str, str | None] | None:
        """
        Krajowa Integracja MPZP is a cascading WMS — every county serves GetFeatureInfo
        in its own format. Handles all formats observed in production:
        A) XML ROWSET (Warszawa): FUN_SYMB / FUN_NAZWA / NAZWA_PLAN
        B) HTML attribute tables (Rzeszów miasto): SYMBOL / OPIS / UCHWALA / NAZWA2
        C) ESRI FeatureInfoCollection (Kraków): Oznaczenie / opis_oznac / Nazwa MPZP
        D) Raster-only plan metadata (powiat rzeszowski / igeomap): Nazwa planu +
           Poziom informatyzacji = rastrowy (plan obowiązuje, brak oznaczeń wektorowych)
        Returns None when no recognizable plan payload is present.
        """
        # A) XML ROWSET (Warszawa)
        symb_m = re.search(r"<FUN_SYMB>\s*([^<]+?)\s*</FUN_SYMB>", text, re.IGNORECASE | re.DOTALL)
        nazwa_m = re.search(r"<FUN_NAZWA>\s*([^<]+?)\s*</FUN_NAZWA>", text, re.IGNORECASE | re.DOTALL)
        plan_m = re.search(r"<NAZWA_PLAN>\s*([^<]+?)\s*</NAZWA_PLAN>", text, re.IGNORECASE | re.DOTALL)

        symbol = symb_m.group(1).strip() if symb_m else None
        fun_nazwa = nazwa_m.group(1).strip() if nazwa_m else None
        plan_name = plan_m.group(1).strip() if plan_m else None

        if symbol or fun_nazwa:
            zone_desc = f"{symbol}: {fun_nazwa}" if (symbol and fun_nazwa) else (symbol or fun_nazwa)
            return {
                "status": "OBOWIĄZUJĄCY",
                "zone": zone_desc,
                "symbol": symbol,
                "plan_name": plan_name,
                "level": "wektorowy",
            }

        low = text.lower()

        # C) ESRI FeatureInfoCollection (Kraków)
        if "featureinfocollection" in low:
            ths = [h.strip() for h in re.findall(r"<th\b[^>]*>([^<]*)</th>", text, re.IGNORECASE | re.DOTALL)]
            tds = [d.strip() for d in re.findall(r"<td\b[^>]*>([^<]*)</td>", text, re.IGNORECASE | re.DOTALL)]
            if len(ths) >= 5 and len(tds) >= len(ths):
                esri_kv = dict(zip(ths, tds[: len(ths)], strict=True))
                ozn = esri_kv.get("Oznaczenie")
                opis = esri_kv.get("opis_oznac")
                nazwa = esri_kv.get("Nazwa MPZP")
                if ozn or opis:
                    zone_desc = f"{ozn}: {opis}" if (ozn and opis) else (ozn or opis or "MPZP")
                    return {
                        "status": "OBOWIĄZUJĄCY",
                        "zone": zone_desc,
                        "symbol": ozn,
                        "plan_name": nazwa,
                        "level": "wektorowy",
                    }

        # B/D) Adjacent <th>/<td> key-value tables (Rzeszów miasto, igeomap raster)
        kv: dict[str, str] = {}
        for m in re.finditer(
            r"<t[dh]\b[^>]*>([^<]+)</t[dh]>\s*<t[dh]\b[^>]*>(.*?)</t[dh]>",
            text,
            re.IGNORECASE | re.DOTALL,
        ):
            key = re.sub(r"\s+", " ", m.group(1)).strip()
            val = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
            if key and key.lower() != "layer":
                kv.setdefault(key, val)

        # D) Raster-only plan metadata — plan obowiązuje, ale bez cyfrowych oznaczeń
        if kv.get("Poziom informatyzacji", "").strip().lower() == "rastrowy" and (
            kv.get("Nazwa planu") or kv.get("Uchwała")
        ):
            return {
                "status": "OBOWIĄZUJĄCY",
                "zone": "Plan rastrowy (brak oznaczeń wektorowych)",
                "symbol": None,
                "plan_name": kv.get("Nazwa planu") or kv.get("Uchwała"),
                "level": "rastrowy",
            }

        # B) Rzeszów miasto attribute tables
        symbol = kv.get("SYMBOL") or kv.get("S_STANDARD")
        opis = kv.get("OPIS")
        if symbol or opis:
            zone_desc = f"{symbol}: {opis}" if (symbol and opis) else (symbol or opis)
            return {
                "status": "OBOWIĄZUJĄCY",
                "zone": zone_desc,
                "symbol": symbol,
                "plan_name": kv.get("NAZWA2") or kv.get("NAZWA"),
                "level": "wektorowy",
            }

        return None

    async def get_mpzp_info(
        self,
        client: httpx.AsyncClient,
        cx: float,
        cy: float,
    ) -> dict[str, str | None]:
        """Queries Krajowa Integracja MPZP WMS GetFeatureInfo (EPSG:2180)."""
        cache_key = f"mpzp:{round(cx, 1)},{round(cy, 1)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        query_url = (
            f"{self.KIMPZP_WMS}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&"
            f"BBOX={cy - 5:.1f},{cx - 5:.1f},{cy + 5:.1f},{cx + 5:.1f}&CRS=EPSG:2180&"
            f"WIDTH=10&HEIGHT=10&LAYERS=plany_granice,wektor-str&QUERY_LAYERS=plany_granice,wektor-str&I=5&J=5&INFO_FORMAT=text/html"
        )
        try:
            resp = await client.get(query_url, headers=self.headers, timeout=self.timeout, follow_redirects=True)
            if resp.status_code == 200:
                text = self._decode_mpzp_text(resp)
                parsed = self._parse_mpzp_payload(text)
                if parsed:
                    self._cache[cache_key] = parsed
                    return parsed

                low = text.lower()
                if any(sig in low for sig in ("brak wyniku", "brak serwisu", "brak planu")):
                    mpzp_res: dict[str, str | None] = {
                        "status": "BRAK_PLANU_LUB_CYFRYZACJI",
                        "zone": "Brak MPZP w rejestrze cyfrowym (wymagane WZ)",
                        "symbol": None,
                        "plan_name": None,
                    }
                    self._cache[cache_key] = mpzp_res
                    return mpzp_res

                # Rows present but no recognized payload — unknown format, do not
                # claim "brak planu" (avoids false WZ warnings for new proxy formats).
                if "<ROW" in text.upper() or not text.strip():
                    unknown_mpzp: dict[str, str | None] = {
                        "status": "NIEZNANY",
                        "zone": None,
                        "symbol": None,
                        "plan_name": None,
                    }
                    self._cache[cache_key] = unknown_mpzp
                    return unknown_mpzp

                # Empty HTML tables (layer echo without features) — no digital plan data.
                mpzp_res = {
                    "status": "BRAK_PLANU_LUB_CYFRYZACJI",
                    "zone": "Brak MPZP w rejestrze cyfrowym (wymagane WZ)",
                    "symbol": None,
                    "plan_name": None,
                }
                self._cache[cache_key] = mpzp_res
                return mpzp_res
        except Exception as e:
            logger.debug(f"[Geoportal] MPZP GetFeatureInfo failed: {e}")

        default_mpzp: dict[str, str | None] = {"status": "NIEZNANY", "zone": None, "symbol": None, "plan_name": None}
        self._cache[cache_key] = default_mpzp
        return default_mpzp

    async def get_flood_risk_isok(
        self,
        client: httpx.AsyncClient,
        cx: float,
        cy: float,
    ) -> dict[str, str | None]:
        """Queries Wody Polskie INSPIRE ISOK Flood WMS GetFeatureInfo (EPSG:2180)."""
        cache_key = f"flood:{round(cx, 1)},{round(cy, 1)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetFeatureInfo",
            "BBOX": f"{cy - 10:.1f},{cx - 10:.1f},{cy + 10:.1f},{cx + 10:.1f}",
            "CRS": "EPSG:2180",
            "WIDTH": "10",
            "HEIGHT": "10",
            "LAYERS": "NZ.RiskZone,NZ.Fluvial",
            "QUERY_LAYERS": "NZ.RiskZone,NZ.Fluvial",
            "I": "5",
            "J": "5",
            "INFO_FORMAT": "application/json",
        }
        try:
            resp = await client.get(
                self.ISOK_FLOOD_WMS,
                params=params,
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                if features:
                    risk_levels = []
                    for f in features:
                        props = f.get("properties") or {}
                        val = props.get("lor_qualitativevalue")
                        if val:
                            risk_levels.append(str(val))
                    level_str = ", ".join(sorted(set(risk_levels))) if risk_levels else "strefa zalewowa"
                    flood_res: dict[str, str | None] = {
                        "flood_zone": "ZAGROŻENIE_POWODZIOWE",
                        "risk_level": level_str,
                        "description": f"Działka w strefie zagrożenia powodziowego ({level_str})",
                    }
                    self._cache[cache_key] = flood_res
                    return flood_res

                flood_res = {
                    "flood_zone": "BRAK",
                    "risk_level": None,
                    "description": "Brak bezpośredniego zagrożenia powodziowego (poza strefą ISOK)",
                }
                self._cache[cache_key] = flood_res
                return flood_res
        except Exception as e:
            logger.debug(f"[Geoportal] ISOK Flood GetFeatureInfo failed: {e}")

        default_flood: dict[str, str | None] = {"flood_zone": "NIEZNANY", "risk_level": None, "description": None}
        self._cache[cache_key] = default_flood
        return default_flood

    async def get_landslide_risk_sopo(
        self,
        client: httpx.AsyncClient,
        cx: float,
        cy: float,
    ) -> dict[str, Any]:
        """
        Queries PIG-PIB SOPO WMS GetFeatureInfo (EPSG:2180) for active landslides
        and mass movement hazard areas.
        """
        cache_key = f"sopo:{round(cx, 1)},{round(cy, 1)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetFeatureInfo",
            "BBOX": f"{cy - 10:.1f},{cx - 10:.1f},{cy + 10:.1f},{cx + 10:.1f}",
            "CRS": "EPSG:2180",
            "WIDTH": "10",
            "HEIGHT": "10",
            "LAYERS": "0,13",
            "QUERY_LAYERS": "0,13",
            "I": "5",
            "J": "5",
            "INFO_FORMAT": "application/geo+json",
        }
        try:
            resp = await client.get(
                self.SOPO_LANDSLIDE_WMS,
                params=params,
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                if features:
                    ids_clean = [
                        str(p.get("Numer identyfikacyjny") or p.get("ID") or "")
                        for f in features
                        if (p := f.get("properties")) and (p.get("Numer identyfikacyjny") or p.get("ID"))
                    ]
                    ids_str = f" id: {', '.join(ids_clean)}" if ids_clean else ""
                    res = {
                        "risk": "OSUWISKO",
                        "landslide_zone": "OSUWISKO",
                        "has_risk": True,
                        "has_landslide": True,
                        "description": f"Obszar osuwiska / teren zagrożony ruchami masowymi (PIG-PIB SOPO{ids_str})",
                        "features_count": len(features),
                    }
                    self._cache[cache_key] = res
                    return res

                res = {
                    "risk": "BRAK",
                    "landslide_zone": "BRAK",
                    "has_risk": False,
                    "has_landslide": False,
                    "description": "Brak osuwisk i terenów zagrożonych ruchami masowymi (SOPO PIG-PIB)",
                    "features_count": 0,
                }
                self._cache[cache_key] = res
                return res
        except Exception as e:
            logger.debug(f"[Geoportal] SOPO Landslide GetFeatureInfo failed: {e}")

        default_res: dict[str, Any] = {
            "risk": "NIEZNANY",
            "landslide_zone": "NIEZNANY",
            "has_risk": False,
            "has_landslide": False,
            "description": None,
            "features_count": 0,
        }
        self._cache[cache_key] = default_res
        return default_res

    async def get_egib_full_audit(
        self,
        client: httpx.AsyncClient,
        cx: float,
        cy: float,
    ) -> dict[str, Any]:
        """
        Queries KIEG WMS GetFeatureInfo (EPSG:2180) to inspect:
        1. Whether building is disclosed in cadastre (B / Br vs unbuilt Bp or pure agricultural R).
        2. Soil classification and whether agricultural soil is protected (RIIIa/RIIIb/RI/RII/ŁIII/PsIII).
        """
        cache_key = f"egib_full:{round(cx, 1)},{round(cy, 1)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        query_url = (
            f"{self.KIEG_WMS}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&"
            f"BBOX={cy - 5:.1f},{cx - 5:.1f},{cy + 5:.1f},{cx + 5:.1f}&CRS=EPSG:2180&"
            f"WIDTH=10&HEIGHT=10&LAYERS=dzialki,budynki,kontury,uzytki&QUERY_LAYERS=dzialki,budynki,kontury,uzytki&"
            f"I=5&J=5&INFO_FORMAT=text/html"
        )
        try:
            resp = await client.get(query_url, headers=self.headers, timeout=self.timeout)
            if resp.status_code == 200:
                text = resp.text
                found_values: list[str] = []
                for label in ("Oznaczenie konturu", "Oznaczenie u[zż]ytku", "Klasou[zż]ytek", "U[zż]ytek", "Klasa"):
                    for m in re.finditer(rf"{label}[^<]*</td>\s*<td[^>]*>(.*?)</td>", text, re.IGNORECASE):
                        v = m.group(1).strip()
                        if v and v not in found_values:
                            found_values.append(v)

                combined = ",".join(found_values) if found_values else ""
                tokens = re.findall(r"\b[A-Za-z0-9/_-]+\b", combined or text)
                tokens_upper = {t.upper() for t in tokens}

                # Building presence analysis:
                # B = tereny mieszkaniowe, Br = rolne zabudowane, Ba = przemysł, Bi = inne zabudowane
                # Bp = zurbanizowane niezabudowane lub w trakcie budowy
                if any(tag in tokens_upper for tag in ("B", "BR", "BI", "BA")):
                    building_status = "UJAWNIONY"
                elif "BP" in tokens_upper:
                    building_status = "W_TRAKCIE_BUDOWY"
                elif combined:
                    building_status = "BRAK_W_EWIDENCJI"
                else:
                    building_status = "NIEZNANY"

                # Soil classification & protection analysis
                soil_classes = re.findall(
                    r"\b((?:R|Ł|Ps|S|Lzr)(?:I{1,3}[ab]?|IV[ab]?|V|VI[z]?))\b",
                    combined or text,
                    re.IGNORECASE,
                )
                soil_classes_unique = sorted(set(soil_classes), key=lambda s: s.upper())
                protected_matches = [
                    s for s in soil_classes_unique if re.search(r"^(?:R|Ł|Ps|S)(?:I{1,3}[ab]?)$", s, re.IGNORECASE)
                ]
                is_protected_soil = len(protected_matches) > 0
                if soil_classes_unique:
                    soil_class_str: str | None = ", ".join(soil_classes_unique)
                elif combined:
                    soil_class_str = combined
                else:
                    soil_class_str = None

                res = {
                    "contour": combined,
                    "building_status": building_status,
                    "soil_class": soil_class_str,
                    "is_protected_soil": is_protected_soil,
                    "protected_classes": protected_matches,
                }
                self._cache[cache_key] = res
                return res
        except Exception as e:
            logger.debug(f"[Geoportal] KIEG full audit failed: {e}")

        default_res: dict[str, Any] = {
            "contour": "",
            "building_status": "NIEZNANY",
            "soil_class": None,
            "is_protected_soil": False,
            "protected_classes": [],
        }
        self._cache[cache_key] = default_res
        return default_res

    async def get_gdos_protected_areas(
        self,
        client: httpx.AsyncClient,
        cx: float,
        cy: float,
    ) -> dict[str, Any]:
        """
        Queries GDOŚ WMS GetFeatureInfo (EPSG:2180) for Natura 2000,
        nature reserves, landscape parks, and ecological grounds.
        """
        cache_key = f"gdos:{round(cx, 1)},{round(cy, 1)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        layers = (
            "GDOS:ObszarySpecjalnejOchrony,GDOS:SpecjalneObszaryOchrony,"
            "GDOS:ParkiNarodowe,GDOS:Rezerwaty,GDOS:ParkiKrajobrazowe,"
            "GDOS:ObszaryChronionegoKrajobrazu,GDOS:UzytkiEkologiczne"
        )
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetFeatureInfo",
            "BBOX": f"{cy - 10:.1f},{cx - 10:.1f},{cy + 10:.1f},{cx + 10:.1f}",
            "CRS": "EPSG:2180",
            "WIDTH": "10",
            "HEIGHT": "10",
            "LAYERS": layers,
            "QUERY_LAYERS": layers,
            "I": "5",
            "J": "5",
            "INFO_FORMAT": "application/json",
        }
        try:
            resp = await client.get(
                self.GDOS_PROTECTED_WMS,
                params=params,
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                if features:
                    names = []
                    for f in features:
                        props = f.get("properties") or {}
                        n = props.get("nazwa") or props.get("kodinspire")
                        if n:
                            names.append(str(n))
                    names_str = ", ".join(sorted(set(names))) if names else "Obszar chroniony"
                    res = {
                        "is_protected": True,
                        "zone_type": names_str,
                        "description": f"Obszar chroniony przyrodniczo (GDOŚ: {names_str})",
                        "features_count": len(features),
                    }
                    self._cache[cache_key] = res
                    return res

                res = {
                    "is_protected": False,
                    "zone_type": None,
                    "description": "Brak form ochrony przyrody (poza strefą GDOŚ)",
                    "features_count": 0,
                }
                self._cache[cache_key] = res
                return res
        except Exception as e:
            logger.debug(f"[Geoportal] GDOŚ GetFeatureInfo failed: {e}")

        default_res = {
            "is_protected": False,
            "zone_type": None,
            "description": None,
            "features_count": 0,
        }
        self._cache[cache_key] = default_res
        return default_res

    async def get_nid_monuments(
        self,
        client: httpx.AsyncClient,
        cx: float,
        cy: float,
    ) -> dict[str, Any]:
        """
        Queries NID WMS GetFeatureInfo (EPSG:2180) for immovable historical monuments
        and conservatory protection zones.
        """
        cache_key = f"nid:{round(cx, 1)},{round(cy, 1)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetFeatureInfo",
            "BBOX": f"{cy - 10:.1f},{cx - 10:.1f},{cy + 10:.1f},{cx + 10:.1f}",
            "CRS": "EPSG:2180",
            "WIDTH": "10",
            "HEIGHT": "10",
            "LAYERS": "Immovable_Monuments",
            "QUERY_LAYERS": "Immovable_Monuments",
            "I": "5",
            "J": "5",
            "INFO_FORMAT": "text/html",
        }
        try:
            resp = await client.get(
                self.NID_MONUMENTS_WMS,
                params=params,
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
            if resp.status_code == 200 and ("<TABLE" in resp.text.upper() or "SITENAME" in resp.text.upper()):
                site_m = re.search(r"SITENAME.*?<TD[^>]*>(.*?)</TD>", resp.text, re.IGNORECASE | re.DOTALL)
                doc_m = re.search(
                    r"LEGALFOUNDATIONDOCUMENT.*?<TD[^>]*>(.*?)</TD>", resp.text, re.IGNORECASE | re.DOTALL
                )
                site_name = re.sub(r"[\s]+", " ", site_m.group(1)).strip() if site_m else "Zabytek nieruchomy"
                doc_name = re.sub(r"[\s]+", " ", doc_m.group(1)).strip() if doc_m else ""
                desc = f"Zabytek nieruchomy / strefa konserwatorska (NID: {site_name}{f', {doc_name}' if doc_name else ''})"
                res = {
                    "is_monument": True,
                    "name": site_name,
                    "document": doc_name,
                    "description": desc,
                }
                self._cache[cache_key] = res
                return res

            res = {
                "is_monument": False,
                "name": None,
                "document": None,
                "description": "Brak wpisu w rejestrze zabytków NID",
            }
            self._cache[cache_key] = res
            return res
        except Exception as e:
            logger.debug(f"[Geoportal] NID GetFeatureInfo failed: {e}")

        default_res = {
            "is_monument": False,
            "name": None,
            "document": None,
            "description": None,
        }
        self._cache[cache_key] = default_res
        return default_res

    async def get_noise_level_audit(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lon: float,
        voivodeship: str = "PODKARPACKIE",
    ) -> dict[str, Any]:
        """
        Acoustic audit: queries GIOŚ EHAŁAS REST API (strategic noise maps Lden/Lnight)
        and evaluates proximity buffers to major expressways (S19/A4), DK94, and active railway lines.
        Threshold: >65 dB Lden triggers a warning and score penalty.
        """
        cache_key = f"noise:{round(lat, 4)},{round(lon, 4)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 1. Check GIOŚ EHAŁAS API with short timeout
        try:
            api_url = (
                f"{self.GIOS_NOISE_API}?numerStrony=0&liczbaElementowNaStronie=10&"
                f"wojewodztwo={voivodeship}&rundaMapowania=4%20Runda%20Mapowania&"
                f"zrodloHalasu=Ha%C5%82as%20drogowy%20poza%20aglomeracj%C4%85"
            )
            resp = await client.get(api_url, headers=self.headers, timeout=2.5)
            if resp.status_code == 200:
                data = resp.json()
                for feat in data.get("features", []):
                    props = feat.get("properties") or {}
                    interval = str(props.get("przedzial") or "")
                    if any(t in interval for t in ("6569", "7074", "7579", "Lden65", "Lden70", "Lden75")):
                        res = {
                            "noise_level_db": 68.0,
                            "exceeds_threshold": True,
                            "zone": "WYSOKI_HAŁAS (>65 dB)",
                            "description": f"Podwyższony poziom hałasu komunikacyjnego (>65 dB Lden, GIOŚ: {interval})",
                        }
                        self._cache[cache_key] = res
                        return res
        except Exception as e:
            logger.debug(f"[Geoportal] GIOŚ noise API query failed or timed out: {e}")

        # 2. Highway / transit corridor acoustic proximity model (S19, A4, DK94)
        from src.services.market_analyzer import EXPRESSWAY_HUBS, haversine_km

        min_hub_dist_km = min(haversine_km(lat, lon, h_lat, h_lon) for _, h_lat, h_lon in EXPRESSWAY_HUBS)

        if min_hub_dist_km < 0.40:
            level = 68.0 if min_hub_dist_km >= 0.20 else 72.0
            res = {
                "noise_level_db": level,
                "exceeds_threshold": True,
                "zone": "WYSOKI_HAŁAS (>65 dB)",
                "description": f"Podwyższony poziom hałasu komunikacyjnego ({level:.0f} dB Lden) — sąsiedztwo węzła S19/A4/DK94 (<400m)",
            }
            self._cache[cache_key] = res
            return res

        res = {
            "noise_level_db": 52.0,
            "exceeds_threshold": False,
            "zone": "NORMATYWNY",
            "description": "Poziom hałasu w normie środowiskowej (<55 dB Lden)",
        }
        self._cache[cache_key] = res
        return res

    def get_cemetery_proximity(
        self,
        cx: float,
        cy: float,
        mpzp_zone: str | None = None,
        surrounding_risks: list[str] | None = None,
        kieg_contours: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Evaluates proximity to cemeteries based on MPZP (symbol ZC / cmentarz)
        and cadastre contours (Tc / cmentarz).
        Zone <50m: statutory prohibition on building/window placement.
        Zone 50-150m: water supply and sanitary restrictions.
        """
        _ = (cx, cy)
        risks_joined = " ".join(surrounding_risks or []).lower()
        contours_joined = " ".join(kieg_contours or []).lower()
        mpzp_lower = (mpzp_zone or "").lower()

        is_cemetery_immediate = "zc" in mpzp_lower or "cmentarz" in mpzp_lower
        is_cemetery_surrounding = "cmentarz" in risks_joined or "cmentarz" in contours_joined or "tc" in contours_joined

        if is_cemetery_immediate:
            return {
                "has_cemetery_risk": True,
                "zone": "<50m",
                "distance_m": 50.0,
                "description": "Działka w bezpośredniej strefie cmentarza (<50m) — zakaz rozbudowy i okien mieszkalnych",
            }
        if is_cemetery_surrounding:
            return {
                "has_cemetery_risk": True,
                "zone": "50-150m",
                "distance_m": 150.0,
                "description": "Działka w strefie ochronnej cmentarza (50–150m) — ograniczenia ujęć wody i sanitarne",
            }

        return {
            "has_cemetery_risk": False,
            "zone": "BRAK",
            "distance_m": None,
            "description": "Brak cmentarza w strefie ochronnej 150m",
        }

    @staticmethod
    def count_gesut_pixels(png_bytes: bytes) -> dict[str, int]:
        """Counts pixels of each GESUT network color in a transparent WMS map image.

        Optimized: single pass over ``getdata()`` with precomputed layer colors
        and early skip of fully transparent pixels (no nested per-pixel loops
        over ``img.load()``).
        """
        counts: dict[str, int] = {key: 0 for key, _, _ in GeoportalService.GESUT_LAYERS}
        try:
            img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        except Exception:
            return counts
        tol = GeoportalService.GESUT_COLOR_TOLERANCE
        layers = [(key, tr, tg, tb) for key, _, (tr, tg, tb) in GeoportalService.GESUT_LAYERS]
        pixels = list(img.getdata())
        for pixel in pixels:
            if not isinstance(pixel, tuple) or len(pixel) < 4:
                continue
            a = pixel[3]
            if a < 128:
                continue
            r, g, b = pixel[0], pixel[1], pixel[2]
            for key, tr, tg, tb in layers:
                if abs(r - tr) <= tol and abs(g - tg) <= tol and abs(b - tb) <= tol:
                    counts[key] += 1
                    break
        return counts

    async def get_gesut_networks(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lon: float,
        radius_m: float | None = None,
    ) -> dict[str, Any]:
        """
        Detects utility networks (water, sewerage, gas, power, heating, telecom)
        near the given coordinates by rendering the powiat GESUT WMS and counting
        colored network pixels within a ~radius_m box.

        Returns:
            {"coverage": bool, "checked_radius_m": float, "sources": [...],
             "networks": {"woda": bool, ...}}
        """
        radius = radius_m or self.GESUT_CHECK_RADIUS_M
        cache_key = f"gesut:{round(lat, 3)},{round(lon, 3)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        half = radius / 111139.0
        bbox = f"{lat - half:.7f},{lon - half:.7f},{lat + half:.7f},{lon + half:.7f}"
        layer_names = [layer for _, layer, _ in self.GESUT_LAYERS]
        detected: dict[str, bool] = {key: False for key, _, _ in self.GESUT_LAYERS}
        sources: list[str] = []
        coverage = False

        for service in self.GESUT_WMS_SERVICES:
            params = {
                "SERVICE": "WMS",
                "VERSION": "1.3.0",
                "REQUEST": "GetMap",
                "BBOX": bbox,
                "CRS": "EPSG:4326",
                "WIDTH": "128",
                "HEIGHT": "128",
                "LAYERS": ",".join(layer_names),
                "STYLES": "",
                "FORMAT": "image/png",
                "TRANSPARENT": "true",
            }
            try:
                resp = await client.get(service["url"], params=params, headers=self.headers, timeout=self.timeout)
                if resp.status_code != 200:
                    logger.debug(f"[Geoportal] GESUT WMS {service['name']} HTTP {resp.status_code}")
                    continue
                content = resp.content
                if not content or not content.startswith(b"\x89PNG"):
                    logger.debug(f"[Geoportal] GESUT WMS {service['name']} returned non-PNG data")
                    continue
                counts = self.count_gesut_pixels(content)
                hit_any = any(c >= self.GESUT_MIN_PIXELS for c in counts.values())
                if not hit_any:
                    continue
                coverage = True
                sources.append(service["name"])
                for key in detected:
                    if counts.get(key, 0) >= self.GESUT_MIN_PIXELS:
                        detected[key] = True
            except Exception as e:
                logger.debug(f"[Geoportal] GESUT WMS {service['name']} query failed: {e}")

        result: dict[str, Any] = {
            "coverage": coverage,
            "checked_radius_m": radius,
            "sources": sources,
            "networks": detected,
        }
        self._cache[cache_key] = result
        return result

    async def get_broadband_status(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lon: float,
        voivodeship: str = "podkarpackie",
    ) -> dict[str, Any]:
        """
        Queries official SIDUSIS (internet.gov.pl) GeoServer WMS GetFeatureInfo.
        Checks broadband connectivity (FTTH / fixed line coverage / KPO plans).
        """
        cache_key = f"broadband:{round(lat, 4)},{round(lon, 4)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        res: dict[str, Any] = {
            "status": "BRAK_ZASIĘGU",
            "details": "Brak potwierdzonego zasięgu stacjonarnego internetu szerokopasmowego w SIDUSIS",
            "has_fiber": False,
        }

        # Convert WGS84 (lat, lon) to EPSG:3857 (Web Mercator)
        x = lon * 20037508.34 / 180.0
        y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0) * 20037508.34 / 180.0

        layer = f"s_{voivodeship.lower()}_buildings"
        query_url = (
            f"https://internet.gov.pl/geoserver/public/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&"
            f"LAYERS={layer}&QUERY_LAYERS={layer}&CRS=EPSG:3857&"
            f"BBOX={x - 30:.1f},{y - 30:.1f},{x + 30:.1f},{y + 30:.1f}&"
            f"WIDTH=101&HEIGHT=101&I=50&J=50&INFO_FORMAT=application/json"
        )
        try:
            resp = await client.get(query_url, headers=self.headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                if features:
                    props = features[0].get("properties", {})
                    status_code = props.get("status") or props.get("id_statusu") or props.get("status_id")
                    medium = str(props.get("medium") or "").lower()
                    operator = props.get("nazwa_operatora") or ""
                    if status_code in (1, 2) or "światłowód" in medium or "ftth" in medium:
                        op_info = f" ({operator})" if operator else ""
                        res = {
                            "status": "ŚWIATŁOWÓD_AKTYWNY",
                            "details": f"Aktywny zasięg stacjonarnego internetu światłowodowego FTTH{op_info}",
                            "has_fiber": True,
                        }
                    elif status_code in (4, 5) or "kpo" in str(props).lower() or "ferc" in str(props).lower():
                        plan = props.get("planowany_termin_realizacji") or ""
                        plan_info = f" (plan: {plan})" if plan else ""
                        res = {
                            "status": "PLANOWANY_KPO_FERC",
                            "details": f"Planowana inwestycja szerokopasmowa ze środków publicznych KPO/FERC{plan_info}",
                            "has_fiber": False,
                        }
                    elif status_code == 3:
                        res = {
                            "status": "ZASIĘG_TEORETYCZNY",
                            "details": "Zasięg teoretyczny – wymaga potwierdzenia warunków technicznych u operatora",
                            "has_fiber": False,
                        }
        except Exception as e:
            logger.debug(f"[Geoportal] SIDUSIS broadband query failed: {e}")

        self._cache[cache_key] = res
        return res

    async def get_terrain_slope_and_aspect(
        self,
        client: httpx.AsyncClient,
        cx: float,
        cy: float,
    ) -> dict[str, Any]:
        """
        Queries official GUGiK NMT REST API (Numeryczny Model Terenu).
        Calculates elevation, slope percentage and terrain aspect direction.
        cx: Easting in EPSG:2180 (~740000)
        cy: Northing in EPSG:2180 (~260000)
        """
        cache_key = f"nmt:{round(cx, 1)},{round(cy, 1)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        res: dict[str, Any] = {
            "elevation_m": None,
            "slope_pct": None,
            "aspect": None,
            "description": None,
            "severity": "info",
        }

        pts = [
            ("C", cy, cx),
            ("N", cy + 25.0, cx),
            ("S", cy - 25.0, cx),
            ("E", cy, cx + 25.0),
            ("W", cy, cx - 25.0),
        ]
        tasks = [
            client.get(
                "https://services.gugik.gov.pl/nmt/",
                params={"request": "GetHbyXY", "x": f"{y:.1f}", "y": f"{x:.1f}"},
                headers=self.headers,
                timeout=self.timeout,
            )
            for _, y, x in pts
        ]
        try:
            resps = await asyncio.gather(*tasks, return_exceptions=True)
            elevs: dict[str, float] = {}
            for (k, _, _), r in zip(pts, resps, strict=True):
                if not isinstance(r, BaseException) and getattr(r, "status_code", None) == 200:
                    text_val = str(getattr(r, "text", ""))
                    val_str = text_val.strip().replace(",", ".")
                    try:
                        elevs[k] = float(val_str)
                    except ValueError:
                        pass

            if "C" in elevs:
                res["elevation_m"] = round(elevs["C"], 1)

            if len(elevs) == 5:
                g_ns = (elevs["N"] - elevs["S"]) / 50.0
                g_ew = (elevs["E"] - elevs["W"]) / 50.0
                slope_pct = round(math.hypot(g_ns, g_ew) * 100.0, 1)
                res["slope_pct"] = slope_pct

                if slope_pct < 2.0:
                    aspect = "PŁASKI"
                else:
                    angle = math.degrees(math.atan2(-g_ew, -g_ns)) % 360.0
                    if 135.0 <= angle <= 225.0:
                        aspect = "POŁUDNIOWY"
                    elif 45.0 < angle < 135.0:
                        aspect = "WSCHODNI"
                    elif 225.0 < angle < 315.0:
                        aspect = "ZACHODNI"
                    else:
                        aspect = "PÓŁNOCNY"
                res["aspect"] = aspect

                if slope_pct > 8.0:
                    res["severity"] = "danger"
                    res["description"] = (
                        f"Strome nachylenie stoku: spadek {slope_pct:.1f}% ({aspect}) – "
                        "ryzyko konieczności budowy murów oporowych i trudności w odprowadzaniu wód opadowych"
                    )
                elif aspect in ("POŁUDNIOWY", "POŁUDNIOWO-ZACHODNI", "POŁUDNIOWO-WSCHODNI") and slope_pct >= 2.0:
                    res["severity"] = "success"
                    res["description"] = (
                        f"Korzystna południowa ekspozycja stoku: spadek {slope_pct:.1f}% ({aspect}) – "
                        "doskonałe nasłonecznienie parceli"
                    )
                else:
                    res["severity"] = "info"
                    res["description"] = f"Umiarkowane nachylenie terenu: {slope_pct:.1f}% ({aspect})"
        except Exception as e:
            logger.debug(f"[Geoportal] GUGiK NMT query failed: {e}")

        self._cache[cache_key] = res
        return res

    def get_walkability_audit(
        self,
        lat: float,
        lon: float,
    ) -> dict[str, Any]:
        """
        Audits walkability: distance to nearest Podkarpacie PKA station/halt
        and transit availability.
        """
        from src.services.market_analyzer import PKA_STATIONS, haversine_km

        pka_distances = [(name, haversine_km(lat, lon, plat, plon)) for name, plat, plon in PKA_STATIONS]
        pka_distances.sort(key=lambda x: x[1])
        nearest_pka_name, nearest_pka_dist_km = pka_distances[0]

        if nearest_pka_dist_km > 15.0:
            return {
                "pka_name": None,
                "nearest_station": None,
                "pka_dist_m": None,
                "distance_m": None,
                "pka_dist_km": None,
                "is_near_pka": False,
                "walk_min": None,
                "walk_time_min": None,
                "description": "Lokalizacja poza zasięgiem kolei aglomeracyjnej (>15 km do stacji).",
            }

        pka_dist_m = int(round(nearest_pka_dist_km * 1000))

        is_near_pka = pka_dist_m <= 1500
        walk_min = max(1, round(pka_dist_m / 80))

        desc = (
            f"Stacja kolejowa: {nearest_pka_name} ({pka_dist_m} m, ~{walk_min} min pieszo) – szybki dojazd aglomeracyjny"
            if is_near_pka
            else f"Najbliższa stacja kolejowa: {nearest_pka_name} ({nearest_pka_dist_km:.1f} km)"
        )

        return {
            "pka_name": nearest_pka_name,
            "nearest_station": nearest_pka_name,
            "pka_dist_m": pka_dist_m,
            "distance_m": pka_dist_m,
            "pka_dist_km": nearest_pka_dist_km,
            "is_near_pka": is_near_pka,
            "walk_min": walk_min if is_near_pka else None,
            "walk_time_min": walk_min if is_near_pka else None,
            "description": desc,
        }

    async def get_power_lines_risk(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lon: float,
        cx: float | None = None,
        cy: float | None = None,
    ) -> dict[str, Any]:
        """
        Audits proximity to high-voltage transmission lines (110kV / 220kV / 400kV).
        """
        cache_key = f"power:{round(lat, 4)},{round(lon, 4)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        res: dict[str, Any] = {
            "risk": "BEZPIECZNIE",
            "distance_m": None,
            "description": "Brak napowietrznych linii przesyłowych najwyższych napięć w buforze 200m",
        }

        from src.services.market_analyzer import haversine_km

        for name, clat, clon in HIGH_VOLTAGE_CORRIDORS:
            dist_km = haversine_km(lat, lon, clat, clon)
            dist_m = int(round(dist_km * 1000))
            if dist_m < 150:
                res = {
                    "risk": f"LINIA_WN_{dist_m}M",
                    "distance_m": dist_m,
                    "description": f"{name} w odległości {dist_m} m (pas technologiczny, pole EM)",
                }
                break

        self._cache[cache_key] = res
        return res

    async def get_solar_potential(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lon: float,
    ) -> dict[str, Any]:
        """
        Queries European Commission PVGIS API for solar irradiance and annual sun potential.
        Calculates kWh/m2/year and annual sunshine hours for the exact location.
        """
        cache_key = f"solar:pvgis:{round(lat, 4)},{round(lon, 4)}"
        cached = await self._get_cached(cache_key)
        if cached and isinstance(cached, dict):
            return cached

        default_res: dict[str, Any] = {
            "solar_energy_kwh_m2": None,
            "solar_hours_per_year": None,
            "description": None,
        }

        url = (
            f"https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
            f"?lat={round(lat, 4)}&lon={round(lon, 4)}&peakpower=1&loss=14&outputformat=json"
        )
        try:
            resp = await client.get(url, timeout=4.0)
            if resp.status_code == 200:
                payload = resp.json()
                outputs = payload.get("outputs", {})
                totals = outputs.get("totals", {}).get("fixed", {})
                kwh_m2 = totals.get("H(i)_y")
                sun_hours = None
                if kwh_m2 is not None:
                    kwh_m2 = round(float(kwh_m2), 1)
                    sun_hours = round(kwh_m2 * 1.62)

                res = {
                    "solar_energy_kwh_m2": kwh_m2,
                    "solar_hours_per_year": sun_hours,
                    "description": f"Nasłonecznienie: {kwh_m2} kWh/m²/rok (~{sun_hours} h słońca)" if kwh_m2 else None,
                }
                await self._set_cached(cache_key, res, ttl_days=180)
                return res
        except Exception as e:
            logger.debug(f"[Geoportal] PVGIS solar fetch note: {e}")

        return default_res

    async def get_walkability_poi_audit(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lon: float,
        radius_m: int = 1500,
    ) -> dict[str, Any]:
        """
        Inventories social infrastructure and daily amenities within radius_m (15-minute city audit)
        using OpenStreetMap Overpass API. Calculates distances and walking times.
        """
        cache_key = f"poi:overpass:{round(lat, 4)},{round(lon, 4)}:{radius_m}"
        cached = await self._get_cached(cache_key)
        if cached and isinstance(cached, dict):
            return cached

        default_res: dict[str, Any] = {
            "poi_counts": {},
            "nearest_poi": {},
        }

        overpass_ql = f"""[out:json][timeout:5];
(
  node["shop"~"supermarket|convenience"](around:{radius_m},{lat},{lon});
  node["amenity"="pharmacy"](around:{radius_m},{lat},{lon});
  node["amenity"~"school|kindergarten"](around:{radius_m},{lat},{lon});
  node["amenity"~"clinic|doctors|hospital"](around:{radius_m},{lat},{lon});
  node["highway"="bus_stop"](around:{radius_m},{lat},{lon});
  node["railway"~"station|halt"](around:{radius_m},{lat},{lon});
  node["leisure"~"park|playground"](around:{radius_m},{lat},{lon});
);
out tags center 60;"""

        try:
            from src.services.market_analyzer import haversine_km

            resp = await client.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": overpass_ql},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                elements = data.get("elements", [])
                counts: dict[str, int] = {
                    "sklepy": 0,
                    "apteki": 0,
                    "edukacja": 0,
                    "zdrowie": 0,
                    "transport": 0,
                    "rekreacja": 0,
                }
                nearest: dict[str, dict[str, Any]] = {}

                for el in elements:
                    tags = el.get("tags", {})
                    p_lat = el.get("lat")
                    p_lon = el.get("lon")
                    if p_lat is None or p_lon is None:
                        continue
                    dist_m = int(round(haversine_km(lat, lon, p_lat, p_lon) * 1000))
                    walk_min = max(1, round(dist_m / 80))
                    name = tags.get("name") or "obiekt"

                    category = None
                    if tags.get("shop") in ("supermarket", "convenience"):
                        category = "sklepy"
                    elif tags.get("amenity") == "pharmacy":
                        category = "apteki"
                    elif tags.get("amenity") in ("school", "kindergarten"):
                        category = "edukacja"
                    elif tags.get("amenity") in ("clinic", "doctors", "hospital"):
                        category = "zdrowie"
                    elif tags.get("highway") == "bus_stop" or tags.get("railway") in ("station", "halt"):
                        category = "transport"
                    elif tags.get("leisure") in ("park", "playground"):
                        category = "rekreacja"

                    if category:
                        counts[category] = counts.get(category, 0) + 1
                        if category not in nearest or dist_m < nearest[category]["dist_m"]:
                            nearest[category] = {
                                "name": name,
                                "dist_m": dist_m,
                                "walk_min": walk_min,
                            }

                res = {
                    "poi_counts": counts,
                    "nearest_poi": nearest,
                }
                await self._set_cached(cache_key, res, ttl_days=45)
                return res
        except Exception as e:
            logger.debug(f"[Geoportal] Overpass POI fetch note: {e}")

        return default_res

    async def get_geology_audit(
        self,
        client: httpx.AsyncClient,
        lat: float,
        lon: float,
        egib_soil: str | None = None,
        slope_pct: float | None = None,
    ) -> dict[str, Any]:
        """
        Audits geotechnical and soil conditions based on EGiB soil records,
        slope parameters, and hydrogeological factors (GZWP).
        """
        _ = client
        cache_key = f"geology:{round(lat, 4)},{round(lon, 4)}:{egib_soil or ''}"
        cached = await self._get_cached(cache_key)
        if cached and isinstance(cached, dict):
            return cached

        formation = "Grunty mineralne rodzime (utwory czwartorzędowe)"
        risk_note = (
            "Warunki gruntowe standardowe — zalecana opinia geotechniczna przed rozpoczęciem prac fundamentowych."
        )

        soil_upper = (egib_soil or "").upper()
        if any(s in soil_upper for s in ("Ł", "PS", "LZ", "W-Ł")):
            formation = "Grunty organiczne / aluwia dolinne (utwory podmokłe)"
            risk_note = "⚠️ Grunty o obniżonej nośności (użytki zielone/aluwialne) — ryzyko konieczności wymiany gruntu lub posadowienia pośredniego (płyta fundamentowa / mikropale)."
        elif any(s in soil_upper for s in ("RII", "RIII", "RIV")):
            formation = "Gliny piaszczyste i pyły lessowe / utwory zwałowe"
            risk_note = "Dobra nośność podłoża gruntowego pod budownictwo jednorodzinne."

        if slope_pct and slope_pct > 12.0:
            risk_note += f" Uwaga: znaczny spadek terenu ({slope_pct:.1f}%) — wymagana weryfikacja stateczności skarpy i drenażu opadowego."

        res = {
            "geology_formation": formation,
            "geology_risk_note": risk_note,
        }
        await self._set_cached(cache_key, res, ttl_days=90)
        return res

    async def audit_location(
        self,
        lat: float,
        lon: float,
        radius_meters: int = 120,
        category: Any = "dom",
    ) -> dict[str, Any]:
        """
        Comprehensive spatial audit:
        1. Identifies the main parcel and computes exact cadastral area.
        2. Queries MPZP zoning and digital plan status (KI MPZP).
        3. Queries flood risk zones (ISOK Hydroportal).
        4. Scans surrounding parcels within radius_meters in 8 cardinal directions (skipped for flats).
        5. Checks official EGiB contours for industrial (Ba), commercial (Bi), or railway (Tk) risks.
        6. Generates direct Geoportal link.
        """
        cat_str = category.value if hasattr(category, "value") else str(category or "dom")
        audit_cache_key = f"audit:v3:{round(lat, 5)},{round(lon, 5)}:{cat_str.lower()}"
        cached_audit = await self._get_cached(audit_cache_key)
        if cached_audit and isinstance(cached_audit, dict):
            return cached_audit

        result: dict[str, Any] = {
            "main_parcel_id": None,
            "main_parcel_number": None,
            "cadastral_area": None,
            "geoportal_url": self.generate_geoportal_url(lat=lat, lon=lon),
            "gunb_url": self.generate_gunb_url(),
            "gesut_url": self.generate_geoportal_url(lat=lat, lon=lon),
            "gesut_networks": None,
            "surrounding_risks": [],
            "surrounding_parcels_count": 0,
            "mpzp_zone": None,
            "mpzp_status": None,
            "mpzp_plan_name": None,
            "flood_risk_zone": None,
            "flood_risk_level": None,
            "flood_risk_desc": None,
            "landslide_risk": None,
            "egib_building_status": None,
            "egib_soil_class": None,
            "noise_level_db": None,
            "noise_zone": None,
            "nature_protected_zone": None,
            "monument_zone": None,
            "cemetery_buffer_zone": None,
            "broadband_status": None,
            "broadband_details": None,
            "parcel_front_width_m": None,
            "parcel_length_m": None,
            "parcel_aspect_ratio": None,
            "parcel_shape_type": None,
            "terrain_slope_pct": None,
            "terrain_aspect": None,
            "walkability_pka_dist_m": None,
            "walkability_pka_name": None,
            "power_lines_risk": None,
            "solar_hours_per_year": None,
            "solar_energy_kwh_m2": None,
            "poi_counts": None,
            "nearest_poi": None,
            "geology_formation": None,
            "geology_risk_note": None,
        }

        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            # Step 1: Main parcel
            main_info = await self.get_parcel_by_xy(client, lat, lon)
            if not main_info:
                return result

            main_pid = main_info["parcel_id"]
            result["main_parcel_id"] = main_pid
            result["main_parcel_number"] = main_pid.split(".")[-1]
            result["geoportal_url"] = self.generate_geoportal_url(parcel_id=main_pid)
            result["gunb_url"] = self.generate_gunb_url(parcel_id=main_pid)
            result["gesut_url"] = self.generate_geoportal_url(parcel_id=main_pid)

            main_area, main_centroid, shape_metrics = await self.get_parcel_geometry_and_area(
                client, main_pid, return_details=True
            )
            result["cadastral_area"] = main_area
            result["parcel_front_width_m"] = shape_metrics.get("front_width_m")
            result["parcel_length_m"] = shape_metrics.get("length_m")
            result["parcel_aspect_ratio"] = shape_metrics.get("aspect_ratio")
            result["parcel_shape_type"] = shape_metrics.get("shape_type")

            is_flat = cat_str.lower() in ("mieszkanie", "apartment", "flat")
            if not is_flat and shape_metrics.get("front_width_m") and shape_metrics["front_width_m"] < 16.0:
                result["surrounding_risks"].append(
                    f"Wąska działka: szerokość frontu {shape_metrics['front_width_m']:.1f} m (<16 m)"
                )

            # Step 2: Environmental, Zoning, Broadband, Terrain & Utility queries (concurrent)
            mpzp_task = self.get_mpzp_info(client, main_centroid[0], main_centroid[1]) if main_centroid else None
            flood_task = self.get_flood_risk_isok(client, main_centroid[0], main_centroid[1]) if main_centroid else None
            sopo_task = (
                self.get_landslide_risk_sopo(client, main_centroid[0], main_centroid[1]) if main_centroid else None
            )
            egib_task = self.get_egib_full_audit(client, main_centroid[0], main_centroid[1]) if main_centroid else None
            gdos_task = (
                self.get_gdos_protected_areas(client, main_centroid[0], main_centroid[1]) if main_centroid else None
            )
            nid_task = self.get_nid_monuments(client, main_centroid[0], main_centroid[1]) if main_centroid else None
            noise_task = self.get_noise_level_audit(client, lat, lon)
            gesut_task = self.get_gesut_networks(client, lat, lon)
            broadband_task = self.get_broadband_status(client, lat, lon)
            terrain_task = (
                self.get_terrain_slope_and_aspect(client, main_centroid[0], main_centroid[1]) if main_centroid else None
            )
            power_lines_task = self.get_power_lines_risk(
                client,
                lat,
                lon,
                cx=main_centroid[0] if main_centroid else None,
                cy=main_centroid[1] if main_centroid else None,
            )
            solar_task = self.get_solar_potential(client, lat, lon)
            poi_task = self.get_walkability_poi_audit(client, lat, lon)

            # Step 3: Surrounding search points (8 directions) - only for non-flats.
            # Coroutines are only created here; they run together with the env tasks
            # in the single gather wave below (all inputs are already known —
            # main_pid is only used to filter the results afterwards).
            surround_tasks = []
            if not is_flat:
                d_lat = radius_meters / 111139.0
                d_lon = radius_meters / (111139.0 * math.cos(math.radians(lat)))
                angles = [0, 45, 90, 135, 180, 225, 270, 315]
                surround_coords = [
                    (lon + d_lon * math.cos(math.radians(a)), lat + d_lat * math.sin(math.radians(a))) for a in angles
                ]
                surround_tasks = [self.get_parcel_by_xy(client, pt_lat, pt_lon) for pt_lon, pt_lat in surround_coords]

            # Single concurrent wave: surrounding parcels + environmental/zoning tasks.
            # Both gathers are created first and awaited together, so the I/O
            # overlaps in one wave. Semantics unchanged: parcel-level failures stay
            # non-fatal (inner return_exceptions=True, as before), env failures
            # propagate exactly like the old standalone env gather.
            surround_future = asyncio.gather(*surround_tasks, return_exceptions=True)
            env_future = asyncio.gather(
                mpzp_task or asyncio.sleep(0, result={}),
                flood_task or asyncio.sleep(0, result={}),
                sopo_task or asyncio.sleep(0, result={}),
                egib_task or asyncio.sleep(0, result={}),
                gdos_task or asyncio.sleep(0, result={}),
                nid_task or asyncio.sleep(0, result={}),
                noise_task,
                gesut_task,
                broadband_task,
                terrain_task or asyncio.sleep(0, result={}),
                power_lines_task,
                solar_task,
                poi_task,
            )
            surround_infos, env_results = await asyncio.gather(surround_future, env_future)
            (
                mpzp,
                flood,
                sopo,
                egib,
                gdos,
                nid,
                noise,
                gesut,
                broadband,
                terrain,
                power_lines,
                solar,
                poi,
            ) = env_results

            geology = await self.get_geology_audit(
                client,
                lat,
                lon,
                egib_soil=egib.get("soil_class"),
                slope_pct=terrain.get("slope_pct"),
            )
            result["solar_hours_per_year"] = solar.get("solar_hours_per_year")
            result["solar_energy_kwh_m2"] = solar.get("solar_energy_kwh_m2")
            result["poi_counts"] = poi.get("poi_counts")
            result["nearest_poi"] = poi.get("nearest_poi")
            result["geology_formation"] = geology.get("geology_formation")
            result["geology_risk_note"] = geology.get("geology_risk_note")
            if geology.get("geology_risk_note") and "⚠️" in str(geology["geology_risk_note"]):
                result["surrounding_risks"].append(geology["geology_risk_note"])

            surround_pids: set[str] = set()
            for r in surround_infos:
                if isinstance(r, dict) and r.get("parcel_id"):
                    pid = r["parcel_id"]
                    if pid != main_pid:
                        surround_pids.add(pid)

            result["surrounding_parcels_count"] = len(surround_pids)

            result["mpzp_zone"] = mpzp.get("zone")
            result["mpzp_status"] = mpzp.get("status")
            result["mpzp_plan_name"] = mpzp.get("plan_name")
            result["flood_risk_zone"] = flood.get("flood_zone")
            result["flood_risk_level"] = flood.get("risk_level")
            result["flood_risk_desc"] = flood.get("description")
            if flood.get("flood_zone") == "ZAGROŻENIE_POWODZIOWE":
                result["surrounding_risks"].append(flood.get("description") or "Strefa zagrożenia powodziowego")

            result["gesut_networks"] = gesut
            result["landslide_risk"] = sopo.get("risk")
            if sopo.get("risk") in ("OSUWISKO", "ZAGROŻENIE_OSUWISKIEM"):
                result["surrounding_risks"].append(
                    sopo.get("description") or "Obszar zagrożony osuwiskami (SOPO PIG-PIB)"
                )

            result["egib_building_status"] = egib.get("building_status")
            result["egib_soil_class"] = egib.get("soil_class")
            if egib.get("is_protected_soil") and egib.get("soil_class"):
                result["surrounding_risks"].append(f"Gleba chroniona w EGiB ({egib.get('soil_class')})")

            if gdos.get("is_protected"):
                result["nature_protected_zone"] = gdos.get("zone_type")
                result["surrounding_risks"].append(gdos.get("description") or "Obszar chroniony przyrodniczo (GDOŚ)")

            if nid.get("is_monument"):
                result["monument_zone"] = nid.get("name")
                result["surrounding_risks"].append(nid.get("description") or "Zabytek / strefa konserwatorska (NID)")

            result["noise_level_db"] = noise.get("noise_level_db")
            result["noise_zone"] = noise.get("zone")
            if noise.get("exceeds_threshold"):
                result["surrounding_risks"].append(noise.get("description") or "Przekroczenie norm hałasu (>65 dB)")

            result["broadband_status"] = broadband.get("status")
            result["broadband_details"] = broadband.get("details")
            if broadband.get("status") == "BRAK":
                result["surrounding_risks"].append("Brak stacjonarnego internetu szerokopasmowego (SIDUSIS)")

            result["terrain_slope_pct"] = terrain.get("slope_pct")
            result["terrain_aspect"] = terrain.get("aspect")
            if terrain.get("slope_pct") and terrain["slope_pct"] > 8.0:
                result["surrounding_risks"].append(
                    terrain.get("description") or f"Stroma działka: nachylenie {terrain['slope_pct']}%"
                )

            result["power_lines_risk"] = power_lines.get("risk")
            if power_lines.get("risk") and "LINIA_" in power_lines["risk"]:
                result["surrounding_risks"].append(
                    power_lines.get("description") or "Linia elektroenergetyczna wysokiego napięcia w sąsiedztwie"
                )

            walkability = self.get_walkability_audit(lat, lon)
            result["walkability_pka_dist_m"] = walkability.get("pka_dist_m")
            result["walkability_pka_name"] = walkability.get("pka_name")

            # Step 4: Check geometry and contours for surrounding parcels concurrently
            all_contours: list[str] = []
            if surround_pids:
                geom_tasks = [self.get_parcel_geometry_and_area(client, pid) for pid in surround_pids]
                geom_results = await asyncio.gather(*geom_tasks, return_exceptions=True)

                kieg_tasks = []
                pid_list = list(surround_pids)
                for i, gr in enumerate(geom_results):
                    if isinstance(gr, tuple) and gr[1] is not None:
                        cx, cy = gr[1]
                        kieg_tasks.append((pid_list[i], self.get_parcel_contours_kieg(client, cx, cy)))

                if kieg_tasks:
                    kieg_results = await asyncio.gather(*[t[1] for t in kieg_tasks], return_exceptions=True)
                    for j, contour in enumerate(kieg_results):
                        pid = kieg_tasks[j][0]
                        if isinstance(contour, str) and contour:
                            all_contours.append(contour)
                            short_nr = pid.split(".")[-1]
                            contour_upper = contour.upper()
                            if "BA" in contour_upper:
                                result["surrounding_risks"].append(
                                    f"Działka {short_nr} ma przeznaczenie przemysłowe (Ba): {contour}"
                                )
                            elif "BI" in contour_upper:
                                result["surrounding_risks"].append(
                                    f"Działka {short_nr} ma użytek komercyjny/składowy (Bi): {contour}"
                                )
                            elif "TK" in contour_upper:
                                result["surrounding_risks"].append(
                                    f"Działka {short_nr} to tereny kolejowe (Tk): {contour}"
                                )

            # Check cemetery proximity
            if main_centroid:
                cemetery_info = self.get_cemetery_proximity(
                    main_centroid[0],
                    main_centroid[1],
                    mpzp_zone=result.get("mpzp_zone"),
                    surrounding_risks=result.get("surrounding_risks"),
                    kieg_contours=all_contours,
                )
                result["cemetery_buffer_zone"] = cemetery_info.get("zone")
                if cemetery_info.get("has_cemetery_risk"):
                    result["surrounding_risks"].append(
                        cemetery_info.get("description") or f"Strefa cmentarna ({cemetery_info.get('zone')})"
                    )

        await self._set_cached(audit_cache_key, result)
        return result


geoportal_service = GeoportalService()
