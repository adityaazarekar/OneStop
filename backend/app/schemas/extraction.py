"""Pydantic v2 schemas for all extraction, parsing, and scraper payloads."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ───────────────────────────────────────────
# Client / DOM extractor payload
# ───────────────────────────────────────────
class DynamicSellerInfo(BaseModel):
    """Dynamically extracted seller from the live product page."""

    seller_name: str
    price: float | None = None
    rating: float | None = None
    review_count: int | None = None
    fulfillment: str | None = None  # e.g., "Fulfilled by Amazon", "F-Assured", "Direct"
    return_policy: str | None = None  # e.g., "7 Days Replacement", "10 Days Returnable"
    warranty_notes: str | None = None  # e.g., "1 Year Manufacturer Warranty"
    is_buy_box_winner: bool = True


class LiveOfferRaw(BaseModel):
    """Raw offer tagged directly from the live product page."""

    title: str
    offer_type: str = "general"  # bank, coupon, emi, cashback, upi, fee, special
    raw_text: str
    source: Literal["live_page", "stack_optimization"] = "live_page"
    conditions: str = ""
    bank_or_app: str | None = None
    payment_type: str | None = None  # credit_card, debit_card, upi, emi, coupon, fee
    discount_inr: float | None = None
    coupon_scope: str | None = None  # current_item_coupon | cross_sell | future_order | None
    issuer_or_app: str | None = None
    payment_channel: str | None = None
    discount_type: str | None = None  # flat | percentage | teaser | cashback | emi | fee
    discount_value: float | None = None
    max_cap: float | None = None
    min_spend: float | None = None
    emi_only: bool = False
    is_teaser_summary: bool = False
    is_non_item_promo: bool = False
    raw_source_text: str | None = None


class ClientPayload(BaseModel):
    """Standardized payload from the Chrome Extension, Android App, or server scraper."""

    retailer: str = Field(description="Retailer key: amazon_in, flipkart, croma, reliance_digital, or hostname-derived for generic sites")
    url: str
    title: str
    brand: str | None = None
    sku: str = Field(default="", description="ASIN, FSN, or retailer-specific product ID")
    listed_price: float | None = None
    mrp: float | None = None
    retailer_advertised_lowest_price: float | None = None
    image_url: str | None = None
    images: list[str] = Field(default_factory=list)
    pincode: str | None = None
    currency: str = "INR"
    in_stock: bool = True
    is_transactional_store: bool = True
    requires_offer_verification: bool = False
    offer_verification_note: str | None = None
    seller_name: str | None = None
    seller_rating: float | None = None
    sellers: list[DynamicSellerInfo] = Field(default_factory=list)
    raw_bank_offers: list[str] = Field(default_factory=list)
    raw_coupons: list[str] = Field(default_factory=list)
    raw_exchange_offers: list[str] = Field(default_factory=list)
    raw_live_offers: list[LiveOfferRaw] = Field(default_factory=list)
    delivery_fee: float = 0.0
    handling_fee: float = 0.0
    recent_reviews: list[str] = Field(default_factory=list)
    variant_specs: dict = Field(default_factory=dict)

    @field_validator(
        "images",
        "sellers",
        "raw_bank_offers",
        "raw_coupons",
        "raw_exchange_offers",
        "raw_live_offers",
        "recent_reviews",
        mode="before",
    )
    @classmethod
    def _coerce_none_list(cls, v):
        return v if v is not None else []

    @field_validator("delivery_fee", "handling_fee", mode="before")
    @classmethod
    def _coerce_none_float(cls, v):
        return float(v) if v is not None else 0.0

    @field_validator("variant_specs", mode="before")
    @classmethod
    def _coerce_none_dict(cls, v):
        return v if v is not None else {}

    @field_validator("sku", mode="before")
    @classmethod
    def _coerce_none_str(cls, v):
        return str(v) if v is not None else ""
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source: Literal["extension", "app", "server"] = "server"


# ───────────────────────────────────────────
# Cross-store competitor quotes (from extension)
# ───────────────────────────────────────────
class CompetitorQuote(BaseModel):
    """Live price scraped from a competing retailer by the extension's background.js or universal scraper."""

    retailer: str
    title: str
    listed_price: float | None = None
    currency: str = "INR"
    url: str
    sku: str = ""
    in_stock: bool = True
    is_transactional_store: bool = True
    seller_name: str | None = None
    seller_rating: float | None = None
    return_policy: str | None = None
    warranty_notes: str | None = None
    delivery_fee: float = 0.0
    bank_offer_summary: str | None = None
    match_score: float | None = None
    extraction_tier: str | None = None
    image_url: str | None = None
    source: str | None = None  # "extension_offscreen" | "searxng" | "serper"
    store_bucket: Literal["exact_match", "lesser_known_store", "variant_mismatch"] = "exact_match"
    variant_diff_badges: list[str] = Field(default_factory=list)
    eta_label: str | None = None


