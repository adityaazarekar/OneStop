"""Flipkart product page scraper — live extraction, no fake data."""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote_plus, urlparse, parse_qs

from app.schemas.extraction import ClientPayload, DynamicSellerInfo, ScraperResult, SearchHit
from app.scrapers.base_scraper import (
    BaseScraper,
    ScraperParseError,
    parse_indian_price,
)
from app.services.product_matcher import sanitize_seller_name

logger = logging.getLogger(__name__)


class FlipkartScraper(BaseScraper):
    retailer = "flipkart"

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

        # ── Structured data first: JSON-LD + window.__INITIAL_STATE__ ──
        jsonld = self._extract_jsonld(soup)
        state_data = self._extract_initial_state(html)

        # ── Title ──
        title = ""
        if jsonld:
            title = jsonld.get("name", "")
        if not title:
            title = state_data.get("title") or ""
        if not title:
            for sel in ["span.VU-ZEz", "h1 span", "h1"]:
                el = soup.select_one(sel)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break
        if not title:
            og = soup.select_one('meta[property="og:title"]')
            title = og["content"].strip() if og and og.get("content") else ""
        if not title:
            raise ScraperParseError("Could not extract product title from Flipkart page")

        # ── FSN / Product ID ──
        fsn = state_data.get("fsn") or self._extract_fsn(url, soup)

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
            listed_price = self._extract_price_from_dom(soup)

        mrp = state_data.get("mrp")
        if mrp is None and jsonld:
            offers = jsonld.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                mrp = self._safe_float(offers.get("highPrice"))
        if mrp is None:
            mrp = self._extract_mrp(soup)

        # ── Stock ──
        in_stock = True
        if jsonld:
            avail = jsonld.get("offers", {})
            if isinstance(avail, list) and avail:
                avail = avail[0]
            avail_str = str(avail.get("availability", "") if isinstance(avail, dict) else "").lower()
            in_stock = "outofstock" not in avail_str
        else:
            body_text = soup.get_text().lower()
            if "currently unavailable" in body_text or "coming soon" in body_text:
                in_stock = False

        # ── Brand ──
        brand = None
        if jsonld and isinstance(jsonld.get("brand"), dict):
            brand = jsonld["brand"].get("name")
        elif jsonld and isinstance(jsonld.get("brand"), str):
            brand = jsonld.get("brand")
        if not brand:
            brand = state_data.get("brand") or self._extract_brand_from_specs(soup)

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
        seller_name = state_data.get("seller_name") or self._extract_seller(soup)
        seller_name = sanitize_seller_name(seller_name, url, "flipkart")
        seller_rating = state_data.get("seller_rating") or self._extract_seller_rating(soup)

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

        # ── Bank offers ──
        raw_bank_offers = self._extract_bank_offers(soup)

        # ── Coupons ──
        raw_coupons = self._extract_coupons(soup)

        # ── Exchange offers ──
        raw_exchange_offers = []
        for el in soup.find_all(string=re.compile(r"exchange", re.I)):
            parent = el.find_parent("div") or el.find_parent("li")
            if parent:
                txt = parent.get_text(" ", strip=True)
                if "₹" in txt and len(txt) > 20:
                    raw_exchange_offers.append(txt[:600])
                    break

        # ── Reviews ──
        recent_reviews = self._extract_reviews(soup)
        if not recent_reviews and jsonld and isinstance(jsonld.get("review"), list):
            for rv in jsonld["review"][:10]:
                if isinstance(rv, dict) and rv.get("reviewBody"):
                    recent_reviews.append(str(rv["reviewBody"]).strip()[:500])

        # ── Specs ──
        variant_specs = self._extract_specs(soup)

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

    def _extract_initial_state(self, html: str) -> dict:
        """Extract structured price, MRP, seller, rating, images, and sellers from window.__INITIAL_STATE__."""
        out: dict = {"images": [], "sellers": []}
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>", html, re.S)
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

        # Price & MRP from fdpEventTracking.events.psi.ppd
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

        # Walk slots for ATLAS widgets (pricing summary, title, seller, other sellers)
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

                # Seller details bottomsheet modal inside box_3
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

            # Check legacy/alternate seller list widgets if present
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

    def _extract_fsn(self, url: str, soup) -> str:
        # From URL query param pid=
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "pid" in qs:
            return qs["pid"][0]
        # From URL path (last path segment often contains FSN)
        path_parts = parsed.path.rstrip("/").split("/")
        if path_parts:
            last = path_parts[-1]
            if re.match(r"^[A-Z0-9]{16}$", last):
                return last
        # From meta canonical
        canonical = soup.select_one('link[rel="canonical"]')
        if canonical and canonical.get("href"):
            m = re.search(r"pid=([A-Z0-9]+)", canonical["href"])
            if m:
                return m.group(1)
        # From page script data
        for script in soup.select("script"):
            txt = script.string or ""
            m = re.search(r'"productId"\s*:\s*"([A-Z0-9]+)"', txt)
            if m:
                return m.group(1)
        return "UNKNOWN_FSN"

    def _extract_price_from_dom(self, soup) -> float | None:
        # Flipkart price selectors (class names change frequently)
        for sel in ["div.Nx9bqj", "div._30jeq3", "div._16Jk6d"]:
            el = soup.select_one(sel)
            if el:
                p = parse_indian_price(el.get_text())
                if p and p > 0:
                    return p
        # Pattern: find elements starting with ₹ that look like prices
        for el in soup.find_all(string=re.compile(r"₹[\d,]+")):
            p = parse_indian_price(el.strip())
            if p and p > 100:
                return p
        return None

    def _extract_mrp(self, soup) -> float | None:
        for sel in ["div.yRaY8j", "div._3I9_wc", "div.hGSR34"]:
            el = soup.select_one(sel)
            if el:
                p = parse_indian_price(el.get_text())
                if p and p > 0:
                    return p
        return None

    def _extract_brand_from_specs(self, soup) -> str | None:
        for row in soup.select("table tr, div._14cfVK div"):
            text = row.get_text(" ", strip=True).lower()
            if "brand" in text:
                cells = row.select("td, li, span")
                if len(cells) >= 2:
                    return cells[-1].get_text(strip=True)
        return None

    def _extract_seller(self, soup) -> str | None:
        for sel in ["div#sellerName span span", "div._1RLviY span"]:
            el = soup.select_one(sel)
            if el:
                name = sanitize_seller_name(el.get_text(strip=True), "", "flipkart")
                if name:
                    return name
        for el in soup.find_all(string=re.compile(r"Sold\s+by", re.I)):
            parent = el.find_parent("div")
            if parent:
                links = parent.select("a, span")
                for link in links:
                    raw = link.get_text(strip=True)
                    if raw and raw.lower() != "sold by":
                        name = sanitize_seller_name(raw, "", "flipkart")
                        if name:
                            return name
        return None

    def _extract_seller_rating(self, soup) -> float | None:
        for el in soup.select("div._3LWZlK, span._1Y_Nkh"):
            m = re.search(r"(\d+\.?\d*)", el.get_text())
            if m:
                val = float(m.group(1))
                if 1.0 <= val <= 5.0:
                    return val
        return None

    def _extract_bank_offers(self, soup) -> list[str]:
        offers: list[str] = []
        for el in soup.find_all(string=re.compile(r"Bank\s*Offer", re.I)):
            parent = el.find_parent("li") or el.find_parent("div")
            if parent:
                txt = parent.get_text(" ", strip=True)
                if len(txt) > 15:
                    offers.append(txt[:800])
        # Also scan for EMI / discount text near bank names
        bank_keywords = ["hdfc", "icici", "axis", "sbi", "kotak", "amex", "no cost emi"]
        for li in soup.select("li"):
            txt = li.get_text(" ", strip=True)
            if any(kw in txt.lower() for kw in bank_keywords) and "₹" in txt:
                if txt not in offers:
                    offers.append(txt[:800])
        return offers[:15]

    def _extract_coupons(self, soup) -> list[str]:
        coupons: list[str] = []
        for el in soup.find_all(string=re.compile(r"coupon|extra\s+off", re.I)):
            parent = el.find_parent("li") or el.find_parent("div")
            if parent:
                txt = parent.get_text(" ", strip=True)
                if "₹" in txt and len(txt) > 10:
                    coupons.append(txt[:600])
        return coupons[:5]

    def _extract_reviews(self, soup) -> list[str]:
        reviews: list[str] = []
        for rev in soup.select("div.t-ZTKy, div._6K-7Co, p._2-N8zT"):
            txt = rev.get_text(strip=True)
            if len(txt) > 20:
                reviews.append(txt[:500])
            if len(reviews) >= 10:
                break
        return reviews

    def _extract_specs(self, soup) -> dict:
        specs: dict = {}
        for table in soup.select("table._14cfVK, div._3Rrcbo"):
            for row in table.select("tr"):
                cells = row.select("td")
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True).lower().replace(" ", "_")
                    val = cells[1].get_text(strip=True)
                    specs[key] = val
        # Parse RAM/Storage from title or specs
        page_text = soup.get_text()
        ram_m = re.search(r"(\d+)\s*GB\s*RAM", page_text, re.I)
        if ram_m:
            specs.setdefault("ram_gb", int(ram_m.group(1)))
        storage_m = re.search(r"(\d+)\s*(?:GB|TB)\s*(?:ROM|Storage|Internal)", page_text, re.I)
        if storage_m:
            specs.setdefault("storage_gb", int(storage_m.group(1)))
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

        soup = self._soup(html)
        results: list[SearchHit] = []
        for card in soup.select("div._1AtVbE a[href*='/p/'], a.CGtC98"):
            href = card.get("href", "")
            if not href.startswith("http"):
                href = f"https://www.flipkart.com{href}"
            title_el = card.select_one("div._4rR01T, a.s1Q9rs, div.KzDlHZ")
            title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:100]
            price_el = card.select_one("div._30jeq3, div.Nx9bqj")
            price = parse_indian_price(price_el.get_text()) if price_el else None
            img_el = card.select_one("img._396cs4, img.DByuf4")
            thumb = img_el.get("src") if img_el else None
            # Extract pid from href
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

