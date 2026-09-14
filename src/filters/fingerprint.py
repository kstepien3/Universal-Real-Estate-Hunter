import hashlib
import re
import unicodedata


def normalize_text(text: str | None) -> str:
    """Normalize Polish characters and clean up whitespace."""
    if not text:
        return ""
    text = text.lower().strip()
    # Normalize unicode forms
    text = unicodedata.normalize("NFKD", text)
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


def generate_property_fingerprint(
    price: float,
    area_home: float,
    area_plot: float | None,
    street: str | None = None,
    district: str | None = None,
    location_raw: str | None = None,
    title: str | None = None,
    category: str = "dom",
) -> str:
    """
    Generate unique property fingerprint based on:
    - Property category (dom, mieszkanie, dzialka)
    - Normalized price (bucketed to nearest 10,000 PLN)
    - Home area (+/- 2 m² tolerance -> bucketed to 2m² intervals)
    - Plot area (+/- 10 m² tolerance -> bucketed to 10m² intervals)
    - Extracted street / micro-location fragment
    """
    # Price bucket (e.g. 1,199,000 -> 1,200,000)
    price_bucket = int(round(price / 10000.0) * 10000) if price > 0 else 0

    # Home area bucket (+/- 2 m² tolerance -> bucket width 4 m²)
    home_bucket = int(round(area_home / 4.0) * 4) if area_home > 0 else 0

    # Plot area bucket (+/- 10 m²)
    if area_plot and area_plot > 0:
        plot_bucket = str(int(round(area_plot / 10.0) * 10))
    else:
        plot_bucket = "noplot"

    street_token = extract_street_token(street, district, location_raw, title)
    cat_str = str(getattr(category, "value", category) or "dom").lower()

    raw_signature = f"{cat_str}|{street_token}|{price_bucket}|{home_bucket}|{plot_bucket}"
    digest = hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()[:16]
    return f"fp_{digest}"
