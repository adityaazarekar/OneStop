"""
Comprehensive tests for OneStop accuracy invariants:
1. Strict Cash-vs-Points separation (points NEVER deducted from checkout_payable_inr)
2. Exact conversion ratios (3.33 pts = ₹1 cash vs 1 pt = ₹1 travel) with verified rules
3. Enforced monthly caps & voucher processing fees
4. Zero hardcoded ESTIMATE fallbacks or ₹250 UPI placeholders (Strict Honesty Rule)
5. Unowned cards strictly excluded from primary user price (placed in unowned_card_routes)
6. Non-retailer / aggregator / coupon / review / forum blocklist (pricehistory.app, etc.)
   + transactional checkout verification + current-store deduplication
7. Multi-bank live on-page bank offer parsing & wallet matching
8. DB persistence round-trip: POST /api/v1/analyze-client-payload -> GET /api/v1/products/{id}
9. "Become a Seller" rejection, "Generic LG" brand fix, ₹0 filtering, RapidFuzz, RateGuard
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.schemas.extraction import (
    BankOfferRule,
    ClientPayload,
    CompetitorQuote,
    ListingSnapshot,
    LiveOfferRaw,
    VerifiedPaymentRule,
)
from app.services.card_optimizer import CardLike
from app.services.llm_parser import _deterministic_bank_offers, _deterministic_variant
from app.services.offer_stack_engine import compute_cash_vs_points_report
from app.services.product_matcher import (
    extract_clean_brand,
    is_generic_or_invalid_listing,
    sanitize_seller_name,
    score_candidate_match,
)
from app.services.rate_guard import (
    BROWSER_SEMAPHORE,
    CircuitOpenError,
    RateLimitOrBlockedError,
    guarded_domain_call,
    is_circuit_open,
)
from app.services.store_ranker import rank_stores_fairly
from app.services.universal_scraper import (
    has_transactional_checkout_signals,
    is_blocked_non_retailer_domain,
    parse_html_to_payload,
)


def _card(name: str, bank: str, base: float = 1.0, amz: float = 1.0, fk: float = 1.0) -> CardLike:
    c = CardLike()
    c.card_name = name
    c.bank_name = bank
    c.base_reward_pct = base
    c.amazon_reward_pct = amz
    c.flipkart_reward_pct = fk
    return c


def _verified_infinia_rule() -> VerifiedPaymentRule:
    return VerifiedPaymentRule(
        method_name="HDFC Infinia",
        method_type="credit_card",
        reward_type="points",
        points_per_spend_unit=5.0,
        spend_unit_inr=150.0,
        point_value_cash_inr=0.30,
        point_value_travel_inr=1.00,
        monthly_cap_inr=15000.0,
        exclusions=["Statement cash ₹0.30/pt vs ₹1.00/pt SmartBuy travel"],
        verification_status="live_tc_verified",
        source_urls=["https://www.hdfcbank.com/personal/pay/cards/credit-cards/infinia-credit-card"],
        last_verified_at=datetime.now(timezone.utc),
        plain_english_summary="5 RPs per ₹150 spend (₹0.30/pt cash, ₹1.00/pt travel).",
    )


def _verified_sbi_cb_rule() -> VerifiedPaymentRule:
    return VerifiedPaymentRule(
        method_name="SBI Cashback",
        method_type="credit_card",
        reward_type="cashback",
        points_per_spend_unit=5.0,
        spend_unit_inr=100.0,
        point_value_cash_inr=1.00,
        point_value_travel_inr=1.00,
        monthly_cap_inr=5000.0,
        exclusions=["5% online cashback capped at ₹5,000 per billing cycle"],
        verification_status="live_tc_verified",
        source_urls=["https://www.sbicard.com/en/personal/credit-cards/rewards/cashback-sbi-card.page"],
        last_verified_at=datetime.now(timezone.utc),
        plain_english_summary="5% direct cashback capped at ₹5,000/month.",
    )


def _verified_gyftr_rule() -> VerifiedPaymentRule:
    return VerifiedPaymentRule(
        method_name="HDFC SmartBuy GyFTR Amazon Voucher (Infinia 5X)",
        method_type="gift_voucher",
        reward_type="points",
        points_per_spend_unit=25.0,
        spend_unit_inr=150.0,
        point_value_cash_inr=0.30,
        point_value_travel_inr=1.00,
        monthly_cap_inr=10000.0,
        processing_fee_pct=4.13,
        exclusions=["₹10,000/month voucher cap; 4.13% incl GST processing fee"],
        verification_status="live_tc_verified",
        source_urls=["https://www.gyftr.com/instantvouchers/"],
        last_verified_at=datetime.now(timezone.utc),
        plain_english_summary="5X SmartBuy points on GyFTR voucher subject to ₹10,000 cap & 4.13% fee.",
    )


def test_cash_vs_points_never_subtracts_points_from_checkout_payable():
    """When no verified instant bank discount exists, checkout_payable_inr == listed + fees."""
    infinia = _card("HDFC Infinia", "HDFC", base=3.3, amz=3.3, fk=3.3)
    sbi_cb = _card("SBI Cashback", "SBI", base=5.0, amz=5.0, fk=5.0)

    report = compute_cash_vs_points_report(
        listed_price=60000.0,
        raw_coupons=[],
        bank_offers=[],  # NO live instant bank discount on page
        user_cards=[infinia, sbi_cb],
        retailer="amazon_in",
        verified_rules=[_verified_infinia_rule(), _verified_sbi_cb_rule()],
        delivery_fee=0.0,
        handling_fee=99.0,
    )

    assert report.instant_bank_discount_inr == 0.0
    assert report.mandatory_fees_inr == 99.0
    assert report.checkout_payable_inr == 60099.0

    pts_route = next(r for r in report.routes if r.route_type == "direct_points_card")
    cb_route = next(r for r in report.routes if r.route_type == "direct_cashback_card")

    assert pts_route.checkout_payable_inr == 60099.0
    assert cb_route.checkout_payable_inr == 60099.0
    # Infinia: floor(60099/150) * 5 = 400 * 5 = 2000 pts -> ₹600 cash (@0.30/pt) vs ₹2000 travel (@1.00/pt)
    assert pts_route.rewards.points_earned == 2000.0
    assert "3.33 pts = ₹1 statement cash" in pts_route.rewards.conversion_ratio_cash
    assert "1 pt = ₹1.00 travel" in pts_route.rewards.conversion_ratio_travel
    assert pts_route.rewards.cash_value_inr == 600.0
    assert pts_route.rewards.travel_value_inr == 2000.0
    assert pts_route.effective_cost_if_cash_inr == 59499.0
    assert pts_route.effective_cost_if_optimal_redemption_inr == 58099.0
    assert cb_route.rewards.cash_value_inr == pytest.approx(3004.95, abs=0.05)
    assert cb_route.effective_cost_if_cash_inr < pts_route.effective_cost_if_cash_inr
    assert report.efficiency_verdict


def test_monthly_caps_and_voucher_processing_fee_enforced():
    """SBI Cashback ₹5,000 monthly cap and SmartBuy GyFTR ₹10,000 cap + 4.13% fee are enforced."""
    infinia = _card("HDFC Infinia", "HDFC", base=3.3, amz=3.3, fk=3.3)
    sbi_cb = _card("SBI Cashback", "SBI", base=5.0, amz=5.0, fk=5.0)

    report = compute_cash_vs_points_report(
        listed_price=150000.0,
        raw_coupons=["Apply ₹1,000 coupon"],
        bank_offers=[],
        user_cards=[infinia, sbi_cb],
        retailer="amazon_in",
        verified_rules=[_verified_infinia_rule(), _verified_sbi_cb_rule(), _verified_gyftr_rule()],
    )

    assert report.checkout_payable_inr == 149000.0

    cb_route = next(r for r in report.routes if r.route_type == "direct_cashback_card")
    assert cb_route.rewards.cap_applied is True
    assert cb_route.rewards.monthly_cap_inr == 5000.0
    assert cb_route.rewards.cash_value_inr == 5000.0
    assert cb_route.effective_cost_if_cash_inr == 144000.0

    voucher_route = next(r for r in report.routes if r.route_type == "gift_voucher_stack")
    assert voucher_route.rewards.cap_applied is True
    assert voucher_route.rewards.processing_fee_inr == pytest.approx(413.0, abs=1.0)
    assert "Cap warning" in report.efficiency_verdict or "fee" in report.efficiency_verdict.lower()


def test_zero_hardcoded_upi_250_or_synthetic_estimates():
    """Strict Honesty Rule: Without verified rules or live offers, ZERO ₹250 UPI or synthetic discounts exist."""
    infinia = _card("HDFC Infinia", "HDFC", base=3.3, amz=3.3, fk=3.3)
    report = compute_cash_vs_points_report(
        listed_price=50000.0,
        raw_coupons=[],
        bank_offers=[],
        user_cards=[infinia],
        retailer="amazon_in",
        verified_rules=[],  # No crawled live T&Cs
        raw_live_offers=[],  # No live page UPI offers
    )

    assert report.checkout_payable_inr == 50000.0
    assert report.best_user_card_net_price_cash == 50000.0

    upi_route = next(r for r in report.routes if r.route_type == "upi_app_offer")
    assert upi_route.available is False
    assert upi_route.verification_status == "no_verified_offer"
    assert upi_route.rewards.cash_value_inr == 0.0
    assert upi_route.rewards.monthly_cap_inr is None
    assert "No active verified offer found" in upi_route.summary

    voucher_route = next(r for r in report.routes if r.route_type == "gift_voucher_stack")
    assert voucher_route.available is False
    assert voucher_route.verification_status == "no_verified_offer"
    assert voucher_route.rewards.cash_value_inr == 0.0
    assert "No active verified offer found" in voucher_route.summary

    pts_route = next(r for r in report.routes if r.route_type == "direct_points_card")
    assert pts_route.verification_status == "no_verified_offer"
    assert pts_route.rewards.cash_value_inr == 0.0
    assert pts_route.effective_cost_if_cash_inr == 50000.0


def test_unowned_cards_excluded_from_primary_user_price():
    """Unowned cards with big bank discounts NEVER distort the user's primary payable/net price."""
    owned_hdfc = _card("HDFC Millennia", "HDFC", base=1.0, amz=5.0, fk=5.0)
    unowned_icici = _card("ICICI Sapphiro", "ICICI", base=0.5, amz=0.5, fk=0.5)

    icici_offer = BankOfferRule(
        bank_name="ICICI",
        discount_flat=5000.0,
        min_txn_amount=40000.0,
        summary="Flat ₹5,000 Instant Discount on ICICI Credit Cards",
    )

    report = compute_cash_vs_points_report(
        listed_price=76990.0,
        raw_coupons=[],
        bank_offers=[icici_offer],
        user_cards=[owned_hdfc],  # User ONLY owns HDFC Millennia
        retailer="amazon_in",
        verified_rules=[],
        unowned_cards=[unowned_icici],
    )

    # Primary price MUST stay ₹76,990 (not ₹71,990 from unowned ICICI card!)
    assert report.instant_bank_discount_inr == 0.0
    assert report.checkout_payable_inr == 76990.0
    assert report.best_user_card_net_price_cash == 76990.0
    assert report.winning_owned_card_name == "HDFC Millennia"

    # Unowned ICICI card appears separately in unowned_card_routes
    assert len(report.unowned_card_routes) == 1
    assert report.unowned_card_routes[0].method_name == "ICICI Sapphiro"
    assert report.unowned_card_routes[0].is_owned_card is False
    assert report.unowned_card_routes[0].checkout_payable_inr == 71990.0


