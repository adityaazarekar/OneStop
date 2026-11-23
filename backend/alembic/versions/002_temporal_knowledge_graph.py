"""temporal knowledge graph tables (kg_entities, kg_edges)

Revision ID: 002_temporal_kg
Revises: 001_initial
Create Date: 2026-11-23 17:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "002_temporal_kg"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safely probe if pgvector is installed on this TimescaleDB instance without aborting transaction
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
                CREATE EXTENSION IF NOT EXISTS vector;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END
        $$;
        """
    )

    op.create_table(
        "kg_entities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("canonical_key", sa.String(180), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("metadata_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("embedding", JSONB, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_kg_entities_entity_type", "kg_entities", ["entity_type"])
    op.create_index(
        "ix_kg_entities_canonical_key",
        "kg_entities",
        ["canonical_key"],
        unique=True,
    )

    op.create_table(
        "kg_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("kg_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(60), nullable=False),
        sa.Column(
            "target_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("kg_entities.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("attributes_json", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "last_verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_kg_edges_source_relation",
        "kg_edges",
        ["source_entity_id", "relation"],
    )
    op.create_index(
        "ix_kg_edges_target_relation",
        "kg_edges",
        ["target_entity_id", "relation"],
    )
    op.create_index("ix_kg_edges_valid_to", "kg_edges", ["valid_to"])


def downgrade() -> None:
    op.drop_index("ix_kg_edges_valid_to", table_name="kg_edges")
    op.drop_index("ix_kg_edges_target_relation", table_name="kg_edges")
    op.drop_index("ix_kg_edges_source_relation", table_name="kg_edges")
    op.drop_table("kg_edges")
    op.drop_index("ix_kg_entities_canonical_key", table_name="kg_entities")
    op.drop_index("ix_kg_entities_entity_type", table_name="kg_entities")
    op.drop_table("kg_entities")
