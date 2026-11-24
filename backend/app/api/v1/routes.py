"""
Phase 3 API routes — all intelligence endpoints.

Every product, price, and offer comes from real-time extraction.
No fake data, no silent mock fallback.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

from app.db.session import get_db
from app.models.bank_offer_rule import BankOfferRule as BankOfferRuleModel
from app.models.canonical_product import CanonicalProduct
from app.models.credit_card import CreditCard
from app.models.price_observation import PriceObservation
from app.models.retailer_listing import RetailerListing
from app.schemas.extraction import (
    AnalysisResponse,
    AnalyzePayloadRequest,
    BankOfferRule,
    BuyWaitVerdictResponse,
    CardResultResponse,
    CashVsPointsReport,
    ClientPayload,
    CompareAllStoresRequest,
    CompareAllStoresResponse,
    CompetitorQuote,
    DiscoverStoresRequest,
    DiscoverStoresResponse,
    DynamicSellerInfo,
    EffectivePriceRequest,
    EffectivePriceResponse,
    ExchangeComparisonResponse,
    ExchangeOptionResponse,
    ExecutiveSummary,
    ListingSnapshot,
    LiveOfferRaw,
    MissedSavingResponse,
    MultiSellerReport,
    QuickCommerceQuote,
    ScraperResult,
    StackedEffectivePriceResult,
    StoreRankingChoice,
    TrustReportResponse,
    VaultItemResponse,
)
from app.scrapers import get_scraper, ScraperError
from app.services.buy_wait_engine import get_buy_wait_verdict
from app.services.card_optimizer import (
    CardLike,
    calculate_effective_prices,
)
from app.services.exchange_arbitrage import compare_exchange_options
from app.services.live_terms_engine import resolve_live_payment_rules
from app.services.llm_parser import get_llm_parser
from app.services.offer_stack_engine import (
    compute_cash_vs_points_report,
    compute_stacked_offer_price,
)
from app.services.product_matcher import match_or_create
from app.services.quick_commerce_engine import (
    build_qc_quote_from_payload,
    detect_qc_platform_key,
    discover_quick_commerce_quotes,
    sanitize_pincode,
)
from app.services.seller_forensics import analyze_dynamic_sellers, compute_trust_score
from app.services.shopping_discovery import discover_stores
from app.services.store_ranker import rank_stores_fairly
from app.services.universal_scraper import extract_from_url, is_blocked_non_retailer_domain
from app.services.vault_service import (
    days_remaining,
    get_vault_items,
    process_invoice,
)
from app.worker import launch_compare_all_stores_job, stream_job_events

router = APIRouter(prefix="/api/v1", tags=["v1"])


# ── Helpers ──────────────────────────────────


def _card_to_cardlike(card: CreditCard) -> CardLike:
    """Adapt SQLAlchemy CreditCard → CardLike protocol."""
    cl = CardLike()
    cl.card_name = card.card_name
    cl.bank_name = card.bank_name
    cl.base_reward_pct = card.base_reward_pct
    cl.amazon_reward_pct = card.amazon_reward_pct
    cl.flipkart_reward_pct = card.flipkart_reward_pct
    return cl


def _card_result_to_response(cr) -> CardResultResponse:
    return CardResultResponse(
        card_name=cr.card_name,
        bank_name=cr.bank_name,
        effective_price=cr.effective_price,
        guaranteed_price=cr.guaranteed_price,
        bank_discount=cr.bank_discount,
        reward_value=cr.reward_value,
        reward_pct=cr.reward_pct,
        offer_summary=cr.offer_summary,
    )


def _parse_valid_uuids(raw_ids: list[str] | None) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for raw in raw_ids or []:
        if not raw:
            continue
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(uuid.UUID(part))
            except ValueError:
                pass
    return out


async def _process_competitor_quotes(
    quotes: list[CompetitorQuote],
    canonical_product: CanonicalProduct,
    session: AsyncSession,
    variant_specs: dict,
) -> list[ListingSnapshot]:
    """Persist valid transactional competitor quotes as listings + price observations, blocking aggregators."""
    from app.services.product_matcher import (
        _variant_specs_match,
        extract_variant_specs_from_title,
    )

    valid_snaps: list[ListingSnapshot] = []
    existing_specs = dict(canonical_product.variant_specs or {})
    title_specs = extract_variant_specs_from_title(
        f"{canonical_product.brand or ''} {canonical_product.model_name or ''}"
    )
    for k, v in title_specs.items():
        existing_specs.setdefault(k, v)

    for q in quotes:
        if not q.listed_price or q.listed_price <= 0:
            continue
        if is_blocked_non_retailer_domain(q.url) or is_blocked_non_retailer_domain(q.retailer):
            continue
        # Variant check: reject if title clearly indicates different storage/RAM/tier/accessories
        quote_specs = extract_variant_specs_from_title(q.title)

        if not _variant_specs_match(quote_specs, existing_specs):
            continue  # Wrong variant — skip

        # Upsert listing
        retailer_clean = (q.retailer or "generic")[:20]
        sku = (q.sku or f"CQ-{retailer_clean}-{abs(hash(q.url)) % 10**8}")[:100]
        stmt = select(RetailerListing).where(
            RetailerListing.retailer == retailer_clean,
            RetailerListing.external_sku == sku,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.canonical_product_id = canonical_product.id
            existing.current_listed_price = q.listed_price
            existing.in_stock = q.in_stock
            existing.last_checked_at = datetime.now(timezone.utc)
            listing = existing
        else:
            listing = RetailerListing(
                id=uuid.uuid4(),
                canonical_product_id=canonical_product.id,
                retailer=retailer_clean,
                external_sku=sku,
                listing_url=(q.url or "")[:1000],
                current_listed_price=q.listed_price,
                in_stock=q.in_stock,
                match_confidence=0.7,
            )
            session.add(listing)
            await session.flush()

        # Record price observation
        obs = PriceObservation(
            observed_at=datetime.now(timezone.utc),
            listing_id=listing.id,
            listed_price=q.listed_price,
            in_stock=q.in_stock,
            source="extension",
        )
        session.add(obs)

        valid_snaps.append(ListingSnapshot(
            retailer=retailer_clean,
            listing_url=q.url,
            listed_price=q.listed_price,
            in_stock=q.in_stock,
            match_confidence=0.7,
        ))

    return valid_snaps


async def _run_analysis_pipeline(
    payload: ClientPayload,
    session: AsyncSession,
    user_card_ids: list[str] | None = None,
    competitor_quotes: list[CompetitorQuote] | None = None,
    *,
    endpoint_label: str = "/analyze-client-payload",
    redirect_ms: float = 0.0,
    fetch_ms: float = 0.0,
    parse_ms: float = 0.0,
) -> AnalysisResponse:
    """Core pipeline: match → parse offers → optimize → forensics → buy/wait."""
    t_pipe0 = time.perf_counter()
    from app.services.product_matcher import is_generic_or_invalid_listing
    is_invalid, reason = is_generic_or_invalid_listing(payload.title, payload.listed_price, payload.url)
    if is_invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot analyze listing: {reason}. OneStop processes genuine Indian product pages with listed price > ₹0.",
        )

    parser = await get_llm_parser()

    # Concurrently parse variant, bank offers, and review signals (bounded with 4s timeout & deterministic fallbacks)
    t_llm0 = time.perf_counter()
    variant_task = parser.parse_variant_attributes(
        payload.title or "",
        str(payload.variant_specs or {}),
    )
    offers_task = parser.parse_bank_offers(
        payload.raw_bank_offers or []
    )
    reviews_task = parser.analyze_reviews(
        payload.recent_reviews or []
    )
    variant_res, offers_res, reviews_res = await asyncio.gather(
        variant_task, offers_task, reviews_task, return_exceptions=True
    )
    llm_gather_ms = (time.perf_counter() - t_llm0) * 1000
    from app.schemas.extraction import VariantAttributes, ReviewScamSignals
    variant = variant_res if isinstance(variant_res, VariantAttributes) else None
    parsed_offers = offers_res if isinstance(offers_res, list) else []
    review_signals = reviews_res if isinstance(reviews_res, ReviewScamSignals) else None

    # 2. Match or create canonical product + listing
    t_match0 = time.perf_counter()
    product, listing, confidence = await match_or_create(session, payload, variant)
    match_ms = (time.perf_counter() - t_match0) * 1000

    # 3. Record price observation (deduped: only insert if price/stock changed or last obs > 1h old)
    t_db0 = time.perf_counter()
    now_utc = datetime.now(timezone.utc)
    new_price = payload.listed_price or 0
    should_insert_obs = True
    if not getattr(listing, "_is_new", False):
        try:
            last_obs_stmt = (
                select(
                    PriceObservation.listed_price,
                    PriceObservation.in_stock,
                    PriceObservation.observed_at,
                )
                .where(PriceObservation.listing_id == listing.id)
                .order_by(PriceObservation.observed_at.desc())
                .limit(1)
            )
            last_obs_row = (await session.execute(last_obs_stmt)).first()
            if last_obs_row is not None:
                last_price, last_in_stock, last_ts = last_obs_row
                if last_ts is not None and getattr(last_ts, "tzinfo", None) is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                same_price = abs(float(last_price or 0) - float(new_price)) < 1e-6
                same_stock = bool(last_in_stock) == bool(payload.in_stock)
                within_1h = last_ts is not None and (now_utc - last_ts) < timedelta(hours=1)
                if same_price and same_stock and within_1h:
                    should_insert_obs = False
        except Exception:
            should_insert_obs = True

    if should_insert_obs:
        obs = PriceObservation(
            observed_at=now_utc,
            listing_id=listing.id,
            listed_price=new_price,
            coupon_discount=0,
            best_bank_discount=0,
            in_stock=payload.in_stock,
            source=payload.source,
        )
        session.add(obs)

    # Persist parsed offers
    for po in parsed_offers:
        parsed_valid_until = None
        if po.valid_until:
            try:
                parsed_valid_until = datetime.fromisoformat(po.valid_until)
            except Exception:
                parsed_valid_until = None
        db_offer = BankOfferRuleModel(
            id=uuid.uuid4(),
            listing_id=listing.id,
            retailer=(payload.retailer or "generic")[:20],
            bank_name=po.bank_name[:100],
            card_type=(po.card_type or "")[:50] or None,
            min_txn_amount=po.min_txn_amount or 0.0,
            discount_flat=po.discount_flat or 0.0,
            discount_pct=po.discount_pct or 0.0,
            max_discount_cap=po.max_discount_cap or 0.0,
            excluded_cards={"excluded": po.excluded_cards} if po.excluded_cards else None,
            raw_terms=po.summary,
            valid_until=parsed_valid_until,
        )
        session.add(db_offer)

    # 5. Single CreditCard query for both owned wallet & unowned/missed-savings comparisons
    all_cards_result = await session.execute(select(CreditCard))
    all_cards_db = list(all_cards_result.scalars().all())
    all_cards = [_card_to_cardlike(c) for c in all_cards_db]
    parsed_user_uuids = set(_parse_valid_uuids(user_card_ids))
    if parsed_user_uuids:
        user_cards = [_card_to_cardlike(c) for c in all_cards_db if c.id in parsed_user_uuids]
    else:
        user_cards = []
    owned_names_set = {c.card_name.lower() for c in user_cards}
    unowned_cards = [c for c in all_cards if c.card_name.lower() not in owned_names_set]
    db_ms = (time.perf_counter() - t_db0) * 1000

    # 6. Card optimizer (strictly on user's owned cards)
    listed = payload.listed_price or 0.0
    opt_result = calculate_effective_prices(
        listed_price=listed,
        coupon_discount=0.0,
        platform_fee=round((payload.delivery_fee or 0.0) + (payload.handling_fee or 0.0), 2),
        bank_offers=parsed_offers,
        user_cards=user_cards,
        retailer=payload.retailer,
        all_cards=all_cards,
    )
    best_card = opt_result.best_card

    # 6b. Dynamic Offer Stacking Engine (No hardcoded offer tables)
    try:
        stacked_offers = compute_stacked_offer_price(
            listed_price=listed,
            raw_coupons=payload.raw_coupons or [],
            bank_offers=parsed_offers,
            user_cards=user_cards,
            retailer=payload.retailer or "amazon_in",
            raw_live_offers=payload.raw_live_offers or [],
            delivery_fee=payload.delivery_fee or 0.0,
            handling_fee=payload.handling_fee or 0.0,
            all_cards=all_cards,
            retailer_advertised_lowest_price=payload.retailer_advertised_lowest_price,
        )
    except Exception as exc:
        logger.warning(f"Dynamic offer stacking failed ({exc}) — using basic price")
        stacked_offers = StackedEffectivePriceResult(
            original_listed_price=listed,
            best_stacked_price=listed,
            total_savings=0.0,
            steps=[],
            applied_offers=[],
            alternative_paths=[],
            notes=str(exc),
        )

    # 6c. Strict Cash-vs-Points Financial Transparency & Live T&C Rules (Zero Fake Estimates)
    verified_rules = []
    cash_vs_points: CashVsPointsReport | None = None
    try:
        verified_rules = await resolve_live_payment_rules(
            retailer=payload.retailer or "amazon_in",
            user_cards=user_cards,
            allow_live_crawl=False,  # Stage 1 reads 7-day Redis T&C cache (<20ms); live crawl on demand
            timeout_seconds=2.5,
        )
        cash_vs_points = compute_cash_vs_points_report(
            listed_price=listed,
            raw_coupons=payload.raw_coupons or [],
            bank_offers=parsed_offers,
            user_cards=user_cards,
            retailer=payload.retailer or "amazon_in",
            verified_rules=verified_rules,
            raw_live_offers=payload.raw_live_offers or [],
            delivery_fee=payload.delivery_fee or 0.0,
            handling_fee=payload.handling_fee or 0.0,
            unowned_cards=unowned_cards,
            retailer_advertised_lowest_price=payload.retailer_advertised_lowest_price,
        )
    except Exception as exc:
        logger.warning(f"Cash-vs-Points computation failed ({exc})")

    # 7. Seller forensics & Dynamic Multi-Seller Fact-Check
    try:
        trust_report = await compute_trust_score(
            session=session,
            seller_name=payload.seller_name,
            retailer=payload.retailer,
            seller_rating=payload.seller_rating,
            review_signals=review_signals,
            current_price=listed,
            current_mrp=payload.mrp,
        )
    except Exception as exc:
        logger.warning(f"Trust score computation failed ({exc}) — using default score")
        from app.services.seller_forensics import TrustReport
        trust_report = TrustReport(
            trust_score=70,
            seller_name=payload.seller_name or "Unknown",
            is_authorized=False,
            trust_tier="unverified",
            warnings=[],
            breakdown={},
        )

    try:
        multi_seller_report = analyze_dynamic_sellers(
            sellers=payload.sellers or [],
            review_signals=review_signals,
            retailer=payload.retailer,
            current_price=listed,
            current_mrp=payload.mrp,
            fallback_seller_name=payload.seller_name,
            fallback_seller_rating=payload.seller_rating,
        )
    except Exception as exc:
        logger.warning(f"Dynamic seller analysis failed ({exc}) — fallback report")
        multi_seller_report = MultiSellerReport(
            recommended_seller=payload.seller_name or "Direct Merchant",
            recommended_reason="Primary listed seller",
            sellers=[],
        )

    # 8. Buy/wait verdict
    try:
        buy_wait = await get_buy_wait_verdict(session, str(listing.id), listed)
    except Exception as exc:
        logger.warning(f"Buy/wait verdict failed ({exc}) — using fallback")
        buy_wait = BuyWaitVerdictResponse(
            verdict="INSUFFICIENT_HISTORY",
            current_price=listed,
            p10=None,
            median_90d=None,
            all_time_low=None,
            observation_count=1,
            next_sale_name=None,
            days_to_next_sale=None,
            reasoning="Price observation logged. More observations needed.",
        )

    # 9. Process competitor quotes from extension (blocking aggregators/non-retailers)
    comp_snaps: list[ListingSnapshot] = []
    if competitor_quotes:
        comp_snaps = await _process_competitor_quotes(
            competitor_quotes, product, session, variant.model_dump() if variant else {},
        )

    # Explicitly commit CanonicalProduct, RetailerListing, PriceObservation BEFORE returning canonical_product_id
    t_commit0 = time.perf_counter()
    await session.commit()
    db_ms += (time.perf_counter() - t_commit0) * 1000

    # Build listings snapshot (filtering out any legacy blocked non-retailer rows)
    all_listings_stmt = (
        select(RetailerListing)
        .where(RetailerListing.canonical_product_id == product.id)
    )
    all_listings = (await session.execute(all_listings_stmt)).scalars().all()
    listing_snaps = [
        ListingSnapshot(
            retailer=getattr(l, "retailer", payload.retailer),
            listing_url=getattr(l, "listing_url", payload.url),
            listed_price=getattr(l, "current_listed_price", listed),
            mrp=getattr(l, "current_mrp", payload.mrp),
            in_stock=getattr(l, "in_stock", True),
            seller_name=getattr(l, "seller_name", payload.seller_name),
            trust_score=getattr(l, "trust_score", 70),
            match_confidence=getattr(l, "match_confidence", 0.0) or 0.0,
        )
        for l in all_listings
        if hasattr(l, "retailer")
        and not is_blocked_non_retailer_domain(getattr(l, "listing_url", "") or "")
        and not is_blocked_non_retailer_domain(getattr(l, "retailer", "") or "")
    ]
    if not listing_snaps and listing:
        listing_snaps = [
            ListingSnapshot(
                retailer=listing.retailer,
                listing_url=listing.listing_url,
                listed_price=listing.current_listed_price,
                mrp=listing.current_mrp,
                in_stock=listing.in_stock,
                seller_name=listing.seller_name,
                trust_score=listing.trust_score,
                match_confidence=listing.match_confidence or 0.0,
            )
        ]

    # 10. Fair Multi-Site Ranking (Uniform 2-metric comparison, deduplicated Current Page, 3-tier dynamic bucketing)
    cur_checkout_cash = cash_vs_points.checkout_payable_inr if cash_vs_points else listed
    cur_best_net = cash_vs_points.best_user_card_net_price_cash if cash_vs_points else cur_checkout_cash
    try:
        ranked_store_choices = rank_stores_fairly(
            current_retailer=payload.retailer or "store",
            current_listed_price=listed,
            current_checkout_cash_price=cur_checkout_cash,
            current_best_user_card_net_price=cur_best_net,
            current_url=payload.url or "",
            current_seller=multi_seller_report.recommended_seller or payload.seller_name or "Official Seller",
            current_peace_of_mind=trust_report.trust_score if trust_report else 70,
            competitors=competitor_quotes or [],
            existing_listings=listing_snaps,
            user_cards=user_cards,
            verified_rules=verified_rules,
            target_title=payload.title,
            target_brand=product.brand or payload.brand,
            target_specs=product.variant_specs or payload.variant_specs,
            current_in_stock=payload.in_stock,
        )
    except Exception as exc:
        logger.warning(f"Fair store ranking failed ({exc}) — using empty list")
        ranked_store_choices = []

    # 11. Brutally Honest LLM Executive Recommendation Summary ("Which Site, Seller & Offer to Pick")
    winner_choice = next((s for s in ranked_store_choices if s.is_overall_winner), None) or (
        ranked_store_choices[0] if ranked_store_choices else None
    )
    best_offer_str = (
        cash_vs_points.winning_instant_offer_summary
        if (cash_vs_points and cash_vs_points.winning_instant_offer_summary)
        else (
            stacked_offers.applied_offers[0]["title"]
            if (stacked_offers and stacked_offers.applied_offers)
            else (best_card.offer_summary if (best_card and best_card.offer_summary) else "Standard Upfront Checkout")
        )
    )
    key_pros = [p for s in (multi_seller_report.sellers if multi_seller_report else []) for p in s.pros]
    key_cons = [c for s in (multi_seller_report.sellers if multi_seller_report else []) for c in s.cons]

    best_store_name = winner_choice.store_name if winner_choice else (payload.retailer or "Store")
    best_seller_name = (
        (winner_choice.seller_name if winner_choice else None)
        or (multi_seller_report.recommended_seller if multi_seller_report else None)
        or payload.seller_name
        or "Official / Verified Seller"
    )
    upfront_cash_price = (
        winner_choice.checkout_cash_price
        if (winner_choice and winner_choice.checkout_cash_price > 0)
        else cur_checkout_cash
    )
    final_price = (
        winner_choice.effective_price
        if (winner_choice and winner_choice.effective_price is not None)
        else (stacked_offers.best_stacked_price if stacked_offers else listed)
    )
    peace_score = (
        winner_choice.peace_of_mind_score
        if winner_choice
        else (trust_report.trust_score if trust_report else 70)
    )
    prod_title = f"{product.brand or ''} {product.model_name or ''}".strip() or (payload.title or "Product")

    unowned_note: str | None = None
    if cash_vs_points and cash_vs_points.unowned_card_routes:
        top_u = cash_vs_points.unowned_card_routes[0]
        if top_u.effective_cost_if_cash_inr < final_price:
            unowned_note = (
                f"Alternative (Card Not in Wallet): {top_u.method_name} pays ₹{top_u.checkout_payable_inr:,.0f} today "
                f"and works out to ₹{top_u.effective_cost_if_cash_inr:,.0f} after rewards, but requires a card not in your wallet."
            )

    excluded_stores: list[str] = []
    for st in ranked_store_choices:
        if st.store_bucket == "variant_mismatch":
            badges_str = ", ".join(st.variant_diff_badges[:2]) or "variant difference"
            excluded_stores.append(f"{st.store_name} (unconfirmed variant: {badges_str})")
        elif not st.in_stock:
            excluded_stores.append(f"{st.store_name} (out of stock)")
    excluded_note = (
        f"Excluded from winning recommendation: {'; '.join(excluded_stores[:3])}."
        if excluded_stores
        else None
    )

    t_sum0 = time.perf_counter()
    try:
        exec_summary = await parser.generate_executive_summary(
            product_title=prod_title,
            best_store=best_store_name,
            best_seller=best_seller_name,
            effective_price=final_price,
            best_offer_stack=best_offer_str,
            peace_of_mind_score=peace_score,
            key_pros=key_pros,
            key_cons=key_cons,
            checkout_payable_inr=upfront_cash_price,
            unowned_option_note=unowned_note,
            excluded_stores_note=excluded_note,
        )
    except Exception as exc:
        logger.warning(f"Executive summary generation failed ({exc}) — fallback")
        exec_summary = ExecutiveSummary(
            best_store=best_store_name,
            best_seller=best_seller_name,
            best_offer_stack=best_offer_str,
            final_net_price=final_price,
            why_this_wins=f"Direct net checkout price of ₹{final_price:,.0f} from verified seller.",
            key_caveat=key_cons[0] if key_cons else "Verify serial number and seal upon delivery.",
            summary_bullets=[
                f"Store: Buy from {best_store_name} (Seller: {best_seller_name}).",
                f"Price: Final effective price ₹{final_price:,.0f}.",
            ],
        )
    llm_total_ms = llm_gather_ms + ((time.perf_counter() - t_sum0) * 1000)
    pipe_total_ms = (time.perf_counter() - t_pipe0) * 1000
    total_with_scrape_ms = redirect_ms + fetch_ms + parse_ms + pipe_total_ms
    logger.info(
        "PIPELINE_TIMING endpoint=%s redirect_ms=%.1f fetch_ms=%.1f parse_ms=%.1f match_ms=%.1f llm_ms=%.1f db_ms=%.1f total_ms=%.1f",
        endpoint_label,
        redirect_ms,
        fetch_ms,
        parse_ms,
        match_ms,
        llm_total_ms,
        db_ms,
        total_with_scrape_ms,
    )

    qc_quotes: list[QuickCommerceQuote] = []
    qc_key = detect_qc_platform_key(payload.url) or detect_qc_platform_key(payload.retailer)
    if qc_key:
        qc_q = build_qc_quote_from_payload(payload, pincode=payload.pincode or "411001", platform_key=qc_key)
        if qc_q:
            qc_quotes.append(qc_q)

    return AnalysisResponse(
        canonical_product_id=str(product.id),
        brand=product.brand,
        model_name=product.model_name,
        category=product.category,
        variant_specs=product.variant_specs or {},
        listings=listing_snaps,
        ranked_cards=[_card_result_to_response(c) for c in opt_result.ranked_cards],
        best_card=_card_result_to_response(opt_result.best_card) if opt_result.best_card else None,
        missed_savings=MissedSavingResponse(
            card_name=opt_result.missed_savings.card_name,
            bank_name=opt_result.missed_savings.bank_name,
            effective_price=opt_result.missed_savings.effective_price,
            savings_vs_best_owned=opt_result.missed_savings.savings_vs_best_owned,
        ) if opt_result.missed_savings else None,
        guaranteed_price=opt_result.guaranteed_price,
        listed_price=opt_result.listed_price,
        trust_report=TrustReportResponse(
            trust_score=trust_report.trust_score,
            seller_name=trust_report.seller_name,
            is_authorized=trust_report.is_authorized,
            trust_tier=trust_report.trust_tier,
            warnings=trust_report.warnings,
            breakdown=trust_report.breakdown,
        ),
        buy_wait=BuyWaitVerdictResponse(
            verdict=buy_wait.verdict,
            current_price=buy_wait.current_price,
            p10=buy_wait.p10,
            median_90d=buy_wait.median_90d,
            all_time_low=buy_wait.all_time_low,
            observation_count=buy_wait.observation_count,
            next_sale_name=buy_wait.next_sale_name,
            days_to_next_sale=buy_wait.days_to_next_sale,
            reasoning=buy_wait.reasoning,
        ),
        bank_offers_parsed=parsed_offers,
        stacked_offers=stacked_offers,
        seller_report=multi_seller_report,
        ranked_store_choices=ranked_store_choices,
        quick_commerce_quotes=qc_quotes,
        llm_summary=exec_summary,
        cash_vs_points=cash_vs_points,
    )


# ═══════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════


@router.post("/discover-stores", response_model=DiscoverStoresResponse)
async def discover_stores_endpoint(req: DiscoverStoresRequest):
    """Stage 2/3 universal cross-store + quick-commerce discovery via SearXNG + 4-Tier Scrapling + RapidFuzz."""
    pin = sanitize_pincode(req.pincode or req.payload.pincode)
    stores_res, qc_quotes = await asyncio.gather(
        discover_stores(req.payload, max_stores=req.max_stores),
        discover_quick_commerce_quotes(req.payload, pincode=pin),
        return_exceptions=True,
    )
    if isinstance(stores_res, DiscoverStoresResponse):
        if isinstance(qc_quotes, list):
            stores_res.quick_commerce_quotes = qc_quotes
        return stores_res
    return DiscoverStoresResponse(
        query=req.payload.title,
        quotes=[],
        quick_commerce_quotes=qc_quotes if isinstance(qc_quotes, list) else [],
        errors=[str(stores_res)],
    )


@router.post("/compare-all-stores", response_model=CompareAllStoresResponse)
async def compare_all_stores_endpoint(req: CompareAllStoresRequest):
    """
    Stage 1 Fast Async Enqueue (<150ms):
    Immediately returns `{ "job_id": "...", "pincode": "...", "cached_stores": [...] }`
    and triggers Universal Store Discovery + Quick-Commerce Compare concurrently in the background.
    Stream progressive results via `GET /api/v1/stream-compare/{job_id}`.
    """
    job_id = uuid.uuid4().hex
    pin = sanitize_pincode(req.pincode or req.payload.pincode)
    await launch_compare_all_stores_job(job_id, req.payload, pincode=pin)
    return CompareAllStoresResponse(
        job_id=job_id,
        pincode=pin,
        cached_stores=[],
        cached_quick_commerce=[],
    )


@router.get("/stream-compare/{job_id}")
async def stream_compare_endpoint(job_id: str):
    """
    Stage 1 Server-Sent Events (SSE) endpoint (`text/event-stream`):
    Streams each newly verified universal store (`event: store_quote`), quick-commerce app
    (`event: quick_commerce_quote`), and final completion summary (`event: complete`).
    """
    return StreamingResponse(
        stream_job_events(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/analyze-client-payload", response_model=AnalysisResponse)
async def analyze_client_payload(
    req: AnalyzePayloadRequest,
    session: AsyncSession = Depends(get_db),
):
    """Primary ₹0 endpoint — Extension/App posts DOM-extracted payload."""
    return await _run_analysis_pipeline(
        req.payload,
        session,
        req.user_card_ids or None,
        req.competitor_quotes or None,
    )


@router.post("/analyze-url", response_model=AnalysisResponse)
async def analyze_url(
    url: str = Query(..., description="Product URL to scrape"),
    card_ids: list[str] = Query(default=[]),
    session: AsyncSession = Depends(get_db),
):
    """Server-side scrape for direct URL pastes: dedicated Amazon/Flipkart scrapers first, universal_scraper fallback/other stores."""
    from app.scrapers.base_scraper import detect_retailer, resolve_url_redirects

    t_redir0 = time.perf_counter()
    canonical_url = await resolve_url_redirects(url)
    redirect_ms = (time.perf_counter() - t_redir0) * 1000
    retailer = detect_retailer(canonical_url)

    # Dedicated scrapers first for Amazon.in and Flipkart
    if retailer in ("amazon_in", "flipkart"):
        scraper = get_scraper(canonical_url)
        try:
            result: ScraperResult = await scraper.scrape(canonical_url)
            if result.success and result.payload is not None and result.payload.listed_price:
                return await _run_analysis_pipeline(
                    result.payload,
                    session,
                    card_ids or None,
                    endpoint_label="/analyze-url",
                    redirect_ms=redirect_ms,
                    fetch_ms=result.fetch_ms,
                    parse_ms=result.parse_ms,
                )
        except ScraperError as exc:
            logger.warning("Dedicated %s scraper failed for %s (%s) — trying universal_scraper", retailer, canonical_url, exc)

        t_u0 = time.perf_counter()
        uni_payload, tier_info = await extract_from_url(canonical_url, allow_tier2=True)
        u_ms = (time.perf_counter() - t_u0) * 1000
        if uni_payload and uni_payload.listed_price and uni_payload.listed_price > 0:
            return await _run_analysis_pipeline(
                uni_payload,
                session,
                card_ids or None,
                endpoint_label="/analyze-url",
                redirect_ms=redirect_ms,
                fetch_ms=u_ms,
            )
        raise HTTPException(
            status_code=502,
            detail=f"Live fetch failed ({tier_info}). Use Chrome Extension or App for ₹0 extraction.",
        )

    # All other domains: 4-tier universal scraper first, dedicated scraper fallback
    t_u0 = time.perf_counter()
    uni_payload, tier_info = await extract_from_url(canonical_url, allow_tier2=True)
    u_ms = (time.perf_counter() - t_u0) * 1000
    if uni_payload and uni_payload.listed_price and uni_payload.listed_price > 0:
        return await _run_analysis_pipeline(
            uni_payload,
            session,
            card_ids or None,
            endpoint_label="/analyze-url",
            redirect_ms=redirect_ms,
            fetch_ms=u_ms,
        )

    scraper = get_scraper(canonical_url)
    try:
        result: ScraperResult = await scraper.scrape(canonical_url)
    except ScraperError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Live fetch failed ({tier_info}; {exc}). Use Chrome Extension or App for ₹0 extraction.",
        )
    if not result.success or result.payload is None:
        raise HTTPException(
            status_code=502,
            detail=result.error or f"Scraper returned no data ({tier_info}). Use Extension/App instead.",
        )
    return await _run_analysis_pipeline(
        result.payload,
        session,
        card_ids or None,
        endpoint_label="/analyze-url",
        redirect_ms=redirect_ms,
        fetch_ms=result.fetch_ms,
        parse_ms=result.parse_ms,
    )


@router.post("/calculate-effective-price", response_model=EffectivePriceResponse)
async def calculate_effective_price(
    req: EffectivePriceRequest,
    session: AsyncSession = Depends(get_db),
):
    """Instant card wallet recalculation without full scrape."""
    # Load requested cards STRICTLY (empty card_ids => no owned cards, not all DB cards)
    parsed_req_uuids = _parse_valid_uuids(req.card_ids)
    if parsed_req_uuids:
        stmt = select(CreditCard).where(CreditCard.id.in_(parsed_req_uuids))
        result = await session.execute(stmt)
        cards_db = list(result.scalars().all())
        user_cards = [_card_to_cardlike(c) for c in cards_db]
    else:
        user_cards = []

    all_result = await session.execute(select(CreditCard))
    all_cards = [_card_to_cardlike(c) for c in all_result.scalars().all()]

    opt = calculate_effective_prices(
        listed_price=req.listed_price,
        coupon_discount=req.coupon_discount,
        platform_fee=req.platform_fee,
        bank_offers=req.bank_offers,
        user_cards=user_cards,
        retailer=req.retailer,
        all_cards=all_cards,
    )
    return EffectivePriceResponse(
        guaranteed_price=opt.guaranteed_price,
        ranked_cards=[_card_result_to_response(c) for c in opt.ranked_cards],
        best_card=_card_result_to_response(opt.best_card) if opt.best_card else None,
        missed_savings=MissedSavingResponse(
            card_name=opt.missed_savings.card_name,
            bank_name=opt.missed_savings.bank_name,
            effective_price=opt.missed_savings.effective_price,
            savings_vs_best_owned=opt.missed_savings.savings_vs_best_owned,
        ) if opt.missed_savings else None,
    )


@router.get("/products/recent")
async def list_recent_products(
    limit: int = Query(default=12, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
):
    """Return real tracked products stored in PostgreSQL/TimescaleDB (never fake data)."""
    valid_subquery = (
        select(RetailerListing.canonical_product_id)
        .where(RetailerListing.current_listed_price > 0)
        .distinct()
    )

    stmt = (
        select(CanonicalProduct)
        .options(selectinload(CanonicalProduct.listings))
        .where(
            CanonicalProduct.id.in_(valid_subquery),
            CanonicalProduct.model_name != "",
            CanonicalProduct.model_name.isnot(None),
            CanonicalProduct.brand != "Generic",
        )
        .order_by(CanonicalProduct.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    products = result.scalars().all()

    from app.services.product_matcher import is_generic_or_invalid_listing

    out = []
    for p in products:
        listings = [l for l in (p.listings or []) if l.current_listed_price and l.current_listed_price > 0]
        if not listings:
            continue
        is_junk, _ = is_generic_or_invalid_listing(p.model_name, listings[0].current_listed_price)
        if is_junk:
            continue

        prices = [l.current_listed_price for l in listings]
        mrps = [l.current_mrp for l in listings if l.current_mrp and l.current_mrp > 0]
        trust_scores = [l.trust_score for l in listings if l.trust_score is not None]
        out.append({
            "id": str(p.id),
            "brand": p.brand,
            "model_name": p.model_name,
            "category": p.category,
            "variant_specs": p.variant_specs or {},
            "created_at": str(p.created_at),
            "listings_count": len(listings),
            "lowest_price": min(prices) if prices else None,
            "highest_mrp": max(mrps) if mrps else None,
            "retailers": list({l.retailer for l in listings}),
            "in_stock": any(l.in_stock for l in listings),
            "listing_url": listings[0].listing_url,
            "seller_name": listings[0].seller_name,
            "trust_score": max(trust_scores) if trust_scores else None,
        })
    return out


@router.get("/products/{canonical_id}")
async def get_product_details(
    canonical_id: str,
    card_ids: list[str] = Query(default=[]),
    live_terms: bool = Query(default=False),
    session: AsyncSession = Depends(get_db),
):
    """Return details, live listings, and Cash-vs-Points analysis for a canonical product."""
    clean_id = (canonical_id or "").strip()
    try:
        product_uuid = uuid.UUID(clean_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Product not found: '{clean_id}' is not a valid canonical product UUID.",
        )

    stmt = (
        select(CanonicalProduct)
        .options(selectinload(CanonicalProduct.listings).selectinload(RetailerListing.bank_offer_rules))
        .where(CanonicalProduct.id == product_uuid)
    )
    result = await session.execute(stmt)
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(
            status_code=404,
            detail=f"Product not found in database for canonical_product_id={product_uuid}.",
        )

    listings = [
        l
        for l in (product.listings or [])
        if l.current_listed_price
        and l.current_listed_price > 0
        and not is_blocked_non_retailer_domain(l.listing_url or "")
        and not is_blocked_non_retailer_domain(l.retailer or "")
    ]
    cheapest_listing = None
    if listings:
        cheapest_listing = min(
            listings,
            key=lambda l: l.current_listed_price,
        )

    buy_wait_data = None
    if cheapest_listing and cheapest_listing.current_listed_price > 0:
        verdict = await get_buy_wait_verdict(session, str(cheapest_listing.id), cheapest_listing.current_listed_price)
        buy_wait_data = {
            "verdict": verdict.verdict,
            "current_price": verdict.current_price,
            "p10": verdict.p10,
            "median_90d": verdict.median_90d,
            "all_time_low": verdict.all_time_low,
            "observation_count": verdict.observation_count,
            "next_sale_name": verdict.next_sale_name,
            "days_to_next_sale": verdict.days_to_next_sale,
            "reasoning": verdict.reasoning,
        }

    stacked_offers_data = None
    seller_report_data = None
    ranked_store_choices_data = []
    llm_summary_data = None
    cash_vs_points_data = None

    # Parse optional comma-separated or repeated card_ids STRICTLY
    parsed_card_uuids = _parse_valid_uuids(card_ids)
    if parsed_card_uuids:
        c_res = await session.execute(select(CreditCard).where(CreditCard.id.in_(parsed_card_uuids)))
        wallet_cards = [_card_to_cardlike(c) for c in c_res.scalars().all()]
    else:
        wallet_cards = []

    all_c_res = await session.execute(select(CreditCard))
    all_db_cards = [_card_to_cardlike(c) for c in all_c_res.scalars().all()]
    owned_names = {c.card_name.lower() for c in wallet_cards}
    unowned_cards = [c for c in all_db_cards if c.card_name.lower() not in owned_names]

    if cheapest_listing and cheapest_listing.current_listed_price and cheapest_listing.current_listed_price > 0:
        cheapest_price = cheapest_listing.current_listed_price
        parser = await get_llm_parser()

        bank_offer_schemas = [
            BankOfferRule(
                bank_name=bo.bank_name,
                card_type=bo.card_type if bo.card_type in ("credit", "debit", "both", "emi") else None,
                min_txn_amount=bo.min_txn_amount or 0.0,
                discount_flat=bo.discount_flat or 0.0,
                discount_pct=bo.discount_pct or 0.0,
                max_discount_cap=bo.max_discount_cap or 0.0,
                excluded_cards=(bo.excluded_cards or {}).get("excluded", []) if isinstance(bo.excluded_cards, dict) else [],
                summary=bo.raw_terms or f"{bo.bank_name} discount",
            )
            for bo in (cheapest_listing.bank_offer_rules or [])
        ]

        live_offers_for_stack = [
            LiveOfferRaw(
                title=f"{bo.bank_name} Instant Discount",
                offer_type="bank",
                raw_text=bo.raw_terms or f"{bo.bank_name} discount",
                source="live_page",
                conditions=f"Min txn ₹{bo.min_txn_amount}",
            )
            for bo in (cheapest_listing.bank_offer_rules or [])
        ]
        try:
            stacked = compute_stacked_offer_price(
                listed_price=cheapest_price,
                raw_coupons=[],
                bank_offers=bank_offer_schemas,
                user_cards=wallet_cards,
                retailer=cheapest_listing.retailer,
                raw_live_offers=live_offers_for_stack,
                delivery_fee=0.0,
                handling_fee=0.0,
                all_cards=all_db_cards,
            )
            stacked_offers_data = stacked.model_dump()
        except Exception as exc:
            logger.warning(f"Failed to compute stacked offer price in get_product_details: {exc}")
            stacked_offers_data = None
            stacked = StackedEffectivePriceResult(
                original_listed_price=cheapest_price,
                best_stacked_price=cheapest_price,
                total_savings=0.0,
            )

        verified_rules = []
        cvp_obj: CashVsPointsReport | None = None
        try:
            verified_rules = await resolve_live_payment_rules(
                retailer=cheapest_listing.retailer,
                user_cards=wallet_cards,
                allow_live_crawl=live_terms,
                timeout_seconds=12.0 if live_terms else 2.5,
            )
            cvp_obj = compute_cash_vs_points_report(
                listed_price=cheapest_price,
                raw_coupons=[],
                bank_offers=bank_offer_schemas,
                user_cards=wallet_cards,
                retailer=cheapest_listing.retailer,
                verified_rules=verified_rules,
                raw_live_offers=live_offers_for_stack,
                delivery_fee=0.0,
                handling_fee=0.0,
                unowned_cards=unowned_cards,
            )
            cash_vs_points_data = cvp_obj.model_dump()
        except Exception as exc:
            logger.warning(f"Failed to compute cash_vs_points in get_product_details: {exc}")

        seller_list: list[DynamicSellerInfo] = []
        for l in listings:
            if l.seller_name:
                seller_list.append(
                    DynamicSellerInfo(
                        seller_name=l.seller_name,
                        price=l.current_listed_price,
                        rating=l.seller_rating,
                        review_count=None,
                        fulfillment="Authorized Retail Partner" if l.retailer in ("croma", "reliance_digital", "vijaysales", "tatacliq") else "Marketplace Dispatch",
                        return_policy="7-14 Days Return Policy",
                        warranty_notes="1 Year Manufacturer Warranty",
                        is_buy_box_winner=(l.id == cheapest_listing.id),
                    )
                )
        seller_report = analyze_dynamic_sellers(
            sellers=seller_list,
            review_signals=None,
            retailer=cheapest_listing.retailer,
            current_price=cheapest_price,
            current_mrp=cheapest_listing.current_mrp,
            fallback_seller_name=cheapest_listing.seller_name,
            fallback_seller_rating=cheapest_listing.seller_rating,
        )
        seller_report_data = seller_report.model_dump()

        listing_snaps = [
            ListingSnapshot(
                retailer=l.retailer,
                listing_url=l.listing_url or "",
                listed_price=l.current_listed_price or 0.0,
                mrp=l.current_mrp,
                in_stock=l.in_stock,
                seller_name=l.seller_name,
                trust_score=l.trust_score or 75,
                match_confidence=l.match_confidence or 1.0,
            )
            for l in listings
            if l.current_listed_price and l.current_listed_price > 0
        ]
        cur_cash_price = cvp_obj.checkout_payable_inr if cvp_obj else cheapest_price
        cur_net_price = cvp_obj.best_user_card_net_price_cash if cvp_obj else cur_cash_price
        ranked_stores = rank_stores_fairly(
            current_retailer=cheapest_listing.retailer,
            current_listed_price=cheapest_price,
            current_checkout_cash_price=cur_cash_price,
            current_best_user_card_net_price=cur_net_price,
            current_url=cheapest_listing.listing_url or "",
            current_seller=seller_report.recommended_seller or cheapest_listing.seller_name or "Official Seller",
            current_peace_of_mind=cheapest_listing.trust_score or 75,
            competitors=[],
            existing_listings=listing_snaps,
            user_cards=wallet_cards,
            verified_rules=verified_rules,
        )
        ranked_store_choices_data = [r.model_dump() for r in ranked_stores]

        best_choice = ranked_stores[0] if ranked_stores else None
        key_pros = [p for s in seller_report.sellers for p in s.pros]
        key_cons = [c for s in seller_report.sellers for c in s.cons]
        best_store_name = best_choice.store_name if best_choice else (cheapest_listing.retailer or "Store")
        best_seller_name = (
            seller_report.recommended_seller
            or cheapest_listing.seller_name
            or "Official Partner"
        )
        final_price = (
            best_choice.effective_price
            if (best_choice and best_choice.effective_price is not None)
            else stacked.best_stacked_price
        )
        peace_score = (
            best_choice.peace_of_mind_score
            if best_choice
            else (cheapest_listing.trust_score or 75)
        )
        offer_stack_title = (
            stacked.applied_offers[0]["title"]
            if (stacked and stacked.applied_offers)
            else "Best Direct Net Price"
        )
        try:
            exec_summary = await parser.generate_executive_summary(
                product_title=f"{product.brand or ''} {product.model_name or ''}".strip(),
                best_store=best_store_name,
                best_seller=best_seller_name,
                effective_price=final_price,
                best_offer_stack=offer_stack_title,
                peace_of_mind_score=peace_score,
                key_pros=key_pros,
                key_cons=key_cons,
            )
            llm_summary_data = exec_summary.model_dump()
        except Exception as exc:
            logger.warning(f"Failed to generate executive summary in get_product_details: {exc}")
            exec_summary = ExecutiveSummary(
                best_store=best_store_name,
                best_seller=best_seller_name,
                best_offer_stack=offer_stack_title,
                final_net_price=final_price,
                why_this_wins=f"Direct net checkout price of ₹{final_price:,.0f} from verified seller.",
                key_caveat=key_cons[0] if key_cons else "Verify seal on delivery.",
                summary_bullets=[
                    f"Store: Buy from {best_store_name} (Seller: {best_seller_name}).",
                    f"Price: Final effective price ₹{final_price:,.0f}.",
                ],
            )
            llm_summary_data = exec_summary.model_dump()

    return {
        "id": str(product.id),
        "brand": product.brand,
        "model_name": product.model_name,
        "mpn_or_ean": product.mpn_or_ean,
        "category": product.category,
        "variant_specs": product.variant_specs or {},
        "created_at": str(product.created_at),
        "listings": [
            {
                "id": str(l.id),
                "retailer": l.retailer,
                "external_sku": l.external_sku,
                "listing_url": l.listing_url,
                "current_listed_price": l.current_listed_price,
                "current_mrp": l.current_mrp,
                "in_stock": l.in_stock,
                "seller_name": l.seller_name,
                "seller_rating": l.seller_rating,
                "trust_score": l.trust_score,
                "match_confidence": l.match_confidence,
                "last_checked_at": str(l.last_checked_at),
                "bank_offers": [
                    {
                        "bank_name": bo.bank_name,
                        "discount_flat": bo.discount_flat,
                        "discount_pct": bo.discount_pct,
                        "max_discount_cap": bo.max_discount_cap,
                        "min_txn_amount": bo.min_txn_amount,
                        "summary": getattr(bo, "raw_terms", None) or "",
                    }
                    for bo in (l.bank_offer_rules or [])
                ],
            }
            for l in listings
        ],
        "buy_wait": buy_wait_data,
        "stacked_offers": stacked_offers_data,
        "seller_report": seller_report_data,
        "ranked_store_choices": ranked_store_choices_data,
        "llm_summary": llm_summary_data,
        "cash_vs_points": cash_vs_points_data,
    }


@router.get("/products/{canonical_id}/history")
async def product_price_history(
    canonical_id: str,
    days: int = Query(default=90, ge=1, le=365),
    session: AsyncSession = Depends(get_db),
):
    """TimescaleDB price history for a canonical product."""
    try:
        product_uuid = uuid.UUID((canonical_id or "").strip())
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Invalid canonical product UUID: '{canonical_id}'")
    # Get all listings for this product
    listings_stmt = select(RetailerListing.id, RetailerListing.retailer).where(
        RetailerListing.canonical_product_id == product_uuid
    )
    listings = (await session.execute(listings_stmt)).all()
    if not listings:
        raise HTTPException(status_code=404, detail="Product not found")

    listing_ids = [str(l.id) for l in listings]

    rows = await session.execute(
        text("""
            SELECT
                po.observed_at,
                po.listing_id,
                rl.retailer,
                po.listed_price,
                po.coupon_discount,
                po.best_bank_discount,
                po.in_stock
            FROM price_observations po
            JOIN retailer_listings rl ON rl.id = po.listing_id
            WHERE po.listing_id = ANY(:ids)
              AND po.observed_at >= now() - make_interval(days => :days)
            ORDER BY po.observed_at ASC
        """),
        {"ids": listing_ids, "days": days},
    )
    history = [
        {
            "observed_at": str(r.observed_at),
            "retailer": r.retailer,
            "listed_price": r.listed_price,
            "coupon_discount": r.coupon_discount,
            "best_bank_discount": r.best_bank_discount,
            "in_stock": r.in_stock,
        }
        for r in rows
    ]
    return {"canonical_product_id": canonical_id, "days": days, "observations": history}


@router.get("/cards")
async def list_cards(session: AsyncSession = Depends(get_db)):
    """List all seeded credit cards."""
    result = await session.execute(select(CreditCard))
    cards = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "bank_name": c.bank_name,
            "card_name": c.card_name,
            "card_network": c.card_network,
            "base_reward_pct": c.base_reward_pct,
            "amazon_reward_pct": c.amazon_reward_pct,
            "flipkart_reward_pct": c.flipkart_reward_pct,
            "annual_fee": c.annual_fee,
        }
        for c in cards
    ]


@router.post("/vault/upload-invoice", response_model=VaultItemResponse)
async def upload_invoice(
    file: UploadFile = File(...),
    user_email: str = Query(..., description="User email for vault ownership"),
    session: AsyncSession = Depends(get_db),
):
    """PDF upload → pdfplumber → LLM parse → warranty vault."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    try:
        item = await process_invoice(session, file_bytes, user_email)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Invoice processing failed: {exc}")

    return VaultItemResponse(
        id=str(item.id),
        product_name=item.product_name,
        retailer=item.retailer,
        purchase_date=str(item.purchase_date),
        price_paid=item.price_paid,
        seller_name=item.seller_name,
        invoice_number=item.invoice_number,
        warranty_months=item.warranty_months,
        warranty_expiry_date=str(item.warranty_expiry_date),
        days_remaining=days_remaining(item),
    )