def test_verified_instant_bank_discount_reduces_checkout_payable():
    """Verified on-page instant bank discount matching user's card DOES reduce checkout_payable_inr."""
    sapphiro = _card("ICICI Sapphiro", "ICICI", base=0.5, amz=0.5, fk=0.5)
    raw_lines = [
        "Flat INR 4000 Instant Discount on ICICI Bank, SBI & Kotak Credit Card Non-EMI Txn. Min purchase value INR 60000",
        "10% Instant Discount up to ₹1,500 on HDFC Bank Debit Cards on orders above ₹5,000",
    ]
    parsed = _deterministic_bank_offers(raw_lines)
    banks = {o.bank_name for o in parsed}
    assert {"ICICI", "SBI", "KOTAK", "HDFC"}.issubset(banks)
    hdfc_rule = next(o for o in parsed if o.bank_name == "HDFC")
    assert hdfc_rule.discount_pct == 10.0
    assert hdfc_rule.discount_flat == 0.0  # Must NOT treat ₹1,500 cap or ₹5,000 min spend as flat!
    assert hdfc_rule.max_discount_cap == 1500.0
    assert hdfc_rule.min_txn_amount == 5000.0
    assert hdfc_rule.card_type == "debit"

    report = compute_cash_vs_points_report(
        listed_price=76990.0,
        raw_coupons=[],
        bank_offers=parsed,
        user_cards=[sapphiro],
        retailer="amazon_in",
    )
    assert report.instant_bank_discount_inr == 4000.0
    assert report.checkout_payable_inr == 72990.0


