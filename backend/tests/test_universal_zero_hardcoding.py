"""
Parameterized Multi-Category Test Suite for Zero-Hardcoding Universal Intelligence (`test_universal_zero_hardcoding.py`).

Tests across 4 completely distinct product categories:
  1. Peripherals / Consumer Electronics (Mechanical Keyboards & Smartphones)
  2. Apparel & Footwear (Running Shoes & Menswear)
  3. Kitchen & Home Appliances (Pressure Cookers)
  4. FMCG / Skincare (Face Serums & Coffee Packs)

Verifies:
  - PART 1: 5-Gate Offer Verification Engine (zero teaser hallucination, Y% up to Z with min_spend,
    EMI-only exclusion from full-swipe checkout cash, channel mismatch, non-item promo rejection).
  - PART 2 & 3: Open-Vocabulary `CanonicalProductIdentity` induction, dynamic `product_type` accessory/part
    rejection, dynamic 3-tier store bucketing (`exact_match`, `lesser_known_store`, `variant_mismatch`),
    auto-generated `variant_diff_badges`, and Winner Guardrail (out-of-stock and variant_mismatch never win).
  - PART 3: Universal 25-domain discovery hygiene (non-store blocklist + unknown store acceptance).
"""
from __future__ import annotations

import pytest

from app.schemas.extraction import CompetitorQuote, LiveOfferRaw
from app.services.card_optimizer import CardLike
from app.services.llm_parser import _deterministic_bank_offers, classify_offer_clause
from app.services.offer_stack_engine import compute_cash_vs_points_report, evaluate_offer_5_gates
from app.services.product_identity import (
    compare_product_identities,
    induce_product_identity_sync,
)
from app.services.product_matcher import classify_store_candidate
from app.services.shopping_discovery import select_diverse_candidates
from app.services.store_ranker import rank_stores_fairly
from app.services.universal_scraper import is_blocked_non_retailer_domain, parse_html_to_payload


def _wallet_card(name: str, bank: str, base: float = 1.0) -> CardLike:
    c = CardLike()
    c.card_name = name
    c.bank_name = bank
    c.base_reward_pct = base
    c.amazon_reward_pct = base
    c.flipkart_reward_pct = base
    return c


# ─────────────────────────────────────────────────────────────────────────────
# PART 1: 5-GATE OFFER VERIFICATION ENGINE (ZERO TEASER HALLUCINATION)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "teaser_text",
    [
        "Upto ₹3,000.00 discount on select Credit Cards (14 offers)",
        "Up to ₹4,500 off on select bank cards",
        "Bank Offer: Upto ₹2,000 off on all banks",
        "12 Bank Offers available",
        "Upto ₹500 cashback on Paytm UPI",
        "Get up to ₹1,500 cashback on RuPay Credit Cards",
        "Up to ₹2,500 off on HDFC Bank Credit Cards",  # Lacks BOTH 'Flat' guarantee AND verified min_spend
        "Additional benefits: Unlock 15% off coupon on next order",
        "Partner Offer: Get GST invoice and save up to 18% on business purchases",
    ],
)
def test_5_gate_zero_teaser_and_cashback_hallucination(teaser_text: str):
    """Any open-ended teaser, cashback banner, or non-item promo MUST yield ₹0 guaranteed checkout discount."""
    hdfc_card = _wallet_card("HDFC Regalia Credit Card", "HDFC", base=1.3)
    axis_card = _wallet_card("Axis Ace Credit Card", "Axis", base=2.0)

    parsed_offers = _deterministic_bank_offers([teaser_text])
    report = compute_cash_vs_points_report(
        listed_price=24990.0,
        raw_coupons=[teaser_text] if "coupon" in teaser_text.lower() else [],
        bank_offers=parsed_offers,
        user_cards=[hdfc_card, axis_card],
        retailer="amazon_in",
        raw_live_offers=[LiveOfferRaw(title="Promo", offer_type="bank", raw_text=teaser_text)],
    )

    assert report.instant_bank_discount_inr == 0.0, f"Teaser '{teaser_text}' must yield ₹0 instant discount"
    assert report.coupon_discount_inr == 0.0
    assert report.checkout_payable_inr == 24990.0


