"""Add attribute_aliases, serp_cache, decision_traces, and review_queue tables.

Revision ID: 004_matching_tables
Revises: 003_price_obs_index
Create Date: 2026-11-24 18:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004_matching_tables"
down_revision: Union[str, None] = "003_price_obs_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attribute_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attr_name", sa.String(length=64), nullable=False, index=True),
        sa.Column("alias_value", sa.String(length=128), nullable=False, index=True),
        sa.Column("canonical_value", sa.String(length=128), nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=False, server_default="human_review"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "serp_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("query_hash", sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column("query_text", sa.String(length=500), nullable=False),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("credits_used", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "decision_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cand_spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, index=True, server_default=sa.text("now()")),
    )
    op.create_table(
        "review_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("decision_trace_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("cand_url", sa.String(length=1000), nullable=False),
        sa.Column("reported_issue", sa.Text(), nullable=False, server_default="wrong_variant"),
        sa.Column("source_spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cand_spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, index=True, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("review_queue")
    op.drop_table("decision_traces")
    op.drop_table("serp_cache")
    op.drop_table("attribute_aliases")
