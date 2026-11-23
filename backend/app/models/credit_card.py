from __future__ import annotations

import uuid

from sqlalchemy import Float, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CreditCard(Base):
    __tablename__ = "credit_cards_catalog"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    card_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    card_network: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="visa"
    )
    base_reward_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    amazon_reward_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    flipkart_reward_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    annual_fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    referral_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

