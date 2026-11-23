"""
Unit test suite for the Dynamic Offer Stacking Engine.

All math is deterministic Python arithmetic — no hardcoded tables or LLMs.
"""
from __future__ import annotations

import pytest

from app.schemas.extraction import BankOfferRule, LiveOfferRaw
from app.services.card_optimizer import CardLike
from app.services.offer_stack_engine import (
    compute_stacked_offer_price,
    _parse_coupon_value,
    _parse_live_cashback_or_upi,
)


def _mock_card(name: str, bank: str, base: float = 1.0, amz: float = 1.0, fk: float = 1.0) -> CardLike:
    c = CardLike()
    c.card_name = name
    c.bank_name = bank
    c.base_reward_pct = base
    c.amazon_reward_pct = amz
    c.flipkart_reward_pct = fk
    return c


def test_parse_coupon_flat_and_pct():
    disc, desc = _parse_coupon_value("Apply ₹1,000 coupon at checkout", 50000.0)
    assert disc == 1000.0
    assert "₹1,000" in desc

    disc_pct, desc_pct = _parse_coupon_value("5% off coupon with max ₹500", 20000.0)
    assert disc_pct == 500.0
    assert "5% off" in desc_pct


def test_parse_live_cashback():
    val, desc = _parse_live_cashback_or_upi("Pay using Amazon Pay UPI and get flat ₹50 cashback", 15000.0)
    assert val == 50.0
    assert "₹50" in desc


def test_coupon_bank_rewards_stack():
    # 50,000 listed price
    # Coupon: ₹1,000 off -> 49,000
    # Bank offer: HDFC flat ₹2,000 off -> 47,000
    # Card reward: HDFC Infinia 3.3% on 47,000 -> 1,551
    # Net: 45,449
    card = _mock_card("HDFC Infinia", "HDFC", base=3.3, amz=3.3, fk=3.3)
    offer = BankOfferRule(
        bank_name="HDFC",
        discount_flat=2000.0,
        min_txn_amount=10000.0,
        summary="Flat ₹2,000 off with HDFC Cards",
    )

    res = compute_stacked_offer_price(
        listed_price=50000.0,
        raw_coupons=["Apply ₹1,000 coupon"],
        bank_offers=[offer],
        user_cards=[card],
        retailer="amazon_in",
    )

    assert res.original_listed_price == 50000.0
    assert res.best_stacked_price == 45449.0
    assert res.total_savings == 4551.0
    assert len(res.steps) == 4  # Listed, Coupon, Bank, Rewards


def test_fee_surcharge_application():
    card = _mock_card("Basic Card", "SBI", base=0.0, amz=0.0, fk=0.0)
    res = compute_stacked_offer_price(
        listed_price=2000.0,
        raw_coupons=[],
        bank_offers=[],
        user_cards=[card],
        retailer="flipkart",
        delivery_fee=40.0,
        handling_fee=99.0,
    )
    # 2000 + 40 + 99 = 2139
    assert res.best_stacked_price == 2139.0
    assert any("Platform Handling Fee" in s.step_name for s in res.steps)


def test_alternative_paths_unowned_card():
    owned = _mock_card("Basic Card", "SBI", base=0.0, amz=0.0, fk=0.0)
    unowned = _mock_card("ICICI Coral", "ICICI", base=1.0, amz=1.0, fk=1.0)
    offer = BankOfferRule(
        bank_name="ICICI",
        discount_flat=3000.0,
        min_txn_amount=25000.0,
        summary="Flat ₹3,000 off with ICICI Cards",
    )

    res = compute_stacked_offer_price(
        listed_price=35000.0,
        raw_coupons=[],
        bank_offers=[offer],
        user_cards=[owned],
        retailer="croma",
        all_cards=[owned, unowned],
    )

    # Alternative path should recommend switching to ICICI
    assert any("Switch to ICICI Coral" in p.title for p in res.alternative_paths)
