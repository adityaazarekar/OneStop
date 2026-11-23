from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WarrantyVaultItem(Base):
    __tablename__ = "warranty_vault_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    retailer: Mapped[str | None] = mapped_column(String(20), nullable=True)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    price_paid: Mapped[float] = mapped_column(Float, nullable=False)
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    warranty_months: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("12")
    )
    warranty_expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("retailer_listings.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

