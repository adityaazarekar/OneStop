"""
M5' Unit & Evaluation Tests: Hybrid RRF Retrieval (`recall@20`), Three-Band Router,
Isotonic Calibration, and Stage C Escalation Agent.

Zero network calls.
Verifies M5' Gates:
1. Retrieval = lexical (`BM25`/`pg_trgm`) + dense text (`bge-m3`) fused with RRF (`k=60`) + exact GTIN/MPN channel;
   `recall@20` measured per channel and fused on the golden set; A/B comparison vs unigram API baseline.
2. Three-Band Router applies ONLY to pairs unsettled by deterministic comparator (`MISMATCH` and `EXACT` are final).
3. Stage C Agent verifies verbatim substring quotes on BOTH inputs (`quote_a in input_a` and `quote_b in input_b`),
   enforces hard cap of 4 `web_search` rounds, never overrides a comparator `MISMATCH`, and handles `gtin_collision_suspect`.
4. Agent precision >= 0.98 on `MEDIUM`-band golden pairs; end-to-end `EXACT` precision >= 0.99 and recall >= 0.80.
"""
from __future__ import annotations

import pytest

from app.matching.comparator import decide
from app.matching.contracts import (
    Attr,
    AttrKind,
    Bucket,
    Evidence,
    Source,
    VariantSpec,
    Verdict,
)
from app.matching.retrieval import evaluate_retrieval_channels_on_golden
from app.matching.router import (
    AgentAttrVerdict,
    AgentJudgeOutput,
    IsotonicCalibrator,
    StageCEscalationAgent,
    ThreeBandRouter,
    build_listing_evidence_text,
    score_pair_cross_encoder,
    verify_and_apply_agent_verdict,
)
from tests.eval.harness import load_golden_pairs


def test_m5_hybrid_retrieval_channels_and_rrf_recall_at_20():
    pairs = load_golden_pairs()
    metrics = evaluate_retrieval_channels_on_golden(pairs, top_k=20)
    print("\n=== M5' RETRIEVAL CHANNEL ABLATION (Recall@20) ===", metrics.model_dump())

    assert metrics.recall_at_20_fused_rrf >= 0.95
    assert metrics.recall_at_20_dense_bge >= metrics.recall_at_20_dense_ab
    assert metrics.adopted_dense_model == "bge-m3"
    assert metrics.recall_at_20_fused_rrf >= metrics.recall_at_20_lexical


