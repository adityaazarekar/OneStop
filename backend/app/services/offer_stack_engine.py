"""
Dynamic Offer Stacking & Strict Cash-vs-Points Financial Transparency Engine.

Core invariants:
1. NEVER disguise post-purchase reward points as instant checkout cash.
   `checkout_payable_inr = listed_price - verified_instant_bank_discount - verified_coupon + mandatory_fees`.
   If the page has no verified live instant bank discount, `instant_bank_discount_inr = 0.0`.
2. Explicitly compute `post_purchase_rewards`: points earned, exact cash & travel
   conversion ratios (e.g. `3.33 pts = ₹1 statement cash (₹0.30/pt)` vs `1 pt = ₹1.00 travel`),
   enforced `monthly_cap_inr`, and any `processing_fee_inr` (e.g. GyFTR convenience fee).
3. Rank Direct Points Card, Direct Cashback Card, Gift Voucher / SmartBuy / GyFTR Stack,
   and UPI App Offer side-by-side with a plain-English efficiency verdict citing real caps & exclusions.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime

from app.schemas.extraction import (
    AlternativeOptimizationPath,
    BankOfferRule,
    CashVsPointsReport,
    LiveOfferRaw,
    PaymentRouteAnalysis,
    PostPurchaseRewards,
    StackedEffectivePriceResult,
    StackStep,
    VerifiedPaymentRule,
)
from app.services.card_optimizer import CardLike

logger = logging.getLogger(__name__)

CROSS_SELL_COUPON_BLOCK_RE = re.compile(
    r"\b(?:additional\s+benefits|unlock|air\s*fryer|next\s+purchase|next\s+order|future\s+order|"
    r"combo|buy\s+together|extra\s+off\s+on\s+select|spotify|youtube|ott|subscription|"
    r"partner\s+offer|price\s+inclusive|inclusive\s+of\s+(?:cashback|coupon)|already\s+applied|"
    r"exchange|with\s+this\s+item|frequently\s+bought|add-?on)\b",
    re.I,
)

EXPLICIT_CURRENT_SKU_COUPON_RE = re.compile(
    r"\b(?:apply\s+(?:₹|Rs\.?|INR)?\s*[\d,]+\s*coupon|coupon\s+checkbox|save\s+(?:₹|Rs\.?|INR)\s*[\d,]+\s+with\s+coupon)\b",
    re.I,
)


def is_cross_sell_or_invalid_coupon(coupon_text: str) -> bool:
    """Reject cross-sell, future-order, partner-benefit, or already-inclusive promos."""
    if not coupon_text or not coupon_text.strip():
        return True
    return bool(CROSS_SELL_COUPON_BLOCK_RE.search(coupon_text))


def _parse_coupon_value(coupon_text: str, base_price: float) -> tuple[float, str]:
    """Parse flat or percentage discount from a genuine current-item coupon."""
    if not coupon_text or is_cross_sell_or_invalid_coupon(coupon_text):
        return 0.0, ""

    pct_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*off", coupon_text, re.I)
    if pct_m:
        pct = float(pct_m.group(1))
        disc = round(base_price * pct / 100.0, 2)
        cap_m = re.search(r"(?:upto|up to|max(?:imum)?)\s*(?:₹|Rs\.?|INR)?\s*([\d,]+)", coupon_text, re.I)
        if cap_m:
            cap = float(cap_m.group(1).replace(",", ""))
            disc = min(disc, cap)
        return min(disc, base_price), f"Coupon: {pct:g}% off applied"

    flat_m = re.search(r"(?:₹|Rs\.?|INR)\s*([\d,]+)", coupon_text, re.I)
    if flat_m:
        val = float(flat_m.group(1).replace(",", ""))
        return min(val, base_price), f"Coupon: ₹{val:,.0f} off applied"

    return 0.0, ""


def _resolve_verified_coupon_discount(
    raw_coupons: list[str],
    listed_price: float,
    retailer_advertised_lowest_price: float | None = None,
) -> tuple[float, str]:
    """
    Pick the highest genuine current-item coupon discount while enforcing:
      1. Strict rejection of cross-sell / future-order / partner-benefit coupons.
      2. Advertised Lowest-Price Sanity Check: if a coupon drops price materially below
         `retailer_advertised_lowest_price` without an explicit current-SKU checkbox coupon,
         reject it and log a warning.
    """
    best_disc = 0.0
    best_desc = ""
    best_raw = ""
    for c in raw_coupons or []:
        if is_cross_sell_or_invalid_coupon(c):
            logger.warning("Rejected non-item / cross-sell coupon: %s", c[:120])
            continue
        disc, desc = _parse_coupon_value(c, listed_price)
        if disc > best_disc:
            best_disc = disc
            best_desc = desc
            best_raw = c

    if (
        best_disc > 0
        and retailer_advertised_lowest_price
        and retailer_advertised_lowest_price > 0
        and (listed_price - best_disc) < (retailer_advertised_lowest_price - 250.0)
        and not EXPLICIT_CURRENT_SKU_COUPON_RE.search(best_raw)
    ):
        logger.warning(
            "Rejected coupon '%s' (-₹%.0f): would drop price ₹%.0f below retailer_advertised_lowest_price ₹%.0f without explicit SKU checkbox",
            best_raw[:100],
            best_disc,
            listed_price - best_disc,
            retailer_advertised_lowest_price,
        )
        return 0.0, ""

    return round(best_disc, 2), best_desc


def _parse_live_cashback_or_upi(offer_text: str, current_price: float) -> tuple[float, str]:
    """Detect live UPI or wallet instant discount / cashback mentioned on the product page."""
    if not offer_text or is_cross_sell_or_invalid_coupon(offer_text):
        return 0.0, ""

    lower = offer_text.lower()
    # Reject open-ended 'up to' / 'upto' teaser ceilings unless an explicit 'flat' or '%' formula is given
    if re.search(r"\b(?:up\s*to|upto)\b", lower) and "flat" not in lower and "%" not in lower:
        return 0.0, ""

    if any(
        k in lower
        for k in (
            "upi",
            "wallet",
            "google pay",
            "gpay",
            "phonepe",
            "paytm",
            "cred",
            "super.money",
            "bhim",
            "amazon pay balance",
        )
    ):
        no_min = re.sub(
            r"(?:min(?:imum)?\s*(?:purchase|spend|order|txn|transaction)[^₹\d]{0,20}|orders?\s+above\s*)(?:₹|Rs\.?|INR)?\s*[\d,]+",
            "",
            offer_text,
            flags=re.I,
        )
        m = re.search(
            r"(?:cashback|flat\s+off|instant\s+(?:discount|saving)|get|save|up\s*to|upto).*?(?:₹|Rs\.?|INR)\s*([\d,]+)",
            no_min,
            re.I,
        )
        if not m:
            m = re.search(
                r"(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:off|instant\s+discount|discount|cashback|rewards)",
                no_min,
                re.I,
            )
        if m:
            val = float(m.group(1).replace(",", ""))
            if 0 < val < current_price:
                label = "UPI Instant Discount" if ("off" in lower or "instant discount" in lower) and "cashback" not in lower else "UPI/Wallet Cashback"
                return val, f"{label}: ₹{val:,.0f} ({offer_text[:90]})"

    return 0.0, ""


def _format_conversion_ratio(point_value_inr: float, label: str) -> str:
    if point_value_inr <= 0:
        return f"Not redeemable for {label}"
    if abs(point_value_inr - 1.0) < 1e-6:
        return f"1 pt = ₹1.00 {label}"
    if point_value_inr < 1.0:
        pts_per_rupee = 1.0 / point_value_inr
        return f"{pts_per_rupee:.2f} pts = ₹1 {label} (₹{point_value_inr:.2f}/pt)"
    return f"1 pt = ₹{point_value_inr:.2f} {label}"


def _normalize_bank_token(name: str) -> str:
    low = (name or "").lower().strip()
    aliases = {
        "state bank of india": "sbi",
        "sbi card": "sbi",
        "sbi bank": "sbi",
        "hdfc bank": "hdfc",
        "icici bank": "icici",
        "axis bank": "axis",
        "kotak mahindra": "kotak",
        "kotak bank": "kotak",
        "american express": "amex",
        "idfc first": "idfc",
        "bank of baroda": "bob",
        "one card": "onecard",
        "yes bank": "yes",
        "standard chartered": "sc",
        "indusind bank": "indusind",
    }
    for k, v in aliases.items():
        if k in low:
            return v
    return low.split()[0] if low else ""


def _card_name_matches_text(card_name: str, text: str) -> bool:
    """Match card names regardless of word order (e.g. 'ICICI Amazon Pay' vs 'Amazon Pay ICICI')."""
    if not card_name or not text:
        return False
    t_low = text.lower()
    c_low = card_name.lower().strip()
    if c_low in t_low:
        return True
    tokens = [w for w in re.split(r"[^a-z0-9]+", c_low) if len(w) >= 3 and w not in {"bank", "card", "credit", "debit"}]
    return len(tokens) >= 2 and all(tok in t_low for tok in tokens)


def evaluate_offer_5_gates(
    offer: BankOfferRule,
    card: CardLike | None,
    base_price: float,
    *,
    retailer_advertised_lowest_price: float | None = None,
) -> tuple[bool, float, str, str]:
    """
    Strict 5-Gate Deterministic Math Guard (`offer_stack_engine.py`):
    No offer may reduce `checkout_payable_inr` unless it passes ALL 5 deterministic gates:
      Gate 1 — Guaranteed Instant Discount Only (rejects teasers, non-item promos, post-purchase cashback/points)
      Gate 2 — Exact User-Wallet Match (issuer, payment channel credit vs debit, and card exclusions)
      Gate 3 — Payment Mode Match (rejects `emi_only` from full-swipe checkout cash)
      Gate 4 — Minimum Order Value (`min_spend`) Verification & conditional 'up to' guard
      Gate 5 — Exact Formula (`min(round(listed_price * Y / 100), Z)`) & advertised lowest-price sanity guard
    Returns `(passed, guaranteed_discount_inr, gate_code, detail_reason)`.
    """
    from app.services.llm_parser import classify_offer_clause

    summary_text = offer.summary or ""
    flags = classify_offer_clause(summary_text) if summary_text else {}

    is_non_item = bool(getattr(offer, "is_non_item_promo", False) or flags.get("is_non_item_promo", False))
    is_teaser = bool(getattr(offer, "is_teaser_summary", False) or flags.get("is_teaser_summary", False))
    is_cashback = bool(getattr(offer, "is_cashback", False) or flags.get("is_cashback", False))
    is_emi = bool(getattr(offer, "emi_only", False) or offer.card_type == "emi" or flags.get("emi_only", False))
    is_upto = bool(getattr(offer, "is_upto_ceiling", False) or flags.get("is_upto_ceiling", False))
    has_flat = bool(getattr(offer, "has_explicit_flat", False) or flags.get("has_explicit_flat", False))

    # ── Gate 1: Guaranteed Instant Discount Only ──
    if is_non_item or (summary_text and is_cross_sell_or_invalid_coupon(summary_text)):
        return False, 0.0, "gate1_non_item_promo", "Rejected non-item / cross-sell / future-order promo"
    if is_teaser:
        return False, 0.0, "gate1_teaser_summary", "Rejected open-ended teaser summary (requires offer verification)"
    if is_cashback:
        return False, 0.0, "gate1_post_purchase_cashback", "Excluded post-purchase cashback/points from upfront checkout cash"

    # ── Gate 3: Payment Mode Match (checked early so EMI offers are cleanly classified) ──
    if is_emi:
        return (
            False,
            0.0,
            "gate3_emi_only",
            "EMI-only offer excluded from full-swipe checkout cash (processing fee + 18% GST apply on EMI)",
        )

    # ── Gate 2: Exact User-Wallet Match ──
    if card is None:
        return False, 0.0, "gate2_no_user_card", "No matching card in user wallet"
    bname = (offer.bank_name or "").strip()
    if not bname or bname.upper() in ("ALL BANKS", "ALL", "SELECT CARDS", "SELECT BANK"):
        return False, 0.0, "gate2_no_explicit_issuer", "Offer lacks explicit bank issuer name"

    card_bank_norm = _normalize_bank_token(card.bank_name)
    offer_bank_norm = _normalize_bank_token(bname)
    c_low = card.card_name.lower().strip()

    if (
        offer_bank_norm != card_bank_norm
        and bname.lower() not in card.bank_name.lower()
        and card.bank_name.lower() not in bname.lower()
    ):
        return False, 0.0, "gate2_issuer_mismatch", f"Issuer '{bname}' does not match wallet card '{card.bank_name}'"

    # Channel check: Debit vs Credit
    is_user_debit = "debit" in c_low
    if offer.card_type == "debit" and not is_user_debit:
        return False, 0.0, "gate2_channel_mismatch", f"Offer requires {bname} Debit Card; '{card.card_name}' is a Credit Card"
    if offer.card_type == "credit" and is_user_debit:
        return False, 0.0, "gate2_channel_mismatch", f"Offer requires {bname} Credit Card; '{card.card_name}' is a Debit Card"

    excluded = [e.lower() for e in (offer.excluded_cards or [])]
    if c_low in excluded or any(_card_name_matches_text(card.card_name, ex) for ex in excluded):
        return False, 0.0, "gate2_card_excluded", f"Card '{card.card_name}' is explicitly excluded in offer T&Cs"
    if c_low in ("sbi cashback", "icici amazon pay") and not _card_name_matches_text(card.card_name, summary_text):
        return False, 0.0, "gate2_cobrand_excluded", f"Co-branded '{card.card_name}' is excluded from general instant discounts"

    # ── Gate 4: Minimum Order Value (`min_spend`) Verification ──
    min_spend = float(offer.min_txn_amount or 0.0)
    if base_price < min_spend:
        return (
            False,
            0.0,
            "gate4_min_spend_not_met",
            f"Listed price ₹{base_price:,.0f} is below required min_spend ₹{min_spend:,.0f}",
        )
    if is_upto and offer.discount_pct <= 0 and not has_flat and min_spend <= 0:
        return (
            False,
            0.0,
            "gate4_conditional_unverified",
            "Up-to offer lacks both a Flat guarantee and a verified min_spend",
        )

    # ── Gate 5: Exact Formula & Sanity Guard ──
    disc = 0.0
    if offer.discount_pct > 0:
        raw_pct_disc = round(base_price * offer.discount_pct / 100.0, 2)
        disc = min(raw_pct_disc, offer.max_discount_cap) if offer.max_discount_cap > 0 else raw_pct_disc
    elif offer.discount_flat > 0:
        disc = round(float(offer.discount_flat), 2)
    else:
        return False, 0.0, "gate5_no_formula", "Offer lacks a valid flat or percentage discount formula"

    if disc <= 0 or disc >= base_price:
        return False, 0.0, "gate5_invalid_discount_bounds", f"Computed discount ₹{disc:,.0f} is out of valid bounds"

    if (
        retailer_advertised_lowest_price
        and retailer_advertised_lowest_price > 0
        and (base_price - disc) < (retailer_advertised_lowest_price - 500.0)
        and not has_flat
        and offer.discount_pct <= 0
    ):
        return (
            False,
            0.0,
            "gate5_advertised_lowest_sanity_failed",
            f"Discount drops price below retailer advertised lowest price ₹{retailer_advertised_lowest_price:,.0f}",
        )

    return True, round(disc, 2), "passed", "Verified 5-Gate instant discount"


def _best_verified_instant_discount(
    card: CardLike,
    bank_offers: list[BankOfferRule],
    base_price: float,
    *,
    retailer_advertised_lowest_price: float | None = None,
    audit_log: list[dict] | None = None,
) -> tuple[float, BankOfferRule | None]:
    best_disc = 0.0
    best_rule: BankOfferRule | None = None

    for offer in bank_offers:
        passed, disc, gate_code, reason = evaluate_offer_5_gates(
            offer,
            card,
            base_price,
            retailer_advertised_lowest_price=retailer_advertised_lowest_price,
        )
        if audit_log is not None:
            audit_log.append(
                {
                    "card": card.card_name,
                    "bank": offer.bank_name,
                    "summary": offer.summary,
                    "passed": passed,
                    "discount_inr": disc,
                    "gate": gate_code,
                    "reason": reason,
                }
            )
        if passed and disc > best_disc:
            best_disc = disc
            best_rule = offer

    return round(best_disc, 2), best_rule


def _evaluate_rule_rewards(
    spend_amount: float,
    rule: VerifiedPaymentRule,
) -> PostPurchaseRewards:
    """Compute exact points earned, cash/travel values, enforced monthly cap, and processing fee."""
    unit = rule.spend_unit_inr if rule.spend_unit_inr > 0 else 100.0
    if rule.points_per_spend_unit <= 0 or spend_amount <= 0:
        fee_inr = round(spend_amount * (rule.processing_fee_pct / 100.0), 2) if rule.processing_fee_pct > 0 else 0.0
        return PostPurchaseRewards(
            reward_type=rule.reward_type,
            points_earned=0.0,
            conversion_ratio_cash="",
            conversion_ratio_travel="",
            cash_value_inr=0.0,
            travel_value_inr=0.0,
            monthly_cap_inr=rule.monthly_cap_inr,
            cap_applied=False,
            processing_fee_inr=fee_inr,
        )

    # For vouchers with monthly purchase caps (e.g. ₹10,000/month GyFTR Amazon Pay cap),
    # only the capped spend portion earns accelerated voucher rewards & incurs voucher fee.
    if rule.method_type == "gift_voucher" and rule.monthly_cap_inr and spend_amount > rule.monthly_cap_inr:
        eligible_spend = rule.monthly_cap_inr
        cap_applied = True
    else:
        eligible_spend = spend_amount
        cap_applied = False

    units = math.floor(eligible_spend / unit) if rule.reward_type == "points" and unit >= 50 else (eligible_spend / unit)
    points_earned = round(units * rule.points_per_spend_unit, 2)

    raw_cash = round(points_earned * rule.point_value_cash_inr, 2)
    raw_travel = round(points_earned * max(rule.point_value_travel_inr, rule.point_value_cash_inr), 2)

    # For cashback / card reward caps (e.g. SBI Cashback ₹5,000/month or HDFC Millennia ₹1,000/month)
    if rule.method_type != "gift_voucher" and rule.monthly_cap_inr is not None and rule.monthly_cap_inr > 0:
        if raw_cash > rule.monthly_cap_inr:
            raw_cash = round(rule.monthly_cap_inr, 2)
            cap_applied = True
        if raw_travel > rule.monthly_cap_inr:
            raw_travel = round(rule.monthly_cap_inr, 2)
            cap_applied = True

    fee_inr = round(eligible_spend * (rule.processing_fee_pct / 100.0), 2) if rule.processing_fee_pct > 0 else 0.0

    conv_cash = (
        f"Direct cashback (₹1 = ₹1.00)"
        if rule.reward_type in ("cashback", "voucher_discount") and abs(rule.point_value_cash_inr - 1.0) < 1e-6
        else _format_conversion_ratio(rule.point_value_cash_inr, "statement cash")
    )
    conv_travel = (
        conv_cash
        if abs(rule.point_value_travel_inr - rule.point_value_cash_inr) < 1e-6
        else _format_conversion_ratio(rule.point_value_travel_inr, "travel/vouchers")
    )

    return PostPurchaseRewards(
        reward_type=rule.reward_type,
        points_earned=points_earned,
        conversion_ratio_cash=conv_cash,
        conversion_ratio_travel=conv_travel,
        cash_value_inr=raw_cash,
        travel_value_inr=raw_travel,
        monthly_cap_inr=rule.monthly_cap_inr,
        cap_applied=cap_applied,
        processing_fee_inr=fee_inr,
    )


def compute_cash_vs_points_report(
    *,
    listed_price: float,
    raw_coupons: list[str],
    bank_offers: list[BankOfferRule],
    user_cards: list[CardLike],
    retailer: str,
    verified_rules: list[VerifiedPaymentRule] | None = None,
    raw_live_offers: list[LiveOfferRaw] | None = None,
    delivery_fee: float = 0.0,
    handling_fee: float = 0.0,
    unowned_cards: list[CardLike] | None = None,
    retailer_advertised_lowest_price: float | None = None,
) -> CashVsPointsReport:
    """
    Strict Cash-vs-Points transparency engine.

    Invariants:
    1. NEVER deducts reward points from `checkout_payable_inr`.
    2. ZERO hardcoded ESTIMATE fallbacks or ₹250 UPI placeholders:
       If a route has no live page offer AND no crawled/verified T&C rule,
       it shows `verification_status="no_verified_offer"` with ₹0 discount/rewards
       and `"No active verified offer found"`.
    3. Primary price & routes are strictly based on `user_cards` (user's checked wallet cards).
       Any `unowned_cards` are evaluated separately in `unowned_card_routes`
       ("Other Cards (Not in Your Wallet)") so they NEVER distort the user's real price.
    """
    if listed_price <= 0:
        return CashVsPointsReport(
            listed_price=0.0,
            checkout_payable_inr=0.0,
            best_user_card_net_price_cash=0.0,
            best_user_card_net_price_optimal=0.0,
            efficiency_verdict="Listed price unavailable — unable to compute payment routes.",
        )

    from app.services.live_terms_engine import build_no_verified_offer_rule

    mandatory_fees = round(max(0.0, delivery_fee) + max(0.0, handling_fee), 2)

    # Verified on-page current-item coupon ONLY (cross-sell / future-order / sanity-failed coupons rejected)
    coupon_disc, _ = _resolve_verified_coupon_discount(
        raw_coupons or [],
        listed_price,
        retailer_advertised_lowest_price=retailer_advertised_lowest_price,
    )

    price_after_coupon = max(0.0, round(listed_price - coupon_disc, 2))
    base_payable_no_bank = max(0.0, round(listed_price - coupon_disc + mandatory_fees, 2))

    # Map ONLY genuinely verified rules (live_tc_verified, issuer_tc_verified, or page_verified)
    rule_by_name: dict[str, VerifiedPaymentRule] = {}
    voucher_rule: VerifiedPaymentRule | None = None
    upi_rule: VerifiedPaymentRule | None = None
    latest_verified: datetime | None = None
    verified_statuses = ("live_tc_verified", "issuer_tc_verified", "page_verified")

    for r in verified_rules or []:
        if r.verification_status not in verified_statuses:
            continue
        rule_by_name[r.method_name.lower()] = r
        if r.method_type == "gift_voucher" and voucher_rule is None:
            voucher_rule = r
        elif r.method_type == "upi_app" and upi_rule is None:
            upi_rule = r
        if r.last_verified_at and (latest_verified is None or r.last_verified_at > latest_verified):
            latest_verified = r.last_verified_at

    gate_audit_log: list[dict] = []
    emi_offers: list[dict] = []
    saw_teaser_or_conditional = False

    for bo in bank_offers or []:
        from app.services.llm_parser import classify_offer_clause

        flags = classify_offer_clause(bo.summary or "")
        if getattr(bo, "emi_only", False) or bo.card_type == "emi" or flags.get("emi_only"):
            emi_disc = (
                min(round(price_after_coupon * bo.discount_pct / 100.0, 2), bo.max_discount_cap)
                if (bo.discount_pct > 0 and bo.max_discount_cap > 0)
                else (round(price_after_coupon * bo.discount_pct / 100.0, 2) if bo.discount_pct > 0 else bo.discount_flat)
            )
            emi_offers.append(
                {
                    "bank_name": bo.bank_name,
                    "discount_inr": round(emi_disc, 2),
                    "min_spend": bo.min_txn_amount,
                    "summary": bo.summary,
                    "fee_note": "EMI processing fee (typically ₹199–₹299) + 18% GST on interest/fees apply; excluded from full-swipe checkout cash.",
                }
            )
        if getattr(bo, "is_teaser_summary", False) or flags.get("is_teaser_summary"):
            saw_teaser_or_conditional = True

    for lo in raw_live_offers or []:
        if getattr(lo, "is_teaser_summary", False):
            saw_teaser_or_conditional = True

    def _evaluate_card_route(card: CardLike, is_owned: bool) -> tuple[PaymentRouteAnalysis, float, BankOfferRule | None]:
        rule = rule_by_name.get(card.card_name.lower()) or build_no_verified_offer_rule(
            card.card_name, "credit_card", reason="No active verified offer found"
        )
        instant_disc, matched_offer = _best_verified_instant_discount(
            card,
            bank_offers,
            price_after_coupon,
            retailer_advertised_lowest_price=retailer_advertised_lowest_price,
            audit_log=gate_audit_log if is_owned else None,
        )
        payable_today = max(0.0, round(listed_price - coupon_disc - instant_disc + mandatory_fees, 2))

        # Mutual-exclusion guard: if an instant bank discount is applied AND the card's verified T&C exclusions
        # state that instant discount transactions do not earn base reward points/cashback, do not stack them.
        excl_blob = " ".join(rule.exclusions or []).lower()
        instant_excludes_base_rewards = instant_disc > 0 and any(
            phrase in excl_blob
            for phrase in ("no reward points on instant discount", "excludes instant discount", "not stackable with instant discount")
        )

        if rule.verification_status in verified_statuses and not instant_excludes_base_rewards:
            rewards = _evaluate_rule_rewards(payable_today, rule)
        else:
            rewards = PostPurchaseRewards(reward_type="none")

        eff_cash = max(0.0, round(payable_today + rewards.processing_fee_inr - rewards.cash_value_inr, 2))
        eff_opt = max(
            0.0,
            round(payable_today + rewards.processing_fee_inr - max(rewards.cash_value_inr, rewards.travel_value_inr), 2),
        )

        if matched_offer and rule.verification_status in ("live_tc_verified", "issuer_tc_verified"):
            ver_status = rule.verification_status
        elif matched_offer:
            ver_status = "page_verified"
        elif rule.verification_status in verified_statuses:
            ver_status = rule.verification_status
        else:
            ver_status = "no_verified_offer"

        is_cashback = rule.reward_type == "cashback" or (
            rule.reward_type == "none" and ("cashback" in card.card_name.lower() or "amazon pay" in card.card_name.lower() or "flipkart" in card.card_name.lower())
        )
        summary_parts: list[str] = []
        if matched_offer:
            summary_parts.append(f"Live Page Instant Discount: -₹{instant_disc:,.0f} ({matched_offer.summary})")
        if rule.verification_status in verified_statuses and rule.plain_english_summary:
            summary_parts.append(rule.plain_english_summary)
        if not summary_parts:
            summary_parts.append("No active verified offer found")

        route = PaymentRouteAnalysis(
            route_type=(
                "unowned_card"
                if not is_owned
                else ("direct_cashback_card" if is_cashback else "direct_points_card")
            ),
            route_label=(
                "Other Card (Not in Wallet)"
                if not is_owned
                else ("Direct Cashback Card" if is_cashback else "Direct Points Card")
            ),
            method_name=card.card_name,
            is_owned_card=is_owned,
            available=True,
            checkout_payable_inr=payable_today,
            instant_discount_inr=instant_disc,
            rewards=rewards,
            effective_cost_if_cash_inr=eff_cash,
            effective_cost_if_optimal_redemption_inr=eff_opt,
            verification_status=ver_status,  # type: ignore[arg-type]
            source_urls=rule.source_urls,
            exclusions=rule.exclusions,
            last_verified_at=rule.last_verified_at,
            summary=" · ".join(summary_parts),
        )
        return route, instant_disc, matched_offer

    # ── 1. Evaluate ONLY User's Owned Cards ──
    points_candidates: list[PaymentRouteAnalysis] = []
    cashback_candidates: list[PaymentRouteAnalysis] = []
    all_owned_card_routes: list[PaymentRouteAnalysis] = []
    best_instant_bank_disc = 0.0
    winning_instant_offer_summary: str | None = None

    for card in user_cards:
        route, instant_disc, matched_offer = _evaluate_card_route(card, is_owned=True)
        all_owned_card_routes.append(route)
        if instant_disc > best_instant_bank_disc:
            best_instant_bank_disc = instant_disc
            winning_instant_offer_summary = (
                f"{card.card_name}: {matched_offer.summary}" if matched_offer else card.card_name
            )
        if route.route_type == "direct_cashback_card":
            cashback_candidates.append(route)
        else:
            points_candidates.append(route)

    # ── 2. Evaluate Unowned Cards Separately (NEVER pollute primary user prices) ──
    owned_names = {c.card_name.lower() for c in user_cards}
    unowned_card_routes: list[PaymentRouteAnalysis] = []
    for card in unowned_cards or []:
        if card.card_name.lower() in owned_names:
            continue
        u_route, _, _ = _evaluate_card_route(card, is_owned=False)
        # Only surface unowned cards that have an actual verified instant offer or verified T&C reward
        if u_route.instant_discount_inr > 0 or u_route.rewards.cash_value_inr > 0 or u_route.rewards.travel_value_inr > 0:
            unowned_card_routes.append(u_route)
    unowned_card_routes.sort(key=lambda r: (r.effective_cost_if_cash_inr, r.checkout_payable_inr))

    routes: list[PaymentRouteAnalysis] = []

    # Route 1: Best Owned Direct Points Card
    if points_candidates:
        points_candidates.sort(key=lambda r: (r.effective_cost_if_optimal_redemption_inr, r.checkout_payable_inr))
        routes.append(points_candidates[0])
    else:
        routes.append(
            PaymentRouteAnalysis(
                route_type="direct_points_card",
                route_label="Direct Points Card",
                method_name="No points card selected in wallet",
                is_owned_card=True,
                available=False,
                checkout_payable_inr=base_payable_no_bank,
                effective_cost_if_cash_inr=base_payable_no_bank,
                effective_cost_if_optimal_redemption_inr=base_payable_no_bank,
                verification_status="no_verified_offer",
                summary="Select your points cards in My Card Wallet to evaluate verified offers.",
            )
        )

    # Route 2: Best Owned Direct Cashback Card
    if cashback_candidates:
        cashback_candidates.sort(key=lambda r: (r.effective_cost_if_cash_inr, r.checkout_payable_inr))
        routes.append(cashback_candidates[0])
    else:
        routes.append(
            PaymentRouteAnalysis(
                route_type="direct_cashback_card",
                route_label="Direct Cashback Card",
                method_name="No cashback card selected in wallet",
                is_owned_card=True,
                available=False,
                checkout_payable_inr=base_payable_no_bank,
                effective_cost_if_cash_inr=base_payable_no_bank,
                effective_cost_if_optimal_redemption_inr=base_payable_no_bank,
                verification_status="no_verified_offer",
                summary="Select your cashback cards in My Card Wallet to evaluate verified offers.",
            )
        )

    # Route 3: Gift Voucher / SmartBuy / GyFTR Stack (ONLY if live T&C verified — zero fake estimates)
    store_label = retailer.replace("_", " ").title()
    if voucher_rule is not None:
        voucher_payable = max(0.0, round(listed_price - coupon_disc - voucher_rule.instant_discount_inr + mandatory_fees, 2))
        voucher_rewards = _evaluate_rule_rewards(voucher_payable, voucher_rule)
        voucher_eff_cash = max(0.0, round(voucher_payable + voucher_rewards.processing_fee_inr - voucher_rewards.cash_value_inr, 2))
        voucher_eff_opt = max(
            0.0,
            round(voucher_payable + voucher_rewards.processing_fee_inr - max(voucher_rewards.cash_value_inr, voucher_rewards.travel_value_inr), 2),
        )
        routes.append(
            PaymentRouteAnalysis(
                route_type="gift_voucher_stack",
                route_label="Gift Voucher / SmartBuy / GyFTR",
                method_name=voucher_rule.method_name,
                is_owned_card=True,
                available=True,
                checkout_payable_inr=round(voucher_payable + voucher_rewards.processing_fee_inr, 2),
                instant_discount_inr=voucher_rule.instant_discount_inr,
                rewards=voucher_rewards,
                effective_cost_if_cash_inr=voucher_eff_cash,
                effective_cost_if_optimal_redemption_inr=voucher_eff_opt,
                verification_status=voucher_rule.verification_status,
                source_urls=voucher_rule.source_urls,
                exclusions=voucher_rule.exclusions,
                last_verified_at=voucher_rule.last_verified_at,
                summary=voucher_rule.plain_english_summary,
            )
        )
    else:
        routes.append(
            PaymentRouteAnalysis(
                route_type="gift_voucher_stack",
                route_label="Gift Voucher / SmartBuy / GyFTR",
                method_name=f"Gift Voucher ({store_label})",
                is_owned_card=True,
                available=False,
                checkout_payable_inr=base_payable_no_bank,
                instant_discount_inr=0.0,
                rewards=PostPurchaseRewards(reward_type="none"),
                effective_cost_if_cash_inr=base_payable_no_bank,
                effective_cost_if_optimal_redemption_inr=base_payable_no_bank,
                verification_status="no_verified_offer",
                summary="No active verified offer found",
            )
        )

    # Route 4: UPI App Offer (ONLY live page verified OR live T&C verified — NEVER static ₹250 estimate!)
    page_upi_val = 0.0
    page_upi_desc = ""
    page_upi_is_instant = False
    for lo in raw_live_offers or []:
        v, d = _parse_live_cashback_or_upi(lo.raw_text, price_after_coupon)
        if v > page_upi_val:
            page_upi_val = v
            page_upi_desc = d
            page_upi_is_instant = d.startswith("UPI Instant Discount")

    if page_upi_val > 0:
        if page_upi_is_instant:
            upi_checkout = max(0.0, round(base_payable_no_bank - page_upi_val, 2))
            upi_rewards = PostPurchaseRewards(reward_type="none")
            upi_eff = upi_checkout
            upi_inst = page_upi_val
        else:
            upi_checkout = base_payable_no_bank
            upi_rewards = PostPurchaseRewards(
                reward_type="cashback",
                points_earned=page_upi_val,
                conversion_ratio_cash="Direct cashback (₹1 = ₹1.00)",
                conversion_ratio_travel="Direct cashback (₹1 = ₹1.00)",
                cash_value_inr=page_upi_val,
                travel_value_inr=page_upi_val,
                monthly_cap_inr=page_upi_val,
                cap_applied=False,
                processing_fee_inr=0.0,
            )
            upi_eff = max(0.0, round(base_payable_no_bank - page_upi_val, 2))
            upi_inst = 0.0
        routes.append(
            PaymentRouteAnalysis(
                route_type="upi_app_offer",
                route_label="UPI App Offer",
                method_name="Live Page UPI Offer",
                is_owned_card=True,
                available=True,
                checkout_payable_inr=upi_checkout,
                instant_discount_inr=upi_inst,
                rewards=upi_rewards,
                effective_cost_if_cash_inr=upi_eff,
                effective_cost_if_optimal_redemption_inr=upi_eff,
                verification_status="page_verified",
                summary=page_upi_desc,
            )
        )
    elif upi_rule is not None:
        upi_rewards = _evaluate_rule_rewards(base_payable_no_bank, upi_rule)
        upi_eff = max(0.0, round(base_payable_no_bank - upi_rewards.cash_value_inr, 2))
        routes.append(
            PaymentRouteAnalysis(
                route_type="upi_app_offer",
                route_label="UPI App Offer",
                method_name=upi_rule.method_name,
                is_owned_card=True,
                available=True,
                checkout_payable_inr=base_payable_no_bank,
                instant_discount_inr=0.0,
                rewards=upi_rewards,
                effective_cost_if_cash_inr=upi_eff,
                effective_cost_if_optimal_redemption_inr=upi_eff,
                verification_status=upi_rule.verification_status,
                source_urls=upi_rule.source_urls,
                exclusions=upi_rule.exclusions,
                last_verified_at=upi_rule.last_verified_at,
                summary=upi_rule.plain_english_summary,
            )
        )
    else:
        routes.append(
            PaymentRouteAnalysis(
                route_type="upi_app_offer",
                route_label="UPI App Offer",
                method_name="UPI Apps (CRED / Super.money / Amazon Pay UPI)",
                is_owned_card=True,
                available=False,
                checkout_payable_inr=base_payable_no_bank,
                instant_discount_inr=0.0,
                rewards=PostPurchaseRewards(reward_type="none"),
                effective_cost_if_cash_inr=base_payable_no_bank,
                effective_cost_if_optimal_redemption_inr=base_payable_no_bank,
                verification_status="no_verified_offer",
                summary="No active verified offer found",
            )
        )

    # Guaranteed checkout cash price today (strictly user's owned cards + live page offers)
    hero_checkout_payable = max(
        0.0,
        round(listed_price - coupon_disc - best_instant_bank_disc + mandatory_fees, 2),
    )

    available_routes = [r for r in routes if r.available]
    best_cash = min(available_routes, key=lambda r: r.effective_cost_if_cash_inr) if available_routes else None
    best_opt = min(available_routes, key=lambda r: r.effective_cost_if_optimal_redemption_inr) if available_routes else None

    best_user_card_net_cash = best_cash.effective_cost_if_cash_inr if best_cash else hero_checkout_payable
    best_user_card_net_opt = best_opt.effective_cost_if_optimal_redemption_inr if best_opt else hero_checkout_payable
    winning_owned_card_name: str | None = None
    if all_owned_card_routes:
        win_owned = min(all_owned_card_routes, key=lambda r: (r.effective_cost_if_cash_inr, r.checkout_payable_inr))
        winning_owned_card_name = win_owned.method_name

    # Plain-English efficiency verdict
    verdict_parts: list[str] = []
    if not user_cards:
        verdict_parts.append(
            f"No cards selected in My Card Wallet — guaranteed checkout cash price is ₹{hero_checkout_payable:,.0f}."
        )
        if unowned_card_routes:
            top_unowned = unowned_card_routes[0]
            verdict_parts.append(
                f"Note: {top_unowned.method_name} (not in your wallet) has a verified offer bringing net cost to ₹{top_unowned.effective_cost_if_cash_inr:,.0f}."
            )
    elif best_instant_bank_disc > 0:
        verdict_parts.append(
            f"Cheapest checkout cash price with your wallet is ₹{hero_checkout_payable:,.0f} "
            f"(includes -₹{best_instant_bank_disc:,.0f} verified live instant bank discount"
            + (f", -₹{coupon_disc:,.0f} coupon" if coupon_disc > 0 else "")
            + (f", +₹{mandatory_fees:,.0f} mandatory fees" if mandatory_fees > 0 else "")
            + ")."
        )
    else:
        verdict_parts.append(
            f"No verified live instant bank discount matches your selected cards — checkout cash price today is ₹{hero_checkout_payable:,.0f}"
            + (f" (after -₹{coupon_disc:,.0f} coupon)" if coupon_disc > 0 else "")
            + (f" (including +₹{mandatory_fees:,.0f} mandatory fees)" if mandatory_fees > 0 else "")
            + "."
        )

    if best_cash and best_opt and (
        best_cash.effective_cost_if_cash_inr < hero_checkout_payable
        or best_opt.effective_cost_if_optimal_redemption_inr < hero_checkout_payable
    ):
        if best_cash.method_name == best_opt.method_name:
            verdict_parts.append(
                f"Best owned route is {best_cash.route_label} ({best_cash.method_name}): "
                f"effective net ₹{best_cash.effective_cost_if_cash_inr:,.0f} for cash"
                + (
                    f" (₹{best_opt.effective_cost_if_optimal_redemption_inr:,.0f} if redeemed for travel/vouchers)."
                    if best_opt.effective_cost_if_optimal_redemption_inr < best_cash.effective_cost_if_cash_inr
                    else "."
                )
            )
        else:
            gap = round(best_cash.effective_cost_if_cash_inr - best_opt.effective_cost_if_optimal_redemption_inr, 0)
            verdict_parts.append(
                f"Cash vs Points trade-off: Direct cash wins with {best_cash.method_name} (net ₹{best_cash.effective_cost_if_cash_inr:,.0f}); "
                f"{best_opt.method_name} beats it by ₹{gap:,.0f} (net ₹{best_opt.effective_cost_if_optimal_redemption_inr:,.0f}) ONLY if redeemed for travel/vouchers."
            )

    capped = [r for r in available_routes if r.rewards.cap_applied]
    if capped:
        verdict_parts.append(
            f"Cap warning: {capped[0].method_name} hits its ₹{capped[0].rewards.monthly_cap_inr:,.0f} monthly cap on this order."
        )
    fee_routes = [r for r in available_routes if r.rewards.processing_fee_inr > 0]
    if fee_routes:
        verdict_parts.append(
            f"Voucher fee note: {fee_routes[0].method_name} adds ₹{fee_routes[0].rewards.processing_fee_inr:,.0f} processing fee."
        )

    requires_verification = bool(
        best_instant_bank_disc == 0.0
        and (
            saw_teaser_or_conditional
            or any(g.get("gate") in ("gate1_teaser_summary", "gate4_conditional_unverified") for g in gate_audit_log)
        )
    )
    verification_note = (
        "Teaser or conditional 'up to' bank banners were found on the page without verifiable flat/percentage eligibility for your wallet. "
        "Guaranteed instant discount is kept at ₹0 until offer drawer/T&Cs are verified."
        if requires_verification
        else ""
    )
    if requires_verification:
        verdict_parts.append(verification_note)

    return CashVsPointsReport(
        listed_price=listed_price,
        coupon_discount_inr=coupon_disc,
        mandatory_fees_inr=mandatory_fees,
        instant_bank_discount_inr=best_instant_bank_disc,
        checkout_payable_inr=hero_checkout_payable,
        best_user_card_net_price_cash=best_user_card_net_cash,
        best_user_card_net_price_optimal=best_user_card_net_opt,
        winning_owned_card_name=winning_owned_card_name,
        winning_instant_offer_summary=winning_instant_offer_summary,
        user_wallet_empty=(len(user_cards) == 0),
        requires_offer_verification=requires_verification,
        offer_verification_note=verification_note,
        emi_offers=emi_offers,
        gate_audit_log=gate_audit_log,
        routes=routes,
        unowned_card_routes=unowned_card_routes,
        best_route_cash=best_cash.method_name if best_cash else None,
        best_route_optimal=best_opt.method_name if best_opt else None,
        efficiency_verdict=" ".join(verdict_parts),
        terms_last_verified_at=latest_verified,
    )


def compute_stacked_offer_price(
    listed_price: float,
    raw_coupons: list[str],
    bank_offers: list[BankOfferRule],
    user_cards: list[CardLike],
    retailer: str,
    raw_live_offers: list[LiveOfferRaw] | None = None,
    delivery_fee: float = 0.0,
    handling_fee: float = 0.0,
    all_cards: list[CardLike] | None = None,
    retailer_advertised_lowest_price: float | None = None,
) -> StackedEffectivePriceResult:
    """
    Compute stacked effective price from live page offers + card routes.
    Deterministic Python math — every rupee traces to an explicit offer rule.
    """
    if listed_price <= 0:
        return StackedEffectivePriceResult(
            original_listed_price=0.0,
            best_stacked_price=0.0,
            total_savings=0.0,
            steps=[],
            applied_offers=[],
            alternative_paths=[],
            notes="Listed price is zero or unavailable.",
        )

    running_price = listed_price
    steps: list[StackStep] = [
        StackStep(
            step_name="Listed Store Price",
            description=f"Initial base price on {retailer.replace('_', ' ').title()}",
            amount_deducted=0.0,
            running_price=running_price,
            verification_status="verified_on_page",
        )
    ]
    applied_offers: list[dict] = []

    # ── Step 1: Live Page Coupon (strict current-item coupon + advertised lowest-price sanity check) ──
    best_coupon_disc, best_coupon_desc = _resolve_verified_coupon_discount(
        raw_coupons or [],
        running_price,
        retailer_advertised_lowest_price=retailer_advertised_lowest_price,
    )

    if best_coupon_disc > 0:
        running_price = round(running_price - best_coupon_disc, 2)
        steps.append(
            StackStep(
                step_name="Store Page Coupon Checkbox",
                description=best_coupon_desc,
                amount_deducted=best_coupon_disc,
                running_price=running_price,
                verification_status="verified_on_page",
            )
        )
        applied_offers.append({
            "type": "coupon",
            "title": best_coupon_desc,
            "saving": best_coupon_disc,
            "source": "live_page",
        })

    # ── Step 2: Live Bank Instant Discount (Enforcing 5-Gate Math Guard) ──
    best_bank_disc = 0.0
    best_bank_name = ""
    best_bank_rule: BankOfferRule | None = None
    winning_card: CardLike | None = None

    for card in user_cards:
        disc, matched_rule = _best_verified_instant_discount(
            card,
            bank_offers,
            running_price,
            retailer_advertised_lowest_price=retailer_advertised_lowest_price,
        )
        if disc > best_bank_disc and matched_rule is not None:
            best_bank_disc = disc
            best_bank_name = card.card_name
            best_bank_rule = matched_rule
            winning_card = card

    if best_bank_disc > 0 and best_bank_rule:
        running_price = round(running_price - best_bank_disc, 2)
        summary = best_bank_rule.summary or f"{best_bank_rule.bank_name} Instant Discount"
        steps.append(
            StackStep(
                step_name=f"Bank Instant Discount ({best_bank_name})",
                description=summary,
                amount_deducted=round(best_bank_disc, 2),
                running_price=running_price,
                verification_status="verified_on_page",
            )
        )
        applied_offers.append({
            "type": "bank_instant",
            "title": summary,
            "card": best_bank_name,
            "saving": round(best_bank_disc, 2),
            "source": "live_page",
        })

    # ── Step 3: Post-Purchase Card Reward Points / Cashback (tagged post_purchase, never disguised as upfront cash) ──
    reward_val = 0.0
    if winning_card:
        pct = winning_card.base_reward_pct
        if retailer == "amazon_in":
            pct = winning_card.amazon_reward_pct
        elif retailer == "flipkart":
            pct = winning_card.flipkart_reward_pct

        if pct > 0:
            reward_val = round(running_price * pct / 100.0, 2)
            running_price = round(running_price - reward_val, 2)
            steps.append(
                StackStep(
                    step_name=f"Credit Card Rewards ({pct}%) [Post-Purchase]",
                    description=f"{winning_card.card_name} {pct}% post-purchase reward value (not deducted at checkout)",
                    amount_deducted=reward_val,
                    running_price=running_price,
                    verification_status="post_purchase",
                )
            )
            applied_offers.append({
                "type": "card_rewards",
                "title": f"{winning_card.card_name} Rewards (Post-Purchase)",
                "saving": reward_val,
                "reward_pct": pct,
                "source": "user_wallet",
            })
    elif user_cards:
        best_rew_card = max(
            user_cards,
            key=lambda c: (c.amazon_reward_pct if retailer == "amazon_in" else c.flipkart_reward_pct if retailer == "flipkart" else c.base_reward_pct),
        )
        pct = (
            best_rew_card.amazon_reward_pct
            if retailer == "amazon_in"
            else best_rew_card.flipkart_reward_pct
            if retailer == "flipkart"
            else best_rew_card.base_reward_pct
        )
        if pct > 0:
            reward_val = round(running_price * pct / 100.0, 2)
            running_price = round(running_price - reward_val, 2)
            steps.append(
                StackStep(
                    step_name=f"Standard Card Rewards ({pct}%) [Post-Purchase]",
                    description=f"{best_rew_card.card_name} {pct}% post-purchase reward estimate",
                    amount_deducted=reward_val,
                    running_price=running_price,
                    verification_status="post_purchase",
                )
            )

    # ── Step 4: Live Payment Gateway / UPI Cashback (if present on page) ──
    live_upi_disc = 0.0
    live_upi_desc = ""
    if raw_live_offers:
        for lo in raw_live_offers:
            disc, desc = _parse_live_cashback_or_upi(lo.raw_text, running_price)
            if disc > live_upi_disc:
                live_upi_disc = disc
                live_upi_desc = desc

    if live_upi_disc > 0:
        running_price = round(running_price - live_upi_disc, 2)
        steps.append(
            StackStep(
                step_name="UPI / Payment Gateway Cashback",
                description=live_upi_desc,
                amount_deducted=live_upi_disc,
                running_price=running_price,
                verification_status="verified_on_page",
            )
        )
        applied_offers.append({
            "type": "upi_cashback",
            "title": live_upi_desc,
            "saving": live_upi_disc,
            "source": "live_page",
        })

    # ── Step 5: Handling / Delivery Fees Adjustment ──
    total_fee = delivery_fee + handling_fee
    if total_fee > 0:
        running_price = round(running_price + total_fee, 2)
        steps.append(
            StackStep(
                step_name="Delivery / Platform Handling Fee",
                description=f"+₹{total_fee:,.0f} mandatory platform surcharge",
                amount_deducted=-total_fee,
                running_price=running_price,
                verification_status="fee",
            )
        )

    total_savings = round(listed_price - running_price, 2)

    # ── Alternative Optimization Paths ──
    alternative_paths: list[AlternativeOptimizationPath] = []
    if all_cards:
        owned_names = {c.card_name.lower() for c in user_cards}
        for card in all_cards:
            if card.card_name.lower() in owned_names:
                continue
            for offer in bank_offers:
                if offer.bank_name.lower() in card.bank_name.lower() or card.bank_name.lower() in offer.bank_name.lower():
                    disc = offer.discount_flat if offer.discount_flat > 0 else (listed_price * offer.discount_pct / 100.0)
                    if offer.max_discount_cap > 0:
                        disc = min(disc, offer.max_discount_cap)
                    alt_net = round(listed_price - best_coupon_disc - disc, 2)
                    saving_diff = round(running_price - alt_net, 2)
                    if saving_diff > 800:
                        alternative_paths.append(
                            AlternativeOptimizationPath(
                                title=f"Switch to {card.card_name}",
                                method="Bank Instant Discount Stack",
                                potential_savings=round(disc, 2),
                                net_price=alt_net,
                                confidence="high",
                                eligibility=f"Requires {card.card_name} ({offer.bank_name}) on min spend ₹{offer.min_txn_amount:,.0f}",
                            )
                        )
                        break

    no_cost_emi_found = any("no cost emi" in (o.summary or "").lower() for o in bank_offers)
    if no_cost_emi_found:
        alternative_paths.append(
            AlternativeOptimizationPath(
                title="No-Cost EMI Interest Waiver",
                method="Equal Monthly Installments",
                potential_savings=round(listed_price * 0.08, 2),
                net_price=running_price,
                confidence="high",
                eligibility="Select 3 or 6 months No-Cost EMI at payment page with eligible bank cards",
            )
        )

    return StackedEffectivePriceResult(
        original_listed_price=listed_price,
        best_stacked_price=max(0.0, running_price),
        total_savings=max(0.0, total_savings),
        steps=steps,
        applied_offers=applied_offers,
        alternative_paths=alternative_paths,
        notes="Checkout discounts separated strictly from post-purchase reward points (see Cash vs Points report).",
    )
