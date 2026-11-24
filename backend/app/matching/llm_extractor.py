"""
Span-Grounded LLM Variant Attribute Extractor (`backend/app/matching/llm_extractor.py`).

Enforces Hard Invariant #5 & M4 Candidate Evidence Rules:
- LLM extraction schema requires `{value, span}` per attribute.
- Backend verifies that `span` is a non-empty verbatim substring of `page_text` (`span in page_text`).
- Any attribute whose `span` is not found verbatim in `page_text` is logged and dropped to `UNKNOWN`
  so surviving hallucinated-span rate is strictly 0.0.
- Cached in Redis / memory by `sha256(page_text)`.
- Uses `settings.llm_model` with 4.0s timeout and deterministic fallback.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.matching.contracts import (
    Attr,
    AttrKind,
    Evidence,
    Source,
    VariantSpec,
    select_highest_precedence_attr,
    verify_llm_span,
)
from app.matching.spec_builder import _build_typed_attr, build_variant_spec
from app.schemas.extraction import ClientPayload

logger = logging.getLogger(__name__)

LLM_SPAN_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_MEMORY_SPAN_CACHE: dict[str, dict[str, Any]] = {}
_HALLUCINATED_SPANS_DROPPED: int = 0
_TOTAL_SPANS_EVALUATED: int = 0


class SpanValue(BaseModel):
    value: str
    span: str = Field(description="Exact verbatim substring copied directly from the input text")


class SpanGroundedExtractionSchema(BaseModel):
    brand: SpanValue | None = None
    core_model: SpanValue | None = None
    product_type: SpanValue | None = None
    attributes: dict[str, SpanValue] = Field(
        default_factory=dict,
        description="Map of attribute name (color, size, storage, ram, pack_qty, unit_size, condition, mpn) to {value, span}",
    )


def get_span_verification_stats() -> dict[str, int]:
    return {
        "total_spans_evaluated": _TOTAL_SPANS_EVALUATED,
        "hallucinated_spans_dropped": _HALLUCINATED_SPANS_DROPPED,
    }


def apply_span_grounded_extraction(
    base_spec: VariantSpec,
    extracted: SpanGroundedExtractionSchema,
    page_text: str,
) -> tuple[VariantSpec, list[str]]:
    """
    Merge LLM-extracted `SpanGroundedExtractionSchema` into `base_spec` AFTER verifying
    every `span` is a verbatim substring of `page_text`.
    Returns `(updated_spec, dropped_attr_names)`.
    """
    global _HALLUCINATED_SPANS_DROPPED, _TOTAL_SPANS_EVALUATED
    dropped: list[str] = []
    updated_attrs = dict(base_spec.attrs)

    for attr_name, sv in (extracted.attributes or {}).items():
        _TOTAL_SPANS_EVALUATED += 1
        if not sv or not sv.value or not sv.span or not sv.span.strip() or sv.span not in page_text:
            _HALLUCINATED_SPANS_DROPPED += 1
            dropped.append(attr_name)
            logger.warning(
                "Dropped unverified LLM span for attr=%r: value=%r span=%r not in page_text",
                attr_name,
                sv.value if sv else None,
                sv.span if sv else None,
            )
            continue

        candidate_attr = _build_typed_attr(
            attr_name,
            sv.value,
            Source.LLM_SPAN,
            span=sv.span,
            confidence=0.75,
        )
        if candidate_attr is None:
            continue

        verified_attr = verify_llm_span(candidate_attr, page_text)
        if verified_attr is None:
            _HALLUCINATED_SPANS_DROPPED += 1
            dropped.append(attr_name)
            continue

        existing = updated_attrs.get(verified_attr.name)
        if existing is not None and (existing.raw or existing.norm or existing.num is not None):
            updated_attrs[verified_attr.name] = select_highest_precedence_attr([existing, verified_attr])
        else:
            updated_attrs[verified_attr.name] = verified_attr

    brand = base_spec.brand
    if not brand and extracted.brand and extracted.brand.span and extracted.brand.span in page_text:
        brand = extracted.brand.value.strip()

    core_model = base_spec.core_model
    if (not core_model or len(core_model) < 2) and extracted.core_model and extracted.core_model.span and extracted.core_model.span in page_text:
        core_model = extracted.core_model.value.strip()

    product_type = base_spec.product_type
    if not product_type and extracted.product_type and extracted.product_type.span and extracted.product_type.span in page_text:
        product_type = extracted.product_type.value.strip().lower()

    return (
        base_spec.model_copy(
            update={
                "brand": brand,
                "core_model": core_model,
                "product_type": product_type,
                "attrs": updated_attrs,
            }
        ),
        dropped,
    )


async def extract_variant_spec_with_llm_fallback(
    payload: ClientPayload,
    html: str | None = None,
    *,
    page_text: str | None = None,
    redis_client: Any = None,
    allow_llm: bool = False,
) -> VariantSpec:
    """
    Build `VariantSpec` from PDP `ClientPayload` + `html` (Tier 0/1/2), and optionally invoke
    span-grounded Tier 3 LLM extraction (`4.0s` timeout, `sha256(input)` cache, verbatim span check).
    """
    text_context = page_text or f"{payload.title or ''}\n{json.dumps(payload.variant_specs or {}, ensure_ascii=False)}"
    base_spec = build_variant_spec(payload, html=html, source_text_for_llm=text_context)

    if not allow_llm or not settings.openrouter_api_key:
        return base_spec

    input_digest = hashlib.sha256(text_context.encode("utf-8")).hexdigest()
    cache_key = f"llm_span_spec:{input_digest}"

    raw_schema: SpanGroundedExtractionSchema | None = None
    if cache_key in _MEMORY_SPAN_CACHE:
        raw_schema = SpanGroundedExtractionSchema.model_validate(_MEMORY_SPAN_CACHE[cache_key])
    elif redis_client is not None:
        try:
            c_str = await redis_client.get(cache_key)
            if c_str:
                raw_schema = SpanGroundedExtractionSchema.model_validate_json(c_str)
        except Exception:
            pass

    if raw_schema is None:
        try:
            import instructor
            from openai import AsyncOpenAI

            raw_client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                timeout=4.0,
            )
            client = instructor.from_openai(raw_client, mode=instructor.Mode.JSON)
            raw_schema = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.llm_model or settings.openrouter_model,
                    response_model=SpanGroundedExtractionSchema,
                    max_retries=1,
                    temperature=0.0,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Extract product variant attributes. For every value you extract, "
                                "you MUST copy the exact verbatim substring (`span`) from the input text. "
                                "Never guess or paraphrase spans."
                            ),
                        },
                        {"role": "user", "content": text_context[:3500]},
                    ],
                ),
                timeout=4.0,
            )
            _MEMORY_SPAN_CACHE[cache_key] = raw_schema.model_dump(mode="json")
            if redis_client is not None:
                try:
                    await redis_client.set(cache_key, raw_schema.model_dump_json(), ex=LLM_SPAN_CACHE_TTL_SECONDS)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("LLM span extraction fallback to deterministic spec: %s", exc)
            return base_spec

    merged_spec, _ = apply_span_grounded_extraction(base_spec, raw_schema, text_context)
    return merged_spec
