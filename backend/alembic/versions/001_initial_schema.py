"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Extensions ---
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # --- canonical_products ---
    op.create_table(
        "canonical_products",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("brand", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("mpn_or_ean", sa.String(100), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("variant_specs", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_canonical_products_brand", "canonical_products", ["brand"])
    op.create_index(
        "ix_canonical_products_model_name", "canonical_products", ["model_name"]
    )
    op.create_index("ix_canonical_products_category", "canonical_products", ["category"])
    op.create_index(
        "uq_canonical_products_mpn_or_ean",
        "canonical_products",
        ["mpn_or_ean"],
        unique=True,
    )
    op.execute(
        "CREATE INDEX ix_canonical_products_model_name_trgm "
        "ON canonical_products USING gin (model_name gin_trgm_ops);"
    )
    op.execute(
        "CREATE INDEX ix_canonical_products_brand_trgm "
        "ON canonical_products USING gin (brand gin_trgm_ops);"
    )

    # --- retailer_listings ---
    op.create_table(
        "retailer_listings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "canonical_product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("canonical_products.id"),
            nullable=False,
        ),
        sa.Column("retailer", sa.String(20), nullable=False),
        sa.Column("external_sku", sa.String(100), nullable=False),
        sa.Column("listing_url", sa.String(1000), nullable=False),
        sa.Column("affiliate_url", sa.String(1000), nullable=True),
        sa.Column("current_listed_price", sa.Float, nullable=False),
        sa.Column("current_mrp", sa.Float, nullable=True),
        sa.Column("in_stock", sa.Boolean, server_default=sa.text("true")),
        sa.Column("seller_name", sa.String(255), nullable=True),
        sa.Column("seller_rating", sa.Float, nullable=True),
        sa.Column("trust_score", sa.Integer, nullable=True),
        sa.Column("match_confidence", sa.Float, nullable=True),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "retailer", "external_sku", name="uq_retailer_listings_retailer_sku"
        ),
    )
    op.create_index(
        "ix_retailer_listings_canonical_product_id",
        "retailer_listings",
        ["canonical_product_id"],
    )

    # --- price_observations (TimescaleDB hypertable) ---
    op.create_table(
        "price_observations",
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            primary_key=True,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "listing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("retailer_listings.id"),
            primary_key=True,
        ),
        sa.Column("listed_price", sa.Float, nullable=False),
        sa.Column("coupon_discount", sa.Float, nullable=False, server_default="0"),
        sa.Column(
            "best_bank_discount", sa.Float, nullable=False, server_default="0"
        ),
        sa.Column("in_stock", sa.Boolean, server_default=sa.text("true")),
        sa.Column("source", sa.String(20), nullable=False, server_default="server"),
    )
    op.execute(
        "SELECT create_hypertable('price_observations', 'observed_at', "
        "migrate_data => true);"
    )

    # --- bank_offer_rules ---
    op.create_table(
        "bank_offer_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "listing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("retailer_listings.id"),
            nullable=False,
        ),
        sa.Column("retailer", sa.String(20), nullable=False),
        sa.Column("bank_name", sa.String(100), nullable=False),
        sa.Column("card_type", sa.String(50), nullable=True),
        sa.Column("min_txn_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("discount_flat", sa.Float, nullable=False, server_default="0"),
        sa.Column("discount_pct", sa.Float, nullable=False, server_default="0"),
        sa.Column("max_discount_cap", sa.Float, nullable=False, server_default="0"),
        sa.Column("excluded_cards", JSONB, nullable=True),
        sa.Column("raw_terms", sa.Text, nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_bank_offer_rules_listing_id", "bank_offer_rules", ["listing_id"]
    )

    # --- credit_cards_catalog ---
    op.create_table(
        "credit_cards_catalog",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("bank_name", sa.String(100), nullable=False),
        sa.Column("card_name", sa.String(200), nullable=False, unique=True),
        sa.Column("card_network", sa.String(20), nullable=False, server_default="visa"),
        sa.Column(
            "base_reward_pct", sa.Float, nullable=False, server_default="0"
        ),
        sa.Column(
            "amazon_reward_pct", sa.Float, nullable=False, server_default="0"
        ),
        sa.Column(
            "flipkart_reward_pct", sa.Float, nullable=False, server_default="0"
        ),
        sa.Column("annual_fee", sa.Float, nullable=False, server_default="0"),
        sa.Column("referral_url", sa.String(1000), nullable=True),
    )

    # --- authorized_sellers ---
    op.create_table(
        "authorized_sellers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("retailer", sa.String(20), nullable=False),
        sa.Column("seller_name", sa.String(255), nullable=False),
        sa.Column("brand_scope", sa.String(100), nullable=False),
        sa.Column(
            "trust_tier", sa.String(20), nullable=False, server_default="authorized"
        ),
    )
    op.create_index("ix_authorized_sellers_retailer", "authorized_sellers", ["retailer"])

    # --- warranty_vault_items ---
    op.create_table(
        "warranty_vault_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_email", sa.String(255), nullable=False),
        sa.Column("product_name", sa.String(500), nullable=False),
        sa.Column("retailer", sa.String(20), nullable=True),
        sa.Column("purchase_date", sa.Date, nullable=False),
        sa.Column("price_paid", sa.Float, nullable=False),
        sa.Column("seller_name", sa.String(255), nullable=True),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("warranty_months", sa.Integer, nullable=False, server_default="12"),
        sa.Column("warranty_expiry_date", sa.Date, nullable=False),
        sa.Column(
            "listing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("retailer_listings.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_warranty_vault_items_user_email", "warranty_vault_items", ["user_email"]
    )


def downgrade() -> None:
    op.drop_table("warranty_vault_items")
    op.drop_table("authorized_sellers")
    op.drop_table("credit_cards_catalog")
    op.drop_table("bank_offer_rules")
    op.drop_table("price_observations")
    op.drop_table("retailer_listings")
    op.drop_table("canonical_products")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
    op.execute("DROP EXTENSION IF EXISTS timescaledb;")

