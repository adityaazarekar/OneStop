"""
Comprehensive pytest suite for the deterministic card optimizer.

All tests use pure Python math — no DB, no LLM, no async.
"""
from __future__ import annotations

import pytest

from app.schemas.extraction import BankOfferRule
from app.services.card_optimizer import (
    CardLike,
    CardResult,
    MissedSaving,
    OptimizerResult,
    calculate_effective_prices,
)


# ── Fixtures ─────────────────────────────────


def _card(
    name: str = "Test Card",
    bank: str = "HDFC",
    base: float = 1.0,
    amazon: float = 1.0,
    flipkart: float = 1.0,
) -> CardLike:
    c = CardLike()
    c.card_name = name
    c.bank_name = bank
    c.base_reward_pct = base
    c.amazon_reward_pct = amazon
    c.flipkart_reward_pct = flipkart
    return c


def _offer(
    bank: str = "HDFC",
    flat: float = 0.0,
    pct: float = 0.0,
    cap: float = 0.0,
    min_txn: float = 0.0,
    excluded: list[str] | None = None,
    summary: str = "",
) -> BankOfferRule:
    return BankOfferRule(
        bank_name=bank,
        discount_flat=flat,
        discount_pct=pct,
        max_discount_cap=cap,
        min_txn_amount=min_txn,
        excluded_cards=excluded or [],
        summary=summary,
    )


# ── Tests ────────────────────────────────────


class TestBasicFlatDiscount:
    def test_flat_1500_off(self):
        card = _card(name="HDFC Infinia", bank="HDFC")
        offer = _offer(bank="HDFC", flat=1500.0, summary="HDFC ₹1500 off")
        result = calculate_effective_prices(
            listed_price=50000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[offer],
            user_cards=[card],
            retailer="amazon_in",
        )
        assert result.guaranteed_price == 50000.0
        assert result.best_card is not None
        # effective = 50000 - 1500 (bank) - 500 (1% reward on 50000) = 48000
        assert result.best_card.bank_discount == 1500.0
        assert result.best_card.reward_value == 500.0
        assert result.best_card.effective_price == 48000.0


class TestPercentageDiscountWithCap:
    def test_10pct_capped_at_2000(self):
        card = _card(name="ICICI Card", bank="ICICI", base=0.0, amazon=0.0, flipkart=0.0)
        offer = _offer(bank="ICICI", pct=10.0, cap=2000.0)
        result = calculate_effective_prices(
            listed_price=80000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[offer],
            user_cards=[card],
            retailer="croma",
        )
        # 10% of 80000 = 8000, capped at 2000
        assert result.best_card is not None
        assert result.best_card.bank_discount == 2000.0
        assert result.best_card.effective_price == 78000.0

    def test_10pct_uncapped(self):
        card = _card(name="ICICI Card", bank="ICICI", base=0.0, amazon=0.0, flipkart=0.0)
        offer = _offer(bank="ICICI", pct=10.0, cap=0.0)  # no cap
        result = calculate_effective_prices(
            listed_price=15000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[offer],
            user_cards=[card],
            retailer="croma",
        )
        assert result.best_card is not None
        assert result.best_card.bank_discount == 1500.0


class TestMinSpendThresholdNotMet:
    def test_min_txn_not_met(self):
        card = _card(name="SBI Card", bank="SBI", base=0.0, amazon=0.0, flipkart=0.0)
        offer = _offer(bank="SBI", flat=1000.0, min_txn=20000.0)
        result = calculate_effective_prices(
            listed_price=15000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[offer],
            user_cards=[card],
            retailer="amazon_in",
        )
        # 15000 < 20000 min → no discount
        assert result.best_card is not None
        assert result.best_card.bank_discount == 0.0

    def test_min_txn_met(self):
        card = _card(name="SBI Card", bank="SBI", base=0.0, amazon=0.0, flipkart=0.0)
        offer = _offer(bank="SBI", flat=1000.0, min_txn=10000.0)
        result = calculate_effective_prices(
            listed_price=15000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[offer],
            user_cards=[card],
            retailer="amazon_in",
        )
        assert result.best_card is not None
        assert result.best_card.bank_discount == 1000.0


class TestExcludedCard:
    def test_card_excluded(self):
        card = _card(name="HDFC Regalia", bank="HDFC", base=0.0, amazon=0.0, flipkart=0.0)
        offer = _offer(
            bank="HDFC",
            flat=2000.0,
            excluded=["hdfc regalia"],
        )
        result = calculate_effective_prices(
            listed_price=50000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[offer],
            user_cards=[card],
            retailer="amazon_in",
        )
        assert result.best_card is not None
        assert result.best_card.bank_discount == 0.0


class TestCoBrandedCardOnOwnPlatform:
    def test_icici_amazon_pay_on_amazon(self):
        card = _card(
            name="ICICI Amazon Pay Credit Card",
            bank="ICICI",
            base=1.0,
            amazon=5.0,
            flipkart=1.0,
        )
        result = calculate_effective_prices(
            listed_price=20000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[],
            user_cards=[card],
            retailer="amazon_in",
        )
        # 5% of 20000 = 1000 reward
        assert result.best_card is not None
        assert result.best_card.reward_pct == 5.0
        assert result.best_card.reward_value == 1000.0
        assert result.best_card.effective_price == 19000.0

    def test_icici_amazon_pay_on_flipkart(self):
        card = _card(
            name="ICICI Amazon Pay Credit Card",
            bank="ICICI",
            base=1.0,
            amazon=5.0,
            flipkart=1.0,
        )
        result = calculate_effective_prices(
            listed_price=20000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[],
            user_cards=[card],
            retailer="flipkart",
        )
        # Only 1% on Flipkart
        assert result.best_card is not None
        assert result.best_card.reward_pct == 1.0
        assert result.best_card.reward_value == 200.0


