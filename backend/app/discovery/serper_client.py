"""
Serper Google Shopping Candidate Generator (`backend/app/discovery/serper_client.py`).

Enforces M4 invariants:
- `POST https://google.serper.dev/shopping` with `X-API-KEY` and single-dict JSON body
  `{"q": q, "gl": "in", "hl": "en", "num": num}` (never batch arrays).
- `SERP_MODE=live|replay`: caches 6h in Redis (`serp_cache:{sha256}`) + local replay fixture file.
- Logs every live call and credit consumed; enforces per-product credit budget.
- Queries (max 3/product) derived strictly from `VariantSpec` (`<brand> <core_model> <hard attrs>`,
  `<gtin>` if present, `<core_model> <storage/color/size>` fallback) — NEVER from the raw 20-word SEO title.
- SERP rows are LEADS (`SerpLead`): never marked `Bucket.EXACT` until their PDP is fetched and a
  `VariantSpec` is built. Any SERP-only lead is `Bucket.UNVERIFIED` with reason
  `"price from search listing, unconfirmed"`.
"""
from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.matching.contracts import Bucket, MatchDecision, VariantSpec, Verdict
from app.services.rate_guard import extract_domain
from app.services.universal_scraper import (
    has_transactional_checkout_signals,
    is_blocked_non_retailer_domain,
    parse_price_decimal,
)

logger = logging.getLogger(__name__)

SERP_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
DEFAULT_REPLAY_FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "serp_replay.json"

_TOTAL_SERPER_CREDITS_USED: int = 0
_HARD_ATTR_ORDER = ("storage", "ram", "color", "size", "unit_size", "pack_qty", "condition")


def get_serper_credits_used() -> int:
    return _TOTAL_SERPER_CREDITS_USED


def reset_serper_credits_used() -> None:
    global _TOTAL_SERPER_CREDITS_USED
    _TOTAL_SERPER_CREDITS_USED = 0


class SerpLead(BaseModel):
    """A raw search listing lead from Serper Shopping. Never marked EXACT without PDP extraction."""

    query_used: str
    title: str
    url: str
    source_store: str
    price_hint: Decimal | None = None
    image_url: str | None = None
    pdp_html_fixture: str | None = None


def lead_to_unverified_decision(lead: SerpLead) -> MatchDecision:
    """
    A SERP-only lead whose PDP has not been fetched/verified MUST always be `Bucket.UNVERIFIED`
    with `"price from search listing, unconfirmed"` — never `Bucket.EXACT`.
    """
    return MatchDecision(
        bucket=Bucket.UNVERIFIED,
        identity=Verdict.UNKNOWN,
        diffs=[],
        confidence=0.4,
        reasons=["price from search listing, unconfirmed"],
        judged_by="deterministic",
    )


def build_serper_queries(spec: VariantSpec, max_queries: int = 3) -> list[str]:
    """
    Derive at most 3 high-signal search queries strictly from `VariantSpec`
    (never from the raw 20-word SEO title):
      1. "<brand> <core_model> <hard attrs>"
      2. "<gtin>" (if present)
      3. "<core_model> <storage/color/size>" fallback
    """
    queries: list[str] = []
    brand = (spec.brand or "").strip()
    core = (spec.core_model or "").strip()

    hard_parts: list[str] = []
    for k in _HARD_ATTR_ORDER:
        attr = spec.attrs.get(k)
        if attr is None:
            continue
        val = (attr.norm or attr.raw or "").strip()
        if not val:
            continue
        if val.upper().startswith("ALPHA:"):
            val = val.split(":", 1)[1]
        elif ":" in val and k == "size":
            val = val.replace(":", " ")
        elif k == "pack_qty" and val.isdigit():
            if int(val) <= 1:
                continue
            val = f"Pack of {val}"
        if k == "condition" and val.lower() == "new":
            continue
        hard_parts.append(val)

    # Query 1: "<brand> <core_model> <hard attrs>"
    q1_tokens: list[str] = []
    if brand and brand.lower() not in core.lower():
        q1_tokens.append(brand)
    if core:
        q1_tokens.append(core)
    q1_tokens.extend(hard_parts[:3])
    q1 = " ".join(" ".join(q1_tokens).split()).strip()
    if q1:
        queries.append(q1)

    # Query 2: "<gtin>" if present
    gtin = (spec.gtin or "").strip()
    if gtin and gtin not in queries:
        queries.append(gtin)

    # Query 3: "<core_model> <storage/color>" fallback
    primary_hard = [
        (spec.attrs[k].norm or spec.attrs[k].raw or "").replace("ALPHA:", "").replace(":", " ").strip()
        for k in ("storage", "color", "size", "unit_size")
        if k in spec.attrs and (spec.attrs[k].norm or spec.attrs[k].raw)
    ]
    q3_tokens = [core] + [p for p in primary_hard[:2] if p]
    q3 = " ".join(" ".join(q3_tokens).split()).strip()
    if q3 and q3 not in queries:
        queries.append(q3)

    # If still < max_queries and brand+core without attrs is distinct, add "<brand> <core_model>"
    if len(queries) < max_queries and brand and core:
        q_base = " ".join(f"{brand} {core}".split()).strip()
        if q_base and q_base not in queries:
            queries.append(q_base)

    return queries[:max_queries]


