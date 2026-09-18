from typing import Any

import httpx
from loguru import logger

from src.services.config_manager import CITY_CENTROIDS, slugify_city
from src.services.market_analyzer import PKA_STATIONS, haversine_km
from src.services.spatial_cache import get_spatial_cache, set_spatial_cache


class CommuteService:
    """
    Commute Routing Intelligence:
    OSRM Road Routing (driving distance & duration to city center and transit hubs).
    """

    OSRM_BASE_URL = "https://router.project-osrm.org/route/v1/driving"

    def __init__(self, request_timeout: float = 5.0):
        self.timeout = request_timeout
        self.headers = {"User-Agent": "ApartmentHunter-CommuteService/1.0 (spatial-audit; contact@local)"}

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
        """Pedestrian safety audit via external Overpass is disabled for stability and latency."""
        return {
            "pedestrian_sidewalk": None,
            "pedestrian_lit": None,
            "pedestrian_surface": None,
            "pedestrian_safety_note": None,
        }

    async def get_custom_commutes(
        self,
        client: httpx.AsyncClient | None,
        lat: float,
        lon: float,
        destinations: list[dict[str, Any]],
    ) -> dict[str, dict[str, float]]:
        """Computes driving distance & time from listing coords to each user-defined anchor.

        Returns a mapping ``{label: {"min": float, "km": float}}`` for valid destinations.
        """
        result: dict[str, dict[str, float]] = {}
        for dest in destinations or []:
            if not isinstance(dest, dict):
                continue
            label = str(dest.get("label", "")).strip()
            try:
                d_lat = float(dest.get("latitude", 0.0))
                d_lon = float(dest.get("longitude", 0.0))
            except (TypeError, ValueError):
                continue
            if not label or not (-90 <= d_lat <= 90) or not (-180 <= d_lon <= 180):
                continue
            try:
                km, minutes = await self.get_osrm_route(client, lat, lon, d_lat, d_lon)
                result[label] = {"min": float(minutes), "km": float(km)}
            except Exception as e:
                logger.debug(f"[Commute] Custom destination '{label}' route note: {e}")
        return result

    async def audit_commute_and_pedestrian(
        self,
        client: httpx.AsyncClient | None = None,
        lat: float = 0.0,
        lon: float = 0.0,
        city: str | None = None,
        custom_destinations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Consolidated audit calculating driving commute to city center, nearest train station,
        and any user-defined commute anchors.
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

        # 3. Drive to user-defined commute anchors
        custom = await self.get_custom_commutes(client, lat, lon, custom_destinations or [])

        return {
            "commute_drive_min": drive_min,
            "commute_drive_km": drive_km,
            "commute_station_min": station_min,
            "commute_custom": custom,
            "pedestrian_sidewalk": None,
            "pedestrian_lit": None,
            "pedestrian_surface": None,
            "pedestrian_safety_note": None,
        }


commute_service = CommuteService()