def test_block_non_retailer_aggregators_and_deduplicate_current_store():
    """pricehistory.app, coupon/review portals are blocked; non-transactional pages rejected; current store deduplicated."""
    blocked_urls = [
        "https://pricehistory.app/p/apple-iphone-16-128gb",
        "https://www.pricebefore.com/apple-iphone-16-price-in-india.html",
        "https://buyhatke.com/iphone-16",
        "https://keepa.com/#!product/10-B0DGJHBX5Y",
        "https://camelcamelcamel.com/product/B0DGJHBX5Y",
        "https://www.mysmartprice.com/mobile/apple-iphone-16",
        "https://www.91mobiles.com/apple-iphone-16-price-in-india",
        "https://www.smartprix.com/mobiles/apple-iphone-16",
        "https://www.gadgets360.com/apple-iphone-16-price-in-india",
        "https://cashkaro.com/stores/amazon-coupons",
        "https://www.couponzguru.com/amazon/",
        "https://www.grabon.in/amazon-coupons/",
        "https://www.paisawapas.com/amazon-offers",
        "https://www.desidime.com/deals/iphone-16",
        "https://www.reddit.com/r/IndiaTech/comments/iphone16",
        "https://www.youtube.com/watch?v=abc123",
        "https://www.quora.com/Should-I-buy-iPhone-16",
        "https://www.gsmarena.com/apple_iphone_16-13317.php",
        "https://versus.com/en/apple-iphone-16",
        "https://en.wikipedia.org/wiki/IPhone_16",
    ]
    for u in blocked_urls:
        assert is_blocked_non_retailer_domain(u) is True, f"Failed to block {u}"

    assert is_blocked_non_retailer_domain("https://www.croma.com/apple-iphone-16/p/309731") is False
    assert is_blocked_non_retailer_domain("https://www.amazon.in/dp/B0DGJHBX5Y") is False

    # Non-transactional review blog with price text but NO Add-to-Cart / Buy-Now / Offer+availability MUST be rejected
    review_html = "<html><body><h1>Apple iPhone 16 Review</h1><p>Price in India starts at ₹76,990.</p></body></html>"
    assert has_transactional_checkout_signals(review_html, {}) is False
    assert parse_html_to_payload(review_html, "https://techblog.example.in/iphone-16", require_transactional_signals=True) is None

    # Store ranking blocks aggregator quotes AND deduplicates active tab's store with Current Page badge
    ranked = rank_stores_fairly(
        current_retailer="amazon_in",
        current_listed_price=76990.0,
        current_checkout_cash_price=72990.0,
        current_best_user_card_net_price=72990.0,
        current_url="https://www.amazon.in/dp/B0DGJHBX5Y",
        current_seller="Appario Retail",
        current_peace_of_mind=85,
        competitors=[
            CompetitorQuote(retailer="pricehistory_app", title="iPhone 16", listed_price=69990.0, url="https://pricehistory.app/p/iphone-16"),
            CompetitorQuote(retailer="cashkaro", title="iPhone 16", listed_price=70990.0, url="https://cashkaro.com/iphone-16"),
            CompetitorQuote(retailer="amazon_in", title="iPhone 16 dup", listed_price=79990.0, url="https://www.amazon.in/dp/B0DGJHBX5Y"),
            CompetitorQuote(retailer="croma", title="Apple iPhone 16 128GB", listed_price=74900.0, url="https://www.croma.com/p/309731"),
        ],
        existing_listings=[
            ListingSnapshot(retailer="amazon_in", listing_url="https://www.amazon.in/dp/OLD", listed_price=79900.0),
            ListingSnapshot(retailer="91mobiles", listing_url="https://www.91mobiles.com/iphone-16", listed_price=71000.0),
        ],
    )
    retailers = [r.retailer for r in ranked]
    assert retailers.count("amazon_in") == 1
    assert "pricehistory_app" not in retailers
    assert "cashkaro" not in retailers
    assert "91mobiles" not in retailers
    assert "croma" in retailers

    amz_row = next(r for r in ranked if r.retailer == "amazon_in")
    assert amz_row.is_current_store is True
    assert "Current Page" in amz_row.rank_badge
    assert amz_row.checkout_cash_price == 72990.0
    assert amz_row.best_user_card_net_price == 72990.0


