import time
from typing import Any

import httpx
from loguru import logger

from src.services.config_manager import CITY_CENTROIDS, slugify_city
from src.services.market_analyzer import PKA_STATIONS, haversine_km
from src.services.spatial_cache import get_spatial_cache, set_spatial_cache

# Circuit breaker for the Overpass dependency: after this many consecutive
# total failures, pedestrian audits short-circuit to the default instead of
# burning ~16 s per listing on dead mirrors. Observed cost without it: the
# whole processing stage (2088 s for ~130 listings) was Overpass timeouts.
_OVERPASS_BREAKER_THRESHOLD = 3
_OVERPASS_BREAKER_COOLDOWN_S = 900.0


class CommuteService:
    """
    Commute Routing and Pedestrian Safety Intelligence:
    1. OSRM Road Routing (driving distance & duration to city center and transit hubs).
    2. OpenStreetMap Overpass Pedestrian Audit (sidewalks, street lighting, road surface quality).
    """

    OSRM_BASE_URL = "https://router.project-osrm.org/route/v1/driving"
    # Public Overpass instances in probe order. The canonical instance throttles
    # some hosting networks (ConnectTimeout/504 while residential IPs answer in
    # <1 s), so a throttled primary falls through to mirrors. nchc was dropped:
    # its domain no longer resolves (verified 2026-09-17).
    OVERPASS_API_URLS = (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    )

    def __init__(self, request_timeout: float = 5.0):
        self.timeout = request_timeout
        self.headers = {"User-Agent": "ApartmentHunter-CommuteService/1.0 (spatial-audit; contact@local)"}
        self._overpass_failures = 0
        self._overpass_cooldown_until = 0.0

    async def get_osrm_route(
        self,
        client: httpx.AsyncClient | None,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
    ) -> tuple[float, int]:
        """
        Queries OSRM API for road distance (km) and driving time (minutes).
        Falls back to haversine circuity approximation if network fails.
        """
        cache_key = f"osrm:{origin_lat:.4f},{origin_lon:.4f}->{dest_lat:.4f},{dest_lon:.4f}"
        cached = await get_spatial_cache(cache_key)
        if cached and isinstance(cached, list) and len(cached) == 2:
            return float(cached[0]), int(cached[1])

        url = f"{self.OSRM_BASE_URL}/{origin_lon:.6f},{origin_lat:.6f};{dest_lon:.6f},{dest_lat:.6f}?overview=false"
        try:
            if client is not None:
                resp = await client.get(url, headers=self.headers, timeout=self.timeout)
            else:
                async with httpx.AsyncClient() as new_client:
                    resp = await new_client.get(url, headers=self.headers, timeout=self.timeout)

            if resp.status_code == 200:
                data = resp.json()
                routes = data.get("routes", [])
                if routes:
                    primary = routes[0]
                    dist_meters = float(primary.get("distance", 0.0))
                    dur_seconds = float(primary.get("duration", 0.0))
                    dist_km = round(dist_meters / 1000.0, 1)
                    dur_min = max(1, round(dur_seconds / 60.0))
                    await set_spatial_cache(cache_key, [dist_km, dur_min], ttl_days=30)
                    return dist_km, dur_min
        except Exception as e:
            logger.debug(f"[Commute] OSRM query note: {type(e).__name__}: {e}")

        # Fallback: road distance ~ straight line * 1.35 circuity
        straight_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
        road_km = round(straight_km * 1.35, 1)
        # Assume 40 km/h average suburban traffic + 3 min fixed lights/intersection delay
        calc_min = max(3, round((road_km / 40.0) * 60.0 + 3))
        return road_km, calc_min

    async def get_pedestrian_safety_audit(
        self,
        client: httpx.AsyncClient | None,
        lat: float,
        lon: float,
        radius_m: int = 150,
    ) -> dict[str, Any]:
        """
        Audits pedestrian infrastructure around coordinates via OpenStreetMap:
        sidewalk presence, street lighting (lit), and road surface condition.
        """
        cache_key = f"pedestrian:osm:{lat:.4f},{lon:.4f}:{radius_m}"
        cached = await get_spatial_cache(cache_key)
        if cached and isinstance(cached, dict):
            return cached

        default_res: dict[str, Any] = {
            "pedestrian_sidewalk": None,
            "pedestrian_lit": None,
            "pedestrian_surface": None,
            "pedestrian_safety_note": "Brak precyzyjnych danych o chodniku i oświetleniu w OSM.",
        }

        if time.monotonic() < self._overpass_cooldown_until:
            logger.debug("[Commute] Overpass circuit breaker open — pedestrian audit skipped.")
            return default_res

        overpass_ql = f"""[out:json][timeout:5];
(
  way["highway"](around:{radius_m},{lat},{lon});
);
out tags 20;"""

        # Per-try timeout clears the server-side [timeout:5] with margin.
        overpass_timeout = 8.0
        try:
            poster = client
            owned_client: httpx.AsyncClient | None = None
            if poster is None:
                owned_client = httpx.AsyncClient()
                poster = owned_client
            try:
                resp = None
                for overpass_url in self.OVERPASS_API_URLS:
                    try:
                        resp = await poster.post(
                            overpass_url,
                            data={"data": overpass_ql},
                            headers=self.headers,
                            timeout=overpass_timeout,
                        )
                    except Exception as mirror_error:
                        logger.debug(
                            f"[Commute] Overpass mirror unreachable: {overpass_url} "
                            f"({type(mirror_error).__name__}: {mirror_error})"
                        )
                        continue
                    if resp.status_code == 200:
                        break
                if resp is None or resp.status_code != 200:
                    self._overpass_failures += 1
                    if self._overpass_failures >= _OVERPASS_BREAKER_THRESHOLD:
                        self._overpass_cooldown_until = time.monotonic() + _OVERPASS_BREAKER_COOLDOWN_S
                    raise RuntimeError("Overpass: no mirror returned HTTP 200")
            finally:
                if owned_client is not None:
                    await owned_client.aclose()
            if resp.status_code == 200:
                data = resp.json()
                elements = data.get("elements", [])
                has_sidewalk = False
                is_lit = False
                surfaces: list[str] = []

                for el in elements:
                    tags = el.get("tags", {})
                    hw = tags.get("highway")
                    if hw in ("footway", "path", "pedestrian", "steps"):
                        has_sidewalk = True
                    sw = tags.get("sidewalk")
                    if sw in ("yes", "both", "left", "right", "separate"):
                        has_sidewalk = True
                    lit = tags.get("lit")
                    if lit == "yes":
                        is_lit = True
                    surf = tags.get("surface")
                    if surf:
                        surfaces.append(surf)

                primary_surface = surfaces[0] if surfaces else None

                notes = []
                if has_sidewalk:
                    notes.append("chodnik dla pieszych")
                else:
                    notes.append("brak wydzielonego chodnika (ruch pieszy poboczem/jezdnią)")

                if is_lit:
                    notes.append("oświetlenie uliczne obecne")
                else:
                    notes.append("brak potwierdzonego oświetlenia drogi")

                if primary_surface:
                    notes.append(f"nawierzchnia: {primary_surface}")

                safety_note = f"Dostęp pieszy w promieniu {radius_m}m: {', '.join(notes)}."

                res = {
                    "pedestrian_sidewalk": has_sidewalk,
                    "pedestrian_lit": is_lit,
                    "pedestrian_surface": primary_surface,
                    "pedestrian_safety_note": safety_note,
                }
                await set_spatial_cache(cache_key, res, ttl_days=30)
                self._overpass_failures = 0
                return res
        except Exception as e:
            logger.debug(f"[Commute] Pedestrian safety Overpass note: {type(e).__name__}: {e}")
            # Negative cache: a failing endpoint must not be hammered per listing per cycle.
            await set_spatial_cache(cache_key, default_res, ttl_days=1)

        return default_res

    async def audit_commute_and_pedestrian(
        self,
        client: httpx.AsyncClient | None = None,
        lat: float = 0.0,
        lon: float = 0.0,
        city: str | None = None,
    ) -> dict[str, Any]:
        """
        Consolidated audit calculating driving commute to city center, nearest train station,
        and pedestrian safety status.
        """
        # Resolve city center centroid
        city_slug = slugify_city(city or "Rzeszów")
        center_coords = CITY_CENTROIDS.get(city_slug) or CITY_CENTROIDS.get("rzeszow", (50.0375, 22.0047))

        # 1. Drive to center
        drive_km, drive_min = await self.get_osrm_route(client, lat, lon, center_coords[0], center_coords[1])

        # 2. Drive to nearest train station
        station_min: int | None = None
        if PKA_STATIONS:
            # find closest station by straight line, then route to it
            sorted_stations = sorted(
                PKA_STATIONS,
                key=lambda s: haversine_km(lat, lon, s[1], s[2]),
            )
            nearest_st = sorted_stations[0]
            _, station_min = await self.get_osrm_route(client, lat, lon, nearest_st[1], nearest_st[2])

        # 3. Pedestrian safety
        pedestrian = await self.get_pedestrian_safety_audit(client, lat, lon)

        return {
            "commute_drive_min": drive_min,
            "commute_drive_km": drive_km,
            "commute_station_min": station_min,
            "pedestrian_sidewalk": pedestrian.get("pedestrian_sidewalk"),
            "pedestrian_lit": pedestrian.get("pedestrian_lit"),
            "pedestrian_surface": pedestrian.get("pedestrian_surface"),
            "pedestrian_safety_note": pedestrian.get("pedestrian_safety_note"),
        }


commute_service = CommuteService()
