"""Reliance Digital product page scraper — live extraction, no fake data."""
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


class RelianceDigitalScraper(BaseScraper):
    retailer = "reliance_digital"

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
            for sel in ["h1.pdp__product-title", "h1.product-name", "h1"]:
                el = soup.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    if title:
                        break
        if not title:
            og = soup.select_one('meta[property="og:title"]')
            title = og["content"].strip() if og and og.get("content") else ""
        if not title:
            raise ScraperParseError("Could not extract product title from Reliance Digital page")

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
                "span.pdp__offerPrice",
                "span.TextWeb__Text-sc-1cyx778-0.gimCss",
                "span.pdp__priceSection__price",
                'meta[property="product:price:amount"]',
            ]:
                el = soup.select_one(sel)
                if el:
                    val = el.get("content") or el.get_text()
                    listed_price = parse_indian_price(val)
                    if listed_price:
                        break

        for sel in ["span.pdp__mrp", "span.pdp__priceSection__mrp", "span.old-price"]:
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
        if "out of stock" in body_text or "sold out" in body_text:
            in_stock = False

        # ── Brand ──
        brand = jsonld.get("brand", {}).get("name") if jsonld else None
        if not brand:
            el = soup.select_one("a.pdp__brandName, span.pdp__brandName")
            if el:
                brand = el.get_text(strip=True)

        # ── Seller ──
        seller_name = "Reliance Digital"

        # ── Bank offers ──
        raw_bank_offers = []
        for section in soup.select("div.pdp__offers, div.bank-offers, div.offer-section"):
            for li in section.select("li, div.offer-item"):
                txt = li.get_text(" ", strip=True)
                if len(txt) > 15:
                    raw_bank_offers.append(txt[:800])
        if not raw_bank_offers:
            for el in soup.find_all(string=re.compile(r"Bank\s*Offer|Cashback|Instant", re.I)):
                parent = el.find_parent("li") or el.find_parent("div")
                if parent:
                    txt = parent.get_text(" ", strip=True)
                    if len(txt) > 15:
                        raw_bank_offers.append(txt[:800])

        # ── Coupons ──
        raw_coupons = []
        for el in soup.find_all(string=re.compile(r"coupon|extra\s+off|promo", re.I)):
            parent = el.find_parent("div") or el.find_parent("li")
            if parent:
                txt = parent.get_text(" ", strip=True)
                if len(txt) > 10:
                    raw_coupons.append(txt[:600])

        # ── Reviews ──
        recent_reviews = []
        for rev in soup.select("div.review-text, div.user-review-text, p.review-content"):
            txt = rev.get_text(strip=True)
            if len(txt) > 20:
                recent_reviews.append(txt[:500])
            if len(recent_reviews) >= 10:
                break

        # ── Specs ──
        variant_specs = self._extract_specs(soup)

        return ClientPayload(
            retailer="reliance_digital",
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
        # URLs like /product-name/p/XXXXXX
        m = re.search(r"/p/(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"/(\d{5,})", url)
        if m:
            return m.group(1)
        sku_el = soup.select_one("[data-sku], [data-product-id], [data-item-id]")
        if sku_el:
            return (
                sku_el.get("data-sku")
                or sku_el.get("data-product-id")
                or sku_el.get("data-item-id", "UNKNOWN")
            )
        return "UNKNOWN_RD"

    def _extract_specs(self, soup) -> dict:
        specs: dict = {}
        for table in soup.select(
            "table.specification-table, div.pdp__specSection, div.specs-table"
        ):
            for row in table.select("tr, div.spec-row"):
                cells = row.select("td, span, div")
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
        url = f"https://www.reliancedigital.in/search?q={quote_plus(query)}"
        try:
            html = await self.fetch_page(url)
        except Exception as exc:
            logger.warning("Reliance Digital search failed: %s", exc)
            return []

        soup = self._soup(html)
        results: list[SearchHit] = []
        for card in soup.select("div.product-card, li.product, div.sp__product"):
            link = card.select_one("a[href*='/p/']")
            if not link:
                continue
            href = link.get("href", "")
            if not href.startswith("http"):
                href = f"https://www.reliancedigital.in{href}"
            title_el = card.select_one("p.sp__name, h3.product-title, span.product-name")
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)[:100]
            price_el = card.select_one("span.TextWeb__Text-sc-1cyx778-0, span.amount")
            price = parse_indian_price(price_el.get_text()) if price_el else None
            img_el = card.select_one("img")
            thumb = img_el.get("src") or img_el.get("data-src") if img_el else None
            sku_m = re.search(r"/p/(\d+)", href)
            sku = sku_m.group(1) if sku_m else "UNKNOWN"
            if title and len(title) > 5:
                results.append(SearchHit(
                    retailer="reliance_digital",
                    title=title[:200],
                    url=href,
                    price=price,
                    sku=sku,
                    thumbnail_url=thumb,
                ))
            if len(results) >= 10:
                break
        return results

