import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
from loguru import logger

from config import settings
from src.models.enums import QualificationStatus
from src.models.listing import FilterResult, ListingSchema
from src.services.market_analyzer import NegotiationAdvice


class DiscordNotifier:
    """
    Discord Webhook Notifier:
    Sends aesthetically structured rich Embeds for qualified house listings.
    Supports color coding based on whitelist priority and robust error handling.
    """

    COLOR_WHITELIST = 0x2ECC71  # Vibrant Green
    COLOR_QUALIFIED = 0x3498DB  # Nice Blue
    COLOR_REVIEW = 0xF39C12  # Gold/Orange
    COLOR_DEFAULT = 0x95A5A6  # Gray

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or settings.DISCORD_WEBHOOK_URL

    def _get_color_for_status(self, status: QualificationStatus) -> int:
        if status == QualificationStatus.QUALIFIED_WHITELIST:
            return self.COLOR_WHITELIST
        if status == QualificationStatus.QUALIFIED:
            return self.COLOR_QUALIFIED
        if status == QualificationStatus.NEEDS_REVIEW:
            return self.COLOR_REVIEW
        return self.COLOR_DEFAULT

    def format_embed(
        self,
        listing: ListingSchema,
        filter_result: FilterResult,
        negotiation_advice: NegotiationAdvice | None = None,
    ) -> dict[str, Any]:
        """Format listing and filtration details into Discord Embed JSON."""
        color = self._get_color_for_status(filter_result.status)

        # Status badge
        if filter_result.status == QualificationStatus.QUALIFIED_WHITELIST:
            status_header = f"⭐ WHITELIST PRIORYTET ({filter_result.matched_whitelist_area or 'Rzeszów'})"
        elif filter_result.status == QualificationStatus.QUALIFIED:
            status_header = "✅ OFERTA SPEŁNIAJĄCA KRYTERIA"
        else:
            status_header = "🔍 WYMAGA WERYFIKACJI RĘCZNEJ"

        # Price formatting
        price_fmt = f"**{listing.price:,.0f} zł**".replace(",", " ")
        price_m2_fmt = f"{listing.price_per_m2:,.0f} zł/m²".replace(",", " ") if listing.price_per_m2 > 0 else "b/d"

        # Plot formatting
        plot_fmt = f"{listing.area_plot:.0f} m²" if listing.area_plot else "b/d w nagłówku (analiza opisu)"

        # Building & segment
        b_type = listing.building_type.value.capitalize()
        s_type = listing.segment_subtype.value
        building_info = f"{b_type} ({s_type})"

        # Location
        loc_parts = []
        if listing.street:
            loc_parts.append(f"ul. {listing.street}")
        if listing.district:
            loc_parts.append(listing.district)
        if listing.city:
            loc_parts.append(listing.city)
        location_display = ", ".join(loc_parts) or listing.location_raw or "Rzeszów i okolice"

        # Road and parking
        road_parking = f"Droga: **{listing.access_road_type.value}**"
        if filter_result.has_parking_or_garage:
            road_parking += " | 🚗 Parking/Garaż: **TAK**"
        else:
            road_parking += " | 🚗 Parking: niejednoznaczny"

        # Market, Finish Condition and Visualisations
        market_val = listing.market.value if hasattr(listing.market, "value") else str(listing.market)
        market_str = market_val.capitalize()
        finish_val = (
            listing.finish_condition.value
            if hasattr(listing.finish_condition, "value")
            else str(listing.finish_condition)
        )
        finish_str = finish_val.capitalize()
        vis_badge = " | ⚠️ **Wizualizacje 3D / brak zdjęć**" if listing.has_visualisations else ""
        standard_info = f"Stan: **{finish_str}** | Rynek: **{market_str}**{vis_badge}"

        # Media & Utilities
        sew_val = listing.sewerage.value if hasattr(listing.sewerage, "value") else str(listing.sewerage)
        heat_val = listing.heating.value if hasattr(listing.heating, "value") else str(listing.heating)
        fiber_str = "TAK" if listing.has_fiber else "b/d"
        media_info = f"Ścieki: **{sew_val}** | Ogrzewanie: **{heat_val}** | Światłowód: **{fiber_str}**"

        # Category-aware metrics line
        cat_val = (
            listing.category.value if hasattr(listing.category, "value") else str(getattr(listing, "category", "dom"))
        )
        if cat_val == "mieszkanie":
            area_line = f"Mieszkanie: **{listing.area_home:.1f} m²**"
            if listing.rooms:
                area_line += f" | Pokoje: **{listing.rooms}**"
            if listing.floor is not None:
                area_line += f" | Piętro: **{listing.floor}**"
        elif cat_val == "dzialka":
            effective_plot = listing.area_plot or listing.area_home
            area_line = f"Działka: **{effective_plot:.0f} m²**"
        else:
            area_line = f"Dom: **{listing.area_home:.1f} m²** | Działka: **{plot_fmt}**"

        title_display = f"[{listing.profile_name}] {listing.title}" if listing.profile_name else listing.title

        fields = [
            {
                "name": "💰 Cena & Metraż",
                "value": f"{price_fmt} ({price_m2_fmt})\n{area_line}",
                "inline": False,
            },
            {
                "name": "🏡 Typ budynku & Lokalizacja",
                "value": f"Typ: **{building_info}**\n📍 **{location_display}**",
                "inline": False,
            },
            {
                "name": "🔨 Standard wykończenia i Rynek",
                "value": standard_info,
                "inline": False,
            },
            {
                "name": "⚡ Media i Ogrzewanie",
                "value": media_info,
                "inline": False,
            },
            {
                "name": "🚗 Dojazd i Parkowanie",
                "value": road_parking,
                "inline": False,
            },
        ]

        if getattr(listing, "relist_count", 0) > 0:
            relist_drop = ""
            init_p = getattr(listing, "initial_price", None)
            if init_p and init_p > listing.price:
                diff = init_p - listing.price
                pct = (diff / init_p) * 100
                relist_drop = f"\n📉 Pierwotnie: **{init_p:,.0f} zł** (spadek o **{diff:,.0f} zł** / -{pct:.1f}%)"
            days_txt = ""
            first_seen = getattr(listing, "first_seen_at", None)
            if first_seen:
                fs_utc = first_seen.replace(tzinfo=UTC) if first_seen.tzinfo is None else first_seen
                days = max(1, (datetime.now(UTC) - fs_utc).days)
                days_txt = f" | Łącznie na rynku: **{days} dni**"
            fields.insert(
                0,
                {
                    "name": "🔁 WYKRYTO POZORNY RE-LISTING",
                    "value": f"Nieruchomość powraca na rynek ({listing.relist_count}x){days_txt}{relist_drop}\n⚠️ *Sprzedający pod silną presją czasu i negocjacji!*",
                    "inline": False,
                },
            )

        parcel_id = getattr(listing, "parcel_id", None)
        if getattr(listing, "geoportal_url", None) and parcel_id:
            area_str = f" ({listing.cadastral_area:.0f} m²)" if getattr(listing, "cadastral_area", None) else ""
            p_nr = parcel_id.split(".")[-1]
            geo_lines = [f"[Działka nr {p_nr}{area_str}]({listing.geoportal_url})"]
            if getattr(listing, "mpzp_zone", None):
                geo_lines.append(f"🏛️ **MPZP:** {listing.mpzp_zone}")
            if getattr(listing, "flood_risk_zone", None):
                flood_icon = "🌊" if listing.flood_risk_zone == "ZAGROŻENIE_POWODZIOWE" else "🛡️"
                geo_lines.append(f"{flood_icon} **Zagrożenie powodziowe:** {listing.flood_risk_zone}")
            if getattr(listing, "landslide_risk", None) and listing.landslide_risk in (
                "OSUWISKO",
                "ZAGROŻENIE_OSUWISKIEM",
            ):
                geo_lines.append(f"🚨 **Osuwisko (SOPO):** {listing.landslide_risk}")
            nz = getattr(listing, "noise_zone", None)
            if nz and "WYSOKI" in nz:
                db_val = (
                    f"{listing.noise_level_db:.0f}" if getattr(listing, "noise_level_db", None) is not None else ">65"
                )
                geo_lines.append(f"🔊 **Hałas:** {db_val} dB Lden")
            if getattr(listing, "cemetery_buffer_zone", None) and listing.cemetery_buffer_zone in ("<50m", "50-150m"):
                geo_lines.append(f"⚰️ **Strefa cmentarna:** {listing.cemetery_buffer_zone}")
            if getattr(listing, "monument_zone", None):
                geo_lines.append(f"🏛️ **Zabytek (NID):** {listing.monument_zone}")
            fields.append(
                {
                    "name": "🗺️ Geoportal / Ewidencja Gruntów",
                    "value": "\n".join(geo_lines),
                    "inline": False,
                }
            )

        # Key Pros & Cons
        if filter_result.pros:
            fields.append(
                {
                    "name": "✨ Kluczowe zalety",
                    "value": "\n".join([f"• {pro}" for pro in filter_result.pros[:6]]),
                    "inline": False,
                }
            )

        if filter_result.cons:
            fields.append(
                {
                    "name": "⚠️ Wykryte minusy / do weryfikacji",
                    "value": "\n".join([f"• {con}" for con in filter_result.cons[:5]]),
                    "inline": False,
                }
            )

        if filter_result.ai_verdict:
            fields.append(
                {
                    "name": f"⚖️ Werdykt AI {filter_result.verdict_icon}",
                    "value": filter_result.ai_verdict[:1024],
                    "inline": False,
                }
            )

        if filter_result.ai_summary:
            fields.append(
                {
                    "name": "📋 TL;DR",
                    "value": filter_result.ai_summary[:1024],
                    "inline": False,
                }
            )

        if negotiation_advice and (
            negotiation_advice.price_deviation_pct is not None
            or negotiation_advice.suggested_opening_offer
            or negotiation_advice.negotiation_leverage == "WYSOKA"
        ):
            leverage_icon = {"WYSOKA": "🟢", "ŚREDNIA": "🟡", "NISKA": "⚪"}.get(
                negotiation_advice.negotiation_leverage, "⚪"
            )
            neg_lines = [f"**Pozycja:** {leverage_icon} {negotiation_advice.negotiation_leverage}"]
            if negotiation_advice.market_median_m2 and negotiation_advice.price_deviation_pct is not None:
                med_fmt = f"{negotiation_advice.market_median_m2:,.0f} zł/m²".replace(",", " ")
                dev = (
                    negotiation_advice.price_deviation_adjusted_pct
                    if negotiation_advice.price_deviation_adjusted_pct is not None
                    else negotiation_advice.price_deviation_pct
                )
                neg_lines.append(f"**Mediana rynku:** {med_fmt} ({dev:+.1f}% po korekcie o stan)")
            if negotiation_advice.suggested_opening_offer:
                offer_fmt = f"{negotiation_advice.suggested_opening_offer:,.0f} zł".replace(",", " ")
                neg_lines.append(f"**Sugerowane otwarcie:** {offer_fmt}")
            if negotiation_advice.arguments:
                neg_lines.append(f"**Główny argument:** {negotiation_advice.arguments[0]}")

            fields.append(
                {
                    "name": "💼 Wywiad negocjacyjny",
                    "value": "\n".join(neg_lines),
                    "inline": False,
                }
            )

        embed = {
            "title": title_display[:256],
            "url": listing.url,
            "description": f"**Status:** {status_header} • **Score:** `{filter_result.score:.1f} pkt`",
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"Portal: {listing.portal} • Fingerprint: {listing.property_fingerprint} • {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            },
        }

        if listing.main_image_url and listing.main_image_url.startswith("http"):
            embed["image"] = {"url": listing.main_image_url}

        return embed

    async def send_notification(
        self,
        listing: ListingSchema,
        filter_result: FilterResult,
        webhook_url: str | None = None,
        negotiation_advice: NegotiationAdvice | None = None,
    ) -> bool:
        """Send rich Discord notification."""
        target_webhook = webhook_url or self.webhook_url
        if not target_webhook:
            logger.warning("[DiscordNotifier] Webhook URL not configured. Skipping Discord alert.")
            return False

        embed = self.format_embed(listing, filter_result, negotiation_advice=negotiation_advice)
        payload = {
            "username": "Real Estate Hunter",
            "avatar_url": "https://img.icons8.com/fluency/96/real-estate.png",
            "embeds": [embed],
        }

        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(target_webhook, json=payload)
                    if resp.status_code in (200, 204):
                        logger.info(f"[DiscordNotifier] Alert sent successfully for: {listing.title[:50]}")
                        return True
                    if resp.status_code == 429:
                        retry_after = resp.json().get("retry_after", 2.0)
                        logger.warning(f"[DiscordNotifier] Rate limited. Retrying after {retry_after}s...")
                        await asyncio.sleep(retry_after)
                    else:
                        logger.error(f"[DiscordNotifier] Error {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"[DiscordNotifier] Failed sending webhook (attempt {attempt}/3): {e}")
                await asyncio.sleep(1.5)

        return False

    async def send_test_message(self) -> bool:
        """Sends a verification message to Discord."""
        if not self.webhook_url:
            logger.error("[DiscordNotifier] No webhook URL configured.")
            return False

        payload = {
            "username": "Universal Real Estate Hunter (Test)",
            "embeds": [
                {
                    "title": "🔔 Test połączenia z systemem monitorowania ofert",
                    "description": "Webhook Discorda działa prawidłowo! System jest gotowy do monitorowania rynku nieruchomości.",
                    "color": self.COLOR_WHITELIST,
                    "fields": [
                        {"name": "Status", "value": "🟢 Aktywny", "inline": True},
                        {"name": "Czas testu", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "inline": True},
                    ],
                }
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(self.webhook_url, json=payload)
                return res.status_code in (200, 204)
        except Exception as e:
            logger.error(f"[DiscordNotifier] Test message failed: {e}")
            return False