class TestNoApplicableOffers:
    def test_no_offers_no_rewards(self):
        card = _card(name="Basic Card", bank="Unknown Bank", base=0.0, amazon=0.0, flipkart=0.0)
        result = calculate_effective_prices(
            listed_price=30000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[],
            user_cards=[card],
            retailer="croma",
        )
        assert result.best_card is not None
        assert result.best_card.bank_discount == 0.0
        assert result.best_card.reward_value == 0.0
        assert result.best_card.effective_price == 30000.0

    def test_empty_card_wallet(self):
        result = calculate_effective_prices(
            listed_price=30000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[],
            user_cards=[],
            retailer="amazon_in",
        )
        assert result.best_card is None
        assert result.ranked_cards == []


class TestMissedSavingsCallout:
    def test_missed_savings_above_1000(self):
        owned_card = _card(name="Basic SBI", bank="SBI", base=0.5, amazon=0.5, flipkart=0.5)
        unowned_card = _card(name="HDFC Infinia", bank="HDFC", base=3.3, amazon=3.3, flipkart=3.3)
        offer = _offer(bank="HDFC", flat=3000.0)

        result = calculate_effective_prices(
            listed_price=50000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[offer],
            user_cards=[owned_card],
            retailer="amazon_in",
            all_cards=[owned_card, unowned_card],
        )
        # Owned: 50000 - 0 bank - 250 (0.5%) = 49750
        # Unowned: 50000 - 3000 bank - 1650 (3.3%) = 45350
        # Savings: 49750 - 45350 = 4400 > 1000 → missed
        assert result.missed_savings is not None
        assert result.missed_savings.card_name == "HDFC Infinia"
        assert result.missed_savings.savings_vs_best_owned == 4400.0

    def test_no_missed_savings_when_gap_small(self):
        owned = _card(name="HDFC Regalia", bank="HDFC", base=1.3, amazon=1.3, flipkart=1.3)
        unowned = _card(name="ICICI Sapphiro", bank="ICICI", base=1.5, amazon=1.5, flipkart=1.5)
        result = calculate_effective_prices(
            listed_price=20000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[],
            user_cards=[owned],
            retailer="amazon_in",
            all_cards=[owned, unowned],
        )
        # Owned: 20000 - 260 = 19740; Unowned: 20000 - 300 = 19700
        # Diff = 40 < 1000
        assert result.missed_savings is None


class TestMultipleOffersBestWins:
    def test_best_offer_selected(self):
        card = _card(name="HDFC Card", bank="HDFC", base=0.0, amazon=0.0, flipkart=0.0)
        offer1 = _offer(bank="HDFC", flat=500.0, summary="Small offer")
        offer2 = _offer(bank="HDFC", flat=2500.0, summary="Big offer")
        offer3 = _offer(bank="ICICI", flat=5000.0, summary="Wrong bank")
        result = calculate_effective_prices(
            listed_price=40000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[offer1, offer2, offer3],
            user_cards=[card],
            retailer="croma",
        )
        # HDFC best = 2500 (offer2 wins), ICICI offer not applicable
        assert result.best_card is not None
        assert result.best_card.bank_discount == 2500.0
        assert result.best_card.offer_summary == "Big offer"


class TestGuaranteedPriceCalculation:
    def test_coupon_and_fee(self):
        card = _card(name="Test", bank="Test", base=0.0, amazon=0.0, flipkart=0.0)
        result = calculate_effective_prices(
            listed_price=50000.0,
            coupon_discount=1000.0,
            platform_fee=99.0,
            bank_offers=[],
            user_cards=[card],
            retailer="amazon_in",
        )
        # 50000 - 1000 + 99 = 49099
        assert result.guaranteed_price == 49099.0
        assert result.best_card is not None
        assert result.best_card.effective_price == 49099.0


class TestMultipleCardsRanking:
    def test_cards_ranked_by_effective_price(self):
        card1 = _card(name="Basic", bank="SBI", base=0.5, amazon=0.5, flipkart=0.5)
        card2 = _card(name="Premium", bank="HDFC", base=2.0, amazon=3.0, flipkart=2.0)
        offer = _offer(bank="HDFC", flat=1500.0)
        result = calculate_effective_prices(
            listed_price=60000.0,
            coupon_discount=0.0,
            platform_fee=0.0,
            bank_offers=[offer],
            user_cards=[card1, card2],
            retailer="amazon_in",
        )
        assert len(result.ranked_cards) == 2
        # Premium HDFC: 60000 - 1500 - 1800 (3%) = 56700
        # Basic SBI:    60000 - 0    - 300 (0.5%) = 59700
        assert result.ranked_cards[0].card_name == "Premium"
        assert result.ranked_cards[0].effective_price == 56700.0
        assert result.ranked_cards[1].card_name == "Basic"
        assert result.ranked_cards[1].effective_price == 59700.0