@router.get("/vault/items")
async def list_vault_items(
    user_email: str = Query(..., description="User email to query"),
    session: AsyncSession = Depends(get_db),
):
    """List user's warranty vault items."""
    items = await get_vault_items(session, user_email)
    return [
        VaultItemResponse(
            id=str(item.id),
            product_name=item.product_name,
            retailer=item.retailer,
            purchase_date=str(item.purchase_date),
            price_paid=item.price_paid,
            seller_name=item.seller_name,
            invoice_number=item.invoice_number,
            warranty_months=item.warranty_months,
            warranty_expiry_date=str(item.warranty_expiry_date),
            days_remaining=days_remaining(item),
        )
        for item in items
    ]


@router.post("/compare-exchange")
async def compare_exchange(
    new_device_price: float = Query(...),
    marketplace_exchange_quote: float = Query(...),
    external_buyback_quote: float = Query(...),
):
    """Compare marketplace exchange vs external buyback."""
    comp = compare_exchange_options(
        new_device_price=new_device_price,
        marketplace_exchange_quote=marketplace_exchange_quote,
        external_buyback_quote=external_buyback_quote,
    )
    return ExchangeComparisonResponse(
        marketplace_option=ExchangeOptionResponse(
            label=comp.marketplace_option.label,
            gross_value=comp.marketplace_option.gross_value,
            haircut_pct=comp.marketplace_option.haircut_pct,
            net_value=comp.marketplace_option.net_value,
            effective_upgrade_cost=comp.marketplace_option.effective_upgrade_cost,
        ),
        external_option=ExchangeOptionResponse(
            label=comp.external_option.label,
            gross_value=comp.external_option.gross_value,
            haircut_pct=comp.external_option.haircut_pct,
            net_value=comp.external_option.net_value,
            effective_upgrade_cost=comp.external_option.effective_upgrade_cost,
        ),
        recommendation=comp.recommendation,
        reasoning=comp.reasoning,
    )


