"""
LLM parser using OpenRouter (openai-compatible async SDK).

Supports any model on OpenRouter (free tier included).
SHA-256 Redis hash caching (7-day TTL) ensures identical inputs
are never sent twice, keeping API cost at ₹0 for repeat queries.
Strips markdown code fences from free-model responses before Pydantic parse.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import TypeVar

import redis.asyncio as aioredis
from pydantic import BaseModel

from app.config import settings
from app.schemas.extraction import (
    BankOfferRule,
    BankOfferRuleList,
    ExecutiveSummary,
    InvoiceExtracted,
    ReviewScamSignals,
    VariantAttributes,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.M)
_CODE_FENCE_END_RE = re.compile(r"\n?```\s*$", re.M)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that free models sometimes wrap JSON in."""
    text = _CODE_FENCE_RE.sub("", text.strip())
    text = _CODE_FENCE_END_RE.sub("", text.strip())
    return text.strip()


def _deterministic_bank_offers(raw_texts: list[str]) -> list[BankOfferRule]:
    """
    Accurate deterministic parser for live Indian on-page bank offers.
    - Extracts every bank mentioned in a line (e.g. 'HDFC, ICICI & SBI Credit Card').
    - Never confuses min-spend or max-cap with flat discount when percentage discount is present.
    - Parses card_type (credit/debit/emi/both), flat or %, max_discount_cap, and min_txn_amount.
    """
    rules: list[BankOfferRule] = []
    seen_keys: set[tuple[str, float, float, float, float]] = set()
    bank_pat = re.compile(
        r"\b(hdfc|icici|sbi|axis|kotak|amex|american\s+express|citi|rbl|idfc|bob|bank\s+of\s+baroda|standard\s+chartered|hsbc|federal|onecard|one\s+card|yes\s+bank|indusind|au\s+bank|rupay)\b",
        re.I,
    )
    bank_norm_map = {
        "AMERICAN EXPRESS": "AMEX",
        "BANK OF BARODA": "BOB",
        "ONE CARD": "ONECARD",
    }

    for text in raw_texts:
        t = " ".join((text or "").split()).strip()
        if not t or len(t) < 12:
            continue
        low = t.lower()
        # Skip pure no-cost EMI interest-savings lines that are not instant discounts
        if "no cost emi" in low and "instant discount" not in low and "off" not in low and "cashback" not in low:
            continue

        found_banks: list[str] = []
        for bm in bank_pat.finditer(t):
            b = bm.group(1).upper()
            b = bank_norm_map.get(b, b)
            if b not in found_banks:
                found_banks.append(b)

        # Pure UPI app offers (e.g. "₹1,101 off Google Pay UPI", "₹1,101 off PhonePe") belong to the UPI route,
        # NEVER as an "ALL BANKS" credit card offer!
        if not found_banks and re.search(r"\b(?:upi|google\s*pay|gpay|phonepe|paytm|cred|super\.money|bhim)\b", low):
            continue

        # Parse min spend FIRST and strip it before looking for flat discount amounts
        min_m = re.search(
            r"(?:min(?:imum)?\s*(?:purchase|spend|order|txn|transaction)(?:\s+value|\s+amount|\s+of)?|orders?\s+above|on\s+orders?\s+of)\s*(?:₹|Rs\.?|INR)?\s*([\d,]+)",
            t,
            re.I,
        )
        min_txn = float(min_m.group(1).replace(",", "")) if min_m else 0.0
        t_no_min = t[: min_m.start()] + " " + t[min_m.end() :] if min_m else t

        pct_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:instant\s+)?(?:discount|off|cashback|back)", t_no_min, re.I)
        cap_m = re.search(
            r"(?:up\s*to|upto|max(?:imum)?(?:\s+discount)?)\s*(?:₹|Rs\.?|INR)\s*([\d,]+)",
            t_no_min,
            re.I,
        )

        pct = float(pct_m.group(1)) if pct_m else 0.0
        cap = float(cap_m.group(1).replace(",", "")) if cap_m else 0.0
        flat = 0.0

        if pct == 0.0:
            flat_m = re.search(
                r"(?:flat|additional|extra|instant\s+discount\s+of|get|save|up\s*to|upto)?\s*(?:₹|Rs\.?|INR)\s*([\d,]+)(?:\.\d{1,2})?\s*(?:instant\s+discount|off|discount|cashback|back)?",
                t_no_min,
                re.I,
            )
            if flat_m:
                cand_flat = float(flat_m.group(1).replace(",", ""))
                if cand_flat > 0 and cand_flat != min_txn:
                    flat = cand_flat

        if flat <= 0 and pct <= 0:
            continue

        if not found_banks:
            found_banks = ["ALL BANKS"]

        if "non-emi" in low:
            card_type = "debit" if ("debit" in low and "credit" not in low) else ("credit" if "credit" in low else "both")
        elif "emi" in low and "non-emi" not in low:
            card_type = "emi"
        elif "debit" in low and "credit" not in low:
            card_type = "debit"
        elif "credit" in low and "debit" not in low:
            card_type = "credit"
        else:
            card_type = "both"

        for bname in found_banks:
            dedup_key = (bname, flat, pct, cap, min_txn)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            rules.append(
                BankOfferRule(
                    bank_name=bname,
                    card_type=card_type,  # type: ignore[arg-type]
                    card_network="rupay" if "rupay" in low else ("amex" if bname == "AMEX" else "any"),
                    min_txn_amount=min_txn,
                    discount_flat=flat,
                    discount_pct=pct,
                    max_discount_cap=cap,
                    summary=t[:160],
                )
            )
    return rules


