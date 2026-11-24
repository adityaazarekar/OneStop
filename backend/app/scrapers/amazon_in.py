"""Amazon.in product page scraper — live extraction, no fake data."""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote_plus

from app.config import settings
from app.schemas.extraction import ClientPayload, ScraperResult, SearchHit
from app.scrapers.base_scraper import (
    BaseScraper,
    ScraperParseError,
    parse_indian_price,
)

logger = logging.getLogger(__name__)


class AmazonInScraper(BaseScraper):
    retailer = "amazon_in"

    async def _warmup_session(self, session) -> None:
        """Hit Amazon.in homepage to acquire session cookies and apply default pincode via glow endpoint."""
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        }
        try:
            await session.get("https://www.amazon.in/", headers=headers, timeout=10)
            pincode = getattr(settings, "default_pincode", "411001") or "411001"
            await session.post(
                "https://www.amazon.in/portal-migration/hz/glow/address-change?actionSource=glow",
                json={
                    "locationType": "LOCATION_INPUT",
                    "zipCode": str(pincode),
                    "storeContext": "generic",
                    "deviceType": "web",
                    "pageType": "Gateway",
                    "actionSource": "glow",
                },
                headers={**headers, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
                timeout=8,
            )
            logger.debug("Amazon warmup cookies collected: %s", list(session.cookies.keys()))
        except Exception as exc:
            logger.debug("Amazon warmup failed (continuing): %s", exc)

    # ── Single product extraction ──────────

    async def extract_product(self, url: str) -> ScraperResult:
        try:
            html = await self.fetch_page(url, auto_cache=False)
        except Exception as exc:
            return ScraperResult(success=False, error=str(exc))

        try:
            payload = self._parse_product_page(html, url)
            await self.cache_page(url, html)
            return ScraperResult(success=True, payload=payload, source="curl_cffi")
        except ScraperParseError as exc:
            return ScraperResult(success=False, error=str(exc))

    def _parse_product_page(self, html: str, url: str) -> ClientPayload:
        soup = self._soup(html)

        # ── Title ──
        title_el = soup.select_one("#productTitle")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            # Fallback: og:title meta
            og = soup.select_one('meta[property="og:title"]')
            title = og["content"].strip() if og and og.get("content") else ""
        if not title:
            raise ScraperParseError("Could not extract product title from Amazon.in page")

        # ── ASIN ──
        asin = self._extract_asin(url, soup)

        # ── Prices ──
        listed_price = self._extract_listed_price(soup)
        mrp = self._extract_mrp(soup)

        # ── Stock ──
        avail_el = soup.select_one("#availability")
        in_stock = True
        if avail_el:
            avail_text = avail_el.get_text(strip=True).lower()
            in_stock = "unavailable" not in avail_text and "out of stock" not in avail_text

        # ── Seller ──
        seller_name = self._extract_seller(soup)

        # ── Seller rating ──
        seller_rating = None
        rating_selector = f"#seller-rating-count-{asin}" if asin else None
        rating_el = soup.select_one("#sellerProfileTriggerId + span")
        if not rating_el and rating_selector:
            rating_el = soup.select_one(rating_selector)
        if rating_el:
            m = re.search(r"(\d+(?:\.\d+)?)", rating_el.get_text())
            if m:
                seller_rating = float(m.group(1))

        # ── Brand ──
        brand = None
        brand_el = soup.select_one("#bylineInfo")
        if brand_el:
            brand_text = brand_el.get_text(strip=True)
            brand = re.sub(r"^(Visit the |Brand:\s*)", "", brand_text).replace(" Store", "").strip()

        # ── Bank offers ──
        raw_bank_offers = self._extract_bank_offers(soup)

        # ── Coupons ──
        raw_coupons = []
        coupon_el = soup.select_one("#couponBadgeRegularVpc, #vpcButton")
        if coupon_el:
            raw_coupons.append(coupon_el.get_text(" ", strip=True))

        # ── Exchange offers ──
        raw_exchange_offers = []
        exchange_el = soup.select_one("#exchange-device-title, #tradeInButton_feature_div")
        if exchange_el:
            raw_exchange_offers.append(exchange_el.get_text(" ", strip=True))

        # ── Reviews ──
        recent_reviews = []
        for rev in soup.select('[data-hook="review-collapsed"] span, .review-text-content span'):
            txt = rev.get_text(strip=True)
            if len(txt) > 20:
                recent_reviews.append(txt[:500])
            if len(recent_reviews) >= 10:
                break

        # ── Variant specs from detail tables ──
        variant_specs = self._extract_specs(soup)

        # ── Images ──
        images: list[str] = []
        landing_img = soup.select_one("#landingImage, #imgBlkFront")
        if landing_img:
            dyn = landing_img.get("data-a-dynamic-image")
            if dyn:
                try:
                    images = list(json.loads(dyn).keys())[:6]
                except Exception:
                    pass
            if not images and landing_img.get("src"):
                images = [landing_img["src"]]

        return ClientPayload(
            retailer="amazon_in",
            url=url,
            title=title,
            brand=brand,
            sku=asin,
            listed_price=listed_price,
            mrp=mrp,
            image_url=images[0] if images else None,
            images=images,
            in_stock=in_stock,
            seller_name=seller_name,
            seller_rating=seller_rating,
            raw_bank_offers=raw_bank_offers,
            raw_coupons=raw_coupons,
            raw_exchange_offers=raw_exchange_offers,
            recent_reviews=recent_reviews,
            variant_specs=variant_specs,
            source="server",
        )

    # ── Helper methods ──────────────────────

    def _extract_asin(self, url: str, soup) -> str:
        m = re.search(r"/dp/([A-Z0-9]{10})", url)
        if m:
            return m.group(1)
        inp = soup.select_one('input[name="ASIN"]')
        if inp and inp.get("value"):
            return inp["value"]
        el = soup.select_one("[data-asin]")
        if el and el.get("data-asin"):
            return el["data-asin"]
        raise ScraperParseError("Could not extract ASIN from Amazon.in URL or page")

    def _extract_listed_price(self, soup) -> float | None:
        selectors = [
            "#corePrice_feature_div .a-price .a-offscreen",
            ".priceToPay .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "#apex_offerDisplay_desktop .a-price .a-offscreen",
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                p = parse_indian_price(el.get_text())
                if p and p > 0:
                    return p
        # Fallback: first .a-price-whole
        whole = soup.select_one("span.a-price-whole")
        if whole:
            return parse_indian_price(whole.get_text())
        return None

    def _extract_mrp(self, soup) -> float | None:
        selectors = [
            ".basisPrice .a-offscreen",
            ".a-price.a-text-price .a-offscreen",
            "#listPrice .a-offscreen",
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                p = parse_indian_price(el.get_text())
                if p and p > 0:
                    return p
        return None

    def _extract_seller(self, soup) -> str | None:
        for sel in [
            "#sellerProfileTriggerId",
            "#merchant-info a",
            "#tabular-buybox .tabular-buybox-text a",
        ]:
            el = soup.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                if name:
                    return name
        # Check "Ships from and sold by" text
        merchant_info = soup.select_one("#merchant-info")
        if merchant_info:
            text = merchant_info.get_text(strip=True)
            m = re.search(r"(?:sold by|Sold by)\s+(.+?)(?:\.|$)", text)
            if m:
                return m.group(1).strip()
        return None

    def _extract_bank_offers(self, soup) -> list[str]:
        offers: list[str] = []
        # Primary: soWidget / offers section
        for container_sel in [
            "#soWidget_feature_div",
            "#itembox-InstallmentCalculator",
            "#oferDisplayGroups",
            ".celwidget[data-csa-c-slot-id='OFFER_DISPLAY']",
        ]:
            container = soup.select_one(container_sel)
            if container:
                for item in container.select(".a-truncate-full, .a-list-item, .offer-text"):
                    txt = item.get_text(" ", strip=True)
                    if txt and len(txt) > 15 and any(
                        kw in txt.lower()
                        for kw in ["bank", "cashback", "discount", "emi", "no cost", "instant"]
                    ):
                        offers.append(txt[:800])
        # Fallback: any element mentioning "Bank Offer"
        if not offers:
            for el in soup.find_all(string=re.compile(r"Bank\s+Offer", re.I)):
                parent = el.find_parent("li") or el.find_parent("div")
                if parent:
                    txt = parent.get_text(" ", strip=True)
                    if len(txt) > 15:
                        offers.append(txt[:800])
        return offers[:15]

    def _extract_specs(self, soup) -> dict:
        specs: dict = {}
        # Technical Details table
        for table in soup.select("#productDetails_techSpec_section_1, #productDetails_detailBullets_sections1"):
            for row in table.select("tr"):
                cells = row.select("th, td")
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True).lower().replace(" ", "_")
                    val = cells[1].get_text(strip=True)
                    specs[key] = val
        # Feature bullets
        for li in soup.select("#feature-bullets .a-list-item"):
            txt = li.get_text(strip=True)
            ram_m = re.search(r"(\d+)\s*GB\s*RAM", txt, re.I)
            if ram_m:
                specs["ram_gb"] = int(ram_m.group(1))
            storage_m = re.search(r"(\d+)\s*(?:GB|TB)\s*(?:ROM|Storage|SSD)", txt, re.I)
            if storage_m:
                specs["storage_gb"] = int(storage_m.group(1))
        return specs

    # ── Cross-store search ──────────────────

    async def search_products(self, query: str) -> list[SearchHit]:
        search_url = f"https://www.amazon.in/s?k={quote_plus(query)}"
        try:
            html = await self.fetch_page(search_url)
        except Exception as exc:
            logger.warning("Amazon search failed: %s", exc)
            return []

        soup = self._soup(html)
        results: list[SearchHit] = []
        for card in soup.select('[data-component-type="s-search-result"]'):
            asin = card.get("data-asin", "")
            if not asin:
                continue
            title_el = card.select_one("h2 a span")
            title = title_el.get_text(strip=True) if title_el else ""
            price_el = card.select_one(".a-price .a-offscreen")
            price = parse_indian_price(price_el.get_text()) if price_el else None
            img_el = card.select_one("img.s-image")
            thumb = img_el.get("src") if img_el else None
            if title:
                results.append(SearchHit(
                    retailer="amazon_in",
                    title=title,
                    url=f"https://www.amazon.in/dp/{asin}",
                    price=price,
                    sku=asin,
                    thumbnail_url=thumb,
                ))
            if len(results) >= 10:
                break
        return results

