"""Add composite index on price_observations(listing_id, observed_at DESC).

Revision ID: 003_price_obs_index
Revises: 002_temporal_kg
Create Date: 2026-11-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003_price_obs_index"
down_revision: Union[str, None] = "002_temporal_kg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_price_observations_listing_observed_desc "
        "ON price_observations (listing_id, observed_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_price_observations_listing_observed_desc;")