class QuickCommerceQuote(BaseModel):
    """Location-aware 10-minute Quick Commerce quote (Blinkit, Zepto, Instamart, BigBasket Now, Flipkart Minutes, JioMart Quick)."""

    platform: str
    app_name: str
    title: str
    url: str
    pincode: str = "411001"
    selling_price: float
    mrp: float | None = None
    pack_size: str | None = None
    unit_price_inr: float | None = None
    unit_price_label: str | None = None  # e.g. "₹24/100g" or "₹15/unit"
    eta_minutes_label: str = "10 mins"
    in_stock: bool = True
    handling_fee_inr: float = 0.0
    delivery_fee_inr: float = 0.0
    surge_fee_inr: float = 0.0
    small_cart_fee_inr: float = 0.0
    total_payable_inr: float = 0.0
    badge: str = "10-Min Quick Commerce"


class AnalyzePayloadRequest(BaseModel):
    """Wrapper sent by the Chrome Extension with payload + cross-store data."""

    payload: ClientPayload
    competitor_quotes: list[CompetitorQuote] = Field(default_factory=list)
    user_card_ids: list[str] = Field(default_factory=list)
    pincode: str | None = None


# ───────────────────────────────────────────
# LLM-parsed structured outputs
# ───────────────────────────────────────────
class VariantAttributes(BaseModel):
    """Parsed variant attributes from product title and specs text."""

    brand: str
    model_family: str
    model_name: str
    ram_gb: int | None = None
    storage_gb: int | None = None
    color: str | None = None
    screen_size_inches: float | None = None
    resolution: str | None = None
    processor: str | None = None
    condition: Literal["new", "refurbished", "open_box", "used"] = "new"

    @field_validator("brand", "model_family", "model_name", mode="before")
    @classmethod
    def _coerce_null_str(cls, v: object) -> str:
        return "" if v is None else str(v)


class BankOfferRule(BaseModel):
    """Single parsed bank/card offer from raw T&C text."""

    bank_name: str
    card_type: Literal["credit", "debit", "both", "emi"] | None = None
    card_network: Literal["visa", "mastercard", "amex", "rupay", "any"] | None = None
    min_txn_amount: float = 0.0
    discount_flat: float = 0.0
    discount_pct: float = 0.0
    max_discount_cap: float = 0.0
    excluded_cards: list[str] = Field(default_factory=list)
    valid_until: str | None = None
    summary: str = ""
    emi_only: bool = False
    is_teaser_summary: bool = False
    is_non_item_promo: bool = False
    is_cashback: bool = False
    is_upto_ceiling: bool = False
    has_explicit_flat: bool = False

    @field_validator(
        "min_txn_amount",
        "discount_flat",
        "discount_pct",
        "max_discount_cap",
        mode="before",
    )
    @classmethod
    def _coerce_null_float(cls, v):
        return float(v) if v is not None else 0.0

    @field_validator("excluded_cards", mode="before")
    @classmethod
    def _coerce_null_list(cls, v):
        return v if v is not None else []

    @field_validator("summary", mode="before")
    @classmethod
    def _coerce_null_str(cls, v):
        return str(v) if v is not None else ""


class BankOfferRuleList(BaseModel):
    """Container returned by LLM for batch bank-offer parsing."""

    offers: list[BankOfferRule]


class ReviewScamSignals(BaseModel):
    """Scam / trust signals extracted from recent customer reviews."""

    fake_product_mentions: int = 0
    open_box_mentions: int = 0
    seal_broken_mentions: int = 0
    refurbished_mentions: int = 0
    wrong_product_mentions: int = 0
    suspicious_review_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    top_red_flags: list[str] = Field(default_factory=list)
    trust_impact: int = Field(default=0, ge=-100, le=0)


