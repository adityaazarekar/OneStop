"""
Universal Multi-Store Shopping Discovery Engine.

1. Queries self-hosted SearXNG (`/search?format=json`) + optional Serper Google Shopping API
   for normalized `Brand + Model + Exact Variant`.
2. Discovers matching listings across Indian & global stores (Amazon.in, Flipkart, Croma,
   Reliance Digital, Vijay Sales, Tata CLiQ, JioMart, Myntra, Ajio, Nykaa, official brand
   stores like LG/Samsung/Apple/Nike India, and smaller verified merchants).
3. Extracts structured live prices via `universal_scraper.extract_from_url` (respecting
   per-domain rate limits & 30-min circuit breaker), filters accessories & mismatched
   variants via RapidFuzz in `product_matcher.score_candidate_match`.
4. Caches results in Redis for 6 hours (`discover:{sha256}`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
import redis.asyncio as aioredis

from app.config import settings
from app.schemas.extraction import ClientPayload, CompetitorQuote, DiscoverStoresResponse
from app.services.product_matcher import (
    extract_clean_brand,
    extract_variant_specs_from_title,
    score_candidate_match,
)
from app.services.rate_guard import extract_domain
from app.services.universal_scraper import (
    BLOCKED_NON_RETAILER_DOMAINS,
    detect_retailer_key,
    extract_from_url,
    is_blocked_non_retailer_domain,
    parse_price_decimal,
    payload_to_competitor_quote,
)

logger = logging.getLogger(__name__)

DISCOVERY_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours

NON_SHOPPING_HOSTS = BLOCKED_NON_RETAILER_DOMAINS

PREFERRED_SHOPPING_DOMAINS = (
    "amazon.in", "flipkart.com", "croma.com", "reliancedigital.in",
    "vijaysales.com", "tatacliq.com", "jiomart.com", "myntra.com",
    "ajio.com", "nykaa.com", "meesho.com", "snapdeal.com", "pepperfry.com",
    "lenskart.com", "boat-lifestyle.com", "lg.com", "samsung.com",
    "apple.com", "sony.co.in", "nike.com", "adidas.co.in", "puma.com",
    "oneplus.in", "mi.com", "realme.com", "dyson.in", "hp.com", "dell.com",
    "lenovo.com", "asus.com", "wakefit.co", "shoppersstop.com", "westside.com",
    "tirabeauty.com", "purplle.com", "firstcry.com", "decathlon.in",
    "poorvika.com", "sangeethamobiles.com", "lotmobiles.com", "kohinoorelectronics.com",
    "giriasindia.com", "bajajmall.in", "shopatsc.com", "imagineonline.store", "unicornstore.in",
    "aptronixindia.com", "maple.in", "inventstore.in", "iplanet.one", "treasures.in",
)

ECOMMERCE_URL_FOOTPRINT_RE = re.compile(
    r"(?:/products?/|/p/|/dp/|/gp/product/|/itm/|/buy(?:/|$)|/shop/|/store/|/item/)",
    re.I,
)

BLOCKED_URL_PATH_FRAGMENTS = (
    "/search", "/s?", "/category/", "/blog/", "/news/", "/review", "/reviews",
    "/compare", "/cart", "/login", "/price-history", "/coupon", "/deal",
    "/forum", "/topic/", "/article/", "/spec", "/specifications",
)


def build_normalized_query(payload: ClientPayload) -> str:
    """Build clean `Brand + Model + Exact Variant` without duplicating brand or adding 'Generic'."""
    brand = extract_clean_brand(payload.brand, payload.title, domain=payload.url)
    title = (payload.title or "").strip()
    # Remove parenthetical fluff & punctuation
    cleaned_title = re.sub(r"\([^)]*\)", " ", title)
    cleaned_title = re.sub(r"[^a-zA-Z0-9.\s+-]", " ", cleaned_title)
    words = [
        w for w in cleaned_title.split()
        if len(w) > 1 and w.lower() not in {"with", "and", "the", "for", "in", "on", "of", "by", "from", "generic"}
    ]

    parts: list[str] = []
    if brand:
        parts.append(brand)
    for w in words[:7]:
        if brand and w.lower() == brand.lower() and parts:
            continue
        if w.lower() not in {p.lower() for p in parts}:
            parts.append(w)

    specs = {**extract_variant_specs_from_title(title), **(payload.variant_specs or {})}
    if specs.get("storage_gb"):
        tok = f"{specs['storage_gb']}GB" if specs["storage_gb"] < 1024 else f"{specs['storage_gb'] // 1024}TB"
        if tok.lower() not in " ".join(parts).lower():
            parts.append(tok)
    if specs.get("screen_size_inches"):
        tok = f"{int(specs['screen_size_inches'])} inch"
        if tok.lower() not in " ".join(parts).lower():
            parts.append(tok)

    return " ".join(parts).strip()[:110]


def _is_candidate_shopping_url(url: str, current_domain: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    domain = extract_domain(url)
    if not domain or domain == current_domain:
        return False
    if is_blocked_non_retailer_domain(url):
        return False
    low_url = url.lower()
    if "amazon.com/review" in low_url or "flipkart.com/reviews" in low_url:
        return False
    path = (urlparse(url).path or "").lower()
    if any(p in path for p in BLOCKED_URL_PATH_FRAGMENTS):
        return False
    return True


def _is_preferred_domain(url: str) -> bool:
    domain = extract_domain(url)
    return any(domain == pref or domain.endswith("." + pref) for pref in PREFERRED_SHOPPING_DOMAINS)


def _candidate_quality_score(cand: dict[str, Any], brand: str | None = None) -> tuple[int, int]:
    """
    Fair multi-site candidate scoring (NO famous-site monopoly):
    Rewards:
      - Official brand domain match
      - Google Shopping / Serper merchant feed hits
      - Transactional product URL footprint (/products/, /product/, /p/, /dp/)
      - INR price / buy CTA signals in snippet
    """
    url = cand.get("url", "")
    dom = extract_domain(url)
    snippet = f"{cand.get('title', '')} {cand.get('snippet', '')}".lower()
    score = 0

    if brand and len(brand) >= 2:
        b_slug = re.sub(r"[^a-z0-9]", "", brand.lower())
        if b_slug and b_slug in dom.replace(".", ""):
            score += 35  # Official brand D2C store boost

    if cand.get("source") == "serper":
        score += 25
    if ECOMMERCE_URL_FOOTPRINT_RE.search(url):
        score += 20
    if "₹" in snippet or "rs." in snippet or "inr" in snippet:
        score += 12
    if any(k in snippet for k in ("add to cart", "buy now", "add to bag", "in stock", "authorized reseller", "official store")):
        score += 10
    if _is_preferred_domain(url):
        score += 8  # Mild tie-breaker only, never crowds out D2C / regional / Shopify stores

    # Secondary sort key: prefer diverse/D2C/Shopify stores slightly on ties
    is_major_marketplace = 1 if dom in ("amazon.in", "flipkart.com") else 0
    return (-score, is_major_marketplace)


def extract_root_domain(url: str) -> str:
    """Extract registered root domain via `tldextract` (offline suffix list, zero network) or `extract_domain`."""
    try:
        import tldextract

        ext = tldextract.extract(url or "")
        if ext.domain and ext.suffix:
            return f"{ext.domain}.{ext.suffix}".lower()
    except Exception:
        pass
    return extract_domain(url)


def build_transactional_searxng_queries(query: str) -> list[tuple[str, str]]:
    """
    Stage 3 Open-Web Transactional SearXNG Fan-Out (4 parallel intent-targeted queries):
      1. Transactional Checkout Web Query
      2. Universal Storefront URL Pattern Query (Shopify, WooCommerce, Magento, BigCommerce, D2C)
      3. Brand Direct & Authorized Dealer Query
      4. Global / Cross-Border Query
    """
    q = query.strip().replace('"', "")
    return [
        (
            "searxng_checkout",
            f'"{q}" ("Add to Cart" OR "Buy Now" OR "Add to Bag") ("₹" OR "Rs.") -review -youtube -reddit -pricehistory -91mobiles -smartprix India',
        ),
        (
            "searxng_url_footprint",
            f'"{q}" (inurl:/products/ OR inurl:/product/ OR inurl:/p/ OR inurl:/dp/ OR inurl:/buy) India price',
        ),
        (
            "searxng_official_reseller",
            f'"{q}" ("official store" OR "authorized reseller" OR "buy online") India',
        ),
        (
            "searxng_global_buy",
            f'"{q}" buy online price India',
        ),
    ]


async def _query_searxng_single(
    client: httpx.AsyncClient,
    base_url: str,
    query_text: str,
    source_tag: str,
    max_results: int = 15,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        resp = await client.get(
            f"{base_url}/search",
            params={
                "q": query_text,
                "format": "json",
                "language": "en-IN",
                "safesearch": "0",
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            for r in (data.get("results") or [])[:max_results]:
                results.append(
                    {
                        "url": r.get("url", ""),
                        "title": r.get("title", ""),
                        "snippet": r.get("content", ""),
                        "source": source_tag,
                    }
                )
        else:
            logger.warning("SearXNG returned HTTP %s for %s query '%s'", resp.status_code, source_tag, query_text[:80])
    except Exception as exc:
        logger.warning("SearXNG %s query failed: %s", source_tag, exc)
    return results


async def _query_searxng(query: str, max_results: int = 16) -> list[dict[str, Any]]:
    """Fire 3 parallel intent-targeted transactional queries via self-hosted SearXNG."""
    base = (settings.searxng_url or "http://localhost:8080").rstrip("/")
    queries = build_transactional_searxng_queries(query)
    try:
        async with httpx.AsyncClient(timeout=9.0) as client:
            batches = await asyncio.gather(
                *[
                    _query_searxng_single(client, base, q_text, tag, max_results=max_results)
                    for tag, q_text in queries
                ],
                return_exceptions=True,
            )
            combined: list[dict[str, Any]] = []
            for b in batches:
                if isinstance(b, list):
                    combined.extend(b)
            # If strict boolean queries yielded very few hits on current SearXNG engines, run a clean broad fallback
            if len(combined) < 6:
                fallback = await _query_searxng_single(
                    client, base, f"{query} buy online India price", "searxng_broad", max_results=max_results
                )
                combined.extend(fallback)
            return combined
    except Exception as exc:
        logger.warning("SearXNG fan-out failed: %s", exc)
        return []


async def _query_serper_shopping(query: str, max_results: int = 30) -> list[dict[str, Any]]:
    """Angle 1 — Optional Google Shopping Graph via Serper API if SERPER_API_KEY is configured."""
    if not settings.serper_api_key:
        return []
    out: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://google.serper.dev/shopping",
                headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                json={"q": query, "gl": "in", "hl": "en", "num": max_results},
            )
            if resp.status_code == 200:
                for item in (resp.json().get("shopping") or [])[:max_results]:
                    out.append(
                        {
                            "url": item.get("link", ""),
                            "title": item.get("title", ""),
                            "snippet": f"{item.get('source', '')} {item.get('price', '')}",
                            "price_hint": parse_price_decimal(item.get("price")),
                            "seller_hint": item.get("source"),
                            "source": "serper",
                        }
                    )
    except Exception as exc:
        logger.warning("Serper shopping query failed: %s", exc)
    return out


async def _get_redis() -> aioredis.Redis | None:
    try:
        return aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1.5, socket_timeout=1.5)
    except Exception:
        return None


def select_diverse_candidates(
    hits: list[dict[str, Any]],
    current_domain: str,
    *,
    brand: str | None = None,
    max_stores: int = 15,
) -> list[dict[str, Any]]:
    """
    Angle 3 & 4 — Filter blocked non-retailer domains, deduplicate by root domain via `tldextract`
    (keeping highest-scoring URL per merchant), exclude current tab's domain,
    and return up to `max_stores` (default 15) diverse unique merchants without famous-site whitelist bias.
    """
    cur_root = extract_root_domain(f"https://{current_domain}") if current_domain else ""
    best_by_domain: dict[str, dict[str, Any]] = {}
    for hit in hits:
        u = hit.get("url", "")
        if not _is_candidate_shopping_url(u, current_domain):
            continue
        dom = extract_root_domain(u) or extract_domain(u)
        if not dom or dom == cur_root:
            continue
        if dom not in best_by_domain:
            best_by_domain[dom] = hit
        else:
            if _candidate_quality_score(hit, brand) < _candidate_quality_score(best_by_domain[dom], brand):
                best_by_domain[dom] = hit

    ranked = sorted(best_by_domain.values(), key=lambda h: _candidate_quality_score(h, brand))
    return ranked[:max_stores]


async def discover_stores(
    payload: ClientPayload,
    *,
    max_stores: int = 15,
    on_quote_callback=None,
    redis_client: aioredis.Redis | None = None,
) -> DiscoverStoresResponse:
    """
    4-Angle Universal Multi-Store Discovery Pipeline (6h Redis cache, bounded concurrency):
      1. Optional Google Shopping Graph (Serper)
      2. Parallel 4-query Open-Web Transactional SearXNG Fan-Out
      3. Strict Non-Store Domain Blocklist
      4. Deduplicate Current Page & Probe Up To 15 Diverse Unique Root Domains
    Calls `on_quote_callback(quote)` the millisecond each verified store resolves for real-time SSE streaming.
    """
    query = build_normalized_query(payload)
    if len(query) < 3:
        return DiscoverStoresResponse(query=query, quotes=[], errors=["Product query too short"])

    cache_key = f"discover:v3:{hashlib.sha256(f'{query.lower()}|{payload.retailer}'.encode()).hexdigest()}"
    r = redis_client or await _get_redis()
    if r is not None:
        try:
            cached_raw = await r.get(cache_key)
            if cached_raw:
                cached = DiscoverStoresResponse.model_validate_json(cached_raw)
                cached.cached = True
                if on_quote_callback:
                    for q in cached.quotes:
                        try:
                            await on_quote_callback(q)
                        except Exception:
                            pass
                return cached
        except Exception:
            pass

    current_domain = extract_domain(payload.url)
    clean_brand = extract_clean_brand(payload.brand, payload.title, domain=payload.url)
    searx_hits, serper_hits = await asyncio.gather(
        _query_searxng(query, max_results=16),
        _query_serper_shopping(query, max_results=30),
    )

    combined = serper_hits + searx_hits
    ranked_candidates = select_diverse_candidates(
        combined,
        current_domain,
        brand=clean_brand,
        max_stores=max_stores,
    )

    quotes: list[CompetitorQuote] = []
    rejected_count = 0
    errors: list[str] = []

    async def _evaluate_candidate(idx: int, cand: dict[str, Any]) -> CompetitorQuote | None:
        nonlocal rejected_count
        url = cand["url"]
        hit_title = cand.get("title") or ""

        if hit_title:
            pre_ok, _, _ = score_candidate_match(
                payload.title,
                hit_title,
                target_brand=payload.brand,
                target_specs=payload.variant_specs,
                min_ratio=52.0,
            )
            if not pre_ok:
                rejected_count += 1
                return None

        allow_tier2 = idx < 6
        extracted, tier_info = await extract_from_url(
            url,
            allow_tier2=allow_tier2,
            require_transactional=True,
            redis_client=r,
        )

        if extracted and extracted.listed_price and extracted.listed_price > 0:
            ok, score, _reason = score_candidate_match(
                payload.title,
                extracted.title,
                target_brand=payload.brand,
                target_specs=payload.variant_specs,
                min_ratio=68.0,
            )
            if not ok:
                rejected_count += 1
                return None
            quote = payload_to_competitor_quote(
                extracted,
                match_score=score,
                extraction_tier=tier_info,
                source=cand.get("source", "searxng"),
            )
            if on_quote_callback:
                try:
                    await on_quote_callback(quote)
                except Exception:
                    pass
            return quote

        rejected_count += 1
        if tier_info.startswith(("circuit_open:", "blocked:")):
            errors.append(f"{extract_domain(url)}: {tier_info}")
        return None

    try:
        tasks = [_evaluate_candidate(i, c) for i, c in enumerate(ranked_candidates)]
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=30.0,
        )
        for res in results:
            if isinstance(res, CompetitorQuote):
                quotes.append(res)
    except asyncio.TimeoutError:
        errors.append("Some store lookups timed out within 30s budget")

    response = DiscoverStoresResponse(
        query=query,
        quotes=quotes,
        rejected_count=rejected_count,
        cached=False,
        errors=errors,
    )

    if r is not None and quotes:
        try:
            await r.set(cache_key, response.model_dump_json(), ex=DISCOVERY_CACHE_TTL_SECONDS)
        except Exception:
            pass

    return response
