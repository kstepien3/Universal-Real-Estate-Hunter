import asyncio
import inspect
import random
from abc import ABC, abstractmethod
from collections.abc import Callable

from curl_cffi.requests import AsyncSession
from loguru import logger

from config import settings
from src.models.listing import ListingSchema

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]


class BaseScraper(ABC):
    """
    Abstract base scraper equipped with:
    - User-Agent rotation
    - curl_cffi Chrome TLS fingerprint impersonation (primary)
    - httpx fallback
    - Configurable retries with exponential backoff
    - Rate-limiting delay
    """

    def __init__(self, name: str):
        self.name = name
        self.timeout = settings.REQUEST_TIMEOUT_SECONDS
        self.max_retries = settings.MAX_RETRIES
        self.proxy = settings.PROXY_URL
        self.progress_cb: Callable | None = None
        self._session: AsyncSession | None = None
        self._throttle_multiplier = 1.0

    def _get_session(self) -> AsyncSession:
        """Persistent session reused across requests (keep-alive, cookies)."""
        if self._session is None:
            self._session = AsyncSession()
        return self._session

    async def close(self) -> None:
        """Close the persistent HTTP session (call when the scraper is done)."""
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    def delay(self, base_delay: float) -> float:
        """Effective politeness delay including adaptive throttle multiplier."""
        return base_delay * self._throttle_multiplier

    @property
    def is_cancelled(self) -> bool:
        try:
            from src.services.progress import global_tracker

            return global_tracker.is_cancelled()
        except Exception:
            return False

    async def _emit_progress(self, **kwargs) -> None:
        """Report live progress to the tracker callback if configured."""
        if not self.progress_cb:
            return
        try:
            result = self.progress_cb(**kwargs)
            if inspect.iscoroutine(result):
                await result
        except Exception as err:
            logger.debug(f"[{self.name}] Progress callback failed: {err}")

    def get_random_headers(self, referer: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"
        return headers

    async def fetch_html(self, url: str, referer: str | None = None) -> str | None:
        """Fetch URL with curl_cffi (Chrome impersonation), falling back to httpx."""
        headers = self.get_random_headers(referer)

        for attempt in range(1, self.max_retries + 1):
            try:
                # Primary: curl_cffi with Chrome impersonation (persistent session)
                session = self._get_session()
                resp = await session.get(
                    url,
                    headers=headers,
                    proxy=self.proxy,
                    impersonate="chrome120",
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    if self._throttle_multiplier > 1.0:
                        self._throttle_multiplier = max(1.0, self._throttle_multiplier / 1.2)
                    return resp.text
                if resp.status_code in (404, 410):
                    logger.warning(f"[{self.name}] Listing expired or not found ({resp.status_code}): {url}")
                    return None
                if resp.status_code == 403:
                    self._throttle_multiplier = min(8.0, self._throttle_multiplier * 2.0)
                    logger.warning(
                        f"[{self.name}] 403 Forbidden (attempt {attempt}/{self.max_retries}) for: {url} "
                        f"(throttle x{self._throttle_multiplier:.1f})"
                    )
                else:
                    logger.warning(f"[{self.name}] Status {resp.status_code} for {url}")

            except Exception as curl_err:
                logger.warning(f"[{self.name}] curl_cffi error ({attempt}/{self.max_retries}) on {url}: {curl_err}")
                self._session = None  # Reset broken handle; recreate on next request

            delay = 1.5 * (2 ** (attempt - 1)) + random.uniform(0.5, 1.5)
            delay *= self._throttle_multiplier
            logger.info(f"[{self.name}] Backing off for {delay:.2f}s before retry...")
            await asyncio.sleep(delay)

        logger.error(f"[{self.name}] All {self.max_retries} attempts failed for: {url}")
        return None

    @abstractmethod
    async def scrape(self) -> list[ListingSchema]:
        """Scrape and return normalized listings."""
        pass
