"""
Fair Multi-Site Ranking Engine.

Ranks all discovered transactional stores & sellers without marketplace bias:
  - Strictly blocks price trackers, review portals, coupon sites, and forums.
  - Never duplicates the active tab's store with a conflicting price (`is_current_store=True`).
  - Compares EVERY store on the exact same two metrics:
      1. `checkout_cash_price` (Listed Price - Verified Instant Bank/Coupon + Mandatory Fees)
      2. `best_user_card_net_price` (after verified rewards for the user's selected cards)
"""
from __future__ import annotations

from app.schemas.extraction import CompetitorQuote, ListingSnapshot, StoreRankingChoice, VerifiedPaymentRule
from app.services.card_optimizer import CardLike
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
        if r.verification_status in ("page_verified", "live_tc_verified")
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
) -> list[StoreRankingChoice]:
    """
    Ranks transactional stores across uniform metrics (checkout_cash_price and
    best_user_card_net_price), peace of mind, and warranty without duplicates.
    """
    stores: list[StoreRankingChoice] = []
    seen_retailers: set[str] = set()
    seen_domains: set[str] = set()

    cur_ret_norm = (current_retailer or "store").lower().strip()
    cur_dom = extract_domain(current_url) if current_url else ""

    # 1. Add active tab's current store ONCE, clearly marked is_current_store=True
    if current_listed_price > 0 and not (current_url and is_blocked_non_retailer_domain(current_url)):
        seen_retailers.add(cur_ret_norm)
        if cur_dom:
            seen_domains.add(cur_dom)
        stores.append(
            StoreRankingChoice(
                retailer=cur_ret_norm,
                store_name=RETAILER_DISPLAY_NAMES.get(cur_ret_norm, cur_ret_norm.replace("_", " ").title()),
                url=current_url,
                listed_price=round(current_listed_price, 2),
                checkout_cash_price=round(current_checkout_cash_price, 2),
                best_user_card_net_price=round(current_best_user_card_net_price, 2),
                effective_price=round(current_best_user_card_net_price, 2),
                is_current_store=True,
                seller_name=current_seller or "Primary Store Seller",
                peace_of_mind_score=current_peace_of_mind,
                return_terms="7-10 Days Replacement / Official Return Window",
                warranty_terms="1 Year Brand Warranty",
                overall_score=0.0,
                rank_badge="",
                is_overall_winner=False,
            )
        )

    # 2. Add verified transactional competitors (skip blocked domains & active store)
    for q in competitors or []:
        if not q.listed_price or q.listed_price <= 0:
            continue
        if is_blocked_non_retailer_domain(q.url) or is_blocked_non_retailer_domain(q.retailer):
            continue
        ret_norm = (q.retailer or "").lower().strip()
        dom = extract_domain(q.url) if q.url else ""
        if ret_norm in seen_retailers or (dom and dom in seen_domains):
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

        comp_checkout_cash = round(q.listed_price + max(0.0, q.delivery_fee or 0.0), 2)
        comp_best_net = _compute_store_best_user_card_net(comp_checkout_cash, user_cards, verified_rules)

        stores.append(
            StoreRankingChoice(
                retailer=ret_norm,
                store_name=RETAILER_DISPLAY_NAMES.get(ret_norm, ret_norm.replace("_", " ").title()),
                url=q.url,
                listed_price=round(q.listed_price, 2),
                checkout_cash_price=comp_checkout_cash,
                best_user_card_net_price=comp_best_net,
                effective_price=comp_best_net,
                is_current_store=False,
                seller_name=q.seller_name or f"{ret_norm.replace('_', ' ').title()} Verified Seller",
                peace_of_mind_score=base_trust,
                return_terms=q.return_policy or "Standard Manufacturer Replacement Support",
                warranty_terms=q.warranty_notes or "1 Year Manufacturer Warranty",
                overall_score=0.0,
                rank_badge="",
                is_overall_winner=False,
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
                is_current_store=False,
                seller_name=l.seller_name or "Verified Seller",
                peace_of_mind_score=l.trust_score or 75,
                return_terms="Standard Replacement Policy",
                warranty_terms="1 Year Manufacturer Warranty",
                overall_score=0.0,
                rank_badge="",
                is_overall_winner=False,
            )
        )

    if not stores:
        return []

    lowest_price = min(s.best_user_card_net_price for s in stores)

    for s in stores:
        price_diff_pct = (
            ((s.best_user_card_net_price - lowest_price) / lowest_price) * 100.0
            if lowest_price > 0
            else 0.0
        )
        price_score = max(0.0, 100.0 - (price_diff_pct * 2.0))
        s.overall_score = round((price_score * 0.55) + (s.peace_of_mind_score * 0.30) + 15.0, 1)

    stores.sort(key=lambda x: x.overall_score, reverse=True)

    cheapest = min(stores, key=lambda x: x.best_user_card_net_price)
    highest_trust = max(stores, key=lambda x: x.peace_of_mind_score)
    overall_best = stores[0]

    for s in stores:
        badges = []
        if s.is_current_store:
            badges.append("Current Page")
        if s == overall_best:
            badges.append("Overall Best Choice")
            s.is_overall_winner = True
        if s == cheapest:
            badges.append("Lowest Net Price")
        if s == highest_trust and s != cheapest:
            badges.append("Official & High Peace of Mind")

        s.rank_badge = " · ".join(badges) or "Verified Store"

    return stores
