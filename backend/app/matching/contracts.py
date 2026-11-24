"""
Contracts for Exact-Variant Product Matching (Pydantic v2, fully typed).

Enforces:
- Closed attribute kind schema (`AttrKind`) with open-vocabulary values
- Explicit evidence provenance & precedence (`DOM_SELECTED > URL_PARAM > JSONLD > HYDRATION > TITLE_PARSE > LLM_SPAN`)
- Asymmetric trust & mandatory verbatim substring verification for `LLM_SPAN` attributes
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


class Source(StrEnum):
    JSONLD = "jsonld"
    HYDRATION = "hydration"
    DOM_SELECTED = "dom_selected"
    URL_PARAM = "url_param"
    TITLE_PARSE = "title_parse"
    LLM_SPAN = "llm_span"


# Higher integer = higher precedence when one attribute has multiple candidate values on a page:
# DOM_SELECTED > URL_PARAM > JSONLD > HYDRATION > TITLE_PARSE > LLM_SPAN
SOURCE_PRECEDENCE: dict[Source, int] = {
    Source.DOM_SELECTED: 6,
    Source.URL_PARAM: 5,
    Source.JSONLD: 4,
    Source.HYDRATION: 3,
    Source.TITLE_PARSE: 2,
    Source.LLM_SPAN: 1,
}


class AttrKind(StrEnum):
    QUANTITY = "quantity"
    COUNT = "count"
    COLOR = "color"
    SIZE = "size"
    IDENT = "ident"
    ENUM = "enum"
    SET = "set"


class Evidence(BaseModel):
    source: Source
    span: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Attr(BaseModel):
    name: str
    kind: AttrKind
    raw: str
    norm: str | None = None
    num: Decimal | None = None
    unit: str | None = None
    evidence: Evidence


class VariantSpec(BaseModel):
    url: str
    title: str
    brand: str | None = None
    product_type: str | None = None
    core_model: str
    gtin: str | None = None
    mpn: str | None = None
    attrs: dict[str, Attr] = Field(default_factory=dict)
    image_urls: list[str] = Field(default_factory=list)
    listed_price: Decimal | None = None


class AttrDiff(BaseModel):
    name: str
    source_value: str | None = None
    cand_value: str | None = None
    verdict: Verdict


class Bucket(StrEnum):
    EXACT = "exact_match"
    UNVERIFIED = "unverified"
    VARIANT = "variant_mismatch"
    REJECTED = "rejected"


class MatchDecision(BaseModel):
    bucket: Bucket
    identity: Verdict
    diffs: list[AttrDiff] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    judged_by: Literal["deterministic", "cross_encoder", "llm"] = "deterministic"
    total_quantity_source: Decimal | None = None
    total_quantity_cand: Decimal | None = None
    unit_price_source: Decimal | None = None
    unit_price_cand: Decimal | None = None
    unit_price_unit: str | None = None


def select_highest_precedence_attr(candidates: list[Attr]) -> Attr:
    """
    Given multiple extractions of the same attribute from a single page,
    pick the one with highest evidence precedence:
      DOM_SELECTED > URL_PARAM > JSONLD > HYDRATION > TITLE_PARSE > LLM_SPAN
    Tie-breaker: higher evidence.confidence.
    """
    if not candidates:
        raise ValueError("candidates must be non-empty")
    return max(
        candidates,
        key=lambda a: (SOURCE_PRECEDENCE.get(a.evidence.source, 0), a.evidence.confidence),
    )


def verify_llm_span(attr: Attr, source_text: str) -> Attr | None:
    """
    Enforce Hard Invariant #5 on LLM-extracted attributes:
    If `attr.evidence.source == Source.LLM_SPAN`, its `evidence.span` MUST be a non-empty
    verbatim substring of `source_text`. Otherwise the value is dropped (returns None)
    so downstream treats the attribute as UNKNOWN.
    """
    if attr.evidence.source != Source.LLM_SPAN:
        return attr
    span = attr.evidence.span
    if not span or not span.strip() or not source_text or span not in source_text:
        return None
    return attr