class SerperShoppingClient:
    """
    Serper Google Shopping client with `SERP_MODE=live|replay`, 6h Redis + fixture caching,
    per-product credit cap, and live credit consumption logging.
    """

    ENDPOINT = "https://google.serper.dev/shopping"

    def __init__(
        self,
        *,
        mode: Literal["live", "replay"] | None = None,
        api_key: str | None = None,
        redis_client: Any = None,
        replay_fixture_path: Path | None = None,
        max_credits_per_product: int | None = None,
    ) -> None:
        self.mode: Literal["live", "replay"] = mode or settings.serp_mode
        self.api_key = api_key if api_key is not None else settings.serper_api_key
        self.redis = redis_client
        self.replay_path = replay_fixture_path or DEFAULT_REPLAY_FIXTURE_PATH
        self.max_credits = max_credits_per_product or settings.max_serper_credits_per_product
        self._memory_cache: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _cache_key(query: str, gl: str = "in", hl: str = "en") -> str:
        norm = f"{query.strip().casefold()}|{gl}|{hl}"
        return f"serp_cache:{hashlib.sha256(norm.encode()).hexdigest()}"

    def _load_replay_store(self) -> dict[str, Any]:
        if not self.replay_path.exists():
            return {}
        try:
            return json.loads(self.replay_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_replay_entry(self, query: str, items: list[dict[str, Any]]) -> None:
        try:
            self.replay_path.parent.mkdir(parents=True, exist_ok=True)
            store = self._load_replay_store()
            store[query.strip().casefold()] = items
            self.replay_path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.debug("Could not persist replay fixture: %s", exc)

    async def search_query(self, query: str, num: int = 20) -> list[SerpLead]:
        """Run a single Serper shopping query (cached 6h in Redis/memory + replay fixtures)."""
        global _TOTAL_SERPER_CREDITS_USED
        q_clean = " ".join(query.strip().split())
        if not q_clean:
            return []

        ckey = self._cache_key(q_clean)

        # 1. Check in-memory and Redis 6h cache
        if ckey in self._memory_cache:
            return self._items_to_leads(q_clean, self._memory_cache[ckey][:num])

        if self.redis is not None:
            try:
                raw_cached = await self.redis.get(ckey)
                if raw_cached:
                    items = json.loads(raw_cached)
                    self._memory_cache[ckey] = items
                    return self._items_to_leads(q_clean, items[:num])
            except Exception:
                pass

        # 2. Check recorded replay fixture
        store = self._load_replay_store()
        q_norm = q_clean.casefold()
        if q_norm in store:
            items = store[q_norm]
            self._memory_cache[ckey] = items
            return self._items_to_leads(q_clean, items[:num])

        if self.mode == "replay":
            # In replay mode, never hit network; check prefix/substring fallback in recorded store
            for k_rec, v_items in store.items():
                if k_rec in q_norm or q_norm in k_rec:
                    self._memory_cache[ckey] = v_items
                    return self._items_to_leads(q_clean, v_items[:num])
            return []

        # 3. Live Serper POST call (single dict body, NEVER batch arrays)
        if not self.api_key:
            logger.warning("SERPER_API_KEY missing in live mode; returning 0 leads for %r", q_clean)
            return []

        body = {"q": q_clean, "gl": "in", "hl": "en", "num": num}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self.ENDPOINT,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                json=body,
            )
            _TOTAL_SERPER_CREDITS_USED += 1
            logger.info(
                "Serper live call: query=%r status=%d credits_used=1 total_session_credits=%d",
                q_clean,
                resp.status_code,
                _TOTAL_SERPER_CREDITS_USED,
            )
            if resp.status_code != 200:
                return []
            payload = resp.json()
            shopping_items = payload.get("shopping") or []

        self._memory_cache[ckey] = shopping_items
        self._save_replay_entry(q_clean, shopping_items)
        if self.redis is not None:
            try:
                await self.redis.set(ckey, json.dumps(shopping_items), ex=SERP_CACHE_TTL_SECONDS)
            except Exception:
                pass

        return self._items_to_leads(q_clean, shopping_items[:num])

    def _items_to_leads(self, query_used: str, items: list[dict[str, Any]]) -> list[SerpLead]:
        leads: list[SerpLead] = []
        for item in items:
            url = str(item.get("link") or item.get("url") or "").strip()
            if not url or is_blocked_non_retailer_domain(url):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            price_dec = parse_price_decimal(item.get("price"))
            leads.append(
                SerpLead(
                    query_used=query_used,
                    title=title,
                    url=url,
                    source_store=str(item.get("source") or extract_domain(url) or "unknown"),
                    price_hint=price_dec,
                    image_url=item.get("imageUrl") or item.get("thumbnail"),
                    pdp_html_fixture=item.get("pdp_html_fixture"),
                )
            )
        return leads

    async def discover_leads_for_spec(
        self,
        spec: VariantSpec,
        *,
        num_per_query: int = 15,
    ) -> list[SerpLead]:
        """
        Execute up to `min(3, self.max_credits)` queries derived from `spec`,
        deduplicating leads by URL and excluding blocked non-retailer domains.
        """
        queries = build_serper_queries(spec, max_queries=min(3, self.max_credits))
        seen_urls: set[str] = set()
        all_leads: list[SerpLead] = []
        source_dom = extract_domain(spec.url) if spec.url else ""

        for q in queries:
            leads = await self.search_query(q, num=num_per_query)
            for lead in leads:
                if lead.url in seen_urls:
                    continue
                if source_dom and lead.url == spec.url:
                    continue
                seen_urls.add(lead.url)
                all_leads.append(lead)
        return all_leads


def validate_pdp_is_transactional(html: str, meta: dict[str, Any] | None = None) -> bool:
    """Gatekeeper: drop non-transactional pages lacking Add-to-Cart / Buy-Now / Offer+InStock signals."""
    return has_transactional_checkout_signals(html, meta)