@pytest.mark.asyncio
async def test_analyze_client_payload_persists_retrievable_canonical_product_id():
    """POST /api/v1/analyze-client-payload commits canonical_product_id retrievable via GET /api/v1/products/{id}."""
    from fastapi.testclient import TestClient
    from app.main import app

    unique_sku = f"TESTSKU{uuid.uuid4().hex[:8].upper()}"
    body = {
        "payload": {
            "retailer": "amazon_in",
            "url": f"https://www.amazon.in/dp/{unique_sku}",
            "sku": unique_sku,
            "title": f"Apple iPhone 16 128GB Persistence Test {unique_sku}",
            "brand": "Apple",
            "listed_price": 76990.0,
            "mrp": 79900.0,
            "in_stock": True,
            "seller_name": "Appario Retail Private Ltd",
            "raw_bank_offers": ["Flat ₹4,000 Instant Discount on ICICI Bank Credit Cards. Min spend ₹50,000"],
        },
        "competitor_quotes": [],
        "user_card_ids": [],
    }

    with TestClient(app) as client:
        post_resp = client.post("/api/v1/analyze-client-payload", json=body)
        assert post_resp.status_code == 200, post_resp.text
        data = post_resp.json()
        cid = data["canonical_product_id"]
        uuid.UUID(cid)  # Must be valid UUID

        # Immediately retrieve via GET /api/v1/products/{cid}
        get_resp = client.get(f"/api/v1/products/{cid}")
        assert get_resp.status_code == 200, get_resp.text
        prod = get_resp.json()
        assert prod["id"] == cid
        assert prod["brand"] == "Apple"
        assert len(prod["listings"]) >= 1
        assert prod["cash_vs_points"] is not None

        # Invalid UUID returns clear 404 detail
        bad_resp = client.get("/api/v1/products/not-a-valid-uuid")
        assert bad_resp.status_code == 404
        assert "not a valid canonical product UUID" in bad_resp.json()["detail"]