@pytest.mark.parametrize(
    "listed_price,expected_discount,expected_payable",
    [
        (3500.0, 0.0, 3500.0),    # Below min_spend ₹5,000 -> Gate 4 blocks -> ₹0
        (8000.0, 800.0, 7200.0),  # Above min_spend: 10% of 8,000 = ₹800 (< ₹1,500 cap, NEVER ₹1,500 directly!)
        (20000.0, 1500.0, 18500.0),  # Above min_spend: 10% of 20,000 = ₹2,000, capped at ₹1,500
    ],
)
def test_5_gate_percentage_formula_and_min_spend_enforcement(
    listed_price: float,
    expected_discount: float,
    expected_payable: float,
):
    """Gate 4 & Gate 5: 'Y% up to Z on min spend M' yields ₹0 below M and min(round(price * Y%), Z) above M."""
    icici_card = _wallet_card("ICICI Coral Credit Card", "ICICI", base=1.0)
    offer_line = "10% Instant Discount up to ₹1,500 on ICICI Bank Credit Cards on min purchase value ₹5,000"
    parsed = _deterministic_bank_offers([offer_line])
    assert len(parsed) == 1

    report = compute_cash_vs_points_report(
        listed_price=listed_price,
        raw_coupons=[],
        bank_offers=parsed,
        user_cards=[icici_card],
        retailer="flipkart",
    )
    assert report.instant_bank_discount_inr == expected_discount
    assert report.checkout_payable_inr == expected_payable


def test_5_gate_emi_only_and_payment_channel_mismatch():
    """
    Gate 2 & Gate 3:
    - EMI-only offer yields ₹0 on full-swipe checkout cash and is routed to `emi_offers`.
    - Debit-Card-only offer yields ₹0 when user wallet only holds a Credit Card.
    """
    hdfc_credit = _wallet_card("HDFC Millennia Credit Card", "HDFC", base=1.0)
    raw_lines = [
        "Flat ₹3,000 Instant Discount on HDFC Bank Credit Card EMI transactions on min spend ₹15,000",
        "10% Instant Discount up to ₹1,000 on HDFC Bank Debit Cards on orders above ₹5,000",
    ]
    parsed = _deterministic_bank_offers(raw_lines)

    report = compute_cash_vs_points_report(
        listed_price=30000.0,
        raw_coupons=[],
        bank_offers=parsed,
        user_cards=[hdfc_credit],
        retailer="croma",
    )
    # Neither EMI-only nor Debit-only offer may reduce full-swipe Credit Card checkout cash!
    assert report.instant_bank_discount_inr == 0.0
    assert report.checkout_payable_inr == 30000.0
    assert len(report.emi_offers) == 1
    assert report.emi_offers[0]["discount_inr"] == 3000.0
    assert "GST" in report.emi_offers[0]["fee_note"]


