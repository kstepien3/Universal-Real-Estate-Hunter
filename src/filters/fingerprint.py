import hashlib
import re
import unicodedata


def normalize_text(text: str | None) -> str:
    """Normalize Polish characters and clean up whitespace."""
    if not text:
        return ""
    text = text.lower().strip()
    text = text.replace("ł", "l")
    # Normalize unicode forms and strip combining diacritics
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Remove common prefixes
    text = re.sub(r"\b(ul\.|ulica|al\.|aleja|os\.|osiedle|rejon|okolice|blisko|przy)\b", "", text)
    # Remove non-alphanumeric chars except space
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse multiple spaces
    return re.sub(r"\s+", " ", text).strip()


HONORIFIC_PREFIXES = {
    "sw",
    "swietego",
    "ksiecia",
    "biskupa",
    "generala",
    "marszalka",
    "majora",
    "ignacego",
    "jana",
    "tadeusza",
    "jozefa",
    "stanislawa",
    "adama",
    "juliana",
    "ksawerego",
    "wladyslawa",
    "stefana",
    "henryka",
    "mikolaja",
    "krolowej",
}


def extract_street_token(
    street: str | None,
    district: str | None = None,
    location_raw: str | None = None,
    title: str | None = None,
) -> str:
    """Extract standard street or micro-location token for deduplication."""
    if street:
        norm = normalize_text(street)
        words = [w for w in norm.split() if w not in HONORIFIC_PREFIXES and len(w) > 2]
        if words:
            # Surnames in Polish streets are almost always the last token (e.g. "Paderewskiego")
            return words[-1]

    # Try district
    if district:
        norm_dist = normalize_text(district)
        if norm_dist:
            return norm_dist.split()[0]

    # Try extracting street from raw location or title
    for source in (location_raw, title):
        if source:
            match = re.search(
                r"\bul(?:ica|\.)?\s+([A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż]+)?)",
                source,
                re.IGNORECASE,
            )
            if match:
                raw_extracted = normalize_text(match.group(1))
                words = [w for w in raw_extracted.split() if w not in HONORIFIC_PREFIXES and len(w) > 2]
                if words:
                    return words[-1]

    return "unknown_area"


def compute_desc_hash(raw_description: str | None) -> str | None:
    """Stable hash of normalized description for LLM result caching.

    Normalizes whitespace/case via :func:`normalize_text` so trivial
    formatting edits do not invalidate the cache.
    """
    if not raw_description:
        return None
    normalized = normalize_text(raw_description)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def estimate_llm_tokens(text: str | None, head: int = 4000, tail: int = 1500) -> int:
    """Rough token estimate (~4 chars/token) for the sliced description + prompt overhead."""
    if not text:
        return 1500  # prompt template + schema overhead
    sliced_len = min(len(text), head + tail + 10)
    return (sliced_len // 4) + 1500


def generate_physical_fingerprint(
    area_home: float,
    area_plot: float | None = None,
    rooms: int | None = None,
    street: str | None = None,
    district: str | None = None,
    city: str | None = None,
    location_raw: str | None = None,
    title: str | None = None,
    category: str = "dom",
) -> str | None:
    """
    Generate unique physical property signature independent of price:
    - Category (dom, mieszkanie, dzialka)
    - Normalized city token
    - Street / micro-location fragment
    - Home area bucket (+/- 2 m² tolerance -> bucket 4 m²)
    - Plot area bucket (+/- 10 m² tolerance -> bucket 10 m²)
    - Room count (if known)

    Returns None if location is completely indeterminate to prevent false collisions.
    """
    cat_str = str(getattr(category, "value", category) or "dom").lower()
    city_token = normalize_text(city) if city else ""
    street_token = extract_street_token(street, district, location_raw, title)

    if street_token == "unknown_area" and not city_token and not district:
        return None

    home_bucket = int(round(area_home / 4.0) * 4) if area_home > 0 else 0
    plot_bucket = str(int(round(area_plot / 10.0) * 10)) if area_plot and area_plot > 0 else "noplot"

    room_token = str(rooms) if rooms and rooms > 0 else "0"
    dist_token = normalize_text(district) if district else ""

    raw_sig = f"{cat_str}|{city_token}|{dist_token}|{street_token}|{home_bucket}|{plot_bucket}|{room_token}"
    digest = hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()[:16]
    return f"phys_{digest}"
