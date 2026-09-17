import json
import re
from typing import Any

import httpx
from loguru import logger

from src.services.spatial_cache import get_spatial_cache, set_spatial_cache

NOXIOUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bmagazyn", re.IGNORECASE), "Hala magazynowa / logistyka"),
    (re.compile(r"\bhal[aeyęąi]\b|\bhale\b|\bhali\b", re.IGNORECASE), "Hala przemysłowa / produkcyjna"),
    (re.compile(r"\bprodukcyjn", re.IGNORECASE), "Obiekt produkcyjny / przemysłowy"),
    (re.compile(r"\bprzemysłow", re.IGNORECASE), "Inwestycja przemysłowa"),
    (re.compile(r"\bwarsztat", re.IGNORECASE), "Warsztat samochodowy / naprawczy"),
    (re.compile(r"\bstacj[aieęy]\s+paliw", re.IGNORECASE), "Stacja paliw / stacja benzynowa"),
    (re.compile(r"\bstacj[aieęy]\s+bazow", re.IGNORECASE), "Stacja bazowa telefonii komórkowej"),
    (re.compile(r"\bmaszt", re.IGNORECASE), "Maszt telekomunikacyjny"),
    (re.compile(r"\bwież[aeyęąi]", re.IGNORECASE), "Wieża antenowa / nadajnik"),
    (re.compile(r"\bkurnik", re.IGNORECASE), "Ferma drobiu / kurnik przemysłowy"),
    (re.compile(r"\bchlewni", re.IGNORECASE), "Chlewnia / tuczarnia"),
    (re.compile(r"\bferm[aeyęąi]", re.IGNORECASE), "Ferma zwierzęca"),
    (re.compile(r"\bubojni", re.IGNORECASE), "Ubojnia / przetwórstwo mięsne"),
    (re.compile(r"\bbiogazowni", re.IGNORECASE), "Biogazownia"),
    (re.compile(r"\bodpad", re.IGNORECASE), "Składowisko / przetwarzanie odpadów"),
    (re.compile(r"\bmyjni", re.IGNORECASE), "Myjnia samochodowa"),
    (re.compile(r"\blakierni", re.IGNORECASE), "Lakiernia / stacja obsługi"),
    (re.compile(r"\bwielorodzinn", re.IGNORECASE), "Zabudowa wielorodzinna / blok mieszkalny"),
    (re.compile(r"\bblok(?:i|u|ów|em|ach)?\b", re.IGNORECASE), "Blok mieszkalny wielorodzinny"),
]


