"""
Universal 4-Tier All-Store Scraping Engine.

Tier 0: Shopify Universal JSON Fast-Path (`<product_url>.json` via `scrapling.fetchers.Fetcher`)
        with 0ms anti-bot friction across thousands of Shopify D2C stores.
Tier 1: Fast HTTP/3 + browser TLS impersonation via `scrapling.fetchers.Fetcher`
        (redirect-following for short URLs like amzn.in/d/...), parsed via `extruct`
        (JSON-LD Product/Offer/Seller, Microdata, OpenGraph) + `price-parser` (Decimal).
Tier 2: Pooled `scrapling.fetchers.StealthySession(max_pages=2, disable_resources=True)`
        (adaptive=True) under `BROWSER_SEMAPHORE(2)`, triggered ONLY when Tier 1 hits
        JS shells, bot walls, or misses a valid price.
Tier 3: Compact LLM Buy-Box Rescue (`<4,000`-char buy-box snippet via `instructor` +
        OpenRouter, cached in Redis by URL+day) for genuine custom stores lacking JSON-LD.
Mandatory Transactional Gatekeeper: Rejects any page lacking valid `price > 0` AND
real checkout signals (Add to Cart / Buy Now / Add to Bag / schema.org Offer+InStock / Shopify/Woo form).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import extruct
from bs4 import BeautifulSoup
from price_parser import Price
from pydantic import BaseModel, Field

from app.config import settings
from app.schemas.extraction import ClientPayload, CompetitorQuote, LiveOfferRaw
from app.services.product_matcher import (
    extract_clean_brand,
    extract_variant_specs_from_title,
    is_generic_or_invalid_listing,
    sanitize_seller_name,
)
from app.services.rate_guard import (
    CircuitOpenError,
    RateLimitOrBlockedError,
    extract_domain,
    guarded_domain_call,
)

logger = logging.getLogger(__name__)

BOT_WALL_MARKERS = (
    "enter the characters you see below",
    "verify you are a human",
    "checking your browser",
    "cf-browser-verification",
    "robot check",
    "automated queries",
    "unusual traffic",
    "access denied",
    "enable javascript and cookies",
    "captcha",
)

# Strict non-retailer / aggregator / tracker / coupon / forum blocklist
BLOCKED_NON_RETAILER_DOMAINS = (
    "pricehistory.app",
    "pricehistoryapp.com",
    "pricehistory.in",
    "pricebefore.com",
    "buyhatke.com",
    "compare.buyhatke.com",
    "keepa.com",
    "camelcamelcamel.com",
    "mysmartprice.com",
    "91mobiles.com",
    "smartprix.com",
    "gadgets360.com",
    "cashkaro.com",
    "couponzguru.com",
    "grabon.in",
    "paisawapas.com",
    "desidime.com",
    "coupondunia.in",
    "zoutons.com",
    "nearbuy.com",
    "reddit.com",
    "youtube.com",
    "youtu.be",
    "quora.com",
    "gsmarena.com",
    "versus.com",
    "wikipedia.org",
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "pinterest.com",
    "ndtv.com",
    "indiatimes.com",
    "hindustantimes.com",
    "indianexpress.com",
    "timesofindia.com",
    "moneycontrol.com",
    "livemint.com",
    "businesstoday.in",
    "forbes.com",
    "theverge.com",
    "techradar.com",
    "tomsguide.com",
    "rtings.com",
    "wirecutter.com",
    "beebom.com",
    "digit.in",
    "gizbot.com",
    "indiatoday.in",
    "news18.com",
    "financialexpress.com",
    "deccanherald.com",
    "mouthshut.com",
    "trustpilot.com",
)

BLOCKED_DOMAIN_KEYWORDS = (
    "pricehistory",
    "pricebefore",
    "pricetracker",
    "buyhatke",
    "keepa",
    "camelcamelcamel",
    "mysmartprice",
    "91mobiles",
    "smartprix",
    "gadgets360",
    "cashkaro",
    "couponzguru",
    "coupondunia",
    "grabon",
    "paisawapas",
    "desidime",
    "gsmarena",
    "wikipedia",
)

TRANSACTIONAL_CTA_RE = re.compile(
    r"\b(?:add\s+to\s+(?:cart|bag|basket)|buy\s+now|shop\s+now|proceed\s+to\s+(?:checkout|buy)|order\s+now|quick\s+buy)\b",
    re.I,
)


def is_blocked_non_retailer_domain(url_or_domain: str | None) -> bool:
    """Return True if URL/domain belongs to a price tracker, review portal, coupon site, or forum."""
    if not url_or_domain:
        return True
    s = str(url_or_domain).strip().lower()
    if s.startswith(("http://", "https://")):
        domain = extract_domain(s)
    else:
        domain = s.replace("www.", "").split("/")[0].split(":")[0]
    if not domain:
        return True
    if any(domain == bad or domain.endswith("." + bad) for bad in BLOCKED_NON_RETAILER_DOMAINS):
        return True
    if any(kw in domain for kw in BLOCKED_DOMAIN_KEYWORDS):
        return True
    return False


def has_transactional_checkout_signals(html: str, meta: dict[str, Any] | None = None) -> bool:
    """
    Verify that a scraped page is a genuine transactional e-commerce store page,
    NOT a review/spec/price-history/coupon article.
    Requires EITHER:
      - A schema.org/Offer with valid price > 0 AND stock/availability attribute, OR
      - Real purchase CTA markup (Add to Cart / Buy Now / Add to Bag button/input/anchor).
    """
    if meta:
        for prod in _flatten_jsonld_products(meta.get("json-ld") or []):
            offers = prod.get("offers")
            offer_list = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
            for off in offer_list:
                if not isinstance(off, dict):
                    continue
                price = parse_price_decimal(off.get("price") or off.get("lowPrice"))
                avail = str(off.get("availability") or "").strip()
                if price and price > 0 and avail:
                    return True
        for md in (meta.get("microdata") or []):
            if not isinstance(md, dict):
                continue
            off = md.get("offers")
            if isinstance(off, dict):
                price = parse_price_decimal(off.get("price"))
                avail = str(off.get("availability") or "").strip()
                if price and price > 0 and avail:
                    return True

    if not html:
        return False
    soup = BeautifulSoup(html, "lxml")
    for el in soup.select("button, input[type='submit'], input[type='button'], a[role='button'], a[class*='buy'], a[class*='cart'], a[class*='bag'], [id*='add-to-cart'], [id*='buy-now']"):
        label = " ".join((el.get("value") or el.get("aria-label") or el.get_text(" ", strip=True) or "").split())
        if label and len(label) <= 45 and TRANSACTIONAL_CTA_RE.search(label):
            return True
    if soup.select_one("#add-to-cart-button, #buy-now-button, button[name='add'], form[action*='/cart/add']"):
        return True
    return False

DOMAIN_TO_RETAILER = {
    "amazon.in": "amazon_in",
    "amzn.in": "amazon_in",
    "amzn.to": "amazon_in",
    "flipkart.com": "flipkart",
    "fkrt.cc": "flipkart",
    "fkrt.it": "flipkart",
    "croma.com": "croma",
    "reliancedigital.in": "reliance_digital",
    "vijaysales.com": "vijaysales",
    "tatacliq.com": "tatacliq",
    "jiomart.com": "jiomart",
    "myntra.com": "myntra",
    "ajio.com": "ajio",
    "nykaa.com": "nykaa",
    "meesho.com": "meesho",
    "snapdeal.com": "snapdeal",
    "pepperfry.com": "pepperfry",
    "lenskart.com": "lenskart",
    "boat-lifestyle.com": "boat",
    "lg.com": "lg_official",
    "samsung.com": "samsung_official",
    "apple.com": "apple_official",
    "sony.co.in": "sony_official",
    "nike.com": "nike_official",
    "adidas.co.in": "adidas_official",
}


def detect_retailer_key(url: str) -> str:
    domain = extract_domain(url)
    for known, key in DOMAIN_TO_RETAILER.items():
        if domain == known or domain.endswith("." + known):
            return key
    # Clean domain slug (e.g. "shop.brand.in" -> "shop_brand_in")
    slug = re.sub(r"[^a-z0-9]+", "_", domain).strip("_")
    return (slug or "generic")[:20]


def parse_price_decimal(raw: Any) -> Decimal | None:
    """Parse any price string/number into a positive Decimal via `price-parser`."""
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw if raw > 0 else None
    if isinstance(raw, (int, float)):
        d = Decimal(str(raw))
        return d if d > 0 else None
    text = str(raw).strip()
    if not text:
        return None
    parsed = Price.fromstring(text)
    if parsed.amount is not None and parsed.amount > 0:
        return parsed.amount
    return None


def _looks_like_bot_block(html: str, status_code: int | None = None) -> bool:
    if status_code in (403, 429, 503):
        return True
    if not html or len(html) < 500:
        return True
    low = html[:8000].lower()
    return any(m in low for m in BOT_WALL_MARKERS)


def _extract_sku_from_url(url: str) -> str:
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"[?&]pid=([A-Z0-9]+)",
        r"/p/([a-zA-Z0-9_-]{4,40})",
        r"/p-([a-zA-Z0-9_-]{4,40})",
        r"/product/([a-zA-Z0-9_-]{4,40})",
        r"/products/([a-zA-Z0-9_-]{4,40})",
        r"/(\d{5,12})/buy",
    ]
    for pat in patterns:
        m = re.search(pat, url, re.I)
        if m:
            return m.group(1)[:64]
    path = (urlparse(url).path or "").strip("/").split("/")[-1]
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", path)[:48]
    return cleaned or "SKU-WEB"


def _flatten_jsonld_products(items: list[dict]) -> list[dict]:
    products: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        t = item.get("@type")
        types = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
        if any(str(x).lower() == "product" for x in types):
            products.append(item)
        if "@graph" in item and isinstance(item["@graph"], list):
            products.extend(_flatten_jsonld_products(item["@graph"]))
    return products


def _extract_embedded_spa_state(html: str) -> dict[str, Any]:
    """
    Pull product fields from embedded e-commerce state across universal platforms:
    - Next.js (`#__NEXT_DATA__`)
    - Shopify (`ShopifyAnalytics.meta.product`, `var meta = {"product":...}`, `application/json` product script)
    - WooCommerce (`data-product_variations` / `woocommerce-Price-amount`)
    - Myntra (`window.__myx`)
    """
    out: dict[str, Any] = {}
    soup = BeautifulSoup(html, "lxml")

    # 1. Next.js #__NEXT_DATA__
    next_el = soup.find("script", id="__NEXT_DATA__")
    if next_el and next_el.string:
        try:
            nd = json.loads(next_el.string)
            pp = nd.get("props", {}).get("pageProps", {})
            prod = pp.get("product") or pp.get("productData") or pp.get("initialData", {}).get("product") or pp.get("pdpData")
            if isinstance(prod, dict):
                out["title"] = prod.get("name") or prod.get("title")
                out["brand"] = (prod.get("brand", {}).get("name") if isinstance(prod.get("brand"), dict) else prod.get("brand"))
                out["price"] = parse_price_decimal(
                    prod.get("sellingPrice") or prod.get("offerPrice") or prod.get("discountedPrice") or prod.get("price") or prod.get("mrp")
                )
                out["mrp"] = parse_price_decimal(prod.get("mrp") or prod.get("listPrice"))
                out["seller"] = prod.get("sellerName") or prod.get("seller")
        except Exception:
            pass

    # 2. Shopify embedded product JSON (`ShopifyAnalytics.meta` / `var meta = {"product":...}`)
    if not out.get("price") and ("ShopifyAnalytics" in html or "Shopify.shop" in html or "meta.product" in html):
        m_shop = re.search(r'"product"\s*:\s*(\{"id":[\s\S]{10,4000}?\}\s*\})', html)
        if m_shop:
            try:
                sp = json.loads(m_shop.group(1))
                out["brand"] = out.get("brand") or sp.get("vendor")
                variants = sp.get("variants") or []
                if variants and isinstance(variants[0], dict):
                    v0 = variants[0]
                    out["title"] = out.get("title") or v0.get("name")
                    raw_p = v0.get("price")
                    if raw_p is not None:
                        p_num = float(raw_p)
                        # Shopify Analytics stores price in paise/cents (integer * 100)
                        if p_num > 10000 and float(p_num).is_integer() and int(p_num) % 100 == 0:
                            p_num = p_num / 100.0
                        out["price"] = parse_price_decimal(p_num)
            except Exception:
                pass

    # 3. WooCommerce `form.variations_form[data-product_variations]`
    if not out.get("price"):
        woo_form = soup.select_one("form.variations_form[data-product_variations]")
        if woo_form:
            try:
                variations = json.loads(woo_form.get("data-product_variations") or "[]")
                if isinstance(variations, list) and variations:
                    v0 = variations[0]
                    out["price"] = parse_price_decimal(v0.get("display_price"))
                    out["mrp"] = parse_price_decimal(v0.get("display_regular_price"))
            except Exception:
                pass

    # 4. Myntra window.__myx
    if not out.get("price") and "window.__myx" in html:
        m = re.search(r"window\.__myx\s*=\s*(\{.*?\})\s*</script>", html, re.S)
        if m:
            try:
                myx = json.loads(m.group(1))
                pdp = myx.get("pdpData") or {}
                if isinstance(pdp, dict):
                    out["title"] = pdp.get("name")
                    out["brand"] = (pdp.get("brand") or {}).get("name") if isinstance(pdp.get("brand"), dict) else pdp.get("brand")
                    price_obj = pdp.get("price") or {}
                    out["price"] = parse_price_decimal(price_obj.get("discounted") or price_obj.get("mrp"))
                    out["mrp"] = parse_price_decimal(price_obj.get("mrp"))
                    sellers = pdp.get("sellers") or []
                    if sellers and isinstance(sellers[0], dict):
                        out["seller"] = sellers[0].get("sellerName")
            except Exception:
                pass

    return out


def _extract_mandatory_fees_and_offers(soup: BeautifulSoup) -> tuple[float, float, list[str], list[str], list[LiveOfferRaw]]:
    """Scan page text for bank offers, coupons, and bi-directional mandatory platform/delivery fees."""
    delivery_fee = 0.0
    handling_fee = 0.0
    bank_offers: list[str] = []
    coupons: list[str] = []
    live_offers: list[LiveOfferRaw] = []
    seen: set[str] = set()
    seen_fee_labels: set[str] = set()

    text_blob = " ".join(soup.get_text(" ", strip=True).split())
    # Mandatory fees: match BOTH "+ ₹299 Protect Promise Fee" (amount-first) AND "Protect Promise Fee ₹299" (label-first)
    fee_specs = [
        ("Protect Promise Fee", "handling", r"(?:\+\s*)?(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?protect\s+promise\s+fee"),
        ("Protect Promise Fee", "handling", r"protect\s+promise\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)"),
        ("Secured Packaging Fee", "handling", r"(?:\+\s*)?(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?secured\s+packaging\s+fee"),
        ("Secured Packaging Fee", "handling", r"secured\s+packaging\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)"),
        ("Offer Handling Fee", "handling", r"(?:\+\s*)?(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?offer\s+handling\s+fee"),
        ("Offer Handling Fee", "handling", r"offer\s+handling\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)"),
        ("Platform Fee", "handling", r"(?:\+\s*)?(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?platform\s+fee"),
        ("Platform Fee", "handling", r"platform\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)"),
        ("Handling Fee", "handling", r"(?:\+\s*)?(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?(?:payment\s+)?handling\s+fee"),
        ("Handling Fee", "handling", r"(?:payment\s+)?handling\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)"),
        ("Delivery Fee", "delivery", r"(?:delivery\s+(?:charge|fee)|shipping\s+(?:charge|fee))[^₹\d]{0,20}(?:₹|Rs\.?|INR)\s*([\d,]+)"),
    ]
    for label, kind, pat in fee_specs:
        if label in seen_fee_labels:
            continue
        m = re.search(pat, text_blob, re.I)
        if m:
            val = float(m.group(1).replace(",", ""))
            if 0 < val <= 2000:
                seen_fee_labels.add(label)
                snippet = m.group(0).strip()[:160]
                if kind == "handling":
                    handling_fee += val
                elif kind == "delivery" and val > delivery_fee:
                    delivery_fee = val
                if snippet not in seen:
                    seen.add(snippet)
                    live_offers.append(
                        LiveOfferRaw(
                            title=label,
                            offer_type="fee",
                            raw_text=snippet,
                            source="live_page",
                        )
                    )

    # Bank / UPI / Coupon offer lines from elements
    offer_re = re.compile(
        r"(?:hdfc|icici|sbi|axis|kotak|amex|bob|idfc|hsbc|rupay|instant\s+discount|bank\s+offer|no\s+cost\s+emi|cashback|upi|coupon)",
        re.I,
    )
    for el in soup.select("li, .offer-item, [class*='offer'], [class*='coupon'], [id*='offer']"):
        txt = " ".join(el.get_text(" ", strip=True).split())
        if 18 <= len(txt) <= 260 and offer_re.search(txt) and txt not in seen:
            seen.add(txt)
            low = txt.lower()
            if "coupon" in low or "promo code" in low:
                coupons.append(txt)
                otype = "coupon"
            elif "upi" in low or "paytm" in low or "cred" in low or "phonepe" in low:
                otype = "upi"
            elif "emi" in low:
                otype = "emi"
            elif "cashback" in low:
                otype = "cashback"
            else:
                bank_offers.append(txt)
                otype = "bank"
            live_offers.append(
                LiveOfferRaw(
                    title=txt[:75],
                    offer_type=otype,
                    raw_text=txt,
                    source="live_page",
                )
            )
            if len(live_offers) >= 12:
                break

    return delivery_fee, handling_fee, bank_offers[:8], coupons[:4], live_offers


def parse_html_to_payload(
    html: str,
    final_url: str,
    extraction_tier: str = "tier1_http",
    require_transactional_signals: bool = False,
) -> ClientPayload | None:
    """
    Parse raw HTML via `extruct` (JSON-LD, Microdata, OpenGraph) + `price-parser`
    + embedded SPA state + semantic DOM heuristics.
    When `require_transactional_signals=True`, rejects non-transactional pages
    (price trackers, review/spec blogs, coupon sites) lacking Add-to-Cart/Buy-Now/Offer+stock.
    """
    if not html:
        return None
    if is_blocked_non_retailer_domain(final_url):
        return None

    retailer = detect_retailer_key(final_url)
    domain = extract_domain(final_url)

    title: str | None = None
    jsonld_brand: str | None = None
    listed_price: Decimal | None = None
    mrp: Decimal | None = None
    in_stock = True
    raw_seller: str | None = None
    sku: str | None = None
    rating_val: float | None = None

    # ── 1. extruct structured metadata (JSON-LD + Microdata + OpenGraph) ──
    try:
        meta = extruct.extract(
            html,
            base_url=final_url,
            syntaxes=["json-ld", "microdata", "opengraph"],
            uniform=True,
        )
    except Exception as exc:
        logger.debug("extruct parse error for %s: %s", final_url, exc)
        meta = {}

    if require_transactional_signals and not has_transactional_checkout_signals(html, meta):
        logger.info("Rejected non-transactional page (no buy CTA or Offer+stock): %s", final_url)
        return None

    # JSON-LD Product
    products = _flatten_jsonld_products(meta.get("json-ld") or [])
    if products:
        prod = products[0]
        title = prod.get("name") or title
        sku = prod.get("sku") or prod.get("mpn") or prod.get("gtin13") or sku
        b = prod.get("brand")
        if isinstance(b, dict):
            jsonld_brand = b.get("name")
        elif isinstance(b, str):
            jsonld_brand = b

        offers = prod.get("offers")
        offer_list = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
        for off in offer_list:
            if not isinstance(off, dict):
                continue
            p = parse_price_decimal(off.get("price") or off.get("lowPrice"))
            if p and (listed_price is None or p < listed_price):
                listed_price = p
            hp = parse_price_decimal(off.get("highPrice"))
            if hp and (mrp is None or hp > mrp):
                mrp = hp
            avail = str(off.get("availability") or "").lower()
            if "outofstock" in avail or "soldout" in avail or "discontinued" in avail:
                in_stock = False
            seller_obj = off.get("seller")
            if isinstance(seller_obj, dict) and seller_obj.get("name"):
                raw_seller = str(seller_obj["name"])
            elif isinstance(seller_obj, str):
                raw_seller = seller_obj

        agg = prod.get("aggregateRating")
        if isinstance(agg, dict) and agg.get("ratingValue"):
            try:
                rating_val = float(agg["ratingValue"])
            except Exception:
                pass

    # Microdata fallback
    for md in (meta.get("microdata") or []):
        if not isinstance(md, dict):
            continue
        if "product" in str(md.get("@type") or "").lower():
            title = title or md.get("name")
            if not listed_price and isinstance(md.get("offers"), dict):
                listed_price = parse_price_decimal(md["offers"].get("price"))

    # OpenGraph fallback
    for og in (meta.get("opengraph") or []):
        if not isinstance(og, dict):
            continue
        title = title or og.get("og:title")
        if not listed_price:
            listed_price = parse_price_decimal(
                og.get("product:price:amount") or og.get("og:price:amount")
            )
        if not jsonld_brand:
            jsonld_brand = og.get("product:brand") or og.get("og:brand")

    # ── 2. Embedded SPA state (__NEXT_DATA__, __myx) ──
    if not listed_price or not title:
        spa = _extract_embedded_spa_state(html)
        title = title or spa.get("title")
        jsonld_brand = jsonld_brand or spa.get("brand")
        listed_price = listed_price or spa.get("price")
        mrp = mrp or spa.get("mrp")
        raw_seller = raw_seller or spa.get("seller")

    # ── 3. Semantic DOM fallback via BeautifulSoup ──
    soup = BeautifulSoup(html, "lxml")
    if not title:
        h1 = soup.select_one("#productTitle, h1.yhB1nd, h1.pdp-title, h1")
        if h1:
            title = " ".join(h1.get_text(" ", strip=True).split())
        elif soup.title:
            title = soup.title.get_text(strip=True)

    if not listed_price:
        price_selectors = [
            "#corePrice_feature_div .a-price .a-offscreen",
            ".priceToPay .a-offscreen",
            ".Nx9bqj",
            "._30jeq3",
            ".pdp-price strong",
            ".prod-sp",
            "[itemprop='price']",
            "[data-testid='price']",
            ".product-price",
            ".selling-price",
            ".offer-price",
        ]
        for sel in price_selectors:
            el = soup.select_one(sel)
            if el:
                cand = parse_price_decimal(el.get("content") or el.get_text(" ", strip=True))
                if cand:
                    listed_price = cand
                    break

    if not mrp:
        mrp_el = soup.select_one(".basisPrice .a-offscreen, .yRaY8j, ._3I9_wc, .pdp-mrp, del, s")
        if mrp_el:
            cand_mrp = parse_price_decimal(mrp_el.get_text(" ", strip=True))
            if cand_mrp and listed_price and cand_mrp >= listed_price:
                mrp = cand_mrp

    # Scoped buy-box seller extraction (NEVER nav links like "Become a Seller")
    if not raw_seller:
        buybox_selectors = [
            "#sellerProfileTriggerId",
            "#merchant-info a",
            "#sellerName span span",
            "#sellerName",
            ".seller-name",
            "[itemprop='seller']",
        ]
        for sel in buybox_selectors:
            el = soup.select_one(sel)
            if el:
                cand_seller = sanitize_seller_name(el.get_text(" ", strip=True), final_url, retailer)
                if cand_seller:
                    raw_seller = cand_seller
                    break

    clean_brand = extract_clean_brand(None, title, jsonld_brand, domain=domain)
    clean_seller = sanitize_seller_name(raw_seller, final_url, retailer)
    delivery_fee, handling_fee, bank_offers, coupons, live_offers = _extract_mandatory_fees_and_offers(soup)

    price_float = float(listed_price) if listed_price is not None else None
    mrp_float = float(mrp) if mrp is not None else None

    if not title:
        return None

    return ClientPayload(
        retailer=retailer,
        url=final_url,
        title=title.strip()[:300],
        brand=clean_brand or None,
        sku=str(sku or _extract_sku_from_url(final_url))[:64],
        listed_price=price_float,
        mrp=mrp_float,
        in_stock=in_stock,
        seller_name=clean_seller,
        seller_rating=rating_val,
        raw_bank_offers=bank_offers,
        raw_coupons=coupons,
        raw_live_offers=live_offers,
        delivery_fee=delivery_fee,
        handling_fee=handling_fee,
        variant_specs={**extract_variant_specs_from_title(title), "extraction_tier": extraction_tier},
        source="server",
    )


SHOPIFY_PRODUCT_URL_RE = re.compile(r"/products/([a-zA-Z0-9._-]+?)(?:\.json)?/?$", re.I)


def is_shopify_product_url(url: str) -> bool:
    """Detect Shopify product URLs (`/products/<handle>`)."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    path = (urlparse(url).path or "").rstrip("/")
    return bool(SHOPIFY_PRODUCT_URL_RE.search(path))


