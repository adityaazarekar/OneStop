export interface CreditCard {
  id: string;
  bank_name: string;
  card_name: string;
  card_network: string;
  base_reward_pct: number;
  amazon_reward_pct: number;
  flipkart_reward_pct: number;
  annual_fee: number;
}

export interface BankOfferRule {
  bank_name: string;
  card_type?: "credit" | "debit" | "both" | "emi" | null;
  card_network?: "visa" | "mastercard" | "amex" | "rupay" | "any" | null;
  min_txn_amount: number;
  discount_flat: number;
  discount_pct: number;
  max_discount_cap: number;
  excluded_cards: string[];
  valid_until?: string | null;
  summary: string;
}

export interface CardResult {
  card_name: string;
  bank_name: string;
  effective_price: number;
  guaranteed_price: number;
  bank_discount: number;
  reward_value: number;
  reward_pct: number;
  offer_summary: string;
}

export interface MissedSaving {
  card_name: string;
  bank_name: string;
  effective_price: number;
  savings_vs_best_owned: number;
}

export interface TrustReport {
  trust_score: number;
  seller_name: string;
  is_authorized: boolean;
  trust_tier: string;
  warnings: string[];
  breakdown: Record<string, string>;
}

export interface BuyWaitVerdict {
  verdict: "BUY_NOW" | "WAIT" | "OVERPRICED" | "FAIR_PRICE" | "INSUFFICIENT_HISTORY" | string;
  current_price: number;
  p10: number | null;
  median_90d: number | null;
  all_time_low: number | null;
  observation_count: number;
  next_sale_name: string | null;
  days_to_next_sale: number | null;
  reasoning: string;
}

export interface ListingSnapshot {
  id?: string;
  retailer: string;
  listing_url: string;
  listed_price: number;
  current_listed_price?: number;
  mrp?: number | null;
  current_mrp?: number | null;
  in_stock: boolean;
  seller_name?: string | null;
  seller_rating?: number | null;
  trust_score?: number | null;
  match_confidence?: number | null;
  last_checked_at?: string;
  bank_offers?: BankOfferRule[];
}

export interface StackStep {
  step_name: string;
  description: string;
  amount_deducted: number;
  running_price: number;
  verification_status: "verified_on_page" | "stack_path" | "fee" | "post_purchase";
}

export interface AlternativeOptimizationPath {
  title: string;
  method: string;
  potential_savings: number;
  net_price: number;
  confidence: "high" | "medium" | "conditional";
  eligibility: string;
}

export interface StackedEffectivePriceResult {
  original_listed_price: number;
  best_stacked_price: number;
  total_savings: number;
  steps: StackStep[];
  applied_offers: Array<Record<string, any>>;
  alternative_paths: AlternativeOptimizationPath[];
  notes: string;
}

export interface SellerAnalysisItem {
  seller_name: string;
  price: number | null;
  rating: number | null;
  review_count: number | null;
  verdict: "Recommended" | "Proceed with Caution" | "High Risk";
  peace_of_mind_score: number;
  is_buy_box_winner: boolean;
  pros: string[];
  cons: string[];
  risk_assessment: string;
  fulfillment_type: string;
  return_policy: string;
  why_buy_here: string;
}

export interface MultiSellerReport {
  recommended_seller: string;
  recommended_reason: string;
  sellers: SellerAnalysisItem[];
}

export interface StoreRankingChoice {
  retailer: string;
  store_name: string;
  url: string;
  listed_price: number;
  checkout_cash_price?: number;
  best_user_card_net_price?: number;
  effective_price: number;
  in_stock?: boolean;
  is_current_store?: boolean;
  seller_name: string;
  peace_of_mind_score: number;
  return_terms: string;
  warranty_terms: string;
  overall_score: number;
  rank_badge: string;
  is_overall_winner: boolean;
  store_bucket?: "exact_match" | "lesser_known_store" | "variant_mismatch";
  variant_diff_badges?: string[];
  eta_label?: string;
}

export interface QuickCommerceQuote {
  platform: string;
  app_name: string;
  title: string;
  url: string;
  pincode: string;
  selling_price: number;
  mrp?: number | null;
  pack_size?: string | null;
  unit_price_inr?: number | null;
  unit_price_label?: string | null;
  eta_minutes_label: string;
  in_stock: boolean;
  handling_fee_inr: number;
  delivery_fee_inr: number;
  surge_fee_inr: number;
  small_cart_fee_inr: number;
  total_payable_inr: number;
  badge: string;
}

export interface ExecutiveSummary {
  best_store: string;
  best_seller: string;
  best_offer_stack: string;
  final_net_price: number;
  why_this_wins: string;
  key_caveat: string;
  summary_bullets: string[];
}

