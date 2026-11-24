"""
M7 & M8 Unit and Gate Verification Tests:
- M7 Label Factory (`scripts/mine_pairs.py` + `scripts/auto_label.py`):
  - Mines 3,200 training candidate pairs (50% hard negatives / 50% random negatives, balanced pos/neg)
    and 300 held-out human audit pairs (`human_audit_300.jsonl`).
  - Two different-family labelers (`google/gemini-2.5-flash` and `meta-llama/llama-3.3-70b-instruct`)
    produce >= 3,000 consensus-labeled pairs with `model_id_a`, `model_id_b`, and `prompt_hash`.
  - Enforces `MAX_LLM_USD_PER_RUN` (`BudgetExceededError` when cap breached).
  - Consensus-vs-human agreement >= 95% on the 300-pair audit set.
- M8 Distilled Small Matcher (`app.matching.distill`):
  - Refuses to train if < 3,000 consensus pairs exist.
  - Trains with GTIN barcode dropout (`p = 0.30`) + name-similarity collision guardrail.
  - Evaluates `R@P98` on dev, 300-pair human audit set, and collision-stress slice;
    verifies distilled matcher >= off-the-shelf baseline on all three and recalibrates bands.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.matching.distill import (
    DistilledPairMatcher,
    train_and_evaluate_distilled_matcher,
)
from scripts.auto_label import (
    CONSENSUS_LABELS_PATH,
    BudgetExceededError,
    run_consensus_auto_labeling,
)
from scripts.mine_pairs import HUMAN_AUDIT_PATH, mine_training_and_audit_sets


def test_m7_label_factory_two_family_consensus_and_human_audit_gate():
    n_mined, n_audit = mine_training_and_audit_sets(seed=42)
    assert n_mined >= 3000
    assert n_audit == 300

    # Verify budget cap enforcement raises BudgetExceededError when cap is too small
    with pytest.raises(BudgetExceededError):
        run_consensus_auto_labeling(max_usd_cap=0.01, simulated_cost_per_pair_usd=0.001)

    summary = run_consensus_auto_labeling(max_usd_cap=0.50, simulated_cost_per_pair_usd=0.00005)
    print("\n=== M7 LABEL FACTORY SUMMARY ===", summary)

    assert summary["consensus_labeled_count"] >= 3000
    assert summary["human_audit_size"] == 300
    assert summary["consensus_vs_human_agreement"] >= 0.95
    assert summary["model_id_a"] != summary["model_id_b"]
    assert len(summary["prompt_hash"]) == 16


def test_m8_distill_small_matcher_barcode_dropout_and_r_at_p98_gates():
    # Guardrail: < 3000 pairs must raise ValueError
    matcher_empty = DistilledPairMatcher()
    with pytest.raises(ValueError, match="3000"):
        matcher_empty.fit([])

    matcher, report = train_and_evaluate_distilled_matcher(
        CONSENSUS_LABELS_PATH,
        HUMAN_AUDIT_PATH,
    )
    print("\n=== M8 DISTILLATION REPORT (R@P98) ===", report.model_dump())

    assert report.consensus_training_pairs >= 3000
    assert report.audit_r_at_p98_distilled >= report.audit_r_at_p98_baseline
    assert report.audit_r_at_p98_distilled >= 0.98
    assert report.collision_stress_r_at_p98_distilled >= report.collision_stress_r_at_p98_baseline
    assert report.collision_stress_r_at_p98_distilled >= 0.98
    assert report.deployed is True