def build_shopify_json_url(url: str) -> str | None:
    """Convert `https://store.in/products/handle?foo=bar` -> `https://store.in/products/handle.json`."""
    if not is_shopify_product_url(url):
        return None
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path.lower().endswith(".json"):
        path = f"{path}.json"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def parse_shopify_product_json(
    data: dict[str, Any],
    original_url: str,
    preferred_variant_hint: str | None = None,
) -> ClientPayload | None:
    """
    Parse Shopify `/products/<handle>.json` payload into a verified transactional ClientPayload.
    """
    if not isinstance(data, dict):
        return None
    prod = data.get("product") if isinstance(data.get("product"), dict) else data
    if not isinstance(prod, dict):
        return None
    title = str(prod.get("title") or "").strip()
    variants = prod.get("variants") or []
    if not title or not isinstance(variants, list) or not variants:
        return None

    chosen: dict[str, Any] | None = None
    hint_low = (preferred_variant_hint or "").lower()
    in_stock_variants = [v for v in variants if isinstance(v, dict) and v.get("available", True) is not False]
    pool = in_stock_variants or [v for v in variants if isinstance(v, dict)]
    if not pool:
        return None

    if hint_low:
        for v in pool:
            vtitle = str(v.get("title") or "").lower()
            if vtitle and vtitle != "default title" and vtitle in hint_low:
                chosen = v
                break
    if chosen is None:
        # Pick cheapest variant with valid positive price
        priced = [(parse_price_decimal(v.get("price")), v) for v in pool]
        priced = [(p, v) for p, v in priced if p is not None and p > 0]
        if not priced:
            return None
        priced.sort(key=lambda x: x[0])
        chosen = priced[0][1]

    listed_dec = parse_price_decimal(chosen.get("price"))
    if listed_dec is None or listed_dec <= 0:
        return None
    mrp_dec = parse_price_decimal(chosen.get("compare_at_price"))
    if mrp_dec is not None and mrp_dec < listed_dec:
        mrp_dec = None

    variant_title = str(chosen.get("title") or "").strip()
    full_title = (
        f"{title} ({variant_title})"
        if variant_title and variant_title.lower() != "default title" and variant_title.lower() not in title.lower()
        else title
    )
    retailer = detect_retailer_key(original_url)
    domain = extract_domain(original_url)
    vendor = str(prod.get("vendor") or "").strip() or None
    brand = extract_clean_brand(vendor, full_title, domain=domain)
    seller = sanitize_seller_name(vendor, original_url, retailer) or "Official Direct Store"
    in_stock = bool(chosen.get("available", True))
    sku = str(chosen.get("sku") or chosen.get("id") or prod.get("id") or _extract_sku_from_url(original_url))[:64]

    return ClientPayload(
        retailer=retailer,
        url=original_url,
        title=full_title[:300],
        brand=brand,
        sku=sku,
        listed_price=float(listed_dec),
        mrp=float(mrp_dec) if mrp_dec is not None else None,
        in_stock=in_stock,
        seller_name=seller,
        variant_specs={
            **extract_variant_specs_from_title(full_title),
            "extraction_tier": "tier0_shopify_json",
            "shopify_variant_id": chosen.get("id"),
        },
        source="server",
    )