export type VerificationStatus = "page_verified" | "live_tc_verified" | "issuer_tc_verified" | "no_verified_offer";

export interface PostPurchaseRewards {
  reward_type: string;
  points_earned: number;
  conversion_ratio_cash: string;
  conversion_ratio_travel: string;
  cash_value_inr: number;
  travel_value_inr: number;
  monthly_cap_inr: number | null;
  cap_applied: boolean;
  processing_fee_inr: number;
}

export interface PaymentRouteAnalysis {
  route_type: "direct_points_card" | "direct_cashback_card" | "gift_voucher_stack" | "upi_app_offer" | "unowned_card";
  route_label: string;
  method_name: string;
  is_owned_card?: boolean;
  available: boolean;
  checkout_payable_inr: number;
  instant_discount_inr: number;
  rewards: PostPurchaseRewards;
  effective_cost_if_cash_inr: number;
  effective_cost_if_optimal_redemption_inr: number;
  verification_status: VerificationStatus;
  source_urls: string[];
  exclusions: string[];
  last_verified_at: string | null;
  summary: string;
}

export interface CashVsPointsReport {
  listed_price: number;
  coupon_discount_inr: number;
  mandatory_fees_inr: number;
  instant_bank_discount_inr: number;
  checkout_payable_inr: number;
  best_user_card_net_price_cash?: number;
  best_user_card_net_price_optimal?: number;
  winning_owned_card_name?: string | null;
  winning_instant_offer_summary?: string | null;
  user_wallet_empty?: boolean;
  requires_offer_verification?: boolean;
  offer_verification_note?: string;
  emi_offers?: Array<Record<string, any>>;
  gate_audit_log?: Array<Record<string, any>>;
  routes: PaymentRouteAnalysis[];
  unowned_card_routes?: PaymentRouteAnalysis[];
  best_route_cash: string | null;
  best_route_optimal: string | null;
  efficiency_verdict: string;
  terms_last_verified_at: string | null;
}

export interface AnalysisResponse {
  canonical_product_id: string;
  brand: string;
  model_name: string;
  category: string;
  variant_specs: Record<string, any>;
  listings: ListingSnapshot[];
  ranked_cards: CardResult[];
  best_card: CardResult | null;
  missed_savings: MissedSaving | null;
  guaranteed_price: number;
  listed_price: number;
  trust_report: TrustReport | null;
  buy_wait: BuyWaitVerdict | null;
  bank_offers_parsed: BankOfferRule[];
  stacked_offers?: StackedEffectivePriceResult | null;
  seller_report?: MultiSellerReport | null;
  ranked_store_choices?: StoreRankingChoice[];
  quick_commerce_quotes?: QuickCommerceQuote[];
  llm_summary?: ExecutiveSummary | null;
  cash_vs_points?: CashVsPointsReport | null;
}

export interface RecentProduct {
  id: string;
  brand: string;
  model_name: string;
  category: string;
  variant_specs: Record<string, any>;
  created_at: string;
  listings_count: number;
  lowest_price: number | null;
  highest_mrp: number | null;
  retailers: string[];
  in_stock: boolean;
  listing_url: string | null;
  seller_name: string | null;
  trust_score: number | null;
}

export interface ProductDetails {
  id: string;
  brand: string;
  model_name: string;
  mpn_or_ean: string | null;
  category: string;
  variant_specs: Record<string, any>;
  created_at: string;
  listings: ListingSnapshot[];
  buy_wait: BuyWaitVerdict | null;
  stacked_offers?: StackedEffectivePriceResult | null;
  seller_report?: MultiSellerReport | null;
  ranked_store_choices?: StoreRankingChoice[];
  quick_commerce_quotes?: QuickCommerceQuote[];
  llm_summary?: ExecutiveSummary | null;
  cash_vs_points?: CashVsPointsReport | null;
}

export interface PriceObservationPoint {
  observed_at: string;
  retailer: string;
  listed_price: number;
  coupon_discount: number;
  best_bank_discount: number;
  in_stock: boolean;
}

export interface PriceHistoryResponse {
  canonical_product_id: string;
  days: number;
  observations: PriceObservationPoint[];
}

export interface ExchangeComparisonResponse {
  marketplace_option: {
    label: string;
    gross_value: number;
    haircut_pct: number;
    net_value: number;
    effective_upgrade_cost: number;
  };
  external_option: {
    label: string;
    gross_value: number;
    haircut_pct: number;
    net_value: number;
    effective_upgrade_cost: number;
  };
  recommendation: string;
  reasoning: string;
}

export interface VaultItem {
  id: string;
  product_name: string;
  retailer: string | null;
  purchase_date: string;
  price_paid: number;
  seller_name: string | null;
  invoice_number: string | null;
  warranty_months: number;
  warranty_expiry_date: string;
  days_remaining: number;
}