# ─────────────────────────────────────────────────────────────────────────────
# PART 2 & 3: MULTI-CATEGORY DYNAMIC PRODUCT-TYPE & VARIANT DIFFING
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "category_label,target_title,target_brand,accessory_title,exact_niche_title,variant_mismatch_title,expected_diff_substring",
    [
        (
            "mechanical_keyboards",
            "Keychron K2 Wireless Mechanical Keyboard (Gateron Brown Switch, White Backlight)",
            "Keychron",
            "Keychron PBT Double-Shot Keycap Set for Keychron K2",
            "Keychron K2 Wireless Mechanical Keyboard (Gateron Brown Switch, White Backlight)",
            "Keychron K2 Wireless Mechanical Keyboard (Gateron Red Switch, White Backlight)",
            "Red",
        ),
        (
            "footwear_apparel",
            "Nike Air Zoom Pegasus 40 Running Shoes (UK 9, Wolf Grey)",
            "Nike",
            "Nike Dri-FIT Running Crew Socks for Pegasus 40",
            "Nike Air Zoom Pegasus 40 Running Shoes (UK 9, Wolf Grey)",
            "Nike Air Zoom Pegasus 40 Running Shoes (UK 10, Wolf Grey)",
            "Uk 10",
        ),
        (
            "kitchen_appliances",
            "Prestige Svachh 5 Litre Hard Anodised Pressure Cooker (Inner Lid)",
            "Prestige",
            "Replacement Rubber Gasket Ring for Prestige Pressure Cooker",
            "Prestige Svachh 5 Litre Hard Anodised Pressure Cooker (Inner Lid)",
            "Prestige Svachh 3 Litre Hard Anodised Pressure Cooker (Outer Lid)",
            "3L",
        ),
        (
            "skincare_fmcg",
            "Minimalist 10% Niacinamide Face Serum (30 ml)",
            "Minimalist",
            "Replacement Glass Dropper Pipette for Minimalist Face Serum",
            "Minimalist 10% Niacinamide Face Serum (30 ml)",
            "Minimalist 10% Niacinamide Face Serum (30 ml, Pack of 2)",
            "Pack Of 2",
        ),
    ],
)
def test_multi_category_open_vocabulary_identity_and_3_tier_bucketing(
    category_label: str,
    target_title: str,
    target_brand: str,
    accessory_title: str,
    exact_niche_title: str,
    variant_mismatch_title: str,
    expected_diff_substring: str,
):
    """
    Across 4 completely different product categories (with zero hardcoded attribute dictionaries):
      1. Accessories/parts with a different `product_type` are strictly REJECTED.
      2. Exact matches on major stores land in `exact_match`; exact matches on niche D2C stores
         or out-of-stock stores land in `lesser_known_store`.
      3. Same-model items with differing dynamic attributes land in `variant_mismatch` with
         auto-generated `variant_diff_badges`.
    """
    target_id = induce_product_identity_sync(target_title, brand=target_brand)
    assert target_id.brand.lower() == target_brand.lower()
    assert "generic" not in target_id.brand.lower()
    assert len(target_id.distinguishing_attributes) >= 1

    # 1. Accessory / Part with different product_type -> REJECTED
    acc_decision = classify_store_candidate(
        target_title,
        accessory_title,
        target_brand=target_brand,
        candidate_price=499.0,
    )
    assert acc_decision.accepted is False, f"[{category_label}] Accessory '{accessory_title}' must be rejected"
    assert acc_decision.bucket == "rejected"

    # 2. Exact match on established retailer -> BUCKET 1: `exact_match`
    exact_decision = classify_store_candidate(
        target_title,
        exact_niche_title,
        target_brand=target_brand,
        candidate_price=4999.0,
        in_stock=True,
        retailer_key="amazon_in",
    )
    assert exact_decision.accepted is True
    assert exact_decision.bucket == "exact_match"
    assert exact_decision.variant_diff_badges == []

    # 3. Exact match on niche independent D2C store or Out-of-Stock -> BUCKET 2: `lesser_known_store`
    niche_decision = classify_store_candidate(
        target_title,
        exact_niche_title,
        target_brand=target_brand,
        candidate_price=4899.0,
        in_stock=True,
        retailer_key="specialty_d2c_store_in",
    )
    assert niche_decision.accepted is True
    assert niche_decision.bucket == "lesser_known_store"

    oos_decision = classify_store_candidate(
        target_title,
        exact_niche_title,
        target_brand=target_brand,
        candidate_price=4599.0,
        in_stock=False,
        retailer_key="flipkart",
    )
    assert oos_decision.accepted is True
    assert oos_decision.bucket == "lesser_known_store"

    # 4. Same core product with differing dynamic attribute -> BUCKET 3: `variant_mismatch` + dynamic badge
    mismatch_decision = classify_store_candidate(
        target_title,
        variant_mismatch_title,
        target_brand=target_brand,
        candidate_price=4799.0,
        in_stock=True,
        retailer_key="croma",
    )
    assert mismatch_decision.accepted is True, f"[{category_label}] Variant mismatch must be kept in bucket 3, not silently dropped"
    assert mismatch_decision.bucket == "variant_mismatch"
    assert len(mismatch_decision.variant_diff_badges) >= 1
    joined_badges = " ".join(mismatch_decision.variant_diff_badges).lower()
    assert expected_diff_substring.lower() in joined_badges, (
        f"[{category_label}] Expected '{expected_diff_substring}' in dynamic badges {mismatch_decision.variant_diff_badges}"
    )


