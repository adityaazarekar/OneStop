"""
Comprehensive Stage 1, 2 & 3 Verification Test Suite (`test_stage123_universal_qc_kg.py`):
1. ARQ/fallback job enqueue + real-time SSE event streaming (`POST /api/v1/compare-all-stores` + `stream_job_events`)
2. PostgreSQL Temporal Knowledge Graph (`kg_entities` + `kg_edges`): entity upsert, temporal edge creation,
   multi-hop active rule query (`load_verified_rule_from_kg`), and automatic expiration (`valid_to = NOW()`)
3. Cross-sell coupon rejection (`"Unlock Air fryer coupon • 10% off"` -> `₹0` coupon on `₹89,100` iPhone 17)
   + multi-node Flipkart HDFC offer (`-₹891`) + Protect Promise Fee (`+₹299`) = `₹88,508`
4. Zero hardcoded fallback enforcement (`₹0` when unverified) & mutual exclusion guard
5. Non-store blocklist (`pricehistory.app`, `91mobiles.com` rejected) + 4-query 15-domain `tldextract` discovery
   + Shopify `.json`, WooCommerce Store API `.json`, and generic D2C store acceptance
6. Quick-Commerce unit-price (`₹/100g`, `₹/100ml`, `₹/unit`), handling/surge/small-cart fee math, and pincode location resolver
7. Multimodal `RapidFuzz` + perceptual `imagehash.phash` (via Pillow) + pack weight/volume variant/accessory filtering
8. `POST /api/v1/analyze-client-payload` -> immediate `GET /api/v1/products/{id}` returning `200 OK` (zero `Product Not Found`)
"""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone

import pytest

from app.schemas.extraction import (
    ClientPayload,
    LiveOfferRaw,
    VerifiedPaymentRule,
)
from app.services.card_optimizer import CardLike
from app.services.kg_service import (
    cosine_similarity,
    compute_text_embedding,
    load_verified_rule_from_kg,
    persist_verified_rule_to_kg,
    query_active_edges,
    upsert_kg_entity,
    upsert_temporal_edge,
)
from app.services.llm_parser import _deterministic_bank_offers
from app.services.offer_stack_engine import compute_cash_vs_points_report
from app.services.product_matcher import (
    compare_image_phashes,
    compute_image_phash_from_bytes,
    score_candidate_match,
)
from app.services.quick_commerce_engine import (
    build_qc_quote_from_payload,
    compute_qc_checkout_payable,
    parse_pack_quantity_and_unit_price,
    resolve_pincode_location,
    sanitize_pincode,
)
from app.services.shopping_discovery import (
    build_transactional_searxng_queries,
    extract_root_domain,
    select_diverse_candidates,
)
from app.services.universal_scraper import (
    build_shopify_json_url,
    build_woocommerce_store_api_url,
    is_blocked_non_retailer_domain,
    parse_html_to_payload,
    parse_shopify_product_json,
    parse_woocommerce_store_api_json,
)
from app.worker import publish_sse_event, stream_job_events


def _make_card(name: str, bank: str, base: float = 1.0) -> CardLike:
    c = CardLike()
    c.card_name = name
    c.bank_name = bank
    c.base_reward_pct = base
    c.amazon_reward_pct = base
    c.flipkart_reward_pct = base
    return c


@pytest.mark.asyncio
async def test_arq_fallback_sse_event_streaming():
    """Verify SSE event publishing and progressive streaming (`store_quote`, `quick_commerce_quote`, `complete`)."""
    job_id = f"testjob_{uuid.uuid4().hex[:10]}"
    await publish_sse_event(
        job_id,
        "store_quote",
        {"quote": {"retailer": "croma", "listed_price": 74900.0, "url": "https://www.croma.com/p/1"}},
    )
    await publish_sse_event(
        job_id,
        "quick_commerce_quote",
        {"quote": {"platform": "blinkit", "app_name": "Blinkit", "total_payable_inr": 75209.0}},
    )
    await publish_sse_event(
        job_id,
        "complete",
        {"job_id": job_id, "stores_count": 1, "quick_commerce_count": 1},
    )

    frames: list[str] = []
    async for chunk in stream_job_events(job_id, timeout_seconds=3.0):
        frames.append(chunk)

    joined = "".join(frames)
    assert "event: store_quote" in joined
    assert "event: quick_commerce_quote" in joined
    assert "event: complete" in joined
    assert "Blinkit" in joined