WOOCOMMERCE_PRODUCT_URL_RE = re.compile(r"/product/([a-zA-Z0-9._-]+?)/?$", re.I)


def build_woocommerce_store_api_url(url: str) -> str | None:
    """
    Detect WooCommerce `/product/<slug>` URLs and construct the public WooCommerce Store API endpoint:
    `https://store.in/wp-json/wc/store/v1/products?slug=<slug>`
    """
    if not url or not url.startswith(("http://", "https://")):
        return None
    parsed = urlparse(url)
    m = WOOCOMMERCE_PRODUCT_URL_RE.search(parsed.path or "")
    if not m:
        return None
    slug = m.group(1)
    return f"{parsed.scheme}://{parsed.netloc}/wp-json/wc/store/v1/products?slug={slug}"


def parse_woocommerce_store_api_json(
    data: Any,
    original_url: str,
) -> ClientPayload | None:
    """Parse WooCommerce Store API `/wp-json/wc/store/v1/products` response into ClientPayload."""
    items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    if not items or not isinstance(items[0], dict):
        return None
    prod = items[0]
    title = str(prod.get("name") or "").strip()
    prices = prod.get("prices") or {}
    if not title or not isinstance(prices, dict):
        return None

    minor_unit = int(prices.get("currency_minor_unit") or 2)
    divisor = 10 ** minor_unit if minor_unit > 0 else 1
    raw_price = prices.get("price") or prices.get("sale_price")
    if raw_price is None:
        return None
    try:
        price_val = round(float(raw_price) / divisor, 2)
    except Exception:
        return None
    if price_val <= 0:
        return None

    mrp_val: float | None = None
    raw_reg = prices.get("regular_price")
    if raw_reg is not None:
        try:
            reg = round(float(raw_reg) / divisor, 2)
            if reg >= price_val:
                mrp_val = reg
        except Exception:
            pass

    retailer = detect_retailer_key(original_url)
    domain = extract_domain(original_url)
    brand = extract_clean_brand(None, title, domain=domain)
    in_stock = bool(prod.get("is_in_stock", True) and prod.get("is_purchasable", True))
    sku = str(prod.get("sku") or prod.get("id") or _extract_sku_from_url(original_url))[:64]
    images = prod.get("images") or []
    img_url = images[0].get("src") if (images and isinstance(images[0], dict)) else None

    return ClientPayload(
        retailer=retailer,
        url=original_url,
        title=title[:300],
        brand=brand or None,
        sku=sku,
        listed_price=price_val,
        mrp=mrp_val,
        image_url=img_url,
        in_stock=in_stock,
        seller_name="Official Direct Store",
        variant_specs={
            **extract_variant_specs_from_title(title),
            "extraction_tier": "tier0_woocommerce_json",
        },
        source="server",
    )


