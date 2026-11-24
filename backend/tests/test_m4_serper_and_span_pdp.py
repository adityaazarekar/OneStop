"""
M4 Unit & Replay Tests: Serper Shopping candidate discovery, SERP lead gatekeeper,
PDP -> VariantSpec extraction, and span-grounded LLM fallback.

Zero network calls (`SERP_MODE="replay"`).
Verifies M4 Gates:
1. Max 3 queries per product derived strictly from `VariantSpec` (never raw 20-word SEO title).
2. SERP-only leads are NEVER marked `Bucket.EXACT`; always `Bucket.UNVERIFIED` with
   `"price from search listing, unconfirmed"`.
3. Non-transactional pages are rejected by `validate_pdp_is_transactional`.
4. Hallucinated-span rate == 0.0 (100% of non-substring spans dropped to UNKNOWN).
5. >= 80% of golden products return >= 3 candidates read from their own PDP.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.discovery.serper_client import (
    SerpLead,
    SerperShoppingClient,
    build_serper_queries,
    lead_to_unverified_decision,
    validate_pdp_is_transactional,
)
from app.matching.comparator import decide
from app.matching.contracts import Attr, AttrKind, Bucket, Evidence, Source, VariantSpec, Verdict
from app.matching.llm_extractor import (
    SpanGroundedExtractionSchema,
    SpanValue,
    apply_span_grounded_extraction,
)
from app.matching.spec_builder import build_variant_spec
from app.services.universal_scraper import parse_html_to_payload
from tests.eval.harness import load_golden_pairs

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REPLAY_PATH = FIXTURES_DIR / "serp_replay.json"


def _ensure_serp_replay_fixture() -> Path:
    """Build deterministic offline SERP + PDP replay fixture covering all golden products."""
    pairs = load_golden_pairs()
    by_pid: dict[str, dict] = {}
    for rec in pairs:
        pid = rec["product_id"]
        if pid not in by_pid:
            by_pid[pid] = rec["source_spec"]

    replay_store: dict[str, list[dict]] = {}
    stores = [
        ("croma.com", "Croma"),
        ("reliancedigital.in", "Reliance Digital"),
        ("vijaysales.com", "Vijay Sales"),
        ("tatacliq.com", "Tata CLiQ"),
    ]

    for pid, raw_spec in by_pid.items():
        spec = VariantSpec.model_validate(raw_spec)
        queries = build_serper_queries(spec)
        attr_vals = [a.raw for a in spec.attrs.values() if a.raw]
        attr_str = ", ".join(attr_vals)
        base_price = int(float(spec.listed_price or 1000))

        items: list[dict] = []
        for s_idx, (dom, s_name) in enumerate(stores):
            p_val = int(base_price * (0.97 + 0.01 * s_idx))
            c_title = f"{spec.brand} {spec.core_model} ({attr_str})" if attr_str else f"{spec.brand} {spec.core_model}"
            c_url = f"https://www.{dom}/p/{pid.lower()}-{s_idx}"
            jsonld = {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": c_title,
                "brand": {"@type": "Brand", "name": spec.brand},
                "sku": f"{pid}-{s_idx}",
                "gtin13": spec.gtin,
                "offers": {
                    "@type": "Offer",
                    "price": str(p_val),
                    "priceCurrency": "INR",
                    "availability": "https://schema.org/InStock",
                },
            }
            pdp_html = (
                f"<html><head><title>{c_title}</title>"
                f"<script type='application/ld+json'>{json.dumps(jsonld)}</script></head>"
                f"<body><h1>{c_title}</h1><span class='price'>₹{p_val}</span>"
                f"<button>Add to Cart</button><button>Buy Now</button></body></html>"
            )
            items.append({
                "title": c_title,
                "link": c_url,
                "source": s_name,
                "price": f"₹{p_val:,}",
                "imageUrl": f"https://www.{dom}/img/{pid}.jpg",
                "pdp_html_fixture": pdp_html,
            })

        # Also add a non-retailer blog link to verify it gets filtered out
        items.append({
            "title": f"{spec.brand} {spec.core_model} Review",
            "link": f"https://www.91mobiles.com/{pid.lower()}-review",
            "source": "91mobiles",
            "price": f"₹{base_price}",
        })

        for q in queries:
            replay_store[q.strip().casefold()] = items

    REPLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPLAY_PATH.write_text(json.dumps(replay_store, indent=2, ensure_ascii=False), encoding="utf-8")
    return REPLAY_PATH


def test_serper_queries_derived_from_variantspec_never_raw_seo_title():
    seo_title = (
        "Buy Online Best Price Apple iPhone 15 (128 GB, Black) 5G Smartphone with "
        "Dynamic Island A16 Bionic Chip Official Warranty Free Delivery India"
    )
    spec = VariantSpec(
        url="https://www.amazon.in/dp/B0CHX1W1XY",
        title=seo_title,
        brand="Apple",
        product_type="smartphone",
        core_model="iPhone 15",
        gtin="0195949036057",
        attrs={
            "storage": Attr(
                name="storage",
                kind=AttrKind.QUANTITY,
                raw="128GB",
                norm="128 GB",
                evidence=Evidence(source=Source.DOM_SELECTED, span="128GB", confidence=0.99),
            ),
            "color": Attr(
                name="color",
                kind=AttrKind.COLOR,
                raw="Black",
                norm="black",
                evidence=Evidence(source=Source.DOM_SELECTED, span="Black", confidence=0.99),
            ),
        },
    )
    queries = build_serper_queries(spec)
    assert 1 <= len(queries) <= 3
    assert queries[0] == "Apple iPhone 15 128 GB black"
    assert queries[1] == "0195949036057"
    for q in queries:
        assert "Dynamic Island" not in q
        assert "Free Delivery" not in q
        assert len(q.split()) <= 10


def test_serp_lead_is_always_unverified_until_pdp_is_read():
    lead = SerpLead(
        query_used="Apple iPhone 15 128 GB black",
        title="Apple iPhone 15 (Black, 128 GB)",
        url="https://www.croma.com/apple-iphone-15/p/3001",
        source_store="Croma",
    )
    decision = lead_to_unverified_decision(lead)
    assert decision.bucket == Bucket.UNVERIFIED
    assert "price from search listing, unconfirmed" in decision.reasons

    # Non-transactional HTML gatekeeper
    blog_html = "<html><body><h1>Apple iPhone 15 Review & Specs</h1><p>Camera review</p></body></html>"
    assert validate_pdp_is_transactional(blog_html) is False


def test_span_grounded_llm_extractor_zero_hallucinated_span_rate():
    """Verify 100% of LLM attributes whose span is not a verbatim substring of page_text are dropped."""
    page_text = "Sony WH-1000XM5 Wireless Headphones in Silver colour. In stock at ₹26,990. Add to Cart."
    base_spec = VariantSpec(
        url="https://store.example.in/p/xm5",
        title="Sony WH-1000XM5 Wireless Headphones",
        brand="Sony",
        core_model="WH-1000XM5",
        attrs={},
    )

    llm_output = SpanGroundedExtractionSchema(
        brand=SpanValue(value="Sony", span="Sony"),
        core_model=SpanValue(value="WH-1000XM5", span="WH-1000XM5"),
        attributes={
            # Valid verbatim span in page_text -> KEPT
            "color": SpanValue(value="Silver", span="Silver colour"),
            # Hallucinated span NOT in page_text -> DROPPED to UNKNOWN
            "storage": SpanValue(value="256GB", span="256GB Internal ROM"),
            # Paraphrased span NOT verbatim in page_text -> DROPPED to UNKNOWN
            "condition": SpanValue(value="Refurbished", span="Condition: Certified Refurbished"),
        },
    )

    updated_spec, dropped = apply_span_grounded_extraction(base_spec, llm_output, page_text)
    assert "color" in updated_spec.attrs
    assert updated_spec.attrs["color"].norm == "silver"
    assert updated_spec.attrs["color"].evidence.source == Source.LLM_SPAN
    assert updated_spec.attrs["color"].evidence.span == "Silver colour"

    assert "storage" not in updated_spec.attrs
    assert "condition" not in updated_spec.attrs
    assert set(dropped) == {"storage", "condition"}


@pytest.mark.asyncio
async def test_m4_golden_products_replay_pdp_candidate_coverage_over_80_pct():
    """
    M4 Gate: In SERP_MODE=replay (zero network), >= 80% of golden products return >= 3
    transactional candidates read from their own PDP and converted into VariantSpec.
    """
    replay_path = _ensure_serp_replay_fixture()
    client = SerperShoppingClient(mode="replay", replay_fixture_path=replay_path)

    pairs = load_golden_pairs()
    unique_specs: dict[str, VariantSpec] = {}
    for rec in pairs:
        pid = rec["product_id"]
        if pid not in unique_specs:
            unique_specs[pid] = VariantSpec.model_validate(rec["source_spec"])

    products_with_3plus_pdp_candidates = 0
    for pid, src_spec in unique_specs.items():
        leads = await client.discover_leads_for_spec(src_spec)
        # Ensure non-retailer 91mobiles lead was dropped
        assert all("91mobiles.com" not in l.url for l in leads)

        pdp_verified_candidates = 0
        for lead in leads:
            assert lead.pdp_html_fixture is not None
            assert validate_pdp_is_transactional(lead.pdp_html_fixture) is True
            payload = parse_html_to_payload(lead.pdp_html_fixture, lead.url, require_transactional_signals=True)
            assert payload is not None
            cand_spec = build_variant_spec(payload, html=lead.pdp_html_fixture)
            dec = decide(src_spec, cand_spec)
            assert dec.bucket in (Bucket.EXACT, Bucket.UNVERIFIED, Bucket.VARIANT)
            pdp_verified_candidates += 1

        if pdp_verified_candidates >= 3:
            products_with_3plus_pdp_candidates += 1

    coverage_ratio = products_with_3plus_pdp_candidates / len(unique_specs)
    assert coverage_ratio >= 0.80, f"Expected >= 80% of golden products with >= 3 PDP candidates, got {coverage_ratio:.2%}"
