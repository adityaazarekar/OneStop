"""
Fair Multi-Site Ranking & Dynamic 3-Tier Store Bucketing Engine.

Ranks all discovered transactional stores & sellers without marketplace bias:
  - Strictly blocks price trackers, review portals, coupon sites, and forums.
  - Never duplicates the active tab's store with a conflicting price (`is_current_store=True`).
  - Organizes stores transparently into 3 dynamic tiers:
      1. `exact_match`: Confirmed variant, valid price, in stock on established/verified store.
      2. `lesser_known_store`: Confirmed variant on niche/specialty/independent store, OR an exact match that is currently `out_of_stock` (`in_stock=False`).
      3. `variant_mismatch` ("May not be the exact item"): Same core model & product_type, but one or more dynamically induced `distinguishing_attributes` differ (or price is missing/extreme outlier), with auto-generated `variant_diff_badges`.
  - Winner Guardrail: ONLY an `in_stock == True` store in `exact_match` or confirmed-variant `lesser_known_store` can win `"Lowest Net Price"` / `is_overall_winner=True`. Never lets `variant_mismatch` or `out_of_stock` distort the winning recommendation.
"""
from __future__ import annotations

import statistics

from app.schemas.extraction import CompetitorQuote, ListingSnapshot, StoreRankingChoice, VerifiedPaymentRule
from app.services.card_optimizer import CardLike
from app.services.product_matcher import classify_store_candidate
from app.services.rate_guard import extract_domain
from app.services.universal_scraper import is_blocked_non_retailer_domain


RETAILER_DISPLAY_NAMES = {
    "amazon_in": "Amazon India",
    "flipkart": "Flipkart",
    "croma": "Croma (Tata Enterprise)",
    "reliance_digital": "Reliance Digital",
    "vijaysales": "Vijay Sales",
    "tatacliq": "Tata CLiQ",
    "jiomart": "JioMart",
    "myntra": "Myntra",
    "ajio": "Ajio",
    "nykaa": "Nykaa",
    "meesho": "Meesho",
    "snapdeal": "Snapdeal",
    "pepperfry": "Pepperfry",
    "lenskart": "Lenskart",
    "boat": "boAt Official Store",
    "lg_official": "LG India Official",
    "samsung_official": "Samsung India Official",
    "apple_official": "Apple Store India",
    "sony_official": "Sony India Official",
    "nike_official": "Nike India Official",
    "adidas_official": "Adidas India Official",
}


def _compute_store_best_user_card_net(
    checkout_cash_price: float,
    user_cards: list[CardLike] | None,
    verified_rules: list[VerifiedPaymentRule] | None,
) -> float:
    """Compute best net price using ONLY verified rules for the user's owned cards."""
    if not user_cards or not verified_rules or checkout_cash_price <= 0:
        return round(checkout_cash_price, 2)

    from app.services.offer_stack_engine import _evaluate_rule_rewards

    rule_map = {
        r.method_name.lower(): r
        for r in verified_rules
        if r.verification_status in ("page_verified", "live_tc_verified", "issuer_tc_verified")
    }
    best_net = checkout_cash_price
    for card in user_cards:
        rule = rule_map.get(card.card_name.lower())
        if not rule:
            continue
        rewards = _evaluate_rule_rewards(checkout_cash_price, rule)
        net = max(0.0, round(checkout_cash_price + rewards.processing_fee_inr - rewards.cash_value_inr, 2))
        if net < best_net:
            best_net = net
    return round(best_net, 2)


def _infer_eta_label(retailer: str, in_stock: bool, existing_eta: str | None = None) -> str:
    if not in_stock:
        return "Out of stock"
    if existing_eta:
        return existing_eta
    low = (retailer or "").lower()
    if low in ("blinkit", "zepto", "instamart", "bigbasket_now", "flipkart_minutes", "jiomart_quick"):
        return "10-15 mins"
    if low in ("croma", "reliance_digital", "vijaysales", "amazon_in", "flipkart"):
        return "1-2 days"
    return "2-5 days"