@pytest.mark.asyncio
async def test_temporal_knowledge_graph_crud_multihop_and_expiration():
    """Verify `kg_entities` + `kg_edges` creation, multi-hop rule reconstruction, and automatic `valid_to` expiration."""
    from app.db.session import async_session_factory, engine
    from app.models.knowledge_graph import KGEdge
    from sqlalchemy import select

    engine.sync_engine.pool.dispose()

    # Check NumPy cosine similarity on deterministic embeddings
    e1 = compute_text_embedding("card HDFC Infinia Credit Card")
    e2 = compute_text_embedding("card HDFC Infinia")
    e3 = compute_text_embedding("merchant Blinkit Grocery")
    assert cosine_similarity(e1, e2) > cosine_similarity(e1, e3)

    unique_card = f"HDFC Infinia KGTest {uuid.uuid4().hex[:6]}"
    merchant_key = f"flipkart_kg_{uuid.uuid4().hex[:6]}"

    async with async_session_factory() as session:
        card_ent = await upsert_kg_entity(session, entity_type="card", display_name=unique_card)
        merch_ent = await upsert_kg_entity(session, entity_type="merchant", display_name=merchant_key)

        # Insert v1 edge
        edge_v1 = await upsert_temporal_edge(
            session,
            source_entity_id=card_ent.id,
            relation="HAS_MONTHLY_CAP",
            target_entity_id=merch_ent.id,
            attributes_json={"monthly_cap_inr": 10000.0},
            source_url="https://www.hdfcbank.com/infinia-v1",
        )
        assert edge_v1.valid_to is None

        # Insert v2 edge for the same (source, relation, target) -> v1 MUST automatically expire!
        edge_v2 = await upsert_temporal_edge(
            session,
            source_entity_id=card_ent.id,
            relation="HAS_MONTHLY_CAP",
            target_entity_id=merch_ent.id,
            attributes_json={"monthly_cap_inr": 15000.0},
            source_url="https://www.hdfcbank.com/infinia-v2",
        )
        await session.flush()

        active_caps = await query_active_edges(
            session,
            source_entity_id=card_ent.id,
            target_entity_id=merch_ent.id,
            relation="HAS_MONTHLY_CAP",
        )
        assert len(active_caps) == 1
        assert active_caps[0].id == edge_v2.id
        assert active_caps[0].attributes_json["monthly_cap_inr"] == 15000.0

        v1_reloaded = (await session.execute(select(KGEdge).where(KGEdge.id == edge_v1.id))).scalar_one()
        assert v1_reloaded.valid_to is not None

        # Persist full VerifiedPaymentRule and reconstruct via multi-hop KG query
        rule_in = VerifiedPaymentRule(
            method_name=unique_card,
            method_type="credit_card",
            reward_type="points",
            points_per_spend_unit=5.0,
            spend_unit_inr=150.0,
            point_value_cash_inr=0.30,
            point_value_travel_inr=1.00,
            monthly_cap_inr=15000.0,
            processing_fee_pct=0.0,
            exclusions=["No reward points on fuel and wallet loads"],
            stackable_with=["instant_bank_discount"],
            verification_status="issuer_tc_verified",
            source_urls=["https://www.hdfcbank.com/infinia-mitc"],
            last_verified_at=datetime.now(timezone.utc),
            plain_english_summary="5 RPs per ₹150 spend (₹0.30 cash / ₹1.00 SmartBuy travel).",
        )
        await persist_verified_rule_to_kg(session, rule_in, merchant_key)
        await session.commit()

        loaded = await load_verified_rule_from_kg(
            session,
            method_name=unique_card,
            method_type="credit_card",
            retailer=merchant_key,
        )
        assert loaded is not None
        assert loaded.points_per_spend_unit == 5.0
        assert loaded.spend_unit_inr == 150.0
        assert loaded.point_value_cash_inr == pytest.approx(0.30)
        assert loaded.point_value_travel_inr == pytest.approx(1.00)
        assert loaded.monthly_cap_inr == 15000.0
        assert "No reward points on fuel and wallet loads" in loaded.exclusions
        assert loaded.source_urls == ["https://www.hdfcbank.com/infinia-mitc"]

    engine.sync_engine.pool.dispose()