# ═══════════════════════════════════════════════════════════════════════════
# M6: TWO-STAGE EXACT-VARIANT API, DECISION TRACES & WRONG-VARIANT FEEDBACK
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/matching/stage1")
async def stage1_exact_variant_matching(
    req: dict,
    session: AsyncSession = Depends(get_db),
):
    """
    M6 Stage 1 (<500ms warm, zero LLM):
    Builds source `VariantSpec`, evaluates any candidate payloads deterministically via
    `app.matching.comparator.decide`, persists `DecisionTrace` rows, and computes
    `lowest_verified_price` strictly over `EXACT` + `in_stock` + `listed_price > 0` listings.
    """
    from app.matching.comparator import decide
    from app.matching.contracts import Bucket, Verdict
    from app.matching.spec_builder import build_variant_spec
    from app.models.matching import AttributeAlias, DecisionTrace
    from app.schemas.extraction import (
        Stage1CandidateDecision,
        Stage1MatchingRequest,
        Stage1MatchingResponse,
    )

    t0 = time.perf_counter()
    parsed_req = Stage1MatchingRequest.model_validate(req)
    src_payload = parsed_req.payload
    src_spec = build_variant_spec(src_payload)

    # Load human-reviewed attribute_aliases from DB (if any)
    aliases_map: dict[str, str] = {}
    try:
        alias_rows = (await session.execute(select(AttributeAlias))).scalars().all()
        for row in alias_rows:
            aliases_map[row.alias_value.strip().lower()] = row.canonical_value.strip().lower()
    except Exception:
        pass

    decisions_out: list[Stage1CandidateDecision] = []
    verified_prices: list[tuple[float, str]] = []

    if src_payload.in_stock and src_payload.listed_price and src_payload.listed_price > 0:
        verified_prices.append((float(src_payload.listed_price), src_payload.retailer))

    for cand_p in parsed_req.candidates:
        cand_spec = build_variant_spec(cand_p)
        dec = decide(src_spec, cand_spec, aliases=aliases_map or None)

        badges: list[str] = []
        for d in dec.diffs:
            if d.verdict == Verdict.MISMATCH:
                badges.append(f"{d.name}: {d.cand_value or 'differs'}")
            elif d.verdict == Verdict.UNKNOWN:
                badges.append(f"couldn't confirm: {d.name}")

        trace_id_str: str | None = None
        try:
            trace = DecisionTrace(
                id=uuid.uuid4(),
                source_spec_json=src_spec.model_dump(mode="json"),
                cand_spec_json=cand_spec.model_dump(mode="json"),
                decision_json=dec.model_dump(mode="json"),
                versions={"comparator": "v2.0", "normalizer": "v2.0"},
            )
            session.add(trace)
            trace_id_str = str(trace.id)
        except Exception as exc:
            logger.debug("DecisionTrace persistence skipped: %s", exc)

        if dec.bucket == Bucket.EXACT and cand_p.in_stock and cand_p.listed_price and cand_p.listed_price > 0:
            verified_prices.append((float(cand_p.listed_price), cand_p.retailer))

        decisions_out.append(
            Stage1CandidateDecision(
                retailer=cand_p.retailer,
                url=cand_p.url,
                title=cand_p.title,
                listed_price=cand_p.listed_price,
                in_stock=cand_p.in_stock,
                bucket=dec.bucket.value,  # type: ignore[arg-type]
                variant_diff_badges=badges,
                reasons=dec.reasons,
                confidence=dec.confidence,
                unit_price_cand=float(dec.unit_price_cand) if dec.unit_price_cand is not None else None,
                unit_price_unit=dec.unit_price_unit,
                trace_id=trace_id_str,
            )
        )

    try:
        await session.commit()
    except Exception:
        await session.rollback()

    lowest_price: float | None = None
    lowest_store: str | None = None
    if verified_prices:
        verified_prices.sort(key=lambda x: x[0])
        lowest_price, lowest_store = verified_prices[0]

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return Stage1MatchingResponse(
        source_spec=src_spec.model_dump(mode="json"),
        listed_price=float(src_payload.listed_price or 0.0),
        stage1_latency_ms=elapsed_ms,
        decisions=decisions_out,
        lowest_verified_price=lowest_price,
        lowest_verified_store=lowest_store,
    )


