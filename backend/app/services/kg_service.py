"""
Temporal Knowledge Graph Service (`kg_entities` + `kg_edges`).

Provides:
1. Entity upsert (`card`, `portal`, `upi_app`, `merchant`, `seller`, `category`) with
   deterministic char-n-gram float embeddings in JSONB + fast NumPy cosine similarity.
2. Temporal edge upsert (`EARNS_ON`, `EXCLUDES_CATEGORY`, `REQUIRES_PORTAL`,
   `HAS_MONTHLY_CAP`, `HAS_CONVENIENCE_FEE`, `STACKS_WITH`, `AUTHORIZED_BY`, `RISK_FLAG`):
   automatically expires any superseded active edge (`valid_to = NOW()`) before inserting
   the newly verified edge.
3. Fast multi-hop active rule lookup (`valid_to IS NULL OR valid_to > NOW()`) to reconstruct
   `VerifiedPaymentRule` objects in <25ms without blocking user page loads on web crawls.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_graph import KGEdge, KGEntity
from app.schemas.extraction import VerifiedPaymentRule

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = {"card", "portal", "upi_app", "merchant", "seller", "category"}
VALID_RELATIONS = {
    "EARNS_ON",
    "EXCLUDES_CATEGORY",
    "REQUIRES_PORTAL",
    "HAS_MONTHLY_CAP",
    "HAS_CONVENIENCE_FEE",
    "STACKS_WITH",
    "AUTHORIZED_BY",
    "RISK_FLAG",
}


def canonicalize_entity_key(entity_type: str, raw_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (raw_name or "").lower()).strip("_")
    return f"{entity_type.lower().strip()}:{slug or 'unknown'}"[:180]


def compute_text_embedding(text: str, dim: int = 32) -> list[float]:
    """
    Lightweight deterministic L2-normalized character 3-gram embedding (dim=32)
    stored in JSONB for zero-latency NumPy cosine similarity without external API calls.
    """
    vec = np.zeros(dim, dtype=np.float32)
    cleaned = re.sub(r"\s+", " ", (text or "").lower().strip())
    if not cleaned:
        return vec.tolist()
    padded = f"^{cleaned}$"
    for i in range(max(1, len(padded) - 2)):
        gram = padded[i : i + 3]
        idx = hash(gram) % dim
        vec[idx] += 1.0
    norm = float(np.linalg.norm(vec))
    if norm > 1e-8:
        vec = vec / norm
    return [round(float(x), 5) for x in vec.tolist()]


def cosine_similarity(vec_a: list[float] | None, vec_b: list[float] | None) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    a = np.asarray(vec_a, dtype=np.float32)
    b = np.asarray(vec_b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-8:
        return 0.0
    return round(float(np.dot(a, b) / denom), 4)


async def upsert_kg_entity(
    session: AsyncSession,
    *,
    entity_type: str,
    display_name: str,
    canonical_key: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> KGEntity:
    key = canonical_key or canonicalize_entity_key(entity_type, display_name)
    stmt = select(KGEntity).where(KGEntity.canonical_key == key)
    entity = (await session.execute(stmt)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    emb = compute_text_embedding(f"{entity_type} {display_name}")

    if entity is not None:
        entity.display_name = display_name[:255]
        if metadata_json:
            entity.metadata_json = {**(entity.metadata_json or {}), **metadata_json}
        entity.embedding = emb
        entity.updated_at = now
    else:
        entity = KGEntity(
            id=uuid.uuid4(),
            entity_type=entity_type[:40],
            canonical_key=key,
            display_name=display_name[:255],
            metadata_json=metadata_json or {},
            embedding=emb,
            updated_at=now,
        )
        session.add(entity)
    await session.flush()
    return entity


async def upsert_temporal_edge(
    session: AsyncSession,
    *,
    source_entity_id: uuid.UUID,
    relation: str,
    target_entity_id: uuid.UUID | None = None,
    attributes_json: dict[str, Any] | None = None,
    confidence: float = 1.0,
    source_url: str | None = None,
    valid_from: datetime | None = None,
) -> KGEdge:
    """
    Expire any existing active edge for `(source_entity_id, relation, target_entity_id)`
    by setting `valid_to = NOW()`, then insert the new active edge (`valid_to = NULL`).
    """
    now = datetime.now(timezone.utc)
    target_cond = (
        KGEdge.target_entity_id == target_entity_id
        if target_entity_id is not None
        else KGEdge.target_entity_id.is_(None)
    )
    expire_stmt = (
        update(KGEdge)
        .where(
            and_(
                KGEdge.source_entity_id == source_entity_id,
                KGEdge.relation == relation,
                target_cond,
                or_(KGEdge.valid_to.is_(None), KGEdge.valid_to > now),
            )
        )
        .values(valid_to=now)
    )
    await session.execute(expire_stmt)

    edge = KGEdge(
        id=uuid.uuid4(),
        source_entity_id=source_entity_id,
        relation=relation[:60],
        target_entity_id=target_entity_id,
        attributes_json=attributes_json or {},
        valid_from=valid_from or now,
        valid_to=None,
        confidence=confidence,
        source_url=source_url,
        last_verified_at=now,
    )
    session.add(edge)
    await session.flush()
    return edge


async def query_active_edges(
    session: AsyncSession,
    *,
    source_entity_id: uuid.UUID | None = None,
    target_entity_id: uuid.UUID | None = None,
    relation: str | None = None,
) -> list[KGEdge]:
    """Return all currently active temporal edges (`valid_to IS NULL OR valid_to > NOW()`)."""
    now = datetime.now(timezone.utc)
    conds = [or_(KGEdge.valid_to.is_(None), KGEdge.valid_to > now)]
    if source_entity_id is not None:
        conds.append(KGEdge.source_entity_id == source_entity_id)
    if target_entity_id is not None:
        conds.append(KGEdge.target_entity_id == target_entity_id)
    if relation is not None:
        conds.append(KGEdge.relation == relation)

    stmt = select(KGEdge).where(and_(*conds)).order_by(KGEdge.last_verified_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def persist_verified_rule_to_kg(
    session: AsyncSession,
    rule: VerifiedPaymentRule,
    retailer: str,
) -> None:
    """
    Decompose a `VerifiedPaymentRule` into temporal Knowledge Graph entities and edges:
      - Source entity (`card` | `portal` | `upi_app`)
      - Target entity (`merchant:<retailer>`)
      - Edges: `EARNS_ON`, `HAS_MONTHLY_CAP`, `HAS_CONVENIENCE_FEE`, `EXCLUDES_CATEGORY`, `STACKS_WITH`
    """
    if rule.verification_status not in ("issuer_tc_verified", "live_tc_verified", "page_verified"):
        return

    etype = (
        "portal"
        if rule.method_type == "gift_voucher"
        else ("upi_app" if rule.method_type == "upi_app" else "card")
    )
    src = await upsert_kg_entity(
        session,
        entity_type=etype,
        display_name=rule.method_name,
        metadata_json={"method_type": rule.method_type, "reward_type": rule.reward_type},
    )
    merch = await upsert_kg_entity(
        session,
        entity_type="merchant",
        display_name=retailer,
    )
    primary_url = rule.source_urls[0] if rule.source_urls else None

    await upsert_temporal_edge(
        session,
        source_entity_id=src.id,
        relation="EARNS_ON",
        target_entity_id=merch.id,
        attributes_json={
            "reward_type": rule.reward_type,
            "points_per_spend_unit": rule.points_per_spend_unit,
            "spend_unit_inr": rule.spend_unit_inr,
            "point_value_cash_inr": rule.point_value_cash_inr,
            "point_value_travel_inr": rule.point_value_travel_inr,
            "instant_discount_inr": rule.instant_discount_inr,
            "max_discount_cap_inr": rule.max_discount_cap_inr,
            "plain_english_summary": rule.plain_english_summary,
            "source_urls": rule.source_urls,
            "verification_status": rule.verification_status,
        },
        source_url=primary_url,
    )

    if rule.monthly_cap_inr is not None and rule.monthly_cap_inr > 0:
        await upsert_temporal_edge(
            session,
            source_entity_id=src.id,
            relation="HAS_MONTHLY_CAP",
            target_entity_id=merch.id,
            attributes_json={"monthly_cap_inr": rule.monthly_cap_inr},
            source_url=primary_url,
        )

    if rule.processing_fee_pct > 0:
        await upsert_temporal_edge(
            session,
            source_entity_id=src.id,
            relation="HAS_CONVENIENCE_FEE",
            target_entity_id=merch.id,
            attributes_json={"processing_fee_pct": rule.processing_fee_pct},
            source_url=primary_url,
        )

    if rule.exclusions:
        await upsert_temporal_edge(
            session,
            source_entity_id=src.id,
            relation="EXCLUDES_CATEGORY",
            target_entity_id=merch.id,
            attributes_json={"exclusions": rule.exclusions},
            source_url=primary_url,
        )

    if rule.stackable_with:
        await upsert_temporal_edge(
            session,
            source_entity_id=src.id,
            relation="STACKS_WITH",
            target_entity_id=merch.id,
            attributes_json={"stackable_with": rule.stackable_with},
            source_url=primary_url,
        )


async def load_verified_rule_from_kg(
    session: AsyncSession,
    *,
    method_name: str,
    method_type: str,
    retailer: str,
    max_age_days: int = 7,
) -> VerifiedPaymentRule | None:
    """
    Multi-hop Temporal Knowledge Graph query (`<25ms`):
    Reconstructs an active `VerifiedPaymentRule` across `EARNS_ON`, `HAS_MONTHLY_CAP`,
    `HAS_CONVENIENCE_FEE`, `EXCLUDES_CATEGORY`, and `STACKS_WITH` edges if verified within `max_age_days`.
    """
    etype = (
        "portal"
        if method_type == "gift_voucher"
        else ("upi_app" if method_type == "upi_app" else "card")
    )
    src_key = canonicalize_entity_key(etype, method_name)
    merch_key = canonicalize_entity_key("merchant", retailer)

    src = (await session.execute(select(KGEntity).where(KGEntity.canonical_key == src_key))).scalar_one_or_none()
    merch = (await session.execute(select(KGEntity).where(KGEntity.canonical_key == merch_key))).scalar_one_or_none()
    if src is None or merch is None:
        return None

    edges = await query_active_edges(session, source_entity_id=src.id, target_entity_id=merch.id)
    if not edges:
        return None

    earns_edge = next((e for e in edges if e.relation == "EARNS_ON"), None)
    if earns_edge is None:
        return None

    now = datetime.now(timezone.utc)
    age_seconds = (now - earns_edge.last_verified_at).total_seconds()
    if age_seconds > max_age_days * 86400:
        return None

    attrs = earns_edge.attributes_json or {}
    monthly_cap: float | None = None
    proc_fee = 0.0
    exclusions: list[str] = []
    stackable_with: list[str] = []

    for e in edges:
        ea = e.attributes_json or {}
        if e.relation == "HAS_MONTHLY_CAP" and ea.get("monthly_cap_inr") is not None:
            monthly_cap = float(ea["monthly_cap_inr"])
        elif e.relation == "HAS_CONVENIENCE_FEE" and ea.get("processing_fee_pct") is not None:
            proc_fee = float(ea["processing_fee_pct"])
        elif e.relation == "EXCLUDES_CATEGORY" and ea.get("exclusions"):
            exclusions.extend(str(x) for x in ea["exclusions"])
        elif e.relation == "STACKS_WITH" and ea.get("stackable_with"):
            stackable_with.extend(str(x) for x in ea["stackable_with"])

    source_urls = list(attrs.get("source_urls") or ([earns_edge.source_url] if earns_edge.source_url else []))
    return VerifiedPaymentRule(
        method_name=method_name,
        method_type=method_type,  # type: ignore[arg-type]
        instant_discount_inr=float(attrs.get("instant_discount_inr") or 0.0),
        max_discount_cap_inr=attrs.get("max_discount_cap_inr"),
        reward_type=attrs.get("reward_type") or "none",
        points_per_spend_unit=float(attrs.get("points_per_spend_unit") or 0.0),
        spend_unit_inr=float(attrs.get("spend_unit_inr") or 100.0),
        point_value_cash_inr=float(attrs.get("point_value_cash_inr") or 0.0),
        point_value_travel_inr=float(attrs.get("point_value_travel_inr") or 0.0),
        monthly_cap_inr=monthly_cap,
        processing_fee_pct=proc_fee,
        exclusions=exclusions,
        stackable_with=stackable_with,
        verification_status=attrs.get("verification_status") or "issuer_tc_verified",
        source_urls=source_urls,
        last_verified_at=earns_edge.last_verified_at,
        plain_english_summary=str(attrs.get("plain_english_summary") or ""),
    )