def _deterministic_variant(title: str, specs_text: str = "") -> VariantAttributes:
    """Instant regex parser for product specs when LLM is offline/slow."""
    from app.services.product_matcher import extract_clean_brand

    combined = f"{title} {specs_text}"
    ram_m = re.search(r"(\d+)\s*GB\s*RAM", combined, re.I)
    stor_m = re.search(r"(\d+)\s*(?:GB|TB)\s*(?:ROM|Storage|SSD|Internal|Space)", combined, re.I)
    color_m = re.search(r"\b(Black|White|Blue|Gold|Silver|Red|Green|Purple|Titanium|Midnight|Starlight|Natural|Yellow|Pink)\b", combined, re.I)

    brand = extract_clean_brand(None, title) or "Unknown"
    storage = int(stor_m.group(1)) if stor_m else None
    if stor_m and "tb" in stor_m.group(0).lower():
        storage = storage * 1024

    return VariantAttributes(
        brand=brand,
        model_family="Device",
        model_name=title[:80].strip(),
        ram_gb=int(ram_m.group(1)) if ram_m else None,
        storage_gb=storage,
        color=color_m.group(1) if color_m else None,
        condition="new",
    )


def _deterministic_reviews(reviews: list[str]) -> ReviewScamSignals:
    """Instant keyword scanner for scam signals when LLM is offline/slow."""
    fake_cnt = 0
    open_box_cnt = 0
    seal_broken_cnt = 0
    refurb_cnt = 0
    wrong_item_cnt = 0
    red_flags = []

    for r in reviews:
        low = r.lower()
        if "fake" in low or "duplicate" in low or "counterfeit" in low:
            fake_cnt += 1
            red_flags.append("Mentions fake/counterfeit unit")
        if "open box" in low or "already opened" in low:
            open_box_cnt += 1
            red_flags.append("Open box delivery report")
        if "seal broken" in low or "broken seal" in low:
            seal_broken_cnt += 1
            red_flags.append("Tampered packaging / broken seal")
        if "refurbished" in low or "used product" in low:
            refurb_cnt += 1
            red_flags.append("Refurbished unit delivered as new")
        if "wrong product" in low or "different item" in low:
            wrong_item_cnt += 1
            red_flags.append("Wrong model received")

    total_scam_mentions = fake_cnt + open_box_cnt + seal_broken_cnt + refurb_cnt + wrong_item_cnt
    pct = min(100.0, total_scam_mentions * 15.0)
    impact = max(-40, -10 * total_scam_mentions)

    return ReviewScamSignals(
        fake_product_mentions=fake_cnt,
        open_box_mentions=open_box_cnt,
        seal_broken_mentions=seal_broken_cnt,
        refurbished_mentions=refurb_cnt,
        wrong_product_mentions=wrong_item_cnt,
        suspicious_review_pct=pct,
        top_red_flags=list(set(red_flags))[:3],
        trust_impact=impact,
    )


class LLMParserError(Exception):
    """Raised when LLM parsing fails."""


