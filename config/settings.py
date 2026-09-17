from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_whitelist_areas() -> list[dict[str, Any]]:
    return [
        {
            "name": "Słocina Dolna",
            "keywords": ["paderewskiego", "witolda", "powstańców wielkopolskich", "powstancow wielkopolskich"],
            "required_parent": "słocina",
        },
        {
            "name": "Zalesie Dolne/Centralne",
            "keywords": ["łukasiewicza", "lukasiewicza", "dunikowskiego", "spacerowa"],
            "required_parent": "zalesie",
        },
        {
            "name": "Staromieście",
            "keywords": ["staromieście ogrody", "staromiescie ogrody", "borowa", "lubelska", "lubelskiej"],
            "required_parent": "staromieście",
        },
        {
            "name": "Północ/Wschód (Trzebownisko)",
            "keywords": ["trzebownisko"],
            "required_parent": None,
        },
        {
            "name": "Północ/Wschód (Nowa Wieś)",
            "keywords": ["nowa wieś", "nowa wies"],
            "required_parent": None,
        },
        {
            "name": "Północ/Wschód (Terliczka)",
            "keywords": ["terliczka"],
            "required_parent": None,
        },
        {
            "name": "Północ/Wschód (Krasne wzdłuż DK94)",
            "keywords": ["krasne", "dk94", "krasnego"],
            "required_parent": None,
        },
        {
            "name": "Północ/Wschód (Głogów Młp. Niwa / Rogoźnica / PKA)",
            "keywords": ["niwa", "rogoźnica", "rogoznica", "stacji pka", "pka", "głogów", "glogow"],
            "required_parent": None,
        },
    ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database (kept under data/ to avoid committing live DB from repo root)
    DATABASE_URL: str = "sqlite+aiosqlite:///data/listings.db"

    # Notifiers
    DISCORD_WEBHOOK_URL: str | None = None
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None

    # Stage I: Numerical thresholds
    MAX_PRICE: float = 1_300_000.0
    MIN_AREA_HOME: float = 90.0
    MAX_AREA_HOME: float = 145.0
    MIN_AREA_PLOT: float = 250.0  # If < 250 and middle segment -> reject; if None -> analyze desc

    # Stage I: Whitelist configurations
    # Whitelist is prioritized. Offers matching whitelist receive high priority / bonus tag.
    WHITELIST_AREAS: list[dict[str, Any]] = Field(default_factory=_default_whitelist_areas)

    # Scraping Configuration
    REQUEST_TIMEOUT_SECONDS: int = 25
    MAX_RETRIES: int = 3
    FETCH_DETAILS: bool = True
    CONCURRENT_REQUESTS: int = 3
    DETAIL_REFRESH_HOURS: int = 24
    PROXY_URL: str | None = None
    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    # LLM Settings (Optional)
    USE_LLM_ANALYSIS: bool = False
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_MODEL: str = "nex-agi/nex-n2.5-mini:free"
    LLM_MAX_CALLS_PER_MINUTE: int = 15
    LLM_PROMPT_VERSION: str = "v1.1"
    MEDIANS_CACHE_TTL_MINUTES: int = 30
    GEOCODE_BATCH_SIZE: int = 8
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str | None = None
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1:8b"

    # Vision AI Settings (photo audit: renders, finish condition, floorplans, defects).
    # The vision model is independent from the text model (text models are not
    # multimodal). Empty = auto: local base infers qwen2.5vl:7b, cloud base infers
    # OPENAI_MODEL. Explicit Ollama: VISION_BASE_URL=http://localhost:11434/v1
    # (bare host gets /v1 appended) + VISION_MODEL one of qwen2.5vl:7b,
    # llama3.2-vision:11b, minicpm-v:8b, moondream.
    VISION_MODEL: str | None = None
    VISION_BASE_URL: str | None = None
    VISION_API_KEY: str | None = None


settings = Settings()
