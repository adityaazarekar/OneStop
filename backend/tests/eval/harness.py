"""
M3 Evaluation Harness & Old-vs-New Matcher Comparison (`backend/tests/eval/harness.py`).

Computes and reports:
- `exact_precision` (CI floor >= 0.99)
- `exact_recall` (CI floor >= 0.80)
- Per-slice metrics across all M3 + Addendum v2 slices:
    `exact_positive`, `single_attr_flip`, `missing_attr_positive`,
    `pack_size_trap`, `hinglish_transliterated`, `gtin_collision`, `hard_model_reject`
- Per-attribute confusion matrix (`color`, `size`, `pack_qty`, `storage`, `condition`, `unit_size`)
- `abstention_rate` (share routed to `unverified`)
- `hallucinated_span_rate` (must be 0.0)
- `p95_stage1_latency_ms` (must be < 500ms)
- Old (`product_matcher.classify_store_candidate`) vs New (`app.matching.comparator.decide`) comparison table
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.matching.comparator import decide
from app.matching.contracts import Bucket, Source, VariantSpec, Verdict, verify_llm_span
from app.services.product_matcher import classify_store_candidate

EVAL_DIR = Path(__file__).resolve().parent
GOLDEN_PATH = EVAL_DIR / "golden.jsonl"
REPORT_PATH = EVAL_DIR / "latest_eval_report.json"


def load_golden_pairs(path: Path = GOLDEN_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        import sys
        repo_root = path.resolve().parent.parent.parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from scripts.label import write_golden_jsonl
        write_golden_jsonl(path)
    pairs: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def _spec_to_legacy_dict(spec: VariantSpec) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for k, a in spec.attrs.items():
        if k == "storage" and a.num is not None:
            d["storage_gb"] = int(a.num)
        elif k == "ram" and a.num is not None:
            d["ram_gb"] = int(a.num)
        else:
            d[k] = a.norm or a.raw
    return d


def evaluate_old_matcher(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Run legacy `classify_store_candidate` across the golden dataset for baseline comparison."""
    tp = fp = fn = 0
    slice_false_exacts: dict[str, int] = {}
    slice_totals: dict[str, int] = {}

    for rec in pairs:
        sl = rec["slice"]
        slice_totals[sl] = slice_totals.get(sl, 0) + 1
        src = VariantSpec.model_validate(rec["source_spec"])
        cand = VariantSpec.model_validate(rec["cand_spec"])
        expected = rec["expected_bucket"]

        # Legacy matcher also fast-forwards on identical mpn_or_ean (GTIN) in match_or_create
        if src.gtin and cand.gtin and src.gtin == cand.gtin:
            pred_exact = True
        else:
            dec = classify_store_candidate(
                src.title,
                cand.title,
                target_brand=src.brand,
                target_specs=_spec_to_legacy_dict(src),
                candidate_specs=_spec_to_legacy_dict(cand),
                candidate_price=float(cand.listed_price or 1.0),
                in_stock=True,
                retailer_key="amazon_in",
            )
            pred_exact = (dec.accepted and dec.bucket == "exact_match")

        is_true_exact = (expected == Bucket.EXACT.value)
        if pred_exact and is_true_exact:
            tp += 1
        elif pred_exact and not is_true_exact:
            fp += 1
            slice_false_exacts[sl] = slice_false_exacts.get(sl, 0) + 1
        elif not pred_exact and is_true_exact:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return {
        "exact_tp": tp,
        "exact_fp": fp,
        "exact_fn": fn,
        "exact_precision": round(precision, 4),
        "exact_recall": round(recall, 4),
        "slice_false_exacts": slice_false_exacts,
        "slice_totals": slice_totals,
    }