class LLMParser:
    """OpenRouter-backed LLM parser with SHA-256 Redis caching and deterministic fallbacks."""

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client
        self._client = self._build_client()

    @staticmethod
    def _build_client():
        if not settings.openrouter_api_key:
            logger.warning("OPENROUTER_API_KEY not set — LLM parsing disabled")
            return None
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
            timeout=6.0,  # 6-second hard limit on any OpenRouter HTTP call
        )

    # ── Cache ────────────────────────────────

    @staticmethod
    def _hash(text: str, prefix: str) -> str:
        return f"{prefix}:{hashlib.sha256(text.encode()).hexdigest()}"

    async def _get_cached(self, key: str, schema: type[T]) -> T | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            return schema.model_validate_json(raw)
        except Exception:
            return None

    async def _set_cache(self, key: str, obj: BaseModel) -> None:
        await self._redis.set(key, obj.model_dump_json(), ex=CACHE_TTL_SECONDS)

    # ── Core LLM call ───────────────────────

    async def _call(self, prompt: str, schema: type[T], timeout: float = 6.0) -> T:
        if self._client is None:
            raise LLMParserError(
                "OPENROUTER_API_KEY not configured. Set it in .env"
            )
        try:
            resp = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=settings.openrouter_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Respond ONLY with valid JSON matching the requested schema. No markdown, no explanation.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise LLMParserError(f"OpenRouter LLM call timed out after {timeout}s") from exc
        except Exception as exc:
            raise LLMParserError(f"OpenRouter LLM call failed: {exc}") from exc

        raw = resp.choices[0].message.content or ""
        raw = _strip_code_fences(raw)
        try:
            return schema.model_validate_json(raw)
        except Exception as exc:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    return schema.model_validate_json(m.group(0))
                except Exception:
                    pass
            raise LLMParserError(f"Unparseable LLM response: {raw[:300]}") from exc

    # ── 1. Bank Offers ───────────────────────

    async def parse_bank_offers(self, raw_texts: list[str]) -> list[BankOfferRule]:
        if not raw_texts:
            return []

        # Deduplicate and bound raw texts to at most 12 clean items (max 280 chars each)
        cleaned_texts = []
        seen = set()
        for t in raw_texts:
            clean = t.strip()[:280]
            if clean and clean not in seen:
                seen.add(clean)
                cleaned_texts.append(clean)
            if len(cleaned_texts) >= 12:
                break

        combined = "\n---\n".join(cleaned_texts)
        key = self._hash(combined, "offer_rule")
        cached = await self._get_cached(key, BankOfferRuleList)
        if cached:
            return cached.offers

        fallback = _deterministic_bank_offers(cleaned_texts)
        if not settings.openrouter_api_key or self._client is None:
            return fallback

        prompt = (
            "Parse each Indian e-commerce bank/card offer into JSON.\n"
            "Return: {\"offers\": [{\"bank_name\",\"card_type\"(credit/debit/both/emi),\"card_network\"(visa/mastercard/amex/rupay/any),"
            "\"min_txn_amount\",\"discount_flat\",\"discount_pct\",\"max_discount_cap\","
            "\"excluded_cards\":[],\"valid_until\",\"summary\"}]}\n\n"
            "Raw texts:\n" + combined
        )
        try:
            result = await self._call(prompt, BankOfferRuleList, timeout=12.0)
            await self._set_cache(key, result)
            return result.offers
        except Exception as exc:
            logger.warning(f"LLM bank offer parse failed ({exc}) — using deterministic fallback")
            return fallback

    # ── 2. Variant Attributes ────────────────

    async def parse_variant_attributes(self, title: str, specs_text: str = "") -> VariantAttributes:
        input_text = f"Title: {title[:160]}\nSpecs: {specs_text[:300]}"
        key = self._hash(input_text, "variant_attr")
        cached = await self._get_cached(key, VariantAttributes)
        if cached:
            return cached

        fallback = _deterministic_variant(title, specs_text)
        if not settings.openrouter_api_key or self._client is None:
            return fallback

        prompt = (
            "Extract product variant attributes from this Indian electronics listing.\n"
            "Return JSON: {\"brand\",\"model_family\",\"model_name\",\"ram_gb\"(null ok),"
            "\"storage_gb\"(null ok),\"color\"(null ok),\"screen_size_inches\"(null ok),"
            "\"resolution\"(null ok),\"processor\"(null ok),\"condition\"(new/refurbished/open_box/used)}\n\n"
            + input_text
        )
        try:
            result = await self._call(prompt, VariantAttributes, timeout=12.0)
            await self._set_cache(key, result)
            return result
        except Exception as exc:
            logger.warning(f"LLM variant parse failed ({exc}) — using deterministic fallback")
            return fallback

    # ── 3. Review Scam Analysis ──────────────

    async def analyze_reviews(self, reviews: list[str]) -> ReviewScamSignals:
        if not reviews:
            return ReviewScamSignals()

        # Bound review snippets to top 3 reviews, truncated to 150 chars each
        bounded_reviews = [r.strip()[:150] for r in reviews[:3] if r.strip()]
        combined = "\n---\n".join(bounded_reviews)
        key = self._hash(combined, "review_scam")
        cached = await self._get_cached(key, ReviewScamSignals)
        if cached:
            return cached

        fallback = _deterministic_reviews(bounded_reviews)
        if not settings.openrouter_api_key or self._client is None:
            return fallback

        prompt = (
            "Analyze these Indian e-commerce product reviews for scam/trust signals.\n"
            "Return JSON: {\"fake_product_mentions\":int,\"open_box_mentions\":int,"
            "\"seal_broken_mentions\":int,\"refurbished_mentions\":int,"
            "\"wrong_product_mentions\":int,\"suspicious_review_pct\":float(0-100),"
            "\"top_red_flags\":[str],\"trust_impact\":int(-100 to 0)}\n\n"
            "Reviews:\n" + combined
        )
        try:
            result = await self._call(prompt, ReviewScamSignals, timeout=12.0)
            await self._set_cache(key, result)
            return result
        except Exception as exc:
            logger.warning(f"LLM review scam analysis failed ({exc}) — using deterministic fallback")
            return fallback

    # ── 4. PDF Invoice ───────────────────────

    async def parse_invoice(self, invoice_text: str) -> InvoiceExtracted:
        key = self._hash(invoice_text, "invoice")
        cached = await self._get_cached(key, InvoiceExtracted)
        if cached:
            return cached

        prompt = (
            "Extract fields from this Indian e-commerce tax invoice.\n"
            "Return JSON: {\"product_name\",\"purchase_date\"(YYYY-MM-DD),"
            "\"price_paid\":float,\"seller_name\",\"invoice_number\","
            "\"retailer\"(amazon_in/flipkart/croma/null),\"warranty_months\":int}\n\n"
            "Invoice:\n" + invoice_text[:5000]
        )
        result = await self._call(prompt, InvoiceExtracted)
        await self._set_cache(key, result)
        return result

    # ── 5. Executive Recommendation Summary ─

    async def generate_executive_summary(
        self,
        product_title: str,
        best_store: str,
        best_seller: str,
        effective_price: float,
        best_offer_stack: str,
        peace_of_mind_score: int,
        key_pros: list[str],
        key_cons: list[str],
    ) -> ExecutiveSummary:
        cache_key_content = f"{product_title}:{best_store}:{best_seller}:{effective_price}:{best_offer_stack}"
        key = self._hash(cache_key_content, "exec_summary")
        cached = await self._get_cached(key, ExecutiveSummary)
        if cached:
            return cached

        # Deterministic fallback template
        bullets = [
            f"Best Store & Seller: Buy from {best_store} (Seller: {best_seller}) for verified genuine dispatch.",
            f"Maximum Savings Stack: Apply {best_offer_stack} to achieve final net price of ₹{effective_price:,.0f}.",
            f"Decision Verdict ({peace_of_mind_score}/100 Trust): Balances competitive pricing with manufacturer warranty coverage.",
        ]
        if key_cons:
            bullets.append(f"Caveat to watch: {key_cons[0]}")

        fallback = ExecutiveSummary(
            best_store=best_store,
            best_seller=best_seller,
            best_offer_stack=best_offer_stack,
            final_net_price=effective_price,
            why_this_wins=f"Lowest effective net checkout (₹{effective_price:,.0f}) paired with {peace_of_mind_score}/100 seller peace-of-mind score.",
            key_caveat=key_cons[0] if key_cons else "Verify serial number on brand warranty portal upon delivery.",
            summary_bullets=bullets,
        )

        if not settings.openrouter_api_key or self._client is None:
            return fallback

        prompt = (
            "You are India's leading e-commerce consumer trust & price intelligence advisor.\n"
            "Synthesize an authoritative 3-bullet Executive Recommendation for an Indian shopper.\n"
            f"Product: {product_title}\n"
            f"Top Store & Seller: {best_store} / {best_seller} (Peace of Mind: {peace_of_mind_score}/100)\n"
            f"Net Checkout Price: ₹{effective_price:,.0f}\n"
            f"Best Offer Stack: {best_offer_stack}\n"
            f"Key Pros: {', '.join(key_pros[:3])}\n"
            f"Key Cons/Caveats: {', '.join(key_cons[:2]) if key_cons else 'None'}\n\n"
            "Return JSON matching this schema:\n"
            "{\"best_store\":str,\"best_seller\":str,\"best_offer_stack\":str,\"final_net_price\":float,"
            "\"why_this_wins\":str,\"key_caveat\":str,\"summary_bullets\":[str]}"
        )

        try:
            result = await self._call(prompt, ExecutiveSummary)
            await self._set_cache(key, result)
            return result
        except Exception as exc:
            logger.warning(f"LLM executive summary generation failed ({exc}) — using deterministic fallback")
            return fallback


# ── Singleton factory ────────────────────────

_instance: LLMParser | None = None


async def get_llm_parser() -> LLMParser:
    global _instance
    if _instance is None:
        rc = aioredis.from_url(settings.redis_url, decode_responses=True)
        _instance = LLMParser(rc)
    return _instance
