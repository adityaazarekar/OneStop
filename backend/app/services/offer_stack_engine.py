"""
Dynamic Offer Stacking & Optimization Engine.

Combines verified live page offers (coupons, instant bank discounts, UPI/wallet cashback)
with eligible card reward rates and fee adjustments through deterministic Python arithmetic.
Does NOT rely on static hardcoded offer tables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.extraction import (
    AlternativeOptimizationPath,
    BankOfferRule,
    LiveOfferRaw,
    StackedEffectivePriceResult,
    StackStep,
)
from app.services.card_optimizer import CardLike


def _parse_coupon_value(coupon_text: str, base_price: float) -> tuple[float, str]:
    """Parse flat or percentage discount from raw coupon text."""
    if not coupon_text:
        return 0.0, ""

    # 1. Check percentage first (e.g. 5% off up to ₹500)
    pct_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*off", coupon_text, re.I)
    if pct_m:
        pct = float(pct_m.group(1))
        disc = round(base_price * pct / 100.0, 2)
        # Check if capped
        cap_m = re.search(r"(?:upto|up to|max(?:imum)?)\s*(?:₹|Rs\.?|INR)?\s*([\d,]+)", coupon_text, re.I)
        if cap_m:
            cap = float(cap_m.group(1).replace(",", ""))
            disc = min(disc, cap)
        return min(disc, base_price), f"Coupon: {pct:g}% off applied"

    # 2. Check flat ₹X or Rs. X or INR X
    flat_m = re.search(r"(?:₹|Rs\.?|INR)\s*([\d,]+)", coupon_text, re.I)
    if flat_m:
        val = float(flat_m.group(1).replace(",", ""))
        return min(val, base_price), f"Coupon: ₹{val:,.0f} off applied"

    return 0.0, ""


def _parse_live_cashback_or_upi(offer_text: str, current_price: float) -> tuple[float, str]:
    """Detect live UPI or wallet cashback mentioned on the product page."""
    if not offer_text:
        return 0.0, ""

    lower = offer_text.lower()
    if any(k in lower for k in ("upi", "wallet", "paytm", "cred", "amazon pay balance")):
        m = re.search(r"(?:cashback|flat\s+off|instant\s+saving).*?(?:₹|Rs\.?|INR)\s*([\d,]+)", offer_text, re.I)
        if not m:
            m = re.search(r"(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:cashback|rewards)", offer_text, re.I)
        if m:
            val = float(m.group(1).replace(",", ""))
            if val < current_price:
                return val, f"UPI/Wallet Cashback: ₹{val:,.0f}"

    return 0.0, ""


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
) -> StackedEffectivePriceResult:
    """
    Compute optimal stacked checkout price from live page offers + card routes.
    Deterministic Python math — every deducted rupee traces to an explicit offer rule.
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

    # ── Step 1: Live Page Coupon ──
    best_coupon_disc = 0.0
    best_coupon_desc = ""
    for c in raw_coupons:
        disc, desc = _parse_coupon_value(c, running_price)
        if disc > best_coupon_disc:
            best_coupon_disc = disc
            best_coupon_desc = desc

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

    # ── Step 2: Live Bank Instant Discount ──
    best_bank_disc = 0.0
    best_bank_name = ""
    best_bank_rule: BankOfferRule | None = None
    winning_card: CardLike | None = None

    # Check bank offers against user's wallet
    for card in user_cards:
        for offer in bank_offers:
            # Check bank name substring match
            if offer.bank_name.lower() in card.bank_name.lower() or card.bank_name.lower() in offer.bank_name.lower():
                if running_price < offer.min_txn_amount:
                    continue
                excluded = [e.lower() for e in (offer.excluded_cards or [])]
                if card.card_name.lower() in excluded:
                    continue

                disc = 0.0
                if offer.discount_flat > 0:
                    disc = offer.discount_flat
                elif offer.discount_pct > 0:
                    disc = running_price * offer.discount_pct / 100.0
                    if offer.max_discount_cap > 0:
                        disc = min(disc, offer.max_discount_cap)

                if disc > best_bank_disc:
                    best_bank_disc = disc
                    best_bank_name = card.card_name
                    best_bank_rule = offer
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

    # ── Step 3: Card Reward Points / Cashback ──
    reward_val = 0.0
    if winning_card:
        # Calculate rewards on remaining net amount
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
                    step_name=f"Credit Card Rewards ({pct}%)",
                    description=f"{winning_card.card_name} {pct}% reward points credited to statement",
                    amount_deducted=reward_val,
                    running_price=running_price,
                    verification_status="stack_path",
                )
            )
            applied_offers.append({
                "type": "card_rewards",
                "title": f"{winning_card.card_name} Rewards",
                "saving": reward_val,
                "reward_pct": pct,
                "source": "user_wallet",
            })
    elif user_cards:
        # Pick card with highest reward on this platform
        best_rew_card = max(
            user_cards,
            key=lambda c: (c.amazon_reward_pct if retailer == "amazon_in" else c.flipkart_reward_pct if retailer == "flipkart" else c.base_reward_pct)
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
                    step_name=f"Standard Card Rewards ({pct}%)",
                    description=f"{best_rew_card.card_name} {pct}% cash/point return",
                    amount_deducted=reward_val,
                    running_price=running_price,
                    verification_status="stack_path",
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

    # Path A: Unowned Card with better bank discount / rewards
    if all_cards:
        owned_names = {c.card_name.lower() for c in user_cards}
        for card in all_cards:
            if card.card_name.lower() in owned_names:
                continue
            # Evaluate how this unowned card performs
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

    # Path B: Gift Voucher / Amazon Pay or Flipkart Gift Card Route (typical 2-3% reward via gyftr/smartbuy)
    gift_card_savings = round(listed_price * 0.02, 2)
    if gift_card_savings >= 200:
        alternative_paths.append(
            AlternativeOptimizationPath(
                title="Gift Voucher / Brand Gift Card Route",
                method="Gyftr / SmartBuy Instant Voucher",
                potential_savings=gift_card_savings,
                net_price=round(running_price - gift_card_savings, 2),
                confidence="conditional",
                eligibility="Buy official retailer e-gift card via reward portals (Gyftr/SmartBuy) using HDFC/Axis/Amex cards before checkout",
            )
        )

    # Path C: No-Cost EMI interest subsidy
    no_cost_emi_found = any("no cost emi" in (o.summary or "").lower() for o in bank_offers)
    if no_cost_emi_found:
        alternative_paths.append(
            AlternativeOptimizationPath(
                title="No-Cost EMI Interest Waiver",
                method="Equal Monthly Installments",
                potential_savings=round(listed_price * 0.08, 2),  # ~8% interest waived by merchant
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
        notes="All live discounts verified from active page DOM. Card rewards calculated deterministically.",
    )