class InvoiceExtracted(BaseModel):
    """Fields extracted from an uploaded PDF tax invoice."""

    product_name: str
    purchase_date: str
    price_paid: float
    seller_name: str
    invoice_number: str
    retailer: str | None = None
    warranty_months: int = 12


# ───────────────────────────────────────────
# Scraper result wrapper
# ───────────────────────────────────────────
class ScraperResult(BaseModel):
    """Wraps a scraper attempt with success/failure status."""

    success: bool
    payload: ClientPayload | None = None
    error: str | None = None
    source: Literal[
        "curl_cffi", "playwright", "cache", "extension", "app"
    ] = "curl_cffi"
    cached: bool = False
    fetch_ms: float = 0.0
    parse_ms: float = 0.0


class SearchHit(BaseModel):
    """Single result from a cross-store product search."""

    retailer: str = Field(description="Retailer key: amazon_in, flipkart, croma, reliance_digital, or hostname-derived for generic sites")
    title: str
    url: str
    price: float | None = None
    sku: str
    thumbnail_url: str | None = None


# ───────────────────────────────────────────
# API response schemas
# ───────────────────────────────────────────
class CardResultResponse(BaseModel):
    card_name: str
    bank_name: str
    effective_price: float
    guaranteed_price: float
    bank_discount: float
    reward_value: float
    reward_pct: float
    offer_summary: str = ""


class MissedSavingResponse(BaseModel):
    card_name: str
    bank_name: str
    effective_price: float
    savings_vs_best_owned: float


class TrustReportResponse(BaseModel):
    trust_score: int
    seller_name: str
    is_authorized: bool
    trust_tier: str
    warnings: list[str] = Field(default_factory=list)
    breakdown: dict = Field(default_factory=dict)


class BuyWaitVerdictResponse(BaseModel):
    verdict: str
    current_price: float
    p10: float | None = None
    median_90d: float | None = None
    all_time_low: float | None = None
    observation_count: int = 0
    next_sale_name: str | None = None
    days_to_next_sale: int | None = None
    reasoning: str = ""


class ExchangeOptionResponse(BaseModel):
    label: str
    gross_value: float
    haircut_pct: float
    net_value: float
    effective_upgrade_cost: float


class ExchangeComparisonResponse(BaseModel):
    marketplace_option: ExchangeOptionResponse
    external_option: ExchangeOptionResponse
    recommendation: str
    reasoning: str


class ListingSnapshot(BaseModel):
    retailer: str
    listing_url: str
    listed_price: float
    mrp: float | None = None
    in_stock: bool = True
    seller_name: str | None = None
    trust_score: int | None = None
    match_confidence: float = 0.0


# ───────────────────────────────────────────
# Dynamic Offer Stacking Schemas
# ───────────────────────────────────────────
class StackStep(BaseModel):
    step_name: str
    description: str
    amount_deducted: float
    running_price: float
    verification_status: Literal["verified_on_page", "stack_path", "fee", "post_purchase"] = "verified_on_page"


class AlternativeOptimizationPath(BaseModel):
    title: str
    method: str
    potential_savings: float
    net_price: float
    confidence: Literal["high", "medium", "conditional"] = "medium"
    eligibility: str


class StackedEffectivePriceResult(BaseModel):
    original_listed_price: float
    best_stacked_price: float
    total_savings: float
    steps: list[StackStep] = Field(default_factory=list)
    applied_offers: list[dict] = Field(default_factory=list)
    alternative_paths: list[AlternativeOptimizationPath] = Field(default_factory=list)
    notes: str = ""


# ───────────────────────────────────────────
# Dynamic Multi-Seller Fact-Check Schemas
# ───────────────────────────────────────────
class SellerAnalysisItem(BaseModel):
    seller_name: str
    price: float | None = None
    rating: float | None = None
    review_count: int | None = None
    verdict: Literal["Recommended", "Proceed with Caution", "High Risk"] = "Proceed with Caution"
    peace_of_mind_score: int = 50  # 0 to 100
    is_buy_box_winner: bool = False
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    risk_assessment: str = ""  # counterfeit, open-box, return friction risk
    fulfillment_type: str = ""
    return_policy: str = ""
    why_buy_here: str = ""


