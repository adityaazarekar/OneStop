from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.retailer_listing import RetailerListing


class BankOfferRule(Base):
    __tablename__ = "bank_offer_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retailer_listings.id"),
        nullable=False,
        index=True,
    )
    retailer: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    card_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    min_txn_amount: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    discount_flat: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    discount_pct: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    max_discount_cap: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    excluded_cards: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    listing: Mapped[RetailerListing] = relationship(
        back_populates="bank_offer_rules"
    )