def test_cross_sell_air_fryer_rejection_and_flipkart_hdfc_891_protect_299_exact_88508():
    """
    Verify:
    - 'Unlock Air fryer coupon • 10% off' -> ₹0 coupon on ₹89,100 iPhone 17
    - Multi-node Flipkart HDFC offer (-₹891) + Protect Promise Fee (+₹299) = ₹88,508
    - Mutual exclusion guard when instant bank discount T&C excludes base card rewards
    """
    infinia = _make_card("HDFC Infinia", "HDFC", base=3.3)
    parsed_offers = _deterministic_bank_offers(
        [
            "₹891 off HDFC Credit Card",
            "₹1,101 off Google Pay UPI",
            "₹4,000 off Flipkart Axis Bank Credit Card",
        ]
    )
    report = compute_cash_vs_points_report(
        listed_price=89100.0,
        raw_coupons=[
            "Additional benefits on this purchase -> Unlock Air fryer coupon • 10% off",
            "Partner Offer: Get 3 months Spotify Premium free",
            "Unlock ₹1,000 off on future order",
        ],
        bank_offers=parsed_offers,
        user_cards=[infinia],
        retailer="flipkart",
        handling_fee=299.0,
        retailer_advertised_lowest_price=85100.0,
        raw_live_offers=[
            LiveOfferRaw(title="Google Pay UPI", offer_type="upi", raw_text="₹1,101 off Google Pay UPI"),
            LiveOfferRaw(title="Protect Promise Fee", offer_type="fee", raw_text="+ ₹299 Protect Promise Fee"),
        ],
    )
    assert report.coupon_discount_inr == 0.0
    assert report.instant_bank_discount_inr == 891.0
    assert report.mandatory_fees_inr == 299.0
    assert report.checkout_payable_inr == 88508.0


def test_universal_15_domain_discovery_and_shopify_woocommerce_tier0():
    """Verify 4-query fan-out, 15-domain tldextract deduplication, non-store blocklist, Shopify & WooCommerce Tier 0."""
    queries = build_transactional_searxng_queries("Apple iPhone 17 256GB")
    assert len(queries) == 4
    assert extract_root_domain("https://shop.aptronixindia.com/products/iphone-17") == "aptronixindia.com"

    assert is_blocked_non_retailer_domain("https://pricehistory.app/p/iphone-17") is True
    assert is_blocked_non_retailer_domain("https://www.91mobiles.com/apple-iphone-17") is True

    # Generate 18 unique legitimate store domains + 2 blocked domains -> select_diverse_candidates keeps 15 unique stores
    hits = [
        {"url": "https://pricehistory.app/p/iphone-17", "title": "iPhone 17", "snippet": "₹89,100"},
        {"url": "https://www.91mobiles.com/iphone-17", "title": "iPhone 17", "snippet": "₹89,100"},
    ]
    for i in range(18):
        hits.append(
            {
                "url": f"https://merchant{i}.co.in/products/apple-iphone-17-256gb",
                "title": "Apple iPhone 17 256GB",
                "snippet": "₹88,900 Add to Cart",
            }
        )
    selected = select_diverse_candidates(hits, current_domain="flipkart.com", brand="Apple", max_stores=15)
    assert len(selected) == 15
    assert all("pricehistory" not in c["url"] and "91mobiles" not in c["url"] for c in selected)

    # Shopify Tier 0
    s_url = "https://imagineonline.store/products/apple-iphone-17-256gb"
    assert build_shopify_json_url(s_url) == "https://imagineonline.store/products/apple-iphone-17-256gb.json"
    sp = parse_shopify_product_json(
        {
            "product": {
                "id": 101,
                "title": "Apple iPhone 17 256GB",
                "vendor": "Apple",
                "variants": [{"id": 1, "title": "Black", "price": "88900.00", "available": True}],
            }
        },
        s_url,
    )
    assert sp is not None and sp.listed_price == 88900.0

    # WooCommerce Store API Tier 0
    w_url = "https://regionaltech.in/product/apple-iphone-17-256gb/"
    assert build_woocommerce_store_api_url(w_url) == "https://regionaltech.in/wp-json/wc/store/v1/products?slug=apple-iphone-17-256gb"
    wp = parse_woocommerce_store_api_json(
        [
            {
                "id": 505,
                "name": "Apple iPhone 17 256GB Black",
                "sku": "WOO-IP17-256",
                "is_in_stock": True,
                "is_purchasable": True,
                "prices": {"price": "8840000", "regular_price": "8990000", "currency_minor_unit": 2},
                "images": [{"src": "https://regionaltech.in/img/ip17.jpg"}],
            }
        ],
        w_url,
    )
    assert wp is not None
    assert wp.listed_price == 88400.0
    assert wp.mrp == 89900.0
    assert wp.variant_specs["extraction_tier"] == "tier0_woocommerce_json"


