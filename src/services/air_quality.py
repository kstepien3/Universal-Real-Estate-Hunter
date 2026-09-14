import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from loguru import logger

from src.storage import SpatialCacheModel, get_session, safe_commit


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes great-circle distance between two GPS points in kilometres."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def aqi_to_label(aqi: int | float | None) -> str:
    """Maps European AQI numeric value to human-readable Polish label."""
    if aqi is None:
        return "Brak danych"
    val = float(aqi)
    if val <= 20:
        return "Bardzo dobry"
    if val <= 40:
        return "Dobry"
    if val <= 60:
        return "Umiarkowany"
    if val <= 80:
        return "Dostateczny"
    if val <= 100:
        return "Zły"
    return "Bardzo zły"


def aqi_to_color(aqi: int | float | None) -> str:
    """Maps European AQI to hex color band."""
    if aqi is None:
        return "#94a3b8"
    val = float(aqi)
    if val <= 20:
        return "#22c55e"  # Green
    if val <= 40:
        return "#84cc16"  # Lime
    if val <= 60:
        return "#eab308"  # Yellow
    if val <= 80:
        return "#f97316"  # Orange
    if val <= 100:
        return "#ef4444"  # Red
    return "#7f1d1d"  # Dark red / Purple


class AirQualityService:
    """
    Air quality and smog risk service combining:
    1. Open-Meteo Air Quality API (Copernicus CAMS European Ensemble model):
       - Current European AQI, PM2.5, PM10
       - 12-month historical/reanalysis seasonal pattern (heating season vs summer)
       - Exceedance smog days per year
    2. GIOŚ PJP API (Główny Inspektorat Ochrony Środowiska):
       - Nearest official Polish monitoring station
       - Current state air quality index
    """

    OPEN_METEO_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
    GIOS_STATIONS_URL = "https://api.gios.gov.pl/pjp-api/v1/rest/station/findAll"
    GIOS_INDEX_URL = "https://api.gios.gov.pl/pjp-api/v1/rest/aqindex/getIndex/{station_id}"

    # Fallback list of key Polish GIOŚ stations in case the findAll endpoint is down/slow
    FALLBACK_STATIONS = [
        {"id": 10125, "name": "Rzeszów, Al. Piłsudskiego", "lat": 50.0407, "lon": 22.0047, "city": "Rzeszów"},
        {"id": 671, "name": "Rzeszów, Al. Rejtana", "lat": 50.0242, "lon": 22.0106, "city": "Rzeszów"},
        {"id": 17179, "name": "Rzeszów, ul. Starzyńskiego", "lat": 50.0604, "lon": 21.9805, "city": "Rzeszów"},
        {"id": 20202, "name": "Krasne", "lat": 50.0439, "lon": 22.0907, "city": "Krasne"},
        {"id": 20201, "name": "Rzeszów, ul. Kwiatkowskiego", "lat": 49.9984, "lon": 21.9922, "city": "Rzeszów"},
        {"id": 21, "name": "Mielec, ul. Biernackiego", "lat": 50.2972, "lon": 21.4289, "city": "Mielec"},
        {"id": 19, "name": "Krosno, ul. Ks. Popiełuszki", "lat": 49.6887, "lon": 21.7648, "city": "Krosno"},
        {"id": 20, "name": "Jasło, ul. Szkolna", "lat": 49.7456, "lon": 21.4725, "city": "Jasło"},
        {"id": 22, "name": "Przemyśl, ul. Sportowa", "lat": 49.7844, "lon": 22.7678, "city": "Przemyśl"},
        {"id": 23, "name": "Tarnobrzeg, ul. Sienkiewicza", "lat": 50.5731, "lon": 21.6794, "city": "Tarnobrzeg"},
        {"id": 400, "name": "Kraków, al. Krasińskiego", "lat": 50.0577, "lon": 19.9262, "city": "Kraków"},
        {"id": 401, "name": "Kraków, ul. Dietla", "lat": 50.0543, "lon": 19.9436, "city": "Kraków"},
        {"id": 544, "name": "Warszawa, ul. Marszałkowska", "lat": 52.2297, "lon": 21.0122, "city": "Warszawa"},
        {"id": 16, "name": "Wrocław, ul. Wiśniowa", "lat": 51.0863, "lon": 17.0261, "city": "Wrocław"},
        {"id": 17, "name": "Katowice, ul. Kossutha", "lat": 50.2649, "lon": 18.9714, "city": "Katowice"},
        {"id": 18, "name": "Lublin, ul. Obywatelska", "lat": 51.2589, "lon": 22.5636, "city": "Lublin"},
    ]

    MONTH_NAMES_PL = ["Sty", "Lut", "Mar", "Kwi", "Maj", "Cze", "Lip", "Sie", "Wrz", "Paź", "Lis", "Gru"]

    def __init__(self, request_timeout: float = 8.0):
        self.timeout = request_timeout
        self.headers = {"User-Agent": "ApartmentHunter-AirQuality/1.0 (air-quality-audit-suite; contact@local)"}
        self._cached_stations: list[dict[str, Any]] | None = None
        self._stations_fetched_at: float = 0.0

    async def _get_cached(self, key: str) -> dict[str, Any] | None:
        """Retrieves cached spatial data from SQLite SpatialCacheModel."""
        try:
            async with get_session() as session:
                item = await session.get(SpatialCacheModel, key)
                if item:
                    now = datetime.now(UTC)
                    exp = item.expires_at
                    if exp and exp.tzinfo is None:
                        exp = exp.replace(tzinfo=UTC)
                    if exp is None or exp > now:
                        res = json.loads(item.data_json)
                        if isinstance(res, dict):
                            return res
        except Exception as e:
            logger.debug(f"[AirQuality] Cache read error for {key}: {e}")
        return None

    async def _set_cached(self, key: str, value: dict[str, Any], ttl_days: int = 60) -> None:
        """Persists data into SpatialCacheModel."""
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
                await safe_commit(session)
        except Exception as e:
            logger.debug(f"[AirQuality] Cache write error for {key}: {e}")

    async def get_gios_stations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """
        Fetches and caches the list of all Polish air monitoring stations from GIOŚ.
        Uses size=500 pagination parameter and iterates all pages so stations nationwide are loaded.
        Returns a list of parsed dicts: {id, name, lat, lon, city}.
        """
        now = datetime.now(UTC).timestamp()
        if self._cached_stations and (now - self._stations_fetched_at < 86400 * 7):
            return self._cached_stations

        # Check DB cache
        db_cache = await self._get_cached("gios:stations_list_v2")
        if db_cache and isinstance(db_cache.get("stations"), list) and len(db_cache["stations"]) > 50:
            self._cached_stations = db_cache["stations"]
            self._stations_fetched_at = now
            return self._cached_stations

        parsed_stations: list[dict[str, Any]] = []

        def _extract_stations(items: list[Any]) -> None:
            for s in items:
                if not isinstance(s, dict):
                    continue
                try:
                    s_id = s.get("Identyfikator stacji") or s.get("id")
                    s_name = s.get("Nazwa stacji") or s.get("stationName")
                    s_lat = float(s.get("WGS84 \u03c6 N") or s.get("gegrLat") or 0.0)
                    s_lon = float(s.get("WGS84 \u03bb E") or s.get("gegrLon") or 0.0)
                    s_city = s.get("Nazwa miasta") or ""
                    if s_id and s_lat and s_lon:
                        parsed_stations.append(
                            {"id": int(s_id), "name": s_name, "lat": s_lat, "lon": s_lon, "city": s_city}
                        )
                except (ValueError, TypeError):
                    continue

        try:
            resp = await client.get(self.GIOS_STATIONS_URL, params={"size": 500}, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    raw_list = data.get("Lista stacji pomiarowych") or []
                    total_pages = int(data.get("totalPages") or 1)
                elif isinstance(data, list):
                    raw_list = data
                    total_pages = 1
                else:
                    raw_list = []
                    total_pages = 1

                _extract_stations(raw_list)

                if total_pages > 1:
                    for page_idx in range(1, total_pages):
                        try:
                            p_resp = await client.get(
                                self.GIOS_STATIONS_URL,
                                params={"size": 500, "page": page_idx},
                                timeout=self.timeout,
                            )
                            if p_resp.status_code == 200:
                                p_data = p_resp.json()
                                p_list = (
                                    p_data.get("Lista stacji pomiarowych") or []
                                    if isinstance(p_data, dict)
                                    else p_data
                                    if isinstance(p_data, list)
                                    else []
                                )
                                _extract_stations(p_list)
                        except Exception as p_err:
                            logger.debug(f"[AirQuality] Failed to fetch GIOŚ stations page {page_idx}: {p_err}")
        except Exception as e:
            logger.debug(f"[AirQuality] Failed to fetch GIOŚ stations from API: {e}")

        if not parsed_stations:
            parsed_stations = list(self.FALLBACK_STATIONS)

        self._cached_stations = parsed_stations
        self._stations_fetched_at = now
        await self._set_cached("gios:stations_list_v2", {"stations": parsed_stations}, ttl_days=30)
        return parsed_stations

    def find_nearest_station(
        self, lat: float, lon: float, stations: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, float]:
        """Finds the geographically nearest GIOŚ station and returns (station_dict, distance_km)."""
        if not stations:
            return None, 999.0
        best_station: dict[str, Any] | None = None
        min_dist = float("inf")
        for s in stations:
            d = haversine_km(lat, lon, s["lat"], s["lon"])
            if d < min_dist:
                min_dist = d
                best_station = s
        return best_station, round(min_dist, 1)

    async def get_gios_station_index(self, client: httpx.AsyncClient, station_id: int) -> dict[str, Any]:
        """Fetches current GIOŚ index for a given station."""
        cache_key = f"gios:index:{station_id}"
        cached = await self._get_cached(cache_key)
        if cached:
            return cached

        res: dict[str, Any] = {"index_level_name": None, "index_value": None, "calc_date": None}
        try:
            url = self.GIOS_INDEX_URL.format(station_id=station_id)
            resp = await client.get(url, timeout=4.0)
            if resp.status_code == 200:
                data = resp.json()
                aq = data.get("AqIndex") or data.get("aqIndex") or data
                name = (
                    aq.get("Nazwa kategorii indeksu")
                    or (aq.get("stIndexLevel") or {}).get("indexLevelName")
                    or (aq.get("StIndex") or {}).get("IndexLevel", {}).get("IndexLevelName")
                    or aq.get("indexLevelName")
                )
                val = aq.get("Wartość indeksu") or aq.get("indexValue")
                dt = aq.get("Data wykonania obliczeń indeksu") or aq.get("calcDate")
                res["index_level_name"] = name
                res["index_value"] = val
                res["calc_date"] = dt
                # Cache current index for 30 minutes
                await self._set_cached(cache_key, res, ttl_days=1)
        except Exception as e:
            logger.debug(f"[AirQuality] Failed to fetch GIOŚ index for station {station_id}: {e}")

        return res

    def _compute_seasonal_metrics(
        self, hourly: dict[str, list[Any]]
    ) -> tuple[float | None, float | None, int, list[dict[str, Any]]]:
        """
        Processes 8760 hourly readings (past 365 days) into:
        - Heating season PM2.5 avg (Oct–Mar)
        - Summer PM2.5 avg (Apr–Sep)
        - Count of smog exceedance days (>25 µg/m³ daily mean)
        - 12 monthly averages list
        """
        times = hourly.get("time") or []
        pm25_vals = hourly.get("pm2_5") or []
        pm10_vals = hourly.get("pm10") or []
        aqi_vals = hourly.get("european_aqi") or []

        month_pm25: dict[int, list[float]] = {m: [] for m in range(1, 13)}
        month_pm10: dict[int, list[float]] = {m: [] for m in range(1, 13)}
        month_aqi: dict[int, list[float]] = {m: [] for m in range(1, 13)}
        daily_pm25: dict[str, list[float]] = {}

        for i, t in enumerate(times):
            if not isinstance(t, str) or len(t) < 10:
                continue
            try:
                m = int(t[5:7])
                day_str = t[:10]
            except ValueError:
                continue

            p25 = pm25_vals[i] if i < len(pm25_vals) else None
            p10 = pm10_vals[i] if i < len(pm10_vals) else None
            aq = aqi_vals[i] if i < len(aqi_vals) else None

            if p25 is not None:
                month_pm25[m].append(float(p25))
                daily_pm25.setdefault(day_str, []).append(float(p25))
            if p10 is not None:
                month_pm10[m].append(float(p10))
            if aq is not None:
                month_aqi[m].append(float(aq))

        # Count smog exceedance days (daily mean PM2.5 > 25 µg/m³)
        smog_days_total = 0
        month_smog_days: dict[int, int] = dict.fromkeys(range(1, 13), 0)
        for day_str, vals in daily_pm25.items():
            if vals:
                d_avg = sum(vals) / len(vals)
                if d_avg > 25.0:
                    smog_days_total += 1
                    try:
                        m_int = int(day_str[5:7])
                        month_smog_days[m_int] += 1
                    except ValueError:
                        pass

        # Compute 12 monthly averages
        monthly_list: list[dict[str, Any]] = []
        for m in range(1, 13):
            p25_list = month_pm25[m]
            p10_list = month_pm10[m]
            aq_list = month_aqi[m]
            p25_avg = round(sum(p25_list) / len(p25_list), 1) if p25_list else 0.0
            p10_avg = round(sum(p10_list) / len(p10_list), 1) if p10_list else 0.0
            aq_avg = round(sum(aq_list) / len(aq_list), 1) if aq_list else 0.0

            monthly_list.append(
                {
                    "month": m,
                    "month_name": self.MONTH_NAMES_PL[m - 1],
                    "pm2_5": p25_avg,
                    "pm10": p10_avg,
                    "aqi": aq_avg,
                    "smog_days": month_smog_days[m],
                    "is_heating_season": m in (1, 2, 3, 10, 11, 12),
                }
            )

        # Heating season avg: Oct, Nov, Dec, Jan, Feb, Mar
        heating_vals = [monthly_list[m - 1]["pm2_5"] for m in (1, 2, 3, 10, 11, 12) if monthly_list[m - 1]["pm2_5"] > 0]
        heating_avg = round(sum(heating_vals) / len(heating_vals), 1) if heating_vals else None

        # Summer avg: Apr, May, Jun, Jul, Aug, Sep
        summer_vals = [monthly_list[m - 1]["pm2_5"] for m in (4, 5, 6, 7, 8, 9) if monthly_list[m - 1]["pm2_5"] > 0]
        summer_avg = round(sum(summer_vals) / len(summer_vals), 1) if summer_vals else None

        return heating_avg, summer_avg, smog_days_total, monthly_list

    async def get_air_quality_audit(self, lat: float, lon: float, force_refresh: bool = False) -> dict[str, Any]:
        """
        Executes a full air quality audit for given coordinates.
        Utilizes cache (SpatialCacheModel) rounded to 2 decimal places (~1.1 km).
        """
        cache_key = f"air_quality:{round(lat, 2)}:{round(lon, 2)}"
        if not force_refresh:
            cached = await self._get_cached(cache_key)
            if cached and "air_aqi" in cached:
                cached_dist = cached.get("air_gios_dist_km")
                # Do not use stale cache from previous pagination bug where stations > 60km away were picked
                if cached_dist is None or cached_dist <= 60:
                    return cached

        # Default fallback structure
        audit_result: dict[str, Any] = {
            "air_aqi": None,
            "air_aqi_label": None,
            "air_aqi_color": None,
            "air_pm25_heating_avg": None,
            "air_pm25_summer_avg": None,
            "air_smog_days": None,
            "air_smog_risk": "NIEZNANE",
            "air_gios_station": None,
            "air_gios_dist_km": None,
            "air_gios_index": None,
            "monthly_averages": [],
            "attribution": "Open-Meteo (CAMS Copernicus) + GIOŚ Państwowy Monitoring Środowiska",
        }

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            # 1. GIOŚ nearest station lookup
            stations = await self.get_gios_stations(client)
            nearest_st, dist_km = self.find_nearest_station(lat, lon, stations)
            if nearest_st:
                audit_result["air_gios_station"] = nearest_st["name"]
                audit_result["air_gios_dist_km"] = dist_km
                gios_idx = await self.get_gios_station_index(client, nearest_st["id"])
                audit_result["air_gios_index"] = gios_idx.get("index_level_name")

            # 2. Open-Meteo CAMS Air Quality API
            # Query last 365 days for seasonal calculation + current readings
            now_utc = datetime.now(UTC)
            end_date = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
            start_date = (now_utc - timedelta(days=365)).strftime("%Y-%m-%d")

            params: dict[str, Any] = {
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "current": "european_aqi,pm10,pm2_5",
                "hourly": "pm2_5,pm10,european_aqi",
                "start_date": start_date,
                "end_date": end_date,
                "timezone": "Europe/Warsaw",
            }

            try:
                resp = await client.get(self.OPEN_METEO_AQ_URL, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    current = data.get("current") or {}
                    hourly = data.get("hourly") or {}

                    aqi_val = current.get("european_aqi")
                    if aqi_val is not None:
                        audit_result["air_aqi"] = int(round(aqi_val))
                        audit_result["air_aqi_label"] = aqi_to_label(aqi_val)
                        audit_result["air_aqi_color"] = aqi_to_color(aqi_val)

                    heating_avg, summer_avg, smog_days, monthly = self._compute_seasonal_metrics(hourly)
                    audit_result["air_pm25_heating_avg"] = heating_avg
                    audit_result["air_pm25_summer_avg"] = summer_avg
                    audit_result["air_smog_days"] = smog_days
                    audit_result["monthly_averages"] = monthly

                    # Risk assessment
                    if (heating_avg and heating_avg >= 35.0) or smog_days >= 35:
                        audit_result["air_smog_risk"] = "WYSOKIE"
                    elif (heating_avg and heating_avg >= 25.0) or smog_days >= 20:
                        audit_result["air_smog_risk"] = "PODWYŻSZONE"
                    elif heating_avg and heating_avg >= 15.0:
                        audit_result["air_smog_risk"] = "UMIARKOWANE"
                    elif heating_avg:
                        audit_result["air_smog_risk"] = "NISKIE"
            except Exception as e:
                logger.warning(f"[AirQuality] Open-Meteo request error for ({lat}, {lon}): {e}")

        # Persist in cache for 60 days
        if audit_result.get("air_aqi") is not None or audit_result.get("air_pm25_heating_avg") is not None:
            await self._set_cached(cache_key, audit_result, ttl_days=60)

        return audit_result


air_quality_service = AirQualityService()