class MultiSellerReport(BaseModel):
    recommended_seller: str = ""
    recommended_reason: str = ""
    sellers: list[SellerAnalysisItem] = Field(default_factory=list)


# ───────────────────────────────────────────
# Fair Multi-Site Ranking Schemas
# ───────────────────────────────────────────
class StoreRankingChoice(BaseModel):
    retailer: str
    store_name: str
    url: str
    listed_price: float
    checkout_cash_price: float = 0.0
    best_user_card_net_price: float = 0.0
    effective_price: float
    in_stock: bool = True
    is_current_store: bool = False
    seller_name: str = ""
    peace_of_mind_score: int = 50
    return_terms: str = ""
    warranty_terms: str = ""
    overall_score: float = 0.0
    rank_badge: str = ""  # "Lowest Net Price", "Official & Peace of Mind", "Overall Best Choice"
    is_overall_winner: bool = False
    store_bucket: Literal["exact_match", "lesser_known_store", "variant_mismatch"] = "exact_match"
    variant_diff_badges: list[str] = Field(default_factory=list)
    eta_label: str = "2-4 days"


# ───────────────────────────────────────────
# Executive LLM Summary
# ───────────────────────────────────────────
class ExecutiveSummary(BaseModel):
    best_store: str = ""
    best_seller: str = ""
    best_offer_stack: str = ""
    final_net_price: float = 0.0
    why_this_wins: str = ""
    key_caveat: str = ""
    summary_bullets: list[str] = Field(default_factory=list)


# ───────────────────────────────────────────
# Live Payment T&C Rules + Cash-vs-Points Transparency
# ───────────────────────────────────────────
VerificationStatus = Literal["page_verified", "live_tc_verified", "issuer_tc_verified", "no_verified_offer"]


class VerifiedPaymentRule(BaseModel):
    """One payment method's reward/discount rule, verified from page or live official T&C."""

    method_name: str
    method_type: Literal["credit_card", "debit_card", "gift_voucher", "upi_app", "wallet", "emi"] = "credit_card"
    instant_discount_inr: float = 0.0
    max_discount_cap_inr: float | None = None
    reward_type: Literal["points", "cashback", "miles", "voucher_discount", "none"] = "none"
    points_per_spend_unit: float = 0.0
    spend_unit_inr: float = 100.0
    point_value_cash_inr: float = 0.0
    point_value_travel_inr: float = 0.0
    monthly_cap_inr: float | None = None
    processing_fee_pct: float = 0.0
    exclusions: list[str] = Field(default_factory=list)
    stackable_with: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus = "no_verified_offer"
    source_urls: list[str] = Field(default_factory=list)
    last_verified_at: datetime | None = None
    plain_english_summary: str = ""


class VerifiedPaymentRuleList(BaseModel):
    rules: list[VerifiedPaymentRule] = Field(default_factory=list)


class PostPurchaseRewards(BaseModel):
    """Value credited AFTER purchase. Never deducted from checkout_payable_inr."""

    reward_type: str = "none"
    points_earned: float = 0.0
    conversion_ratio_cash: str = ""  # e.g. "3.33 pts = ₹1 statement cash (₹0.30/pt)"
    conversion_ratio_travel: str = ""  # e.g. "1 pt = ₹1.00 travel"
    cash_value_inr: float = 0.0
    travel_value_inr: float = 0.0
    monthly_cap_inr: float | None = None
    cap_applied: bool = False
    processing_fee_inr: float = 0.0


class PaymentRouteAnalysis(BaseModel):
    route_type: Literal["direct_points_card", "direct_cashback_card", "gift_voucher_stack", "upi_app_offer", "unowned_card"]
    route_label: str
    method_name: str
    is_owned_card: bool = True
    available: bool = True
    checkout_payable_inr: float
    instant_discount_inr: float = 0.0
    rewards: PostPurchaseRewards = Field(default_factory=PostPurchaseRewards)
    effective_cost_if_cash_inr: float
    effective_cost_if_optimal_redemption_inr: float
    verification_status: VerificationStatus = "no_verified_offer"
    source_urls: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    last_verified_at: datetime | None = None
    summary: str = ""


