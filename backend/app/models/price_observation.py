from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.retailer_listing import RetailerListing


class PriceObservation(Base):
    __tablename__ = "price_observations"

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=text("now()")
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retailer_listings.id"),
        primary_key=True,
    )
    listed_price: Mapped[float] = mapped_column(Float, nullable=False)
    coupon_discount: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    best_bank_discount: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    in_stock: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'server'")
    )

    listing: Mapped[RetailerListing] = relationship(
        back_populates="price_observations"
    )

