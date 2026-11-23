"""Generic/universal scraper for any Indian e-commerce site not in Tier 1.

Extracts via JSON-LD Product schema, OpenGraph meta tags, and common
DOM patterns. Used as fallback for Vijay Sales, Tata CLiQ, JioMart,
Myntra, Nykaa, Ajio, brand stores, etc.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

from app.schemas.extraction import ClientPayload, ScraperResult, SearchHit
from app.scrapers.base_scraper import BaseScraper, ScraperParseError, parse_indian_price

logger = logging.getLogger(__name__)


class GenericScraper(BaseScraper):
    retailer = "generic"

    async def extract_product(self, url: str) -> ScraperResult:
        try:
            html = await self.fetch_page(url)
        except Exception as exc:
            return ScraperResult(success=False, error=str(exc))
        try:
            payload = self._parse(html, url)
            return ScraperResult(success=True, payload=payload, source="curl_cffi")
        except ScraperParseError as exc:
            return ScraperResult(success=False, error=str(exc))

    def _parse(self, html: str, url: str) -> ClientPayload:
        soup = self._soup(html)
        jsonld = self._jsonld_product(soup)
        host = urlparse(url).hostname or "unknown"
        retailer_key = re.sub(r"^www\.", "", host).replace(".", "_").replace("-", "_")

        # ── Title ──
        title = ""
        if jsonld:
            title = jsonld.get("name", "")
        if not title:
            for sel in ["h1", 'meta[property="og:title"]']:
                el = soup.select_one(sel)
                if el:
                    title = el.get("content", "") if el.name == "meta" else el.get_text(strip=True)
                    if title:
                        break
        if not title:
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            raise ScraperParseError(f"Could not extract title from {host}")

        # ── SKU ──
        sku = "UNKNOWN"
        if jsonld:
            sku = jsonld.get("sku") or jsonld.get("mpn") or jsonld.get("gtin13") or sku
        if sku == "UNKNOWN":
            m = re.search(r"/p(?:roduct)?/([A-Za-z0-9_-]+)", url)
            if m:
                sku = m.group(1)

        # ── Prices ──
        listed_price = None
        mrp = None
        if jsonld:
            offers = jsonld.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            listed_price = self._safe_float(offers.get("price"))
        if listed_price is None:
            pm = soup.select_one('meta[property="product:price:amount"]')
            if pm:
                listed_price = self._safe_float(pm.get("content"))
        if listed_price is None:
            for el in soup.select("[class*='price'], [class*='Price'], [id*='price']"):
                p = parse_indian_price(el.get_text())
                if p and p > 50:
                    listed_price = p
                    break

        # ── Stock ──
        in_stock = True
        if jsonld:
            avail = str(jsonld.get("offers", {}).get("availability", "")).lower()
            in_stock = "outofstock" not in avail
        body = soup.get_text()[:5000].lower()
        if "out of stock" in body or "currently unavailable" in body or "sold out" in body:
            in_stock = False

        # ── Brand ──
        brand = None
        if jsonld:
            b = jsonld.get("brand")
            brand = b.get("name") if isinstance(b, dict) else b if isinstance(b, str) else None
        if not brand:
            bm = soup.select_one('meta[property="product:brand"]')
            brand = bm.get("content") if bm else None

        # ── Bank offers ──
        raw_bank_offers: list[str] = []
        for el in soup.find_all(string=re.compile(r"Bank\s*Offer|Cashback|Instant\s+Discount|No Cost EMI", re.I)):
            parent = el.find_parent("li") or el.find_parent("div")
            if parent:
                txt = parent.get_text(" ", strip=True)
                if len(txt) > 15:
                    raw_bank_offers.append(txt[:800])
        raw_bank_offers = raw_bank_offers[:15]

        # ── Coupons ──
        raw_coupons: list[str] = []
        for el in soup.find_all(string=re.compile(r"coupon|promo|extra.*off", re.I)):
            parent = el.find_parent("li") or el.find_parent("div")
            if parent:
                txt = parent.get_text(" ", strip=True)
                if len(txt) > 10:
                    raw_coupons.append(txt[:600])

        return ClientPayload(
            retailer=retailer_key[:20],
            url=url,
            title=title,
            brand=brand,
            sku=sku,
            listed_price=listed_price,
            mrp=mrp,
            in_stock=in_stock,
            seller_name=host,
            seller_rating=None,
            raw_bank_offers=raw_bank_offers,
            raw_coupons=raw_coupons[:5],
            raw_exchange_offers=[],
            recent_reviews=[],
            variant_specs={},
            source="server",
        )

    def _jsonld_product(self, soup) -> dict | None:
        for s in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(s.string or "")
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            return item
                elif isinstance(data, dict):
                    if data.get("@type") == "Product":
                        return data
                    if "@graph" in data:
                        for item in data["@graph"]:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                return item
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    @staticmethod
    def _safe_float(v) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    async def search_products(self, query: str) -> list[SearchHit]:
        return []  # Generic sites don't have a uniform search API