def test_become_a_seller_rejection_and_d2c_default():
    assert sanitize_seller_name("Become a Seller", "https://www.flipkart.com/p/itm123", "flipkart") is None
    assert sanitize_seller_name("Sell on Amazon", "https://www.amazon.in/dp/B000000001", "amazon_in") is None
    assert sanitize_seller_name("Login", "https://www.flipkart.com/p/itm123", "flipkart") is None
    assert sanitize_seller_name("More", "https://www.flipkart.com/p/itm123", "flipkart") is None
    assert sanitize_seller_name("Appario Retail Private Ltd", "https://www.amazon.in/dp/B000000001", "amazon_in") == "Appario Retail Private Ltd"
    assert sanitize_seller_name("Become a Seller", "https://www.lg.com/in/tvs/oled55", "lg_official") == "Official Direct Store"
    assert sanitize_seller_name(None, "https://www.nike.com/in/t/air-force-1", "nike_official") == "Official Direct Store"


def test_generic_lg_brand_extraction_fix():
    assert extract_clean_brand("Generic", "LG 139 cm (55 inches) 4K Ultra HD Smart OLED TV OLED55C46LA") == "LG"
    assert extract_clean_brand(None, "Samsung Galaxy S24 Ultra 5G (256GB)") == "Samsung"
    assert extract_clean_brand("Visit the Apple Store", "Apple iPhone 16 128 GB") == "Apple"
    assert extract_clean_brand("Generic", "Sony WH-1000XM5 Wireless Noise Cancelling Headphones") == "Sony"
    assert extract_clean_brand(None, "Nike Air Jordan 1 Low Sneakers") == "Nike"

    var = _deterministic_variant("LG 1.5 Ton 5 Star Dual Inverter Split AC")
    assert var.brand == "LG"
    assert "Generic" not in var.brand


def test_zero_price_and_generic_page_filtering():
    bad_zero, _ = is_generic_or_invalid_listing("Apple iPhone 16 128GB", 0.0, "https://www.amazon.in/dp/B0DGJHBX5Y")
    assert bad_zero is True

    bad_none, _ = is_generic_or_invalid_listing("Apple iPhone 16 128GB", None, "https://www.amazon.in/dp/B0DGJHBX5Y")
    assert bad_none is True

    bad_cat, _ = is_generic_or_invalid_listing("All Categories - Buy Products Online", 500.0, "https://www.flipkart.com/all")
    assert bad_cat is True

    bad_search, _ = is_generic_or_invalid_listing("iPhone 16", 76990.0, "https://www.amazon.in/s?k=iphone+16")
    assert bad_search is True

    ok, _ = is_generic_or_invalid_listing("LG 55 inch 4K OLED TV", 119990.0, "https://www.croma.com/lg-oled/p/305123")
    assert ok is False


def test_rapidfuzz_variant_and_accessory_filtering():
    target = "Apple iPhone 16 128GB Black"

    ok_case, _, _ = score_candidate_match(target, "Spigen Back Cover Case for Apple iPhone 16 128GB Black", target_brand="Apple")
    assert ok_case is False

    ok_256, _, _ = score_candidate_match(target, "Apple iPhone 16 256GB Black", target_brand="Apple")
    assert ok_256 is False

    ok_pro, _, _ = score_candidate_match(target, "Apple iPhone 16 Pro 128GB Black", target_brand="Apple")
    assert ok_pro is False

    ok_match, score, _ = score_candidate_match(target, "Apple iPhone 16 (Black, 128 GB)", target_brand="Apple")
    assert ok_match is True
    assert score >= 68.0

    ok_tv, _, _ = score_candidate_match("LG 55 inch 4K OLED TV C4", "LG 65 inch 4K OLED TV C4", target_brand="LG")
    assert ok_tv is False


