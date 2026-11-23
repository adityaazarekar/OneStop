"""Scraper registry and factory."""
from __future__ import annotations

from app.scrapers.base_scraper import BaseScraper, detect_retailer, ScraperError, ScraperBlockedError, ScraperParseError, ScraperNetworkError
from app.scrapers.amazon_in import AmazonInScraper
from app.scrapers.flipkart import FlipkartScraper
from app.scrapers.croma import CromaScraper
from app.scrapers.reliance_digital import RelianceDigitalScraper
from app.scrapers.generic import GenericScraper

_REGISTRY: dict[str, type[BaseScraper]] = {
    "amazon_in": AmazonInScraper,
    "flipkart": FlipkartScraper,
    "croma": CromaScraper,
    "reliance_digital": RelianceDigitalScraper,
}


def get_scraper(url: str) -> BaseScraper:
    """Return the right scraper for *url*, falling back to GenericScraper."""
    retailer = detect_retailer(url)
    cls = _REGISTRY.get(retailer, GenericScraper) if retailer else GenericScraper
    return cls()


__all__ = [
    "BaseScraper",
    "ScraperError",
    "ScraperBlockedError",
    "ScraperParseError",
    "ScraperNetworkError",
    "AmazonInScraper",
    "FlipkartScraper",
    "CromaScraper",
    "RelianceDigitalScraper",
    "GenericScraper",
    "get_scraper",
    "detect_retailer",
]
