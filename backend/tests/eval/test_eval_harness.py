"""
CI-Gated Evaluation Suite (`pytest -m eval`).

Replays `golden.jsonl` (>=60 products across electronics, fashion, grocery, appliance
and all M3 Additions hard-negative & collision slices) offline with zero network calls.
Enforces:
- Unique products >= 60
- `exact_precision >= 0.99` (0 false exacts across all slices)
- `exact_recall >= 0.80`
- `hallucinated_span_rate == 0.0`
- `p95_stage1_latency_ms < 500.0`
- Every M3 slice (`single_attr_flip`, `missing_attr_positive`, `pack_size_trap`,
  `hinglish_transliterated`, `gtin_collision`, `hard_model_reject`, `exact_positive`)
  achieves >= 0.99 exact-safety precision.
"""
from __future__ import annotations

import pytest

from tests.eval.harness import run_full_eval_comparison


@pytest.mark.eval
def test_m3_golden_eval_floors_and_old_vs_new_comparison():
    report = run_full_eval_comparison(save_report=True)
    old_m = report["old_matcher"]
    new_m = report["new_comparator"]

    print(
        f"\n=== M3 GOLDEN EVAL: OLD MATCHER vs NEW COMPARATOR ({new_m['unique_products']} products, {new_m['total_pairs']} pairs) ===\n"
        f"  Metric                  | Old Matcher | New Comparator | Floor\n"
        f"  ------------------------+-------------+----------------+-------\n"
        f"  Exact-Bucket Precision  | {old_m['exact_precision']:<11.4f} | {new_m['exact_precision']:<14.4f} | >= 0.99\n"
        f"  Exact-Bucket Recall     | {old_m['exact_recall']:<11.4f} | {new_m['exact_recall']:<14.4f} | >= 0.80\n"
        f"  False Exact Count (P0)  | {old_m['exact_fp']:<11d} | {new_m['exact_fp']:<14d} | == 0\n"
        f"  Hallucinated Span Rate  | N/A         | {new_m['hallucinated_span_rate']:<14.4f} | == 0.0\n"
        f"  Stage-1 p95 Latency (ms)| N/A         | {new_m['p95_stage1_latency_ms']:<14.4f} | < 500ms"
    )

    assert new_m["unique_products"] >= 60, f"Expected >= 60 unique products, got {new_m['unique_products']}"
    assert not new_m["offending_exact_errors"], (
        f"P0 False-Exact Variant Error(s) detected: {new_m['offending_exact_errors']}"
    )
    assert new_m["exact_precision"] >= 0.99
    assert new_m["exact_recall"] >= 0.80
    assert new_m["hallucinated_span_rate"] == 0.0
    assert new_m["p95_stage1_latency_ms"] < 500.0

    required_slices = (
        "exact_positive",
        "single_attr_flip",
        "missing_attr_positive",
        "pack_size_trap",
        "hinglish_transliterated",
        "gtin_collision",
        "hard_model_reject",
    )
    for sl in required_slices:
        assert sl in new_m["slices"], f"Missing required eval slice: {sl}"
        sl_data = new_m["slices"][sl]
        assert sl_data["exact_safety_precision"] >= 0.99, (
            f"Slice '{sl}' breached precision floor: {sl_data}"
        )
        assert sl_data["bucket_accuracy"] >= 0.99, (
            f"Slice '{sl}' breached bucket accuracy floor: {sl_data}"
        )

    # Confirm new comparator strictly outperforms old matcher on exact precision
    assert new_m["exact_precision"] > old_m["exact_precision"]