def evaluate_new_comparator(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Run deterministic `app.matching.comparator.decide` across golden dataset and verify all floors."""
    tp = fp = fn = 0
    abstained = 0
    hallucinated_spans_surviving = 0
    total_llm_spans_checked = 0
    latencies_ms: list[float] = []
    offending_exact_errors: list[dict[str, Any]] = []

    slice_stats: dict[str, dict[str, int]] = {}
    attr_confusion: dict[str, dict[str, int]] = {}

    for rec in pairs:
        sl = rec["slice"]
        s_stat = slice_stats.setdefault(sl, {"total": 0, "bucket_correct": 0, "false_exact": 0})
        s_stat["total"] += 1

        src = VariantSpec.model_validate(rec["source_spec"])
        cand = VariantSpec.model_validate(rec["cand_spec"])
        expected_bucket = rec["expected_bucket"]

        # Verify span grounding on any LLM_SPAN attributes
        for spec_obj in (src, cand):
            for k, a in list(spec_obj.attrs.items()):
                if a.evidence.source == Source.LLM_SPAN:
                    total_llm_spans_checked += 1
                    verified = verify_llm_span(a, spec_obj.title)
                    if verified is None:
                        del spec_obj.attrs[k]
                    elif not a.evidence.span or a.evidence.span not in spec_obj.title:
                        hallucinated_spans_surviving += 1

        t0 = time.perf_counter()
        decision = decide(src, cand)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(dt_ms)

        pred_bucket = decision.bucket.value
        if pred_bucket == Bucket.UNVERIFIED.value:
            abstained += 1

        if pred_bucket == expected_bucket:
            s_stat["bucket_correct"] += 1

        is_true_exact = (expected_bucket == Bucket.EXACT.value)
        is_pred_exact = (pred_bucket == Bucket.EXACT.value)

        if is_pred_exact and is_true_exact:
            tp += 1
        elif is_pred_exact and not is_true_exact:
            fp += 1
            s_stat["false_exact"] += 1
            offending_exact_errors.append({
                "pair_id": rec["pair_id"],
                "slice": sl,
                "expected_bucket": expected_bucket,
                "predicted_bucket": pred_bucket,
                "reasons": decision.reasons,
                "diffs": [d.model_dump(mode="json") for d in decision.diffs],
            })
        elif not is_pred_exact and is_true_exact:
            fn += 1

        # Per-attribute confusion matrix
        diff_map = {d.name: d.verdict.value for d in decision.diffs}
        for attr_name, truth_v in (rec.get("per_attr_truth") or {}).items():
            # If not in diff_map and decision was not REJECTED, comparator judged it as match
            pred_v = diff_map.get(attr_name, Verdict.MATCH.value)
            if attr_name in ("pack_qty", "unit_size") and "pack_config" in diff_map:
                pred_v = diff_map["pack_config"]
            key = f"truth_{truth_v}__pred_{pred_v}"
            amap = attr_confusion.setdefault(attr_name, {})
            amap[key] = amap.get(key, 0) + 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    lat_sorted = sorted(latencies_ms)
    p95_idx = min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95)) if lat_sorted else 0
    p95_ms = round(lat_sorted[p95_idx], 4) if lat_sorted else 0.0

    slice_summary = {}
    for sl, st_d in slice_stats.items():
        tot = st_d["total"]
        fe = st_d["false_exact"]
        slice_summary[sl] = {
            "total": tot,
            "bucket_accuracy": round(st_d["bucket_correct"] / tot, 4) if tot else 1.0,
            "false_exact_count": fe,
            "exact_safety_precision": round(1.0 - (fe / tot), 4) if tot else 1.0,
        }

    return {
        "total_pairs": len(pairs),
        "unique_products": len({r["product_id"] for r in pairs}),
        "exact_tp": tp,
        "exact_fp": fp,
        "exact_fn": fn,
        "exact_precision": round(precision, 4),
        "exact_recall": round(recall, 4),
        "abstention_rate": round(abstained / len(pairs), 4) if pairs else 0.0,
        "hallucinated_span_rate": round(hallucinated_spans_surviving / max(1, total_llm_spans_checked), 4),
        "p95_stage1_latency_ms": p95_ms,
        "slices": slice_summary,
        "attr_confusion": attr_confusion,
        "offending_exact_errors": offending_exact_errors,
    }


def run_full_eval_comparison(save_report: bool = True) -> dict[str, Any]:
    pairs = load_golden_pairs()
    old_res = evaluate_old_matcher(pairs)
    new_res = evaluate_new_comparator(pairs)
    report = {
        "old_matcher": old_res,
        "new_comparator": new_res,
    }
    if save_report:
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