def test_universal_scraper_extruct_and_fee_parsing():
    html = """
    <html><head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "LG 55 inch 4K Smart OLED TV",
        "brand": {"@type": "Brand", "name": "LG"},
        "sku": "OLED55C4",
        "offers": {
          "@type": "Offer",
          "price": "1,19,990",
          "priceCurrency": "INR",
          "availability": "https://schema.org/InStock",
          "seller": {"@type": "Organization", "name": "Become a Seller"}
        }
      }
      </script>
    </head>
    <body>
      <nav><a href="/sell">Become a Seller</a></nav>
      <div id="sellerName"><span><span>SuperComNet</span></span></div>
      <button>Buy Now</button>
      <div>Secured Packaging Fee ₹199</div>
      <ul><li>Flat ₹5,000 instant discount on HDFC Bank Credit Cards</li></ul>
    </body></html>
    """
    payload = parse_html_to_payload(html, "https://www.flipkart.com/lg-oled/p/itm12345", require_transactional_signals=True)
    assert payload is not None
    assert payload.brand == "LG"
    assert payload.listed_price == 119990.0
    assert payload.seller_name != "Become a Seller"
    assert payload.handling_fee == 199.0
    assert any(o.offer_type == "fee" for o in payload.raw_live_offers)


@pytest.mark.asyncio
async def test_rate_guard_semaphore_and_circuit_breaker():
    assert BROWSER_SEMAPHORE._value == 2
    domain = f"blocked-{uuid.uuid4().hex[:8]}.example"
    attempts = 0

    async def _always_403():
        nonlocal attempts
        attempts += 1
        raise RateLimitOrBlockedError(domain, status_code=403, reason="HTTP 403")

    with pytest.raises(RateLimitOrBlockedError):
        await guarded_domain_call(f"https://{domain}/p/1", _always_403, max_attempts=3)

    assert attempts == 1
    is_open, _ = await is_circuit_open(domain)
    assert is_open is True

    with pytest.raises(CircuitOpenError):
        await guarded_domain_call(f"https://{domain}/p/2", _always_403, max_attempts=2)


def test_flipkart_cross_sell_air_fryer_coupon_rejected_and_advertised_lowest_sanity():
    """Flipkart 'Unlock Air fryer coupon • 10% off' MUST be rejected (₹0 coupon on ₹89,100 iPhone 17)."""
    infinia = _card("HDFC Infinia", "HDFC", base=3.3, amz=3.3, fk=3.3)

    report = compute_cash_vs_points_report(
        listed_price=89100.0,
        raw_coupons=[
            "Additional benefits on this purchase -> Unlock Air fryer coupon • 10% off",
            "Unlock 10% off on next purchase",
            "Special Price Get extra ₹4,000 off (price inclusive of cashback/coupon)",
        ],
        bank_offers=[],
        user_cards=[infinia],
        retailer="flipkart",
        retailer_advertised_lowest_price=85100.0,
    )
    assert report.coupon_discount_inr == 0.0
    assert report.checkout_payable_inr == 89100.0

    # Advertised lowest-price sanity check: bogus 10% non-checkbox coupon dropping below ₹85,100 is rejected
    report_sanity = compute_cash_vs_points_report(
        listed_price=89100.0,
        raw_coupons=["10% off promo"],
        bank_offers=[],
        user_cards=[infinia],
        retailer="flipkart",
        retailer_advertised_lowest_price=85100.0,
    )
    assert report_sanity.coupon_discount_inr == 0.0
    assert report_sanity.checkout_payable_inr == 89100.0


