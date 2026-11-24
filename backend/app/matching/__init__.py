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
from app.matching.spec_builder import (
    build_variant_spec,
    extract_selected_variant_from_html,
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
    "build_variant_spec",
    "decide",
    "extract_selected_variant_from_html",
    "select_highest_precedence_attr",
    "verify_llm_span",
]
