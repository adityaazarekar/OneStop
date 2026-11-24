from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.bank_offer_rule import BankOfferRule
    from app.models.canonical_product import CanonicalProduct
    from app.models.price_observation import PriceObservation


class RetailerListing(Base):
    __tablename__ = "retailer_listings"
    __table_args__ = (
        UniqueConstraint(
            "retailer", "external_sku", name="uq_retailer_listings_retailer_sku"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonical_product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_products.id"),
        nullable=False,
        index=True,
    )
    retailer: Mapped[str] = mapped_column(String(20), nullable=False)
    external_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    listing_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    affiliate_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    current_listed_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_mrp: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_stock: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seller_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    trust_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    canonical_product: Mapped[CanonicalProduct] = relationship(
        back_populates="listings"
    )
    price_observations: Mapped[list[PriceObservation]] = relationship(
        back_populates="listing", lazy="noload"
    )
    bank_offer_rules: Mapped[list[BankOfferRule]] = relationship(
        back_populates="listing", lazy="noload"
    )

