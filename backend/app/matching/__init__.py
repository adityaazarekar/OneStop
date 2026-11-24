"""Exact-variant product matching package (contracts, normalizers, deterministic comparator)."""
from app.matching.comparator import decide
from app.matching.contracts import (
    Attr,
    AttrDiff,
    AttrKind,
    Bucket,
    Evidence,
    MatchDecision,
    Source,
    SOURCE_PRECEDENCE,
    VariantSpec,
    Verdict,
    select_highest_precedence_attr,
    verify_llm_span,
)

__all__ = [
    "Attr",
    "AttrDiff",
    "AttrKind",
    "Bucket",
    "Evidence",
    "MatchDecision",
    "SOURCE_PRECEDENCE",
    "Source",
    "VariantSpec",
    "Verdict",
    "decide",
    "select_highest_precedence_attr",
    "verify_llm_span",
]
