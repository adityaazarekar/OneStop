"""Amazon.in product page scraper — live extraction using selectolax + curl_cffi, no fake data."""
from __future__ import annotations

import json
import logging
import re
import time
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
        t_start = time.perf_counter()
        cached = await self.get_cached_payload(url)
        if cached is not None:
            ms = round((time.perf_counter() - t_start) * 1000, 1)
            return ScraperResult(success=True, payload=cached, source="cache", cached=True, fetch_ms=0.0, parse_ms=ms)

        t_fetch0 = time.perf_counter()
        try:
            html = await self.fetch_page(url, auto_cache=False)
        except Exception as exc:
            return ScraperResult(success=False, error=str(exc))
        fetch_ms = round((time.perf_counter() - t_fetch0) * 1000, 1)

        t_parse0 = time.perf_counter()
        try:
            payload = self._parse_product_page(html, url)
            parse_ms = round((time.perf_counter() - t_parse0) * 1000, 1)
            await self.cache_page(url, html)
            await self.cache_payload(url, payload)
            return ScraperResult(
                success=True,
                payload=payload,
                source="curl_cffi",
                fetch_ms=fetch_ms,
                parse_ms=parse_ms,
            )
        except ScraperParseError as exc:
            return ScraperResult(success=False, error=str(exc))

    def _parse_product_page(self, html: str, url: str) -> ClientPayload:
        tree = self._tree(html)

        # ── Title ──
        title_el = tree.css_first("#productTitle")
        title = title_el.text(strip=True) if title_el else ""
        if not title:
            og = tree.css_first('meta[property="og:title"]')
            title = (og.attributes.get("content") or "").strip() if og else ""
        if not title:
            raise ScraperParseError("Could not extract product title from Amazon.in page")

        # ── ASIN ──
        asin = self._extract_asin(url, tree)

        # ── Prices ──
        listed_price = self._extract_listed_price(tree)
        mrp = self._extract_mrp(tree)

        # ── Stock ──
        avail_el = tree.css_first("#availability")
        in_stock = True
        if avail_el:
            avail_text = avail_el.text(strip=True).lower()
            in_stock = "unavailable" not in avail_text and "out of stock" not in avail_text

        # ── Seller ──
        seller_name = self._extract_seller(tree)

        # ── Seller rating ──
        seller_rating = None
        rating_selector = f"#seller-rating-count-{asin}" if asin else None
        rating_el = tree.css_first("#sellerProfileTriggerId + span")
        if not rating_el and rating_selector:
            rating_el = tree.css_first(rating_selector)
        if rating_el:
            m = re.search(r"(\d+(?:\.\d+)?)", rating_el.text())
            if m:
                seller_rating = float(m.group(1))

        # ── Brand ──
        brand = None
        brand_el = tree.css_first("#bylineInfo")
        if brand_el:
            brand_text = brand_el.text(strip=True)
            brand = re.sub(r"^(Visit the |Brand:\s*)", "", brand_text).replace(" Store", "").strip()

        # ── Bank offers ──
        raw_bank_offers = self._extract_bank_offers(tree)

        # ── Coupons ──
        raw_coupons = []
        coupon_el = tree.css_first("#couponBadgeRegularVpc, #vpcButton")
        if coupon_el:
            txt = coupon_el.text(deep=True, separator=" ", strip=True)
            if txt:
                raw_coupons.append(txt)

        # ── Exchange offers ──
        raw_exchange_offers = []
        exchange_el = tree.css_first("#exchange-device-title, #tradeInButton_feature_div")
        if exchange_el:
            txt = exchange_el.text(deep=True, separator=" ", strip=True)
            if txt:
                raw_exchange_offers.append(txt)

        # ── Reviews ──
        recent_reviews = []
        for rev in tree.css('[data-hook="review-collapsed"] span, .review-text-content span'):
            txt = rev.text(strip=True)
            if len(txt) > 20:
                recent_reviews.append(txt[:500])
            if len(recent_reviews) >= 10:
                break

        # ── Variant specs from detail tables ──
        variant_specs = self._extract_specs(tree)

        # ── Images ──
        images: list[str] = []
        landing_img = tree.css_first("#landingImage, #imgBlkFront")
        if landing_img:
            dyn = landing_img.attributes.get("data-a-dynamic-image")
            if dyn:
                try:
                    images = list(json.loads(dyn).keys())[:6]
                except Exception:
                    pass
            if not images and landing_img.attributes.get("src"):
                images = [landing_img.attributes["src"]]

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

    def _extract_asin(self, url: str, tree) -> str:
        m = re.search(r"/dp/([A-Z0-9]{10})", url)
        if m:
            return m.group(1)
        inp = tree.css_first('input[name="ASIN"]')
        if inp and inp.attributes.get("value"):
            return inp.attributes["value"]
        el = tree.css_first("[data-asin]")
        if el and el.attributes.get("data-asin"):
            return el.attributes["data-asin"]
        raise ScraperParseError("Could not extract ASIN from Amazon.in URL or page")

    def _extract_listed_price(self, tree) -> float | None:
        selectors = [
            "#corePrice_feature_div .a-price .a-offscreen",
            ".priceToPay .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "#apex_offerDisplay_desktop .a-price .a-offscreen",
        ]
        for sel in selectors:
            el = tree.css_first(sel)
            if el:
                p = parse_indian_price(el.text())
                if p and p > 0:
                    return p
        whole = tree.css_first("span.a-price-whole")
        if whole:
            return parse_indian_price(whole.text())
        return None

    def _extract_mrp(self, tree) -> float | None:
        selectors = [
            ".basisPrice .a-offscreen",
            ".a-price.a-text-price .a-offscreen",
            "#listPrice .a-offscreen",
        ]
        for sel in selectors:
            el = tree.css_first(sel)
            if el:
                p = parse_indian_price(el.text())
                if p and p > 0:
                    return p
        return None

    def _extract_seller(self, tree) -> str | None:
        for sel in [
            "#sellerProfileTriggerId",
            "#merchant-info a",
            "#tabular-buybox .tabular-buybox-text a",
        ]:
            el = tree.css_first(sel)
            if el:
                name = el.text(strip=True)
                if name:
                    return name
        merchant_info = tree.css_first("#merchant-info")
        if merchant_info:
            text = merchant_info.text(strip=True)
            m = re.search(r"(?:sold by|Sold by)\s+(.+?)(?:\.|$)", text)
            if m:
                return m.group(1).strip()
        return None

    def _extract_bank_offers(self, tree) -> list[str]:
        offers: list[str] = []
        for container_sel in [
            "#soWidget_feature_div",
            "#itembox-InstallmentCalculator",
            "#oferDisplayGroups",
            ".celwidget[data-csa-c-slot-id='OFFER_DISPLAY']",
        ]:
            container = tree.css_first(container_sel)
            if container:
                for item in container.css(".a-truncate-full, .a-list-item, .offer-text"):
                    txt = item.text(deep=True, separator=" ", strip=True)
                    if txt and len(txt) > 15 and any(
                        kw in txt.lower()
                        for kw in ["bank", "cashback", "discount", "emi", "no cost", "instant"]
                    ):
                        offers.append(txt[:800])
        if not offers:
            for item in tree.css("li"):
                txt = item.text(deep=True, separator=" ", strip=True)
                if len(txt) > 15 and re.search(r"Bank\s+Offer", txt, re.I):
                    offers.append(txt[:800])
                    if len(offers) >= 15:
                        break
        return offers[:15]

    def _extract_specs(self, tree) -> dict:
        specs: dict = {}
        selected_attrs: dict = {}

        for sel in (
            "#inline-twister-expanded-dimension-text-color_name",
            "#variation_color_name .selection",
            "tr.po-color td.a-span9 span",
        ):
            el = tree.css_first(sel)
            if el:
                val = el.text(strip=True)
                if val and val.lower() not in ("select", "select color", "select colour"):
                    specs["color"] = val
                    selected_attrs["color"] = {"value": val, "source": "dom_selected", "span": val}
                    break

        for sel in (
            "#inline-twister-expanded-dimension-text-size_name",
            "#variation_size_name .selection",
            "select#native_dropdown_selected_size_name option[selected]",
            "tr.po-memory_storage_capacity td.a-span9 span",
            "tr.po-size td.a-span9 span",
        ):
            el = tree.css_first(sel)
            if el:
                val = el.text(strip=True)
                if val and val.lower() not in ("select", "select size"):
                    stor_m = re.search(r"^(\d+)\s*(GB|TB)$", val, re.I)
                    if stor_m:
                        gb = int(stor_m.group(1)) * (1024 if stor_m.group(2).upper() == "TB" else 1)
                        specs["storage_gb"] = gb
                        selected_attrs["storage"] = {"value": val, "source": "dom_selected", "span": val}
                    else:
                        specs["size"] = val
                        selected_attrs["size"] = {"value": val, "source": "dom_selected", "span": val}
                    break
                elif val and "select" in val.lower():
                    selected_attrs["size"] = {"value": None, "source": "unknown", "span": val}

        for table in tree.css("#productDetails_techSpec_section_1, #productDetails_detailBullets_sections1"):
            for row in table.css("tr"):
                cells = row.css("th, td")
                if len(cells) >= 2:
                    key = cells[0].text(strip=True).lower().replace(" ", "_")
                    val = cells[1].text(strip=True)
                    specs[key] = val
        for li in tree.css("#feature-bullets .a-list-item"):
            txt = li.text(strip=True)
            ram_m = re.search(r"(\d+)\s*GB\s*RAM", txt, re.I)
            if ram_m:
                specs["ram_gb"] = int(ram_m.group(1))
            storage_m = re.search(r"(\d+)\s*(?:GB|TB)\s*(?:ROM|Storage|SSD)", txt, re.I)
            if storage_m and "storage_gb" not in specs:
                specs["storage_gb"] = int(storage_m.group(1))
        if selected_attrs:
            specs["selected_attrs"] = selected_attrs
        return specs

    # ── Cross-store search ──────────────────

    async def search_products(self, query: str) -> list[SearchHit]:
        search_url = f"https://www.amazon.in/s?k={quote_plus(query)}"
        try:
            html = await self.fetch_page(search_url)
        except Exception as exc:
            logger.warning("Amazon search failed: %s", exc)
            return []

        tree = self._tree(html)
        results: list[SearchHit] = []
        for card in tree.css('[data-component-type="s-search-result"]'):
            asin = card.attributes.get("data-asin", "") or ""
            if not asin:
                continue
            title_el = card.css_first("h2 a span") or card.css_first("h2 span")
            title = title_el.text(strip=True) if title_el else ""
            price_el = card.css_first(".a-price .a-offscreen")
            price = parse_indian_price(price_el.text()) if price_el else None
            img_el = card.css_first("img.s-image")
            thumb = img_el.attributes.get("src") if img_el else None
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
