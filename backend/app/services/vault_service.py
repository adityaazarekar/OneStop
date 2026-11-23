"""
Warranty vault service.

Processes uploaded PDF invoices:
  1. Extracts text via pdfplumber
  2. Sends to LLM parser for structured field extraction
  3. Saves WarrantyVaultItem with computed warranty_expiry_date
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.warranty_vault import WarrantyVaultItem
from app.services.llm_parser import get_llm_parser, LLMParserError


async def process_invoice(
    session: AsyncSession,
    file_bytes: bytes,
    user_email: str,
) -> WarrantyVaultItem:
    """Extract invoice → save to warranty vault."""
    import pdfplumber

    text_parts: list[str] = []
    with pdfplumber.open_from_bytes(file_bytes) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

    if not text_parts:
        raise ValueError("Could not extract text from PDF. The file may be image-based.")

    invoice_text = "\n\n".join(text_parts)

    parser = await get_llm_parser()
    extracted = await parser.parse_invoice(invoice_text)

    # Compute warranty expiry
    try:
        purchase_dt = date.fromisoformat(extracted.purchase_date)
    except (ValueError, TypeError):
        purchase_dt = date.today()

    warranty_expiry = purchase_dt + relativedelta(months=extracted.warranty_months)

    item = WarrantyVaultItem(
        id=uuid.uuid4(),
        user_email=user_email,
        product_name=extracted.product_name,
        retailer=extracted.retailer,
        purchase_date=purchase_dt,
        price_paid=extracted.price_paid,
        seller_name=extracted.seller_name,
        invoice_number=extracted.invoice_number,
        warranty_months=extracted.warranty_months,
        warranty_expiry_date=warranty_expiry,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def get_vault_items(
    session: AsyncSession,
    user_email: str,
) -> list[WarrantyVaultItem]:
    """Return all vault items for a user."""
    stmt = (
        select(WarrantyVaultItem)
        .where(WarrantyVaultItem.user_email == user_email)
        .order_by(WarrantyVaultItem.warranty_expiry_date.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def days_remaining(item: WarrantyVaultItem) -> int:
    """Days until warranty expiry (negative = expired)."""
    return (item.warranty_expiry_date - date.today()).days