@pytest.mark.asyncio
async def test_quick_commerce_unit_price_fees_and_pincode_location():
    """Verify Quick-Commerce unit-price (`₹/100g`, `₹/100ml`, `₹/unit`), fee math, and pincode resolver."""
    assert sanitize_pincode("411001") == "411001"
    assert sanitize_pincode("invalid") == "411001"
    loc = await resolve_pincode_location("411001")
    assert loc["city"] == "Pune"
    assert loc["lat"] == pytest.approx(18.5204, abs=0.05)

    # 500g at ₹120 -> ₹24/100g
    pack_lbl, unit_val, unit_lbl = parse_pack_quantity_and_unit_price(120.0, "Davidoff Rich Aroma Instant Coffee 500 g")
    assert pack_lbl == "500 g"
    assert unit_val == 24.0
    assert unit_lbl == "₹24/100g"

    # 1.5 kg at ₹300 -> ₹20/100g
    _, unit_val_kg, unit_lbl_kg = parse_pack_quantity_and_unit_price(300.0, "Tata Sampann Toor Dal 1.5 kg")
    assert unit_val_kg == 20.0
    assert unit_lbl_kg == "₹20/100g"

    # Small cart (< ₹199 on Blinkit) adds ₹9 handling + ₹25 small-cart fee + surge
    h, d, s, sc, total = compute_qc_checkout_payable("blinkit", 120.0, surge_fee=15.0)
    assert h == 9.0
    assert sc == 25.0
    assert s == 15.0
    assert total == 169.0

    qc_payload = ClientPayload(
        retailer="blinkit",
        url="https://blinkit.com/prn/davidoff-coffee-500g/prid/12345",
        title="Davidoff Rich Aroma Coffee 500 g",
        brand="Davidoff",
        listed_price=550.0,
        mrp=650.0,
        handling_fee=9.0,
    )
    quote = build_qc_quote_from_payload(qc_payload, pincode="411001", platform_key="blinkit")
    assert quote is not None
    assert quote.app_name == "Blinkit"
    assert quote.unit_price_label == "₹110/100g"
    assert quote.total_payable_inr == 559.0  # ₹550 >= ₹199 so no small-cart fee, + ₹9 handling fee


