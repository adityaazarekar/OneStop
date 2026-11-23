"""
Unit tests for _run_analysis_pipeline and its defensive fallbacks.
Tests:
1. No NameError for best_card when no cards owned / best_card is None.
2. Correct resolution when best_card is present.
3. Graceful degradation when LLM / external services fail.
4. Safe handling of empty / missing payload attributes.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException

from app.api.v1.routes import _run_analysis_pipeline
from app.models.canonical_product import CanonicalProduct
from app.models.credit_card import CreditCard
from app.models.retailer_listing import RetailerListing
from app.schemas.extraction import (
    AnalysisResponse,
    BuyWaitVerdictResponse,
    ClientPayload,
    DynamicSellerInfo,
    ExecutiveSummary,
    LiveOfferRaw,
)
from app.services.buy_wait_engine import BuyWaitVerdict


@pytest.fixture
def mock_llm_parser():
    parser = AsyncMock()
    parser.parse_variant_attributes = AsyncMock(return_value=None)
    parser.parse_bank_offers = AsyncMock(return_value=[])
    parser.analyze_reviews = AsyncMock(return_value=None)
    parser.generate_executive_summary = AsyncMock(return_value=ExecutiveSummary(
        best_store="amazon_in",
        best_seller="Appario Retail Private Ltd",
        best_offer_stack="HDFC Bank Discount",
        final_net_price=65900.0,
        why_this_wins="Cheapest verified price",
        key_caveat="Verify seal on delivery",
        summary_bullets=["Buy from Amazon"],
    ))
    return parser


@pytest.fixture
def mock_session():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def sample_payload():
    return ClientPayload(
        retailer="amazon_in",
        url="https://www.amazon.in/dp/B0BDK62PDX",
        sku="B0BDK62PDX",
        title="Apple iPhone 14 (128 GB) - Blue",
        brand="Apple",
        listed_price=69900.0,
        mrp=79900.0,
        in_stock=True,
        seller_name="Appario Retail Private Ltd",
        seller_rating=4.5,
        sellers=[
            DynamicSellerInfo(
                seller_name="Appario Retail Private Ltd",
                price=69900.0,
                rating=4.5,
                fulfillment="Fulfilled by Amazon",
                return_policy="7 Days Replacement",
                warranty_notes="1 Year Manufacturer Warranty",
                is_buy_box_winner=True,
            )
        ],
        raw_bank_offers=["Flat ₹4,000 instant discount on HDFC Bank Credit Cards"],
        raw_coupons=[],
        raw_live_offers=[
            LiveOfferRaw(
                title="HDFC Bank Discount",
                offer_type="bank",
                raw_text="Flat ₹4,000 instant discount on HDFC Bank Credit Cards",
                source="live_page",
                conditions="",
            )
        ],
        delivery_fee=0.0,
        handling_fee=0.0,
        recent_reviews=["Great phone battery lasts long", "Genuine product sealed pack"],
        variant_specs={"ram_gb": 6, "storage_gb": 128},
        source="extension",
    )


@pytest.mark.asyncio
async def test_run_analysis_pipeline_when_best_card_is_none(mock_session, mock_llm_parser, sample_payload):
    """Test that pipeline executes cleanly without NameError when best_card is None."""
    dummy_product = CanonicalProduct(
        id=uuid.uuid4(),
        brand="Apple",
        model_name="iPhone 14",
        category="smartphones",
        variant_specs={"storage_gb": 128},
    )
    dummy_listing = RetailerListing(
        id=uuid.uuid4(),
        canonical_product_id=dummy_product.id,
        retailer="amazon_in",
        external_sku="B0BDK62PDX",
        listing_url=sample_payload.url,
        current_listed_price=69900.0,
        in_stock=True,
        seller_name="Appario Retail Private Ltd",
        trust_score=85,
    )

    with patch("app.api.v1.routes.get_llm_parser", new=AsyncMock(return_value=mock_llm_parser)), \
         patch("app.api.v1.routes.match_or_create", new=AsyncMock(return_value=(dummy_product, dummy_listing, 0.95))), \
         patch("app.api.v1.routes.get_buy_wait_verdict", new=AsyncMock(return_value=BuyWaitVerdict(
             verdict="BUY_NOW",
             current_price=69900.0,
             p10=68000.0,
             median_90d=71000.0,
             all_time_low=67000.0,
             observation_count=10,
             next_sale_name="Republic Day Sale",
             days_to_next_sale=14,
             reasoning="Price is near all-time low",
         ))):

        response = await _run_analysis_pipeline(
            payload=sample_payload,
            session=mock_session,
            user_card_ids=[],
            competitor_quotes=[],
        )

        assert isinstance(response, AnalysisResponse)
        assert response.canonical_product_id == str(dummy_product.id)
        assert response.brand == "Apple"
        assert response.listed_price == 69900.0
        assert response.best_card is None
        assert response.llm_summary is not None
        assert response.llm_summary.final_net_price > 0


@pytest.mark.asyncio
async def test_run_analysis_pipeline_with_best_card_present(mock_session, mock_llm_parser, sample_payload):
    """Test pipeline uses best_card properly when best_card exists."""
    dummy_product = CanonicalProduct(
        id=uuid.uuid4(),
        brand="Apple",
        model_name="iPhone 14",
        category="smartphones",
        variant_specs={"storage_gb": 128},
    )
    dummy_listing = RetailerListing(
        id=uuid.uuid4(),
        canonical_product_id=dummy_product.id,
        retailer="amazon_in",
        external_sku="B0BDK62PDX",
        listing_url=sample_payload.url,
        current_listed_price=69900.0,
        in_stock=True,
        seller_name="Appario Retail Private Ltd",
        trust_score=85,
    )

    card = CreditCard(
        id=uuid.uuid4(),
        bank_name="HDFC",
        card_name="Millennia",
        card_network="visa",
        base_reward_pct=1.0,
        amazon_reward_pct=5.0,
        flipkart_reward_pct=1.0,
    )

    res_cards = MagicMock()
    res_cards.scalars.return_value.all.return_value = [card]
    res_cards.scalar_one_or_none.return_value = None

    res_auth = MagicMock()
    res_auth.scalar_one_or_none.return_value = None
    res_auth.scalars.return_value.all.return_value = []

    res_listings = MagicMock()
    res_listings.scalars.return_value.all.return_value = [dummy_listing]
    res_listings.scalar_one_or_none.return_value = None

    mock_session.execute.side_effect = [res_cards, res_cards, res_auth, res_listings]

    with patch("app.api.v1.routes.get_llm_parser", new=AsyncMock(return_value=mock_llm_parser)), \
         patch("app.api.v1.routes.match_or_create", new=AsyncMock(return_value=(dummy_product, dummy_listing, 0.95))), \
         patch("app.api.v1.routes.get_buy_wait_verdict", new=AsyncMock(return_value=BuyWaitVerdict(
             verdict="BUY_NOW",
             current_price=69900.0,
             p10=68000.0,
             median_90d=71000.0,
             all_time_low=67000.0,
             observation_count=10,
             next_sale_name=None,
             days_to_next_sale=None,
             reasoning="Good deal",
         ))):

        response = await _run_analysis_pipeline(
            payload=sample_payload,
            session=mock_session,
            user_card_ids=[str(card.id)],
            competitor_quotes=[],
        )

        assert isinstance(response, AnalysisResponse)
        assert response.best_card is not None
        assert response.best_card.card_name == "Millennia"
        assert response.llm_summary is not None


@pytest.mark.asyncio
async def test_run_analysis_pipeline_defensive_fallbacks_on_empty_payload(mock_session, mock_llm_parser):
    """Test that an empty/minimal payload with None fields does not crash the pipeline."""
    sparse_payload = ClientPayload(
        retailer="croma",
        url="https://www.croma.com/p/123",
        sku="123",
        title="Generic Headphones",
        brand=None,
        listed_price=None,
        mrp=None,
        in_stock=True,
        seller_name=None,
        sellers=None,
        raw_bank_offers=None,
        raw_coupons=None,
        raw_live_offers=None,
        delivery_fee=None,
        handling_fee=None,
        recent_reviews=None,
        variant_specs=None,
        source="extension",
    )

    dummy_product = CanonicalProduct(
        id=uuid.uuid4(),
        brand="Generic",
        model_name="Headphones",
        category="audio",
        variant_specs={},
    )
    dummy_listing = RetailerListing(
        id=uuid.uuid4(),
        canonical_product_id=dummy_product.id,
        retailer="croma",
        external_sku="123",
        listing_url=sparse_payload.url,
        current_listed_price=0.0,
        in_stock=True,
        seller_name="Croma",
        trust_score=75,
    )

    # 1. Unpriced / zero-price payload must raise HTTPException 422
    with pytest.raises(HTTPException) as exc_info:
        await _run_analysis_pipeline(
            payload=sparse_payload,
            session=mock_session,
            user_card_ids=None,
            competitor_quotes=None,
        )
    assert exc_info.value.status_code == 422
    assert "Listed price is missing or <= ₹0" in exc_info.value.detail

    # 2. Sparse payload with minimal valid price must execute without crashing
    sparse_payload.listed_price = 1999.0
    dummy_listing.current_listed_price = 1999.0

    with patch("app.api.v1.routes.get_llm_parser", new=AsyncMock(return_value=mock_llm_parser)), \
         patch("app.api.v1.routes.match_or_create", new=AsyncMock(return_value=(dummy_product, dummy_listing, 0.5))), \
         patch("app.api.v1.routes.get_buy_wait_verdict", new=AsyncMock(return_value=BuyWaitVerdict(
             verdict="INSUFFICIENT_HISTORY",
             current_price=1999.0,
             p10=None,
             median_90d=None,
             all_time_low=None,
             observation_count=1,
             next_sale_name=None,
             days_to_next_sale=None,
             reasoning="Not enough data",
         ))):

        response = await _run_analysis_pipeline(
            payload=sparse_payload,
            session=mock_session,
            user_card_ids=None,
            competitor_quotes=None,
        )

        assert isinstance(response, AnalysisResponse)
        assert response.canonical_product_id == str(dummy_product.id)
        assert response.listed_price == 1999.0
        assert response.llm_summary is not None


@pytest.mark.asyncio
async def test_run_analysis_pipeline_llm_failure_graceful_fallback(mock_session, sample_payload):
    """Test that if the LLM parser throws an unhandled exception, pipeline falls back deterministically."""
    failing_parser = AsyncMock()
    failing_parser.parse_variant_attributes.side_effect = RuntimeError("OpenRouter down")
    failing_parser.parse_bank_offers.side_effect = RuntimeError("OpenRouter timeout")
    failing_parser.analyze_reviews.side_effect = RuntimeError("OpenRouter rate limit")
    failing_parser.generate_executive_summary.side_effect = RuntimeError("LLM synthesis failed")

    dummy_product = CanonicalProduct(
        id=uuid.uuid4(),
        brand="Apple",
        model_name="iPhone 14",
        category="smartphones",
        variant_specs={},
    )
    dummy_listing = RetailerListing(
        id=uuid.uuid4(),
        canonical_product_id=dummy_product.id,
        retailer="amazon_in",
        external_sku="B0BDK62PDX",
        listing_url=sample_payload.url,
        current_listed_price=69900.0,
        in_stock=True,
        seller_name="Appario",
        trust_score=80,
    )

    with patch("app.api.v1.routes.get_llm_parser", new=AsyncMock(return_value=failing_parser)), \
         patch("app.api.v1.routes.match_or_create", new=AsyncMock(return_value=(dummy_product, dummy_listing, 0.9))), \
         patch("app.api.v1.routes.get_buy_wait_verdict", new=AsyncMock(return_value=BuyWaitVerdict(
             verdict="BUY_NOW",
             current_price=69900.0,
             p10=68000.0,
             median_90d=70000.0,
             all_time_low=67000.0,
             observation_count=5,
             next_sale_name=None,
             days_to_next_sale=None,
             reasoning="Good deal",
         ))):

        response = await _run_analysis_pipeline(
            payload=sample_payload,
            session=mock_session,
            user_card_ids=None,
            competitor_quotes=None,
        )

        assert isinstance(response, AnalysisResponse)
        assert response.llm_summary is not None
        assert response.llm_summary.final_net_price > 0
        assert "Appario" in response.llm_summary.best_seller or "Verified" in response.llm_summary.best_seller


@pytest.mark.asyncio
async def test_run_analysis_pipeline_persists_bank_offers(mock_session, sample_payload):
    """Test that parsed bank offers are converted into BankOfferRuleModel without TypeError."""
    from app.models.bank_offer_rule import BankOfferRule as BankOfferRuleModel
    from app.schemas.extraction import BankOfferRule

    test_offer = BankOfferRule(
        bank_name="HDFC Bank",
        card_type="credit",
        card_network="visa",
        min_txn_amount=50000.0,
        discount_flat=4000.0,
        discount_pct=0.0,
        max_discount_cap=4000.0,
        excluded_cards=["corporate"],
        valid_until="2026-12-31T23:59:59",
        summary="Flat Rs 4000 off on HDFC credit card",
    )

    parser = AsyncMock()
    parser.parse_variant_attributes = AsyncMock(return_value=None)
    parser.parse_bank_offers = AsyncMock(return_value=[test_offer])
    parser.analyze_reviews = AsyncMock(return_value=None)
    parser.generate_executive_summary = AsyncMock(return_value=ExecutiveSummary(
        best_store="amazon_in",
        best_seller="Appario Retail Private Ltd",
        best_offer_stack="HDFC Bank Discount",
        final_net_price=65900.0,
        why_this_wins="Cheapest verified price",
        key_caveat="Verify seal on delivery",
        summary_bullets=["Buy from Amazon"],
    ))

    dummy_product = CanonicalProduct(
        id=uuid.uuid4(),
        brand="Apple",
        model_name="iPhone 14",
        category="smartphones",
        variant_specs={},
    )
    dummy_listing = RetailerListing(
        id=uuid.uuid4(),
        canonical_product_id=dummy_product.id,
        retailer="amazon_in",
        external_sku="B0BDK62PDX",
        listing_url=sample_payload.url,
        current_listed_price=69900.0,
        in_stock=True,
        seller_name="Appario",
        trust_score=80,
    )

    with patch("app.api.v1.routes.get_llm_parser", new=AsyncMock(return_value=parser)), \
         patch("app.api.v1.routes.match_or_create", new=AsyncMock(return_value=(dummy_product, dummy_listing, 0.9))), \
         patch("app.api.v1.routes.get_buy_wait_verdict", new=AsyncMock(return_value=BuyWaitVerdict(
             verdict="BUY_NOW",
             current_price=69900.0,
             p10=68000.0,
             median_90d=70000.0,
             all_time_low=67000.0,
             observation_count=5,
             next_sale_name=None,
             days_to_next_sale=None,
             reasoning="Good deal",
         ))):

        response = await _run_analysis_pipeline(
            payload=sample_payload,
            session=mock_session,
            user_card_ids=None,
            competitor_quotes=None,
        )

        assert isinstance(response, AnalysisResponse)
        # Verify BankOfferRuleModel was added to session
        added_offer_rules = [
            call.args[0] for call in mock_session.add.call_args_list
            if isinstance(call.args[0], BankOfferRuleModel)
        ]
        assert len(added_offer_rules) == 1
        db_rule = added_offer_rules[0]
        assert db_rule.bank_name == "HDFC Bank"
        assert db_rule.retailer == "amazon_in"
        assert db_rule.discount_flat == 4000.0
        assert db_rule.raw_terms == "Flat Rs 4000 off on HDFC credit card"
        assert db_rule.excluded_cards == {"excluded": ["corporate"]}