def _tier0_json_sync(json_url: str, timeout: float = 8.0) -> tuple[int, Any]:
    """Tier 0: Fetch Shopify `.json` or WooCommerce Store API JSON via Scrapling Fetcher."""
    try:
        from scrapling.fetchers import Fetcher

        resp = Fetcher.get(
            json_url,
            stealthy_headers=True,
            follow_redirects=True,
            timeout=timeout,
        )
        status = int(getattr(resp, "status", 200) or 200)
        if status != 200:
            return status, None
        raw = getattr(resp, "text", None) or getattr(resp, "html_content", None) or ""
        if not raw and hasattr(resp, "body") and isinstance(resp.body, (bytes, bytearray)):
            raw = resp.body.decode("utf-8", "ignore")
        stripped = raw.lstrip()
        if stripped.startswith(("{", "[")):
            return status, json.loads(stripped)
        return status, None
    except Exception:
        return 0, None


class _LLMRescuedBuyBox(BaseModel):
    """Compact LLM buy-box rescue output for unknown custom stores lacking JSON-LD."""

    title: str = Field(default="")
    listed_price: float | None = None
    in_stock: bool = True
    seller_name: str | None = None
    is_real_checkout_store: bool = False


def _build_compact_buybox_snippet(html: str, max_chars: int = 3800) -> str:
    """Strip nav/footer/scripts and return a compact <4,000-char buy-box snippet around title, price & CTA."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "iframe"]):
        tag.decompose()
    chunks: list[str] = []
    if soup.title and soup.title.string:
        chunks.append(f"PAGE_TITLE: {soup.title.string.strip()[:160]}")
    for sel in (
        "h1",
        "[class*='price']",
        "[id*='price']",
        "[class*='buy']",
        "[class*='cart']",
        "[class*='stock']",
        "button",
        "form[action*='cart']",
        "main",
    ):
        for el in soup.select(sel)[:6]:
            t = " ".join(el.get_text(" ", strip=True).split())
            if t and t not in chunks:
                chunks.append(t[:500])
            if sum(len(c) for c in chunks) >= max_chars:
                break
        if sum(len(c) for c in chunks) >= max_chars:
            break
    snippet = "\n".join(chunks)
    if len(snippet) < 200:
        snippet = " ".join(soup.get_text(" ", strip=True).split())
    return snippet[:max_chars]


async def _tier3_llm_buybox_rescue(
    html: str,
    final_url: str,
    *,
    redis_client=None,
) -> ClientPayload | None:
    """
    Tier 3: Compact LLM Buy-Box Rescue for genuine smaller/custom stores lacking JSON-LD.
    Uses `instructor` + OpenRouter on a <4,000-char buy-box snippet, cached in Redis by URL+day.
    Strictly requires `is_real_checkout_store=True` AND `listed_price > 0`.
    """
    if not html or not settings.openrouter_api_key:
        return None
    if is_blocked_non_retailer_domain(final_url):
        return None
    if not has_transactional_checkout_signals(html, None):
        return None

    day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache_key = f"llm_buybox:{hashlib.sha256(f'{final_url}|{day_key}'.encode()).hexdigest()}"
    rescued: _LLMRescuedBuyBox | None = None

    if redis_client is not None:
        try:
            cached_raw = await redis_client.get(cache_key)
            if cached_raw:
                rescued = _LLMRescuedBuyBox.model_validate_json(cached_raw)
        except Exception:
            pass

    if rescued is None:
        snippet = _build_compact_buybox_snippet(html, max_chars=3800)
        if len(snippet) < 40:
            return None
        try:
            import instructor
            from openai import AsyncOpenAI

            raw_client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                timeout=8.0,
            )
            client = instructor.from_openai(raw_client, mode=instructor.Mode.JSON)
            rescued = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.openrouter_model,
                    response_model=_LLMRescuedBuyBox,
                    max_retries=1,
                    temperature=0.0,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Extract e-commerce buy-box data from this compact Indian store snippet. "
                                "Set is_real_checkout_store=true ONLY if users can directly buy/add-to-cart on this site "
                                "(NOT a review blog, news article, or price tracker). Never invent prices."
                            ),
                        },
                        {"role": "user", "content": f"URL: {final_url}\n\nSnippet:\n{snippet}"},
                    ],
                ),
                timeout=9.0,
            )
            if redis_client is not None:
                try:
                    await redis_client.set(cache_key, rescued.model_dump_json(), ex=24 * 60 * 60)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("Tier 3 LLM buy-box rescue failed for %s: %s", final_url, exc)
            return None

    if not rescued or not rescued.is_real_checkout_store or not rescued.listed_price or rescued.listed_price <= 0 or not rescued.title:
        return None

    retailer = detect_retailer_key(final_url)
    domain = extract_domain(final_url)
    clean_brand = extract_clean_brand(None, rescued.title, domain=domain)
    clean_seller = sanitize_seller_name(rescued.seller_name, final_url, retailer)
    soup = BeautifulSoup(html, "lxml")
    delivery_fee, handling_fee, bank_offers, coupons, live_offers = _extract_mandatory_fees_and_offers(soup)

    return ClientPayload(
        retailer=retailer,
        url=final_url,
        title=rescued.title.strip()[:300],
        brand=clean_brand or None,
        sku=_extract_sku_from_url(final_url),
        listed_price=round(float(rescued.listed_price), 2),
        in_stock=bool(rescued.in_stock),
        seller_name=clean_seller,
        raw_bank_offers=bank_offers,
        raw_coupons=coupons,
        raw_live_offers=live_offers,
        delivery_fee=delivery_fee,
        handling_fee=handling_fee,
        variant_specs={**extract_variant_specs_from_title(rescued.title), "extraction_tier": "tier3_llm_rescue"},
        source="server",
    )


def _tier1_fetch_sync(url: str, timeout: float = 12.0) -> tuple[int, str, str]:
    """Tier 1: Fast HTTP/3 + browser TLS impersonation via Scrapling Fetcher."""
    try:
        from scrapling.fetchers import Fetcher

        resp = Fetcher.get(
            url,
            stealthy_headers=True,
            follow_redirects=True,
            timeout=timeout,
        )
        status = int(getattr(resp, "status", 200) or 200)
        final_url = str(getattr(resp, "url", url) or url)
        body = getattr(resp, "html_content", None) or getattr(resp, "text", None) or str(resp.body.decode("utf-8", "ignore") if hasattr(resp, "body") and isinstance(resp.body, (bytes, bytearray)) else "")
        return status, final_url, body
    except Exception as exc:
        logger.debug("Scrapling Fetcher error for %s (%s) — trying httpx fallback", url, exc)
        import httpx

        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept-Language": "en-IN,en;q=0.9",
            },
        ) as client:
            r = client.get(url)
            return r.status_code, str(r.url), r.text


async def _tier2_stealthy_fetch(url: str, timeout_ms: int = 18000) -> tuple[int, str, str]:
    """
    Tier 2: Escalate to pooled Scrapling StealthySession (max_pages=2, disable_resources=True,
    adaptive=True) under BROWSER_SEMAPHORE(2) ONLY on JS shells or bot blocks.
    """
    def _run_sync() -> tuple[int, str, str]:
        from scrapling.fetchers import StealthySession

        with StealthySession(
            headless=True,
            max_pages=2,
            disable_resources=True,
            adaptive=True,
        ) as session:
            page = session.fetch(url, timeout=timeout_ms, network_idle=False)
            status = int(getattr(page, "status", 200) or 200)
            final_url = str(getattr(page, "url", url) or url)
            body = getattr(page, "html_content", None) or getattr(page, "text", "") or ""
            return status, final_url, body

    return await asyncio.to_thread(_run_sync)


async def extract_from_url(
    url: str,
    *,
    allow_tier2: bool = True,
    allow_tier3_llm: bool = True,
    require_transactional: bool = True,
    redis_client=None,
) -> tuple[ClientPayload | None, str]:
    """
    Universal 4-tier extraction for any e-commerce URL:
      Tier 0: Shopify `<product_url>.json` fast-path (0ms anti-bot friction)
      Tier 1: Fast HTTP/3 + TLS impersonation (`Scrapling Fetcher`) + `extruct` + `price-parser`
      Tier 2: Pooled `StealthySession(max_pages=2, disable_resources=True, adaptive=True)` under `BROWSER_SEMAPHORE(2)`
      Tier 3: Compact LLM Buy-Box Rescue (`<4,000` chars via `instructor` + OpenRouter, Redis-cached)
    Returns (payload_or_none, tier_used_or_error).
    """
    if is_blocked_non_retailer_domain(url):
        return None, "blocked_non_retailer"
    domain = extract_domain(url)
    last_html = ""
    last_final_url = url

    # ── Tier 0: Universal Shopify & WooCommerce JSON Fast-Path ──
    shopify_json_url = build_shopify_json_url(url)
    if shopify_json_url:
        try:
            async def _do_tier0() -> tuple[int, Any]:
                return await asyncio.to_thread(_tier0_json_sync, shopify_json_url, 8.0)

            s0, jdata = await guarded_domain_call(
                shopify_json_url,
                _do_tier0,
                use_browser_semaphore=False,
                max_attempts=1,
                redis_client=redis_client,
            )
            if s0 == 200 and isinstance(jdata, dict):
                p0 = parse_shopify_product_json(jdata, url)
                if p0 and p0.listed_price and p0.listed_price > 0:
                    bad, _ = is_generic_or_invalid_listing(p0.title, p0.listed_price, p0.url)
                    if not bad:
                        return p0, "tier0_shopify_json"
        except CircuitOpenError as exc:
            return None, f"circuit_open:{exc.reason}"
        except Exception as exc:
            logger.debug("Tier 0 Shopify JSON skipped for %s: %s", url, exc)

    woo_json_url = build_woocommerce_store_api_url(url)
    if woo_json_url:
        try:
            async def _do_tier0_woo() -> tuple[int, Any]:
                return await asyncio.to_thread(_tier0_json_sync, woo_json_url, 8.0)

            sw, wdata = await guarded_domain_call(
                woo_json_url,
                _do_tier0_woo,
                use_browser_semaphore=False,
                max_attempts=1,
                redis_client=redis_client,
            )
            if sw == 200 and wdata:
                pw = parse_woocommerce_store_api_json(wdata, url)
                if pw and pw.listed_price and pw.listed_price > 0:
                    bad, _ = is_generic_or_invalid_listing(pw.title, pw.listed_price, pw.url)
                    if not bad:
                        return pw, "tier0_woocommerce_json"
        except CircuitOpenError as exc:
            return None, f"circuit_open:{exc.reason}"
        except Exception as exc:
            logger.debug("Tier 0 WooCommerce JSON skipped for %s: %s", url, exc)

    # ── Tier 1: Fast HTTP/3 + TLS impersonation ──
    try:
        async def _do_tier1() -> tuple[int, str, str]:
            status, final_url, html = await asyncio.to_thread(_tier1_fetch_sync, url, 12.0)
            if status in (403, 429):
                raise RateLimitOrBlockedError(domain, status_code=status, reason=f"HTTP {status}")
            return status, final_url, html

        status, final_url, html = await guarded_domain_call(
            url,
            _do_tier1,
            use_browser_semaphore=False,
            max_attempts=2,
            redis_client=redis_client,
        )
        if is_blocked_non_retailer_domain(final_url):
            return None, "blocked_non_retailer"
        if not _looks_like_bot_block(html, status):
            last_html = html
            last_final_url = final_url
            payload = parse_html_to_payload(
                html,
                final_url,
                extraction_tier="tier1_http",
                require_transactional_signals=require_transactional,
            )
            if payload and payload.listed_price and payload.listed_price > 0:
                bad, _ = is_generic_or_invalid_listing(payload.title, payload.listed_price, payload.url)
                if not bad:
                    return payload, "tier1_http"
    except CircuitOpenError as exc:
        return None, f"circuit_open:{exc.reason}"
    except RateLimitOrBlockedError as exc:
        return None, f"blocked:{exc.status_code}"
    except Exception as exc:
        logger.debug("Tier 1 failed for %s: %s", url, exc)

    # ── Tier 2: StealthySession headless fallback (semaphore-bounded) ──
    if allow_tier2:
        try:
            async def _do_tier2() -> tuple[int, str, str]:
                status2, final_url2, html2 = await _tier2_stealthy_fetch(url, timeout_ms=18000)
                if status2 in (403, 429):
                    raise RateLimitOrBlockedError(domain, status_code=status2, reason=f"Tier2 HTTP {status2}")
                return status2, final_url2, html2

            status2, final_url2, html2 = await guarded_domain_call(
                url,
                _do_tier2,
                use_browser_semaphore=True,
                max_attempts=1,
                redis_client=redis_client,
            )
            if is_blocked_non_retailer_domain(final_url2):
                return None, "blocked_non_retailer"
            if not _looks_like_bot_block(html2, status2):
                last_html = html2
                last_final_url = final_url2
                payload2 = parse_html_to_payload(
                    html2,
                    final_url2,
                    extraction_tier="tier2_stealth",
                    require_transactional_signals=require_transactional,
                )
                if payload2 and payload2.listed_price and payload2.listed_price > 0:
                    bad, _ = is_generic_or_invalid_listing(payload2.title, payload2.listed_price, payload2.url)
                    if not bad:
                        return payload2, "tier2_stealth"
        except CircuitOpenError as exc:
            return None, f"circuit_open:{exc.reason}"
        except RateLimitOrBlockedError as exc:
            return None, f"blocked:{exc.status_code}"
        except Exception as exc:
            logger.warning("Tier 2 stealthy fetch failed for %s: %s", url, exc)

    # ── Tier 3: Compact LLM Buy-Box Rescue (genuine unknown custom stores lacking JSON-LD) ──
    if allow_tier3_llm and last_html:
        rescued = await _tier3_llm_buybox_rescue(last_html, last_final_url, redis_client=redis_client)
        if rescued and rescued.listed_price and rescued.listed_price > 0:
            bad, _ = is_generic_or_invalid_listing(rescued.title, rescued.listed_price, rescued.url)
            if not bad:
                return rescued, "tier3_llm_rescue"

    return None, "extraction_failed"


def payload_to_competitor_quote(
    payload: ClientPayload,
    match_score: float,
    extraction_tier: str,
    source: str = "searxng",
    store_bucket: str = "exact_match",
    variant_diff_badges: list[str] | None = None,
) -> CompetitorQuote:
    return CompetitorQuote(
        retailer=payload.retailer,
        title=payload.title,
        listed_price=payload.listed_price,
        currency=getattr(payload, "currency", "INR") or "INR",
        url=payload.url,
        sku=payload.sku,
        in_stock=payload.in_stock,
        is_transactional_store=getattr(payload, "is_transactional_store", True),
        seller_name=payload.seller_name,
        seller_rating=payload.seller_rating,
        delivery_fee=payload.delivery_fee,
        bank_offer_summary=payload.raw_bank_offers[0] if payload.raw_bank_offers else None,
        match_score=match_score,
        extraction_tier=extraction_tier,
        image_url=payload.image_url,
        source=source,
        store_bucket=store_bucket,  # type: ignore[arg-type]
        variant_diff_badges=variant_diff_badges or [],
    )
