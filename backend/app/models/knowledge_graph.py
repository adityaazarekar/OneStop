"""
PostgreSQL Temporal Knowledge Graph models (`kg_entities` and `kg_edges`).

Stores institutional knowledge across user sessions:
- Entities: cards, voucher portals (SmartBuy/GyFTR), UPI apps, merchants, sellers, categories.
- Temporal Edges: EARNS_ON, EXCLUDES_CATEGORY, REQUIRES_PORTAL, HAS_MONTHLY_CAP,
  HAS_CONVENIENCE_FEE, STACKS_WITH, AUTHORIZED_BY, RISK_FLAG.
- Superseded edges are expired via `valid_to = NOW()` rather than deleted, preserving
  historical auditability while fast queries filter `valid_to IS NULL OR valid_to > NOW()`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KGEntity(Base):
    __tablename__ = "kg_entities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entity_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        comment="card | portal | upi_app | merchant | seller | category",
    )
    canonical_key: Mapped[str] = mapped_column(
        String(180), nullable=False, unique=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Normalized float embedding stored in JSONB (compatible with all TimescaleDB/Postgres builds;
    # ranked via fast NumPy cosine similarity in kg_service.py).
    embedding: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_kg_entities_entity_type", "entity_type"),
        Index("ix_kg_entities_canonical_key", "canonical_key", unique=True),
    )


class KGEdge(Base):
    __tablename__ = "kg_edges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kg_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        comment=(
            "EARNS_ON | EXCLUDES_CATEGORY | REQUIRES_PORTAL | HAS_MONTHLY_CAP | "
            "HAS_CONVENIENCE_FEE | STACKS_WITH | AUTHORIZED_BY | RISK_FLAG"
        ),
    )
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kg_entities.id", ondelete="CASCADE"),
        nullable=True,
    )
    attributes_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default="1.0"
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_kg_edges_source_relation", "source_entity_id", "relation"),
        Index("ix_kg_edges_target_relation", "target_entity_id", "relation"),
        Index("ix_kg_edges_valid_to", "valid_to"),
    )