class CashVsPointsReport(BaseModel):
    listed_price: float
    coupon_discount_inr: float = 0.0
    mandatory_fees_inr: float = 0.0
    instant_bank_discount_inr: float = 0.0
    checkout_payable_inr: float
    best_user_card_net_price_cash: float = 0.0
    best_user_card_net_price_optimal: float = 0.0
    winning_owned_card_name: str | None = None
    winning_instant_offer_summary: str | None = None
    user_wallet_empty: bool = False
    requires_offer_verification: bool = False
    offer_verification_note: str = ""
    emi_offers: list[dict] = Field(default_factory=list)
    gate_audit_log: list[dict] = Field(default_factory=list)
    routes: list[PaymentRouteAnalysis] = Field(default_factory=list)
    unowned_card_routes: list[PaymentRouteAnalysis] = Field(default_factory=list)
    best_route_cash: str | None = None
    best_route_optimal: str | None = None
    efficiency_verdict: str = ""
    terms_last_verified_at: datetime | None = None


# ───────────────────────────────────────────
# Stage-2 universal store discovery
# ───────────────────────────────────────────
class DiscoverStoresRequest(BaseModel):
    payload: ClientPayload
    max_stores: int = Field(default=25, ge=1, le=30)
    pincode: str | None = None


class DiscoverStoresResponse(BaseModel):
    query: str
    quotes: list[CompetitorQuote] = Field(default_factory=list)
    quick_commerce_quotes: list[QuickCommerceQuote] = Field(default_factory=list)
    rejected_count: int = 0
    cached: bool = False
    errors: list[str] = Field(default_factory=list)


class CompareAllStoresRequest(BaseModel):
    payload: ClientPayload
    pincode: str = "411001"
    user_card_ids: list[str] = Field(default_factory=list)


class CompareAllStoresResponse(BaseModel):
    job_id: str
    pincode: str
    cached_stores: list[CompetitorQuote] = Field(default_factory=list)
    cached_quick_commerce: list[QuickCommerceQuote] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    """Full response from /analyze-client-payload or /analyze-url."""
    canonical_product_id: str
    brand: str
    model_name: str
    category: str
    variant_specs: dict = Field(default_factory=dict)
    listings: list[ListingSnapshot] = Field(default_factory=list)
    ranked_cards: list[CardResultResponse] = Field(default_factory=list)
    best_card: CardResultResponse | None = None
    missed_savings: MissedSavingResponse | None = None
    guaranteed_price: float = 0.0
    listed_price: float = 0.0
    trust_report: TrustReportResponse | None = None
    buy_wait: BuyWaitVerdictResponse | None = None
    exchange_comparison: ExchangeComparisonResponse | None = None
    bank_offers_parsed: list[BankOfferRule] = Field(default_factory=list)
    # ── New Dynamic Intelligence Features ──
    stacked_offers: StackedEffectivePriceResult | None = None
    seller_report: MultiSellerReport | None = None
    ranked_store_choices: list[StoreRankingChoice] = Field(default_factory=list)
    quick_commerce_quotes: list[QuickCommerceQuote] = Field(default_factory=list)
    llm_summary: ExecutiveSummary | None = None
    cash_vs_points: CashVsPointsReport | None = None


class EffectivePriceRequest(BaseModel):
    listed_price: float
    coupon_discount: float = 0.0
    platform_fee: float = 0.0
    bank_offers: list[BankOfferRule] = Field(default_factory=list)
    card_ids: list[str] = Field(default_factory=list, description="UUIDs of cards the user owns")
    retailer: str = "amazon_in"


class EffectivePriceResponse(BaseModel):
    guaranteed_price: float
    ranked_cards: list[CardResultResponse] = Field(default_factory=list)
    best_card: CardResultResponse | None = None
    missed_savings: MissedSavingResponse | None = None


class VaultItemResponse(BaseModel):
    id: str
    product_name: str
    retailer: str | None = None
    purchase_date: str
    price_paid: float
    seller_name: str | None = None
    invoice_number: str | None = None
    warranty_months: int
    warranty_expiry_date: str
    days_remaining: int

