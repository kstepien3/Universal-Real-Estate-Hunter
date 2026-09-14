import httpx
from loguru import logger

from config import settings
from src.models.enums import QualificationStatus
from src.models.listing import FilterResult, ListingSchema
from src.services.market_analyzer import NegotiationAdvice


class TelegramNotifier:
    """
    Telegram Bot Notifier:
    Sends clean, structured HTML messages for qualified property listings.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def format_message(
        self,
        listing: ListingSchema,
        filter_result: FilterResult,
        negotiation_advice: NegotiationAdvice | None = None,
    ) -> str:
        status_tag = (
            "⭐ <b>WHITELIST</b>"
            if filter_result.status == QualificationStatus.QUALIFIED_WHITELIST
            else "✅ <b>KWALIFIKACJA</b>"
        )

        price_fmt = f"{listing.price:,.0f} zł".replace(",", " ")
        price_m2_fmt = f"{listing.price_per_m2:,.0f} zł/m²".replace(",", " ")
        plot_fmt = f"{listing.area_plot:.0f} m²" if listing.area_plot else "b/d (analiza opisu)"

        sew_val = listing.sewerage.value if hasattr(listing.sewerage, "value") else str(listing.sewerage)
        heat_val = listing.heating.value if hasattr(listing.heating, "value") else str(listing.heating)
        fiber_str = " | 🌐 Światłowód" if listing.has_fiber else ""
        vis_tag = " | ⚠️ Wizualizacje 3D" if listing.has_visualisations else ""

        lines = [
            f"🏠 <b><a href='{listing.url}'>{listing.title}</a></b>",
            f"Status: {status_tag} (Score: <b>{filter_result.score:.1f}</b>)",
            "",
            f"💰 Cena: <b>{price_fmt}</b> ({price_m2_fmt})",
            f"📐 Dom: <b>{listing.area_home:.1f} m²</b> | Działka: <b>{plot_fmt}</b>",
            f"🏡 Typ: <b>{listing.building_type.value} ({listing.segment_subtype.value})</b>",
            f"🔨 Stan: <b>{listing.finish_condition.value}</b> | Rynek: <b>{listing.market.value}</b>{vis_tag}",
            f"⚡ Media: <b>{sew_val}</b> (ścieki), <b>{heat_val}</b> (ogrzewanie){fiber_str}",
            f"📍 Lokalizacja: <b>{listing.location_raw}</b>",
            f"🚗 Droga: <b>{listing.access_road_type.value}</b>",
        ]

        if getattr(listing, "relist_count", 0) > 0:
            relist_drop = ""
            init_p = getattr(listing, "initial_price", None)
            if init_p and init_p > listing.price:
                diff = init_p - listing.price
                pct = (diff / init_p) * 100
                relist_drop = f" (obniżka o {diff:,.0f} zł / -{pct:.1f}%)"
            lines.insert(
                2,
                f"🔁 <b>POZORNY RE-LISTING ({listing.relist_count}x):</b> pierwotnie {init_p:,.0f} zł{relist_drop}",
            )

        if filter_result.pros:
            lines.append("\n🌟 <b>Zalety z opisu:</b>")
            for p in filter_result.pros[:4]:
                lines.append(f"  • {p}")

        if filter_result.cons:
            lines.append("\n⚠️ <b>Uwagi / Wady:</b>")
            for c in filter_result.cons[:3]:
                lines.append(f"  • {c}")

        if filter_result.ai_verdict:
            lines.append(f"\n⚖️ <b>Werdykt AI {filter_result.verdict_icon}:</b> {filter_result.ai_verdict}")

        if negotiation_advice and (
            negotiation_advice.price_deviation_pct is not None
            or negotiation_advice.suggested_opening_offer
            or negotiation_advice.negotiation_leverage == "WYSOKA"
        ):
            leverage_icon = {"WYSOKA": "🟢", "ŚREDNIA": "🟡", "NISKA": "⚪"}.get(
                negotiation_advice.negotiation_leverage, "⚪"
            )
            lines.append(
                f"\n💼 <b>Negocjacje:</b> Pozycja {leverage_icon} <b>{negotiation_advice.negotiation_leverage}</b>"
            )
            if negotiation_advice.market_median_m2 and negotiation_advice.price_deviation_pct is not None:
                med_fmt = f"{negotiation_advice.market_median_m2:,.0f} zł/m²".replace(",", " ")
                dev = (
                    negotiation_advice.price_deviation_adjusted_pct
                    if negotiation_advice.price_deviation_adjusted_pct is not None
                    else negotiation_advice.price_deviation_pct
                )
                lines.append(f"  • Rynek: <b>{med_fmt}</b> ({dev:+.1f}% po korekcie o stan)")
            if negotiation_advice.suggested_opening_offer:
                offer_fmt = f"{negotiation_advice.suggested_opening_offer:,.0f} zł".replace(",", " ")
                lines.append(f"  • Sugerowane otwarcie: <b>{offer_fmt}</b>")
            if negotiation_advice.arguments:
                lines.append(f"  • Argument: <i>{negotiation_advice.arguments[0]}</i>")

        parcel_id = getattr(listing, "parcel_id", None)
        if getattr(listing, "geoportal_url", None):
            p_nr = parcel_id.split(".")[-1] if parcel_id else "mapa"
            lines.append(f"🗺️ <a href='{listing.geoportal_url}'>Działka w Geoportalu (nr {p_nr})</a>")
        if getattr(listing, "mpzp_zone", None):
            lines.append(f"🏛️ MPZP: <b>{listing.mpzp_zone}</b>")
        if getattr(listing, "flood_risk_zone", None):
            f_icon = "🌊" if listing.flood_risk_zone == "ZAGROŻENIE_POWODZIOWE" else "🛡️"
            lines.append(f"{f_icon} Powódź: <b>{listing.flood_risk_zone}</b>")
        if getattr(listing, "landslide_risk", None) and listing.landslide_risk in ("OSUWISKO", "ZAGROŻENIE_OSUWISKIEM"):
            lines.append(f"🚨 Osuwisko (SOPO): <b>{listing.landslide_risk}</b>")
        nz = getattr(listing, "noise_zone", None)
        if nz and "WYSOKI" in nz:
            db_val = f"{listing.noise_level_db:.0f}" if getattr(listing, "noise_level_db", None) is not None else ">65"
            lines.append(f"🔊 Hałas: <b>{db_val} dB Lden ({nz})</b>")
        if getattr(listing, "cemetery_buffer_zone", None) and listing.cemetery_buffer_zone in ("<50m", "50-150m"):
            lines.append(f"⚰️ Cmentarz: <b>{listing.cemetery_buffer_zone}</b>")
        if getattr(listing, "monument_zone", None):
            lines.append(f"🏛️ Zabytek (NID): <b>{listing.monument_zone}</b>")
        lines.append(f"\n🔗 <a href='{listing.url}'>Zobacz ogłoszenie na {listing.portal}</a>")
        return "\n".join(lines)

    async def send_notification(
        self,
        listing: ListingSchema,
        filter_result: FilterResult,
        negotiation_advice: NegotiationAdvice | None = None,
    ) -> bool:
        if not self.is_configured():
            return False

        text = self.format_message(listing, filter_result, negotiation_advice=negotiation_advice)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    logger.info(f"[TelegramNotifier] Alert sent for: {listing.title[:40]}")
                    return True
                logger.error(f"[TelegramNotifier] Error {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"[TelegramNotifier] Exception sending alert: {e}")

        return False