def rank_stores_fairly(
    current_retailer: str,
    current_listed_price: float,
    current_checkout_cash_price: float,
    current_best_user_card_net_price: float,
    current_url: str,
    current_seller: str | None,
    current_peace_of_mind: int,
    competitors: list[CompetitorQuote],
    existing_listings: list[ListingSnapshot] | None = None,
    user_cards: list[CardLike] | None = None,
    verified_rules: list[VerifiedPaymentRule] | None = None,
    *,
    target_title: str | None = None,
    target_brand: str | None = None,
    target_specs: dict | None = None,
    current_in_stock: bool = True,
) -> list[StoreRankingChoice]:
    """
    Ranks transactional stores across uniform metrics (`checkout_cash_price` and
    `best_user_card_net_price`), dynamic 3-tier bucketing (`exact_match`,
    `lesser_known_store`, `variant_mismatch`), and stock guardrails.
    """
    stores: list[StoreRankingChoice] = []
    seen_retailers: set[str] = set()
    seen_domains: set[str] = set()

    cur_ret_norm = (current_retailer or "store").lower().strip()
    cur_dom = extract_domain(current_url) if current_url else ""

    # Compute reference median price for statistical outlier detection
    valid_prices: list[float] = []
    if current_listed_price and current_listed_price > 0:
        valid_prices.append(current_listed_price)
    for q in competitors or []:
        if q.listed_price and q.listed_price > 0:
            valid_prices.append(q.listed_price)
    for l in existing_listings or []:
        if l.listed_price and l.listed_price > 0:
            valid_prices.append(l.listed_price)
    median_price = float(statistics.median(valid_prices)) if valid_prices else 0.0

    # 1. Add active tab's current store ONCE, clearly marked is_current_store=True
    if current_listed_price > 0 and not (current_url and is_blocked_non_retailer_domain(current_url)):
        seen_retailers.add(cur_ret_norm)
        if cur_dom:
            seen_domains.add(cur_dom)
        cur_bucket = "exact_match" if current_in_stock else "lesser_known_store"
        stores.append(
            StoreRankingChoice(
                retailer=cur_ret_norm,
                store_name=RETAILER_DISPLAY_NAMES.get(cur_ret_norm, cur_ret_norm.replace("_", " ").title()),
                url=current_url,
                listed_price=round(current_listed_price, 2),
                checkout_cash_price=round(current_checkout_cash_price, 2),
                best_user_card_net_price=round(current_best_user_card_net_price, 2),
                effective_price=round(current_best_user_card_net_price, 2),
                in_stock=current_in_stock,
                is_current_store=True,
                seller_name=current_seller or "Primary Store Seller",
                peace_of_mind_score=current_peace_of_mind,
                return_terms="7-10 Days Replacement / Official Return Window",
                warranty_terms="1 Year Brand Warranty",
                overall_score=0.0,
                rank_badge="",
                is_overall_winner=False,
                store_bucket=cur_bucket,  # type: ignore[arg-type]
                variant_diff_badges=[],
                eta_label=_infer_eta_label(cur_ret_norm, current_in_stock),
            )
        )

    # 2. Add verified transactional competitors (skip blocked domains & active store)
    for q in competitors or []:
        if is_blocked_non_retailer_domain(q.url) or is_blocked_non_retailer_domain(q.retailer):
            continue
        ret_norm = (q.retailer or "").lower().strip()
        dom = extract_domain(q.url) if q.url else ""
        if ret_norm in seen_retailers or (dom and dom in seen_domains):
            continue

        # Determine bucket & dynamic variant diff badges
        bucket = getattr(q, "store_bucket", "exact_match") or "exact_match"
        diff_badges = list(getattr(q, "variant_diff_badges", None) or [])
        q_in_stock = bool(getattr(q, "in_stock", True))
        q_price = float(q.listed_price or 0.0)

        if target_title and q.title and not diff_badges and bucket == "exact_match":
            decision = classify_store_candidate(
                target_title,
                q.title,
                target_brand=target_brand,
                target_specs=target_specs,
                candidate_price=q_price if q_price > 0 else None,
                reference_median_price=median_price if median_price > 0 else None,
                in_stock=q_in_stock,
                retailer_key=ret_norm,
            )
            if not decision.accepted:
                continue
            bucket = decision.bucket
            diff_badges = list(decision.variant_diff_badges)
        else:
            # Still enforce price missing / extreme outlier & out-of-stock bucketing rules
            if q_price <= 0:
                bucket = "variant_mismatch"
                if "Price Missing" not in diff_badges:
                    diff_badges.append("Price Missing")
            elif median_price > 0 and (q_price < median_price * 0.45 or q_price > median_price * 2.25):
                bucket = "variant_mismatch"
                if "Outlier Price" not in diff_badges:
                    diff_badges.append("Outlier Price")
            elif bucket == "exact_match" and not q_in_stock:
                bucket = "lesser_known_store"

        if q_price <= 0 and bucket != "variant_mismatch":
            continue

        seen_retailers.add(ret_norm)
        if dom:
            seen_domains.add(dom)

        base_trust = 75
        if ret_norm in ("croma", "reliance_digital", "tatacliq"):
            base_trust = 85
        elif ret_norm == "vijaysales":
            base_trust = 80
        elif ret_norm in ("amazon_in", "flipkart"):
            base_trust = 78

        comp_checkout_cash = round(q_price + max(0.0, q.delivery_fee or 0.0), 2) if q_price > 0 else 0.0
        comp_best_net = (
            _compute_store_best_user_card_net(comp_checkout_cash, user_cards, verified_rules)
            if comp_checkout_cash > 0
            else 0.0
        )

        stores.append(
            StoreRankingChoice(
                retailer=ret_norm,
                store_name=RETAILER_DISPLAY_NAMES.get(ret_norm, ret_norm.replace("_", " ").title()),
                url=q.url,
                listed_price=round(q_price, 2),
                checkout_cash_price=comp_checkout_cash,
                best_user_card_net_price=comp_best_net,
                effective_price=comp_best_net,
                in_stock=q_in_stock,
                is_current_store=False,
                seller_name=q.seller_name or f"{ret_norm.replace('_', ' ').title()} Verified Seller",
                peace_of_mind_score=base_trust,
                return_terms=q.return_policy or "Standard Manufacturer Replacement Support",
                warranty_terms=q.warranty_notes or "1 Year Manufacturer Warranty",
                overall_score=0.0,
                rank_badge="",
                is_overall_winner=False,
                store_bucket=bucket,  # type: ignore[arg-type]
                variant_diff_badges=diff_badges,
                eta_label=_infer_eta_label(ret_norm, q_in_stock, getattr(q, "eta_label", None)),
            )
        )

    # 3. Add distinct DB listings (strictly deduplicated by retailer AND domain, blocking non-retailers)
    for l in existing_listings or []:
        if not l.listed_price or l.listed_price <= 0:
            continue
        if is_blocked_non_retailer_domain(l.listing_url) or is_blocked_non_retailer_domain(l.retailer):
            continue
        ret_norm = (l.retailer or "").lower().strip()
        dom = extract_domain(l.listing_url) if l.listing_url else ""
        if ret_norm in seen_retailers or (dom and dom in seen_domains):
            continue
        seen_retailers.add(ret_norm)
        if dom:
            seen_domains.add(dom)

        l_in_stock = bool(getattr(l, "in_stock", True))
        l_bucket = "exact_match" if l_in_stock else "lesser_known_store"
        db_checkout_cash = round(l.listed_price, 2)
        db_best_net = _compute_store_best_user_card_net(db_checkout_cash, user_cards, verified_rules)
        stores.append(
            StoreRankingChoice(
                retailer=ret_norm,
                store_name=RETAILER_DISPLAY_NAMES.get(ret_norm, ret_norm.replace("_", " ").title()),
                url=l.listing_url,
                listed_price=round(l.listed_price, 2),
                checkout_cash_price=db_checkout_cash,
                best_user_card_net_price=db_best_net,
                effective_price=db_best_net,
                in_stock=l_in_stock,
                is_current_store=False,
                seller_name=l.seller_name or "Verified Seller",
                peace_of_mind_score=l.trust_score or 75,
                return_terms="Standard Replacement Policy",
                warranty_terms="1 Year Manufacturer Warranty",
                overall_score=0.0,
                rank_badge="",
                is_overall_winner=False,
                store_bucket=l_bucket,  # type: ignore[arg-type]
                variant_diff_badges=[],
                eta_label=_infer_eta_label(ret_norm, l_in_stock),
            )
        )

    if not stores:
        return []

    # ── Winner Guardrail: ONLY in_stock stores in exact_match (or confirmed lesser_known_store) with price > 0 can win ──
    exact_verified_winners = [
        s
        for s in stores
        if s.in_stock and s.store_bucket == "exact_match" and s.best_user_card_net_price > 0
    ]
    eligible_winners = exact_verified_winners or [
        s
        for s in stores
        if s.in_stock and s.store_bucket in ("exact_match", "lesser_known_store") and s.best_user_card_net_price > 0
    ]
    reference_pool = eligible_winners if eligible_winners else [s for s in stores if s.best_user_card_net_price > 0]
    lowest_price = min((s.best_user_card_net_price for s in reference_pool), default=0.0)

    for s in stores:
        if s.best_user_card_net_price > 0 and lowest_price > 0:
            price_diff_pct = ((s.best_user_card_net_price - lowest_price) / lowest_price) * 100.0
            price_score = max(0.0, 100.0 - (price_diff_pct * 2.0))
        else:
            price_score = 0.0
        base_overall = round((price_score * 0.55) + (s.peace_of_mind_score * 0.30) + 15.0, 1)
        # Penalize variant_mismatch, unverified, and out_of_stock so they never rank above confirmed in-stock exact matches
        if s.store_bucket == "variant_mismatch":
            base_overall = round(max(1.0, base_overall - 50.0), 1)
        elif s.store_bucket == "unverified":
            base_overall = round(max(1.0, base_overall - 40.0), 1)
        elif not s.in_stock:
            base_overall = round(max(1.0, base_overall - 35.0), 1)
        s.overall_score = base_overall

    stores.sort(
        key=lambda x: (
            0 if (x.store_bucket == "exact_match" and x.in_stock) else (1 if x.store_bucket == "lesser_known_store" else 2),
            -x.overall_score,
        )
    )

    cheapest = min(eligible_winners, key=lambda x: x.best_user_card_net_price) if eligible_winners else None
    highest_trust = max(eligible_winners, key=lambda x: x.peace_of_mind_score) if eligible_winners else None
    overall_best = eligible_winners[0] if eligible_winners else None
    if eligible_winners:
        overall_best = max(eligible_winners, key=lambda x: x.overall_score)

    for s in stores:
        badges: list[str] = []
        if s.is_current_store:
            badges.append("Current Page")
        if not s.in_stock:
            badges.append("Out of Stock")
        if s.store_bucket == "variant_mismatch":
            badges.append("May Differ: " + ", ".join(s.variant_diff_badges[:2]) if s.variant_diff_badges else "Unconfirmed Variant")
        else:
            if overall_best is not None and s == overall_best:
                badges.append("Overall Best Choice")
                s.is_overall_winner = True
            if cheapest is not None and s == cheapest:
                badges.append("Lowest Net Price")
            if highest_trust is not None and s == highest_trust and s != cheapest:
                badges.append("Official & High Peace of Mind")

        s.rank_badge = " · ".join(badges) or "Verified Store"

    return stores