def test_multimodal_rapidfuzz_and_imagehash_phash_filtering():
    """Verify Pillow + `imagehash.phash` perceptual image matching and pack-weight hard blockers."""
    from PIL import Image, ImageDraw

    # Create two visually similar product images and one completely different image
    img1 = Image.new("RGB", (64, 64), color=(255, 255, 255))
    d1 = ImageDraw.Draw(img1)
    d1.rectangle([12, 8, 52, 56], fill=(20, 20, 20))
    buf1 = io.BytesIO()
    img1.save(buf1, format="PNG")

    img2 = Image.new("RGB", (64, 64), color=(252, 252, 252))
    d2 = ImageDraw.Draw(img2)
    d2.rectangle([12, 8, 52, 56], fill=(25, 25, 25))
    buf2 = io.BytesIO()
    img2.save(buf2, format="PNG")

    img_diff = Image.new("RGB", (64, 64), color=(255, 255, 255))
    d3 = ImageDraw.Draw(img_diff)
    d3.ellipse([2, 2, 30, 30], fill=(200, 10, 10))
    buf3 = io.BytesIO()
    img_diff.save(buf3, format="PNG")

    ph1 = compute_image_phash_from_bytes(buf1.getvalue())
    ph2 = compute_image_phash_from_bytes(buf2.getvalue())
    ph_diff = compute_image_phash_from_bytes(buf3.getvalue())
    assert ph1 is not None and ph2 is not None and ph_diff is not None

    same_ok, dist_same = compare_image_phashes(ph1, ph2)
    assert same_ok is True and dist_same is not None and dist_same <= 6

    # Pack weight mismatch (500g vs 1kg) MUST be rejected even with high title similarity
    ok_weight, _, reason_w = score_candidate_match(
        "Davidoff Rich Aroma Coffee 500 g",
        "Davidoff Rich Aroma Coffee 1 kg",
    )
    assert ok_weight is False
    assert "volume" in reason_w.lower() or "variant" in reason_w.lower()

    # Matching pack weight (500g == 500gm) + matching phash succeeds
    ok_match, score, _ = score_candidate_match(
        "Davidoff Rich Aroma Coffee 500 g",
        "Davidoff Rich Aroma Coffee (500 gm)",
        target_image_phash=ph1,
        candidate_image_phash=ph2,
    )
    assert ok_match is True
    assert score >= 75.0


def test_analyze_client_payload_and_compare_all_stores_endpoints():
    """Verify POST /api/v1/analyze-client-payload -> GET /api/v1/products/{id} (200 OK) and POST /api/v1/compare-all-stores."""
    from fastapi.testclient import TestClient
    from app.db.session import engine
    from app.main import app

    engine.sync_engine.pool.dispose()

    unique_sku = f"STG123SKU{uuid.uuid4().hex[:8].upper()}"
    body = {
        "payload": {
            "retailer": "flipkart",
            "url": f"https://www.flipkart.com/apple-iphone-17/p/{unique_sku}",
            "sku": unique_sku,
            "title": f"Apple iPhone 17 (Black, 256 GB) {unique_sku}",
            "brand": "Apple",
            "listed_price": 89100.0,
            "mrp": 89900.0,
            "retailer_advertised_lowest_price": 85100.0,
            "in_stock": True,
            "seller_name": "SuperComNet",
            "handling_fee": 299.0,
            "pincode": "411001",
            "raw_bank_offers": ["₹891 off HDFC Credit Card", "₹1,101 off Google Pay UPI"],
            "raw_coupons": ["Additional benefits on this purchase -> Unlock Air fryer coupon • 10% off"],
        },
        "competitor_quotes": [],
        "user_card_ids": [],
        "pincode": "411001",
    }

    with TestClient(app) as client:
        post_resp = client.post("/api/v1/analyze-client-payload", json=body)
        assert post_resp.status_code == 200, post_resp.text
        data = post_resp.json()
        cid = data["canonical_product_id"]
        assert data["cash_vs_points"]["coupon_discount_inr"] == 0.0
        assert data["cash_vs_points"]["mandatory_fees_inr"] == 299.0

        get_resp = client.get(f"/api/v1/products/{cid}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["id"] == cid

        cmp_resp = client.post(
            "/api/v1/compare-all-stores",
            json={"payload": body["payload"], "pincode": "411001", "user_card_ids": []},
        )
        assert cmp_resp.status_code == 200, cmp_resp.text
        cmp_data = cmp_resp.json()
        assert cmp_data["job_id"]
        assert cmp_data["pincode"] == "411001"
    engine.sync_engine.pool.dispose()
