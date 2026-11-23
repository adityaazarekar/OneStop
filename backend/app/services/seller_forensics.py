"""
Seller forensics and trust scoring.

Transparent 0-100 trust_score based on:
  - Authorized seller status (+20 to +40)
  - Seller rating (+0 to +10)
  - Review scam signals (0 to -40)
  - Fake MRP hike detection (-10)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authorized_seller import AuthorizedSeller
from app.schemas.extraction import (
    DynamicSellerInfo,
    MultiSellerReport,
    ReviewScamSignals,
    SellerAnalysisItem,
)


@dataclass
class TrustReport:
    trust_score: int  # 0-100
    seller_name: str
    is_authorized: bool
    trust_tier: str  # official / premium / authorized / unknown
    warnings: list[str] = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)


TIER_BONUS = {"official": 40, "premium": 30, "authorized": 20}


def analyze_dynamic_sellers(
    sellers: list[DynamicSellerInfo],
    review_signals: ReviewScamSignals | None = None,
    retailer: str = "amazon_in",
    current_price: float | None = None,
    current_mrp: float | None = None,
    fallback_seller_name: str | None = None,
    fallback_seller_rating: float | None = None,
) -> MultiSellerReport:
    """
    Live seller fact-check & pros/cons synthesis.
    Does NOT rely on a static hardcoded seller list.
    Evaluates rating, review volume, return friction, fulfillment guarantees, and counterfeit risks.
    """
    seller_items: list[SellerAnalysisItem] = []

    # If no dynamic sellers list provided, use fallback primary seller
    candidate_sellers = list(sellers)
    if not candidate_sellers and fallback_seller_name:
        candidate_sellers.append(
            DynamicSellerInfo(
                seller_name=fallback_seller_name,
                price=current_price,
                rating=fallback_seller_rating,
                fulfillment="Direct / Platform Partner",
                return_policy="Standard Platform Replacement",
                is_buy_box_winner=True,
            )
        )

    for s in candidate_sellers:
        score = 60  # Neutral starting baseline
        pros: list[str] = []
        cons: list[str] = []
        risks: list[str] = []

        # 1. Rating evaluation
        rating = s.rating or fallback_seller_rating
        if rating is not None:
            if rating >= 4.4:
                score += 15
                pros.append(f"Strong customer satisfaction rating ({rating:.1f}★)")
            elif rating >= 4.0:
                score += 5
                pros.append(f"Reliable rating ({rating:.1f}★)")
            elif rating >= 3.6:
                score -= 10
                cons.append(f"Mediocre customer rating ({rating:.1f}★) — verify packaging on delivery")
            else:
                score -= 30
                cons.append(f"Poor seller rating ({rating:.1f}★) — high return rate")
                risks.append("Sub-par fulfillment track record")

        # 2. Fulfillment guarantees
        fulfill = (s.fulfillment or "").lower()
        if any(k in fulfill for k in ("amazon", "fulfilled", "f-assured", "assured", "croma", "reliance")):
            score += 15
            pros.append(f"Platform-backed logistics ({s.fulfillment})")
        elif "merchant" in fulfill or "seller" in fulfill:
            score -= 10
            cons.append("Direct merchant dispatch (potentially slower return turnaround)")

        # 3. Return & Replacement policy
        ret_pol = (s.return_policy or "").lower()
        if "replacement" in ret_pol or "returnable" in ret_pol:
            pros.append(s.return_policy or "Replacement window available")
            score += 10
        elif "non-returnable" in ret_pol:
            score -= 20
            cons.append("Strict non-returnable policy — brand center visit required for any defect")
            risks.append("Zero platform return window")

        # 4. Review scam signals integration
        if review_signals:
            if review_signals.seal_broken_mentions > 0 or review_signals.open_box_mentions > 0:
                score -= 15
                cons.append(f"Recent customer reports of open-box / broken seal units ({review_signals.open_box_mentions + review_signals.seal_broken_mentions} mentions)")
                risks.append("Risk of unsealed / returned unit received")

            if review_signals.fake_product_mentions > 0:
                score -= 25
                cons.append(f"Counterfeit / duplicate allegations ({review_signals.fake_product_mentions} mentions)")
                risks.append("Potential authenticity risk")

            if review_signals.wrong_product_mentions > 0:
                score -= 10
                cons.append(f"Wrong item / variant sent reports ({review_signals.wrong_product_mentions} mentions)")

        # 5. Pricing anomaly / fake MRP check
        price = s.price or current_price
        if price and current_mrp and current_mrp > price:
            disc_pct = (current_mrp - price) / current_mrp * 100.0
            if disc_pct > 65:
                cons.append(f"Artificially inflated MRP (₹{current_mrp:,.0f} with {disc_pct:.0f}% off)")
                score -= 5

        # Clamp score
        peace_of_mind = max(10, min(98, score))

        # Verdict
        if peace_of_mind >= 75:
            verdict = "Recommended"
        elif peace_of_mind >= 50:
            verdict = "Proceed with Caution"
        else:
            verdict = "High Risk"

        risk_summary = "; ".join(risks) if risks else "Low friction; standard manufacturer warranty applies"
        why = f"{verdict} based on rating ({rating or 'N/A'}★) and {s.fulfillment or 'direct fulfillment'}"

        seller_items.append(
            SellerAnalysisItem(
                seller_name=s.seller_name,
                price=price,
                rating=rating,
                review_count=s.review_count,
                verdict=verdict,
                peace_of_mind_score=peace_of_mind,
                is_buy_box_winner=s.is_buy_box_winner,
                pros=pros,
                cons=cons,
                risk_assessment=risk_summary,
                fulfillment_type=s.fulfillment or "Standard Dispatch",
                return_policy=s.return_policy or "7-Day Replacement / Support",
                why_buy_here=why,
            )
        )

    # Pick recommended seller: highest peace_of_mind_score among reasonable prices
    if seller_items:
        recommended = max(seller_items, key=lambda x: (x.peace_of_mind_score, -(x.price or 999999)))
        rec_reason = (
            f"Recommended '{recommended.seller_name}' with {recommended.peace_of_mind_score}/100 Peace of Mind score "
            f"({recommended.fulfillment_type}, {recommended.verdict})."
        )
    else:
        recommended = SellerAnalysisItem(
            seller_name="Official Listing",
            verdict="Recommended",
            peace_of_mind_score=75,
            why_buy_here="Direct retailer listing with standard brand warranty",
        )
        rec_reason = "Direct official retail listing with manufacturer warranty."

    return MultiSellerReport(
        recommended_seller=recommended.seller_name,
        recommended_reason=rec_reason,
        sellers=seller_items,
    )


async def compute_trust_score(
    session: AsyncSession,
    seller_name: str | None,
    retailer: str,
    seller_rating: float | None = None,
    review_signals: ReviewScamSignals | None = None,
    current_price: float | None = None,
    current_mrp: float | None = None,
    historical_median: float | None = None,
) -> TrustReport:
    score = 50  # Neutral baseline
    warnings: list[str] = []
    breakdown: dict = {}

    # ── 1. Authorized seller check ──
    tier = "unknown"
    is_auth = False
    if seller_name:
        stmt = select(AuthorizedSeller).where(
            AuthorizedSeller.retailer == retailer,
        )
        result = await session.execute(stmt)
        auth_sellers = list(result.scalars().all())
        s_clean = seller_name.strip().lower()
        matched_auth = None
        for a in auth_sellers:
            a_clean = a.seller_name.strip().lower()
            if a_clean == s_clean or a_clean in s_clean or s_clean in a_clean:
                matched_auth = a
                break

        if matched_auth:
            is_auth = True
            tier = matched_auth.trust_tier
            bonus = TIER_BONUS.get(tier, 10)
            score += bonus
            breakdown["authorized_seller"] = f"+{bonus} ({tier})"
        else:
            score -= 10
            warnings.append(
                f"⚠️ '{seller_name}' is NOT a known authorized seller on {retailer}. "
                "Warranty claims may be rejected by the brand."
            )
            breakdown["authorized_seller"] = "-10 (unknown seller)"
    else:
        score -= 15
        warnings.append("⚠️ Seller name not available on this listing.")
        breakdown["authorized_seller"] = "-15 (no seller info)"

    # ── 2. Seller rating ──
    if seller_rating is not None:
        if seller_rating >= 4.5:
            score += 10
            breakdown["seller_rating"] = f"+10 ({seller_rating}★)"
        elif seller_rating >= 4.0:
            score += 5
            breakdown["seller_rating"] = f"+5 ({seller_rating}★)"
        elif seller_rating >= 3.5:
            breakdown["seller_rating"] = f"+0 ({seller_rating}★)"
        else:
            score -= 10
            warnings.append(f"⚠️ Low seller rating: {seller_rating}★")
            breakdown["seller_rating"] = f"-10 ({seller_rating}★)"

    # ── 3. Review scam signals ──
    if review_signals:
        impact = review_signals.trust_impact  # -100 to 0
        # Scale to max -40 on trust score
        scaled = max(int(impact * 0.4), -40)
        score += scaled
        if review_signals.top_red_flags:
            warnings.extend(
                [f"🚩 Review flag: {flag}" for flag in review_signals.top_red_flags[:3]]
            )
        breakdown["review_analysis"] = f"{scaled} (trust_impact={impact})"

    # ── 4. Fake MRP hike check ──
    if current_price and current_mrp and current_mrp > 0:
        discount_pct = (current_mrp - current_price) / current_mrp * 100
        if discount_pct > 60:
            score -= 10
            warnings.append(
                f"⚠️ Suspicious MRP: ₹{current_mrp:,.0f} with {discount_pct:.0f}% discount. "
                "MRP may be artificially inflated."
            )
            breakdown["mrp_hike"] = f"-10 ({discount_pct:.0f}% off MRP)"

    if historical_median and current_price and current_price > historical_median * 1.3:
        score -= 5
        warnings.append(
            f"⚠️ Price ₹{current_price:,.0f} is >30% above 90-day median ₹{historical_median:,.0f}."
        )
        breakdown["price_hike"] = "-5 (above 90d median)"

    # Clamp
    score = max(0, min(100, score))

    return TrustReport(
        trust_score=score,
        seller_name=seller_name or "Unknown",
        is_authorized=is_auth,
        trust_tier=tier,
        warnings=warnings,
        breakdown=breakdown,
    )
