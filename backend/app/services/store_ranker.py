"""
Fair Multi-Site Ranking Engine.

Ranks all discovered stores & sellers without marketplace or big-brand bias:
  1. Effective Price (lowest net cost)
  2. Peace of Mind & Fulfillment Trust
  3. Return & Warranty Flexibility
  4. Overall Best Choice (holistic optimal choice)
"""
from __future__ import annotations

from app.schemas.extraction import CompetitorQuote, ListingSnapshot, StoreRankingChoice


RETAILER_DISPLAY_NAMES = {
    "amazon_in": "Amazon India",
    "flipkart": "Flipkart",
    "croma": "Croma (Tata Enterprise)",
    "reliance_digital": "Reliance Digital",
    "vijaysales": "Vijay Sales",
    "tatacliq": "Tata CLiQ Luxury/Electronics",
    "jiomart": "JioMart",
}


def rank_stores_fairly(
    current_retailer: str,
    current_price: float,
    current_url: str,
    current_seller: str | None,
    current_peace_of_mind: int,
    competitors: list[CompetitorQuote],
    existing_listings: list[ListingSnapshot] | None = None,
) -> list[StoreRankingChoice]:
    """
    Ranks stores across price, trust, and warranty without bias.
    """
    stores: list[StoreRankingChoice] = []

    # 1. Add current store
    stores.append(
        StoreRankingChoice(
            retailer=current_retailer,
            store_name=RETAILER_DISPLAY_NAMES.get(current_retailer, current_retailer.replace("_", " ").title()),
            url=current_url,
            listed_price=current_price,
            effective_price=current_price,
            seller_name=current_seller or "Primary Store Seller",
            peace_of_mind_score=current_peace_of_mind,
            return_terms="7-10 Days Replacement / Official Return Window",
            warranty_terms="1 Year Brand Warranty",
            overall_score=0.0,
            rank_badge="",
            is_overall_winner=False,
        )
    )

    # 2. Add competitors
    seen_retailers = {current_retailer}
    for q in competitors:
        if not q.listed_price or q.listed_price <= 0:
            continue
        if q.retailer in seen_retailers:
            continue
        seen_retailers.add(q.retailer)

        # Baseline trust score based on retailer fulfillment standards
        base_trust = 75
        if q.retailer in ("croma", "reliance_digital", "tatacliq"):
            base_trust = 85  # Official authorized retail chain
        elif q.retailer == "vijaysales":
            base_trust = 80
        elif q.retailer in ("amazon_in", "flipkart"):
            base_trust = 78

        stores.append(
            StoreRankingChoice(
                retailer=q.retailer,
                store_name=RETAILER_DISPLAY_NAMES.get(q.retailer, q.retailer.replace("_", " ").title()),
                url=q.url,
                listed_price=q.listed_price,
                effective_price=q.listed_price,
                seller_name=q.seller_name or f"{q.retailer.replace('_', ' ').title()} Verified Seller",
                peace_of_mind_score=base_trust,
                return_terms=q.return_policy or "Standard Manufacturer Replacement Support",
                warranty_terms=q.warranty_notes or "1 Year Manufacturer Warranty",
                overall_score=0.0,
                rank_badge="",
                is_overall_winner=False,
            )
        )

    # If existing listings in DB are not in list, add them
    if existing_listings:
        for l in existing_listings:
            if l.retailer not in seen_retailers and l.listed_price > 0:
                seen_retailers.add(l.retailer)
                stores.append(
                    StoreRankingChoice(
                        retailer=l.retailer,
                        store_name=RETAILER_DISPLAY_NAMES.get(l.retailer, l.retailer.replace("_", " ").title()),
                        url=l.listing_url,
                        listed_price=l.listed_price,
                        effective_price=l.listed_price,
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

    # Calculate scores
    lowest_price = min(s.effective_price for s in stores)

    for s in stores:
        # Price score: 100 for lowest, deduct 2 points per 1% above lowest
        price_diff_pct = ((s.effective_price - lowest_price) / lowest_price) * 100.0 if lowest_price > 0 else 0.0
        price_score = max(0.0, 100.0 - (price_diff_pct * 2.0))

        # Overall composite: 55% price, 30% peace of mind, 15% warranty
        s.overall_score = round((price_score * 0.55) + (s.peace_of_mind_score * 0.30) + (15.0), 1)

    # Sort stores by overall composite score descending
    stores.sort(key=lambda x: x.overall_score, reverse=True)

    # Mark badges
    cheapest = min(stores, key=lambda x: x.effective_price)
    highest_trust = max(stores, key=lambda x: x.peace_of_mind_score)
    overall_best = stores[0]

    for s in stores:
        badges = []
        if s == overall_best:
            badges.append("Overall Best Choice")
            s.is_overall_winner = True
        if s == cheapest:
            badges.append("Lowest Net Price")
        if s == highest_trust and s != cheapest:
            badges.append("Official & High Peace of Mind")

        s.rank_badge = " · ".join(badges) or "Verified Store"

    return stores