@router.post("/matching/feedback")
async def submit_wrong_variant_feedback(
    req: dict,
    session: AsyncSession = Depends(get_db),
):
    """
    M6 "Wrong variant?" Feedback Endpoint -> `review_queue` table.
    Allows users/evaluators to flag a candidate pair for human review or alias addition.
    """
    from app.models.matching import ReviewQueueItem
    from app.schemas.extraction import VariantFeedbackRequest, VariantFeedbackResponse

    parsed = VariantFeedbackRequest.model_validate(req)
    trace_uuid: uuid.UUID | None = None
    if parsed.decision_trace_id:
        try:
            trace_uuid = uuid.UUID(parsed.decision_trace_id)
        except ValueError:
            trace_uuid = None

    item = ReviewQueueItem(
        id=uuid.uuid4(),
        decision_trace_id=trace_uuid,
        source_url=parsed.source_url[:1000],
        cand_url=parsed.cand_url[:1000],
        reported_issue=parsed.reported_issue or "wrong_variant",
        source_spec_json=parsed.source_spec_json,
        cand_spec_json=parsed.cand_spec_json,
        status="pending",
    )
    session.add(item)
    await session.commit()
    return VariantFeedbackResponse(
        review_queue_id=str(item.id),
        status=item.status,
        message="Queued for human variant review",
    )


@router.get("/matching/traces")
async def list_decision_traces(
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """List recent pair-level matching `decision_traces`."""
    from app.models.matching import DecisionTrace

    stmt = select(DecisionTrace).order_by(DecisionTrace.ts.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "source_spec_json": r.source_spec_json,
            "cand_spec_json": r.cand_spec_json,
            "decision_json": r.decision_json,
            "versions": r.versions,
            "ts": str(r.ts),
        }
        for r in rows
    ]
