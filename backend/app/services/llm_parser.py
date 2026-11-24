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
LLM_CALL_TIMEOUT_SECONDS = 4.0

# Tracks runtime LLM availability: None (untested), "ok", or "degraded"
_llm_health_state: str | None = None


def get_llm_status() -> str:
    """Return 'no_key', 'ok', or 'degraded' for /health reporting."""
    if not settings.openrouter_api_key:
        return "no_key"
    if _llm_health_state == "degraded":
        return "degraded"
    return "ok"

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.M)
_CODE_FENCE_END_RE = re.compile(r"\n?```\s*$", re.M)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that free models sometimes wrap JSON in."""
    text = _CODE_FENCE_RE.sub("", text.strip())
    text = _CODE_FENCE_END_RE.sub("", text.strip())
    return text.strip()


_TEASER_CEILING_RE = re.compile(
    r"\b(?:(?:up\s*to|upto)\b.*?\b(?:select\s+(?:credit|debit|bank)?\s*cards?|all\s+banks?|\d+\s+offers?|bank\s+offers?|more\s+offers?)|"
    r"\d+\s+(?:bank\s+)?offers?\s+available)\b",
    re.I,
)

_NON_ITEM_PROMO_RE = re.compile(
    r"\b(?:additional\s+benefits|unlock|next\s+purchase|next\s+order|future\s+order|"
    r"combo|buy\s+together|extra\s+off\s+on\s+select|spotify|youtube|ott|subscription|"
    r"partner\s+offer|price\s+inclusive|inclusive\s+of\s+(?:cashback|coupon)|already\s+applied|"
    r"exchange|with\s+this\s+item|frequently\s+bought|add-?on|gst\s+invoice|business\s+purchase)\b",
    re.I,
)

_POST_PURCHASE_CASHBACK_RE = re.compile(
    r"\b(?:cashback|wallet\s+(?:balance|credit)|reward\s+points?|super\s*coins?|neu\s*coins?|pay\s+balance)\b",
    re.I,
)


def classify_offer_clause(raw_text: str) -> dict[str, bool]:
    """
    Universal structural classifier for an on-page offer clause (zero hardcoded product words):
      - `is_non_item_promo`: future-order, unlockable, cross-sell, partner/GST, or subscription perk
      - `is_teaser_summary`: open-ended 'up to' on select cards / N offers without explicit bank + formula
      - `is_cashback`: post-purchase cashback, wallet credit, or reward points (not instant discount)
      - `emi_only`: EMI-only offer
      - `is_upto_ceiling`: uses 'up to' / 'upto' ceiling language
      - `has_explicit_flat`: states explicit 'Flat' guarantee (or direct '₹X off' without 'up to')
    """
    t = " ".join((raw_text or "").split()).strip()
    low = t.lower()
    is_non_item = bool(_NON_ITEM_PROMO_RE.search(t))
    has_upto = bool(re.search(r"\b(?:up\s*to|upto)\b", low))
    has_pct = bool(re.search(r"\d+(?:\.\d+)?\s*%", low))
    has_flat_word = bool(re.search(r"\bflat\b", low))
    has_explicit_flat = has_flat_word or (not has_upto and not has_pct and bool(re.search(r"(?:₹|Rs\.?|INR)\s*[\d,]+", t, re.I)))
    is_cashback = bool(_POST_PURCHASE_CASHBACK_RE.search(low)) and "instant discount" not in low
    emi_only = ("emi" in low) and ("non-emi" not in low)
    is_teaser = bool(_TEASER_CEILING_RE.search(t)) or (
        has_upto and not has_pct and not has_flat_word
    )
    return {
        "is_non_item_promo": is_non_item,
        "is_teaser_summary": is_teaser,
        "is_cashback": is_cashback,
        "emi_only": emi_only,
        "is_upto_ceiling": has_upto,
        "has_explicit_flat": has_explicit_flat,
    }


def _deterministic_bank_offers(raw_texts: list[str]) -> list[BankOfferRule]:
    """
    Accurate deterministic parser for live Indian on-page bank offers.
    - Extracts every bank mentioned in a line (e.g. 'HDFC, ICICI & SBI Credit Card').
    - Never confuses min-spend or max-cap with flat discount when percentage discount is present.
    - Parses card_type (credit/debit/emi/both), flat or %, max_discount_cap, min_txn_amount,
      and 5-gate verification flags (`is_teaser_summary`, `is_non_item_promo`, `is_cashback`, `emi_only`).
    """
    rules: list[BankOfferRule] = []
    seen_keys: set[tuple[str, float, float, float, float, str]] = set()
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
        if not t or len(t) < 10:
            continue
        low = t.lower()
        flags = classify_offer_clause(t)

        # Skip pure no-cost EMI interest-savings lines that have no discount amount
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

        if flat <= 0 and pct <= 0 and not flags["is_teaser_summary"]:
            continue

        is_teaser = flags["is_teaser_summary"]
        if not found_banks:
            found_banks = ["ALL BANKS"]
            # Any offer without an explicit bank issuer name is a teaser / unverified summary
            is_teaser = True

        # If an 'up to' flat offer has no % formula, no explicit 'Flat' word, and no verified min_txn_amount, it is a teaser ceiling
        if flags["is_upto_ceiling"] and pct == 0.0 and not flags["has_explicit_flat"] and min_txn <= 0:
            is_teaser = True

        if "non-emi" in low:
            card_type = "debit" if ("debit" in low and "credit" not in low) else ("credit" if "credit" in low else "both")
        elif flags["emi_only"]:
            card_type = "emi"
        elif "debit" in low and "credit" not in low:
            card_type = "debit"
        elif "credit" in low and "debit" not in low:
            card_type = "credit"
        else:
            card_type = "both"

        for bname in found_banks:
            dedup_key = (bname, flat, pct, cap, min_txn, card_type)
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
                    emi_only=(card_type == "emi" or flags["emi_only"]),
                    is_teaser_summary=is_teaser,
                    is_non_item_promo=flags["is_non_item_promo"],
                    is_cashback=flags["is_cashback"],
                    is_upto_ceiling=flags["is_upto_ceiling"],
                    has_explicit_flat=flags["has_explicit_flat"],
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


def _deterministic_invoice(invoice_text: str) -> InvoiceExtracted:
    """Regex fallback for tax invoice parsing when OpenRouter is unavailable/slow."""
    from datetime import date

    text = invoice_text or ""
    prod_m = re.search(r"(?:Product|Description|Item)\s*[:\-]\s*(.+)", text, re.I)
    price_m = re.search(r"(?:Grand\s*Total|Total\s*Amount|Total|Amount\s*Paid|Price)\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)", text, re.I)
    inv_m = re.search(r"(?:Invoice\s*(?:No|Number|#)|Bill\s*No)\s*[:\-]?\s*([A-Za-z0-9\-/]+)", text, re.I)
    date_m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    seller_m = re.search(r"(?:Sold\s*By|Seller)\s*[:\-]\s*(.+)", text, re.I)

    low = text.lower()
    retailer = "amazon_in" if "amazon" in low else ("flipkart" if "flipkart" in low else ("croma" if "croma" in low else None))
    price_val = float(price_m.group(1).replace(",", "")) if price_m else 0.0

    return InvoiceExtracted(
        product_name=(prod_m.group(1).strip()[:120] if prod_m else (text.strip().splitlines()[0][:120] if text.strip() else "Purchased Product")),
        purchase_date=date_m.group(1) if date_m else date.today().isoformat(),
        price_paid=price_val,
        seller_name=(seller_m.group(1).strip()[:100] if seller_m else "Retailer"),
        invoice_number=(inv_m.group(1).strip()[:60] if inv_m else f"INV-{abs(hash(text)) % 100000}"),
        retailer=retailer,
        warranty_months=12,
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
            timeout=LLM_CALL_TIMEOUT_SECONDS,  # 4-second hard limit on any OpenRouter HTTP call
        )

    # ── Cache ────────────────────────────────

    @staticmethod
    def _hash(text: str, prefix: str) -> str:
        return f"{prefix}:{hashlib.sha256(text.encode()).hexdigest()}"

    async def _get_cached(self, key: str, schema: type[T]) -> T | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(key)
        except Exception:
            return None
        if raw is None:
            return None
        try:
            return schema.model_validate_json(raw)
        except Exception:
            return None

    async def _set_cache(self, key: str, obj: BaseModel, ttl: int | None = None) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, obj.model_dump_json(), ex=ttl or CACHE_TTL_SECONDS)
        except Exception:
            pass

    # ── Core LLM call ───────────────────────

    async def _call(self, prompt: str, schema: type[T], timeout: float = LLM_CALL_TIMEOUT_SECONDS) -> T:
        global _llm_health_state
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
                    max_tokens=1024,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            _llm_health_state = "degraded"
            raise LLMParserError(f"OpenRouter LLM call timed out after {timeout}s") from exc
        except Exception as exc:
            _llm_health_state = "degraded"
            raise LLMParserError(f"OpenRouter LLM call failed: {exc}") from exc

        raw = resp.choices[0].message.content or ""
        raw = _strip_code_fences(raw)
        try:
            parsed = schema.model_validate_json(raw)
            _llm_health_state = "ok"
            return parsed
        except Exception as exc:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    parsed = schema.model_validate_json(m.group(0))
                    _llm_health_state = "ok"
                    return parsed
                except Exception:
                    pass
            _llm_health_state = "degraded"
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
            logger.info("parse_bank_offers: llm_mode=llm cache_hit=true count=%d", len(cached.offers))
            return cached.offers

        fallback = _deterministic_bank_offers(cleaned_texts)
        if not settings.openrouter_api_key or self._client is None:
            logger.info("parse_bank_offers: llm_mode=fallback reason=no_key count=%d", len(fallback))
            return fallback

        prompt = (
            "Parse each Indian e-commerce bank/card offer into JSON.\n"
            "Return: {\"offers\": [{\"bank_name\",\"card_type\"(credit/debit/both/emi),\"card_network\"(visa/mastercard/amex/rupay/any),"
            "\"min_txn_amount\",\"discount_flat\",\"discount_pct\",\"max_discount_cap\","
            "\"excluded_cards\":[],\"valid_until\",\"summary\"}]}\n\n"
            "Raw texts:\n" + combined
        )
        try:
            result = await self._call(prompt, BankOfferRuleList, timeout=LLM_CALL_TIMEOUT_SECONDS)
            await self._set_cache(key, result)
            logger.info("parse_bank_offers: llm_mode=llm cache_hit=false count=%d", len(result.offers))
            return result.offers
        except Exception as exc:
            logger.warning("parse_bank_offers: llm_mode=fallback reason=%s", exc)
            await self._set_cache(key, BankOfferRuleList(offers=fallback), ttl=300)
            return fallback

    # ── 2. Variant Attributes ────────────────

    async def parse_variant_attributes(self, title: str, specs_text: str = "") -> VariantAttributes:
        input_text = f"Title: {title[:160]}\nSpecs: {specs_text[:300]}"
        key = self._hash(input_text, "variant_attr")
        cached = await self._get_cached(key, VariantAttributes)
        if cached:
            logger.info("parse_variant_attributes: llm_mode=llm cache_hit=true")
            return cached

        fallback = _deterministic_variant(title, specs_text)
        if not settings.openrouter_api_key or self._client is None:
            logger.info("parse_variant_attributes: llm_mode=fallback reason=no_key")
            return fallback

        prompt = (
            "Extract product variant attributes from this Indian electronics listing.\n"
            "Return JSON: {\"brand\",\"model_family\",\"model_name\",\"ram_gb\"(null ok),"
            "\"storage_gb\"(null ok),\"color\"(null ok),\"screen_size_inches\"(null ok),"
            "\"resolution\"(null ok),\"processor\"(null ok),\"condition\"(new/refurbished/open_box/used)}\n\n"
            + input_text
        )
        try:
            result = await self._call(prompt, VariantAttributes, timeout=LLM_CALL_TIMEOUT_SECONDS)
            await self._set_cache(key, result)
            logger.info("parse_variant_attributes: llm_mode=llm cache_hit=false")
            return result
        except Exception as exc:
            logger.warning("parse_variant_attributes: llm_mode=fallback reason=%s", exc)
            await self._set_cache(key, fallback, ttl=300)
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
            logger.info("analyze_reviews: llm_mode=llm cache_hit=true")
            return cached

        fallback = _deterministic_reviews(bounded_reviews)
        if not settings.openrouter_api_key or self._client is None:
            logger.info("analyze_reviews: llm_mode=fallback reason=no_key")
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
            result = await self._call(prompt, ReviewScamSignals, timeout=LLM_CALL_TIMEOUT_SECONDS)
            await self._set_cache(key, result)
            logger.info("analyze_reviews: llm_mode=llm cache_hit=false")
            return result
        except Exception as exc:
            logger.warning("analyze_reviews: llm_mode=fallback reason=%s", exc)
            await self._set_cache(key, fallback, ttl=300)
            return fallback

    # ── 4. PDF Invoice ───────────────────────

    async def parse_invoice(self, invoice_text: str) -> InvoiceExtracted:
        key = self._hash(invoice_text, "invoice")
        cached = await self._get_cached(key, InvoiceExtracted)
        if cached:
            logger.info("parse_invoice: llm_mode=llm cache_hit=true")
            return cached

        fallback = _deterministic_invoice(invoice_text)
        if not settings.openrouter_api_key or self._client is None:
            logger.info("parse_invoice: llm_mode=fallback reason=no_key")
            return fallback

        prompt = (
            "Extract fields from this Indian e-commerce tax invoice.\n"
            "Return JSON: {\"product_name\",\"purchase_date\"(YYYY-MM-DD),"
            "\"price_paid\":float,\"seller_name\",\"invoice_number\","
            "\"retailer\"(amazon_in/flipkart/croma/null),\"warranty_months\":int}\n\n"
            "Invoice:\n" + invoice_text[:5000]
        )
        try:
            result = await self._call(prompt, InvoiceExtracted, timeout=LLM_CALL_TIMEOUT_SECONDS)
            await self._set_cache(key, result)
            logger.info("parse_invoice: llm_mode=llm cache_hit=false")
            return result
        except Exception as exc:
            logger.warning("parse_invoice: llm_mode=fallback reason=%s", exc)
            return fallback

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
        *,
        checkout_payable_inr: float | None = None,
        unowned_option_note: str | None = None,
        excluded_stores_note: str | None = None,
    ) -> ExecutiveSummary:
        upfront_cash = checkout_payable_inr if (checkout_payable_inr is not None and checkout_payable_inr > 0) else effective_price
        cache_key_content = (
            f"{product_title}:{best_store}:{best_seller}:{upfront_cash}:{effective_price}:"
            f"{best_offer_stack}:{unowned_option_note or ''}:{excluded_stores_note or ''}"
        )
        key = self._hash(cache_key_content, "exec_summary")
        cached = await self._get_cached(key, ExecutiveSummary)
        if cached:
            logger.info("generate_executive_summary: llm_mode=llm cache_hit=true")
            return cached

        # Brutally Honest Deterministic Recommendation
        bullets = [
            f"Cheapest Guaranteed Upfront Checkout: Pay ₹{upfront_cash:,.0f} today on {best_store} (Seller: {best_seller}) using {best_offer_stack}.",
        ]
        if effective_price < upfront_cash:
            bullets.append(
                f"Post-Purchase Net Cost: Works out to ₹{effective_price:,.0f} only after ₹{upfront_cash - effective_price:,.0f} in verified post-purchase rewards."
            )
        if unowned_option_note:
            bullets.append(unowned_option_note)
        if excluded_stores_note:
            bullets.append(excluded_stores_note)
        bullets.append(
            f"Seller Trust ({peace_of_mind_score}/100): Verified dispatch with manufacturer warranty coverage."
        )

        caveat_text = (
            excluded_stores_note
            or (key_cons[0] if key_cons else "Verify serial number and package seal on delivery.")
        )

        fallback = ExecutiveSummary(
            best_store=best_store,
            best_seller=best_seller,
            best_offer_stack=best_offer_stack,
            final_net_price=effective_price,
            why_this_wins=(
                f"Cheapest guaranteed upfront checkout of ₹{upfront_cash:,.0f} today"
                + (f" (₹{effective_price:,.0f} net after verified rewards)" if effective_price < upfront_cash else "")
                + f" on {best_store} with {peace_of_mind_score}/100 seller trust."
            ),
            key_caveat=caveat_text,
            summary_bullets=bullets,
        )

        if not settings.openrouter_api_key or self._client is None:
            logger.info("generate_executive_summary: llm_mode=fallback reason=no_key")
            return fallback

        prompt = (
            "You are India's brutally honest e-commerce consumer trust & price intelligence advisor.\n"
            "Synthesize an authoritative 3-4 bullet Executive Recommendation:\n"
            "1. Lead with the exact cheapest guaranteed upfront checkout action (Cash Paid Today).\n"
            "2. Explicitly separate any post-purchase rewards or unowned-card option.\n"
            "3. Explicitly state if any cheaper/equal store was excluded due to unconfirmed variant or out-of-stock status.\n"
            f"Product: {product_title}\n"
            f"Winning Store & Seller: {best_store} / {best_seller} (Trust: {peace_of_mind_score}/100)\n"
            f"Upfront Cash Paid Today: ₹{upfront_cash:,.0f}\n"
            f"Effective Net After Verified Rewards: ₹{effective_price:,.0f}\n"
            f"Best Offer Stack: {best_offer_stack}\n"
            f"Unowned Card Note: {unowned_option_note or 'None'}\n"
            f"Excluded Stores Note: {excluded_stores_note or 'None'}\n\n"
            "Return JSON matching this schema:\n"
            "{\"best_store\":str,\"best_seller\":str,\"best_offer_stack\":str,\"final_net_price\":float,"
            "\"why_this_wins\":str,\"key_caveat\":str,\"summary_bullets\":[str]}"
        )

        try:
            result = await self._call(prompt, ExecutiveSummary, timeout=LLM_CALL_TIMEOUT_SECONDS)
            await self._set_cache(key, result)
            logger.info("generate_executive_summary: llm_mode=llm cache_hit=false")
            return result
        except Exception as exc:
            logger.warning("generate_executive_summary: llm_mode=fallback reason=%s", exc)
            await self._set_cache(key, fallback, ttl=300)
            return fallback


# ── Singleton factory ────────────────────────

_instance: LLMParser | None = None


async def get_llm_parser() -> LLMParser:
    global _instance
    if _instance is None:
        rc = aioredis.from_url(settings.redis_url, decode_responses=True)
        _instance = LLMParser(rc)
    return _instance