def test_flipkart_multinode_hdfc_891_and_protect_promise_fee_299_exact_88508():
    """Multi-node '₹891 off HDFC Credit Card' + '+₹299 Protect Promise Fee' -> exact checkout ₹88,508."""
    infinia = _card("HDFC Infinia", "HDFC", base=3.3, amz=3.3, fk=3.3)
    raw_offers = [
        "₹891 off HDFC Credit Card",
        "₹1,101 off Google Pay UPI",
        "₹4,000 off Flipkart Axis Bank Credit Card",
    ]
    parsed_bank = _deterministic_bank_offers(raw_offers)
    # Google Pay UPI must NOT become an ALL BANKS credit card rule
    assert all(o.bank_name != "ALL BANKS" for o in parsed_bank)
    hdfc_rule = next(o for o in parsed_bank if o.bank_name == "HDFC")
    assert hdfc_rule.discount_flat == 891.0

    # Verify +₹299 Protect Promise Fee (amount-first) is extracted by universal_scraper too
    fk_html = """
    <html><head>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"Product","name":"Apple iPhone 17 (256 GB)",
       "brand":{"@type":"Brand","name":"Apple"},
       "offers":{"@type":"Offer","price":"89100","availability":"https://schema.org/InStock"}}
      </script>
    </head><body>
      <div>+ ₹299 Protect Promise Fee</div>
      <button>Buy Now</button>
    </body></html>
    """
    scraped = parse_html_to_payload(fk_html, "https://www.flipkart.com/apple-iphone-17/p/itm999", require_transactional_signals=True)
    assert scraped is not None
    assert scraped.handling_fee == 299.0

    report = compute_cash_vs_points_report(
        listed_price=89100.0,
        raw_coupons=["Additional benefits on this purchase -> Unlock Air fryer coupon • 10% off"],
        bank_offers=parsed_bank,
        user_cards=[infinia],
        retailer="flipkart",
        raw_live_offers=[
            LiveOfferRaw(title="Google Pay UPI", offer_type="upi", raw_text="₹1,101 off Google Pay UPI"),
            LiveOfferRaw(title="Protect Promise Fee", offer_type="fee", raw_text="+ ₹299 Protect Promise Fee"),
        ],
        handling_fee=scraped.handling_fee,
        retailer_advertised_lowest_price=85100.0,
    )
    assert report.coupon_discount_inr == 0.0
    assert report.instant_bank_discount_inr == 891.0
    assert report.mandatory_fees_inr == 299.0
    # 89,100 - 891 + 299 = 88,508
    assert report.checkout_payable_inr == 88508.0

    upi_route = next(r for r in report.routes if r.route_type == "upi_app_offer")
    assert upi_route.available is True
    assert upi_route.verification_status == "page_verified"
    assert upi_route.instant_discount_inr == 1101.0
    assert upi_route.checkout_payable_inr == 88298.0  # 89,100 - 1,101 + 299


def test_discovery_blocks_aggregators_and_accepts_shopify_json_and_generic_d2c():
    """4-angle discovery rejects aggregators & keeps diverse domains; Shopify .json + Schema.org D2C accepted."""
    from app.services.shopping_discovery import build_transactional_searxng_queries, select_diverse_candidates
    from app.services.universal_scraper import build_shopify_json_url, parse_shopify_product_json

    queries = build_transactional_searxng_queries("Apple iPhone 17 256GB")
    assert len(queries) == 3
    q_texts = " ".join(q for _, q in queries)
    assert "Add to Cart" in q_texts
    assert "inurl:/products/" in q_texts
    assert "authorized reseller" in q_texts

    hits = [
        {"url": "https://pricehistory.app/p/iphone-17", "title": "iPhone 17 Price History", "snippet": "₹89,100"},
        {"url": "https://www.91mobiles.com/apple-iphone-17", "title": "iPhone 17 Specs", "snippet": "₹89,100"},
        {"url": "https://www.flipkart.com/apple-iphone-17/p/itm1", "title": "iPhone 17", "snippet": "₹89,100"},
        {"url": "https://www.apple.com/in/shop/buy-iphone/iphone-17", "title": "Buy iPhone 17", "snippet": "₹89,900 official store"},
        {"url": "https://imagineonline.store/products/iphone-17-256gb", "title": "Apple iPhone 17 256GB", "snippet": "₹89,900 Add to Cart"},
        {"url": "https://regionalshop.in/products/apple-iphone-17-256gb", "title": "Apple iPhone 17 256GB", "snippet": "₹88,499 Buy Now"},
    ]
    selected = select_diverse_candidates(hits, current_domain="flipkart.com", brand="Apple", max_stores=12)
    selected_urls = [c["url"] for c in selected]
    assert not any("pricehistory.app" in u or "91mobiles.com" in u or "flipkart.com" in u for u in selected_urls)
    assert any("apple.com" in u for u in selected_urls)
    assert any("imagineonline.store" in u for u in selected_urls)
    assert any("regionalshop.in" in u for u in selected_urls)

    # Tier 0: Shopify .json fast-path accepted
    s_url = "https://regionalshop.in/products/apple-iphone-17-256gb?variant=123"
    assert build_shopify_json_url(s_url) == "https://regionalshop.in/products/apple-iphone-17-256gb.json"
    shopify_payload = parse_shopify_product_json(
        {
            "product": {
                "id": 9901,
                "title": "Apple iPhone 17",
                "vendor": "Apple",
                "variants": [
                    {"id": 123, "title": "256GB Black", "price": "88499.00", "compare_at_price": "89900.00", "available": True, "sku": "IP17-256-BLK"}
                ],
            }
        },
        s_url,
    )
    assert shopify_payload is not None
    assert shopify_payload.listed_price == 88499.0
    assert shopify_payload.mrp == 89900.0
    assert shopify_payload.brand == "Apple"
    assert shopify_payload.in_stock is True
    assert shopify_payload.variant_specs["extraction_tier"] == "tier0_shopify_json"

    # Generic Schema.org D2C store accepted, non-checkout blog rejected
    d2c_html = """
    <html><head>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"Product","name":"Apple iPhone 17 256GB",
       "brand":{"@type":"Brand","name":"Apple"},
       "offers":{"@type":"Offer","price":"88900","availability":"https://schema.org/InStock"}}
      </script>
    </head><body><form action="/cart/add"><button>Add to Cart</button></form></body></html>
    """
    d2c_payload = parse_html_to_payload(d2c_html, "https://smallreseller.in/product/iphone-17-256gb", require_transactional_signals=True)
    assert d2c_payload is not None
    assert d2c_payload.listed_price == 88900.0
    assert d2c_payload.seller_name == "Official Direct Store"

    blog_html = "<html><body><h1>Apple iPhone 17 Review</h1><p>Expected price ₹89,100 in India.</p></body></html>"
    assert parse_html_to_payload(blog_html, "https://gadgetblog.in/iphone-17-review", require_transactional_signals=True) is None


