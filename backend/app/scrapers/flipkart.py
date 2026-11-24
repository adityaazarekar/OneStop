"""Flipkart product page scraper — structured JSON + selectolax fast parser, no fake data."""
from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import parse_qs, quote_plus, urlparse

from app.schemas.extraction import ClientPayload, DynamicSellerInfo, ScraperResult, SearchHit
from app.scrapers.base_scraper import (
    BaseScraper,
    ScraperParseError,
    parse_indian_price,
)
from app.services.product_matcher import sanitize_seller_name

logger = logging.getLogger(__name__)

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I,
)
_INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*(?:</script>|$)",
    re.S,
)


class FlipkartScraper(BaseScraper):
    retailer = "flipkart"

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
        # ── 1. Fast structured extraction (JSON-LD + window.__INITIAL_STATE__) without BS4 ──
        jsonld = self._extract_jsonld_from_html(html)
        state_data = self._extract_initial_state(html)

        # Only build Lexbor tree when needed (for DOM fallbacks / specs when JSON lacks them)
        tree = None
        need_tree = not (jsonld and state_data.get("listed_price") and state_data.get("seller_name"))
        if need_tree or not state_data.get("specs"):
            tree = self._tree(html)

        # ── Title ──
        title = ""
        if jsonld:
            title = jsonld.get("name", "")
        if not title:
            title = state_data.get("title") or ""
        if not title:
            if tree is None:
                tree = self._tree(html)
            for sel in ["span.VU-ZEz", "h1 span", "h1"]:
                el = tree.css_first(sel)
                if el and el.text(strip=True):
                    title = el.text(strip=True)
                    break
        if not title:
            if tree is None:
                tree = self._tree(html)
            og = tree.css_first('meta[property="og:title"]')
            title = (og.attributes.get("content") or "").strip() if og else ""
        if not title:
            raise ScraperParseError("Could not extract product title from Flipkart page")

        # ── FSN / Product ID ──
        fsn = state_data.get("fsn") or self._extract_fsn(url, html, tree)

        # ── Prices (JSON-LD & __INITIAL_STATE__ preferred over CSS) ──
        listed_price = None
        if jsonld:
            offers = jsonld.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                listed_price = self._safe_float(offers.get("price"))

        if listed_price is None:
            listed_price = state_data.get("listed_price")
        if listed_price is None:
            if tree is None:
                tree = self._tree(html)
            listed_price = self._extract_price_from_dom(tree)

        mrp = state_data.get("mrp")
        if mrp is None and jsonld:
            offers = jsonld.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                mrp = self._safe_float(offers.get("highPrice"))
        if mrp is None:
            if tree is None:
                tree = self._tree(html)
            mrp = self._extract_mrp(tree)

        # ── Stock ──
        in_stock = True
        if jsonld:
            avail = jsonld.get("offers", {})
            if isinstance(avail, list) and avail:
                avail = avail[0]
            avail_str = str(avail.get("availability", "") if isinstance(avail, dict) else "").lower()
            in_stock = "outofstock" not in avail_str
        else:
            low_sample = html[:200_000].lower()
            if "currently unavailable" in low_sample or "coming soon" in low_sample:
                in_stock = False

        # ── Brand ──
        brand = None
        if jsonld and isinstance(jsonld.get("brand"), dict):
            brand = jsonld["brand"].get("name")
        elif jsonld and isinstance(jsonld.get("brand"), str):
            brand = jsonld.get("brand")
        if not brand:
            brand = state_data.get("brand")
        if not brand:
            if tree is None:
                tree = self._tree(html)
            brand = self._extract_brand_from_specs(tree)

        # ── Images ──
        images: list[str] = []
        if jsonld and jsonld.get("image"):
            raw_imgs = jsonld["image"]
            if isinstance(raw_imgs, str):
                images = [raw_imgs]
            elif isinstance(raw_imgs, list):
                images = [str(x) for x in raw_imgs if isinstance(x, str) and x.startswith("http")][:10]
        if not images and state_data.get("images"):
            images = state_data["images"][:10]

        # ── Seller & Other Sellers (prefer __INITIAL_STATE__ over CSS) ──
        seller_name = state_data.get("seller_name")
        if not seller_name:
            if tree is None:
                tree = self._tree(html)
            seller_name = self._extract_seller(tree)
        seller_name = sanitize_seller_name(seller_name, url, "flipkart")

        seller_rating = state_data.get("seller_rating")
        if seller_rating is None:
            if tree is None:
                tree = self._tree(html)
            seller_rating = self._extract_seller_rating(tree)

        sellers: list[DynamicSellerInfo] = list(state_data.get("sellers") or [])
        if not sellers and seller_name:
            sellers.append(
                DynamicSellerInfo(
                    seller_name=seller_name,
                    price=listed_price,
                    rating=seller_rating,
                    fulfillment="Flipkart Assured" if state_data.get("is_fa") else "Marketplace Dispatch",
                    return_policy="7 Days Replacement",
                    is_buy_box_winner=True,
                )
            )

        # ── Bank offers, coupons, exchange (from __INITIAL_STATE__ + selectolax DOM fallback) ──
        raw_bank_offers = list(state_data.get("bank_offers") or [])
        raw_coupons = list(state_data.get("coupons") or [])
        if not raw_bank_offers:
            if tree is None:
                tree = self._tree(html)
            raw_bank_offers = self._extract_bank_offers(tree)
        if not raw_coupons:
            if tree is None:
                tree = self._tree(html)
            raw_coupons = self._extract_coupons(tree)

        raw_exchange_offers: list[str] = []

        # ── Reviews ──
        recent_reviews: list[str] = []
        if jsonld and isinstance(jsonld.get("review"), list):
            for rv in jsonld["review"][:10]:
                if isinstance(rv, dict) and rv.get("reviewBody"):
                    recent_reviews.append(str(rv["reviewBody"]).strip()[:500])
        if not recent_reviews:
            if tree is None:
                tree = self._tree(html)
            recent_reviews = self._extract_reviews(tree)

        # ── Specs ──
        variant_specs = state_data.get("specs") or {}
        if not variant_specs:
            variant_specs = self._extract_specs_fast(title, tree)

        return ClientPayload(
            retailer="flipkart",
            url=url,
            title=title,
            brand=brand,
            sku=fsn,
            listed_price=listed_price,
            mrp=mrp,
            image_url=images[0] if images else None,
            images=images,
            in_stock=in_stock,
            seller_name=seller_name,
            seller_rating=seller_rating,
            sellers=sellers,
            raw_bank_offers=raw_bank_offers,
            raw_coupons=raw_coupons,
            raw_exchange_offers=raw_exchange_offers,
            recent_reviews=recent_reviews,
            variant_specs=variant_specs,
            source="server",
        )

    # ── Helpers ─────────────────────────────

    def _extract_jsonld_from_html(self, html: str) -> dict | None:
        for raw_json in _JSONLD_RE.findall(html):
            try:
                data = json.loads(raw_json.strip())
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            return item
                elif isinstance(data, dict) and data.get("@type") == "Product":
                    return data
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def _extract_initial_state(self, html: str) -> dict:
        """Extract structured price, MRP, seller, rating, images, offers, and sellers from window.__INITIAL_STATE__."""
        out: dict = {"images": [], "sellers": [], "bank_offers": [], "coupons": []}
        m = _INITIAL_STATE_RE.search(html)
        if not m:
            m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", html, re.S)
        if not m:
            return out
        try:
            state = json.loads(m.group(1))
        except Exception:
            return out

        mws = state.get("multiWidgetState") or {}
        pdr = mws.get("pageDataResponse") or {}
        pc = pdr.get("pageContext") or {}
        if pc.get("productId"):
            out["fsn"] = str(pc["productId"])
        out["is_fa"] = bool(pc.get("isFaAvailable") or pc.get("faAvailable"))

        ppd = (
            pc.get("fdpEventTracking", {})
            .get("events", {})
            .get("psi", {})
            .get("ppd", {})
        )
        if isinstance(ppd, dict):
            lp = self._safe_float(ppd.get("finalPrice") or ppd.get("fsp"))
            mrp = self._safe_float(ppd.get("mrp"))
            if lp and lp > 0:
                out["listed_price"] = lp
            if mrp and mrp > 0:
                out["mrp"] = mrp

        slots = (mws.get("widgetsData") or {}).get("slots") or []
        for sl in slots:
            if not isinstance(sl, dict):
                continue
            wd = (sl.get("slotData") or {}).get("widget") or sl.get("widget") or {}
            if not isinstance(wd, dict):
                continue
            vtype = str(wd.get("viewType") or "")
            dls = (wd.get("data") or {}).get("dlsData") or {}
            if not isinstance(dls, dict):
                continue

            if "productTitle" in vtype and not out.get("title"):
                ce = (dls.get("customEllipsisData_0") or {}).get("value") or {}
                if ce.get("prependingText"):
                    out["title"] = str(ce["prependingText"]).strip()

            if "pricing_price_summary" in vtype:
                pdesc = (dls.get("price_description_1") or {}).get("value") or {}
                if not out.get("mrp"):
                    mrp_txt = ((pdesc.get("label_1") or {}).get("value") or {}).get("text")
                    if mrp_txt:
                        p = parse_indian_price(str(mrp_txt))
                        if p and p > 0:
                            out["mrp"] = p
                if not out.get("listed_price"):
                    l2 = (pdesc.get("label_2") or {}).get("value") or {}
                    lp_txt = ((l2.get("UNLOCKED") or l2.get("LOCKED") or {}).get("value") or {}).get("text")
                    if lp_txt:
                        p = parse_indian_price(str(lp_txt))
                        if p and p > 0:
                            out["listed_price"] = p

            if "additional_benefit" in vtype:
                for item in ((dls.get("carouselData_0") or {}).get("value") or []):
                    if not isinstance(item, dict):
                        continue
                    tr = ((item.get("value") or {}).get("trackerData_0") or {}).get("tracking") or {}
                    ct = str(tr.get("contentTitle") or "").strip()
                    if ct and len(ct) > 8:
                        if "coupon" in ct.lower():
                            out["coupons"].append(ct[:600])
                        else:
                            out["bank_offers"].append(ct[:800])

            if "delivery_widget" in vtype:
                sv2 = (dls.get("default_fk_pp_delivery_widget_seller_v2_0") or {}).get("value") or {}
                s_txt = ((sv2.get("label_0") or {}).get("value") or {}).get("text")
                if s_txt:
                    cleaned = re.sub(r"^Seller\s*:\s*", "", str(s_txt), flags=re.I).strip()
                    if cleaned:
                        out["seller_name"] = cleaned
                r_txt = ((sv2.get("label_1") or {}).get("value") or {}).get("text")
                if r_txt:
                    rv = self._safe_float(r_txt)
                    if rv and 1.0 <= rv <= 5.0:
                        out["seller_rating"] = rv

                box3_dls = (
                    (dls.get("box_3") or {})
                    .get("action", {})
                    .get("params", {})
                    .get("widgetData", {})
                    .get("data", {})
                    .get("dlsData", {})
                )
                if isinstance(box3_dls, dict):
                    stitle = (box3_dls.get("default_fk_pp_atlas_seller_details_seller_title_0") or {}).get("value") or {}
                    sname = ((stitle.get("label_0") or {}).get("value") or {}).get("text")
                    srat = ((stitle.get("label_4") or {}).get("value") or {}).get("text")
                    if sname and not out.get("seller_name"):
                        out["seller_name"] = str(sname).strip()
                    if srat and not out.get("seller_rating"):
                        rv = self._safe_float(srat)
                        if rv and 1.0 <= rv <= 5.0:
                            out["seller_rating"] = rv

            if "seller" in vtype.lower():
                raw_sellers = (wd.get("data") or {}).get("sellers") or dls.get("sellers") or []
                if isinstance(raw_sellers, list):
                    for idx, s_obj in enumerate(raw_sellers):
                        if not isinstance(s_obj, dict):
                            continue
                        sn = s_obj.get("sellerName") or (s_obj.get("sellerInfo") or {}).get("name")
                        if not sn:
                            continue
                        sp = self._safe_float(
                            (s_obj.get("pricing") or {}).get("finalPrice", {}).get("value")
                            or s_obj.get("price")
                        )
                        sr = self._safe_float((s_obj.get("sellerRating") or {}).get("average"))
                        out["sellers"].append(
                            DynamicSellerInfo(
                                seller_name=str(sn).strip(),
                                price=sp,
                                rating=sr,
                                is_buy_box_winner=(idx == 0),
                            )
                        )

        return out

    def _extract_fsn(self, url: str, html: str, tree=None) -> str:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "pid" in qs:
            return qs["pid"][0]
        path_parts = parsed.path.rstrip("/").split("/")
        if path_parts and re.match(r"^[A-Z0-9]{16}$", path_parts[-1]):
            return path_parts[-1]
        if tree is not None:
            canonical = tree.css_first('link[rel="canonical"]')
            if canonical and canonical.attributes.get("href"):
                m = re.search(r"pid=([A-Z0-9]+)", canonical.attributes["href"])
                if m:
                    return m.group(1)
        m = re.search(r'"productId"\s*:\s*"([A-Z0-9]+)"', html)
        if m:
            return m.group(1)
        return "UNKNOWN_FSN"

    def _extract_price_from_dom(self, tree) -> float | None:
        for sel in ["div.Nx9bqj", "div._30jeq3", "div._16Jk6d"]:
            el = tree.css_first(sel)
            if el:
                p = parse_indian_price(el.text())
                if p and p > 0:
                    return p
        return None

    def _extract_mrp(self, tree) -> float | None:
        for sel in ["div.yRaY8j", "div._3I9_wc", "div.hGSR34"]:
            el = tree.css_first(sel)
            if el:
                p = parse_indian_price(el.text())
                if p and p > 0:
                    return p
        return None

    def _extract_brand_from_specs(self, tree) -> str | None:
        for row in tree.css("table tr, div._14cfVK div"):
            text = row.text(deep=True, separator=" ", strip=True).lower()
            if "brand" in text:
                cells = row.css("td, li, span")
                if len(cells) >= 2:
                    return cells[-1].text(strip=True)
        return None

    def _extract_seller(self, tree) -> str | None:
        for sel in ["div#sellerName span span", "div._1RLviY span"]:
            el = tree.css_first(sel)
            if el:
                name = sanitize_seller_name(el.text(strip=True), "", "flipkart")
                if name:
                    return name
        return None

    def _extract_seller_rating(self, tree) -> float | None:
        for el in tree.css("div._3LWZlK, span._1Y_Nkh"):
            m = re.search(r"(\d+\.?\d*)", el.text())
            if m:
                val = float(m.group(1))
                if 1.0 <= val <= 5.0:
                    return val
        return None

    def _extract_bank_offers(self, tree) -> list[str]:
        offers: list[str] = []
        bank_keywords = ("hdfc", "icici", "axis", "sbi", "kotak", "amex", "no cost emi", "bank offer")
        for li in tree.css("li"):
            txt = li.text(deep=True, separator=" ", strip=True)
            low = txt.lower()
            if len(txt) > 15 and any(kw in low for kw in bank_keywords) and ("₹" in txt or "off" in low):
                if txt not in offers:
                    offers.append(txt[:800])
                if len(offers) >= 15:
                    break
        return offers[:15]

    def _extract_coupons(self, tree) -> list[str]:
        coupons: list[str] = []
        for li in tree.css("li"):
            txt = li.text(deep=True, separator=" ", strip=True)
            if len(txt) > 10 and "₹" in txt and re.search(r"coupon|extra\s+off", txt, re.I):
                coupons.append(txt[:600])
                if len(coupons) >= 5:
                    break
        return coupons

    def _extract_reviews(self, tree) -> list[str]:
        reviews: list[str] = []
        for rev in tree.css("div.t-ZTKy, div._6K-7Co, p._2-N8zT"):
            txt = rev.text(strip=True)
            if len(txt) > 20:
                reviews.append(txt[:500])
            if len(reviews) >= 10:
                break
        return reviews

    def _extract_specs_fast(self, title: str, tree=None) -> dict:
        specs: dict = {}
        if tree is not None:
            for table in tree.css("table._14cfVK, div._3Rrcbo"):
                for row in table.css("tr"):
                    cells = row.css("td")
                    if len(cells) >= 2:
                        key = cells[0].text(strip=True).lower().replace(" ", "_")
                        val = cells[1].text(strip=True)
                        specs[key] = val
        ram_m = re.search(r"(\d+)\s*GB\s*RAM", title, re.I)
        if ram_m:
            specs.setdefault("ram_gb", int(ram_m.group(1)))
        storage_m = re.search(r"(\d+)\s*(?:GB|TB)\b", title, re.I)
        if storage_m:
            val = int(storage_m.group(1))
            if "tb" in storage_m.group(0).lower():
                val *= 1024
            specs.setdefault("storage_gb", val)
        return specs

    @staticmethod
    def _safe_float(val) -> float | None:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    # ── Search ──────────────────────────────

    async def search_products(self, query: str) -> list[SearchHit]:
        url = f"https://www.flipkart.com/search?q={quote_plus(query)}"
        try:
            html = await self.fetch_page(url)
        except Exception as exc:
            logger.warning("Flipkart search failed: %s", exc)
            return []

        tree = self._tree(html)
        results: list[SearchHit] = []
        for card in tree.css("div._1AtVbE a[href*='/p/'], a.CGtC98"):
            href = card.attributes.get("href", "") or ""
            if not href.startswith("http"):
                href = f"https://www.flipkart.com{href}"
            title_el = card.css_first("div._4rR01T, a.s1Q9rs, div.KzDlHZ")
            title = title_el.text(strip=True) if title_el else card.text(strip=True)[:100]
            price_el = card.css_first("div._30jeq3, div.Nx9bqj")
            price = parse_indian_price(price_el.text()) if price_el else None
            img_el = card.css_first("img._396cs4, img.DByuf4")
            thumb = img_el.attributes.get("src") if img_el else None
            pid_m = re.search(r"pid=([A-Z0-9]+)", href)
            sku = pid_m.group(1) if pid_m else href.split("/")[-1][:20]
            if title and len(title) > 5:
                results.append(SearchHit(
                    retailer="flipkart",
                    title=title[:200],
                    url=href,
                    price=price,
                    sku=sku,
                    thumbnail_url=thumb,
                ))
            if len(results) >= 10:
                break
        return results
