"""
SQLAlchemy models for Exact-Variant Matching persistence (M6):
- `AttributeAlias`: human-reviewed attribute value equivalences ("Midnight" = "black")
- `SerpCache`: 6-hour Serper Shopping response cache + credit log
- `DecisionTrace`: pair-level audit trace (`source_spec_json`, `cand_spec_json`, `decision_json`, `versions`, `ts`)
- `ReviewQueueItem`: user "Wrong variant?" feedback & human review queue
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AttributeAlias(BaseModelTable := Base):
    __tablename__ = "attribute_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attr_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    alias_value: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    canonical_value: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False, default="human_review")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class SerpCache(Base):
    __tablename__ = "serp_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    credits_used: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DecisionTrace(Base):
    __tablename__ = "decision_traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cand_spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    versions: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {"comparator": "v2.0", "normalizer": "v2.0", "router": "m5_three_band"},
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc),
    )


class ReviewQueueItem(Base):
    __tablename__ = "review_queue"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_trace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    cand_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    reported_issue: Mapped[str] = mapped_column(Text, nullable=False, default="wrong_variant")
    source_spec_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cand_spec_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