class GunbService:
    """
    Integration with GUNB (Główny Urząd Nadzoru Budowlanego) and RWDZ (Rejestr Wniosków,
    Decyzji i Zgłoszeń) for building permit intelligence on target and neighboring plots.
    """

    RWDZ_WMS_URL = "https://mapy.geoportal.gov.pl/wss/ext/GlownyUrzadNadzoruBudowlanego/RWDZ-WMS"
    GUNB_BASE_URL = "https://wyszukiwarka.gunb.gov.pl/"

    def __init__(self, request_timeout: float = 6.0):
        self.timeout = request_timeout
        self.headers = {"User-Agent": "ApartmentHunter-GUNB/1.0 (building-permit-auditor; contact@local)"}

    def generate_gunb_url(self, parcel_id: str | None = None) -> str:
        """Generates deep link to official GUNB public register."""
        if not parcel_id:
            return self.GUNB_BASE_URL
        clean_id = parcel_id.strip()
        return f"{self.GUNB_BASE_URL}?dzialka={clean_id}"

    def parse_rwdz_payload(self, text: str) -> list[dict[str, Any]]:
        """
        Parses XML, HTML or JSON responses from RWDZ WMS GetFeatureInfo.
        Extracts permit application details: decision number, project name, date, category.
        """
        results: list[dict[str, Any]] = []
        if not text:
            return results

        # 1. Check if JSON / GeoJSON
        try:
            parsed = json.loads(text)
            features = parsed.get("features", [])
            for f in features:
                props = f.get("properties", {})
                if props:
                    results.append(
                        {
                            "numer_decyzji": str(
                                props.get("nr_decyzji")
                                or props.get("numer")
                                or props.get("nr_sprawy")
                                or props.get("id")
                                or ""
                            ).strip(),
                            "nazwa_zamierzenia": str(
                                props.get("nazwa_zamierzenia") or props.get("zamierzenie") or props.get("opis") or ""
                            ).strip(),
                            "rodzaj_obiektu": str(
                                props.get("rodzaj_obiektu")
                                or props.get("kategoria")
                                or props.get("rodzaj_zamierzenia")
                                or ""
                            ).strip(),
                            "data_decyzji": str(
                                props.get("data_decyzji") or props.get("data_wplywu") or props.get("data") or ""
                            ).strip(),
                            "status": str(props.get("status") or props.get("stan_sprawy") or "WYDANA").strip(),
                        }
                    )
            if results:
                return results
        except (ValueError, TypeError):
            pass

        # 2. Parse XML / HTML table rowsets
        # Look for <ROW> or <tr> blocks
        row_matches = re.findall(r"(?:<ROW>|<tr>)(.*?)(?:</ROW>|</tr>)", text, re.DOTALL | re.IGNORECASE)
        for row in row_matches:
            # Extract tags / cells
            def _extract_tag(patterns: list[str], _row: str = row) -> str:
                for pat in patterns:
                    m = re.search(pat, _row, re.IGNORECASE | re.DOTALL)
                    if m:
                        val = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                        if val:
                            return val
                return ""

            nr = _extract_tag(
                [
                    r"<(?:NR_DECYZJI|NUMER_DECYZJI|NR_SPRAWY|NUMER)>\s*([^<]+?)\s*</",
                    r"<td>\s*Nr(?:\s+decyzji)?\s*:\s*</td>\s*<td>\s*([^<]+?)\s*</td>",
                    r"<td>([^<]+?/\d{4})</td>",
                ]
            )
            zamierzenie = _extract_tag(
                [
                    r"<(?:NAZWA_ZAMIERZENIA|ZAMIERZENIE|OPIS)>\s*([^<]+?)\s*</",
                    r"<td>\s*Zamierzenie\s*:\s*</td>\s*<td>\s*([^<]+?)\s*</td>",
                ]
            )
            rodzaj = _extract_tag(
                [
                    r"<(?:RODZAJ_OBIEKTU|KATEGORIA|RODZAJ)>\s*([^<]+?)\s*</",
                    r"<td>\s*Rodzaj(?:\s+obiektu)?\s*:\s*</td>\s*<td>\s*([^<]+?)\s*</td>",
                ]
            )
            data_dec = _extract_tag(
                [
                    r"<(?:DATA_DECYZJI|DATA_WPLYWU|DATA)>\s*([^<]+?)\s*</",
                    r"<td>\s*Data(?:\s+decyzji)?\s*:\s*</td>\s*<td>\s*([^<]+?)\s*</td>",
                ]
            )
            status = (
                _extract_tag(
                    [
                        r"<(?:STATUS|STAN_SPRAWY|DECYZJA)>\s*([^<]+?)\s*</",
                        r"<td>\s*Status\s*:\s*</td>\s*<td>\s*([^<]+?)\s*</td>",
                    ]
                )
                or "WYDANA"
            )

            if nr or zamierzenie:
                results.append(
                    {
                        "numer_decyzji": nr or "Brak nru",
                        "nazwa_zamierzenia": zamierzenie or "Zamierzenie budowlane",
                        "rodzaj_obiektu": rodzaj or "Budynek",
                        "data_decyzji": data_dec,
                        "status": status,
                    }
                )

        # 3. Fallback regex for direct string matches if rowset tags weren't standard
        if not results:
            zam_matches = re.findall(
                r"(?:zamierzenie|nazwa_zamierzenia|inwestycja)[\s:=]+([^\n\r<]{10,250})",
                text,
                re.IGNORECASE,
            )
            for z in zam_matches:
                clean_z = z.strip()
                if len(clean_z) > 8:
                    results.append(
                        {
                            "numer_decyzji": "Wpis rejestru",
                            "nazwa_zamierzenia": clean_z,
                            "rodzaj_obiektu": "Budowlany",
                            "data_decyzji": "",
                            "status": "ZAREJESTROWANY",
                        }
                    )

        return results

    def evaluate_neighborhood_risks(
        self,
        permits: list[dict[str, Any]],
        parcel_id: str | None = None,
    ) -> list[str]:
        """
        Audits permits in immediate area for potentially harmful or nuisance developments
        (industrial, warehouse, poultry/livestock farms, tall multi-family blocks, towers).
        """
        flags: list[str] = []
        for p in permits:
            zamierzenie = p.get("nazwa_zamierzenia", "").lower()
            rodzaj = p.get("rodzaj_obiektu", "").lower()
            combined = f"{zamierzenie} {rodzaj}"

            for pat, label in NOXIOUS_PATTERNS:
                if pat.search(combined):
                    nr = p.get("numer_decyzji")
                    nr_str = f" ({nr})" if nr and nr != "Brak nru" else ""
                    dt = p.get("data_decyzji")
                    dt_str = f" z dn. {dt}" if dt else ""
                    flags.append(f"⚠️ Pozwolenie w rejonie: {label} — „{p.get('nazwa_zamierzenia')}”{nr_str}{dt_str}")
                    break

        return flags

    async def audit_gunb_permits(
        self,
        client: httpx.AsyncClient | None = None,
        cx: float | None = None,
        cy: float | None = None,
        parcel_id: str | None = None,
        radius_m: float = 200.0,
    ) -> dict[str, Any]:
        """
        Queries RWDZ-WMS around centroid (EPSG:2180) and evaluates permit safety.
        Returns parsed permits list, risk flags, and deep link URL.
        """
        gunb_url = self.generate_gunb_url(parcel_id)
        default_res: dict[str, Any] = {
            "gunb_permits": [],
            "gunb_risk_flags": [],
            "gunb_url": gunb_url,
            "gunb_status": "BRAK_DANYCH",
        }

        if cx is None or cy is None:
            return default_res

        cache_key = f"gunb:rwdz:{round(cx, 1)},{round(cy, 1)}:{int(radius_m)}"
        cached = await get_spatial_cache(cache_key)
        if cached and isinstance(cached, dict):
            return cached

        # Construct WMS GetFeatureInfo query in EPSG:2180
        bbox_str = f"{cy - radius_m:.1f},{cx - radius_m:.1f},{cy + radius_m:.1f},{cx + radius_m:.1f}"
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetFeatureInfo",
            "BBOX": bbox_str,
            "CRS": "EPSG:2180",
            "WIDTH": "100",
            "HEIGHT": "100",
            "LAYERS": "Decyzje,Wnioski,Zgloszenia",
            "QUERY_LAYERS": "Decyzje,Wnioski,Zgloszenia",
            "I": "50",
            "J": "50",
            "INFO_FORMAT": "text/html",
        }

        try:
            if client is not None:
                resp = await client.get(
                    self.RWDZ_WMS_URL,
                    params=params,
                    headers=self.headers,
                    timeout=self.timeout,
                    follow_redirects=True,
                )
            else:
                async with httpx.AsyncClient() as new_client:
                    resp = await new_client.get(
                        self.RWDZ_WMS_URL,
                        params=params,
                        headers=self.headers,
                        timeout=self.timeout,
                        follow_redirects=True,
                    )
            if resp.status_code == 200 and resp.text:
                permits = self.parse_rwdz_payload(resp.text)
                risk_flags = self.evaluate_neighborhood_risks(permits, parcel_id=parcel_id)

                status = "BEZPIECZNE"
                if risk_flags:
                    status = "RYZYKO_W_SĄSIEDZTWIE"
                elif permits:
                    status = "POZWOLENIA_STANDARDOWE"

                result = {
                    "gunb_permits": permits,
                    "gunb_risk_flags": risk_flags,
                    "gunb_url": gunb_url,
                    "gunb_status": status,
                }
                await set_spatial_cache(cache_key, result, ttl_days=30)
                return result
        except Exception as e:
            logger.debug(f"[GUNB] RWDZ query note for ({cx}, {cy}): {e}")

        return default_res


gunb_service = GunbService()
