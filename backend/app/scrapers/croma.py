"""Croma.com product page scraper — live extraction, no fake data."""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote_plus

from app.schemas.extraction import ClientPayload, ScraperResult, SearchHit
from app.scrapers.base_scraper import (
    BaseScraper,
    ScraperParseError,
    parse_indian_price,
)

logger = logging.getLogger(__name__)


class CromaScraper(BaseScraper):
    retailer = "croma"

    async def extract_product(self, url: str) -> ScraperResult:
        try:
            html = await self.fetch_page(url)
        except Exception as exc:
            return ScraperResult(success=False, error=str(exc))
        try:
            payload = self._parse_product_page(html, url)
            return ScraperResult(success=True, payload=payload, source="curl_cffi")
        except ScraperParseError as exc:
            return ScraperResult(success=False, error=str(exc))

    def _parse_product_page(self, html: str, url: str) -> ClientPayload:
        soup = self._soup(html)
        jsonld = self._extract_jsonld(soup)

        # ── Title ──
        title = ""
        if jsonld:
            title = jsonld.get("name", "")
        if not title:
            for sel in ["h1.pd-title", "h1.product-title", "h1"]:
                el = soup.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    if title:
                        break
        if not title:
            og = soup.select_one('meta[property="og:title"]')
            title = og["content"].strip() if og and og.get("content") else ""
        if not title:
            raise ScraperParseError("Could not extract product title from Croma page")

        # ── SKU ──
        sku = self._extract_sku(url, soup)

        # ── Prices ──
        listed_price = None
        mrp = None
        if jsonld:
            offers = jsonld.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            listed_price = self._safe_float(offers.get("price"))

        if listed_price is None:
            for sel in [
                "span.pdp-price span, span.new-price",
                "span.amount, span.pdpPrice",
                'meta[property="product:price:amount"]',
            ]:
                el = soup.select_one(sel)
                if el:
                    val = el.get("content") or el.get_text()
                    listed_price = parse_indian_price(val)
                    if listed_price:
                        break

        for sel in ["span.old-price", "span.mrpPrice", "span.pdp-mrp"]:
            el = soup.select_one(sel)
            if el:
                mrp = parse_indian_price(el.get_text())
                if mrp:
                    break

        # ── Stock ──
        in_stock = True
        if jsonld:
            avail = str(jsonld.get("offers", {}).get("availability", "")).lower()
            in_stock = "outofstock" not in avail
        body_text = soup.get_text().lower()
        if "out of stock" in body_text or "currently unavailable" in body_text:
            in_stock = False

        # ── Brand ──
        brand = jsonld.get("brand", {}).get("name") if jsonld else None
        if not brand:
            brand_el = soup.select_one("a.brand-name, span.brand-name, a[href*='/brand/']")
            if brand_el:
                brand = brand_el.get_text(strip=True)

        # ── Seller (Croma is single-seller) ──
        seller_name = "Croma - A Tata Enterprise"

        # ── Bank offers ──
        raw_bank_offers = []
        offer_section = soup.select_one("div.offer-section, div.pdp-offers, div.bank-offers")
        if offer_section:
            for li in offer_section.select("li, div.offer-item, div.offer-text"):
                txt = li.get_text(" ", strip=True)
                if len(txt) > 15:
                    raw_bank_offers.append(txt[:800])
        # Fallback: find "Bank Offer" text
        if not raw_bank_offers:
            for el in soup.find_all(string=re.compile(r"Bank\s*Offer|Cashback|Instant\s+Discount", re.I)):
                parent = el.find_parent("li") or el.find_parent("div")
                if parent:
                    txt = parent.get_text(" ", strip=True)
                    if len(txt) > 15:
                        raw_bank_offers.append(txt[:800])

        # ── Coupons ──
        raw_coupons = []
        for el in soup.find_all(string=re.compile(r"coupon|promo\s*code", re.I)):
            parent = el.find_parent("div") or el.find_parent("li")
            if parent:
                txt = parent.get_text(" ", strip=True)
                if len(txt) > 10:
                    raw_coupons.append(txt[:600])

        # ── Reviews ──
        recent_reviews = []
        for rev in soup.select("div.review-text, p.review-comment, div.user-review"):
            txt = rev.get_text(strip=True)
            if len(txt) > 20:
                recent_reviews.append(txt[:500])
            if len(recent_reviews) >= 10:
                break

        # ── Specs ──
        variant_specs = self._extract_specs(soup)

        return ClientPayload(
            retailer="croma",
            url=url,
            title=title,
            brand=brand,
            sku=sku,
            listed_price=listed_price,
            mrp=mrp,
            in_stock=in_stock,
            seller_name=seller_name,
            seller_rating=None,
            raw_bank_offers=raw_bank_offers[:15],
            raw_coupons=raw_coupons[:5],
            raw_exchange_offers=[],
            recent_reviews=recent_reviews,
            variant_specs=variant_specs,
            source="server",
        )

    # ── Helpers ──

    def _extract_jsonld(self, soup) -> dict | None:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    for item in data:
                        if item.get("@type") == "Product":
                            return item
                elif isinstance(data, dict) and data.get("@type") == "Product":
                    return data
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def _extract_sku(self, url: str, soup) -> str:
        # Croma URLs: /product-name/p/XXXXXX
        m = re.search(r"/p/(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"/(\d{5,})", url)
        if m:
            return m.group(1)
        sku_el = soup.select_one("[data-sku], [data-product-id]")
        if sku_el:
            return sku_el.get("data-sku") or sku_el.get("data-product-id", "UNKNOWN")
        return "UNKNOWN_CROMA"

    def _extract_specs(self, soup) -> dict:
        specs: dict = {}
        for table in soup.select("table.specification-table, div.pd-specification"):
            for row in table.select("tr, div.spec-row"):
                cells = row.select("td, span")
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True).lower().replace(" ", "_")
                    val = cells[1].get_text(strip=True)
                    if key and val:
                        specs[key] = val
        return specs

    @staticmethod
    def _safe_float(val) -> float | None:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    async def search_products(self, query: str) -> list[SearchHit]:
        url = f"https://www.croma.com/search/?text={quote_plus(query)}"
        try:
            html = await self.fetch_page(url)
        except Exception as exc:
            logger.warning("Croma search failed: %s", exc)
            return []

        soup = self._soup(html)
        results: list[SearchHit] = []
        for card in soup.select("div.product-item, li.product"):
            link = card.select_one("a[href*='/p/']")
            if not link:
                continue
            href = link.get("href", "")
            if not href.startswith("http"):
                href = f"https://www.croma.com{href}"
            title = link.get_text(strip=True) or ""
            price_el = card.select_one("span.amount, span.new-price")
            price = parse_indian_price(price_el.get_text()) if price_el else None
            img_el = card.select_one("img")
            thumb = img_el.get("src") or img_el.get("data-src") if img_el else None
            sku_m = re.search(r"/p/(\d+)", href)
            sku = sku_m.group(1) if sku_m else "UNKNOWN"
            if title:
                results.append(SearchHit(
                    retailer="croma",
                    title=title[:200],
                    url=href,
                    price=price,
                    sku=sku,
                    thumbnail_url=thumb,
                ))
            if len(results) >= 10:
                break
        return results

