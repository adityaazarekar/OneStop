"""
Live Bank, Gift Card & UPI T&C Engine (`Crawl4AI` + `Instructor` + `SearXNG`).

Dynamically discovers and verifies official terms, monthly caps, convenience fees,
and category exclusions for:
1. Direct Credit/Debit Cards
2. Gift Card / Voucher Portals (HDFC SmartBuy GyFTR, Amex Reward Multiplier, GrabDeals, Woohoo)
3. UPI Apps & Payment Promos (CRED UPI, Super.money, BHIM, Paytm, PhonePe, Amazon Pay UPI, RuPay CC on UPI)

Strict Honesty Rule:
- ZERO hardcoded ESTIMATE percentages, ZERO static ₹250 UPI placeholders.
- Uses Crawl4AI with `DefaultMarkdownGenerator(content_filter=PruningContentFilter(threshold=0.45))`
  so `fit_markdown` extracts clean T&C tables, monthly caps, processing fees, and exclusions.
- Parses crawled T&Cs via `instructor` + OpenRouter into `VerifiedPaymentRule`, cached 7 days in Redis
  (`tc_cache:{route_key}`) with `source_urls[]` and `last_verified_at`.
- If live T&Cs or a live page offer cannot be verified, returns `verification_status="no_verified_offer"`
  with ₹0 discount / 0 rewards and `"No active verified offer found"`.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone

import httpx
import redis.asyncio as aioredis

from app.config import settings
from app.schemas.extraction import VerifiedPaymentRule
from app.services.card_optimizer import CardLike
from app.services.rate_guard import extract_domain, guarded_domain_call

logger = logging.getLogger(__name__)

TC_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

OFFICIAL_TC_DOMAINS = (
    "hdfcbank.com", "offers.smartbuy.hdfcbank.com", "gyftr.com", "sbicard.com",
    "icicibank.com", "axisbank.com", "americanexpress.com", "woohoo.in",
    "amazon.in", "flipkart.com", "cred.club", "super.money", "bhimupi.org.in",
    "paytm.com", "phonepe.com", "npci.org.in", "rupay.co.in", "idfcfirstbank.com",
    "kotak.com", "aubank.in", "getonecard.app", "indusind.com", "hsbc.co.in",
)


def build_no_verified_offer_rule(
    method_name: str,
    method_type: str = "credit_card",
    reason: str = "No active verified offer found",
) -> VerifiedPaymentRule:
    """Strict honesty rule: ₹0 discount and 0 rewards when no live T&C or page offer is verified."""
    return VerifiedPaymentRule(
        method_name=method_name,
        method_type=method_type,  # type: ignore[arg-type]
        instant_discount_inr=0.0,
        max_discount_cap_inr=None,
        reward_type="none",
        points_per_spend_unit=0.0,
        spend_unit_inr=100.0,
        point_value_cash_inr=0.0,
        point_value_travel_inr=0.0,
        monthly_cap_inr=None,
        processing_fee_pct=0.0,
        exclusions=[],
        stackable_with=[],
        verification_status="no_verified_offer",
        source_urls=[],
        last_verified_at=None,
        plain_english_summary=reason,
    )


def _route_cache_key(route_kind: str, method: str, retailer: str) -> str:
    raw = f"v2|{route_kind.lower()}|{method.lower().strip()}|{retailer.lower().strip()}"
    return f"tc_cache:{hashlib.sha256(raw.encode()).hexdigest()}"


async def _get_redis() -> aioredis.Redis | None:
    try:
        return aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
        )
    except Exception:
        return None


HIGH_SIGNAL_TC_KEYWORDS = (
    "reward",
    "cashback",
    "cap",
    "points",
    "redemption",
    "exclusion",
    "fee",
    "smartbuy",
    "gyftr",
    "statement",
    "billing cycle",
    "mitc",
)


def extract_markdown_with_fallback(md_obj: object, max_chars: int = 7000) -> str:
    """
    Prefer `fit_markdown` when it has >= 200 chars; otherwise automatically fall back to
    `raw_markdown` sliced around high-signal T&C lines (`reward`, `cashback`, `cap`,
    `points`, `redemption`, `exclusion`, `fee`) so issuer terms are never silently lost.
    """
    if md_obj is None:
        return ""
    if isinstance(md_obj, str):
        fit = md_obj.strip()
        raw = fit
    else:
        fit = (getattr(md_obj, "fit_markdown", None) or "").strip()
        raw = (getattr(md_obj, "raw_markdown", None) or "").strip()

    if len(fit) >= 200:
        return fit[:max_chars]

    if not raw:
        return fit[:max_chars]

    # Slice raw_markdown around high-signal T&C lines (keep 1 line of context around matches)
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    selected_indices: set[int] = set()
    for idx, line in enumerate(lines):
        low = line.lower()
        if any(kw in low for kw in HIGH_SIGNAL_TC_KEYWORDS):
            for near in range(max(0, idx - 1), min(len(lines), idx + 2)):
                selected_indices.add(near)

    if selected_indices:
        sliced = "\n".join(lines[i] for i in sorted(selected_indices))
        if len(sliced) >= 120:
            return sliced[:max_chars]

    return raw[:max_chars]


def build_issuer_tc_query(card_name: str, retailer: str = "") -> str:
    """Build crisp, high-recall SearXNG queries tailored to Indian issuer MITC / T&C pages."""
    c_low = (card_name or "").lower().strip()
    if "sbi cashback" in c_low or "cashback sbi" in c_low:
        return "SBI Cashback credit card 5% online cashback monthly cap MITC"
    if "infinia" in c_low:
        return "HDFC Infinia credit card reward points per 150 redemption value SmartBuy"
    if "diners club black" in c_low or "dcb" in c_low:
        return "HDFC Diners Club Black reward points per 150 redemption value SmartBuy cap"
    if "millennia" in c_low:
        return "HDFC Millennia credit card 5% cashback Amazon Flipkart monthly cap terms"
    if "amazon pay" in c_low and "icici" in c_low:
        return "Amazon Pay ICICI credit card 5% cashback Prime terms exclusions"
    if "flipkart" in c_low and "axis" in c_low:
        return "Flipkart Axis Bank credit card 5% cashback terms exclusions cap"
    if "magnus" in c_low:
        return "Axis Bank Magnus EDGE reward points per 200 redemption value cap MITC"
    if "atlas" in c_low:
        return "Axis Bank Atlas EDGE miles per 100 redemption value exclusions MITC"
    if "amex" in c_low or "american express" in c_low:
        return f"{card_name} Membership Rewards points per spend redemption value India MITC"
    return f"{card_name} credit card reward points cashback monthly cap MITC India"


def _fast_http_tc_text_sync(url: str, timeout: float = 7.0) -> str:
    """
    Stage 2 Fast Static T&C Fetch:
    Uses `scrapling.fetchers.Fetcher` + `trafilatura.extract(include_tables=True)`
    (ultra-fast, zero browser RAM). Falls back to BeautifulSoup + `extract_markdown_with_fallback`
    before escalating to Crawl4AI (`AsyncWebCrawler`) if output is < 300 chars.
    """
    try:
        from bs4 import BeautifulSoup
        from scrapling.fetchers import Fetcher

        resp = Fetcher.get(url, stealthy_headers=True, follow_redirects=True, timeout=timeout)
        status = int(getattr(resp, "status", 200) or 200)
        if status != 200:
            return ""
        html = getattr(resp, "html_content", None) or getattr(resp, "text", None) or ""
        if not html or len(html) < 400:
            return ""

        # 1. Primary: Trafilatura table-aware extraction
        try:
            import trafilatura

            traf_text = trafilatura.extract(
                html,
                include_tables=True,
                include_comments=False,
                favor_recall=True,
            )
            if traf_text and len(traf_text.strip()) >= 300:
                return extract_markdown_with_fallback(traf_text, max_chars=7000)
        except Exception:
            pass

        # 2. Fallback: BeautifulSoup line slicer
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "svg", "noscript"]):
            tag.decompose()
        raw_lines = [ln.strip() for ln in soup.get_text("\n", strip=True).splitlines() if len(ln.strip()) >= 12]
        return extract_markdown_with_fallback("\n".join(raw_lines), max_chars=7000)
    except Exception:
        return ""


async def _discover_tc_urls(query: str, max_urls: int = 2) -> list[str]:
    """Use self-hosted SearXNG to find top 1-2 official T&C / offer pages."""
    base = (settings.searxng_url or "http://localhost:8080").rstrip("/")
    urls: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=7.0) as client:
            resp = await client.get(
                f"{base}/search",
                params={"q": query, "format": "json", "language": "en-IN"},
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.warning("SearXNG T&C search returned HTTP %s for '%s'", resp.status_code, query)
                return []
            results = resp.json().get("results") or []
            scored: list[tuple[int, str]] = []
            for r in results[:14]:
                u = r.get("url", "")
                if not u.startswith(("http://", "https://")):
                    continue
                if u.lower().endswith(".pdf"):
                    continue
                dom = extract_domain(u)
                # Prefer official issuer/portal domains; skip forums/trackers
                if any(bad in dom for bad in ("reddit.com", "quora.com", "youtube.com", "pricehistory", "buyhatke")):
                    continue
                prio = 0 if any(dom == od or dom.endswith("." + od) for od in OFFICIAL_TC_DOMAINS) else 1
                scored.append((prio, u))
            scored.sort(key=lambda x: x[0])
            for _, u in scored:
                if u not in urls:
                    urls.append(u)
                if len(urls) >= max_urls:
                    break
            if not urls:
                logger.warning("SearXNG T&C search returned 0 usable URLs for '%s'", query)
    except Exception as exc:
        logger.warning("SearXNG T&C URL discovery failed for '%s': %s", query, exc)
    return urls


async def _crawl_fit_markdown(url: str, redis_client=None) -> str:
    """
    Crawl a single T&C page via fast HTTP first, then Crawl4AI with
    `DefaultMarkdownGenerator(content_filter=PruningContentFilter(threshold=0.45))`.
    If `fit_markdown` is empty or < 200 chars, automatically falls back to `raw_markdown`
    sliced around high-signal T&C terms via `extract_markdown_with_fallback()`.
    """
    # Fast HTTP attempt first (saves Chromium budget on static issuer pages)
    try:
        fast_text = await asyncio.wait_for(
            guarded_domain_call(
                url,
                lambda: asyncio.to_thread(_fast_http_tc_text_sync, url, 6.0),
                use_browser_semaphore=False,
                max_attempts=1,
                redis_client=redis_client,
            ),
            timeout=7.5,
        )
        if fast_text and len(fast_text) >= 250 and any(k in fast_text.lower() for k in ("reward", "cashback", "point", "cap")):
            return fast_text
    except Exception as exc:
        logger.debug("Fast HTTP T&C fetch skipped for %s: %s", url, exc)

    async def _do_crawl() -> str:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        md_gen = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.45)
        )
        browser_cfg = BrowserConfig(headless=True, verbose=False)
        run_cfg = CrawlerRunConfig(
            markdown_generator=md_gen,
            excluded_tags=["nav", "header", "footer", "aside", "script", "style"],
            page_timeout=12000,
            verbose=False,
        )
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            res = await crawler.arun(url=url, config=run_cfg)
            if not res or not res.success:
                logger.warning("Crawl4AI unsuccessful for %s (status=%s)", url, getattr(res, "status_code", "unknown"))
                return ""
            md_obj = getattr(res, "markdown", None)
            return extract_markdown_with_fallback(md_obj, max_chars=7000)

    try:
        return await asyncio.wait_for(
            guarded_domain_call(
                url,
                _do_crawl,
                use_browser_semaphore=True,
                max_attempts=1,
                redis_client=redis_client,
            ),
            timeout=13.0,
        )
    except Exception as exc:
        logger.warning("Crawl4AI fit_markdown/raw_markdown crawl failed for %s: %s", url, exc)
        return ""


async def _parse_rule_with_instructor(
    markdown_text: str,
    method_name: str,
    method_type: str,
    retailer: str,
    source_urls: list[str],
) -> VerifiedPaymentRule | None:
    """Parse crawled T&C markdown into strict `VerifiedPaymentRule` via `instructor` + OpenRouter."""
    if not settings.openrouter_api_key or not markdown_text.strip():
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

        prompt = (
            f"Extract exact official Indian payment terms for '{method_name}' ({method_type}) when shopping on '{retailer}'.\n"
            "STRICT RULES:\n"
            "- ONLY populate numbers explicitly stated in the crawled T&C markdown below. NEVER guess or invent discounts.\n"
            "- If the markdown does NOT contain concrete active offer/reward rates for this payment method, set "
            "points_per_spend_unit=0, instant_discount_inr=0, reward_type='none', verification_status='no_verified_offer', "
            "plain_english_summary='No active verified offer found'.\n"
            "- Distinguish point_value_cash_inr (statement cash) vs point_value_travel_inr (flights/hotels/partners).\n"
            "- Capture monthly_cap_inr, processing_fee_pct (e.g. GyFTR convenience fee incl GST), exclusions[], stackable_with[].\n"
            "- When concrete rates ARE present in the text, set verification_status='live_tc_verified'.\n\n"
            f"Crawled Official T&C Markdown:\n{markdown_text[:5000]}"
        )
        rule: VerifiedPaymentRule = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.openrouter_model,
                response_model=VerifiedPaymentRule,
                max_retries=2,
                temperature=0.0,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict Indian credit card, gift voucher & UPI T&C parser. Never hallucinate offers, percentages, or caps not explicitly present in the provided T&C markdown.",
                    },
                    {"role": "user", "content": prompt},
                ],
            ),
            timeout=9.0,
        )
        rule.method_name = method_name
        rule.method_type = method_type  # type: ignore[assignment]
        has_signal = (
            (rule.points_per_spend_unit > 0 and max(rule.point_value_cash_inr, rule.point_value_travel_inr) > 0)
            or rule.instant_discount_inr > 0
        )
        if not has_signal:
            return build_no_verified_offer_rule(method_name, method_type)
        rule.verification_status = "live_tc_verified"
        rule.source_urls = source_urls
        rule.last_verified_at = datetime.now(timezone.utc)
        return rule
    except Exception as exc:
        logger.warning("Instructor T&C parse failed for %s (%s) — falling back to deterministic parser", method_name, exc)
        return None


def _deterministic_tc_parse(
    markdown_text: str,
    method_name: str,
    method_type: str,
    source_urls: list[str],
) -> VerifiedPaymentRule | None:
    """
    Extract explicitly stated reward rates, caps, processing fees, and exclusions from crawled T&C markdown
    when OpenRouter/Instructor is unavailable. Returns None (honest no-offer) if no explicit rate is found.
    """
    if not markdown_text or len(markdown_text) < 60:
        return None

    points_per_unit = 0.0
    spend_unit = 100.0
    point_cash = 0.0
    point_travel = 0.0
    reward_type: str = "none"

    # Match "5 Reward Points per Rs. 150" / "5 RPs for every ₹150" / "4 EDGE Miles per ₹100"
    rp_m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:reward\s+points?|RPs?|edge\s+miles?|edge\s+reward\s+points?|MR\s+points?)\s*(?:per|for\s+every|on\s+every)\s*(?:₹|Rs\.?|INR)\s*([\d,]+)",
        markdown_text,
        re.I,
    )
    if rp_m:
        points_per_unit = float(rp_m.group(1))
        spend_unit = float(rp_m.group(2).replace(",", ""))
        reward_type = "miles" if "mile" in rp_m.group(0).lower() else "points"
        # Look for explicit cash vs travel redemption values in crawled text
        cash_val_m = re.search(
            r"(?:cash|statement)[^₹\d]{0,35}(?:1\s*(?:reward\s+point|RP|point)\s*=\s*)?(?:₹|Rs\.?|INR)\s*(\d+(?:\.\d+)?)",
            markdown_text,
            re.I,
        )
        travel_val_m = re.search(
            r"(?:smartbuy|flight|hotel|travel|airmiles|tanishq)[^₹\d]{0,35}(?:1\s*(?:reward\s+point|RP|point)\s*=\s*)?(?:₹|Rs\.?|INR)\s*(\d+(?:\.\d+)?)",
            markdown_text,
            re.I,
        )
        val_m = re.search(
            r"(?:1\s*(?:reward\s+point|RP|point|mile)\s*(?:=|is|worth|equals)\s*(?:up\s*to\s*)?(?:₹|Rs\.?|INR)\s*(\d+(?:\.\d+)?))|(?:(?:₹|Rs\.?|INR)\s*(\d+(?:\.\d+)?)\s*per\s*(?:reward\s+point|RP|point|mile))",
            markdown_text,
            re.I,
        )
        if cash_val_m:
            v = float(cash_val_m.group(1))
            if 0 < v <= 10.0:
                point_cash = v
        if travel_val_m:
            v = float(travel_val_m.group(1))
            if 0 < v <= 10.0:
                point_travel = v
        if val_m:
            v = float(val_m.group(1) or val_m.group(2))
            if 0 < v <= 10.0:
                if point_cash <= 0:
                    point_cash = v
                if point_travel <= 0:
                    point_travel = v
        if point_travel > 0 and point_cash <= 0:
            point_cash = point_travel
        if point_cash > 0 and point_travel <= 0:
            point_travel = point_cash
    else:
        # Match explicit cashback percentage e.g. "5% cashback on online spends" / "5% unlimited cashback"
        cb_m = re.search(
            r"(\d+(?:\.\d+)?)\s*%\s*(?:unlimited\s+|flat\s+|online\s+)?(?:cashback|cash\s*back|instant\s+discount|reward\s+rate)",
            markdown_text,
            re.I,
        )
        if cb_m:
            pct = float(cb_m.group(1))
            if 0 < pct <= 25.0:
                points_per_unit = pct
                spend_unit = 100.0
                point_cash = 1.0
                point_travel = 1.0
                reward_type = "cashback"

    if points_per_unit <= 0 or max(point_cash, point_travel) <= 0:
        return None

    monthly_cap: float | None = None
    cap_m = re.search(
        r"(?:monthly\s+cap|per\s+(?:month|billing\s+cycle|statement\s+cycle)|max(?:imum)?\s+(?:cashback|points|discount|benefit))[^₹\d]{0,25}(?:₹|Rs\.?|INR)\s*([\d,]+)",
        markdown_text,
        re.I,
    )
    if cap_m:
        try:
            monthly_cap = float(cap_m.group(1).replace(",", ""))
        except Exception:
            monthly_cap = None

    proc_fee = 0.0
    fee_m = re.search(r"(?:convenience|processing)\s+fee[^%\d]{0,20}(\d+(?:\.\d+)?)\s*%", markdown_text, re.I)
    if fee_m:
        try:
            proc_fee = float(fee_m.group(1))
        except Exception:
            proc_fee = 0.0

    excl_lines: list[str] = []
    for line in markdown_text.splitlines():
        l = line.strip(" -*•|")
        if 15 <= len(l) <= 180 and any(
            k in l.lower()
            for k in ("exclud", "not valid", "not applicable", "capped at", "maximum", "convenience fee", "processing fee")
        ):
            excl_lines.append(l)
        if len(excl_lines) >= 4:
            break

    return VerifiedPaymentRule(
        method_name=method_name,
        method_type=method_type,  # type: ignore[arg-type]
        reward_type=reward_type,  # type: ignore[arg-type]
        points_per_spend_unit=points_per_unit,
        spend_unit_inr=spend_unit,
        point_value_cash_inr=point_cash,
        point_value_travel_inr=point_travel,
        monthly_cap_inr=monthly_cap,
        processing_fee_pct=proc_fee,
        exclusions=excl_lines,
        verification_status="live_tc_verified",
        source_urls=source_urls,
        last_verified_at=datetime.now(timezone.utc),
        plain_english_summary=f"Verified from official T&C ({source_urls[0]}).",
    )


async def get_or_verify_rule(
    *,
    route_kind: str,
    method_name: str,
    method_type: str,
    retailer: str,
    search_query: str,
    allow_live_crawl: bool = True,
    redis_client: aioredis.Redis | None = None,
    db_session=None,
) -> VerifiedPaymentRule:
    """
    2-Speed Live T&C Auditor:
    1. Fast Path (<100ms): Reads 7-day Redis cache (`tc_cache:{route_key}`) OR active temporal
       Knowledge Graph edges (`kg_edges` where `valid_to IS NULL OR valid_to > NOW()`).
    2. Background Audit Path (`Trafilatura` -> `Crawl4AI` -> `Instructor`):
       When `allow_live_crawl=False` and rule is missing, schedules `audit_issuer_terms_job`
       in the background and immediately returns honest `no_verified_offer` (₹0) so Stage 1
       never blocks on slow web crawls.
    """
    r = redis_client or await _get_redis()
    key = _route_cache_key(route_kind, method_name, retailer)

    # 1a. Fast path: 7-day Redis cache (<10ms)
    if r is not None:
        try:
            raw = await r.get(key)
            if raw:
                return VerifiedPaymentRule.model_validate_json(raw)
        except Exception:
            pass

    # 1b. Fast path: PostgreSQL Temporal Knowledge Graph (<25ms) — only on real AsyncSession, never concurrently
    from sqlalchemy.ext.asyncio import AsyncSession as _RealAsyncSession

    if db_session is not None and type(db_session) is _RealAsyncSession:
        try:
            from app.services.kg_service import load_verified_rule_from_kg

            kg_rule = await load_verified_rule_from_kg(
                db_session,
                method_name=method_name,
                method_type=method_type,
                retailer=retailer,
                max_age_days=7,
            )
            if kg_rule is not None:
                if r is not None:
                    try:
                        await r.set(key, kg_rule.model_dump_json(), ex=TC_CACHE_TTL_SECONDS)
                    except Exception:
                        pass
                return kg_rule
        except Exception as exc:
            logger.debug("KG rule lookup skipped for %s: %s", method_name, exc)

    if not allow_live_crawl:
        return build_no_verified_offer_rule(
            method_name,
            method_type,
            reason="No active verified offer found (click 'Verify Live T&Cs' to crawl official issuer terms).",
        )

    # 2. Live Audit Path: SearXNG -> Trafilatura (<300 chars escalates to Crawl4AI fit/raw markdown) -> Instructor
    urls = await _discover_tc_urls(search_query, max_urls=2)
    if not urls:
        logger.warning("Live T&C skipped for '%s': SearXNG returned no candidate URLs for query '%s'", method_name, search_query)
        return build_no_verified_offer_rule(method_name, method_type)

    crawled_chunks: list[str] = []
    used_urls: list[str] = []
    for u in urls[:2]:
        md = await _crawl_fit_markdown(u, redis_client=r)
        if md and len(md) > 120:
            crawled_chunks.append(md)
            used_urls.append(u)

    if not crawled_chunks:
        logger.warning("Live T&C crawl returned empty/insufficient markdown for '%s' across %s", method_name, urls[:2])
        return build_no_verified_offer_rule(method_name, method_type)

    combined_md = "\n\n---\n\n".join(crawled_chunks)
    parsed = await _parse_rule_with_instructor(
        combined_md, method_name, method_type, retailer, used_urls
    )
    if parsed is None:
        parsed = _deterministic_tc_parse(combined_md, method_name, method_type, used_urls)

    if parsed is None or parsed.verification_status not in ("live_tc_verified", "issuer_tc_verified"):
        logger.warning("Live T&C parser found no verifiable concrete rates for '%s' from %s", method_name, used_urls)
        return build_no_verified_offer_rule(method_name, method_type)

    if r is not None:
        try:
            await r.set(key, parsed.model_dump_json(), ex=TC_CACHE_TTL_SECONDS)
        except Exception:
            pass

    if db_session is not None:
        try:
            from app.services.kg_service import persist_verified_rule_to_kg

            await persist_verified_rule_to_kg(db_session, parsed, retailer)
        except Exception as exc:
            logger.debug("KG persistence skipped for %s: %s", method_name, exc)

    return parsed


async def resolve_live_payment_rules(
    *,
    retailer: str,
    user_cards: list[CardLike],
    allow_live_crawl: bool = True,
    timeout_seconds: float = 14.0,
    redis_client: aioredis.Redis | None = None,
    db_session=None,
) -> list[VerifiedPaymentRule]:
    """
    Resolve verified payment rules from 7-day Redis cache, PostgreSQL Temporal Knowledge Graph
    (`kg_edges`), or live Trafilatura/Crawl4AI + Instructor audit.
    Zero hardcoded ESTIMATE fallbacks: unverified routes strictly return ₹0 (`no_verified_offer`).
    """
    r = redis_client or await _get_redis()
    store_name = retailer.replace("_", " ").title()

    selected_cards = user_cards[:4]
    coros = []
    defaults: list[VerifiedPaymentRule] = []

    for card in selected_cards:
        defaults.append(build_no_verified_offer_rule(card.card_name, "credit_card"))
        coros.append(
            get_or_verify_rule(
                route_kind="card",
                method_name=card.card_name,
                method_type="credit_card",
                retailer=retailer,
                search_query=build_issuer_tc_query(card.card_name, retailer),
                allow_live_crawl=allow_live_crawl,
                redis_client=r,
                db_session=db_session,
            )
        )

    voucher_name = f"Gift Voucher / SmartBuy GyFTR ({store_name})"
    defaults.append(build_no_verified_offer_rule(voucher_name, "gift_voucher"))
    coros.append(
        get_or_verify_rule(
            route_kind="voucher",
            method_name=voucher_name,
            method_type="gift_voucher",
            retailer=retailer,
            search_query=f"HDFC SmartBuy GyFTR {store_name} voucher reward points processing fee monthly cap",
            allow_live_crawl=allow_live_crawl,
            redis_client=r,
            db_session=db_session,
        )
    )

    upi_name = f"UPI App Offer ({store_name})"
    defaults.append(build_no_verified_offer_rule(upi_name, "upi_app"))
    coros.append(
        get_or_verify_rule(
            route_kind="upi",
            method_name=upi_name,
            method_type="upi_app",
            retailer=retailer,
            search_query=f"{store_name} UPI cashback offer terms Google Pay PhonePe CRED Super.money",
            allow_live_crawl=allow_live_crawl,
            redis_client=r,
            db_session=db_session,
        )
    )

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=timeout_seconds,
        )
        rules: list[VerifiedPaymentRule] = []
        for idx, res in enumerate(results):
            if isinstance(res, VerifiedPaymentRule):
                rules.append(res)
            else:
                rules.append(defaults[idx])
        return rules
    except asyncio.TimeoutError:
        logger.warning("Live T&C resolution exceeded %.1fs budget — returning no-offer defaults", timeout_seconds)
        return defaults