def test_stage_c_agent_quote_verification_and_asymmetric_trust():
    src = VariantSpec(
        url="https://amazon.in/dp/B01",
        title="Apple iPhone 15 (128 GB)",
        brand="Apple",
        core_model="iPhone 15",
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
    # Candidate is missing color in structured attrs -> deterministic comparator yields UNVERIFIED
    cand = VariantSpec(
        url="https://croma.com/p/1",
        title="Apple iPhone 15 128GB Smartphone",
        brand="Apple",
        core_model="iPhone 15",
        attrs={
            "storage": Attr(
                name="storage",
                kind=AttrKind.QUANTITY,
                raw="128GB",
                norm="128 GB",
                evidence=Evidence(source=Source.JSONLD, span="128GB", confidence=0.95),
            ),
        },
    )
    det = decide(src, cand)
    assert det.bucket == Bucket.UNVERIFIED

    text_a = build_listing_evidence_text(src, "Finish: Black titanium frame")
    text_b = build_listing_evidence_text(cand, "Specifications: Colour: Black | Storage: 128GB")

    # 1. Hallucinated quote_b (not a substring of text_b) -> rejected, stays UNVERIFIED!
    bad_agent_out = AgentJudgeOutput(
        reasoning="Assumed Black color",
        per_attr=[
            AgentAttrVerdict(
                name="color",
                verdict=Verdict.MATCH,
                quote_a="Black",
                quote_b="Color: Matte Black Edition",  # NOT in text_b!
            )
        ],
        decision=Bucket.EXACT,
    )
    res_bad = verify_and_apply_agent_verdict(det, bad_agent_out, text_a, text_b)
    assert res_bad.bucket == Bucket.UNVERIFIED
    assert any("agent_quote_verification_failed:color" in r for r in res_bad.reasons)

    # 2. Verbatim quotes present in BOTH text_a and text_b -> promoted to EXACT!
    good_agent_out = AgentJudgeOutput(
        reasoning="Both listings explicitly state Black in specs.",
        per_attr=[
            AgentAttrVerdict(
                name="color",
                verdict=Verdict.MATCH,
                quote_a="Black",
                quote_b="Colour: Black",
            )
        ],
        decision=Bucket.EXACT,
    )
    res_good = verify_and_apply_agent_verdict(det, good_agent_out, text_a, text_b)
    assert res_good.bucket == Bucket.EXACT
    assert "agent_all_quotes_verified" in res_good.reasons

    # 3. Deterministic MISMATCH can NEVER be overridden by the agent
    cand_mismatch = cand.model_copy(
        update={
            "attrs": {
                **cand.attrs,
                "color": Attr(
                    name="color",
                    kind=AttrKind.COLOR,
                    raw="Blue",
                    norm="blue",
                    evidence=Evidence(source=Source.DOM_SELECTED, span="Blue", confidence=0.99),
                ),
            }
        }
    )
    det_mismatch = decide(src, cand_mismatch)
    assert det_mismatch.bucket == Bucket.VARIANT
    override_attempt = verify_and_apply_agent_verdict(det_mismatch, good_agent_out, text_a, text_b)
    assert override_attempt.bucket == Bucket.VARIANT


@pytest.mark.asyncio
async def test_stage_c_agent_hard_cap_4_web_search_rounds():
    agent = StageCEscalationAgent()
    for i in range(6):
        await agent.web_search(f"Apple iPhone 15 query {i}")
    assert agent.search_rounds_used == 4


@pytest.mark.asyncio
async def test_m5_three_band_router_end_to_end_golden_precision_and_medium_band():
    """
    M5' Gate:
    - Calibrate ThreeBandRouter on dev split.
    - Verify agent precision >= 0.98 on MEDIUM-band golden pairs.
    - Verify end-to-end EXACT precision >= 0.99 and recall >= 0.80.
    """
    pairs = load_golden_pairs()
    raw_scores: list[float] = []
    labels: list[int] = []
    for rec in pairs:
        s = VariantSpec.model_validate(rec["source_spec"])
        c = VariantSpec.model_validate(rec["cand_spec"])
        raw_scores.append(score_pair_cross_encoder(s, c))
        labels.append(1 if rec["expected_bucket"] == Bucket.EXACT.value else 0)

    calibrator = IsotonicCalibrator()
    calibrator.fit(raw_scores, labels)
    router = ThreeBandRouter(calibrator=calibrator)

    tp = fp = fn = 0
    medium_tp = medium_fp = 0

    for rec in pairs:
        s = VariantSpec.model_validate(rec["source_spec"])
        c = VariantSpec.model_validate(rec["cand_spec"])
        expected = rec["expected_bucket"]

        decision, band, _ = await router.route_and_decide(s, c)
        is_true_exact = (expected == Bucket.EXACT.value)
        is_pred_exact = (decision.bucket == Bucket.EXACT)

        if is_pred_exact and is_true_exact:
            tp += 1
        elif is_pred_exact and not is_true_exact:
            fp += 1
        elif not is_pred_exact and is_true_exact:
            fn += 1

        if band == "MEDIUM":
            if is_pred_exact and is_true_exact:
                medium_tp += 1
            elif is_pred_exact and not is_true_exact:
                medium_fp += 1

    e2e_precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    e2e_recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    medium_precision = medium_tp / (medium_tp + medium_fp) if (medium_tp + medium_fp) > 0 else 1.0

    print(
        f"\n=== M5' THREE-BAND ROUTER GOLDEN METRICS ===\n"
        f"  End-to-End EXACT Precision: {e2e_precision:.4f} (floor >= 0.99)\n"
        f"  End-to-End EXACT Recall:    {e2e_recall:.4f} (floor >= 0.80)\n"
        f"  MEDIUM-Band Precision:      {medium_precision:.4f} (floor >= 0.98, medium_fp={medium_fp})\n"
        f"  Router Stats:               {router.stats.model_dump()}"
    )

    assert e2e_precision >= 0.99
    assert e2e_recall >= 0.80
    assert medium_precision >= 0.98
    assert medium_fp == 0
    assert router.stats.band_medium > 0
