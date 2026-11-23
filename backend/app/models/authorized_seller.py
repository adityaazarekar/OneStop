from __future__ import annotations

import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuthorizedSeller(Base):
    __tablename__ = "authorized_sellers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    retailer: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    seller_name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand_scope: Mapped[str] = mapped_column(String(100), nullable=False)
    trust_tier: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="authorized"
    )