def test_live_terms_engine_fit_markdown_fallback_to_raw_markdown():
    """live_terms_engine falls back cleanly from empty/<200-char fit_markdown to high-signal raw_markdown."""
    from types import SimpleNamespace
    from app.services.live_terms_engine import _deterministic_tc_parse, build_issuer_tc_query, extract_markdown_with_fallback

    assert "MITC" in build_issuer_tc_query("SBI Cashback")
    assert "SmartBuy" in build_issuer_tc_query("HDFC Infinia")

    raw_md = "\n".join(
        [
            "Home | Personal Banking | Login | Contact Us",
            "Navigation Menu Item 1 | Navigation Menu Item 2",
            "## SBI Cashback Credit Card MITC",
            "Earn 5% cashback on online spends across merchants without merchant restriction.",
            "Maximum cashback is capped at ₹5,000 per statement cycle.",
            "Exclusions: Utility bills, rent, fuel, and wallet loads are excluded from cashback.",
            "Footer Copyright © SBI Cards",
        ]
    )
    # Empty fit_markdown -> falls back to high-signal sliced raw_markdown
    md_empty_fit = SimpleNamespace(fit_markdown="", raw_markdown=raw_md)
    extracted = extract_markdown_with_fallback(md_empty_fit)
    assert "5% cashback on online spends" in extracted
    assert "₹5,000 per statement cycle" in extracted

    # Short (<200 char) fit_markdown -> also falls back to raw_markdown
    md_short_fit = SimpleNamespace(fit_markdown="SBI Cashback Card", raw_markdown=raw_md)
    extracted_short = extract_markdown_with_fallback(md_short_fit)
    assert "5% cashback on online spends" in extracted_short

    rule = _deterministic_tc_parse(
        extracted,
        "SBI Cashback",
        "credit_card",
        ["https://www.sbicard.com/en/personal/credit-cards/rewards/cashback-sbi-card.page"],
    )
    assert rule is not None
    assert rule.verification_status == "live_tc_verified"
    assert rule.points_per_spend_unit == 5.0
    assert rule.monthly_cap_inr == 5000.0
    assert rule.source_urls == ["https://www.sbicard.com/en/personal/credit-cards/rewards/cashback-sbi-card.page"]
    assert rule.last_verified_at is not None

    # Also verify HDFC Infinia dual redemption parsing (cash vs SmartBuy travel)
    infinia_raw = (
        "HDFC Infinia Credit Card earns 5 Reward Points per ₹150 spent on retail purchases. "
        "Redemption value on SmartBuy flights & hotels: 1 Reward Point = ₹1.00. "
        "Statement cash redemption: 1 Reward Point = ₹0.30. Monthly cap ₹15,000."
    )
    infinia_rule = _deterministic_tc_parse(
        infinia_raw,
        "HDFC Infinia",
        "credit_card",
        ["https://www.hdfcbank.com/personal/pay/cards/credit-cards/infinia-credit-card"],
    )
    assert infinia_rule is not None
    assert infinia_rule.verification_status == "live_tc_verified"
    assert infinia_rule.points_per_spend_unit == 5.0
    assert infinia_rule.spend_unit_inr == 150.0
    assert infinia_rule.point_value_cash_inr == pytest.approx(0.30)
    assert infinia_rule.point_value_travel_inr == pytest.approx(1.00)