def test_winner_guardrail_never_awards_out_of_stock_or_variant_mismatch():
    """
    Winner Guardrail:
    Even if an `out_of_stock` store or a `variant_mismatch` store has a lower listed price,
    ONLY an `in_stock=True` confirmed-variant store can win `is_overall_winner=True` / 'Lowest Net Price'.
    """
    target_title = "Keychron K2 Wireless Mechanical Keyboard (Gateron Brown Switch, White Backlight)"
    ranked = rank_stores_fairly(
        current_retailer="amazon_in",
        current_listed_price=8499.0,
        current_checkout_cash_price=8499.0,
        current_best_user_card_net_price=8499.0,
        current_url="https://www.amazon.in/dp/B08KEYCHRON",
        current_seller="Keychron India Direct",
        current_peace_of_mind=85,
        target_title=target_title,
        target_brand="Keychron",
        competitors=[
            # Cheaper (₹6,999), but Out of Stock!
            CompetitorQuote(
                retailer="meckeys_in",
                title=target_title,
                listed_price=6999.0,
                url="https://www.meckeys.com/shop/keyboard/keychron-k2-brown/",
                in_stock=False,
            ),
            # Cheaper (₹7,299), in stock, but DIFFERENT switch variant (Red Switch)!
            CompetitorQuote(
                retailer=" flipkart",
                title="Keychron K2 Wireless Mechanical Keyboard (Gateron Red Switch, White Backlight)",
                listed_price=7299.0,
                url="https://www.flipkart.com/keychron-k2-red/p/itm111",
                in_stock=True,
            ),
            # Confirmed exact variant, in stock at ₹7,999 on specialty D2C store -> TRUE WINNER!
            CompetitorQuote(
                retailer="keychron_in",
                title=target_title,
                listed_price=7999.0,
                url="https://keychron.in/products/keychron-k2-wireless-mechanical-keyboard",
                in_stock=True,
            ),
        ],
    )

    by_ret = {r.retailer: r for r in ranked}
    assert by_ret["meckeys_in"].in_stock is False
    assert by_ret["meckeys_in"].store_bucket == "lesser_known_store"
    assert by_ret["meckeys_in"].is_overall_winner is False

    assert by_ret["flipkart"].store_bucket == "variant_mismatch"
    assert any("red" in b.lower() for b in by_ret["flipkart"].variant_diff_badges)
    assert by_ret["flipkart"].is_overall_winner is False

    # True winner must be the ₹7,999 in-stock confirmed-variant store (or current ₹8,499 store), NEVER OOS or mismatch
    winners = [r for r in ranked if r.is_overall_winner]
    assert len(winners) == 1
    assert winners[0].in_stock is True
    assert winners[0].store_bucket in ("exact_match", "lesser_known_store")
    assert winners[0].retailer in ("keychron_in", "amazon_in")


def test_universal_discovery_hygiene_25_domains_and_unknown_transactional_store():
    """
    Universal Discovery Hygiene:
    - Non-store domains (price trackers, review sites, forums) are strictly blocked.
    - Up to 25 unique unknown domains passing the transactional gatekeeper are accepted without whitelist bias.
    """
    hits = [
        {"url": "https://pricehistory.app/p/item", "title": "Tracker", "snippet": "₹1,000"},
        {"url": "https://www.91mobiles.com/item", "title": "Specs", "snippet": "₹1,000"},
        {"url": "https://www.reddit.com/r/india/item", "title": "Forum", "snippet": "₹1,000"},
    ]
    for i in range(28):
        hits.append(
            {
                "url": f"https://indie-store-{i}.co.in/products/prestige-svachh-5l",
                "title": "Prestige Svachh 5 Litre Pressure Cooker",
                "snippet": "₹2,499 Add to Cart",
            }
        )

    selected = select_diverse_candidates(hits, current_domain="amazon.in", brand="Prestige", max_stores=25)
    assert len(selected) == 25
    assert all(not is_blocked_non_retailer_domain(c["url"]) for c in selected)

    # Unknown custom independent store with valid transactional signals is extracted cleanly
    custom_html = """
    <html><head>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"Product",
       "name":"Prestige Svachh 5 Litre Hard Anodised Pressure Cooker (Inner Lid)",
       "brand":{"@type":"Brand","name":"Prestige"},
       "offers":{"@type":"Offer","price":"2399","priceCurrency":"INR","availability":"https://schema.org/InStock"}}
      </script>
    </head><body><button>Add to Cart</button></body></html>
    """
    payload = parse_html_to_payload(
        custom_html,
        "https://kitchenware-boutique-pune.in/product/prestige-svachh-5l",
        require_transactional_signals=True,
    )
    assert payload is not None
    assert payload.brand == "Prestige"
    assert payload.listed_price == 2399.0
    assert payload.in_stock is True
    assert payload.is_transactional_store is True
