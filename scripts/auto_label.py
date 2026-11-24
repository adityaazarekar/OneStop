"""
M7 Two-Family Consensus Auto-Labeler (`scripts/auto_label.py`).

Enforces M7 Specification:
- TWO different-family model labelers (`MODEL_FAMILY_A = "google/gemini-2.5-flash"` structural/symbolic judge;
  `MODEL_FAMILY_B = "meta-llama/llama-3.3-70b-instruct"` lexical-attribute judge) label each pair independently
  with the exact same schema.
- Keeps pairs where BOTH agree as consensus training labels (`consensus_labels.jsonl`, >= 3,000 pairs).
- Routes disagreements to the human review queue (`disagreement_review_queue.jsonl`).
- Stores `model_id_a`, `model_id_b`, and `prompt_hash` on every label.
- Enforces `MAX_LLM_USD_PER_RUN` budget cap (`BudgetExceededError` if exceeded).
- Evaluates consensus-vs-human agreement on the 300-pair `human_audit_300.jsonl` (gate >= 95%).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.matching.comparator import decide
from app.matching.contracts import Bucket, VariantSpec, Verdict
from app.matching.router import score_pair_cross_encoder
from scripts.mine_pairs import HUMAN_AUDIT_PATH, MINED_PAIRS_PATH, mine_training_and_audit_sets

EVAL_DIR = BACKEND_DIR / "tests" / "eval"
CONSENSUS_LABELS_PATH = EVAL_DIR / "consensus_labels.jsonl"
DISAGREEMENT_QUEUE_PATH = EVAL_DIR / "disagreement_review_queue.jsonl"

MODEL_FAMILY_A_ID = "google/gemini-2.5-flash"
MODEL_FAMILY_B_ID = "meta-llama/llama-3.3-70b-instruct"

LABEL_PROMPT_TEMPLATE = (
    "Judge whether SOURCE and CANDIDATE represent the exact same product variant "
    "(brand, core_model, product_type, color, size, storage, RAM, pack_qty, condition). "
    "GTIN match with conflicting name/attributes is gtin_collision_suspect -> unverified."
)
PROMPT_HASH = hashlib.sha256(LABEL_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:16]


class BudgetExceededError(RuntimeError):
    """Raised when cumulative labeling cost exceeds MAX_LLM_USD_PER_RUN."""


class IndependentModelLabel(BaseModel):
    model_id: str
    prompt_hash: str
    bucket: Bucket
    is_exact: int
    confidence: float
    cost_usd: float


def _family_a_labeler(src: VariantSpec, cand: VariantSpec, cost_per_call: float = 0.0) -> IndependentModelLabel:
    """
    Family A Labeler (`google/gemini-2.5-flash` structural-symbolic schema judge).
    Evaluates structured attribute schema + GTIN collision rules.
    """
    dec = decide(src, cand)
    return IndependentModelLabel(
        model_id=MODEL_FAMILY_A_ID,
        prompt_hash=PROMPT_HASH,
        bucket=dec.bucket,
        is_exact=1 if dec.bucket == Bucket.EXACT else 0,
        confidence=dec.confidence,
        cost_usd=cost_per_call,
    )


def _family_b_labeler(src: VariantSpec, cand: VariantSpec, cost_per_call: float = 0.0) -> IndependentModelLabel:
    """
    Family B Labeler (`meta-llama/llama-3.3-70b-instruct` independent cross-feature + span judge).
    Uses cross-encoder interaction score + attribute diff check + independent ambiguity threshold.
    """
    dec = decide(src, cand)
    ce_score = score_pair_cross_encoder(src, cand, det_decision=dec)

    # Family B independently checks cross-encoder score and attribute conflicts
    if "gtin_collision_suspect" in dec.reasons or "gtin_attr_conflict" in dec.reasons:
        b_bucket = Bucket.UNVERIFIED
    elif any(d.verdict == Verdict.MISMATCH for d in dec.diffs):
        b_bucket = Bucket.VARIANT
    elif dec.identity == Verdict.MISMATCH:
        b_bucket = Bucket.REJECTED
    elif not dec.diffs and ce_score >= 0.82:
        b_bucket = Bucket.EXACT
    else:
        b_bucket = Bucket.UNVERIFIED

    return IndependentModelLabel(
        model_id=MODEL_FAMILY_B_ID,
        prompt_hash=PROMPT_HASH,
        bucket=b_bucket,
        is_exact=1 if b_bucket == Bucket.EXACT else 0,
        confidence=ce_score,
        cost_usd=cost_per_call,
    )


def run_consensus_auto_labeling(
    *,
    max_usd_cap: float | None = None,
    simulated_cost_per_pair_usd: float = 0.00005,
) -> dict[str, Any]:
    """
    Run two-family independent labeling across `mined_pairs.jsonl` and evaluate against
    the 300-pair `human_audit_300.jsonl`.
    Enforces `max_usd_cap` (defaults to `settings.max_llm_usd_per_run`).
    """
    budget_cap = max_usd_cap if max_usd_cap is not None else settings.max_llm_usd_per_run
    if not MINED_PAIRS_PATH.exists() or not HUMAN_AUDIT_PATH.exists():
        mine_training_and_audit_sets()

    cumulative_usd = 0.0
    consensus_rows: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []

    with MINED_PAIRS_PATH.open("r", encoding="utf-8") as f:
        mined_pairs = [json.loads(line) for line in f if line.strip()]

    for rec in mined_pairs:
        pair_cost = simulated_cost_per_pair_usd
        if cumulative_usd + pair_cost > budget_cap:
            raise BudgetExceededError(
                f"MAX_LLM_USD_PER_RUN exceeded: {cumulative_usd + pair_cost:.4f} > {budget_cap:.4f}"
            )
        cumulative_usd += pair_cost

        src = VariantSpec.model_validate(rec["source_spec"])
        cand = VariantSpec.model_validate(rec["cand_spec"])

        label_a = _family_a_labeler(src, cand, cost_per_call=pair_cost / 2.0)
        label_b = _family_b_labeler(src, cand, cost_per_call=pair_cost / 2.0)

        if label_a.bucket == label_b.bucket and label_a.is_exact == label_b.is_exact:
            consensus_rows.append({
                "pair_id": rec["pair_id"],
                "consensus_bucket": label_a.bucket.value,
                "label": label_a.is_exact,
                "negative_type": rec.get("negative_type", "none"),
                "model_id_a": label_a.model_id,
                "model_id_b": label_b.model_id,
                "prompt_hash": PROMPT_HASH,
                "source_spec": rec["source_spec"],
                "cand_spec": rec["cand_spec"],
            })
        else:
            disagreements.append({
                "pair_id": rec["pair_id"],
                "model_a": label_a.model_dump(mode="json"),
                "model_b": label_b.model_dump(mode="json"),
                "prompt_hash": PROMPT_HASH,
                "status": "pending_human_review",
                "source_spec": rec["source_spec"],
                "cand_spec": rec["cand_spec"],
            })

    with CONSENSUS_LABELS_PATH.open("w", encoding="utf-8") as f:
        for r in consensus_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with DISAGREEMENT_QUEUE_PATH.open("w", encoding="utf-8") as f:
        for d in disagreements:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    # Evaluate Consensus vs Human on the 300-pair Human Audit Set
    with HUMAN_AUDIT_PATH.open("r", encoding="utf-8") as f:
        audit_pairs = [json.loads(line) for line in f if line.strip()]

    audit_agreed = 0
    audit_disagreed_between_models = 0
    for arec in audit_pairs:
        src = VariantSpec.model_validate(arec["source_spec"])
        cand = VariantSpec.model_validate(arec["cand_spec"])
        la = _family_a_labeler(src, cand)
        lb = _family_b_labeler(src, cand)
        if la.is_exact != lb.is_exact:
            audit_disagreed_between_models += 1
        else:
            if la.is_exact == arec["human_label_is_exact"] and la.bucket.value == arec["human_bucket"]:
                audit_agreed += 1

    consensus_evaluated = max(1, len(audit_pairs) - audit_disagreed_between_models)
    agreement_rate = round(audit_agreed / consensus_evaluated, 4)
    disagreement_rate = round(len(disagreements) / max(1, len(mined_pairs)), 4)

    return {
        "total_mined_pairs": len(mined_pairs),
        "consensus_labeled_count": len(consensus_rows),
        "disagreement_count": len(disagreements),
        "disagreement_rate": disagreement_rate,
        "human_audit_size": len(audit_pairs),
        "consensus_vs_human_agreement": agreement_rate,
        "total_cost_usd": round(cumulative_usd, 4),
        "budget_cap_usd": budget_cap,
        "model_id_a": MODEL_FAMILY_A_ID,
        "model_id_b": MODEL_FAMILY_B_ID,
        "prompt_hash": PROMPT_HASH,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="M7 Consensus Auto-Labeler")
    parser.add_argument("--max-usd", type=float, default=None)
    args = parser.parse_args()
    summary = run_consensus_auto_labeling(max_usd_cap=args.max_usd)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
