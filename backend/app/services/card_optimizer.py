"""
Deterministic credit card optimizer.

ALL math is pure Python — no LLM involved.
Computes effective checkout price per card and surfaces the best card
plus a "Missed Savings" callout for unowned cards that save >₹1,000 more.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from app.schemas.extraction import BankOfferRule


# ── Result types ─────────────────────────────

@dataclass
class CardResult:
    card_name: str
    bank_name: str
    effective_price: float
    guaranteed_price: float
    bank_discount: float
    reward_value: float
    reward_pct: float
    offer_summary: str = ""


@dataclass
class MissedSaving:
    card_name: str
    bank_name: str
    effective_price: float
    savings_vs_best_owned: float


@dataclass
class OptimizerResult:
    listed_price: float
    coupon_discount: float
    platform_fee: float
    guaranteed_price: float
    ranked_cards: list[CardResult]
    best_card: CardResult | None
    missed_savings: MissedSaving | None


# ── Card-like protocol (matches CreditCard model) ──

class CardLike:
    """Minimal interface a card object must satisfy."""
    card_name: str
    bank_name: str
    base_reward_pct: float
    amazon_reward_pct: float
    flipkart_reward_pct: float


# ── Core math ────────────────────────────────

def _reward_pct_for_retailer(card: CardLike, retailer: str) -> float:
    if retailer == "amazon_in":
        return card.amazon_reward_pct
    if retailer == "flipkart":
        return card.flipkart_reward_pct
    return card.base_reward_pct


def _card_matches_offer(card: CardLike, offer: BankOfferRule) -> bool:
    """Check if a card is eligible for a bank offer (name match)."""
    return offer.bank_name.lower() in card.bank_name.lower() or card.bank_name.lower() in offer.bank_name.lower()


def _best_bank_discount(
    card: CardLike,
    offers: list[BankOfferRule],
    guaranteed_price: float,
) -> tuple[float, str]:
    """Return (best_discount, offer_summary) for this card after 5-gate verification."""
    from app.services.offer_stack_engine import evaluate_offer_5_gates

    best = 0.0
    summary = ""
    for offer in offers:
        passed, disc, _, _ = evaluate_offer_5_gates(offer, card, guaranteed_price)
        if not passed:
            continue
        if disc > best:
            best = disc
            summary = offer.summary or f"{offer.bank_name} {offer.discount_flat or offer.discount_pct}% off"
    return round(best, 2), summary


def _compute_for_card(
    card: CardLike,
    guaranteed_price: float,
    offers: list[BankOfferRule],
    retailer: str,
) -> CardResult:
    bank_disc, offer_summary = _best_bank_discount(card, offers, guaranteed_price)
    reward_pct = _reward_pct_for_retailer(card, retailer)
    reward_val = round(guaranteed_price * reward_pct / 100.0, 2)
    effective = round(guaranteed_price - bank_disc - reward_val, 2)
    return CardResult(
        card_name=card.card_name,
        bank_name=card.bank_name,
        effective_price=effective,
        guaranteed_price=guaranteed_price,
        bank_discount=bank_disc,
        reward_value=reward_val,
        reward_pct=reward_pct,
        offer_summary=offer_summary,
    )


def calculate_effective_prices(
    listed_price: float,
    coupon_discount: float,
    platform_fee: float,
    bank_offers: list[BankOfferRule],
    user_cards: list[CardLike],
    retailer: str,
    all_cards: list[CardLike] | None = None,
) -> OptimizerResult:
    """
    Pure-Python deterministic price calculator.

    guaranteed_price = listed_price - coupon_discount + platform_fee
    effective_price  = guaranteed_price - bank_discount - reward_value
    """
    guaranteed = round(listed_price - coupon_discount + platform_fee, 2)

    ranked = sorted(
        [_compute_for_card(c, guaranteed, bank_offers, retailer) for c in user_cards],
        key=lambda r: r.effective_price,
    )

    best = ranked[0] if ranked else None

    # Missed savings: check unowned cards
    missed: MissedSaving | None = None
    if all_cards and best:
        owned_names = {c.card_name for c in user_cards}
        for card in all_cards:
            if card.card_name in owned_names:
                continue
            res = _compute_for_card(card, guaranteed, bank_offers, retailer)
            saving = best.effective_price - res.effective_price
            if saving > 1000:
                if missed is None or res.effective_price < missed.effective_price:
                    missed = MissedSaving(
                        card_name=card.card_name,
                        bank_name=card.bank_name,
                        effective_price=res.effective_price,
                        savings_vs_best_owned=round(saving, 2),
                    )

    return OptimizerResult(
        listed_price=listed_price,
        coupon_discount=coupon_discount,
        platform_fee=platform_fee,
        guaranteed_price=guaranteed,
        ranked_cards=ranked,
        best_card=best,
        missed_savings=missed,
    )
