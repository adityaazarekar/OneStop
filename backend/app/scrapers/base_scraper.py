"""
Base scraper infrastructure: curl_cffi + Redis cache + Playwright fallback.

NO mock/fake data. If a live fetch fails, returns a clear error.
"""
from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from urllib.parse import urlparse

import redis.asyncio as aioredis
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from app.config import settings
from app.schemas.extraction import ClientPayload, ScraperResult, SearchHit

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours

# ───────────────────────────────────────────
# Error hierarchy
# ───────────────────────────────────────────

class ScraperError(Exception):
    """Base scraper error."""


class ScraperBlockedError(ScraperError):
    """Raised when the site returns a captcha, 403, or 503."""

    def __init__(self, url: str, status: int = 0):
        self.url = url
        self.status = status
        super().__init__(
            f"Live fetch blocked (HTTP {status}) for {url}. "
            "Use the Chrome Extension or Android App to extract from your residential IP."
        )


class ScraperParseError(ScraperError):
    """Page fetched but required data could not be parsed from the HTML."""


class ScraperNetworkError(ScraperError):
    """Connection-level failure (timeout, DNS, etc.)."""


# ───────────────────────────────────────────
# Utilities
# ───────────────────────────────────────────

RETAILER_PATTERNS: dict[str, str] = {
    "amazon_in": r"(?:www\.)?(?:amazon\.in|amzn\.in|amzn\.to)",
    "flipkart": r"(?:www\.)?(?:flipkart\.com|fkrt\.cc|fkrt\.it)",
    "croma": r"(?:www\.)?croma\.com",
    "reliance_digital": r"(?:www\.)?reliancedigital\.in",
    "vijaysales": r"(?:www\.)?vijaysales\.com",
    "tatacliq": r"(?:www\.)?tatacliq\.com",
    "jiomart": r"(?:www\.)?jiomart\.com",
}


async def resolve_url_redirects(url: str, timeout: float = 10.0) -> str:
    """Follow HTTP redirects to unshorten amzn.in, fkrt.cc, bit.ly, etc."""
    short_domains = ("amzn.in", "amzn.to", "fkrt.cc", "fkrt.it", "bit.ly", "t.co", "tinyurl.com")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(sd in host for sd in short_domains):
        return url

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.head(url, headers=headers)
            if resp.status_code in (405, 403, 400):
                async with client.stream("GET", url, headers=headers) as stream_resp:
                    return str(stream_resp.url)
            return str(resp.url)
    except Exception as exc:
        logger.warning(f"Could not unshorten {url}: {exc}")
        return url


def detect_retailer(url: str) -> str | None:
    """Return retailer key from a product URL, or None."""
    host = urlparse(url).hostname or ""
    for key, pattern in RETAILER_PATTERNS.items():
        if re.search(pattern, host):
            return key
    return None


def parse_indian_price(text: str | None) -> float | None:
    """Extract a numeric price from Indian-formatted text like '₹79,900.00'."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _cache_key(url: str) -> str:
    return f"page:{hashlib.sha256(url.encode()).hexdigest()}"


# ───────────────────────────────────────────
# Redis singleton
# ───────────────────────────────────────────

_redis_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url, decode_responses=True
        )
    return _redis_pool


# ───────────────────────────────────────────
# Base scraper
# ───────────────────────────────────────────

class BaseScraper(ABC):
    """
    Abstract base for per-retailer scrapers.

    Fetch order:
      1. Redis page cache (6h TTL)
      2. curl_cffi with Chrome TLS impersonation
      3. Playwright headless fallback
      4. Clear error — never fake data
    """

    retailer: str = ""

    # ── Fetch pipeline ──────────────────────

    async def fetch_page(self, url: str) -> str:
        """Return HTML string for *url*, using cache → curl_cffi → Playwright."""
        redis = await get_redis()
        key = _cache_key(url)

        # 1. Cache hit
        cached = await redis.get(key)
        if cached:
            logger.debug("Cache hit for %s", url)
            return cached

        # 2. curl_cffi
        html: str | None = None
        try:
            html = await self._fetch_curl_cffi(url)
        except ScraperBlockedError:
            logger.warning("curl_cffi blocked for %s, trying Playwright", url)
        except Exception as exc:
            logger.warning("curl_cffi error for %s: %s", url, exc)

        # 3. Playwright fallback
        if html is None:
            try:
                html = await self._fetch_playwright(url)
            except Exception as exc:
                raise ScraperBlockedError(url) from exc

        # 4. Store in cache
        await redis.set(key, html, ex=CACHE_TTL_SECONDS)
        return html

    async def _fetch_curl_cffi(self, url: str) -> str:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        }
        async with AsyncSession(impersonate="chrome") as session:
            resp = await session.get(url, headers=headers, timeout=20)
            if resp.status_code in (403, 429, 503):
                raise ScraperBlockedError(url, resp.status_code)
            if resp.status_code >= 400:
                raise ScraperNetworkError(
                    f"HTTP {resp.status_code} for {url}"
                )
            return resp.text

    async def _fetch_playwright(self, url: str) -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(
                    locale="en-IN",
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                if resp and resp.status in (403, 429, 503):
                    raise ScraperBlockedError(url, resp.status)
                # Wait for dynamic price content
                await page.wait_for_timeout(2000)
                html = await page.content()
                return html
            finally:
                await browser.close()

    def _soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    # ── Abstract interface ──────────────────

    @abstractmethod
    async def extract_product(self, url: str) -> ScraperResult:
        """Fetch and parse a single product page into a ScraperResult."""

    async def scrape(self, url: str) -> ScraperResult:
        """Alias for extract_product."""
        return await self.extract_product(url)

    @abstractmethod
    async def search_products(self, query: str) -> list[SearchHit]:
        """Search this retailer by model name / MPN and return hits."""

