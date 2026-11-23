"""
Seed script: populates ONLY real reference data —
15 Indian credit cards (accurate base reward rates) and
14 known authorized e-commerce sellers.

All product, price, seller, and bank-offer data must come from
real-time extraction (Extension / App / server scraper).

Usage:
    cd backend
    python -m seed
"""
from __future__ import annotations

import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuthorizedSeller, CreditCard

engine = create_engine(settings.database_url_sync, echo=False)

# ────────────────────────────────────────────
# 15 Indian Credit / Debit Cards (2026 rates)
# ────────────────────────────────────────────
CREDIT_CARDS: list[dict] = [
    {
        "bank_name": "HDFC",
        "card_name": "HDFC Infinia",
        "card_network": "visa",
        "base_reward_pct": 3.3,
        "amazon_reward_pct": 3.3,
        "flipkart_reward_pct": 3.3,
        "annual_fee": 12500.0,
    },
    {
        "bank_name": "HDFC",
        "card_name": "HDFC Regalia Gold",
        "card_network": "visa",
        "base_reward_pct": 1.3,
        "amazon_reward_pct": 1.3,
        "flipkart_reward_pct": 1.3,
        "annual_fee": 2500.0,
    },
    {
        "bank_name": "HDFC",
        "card_name": "HDFC Millennia",
        "card_network": "mastercard",
        "base_reward_pct": 1.0,
        "amazon_reward_pct": 2.5,
        "flipkart_reward_pct": 2.5,
        "annual_fee": 1000.0,
    },
    {
        "bank_name": "SBI",
        "card_name": "SBI Cashback",
        "card_network": "visa",
        "base_reward_pct": 5.0,
        "amazon_reward_pct": 5.0,
        "flipkart_reward_pct": 5.0,
        "annual_fee": 999.0,
    },
    {
        "bank_name": "ICICI",
        "card_name": "ICICI Amazon Pay",
        "card_network": "visa",
        "base_reward_pct": 1.0,
        "amazon_reward_pct": 5.0,
        "flipkart_reward_pct": 1.0,
        "annual_fee": 500.0,
    },
    {
        "bank_name": "ICICI",
        "card_name": "ICICI Sapphiro",
        "card_network": "visa",
        "base_reward_pct": 2.0,
        "amazon_reward_pct": 2.0,
        "flipkart_reward_pct": 2.0,
        "annual_fee": 3500.0,
    },
    {
        "bank_name": "Axis",
        "card_name": "Axis Flipkart",
        "card_network": "visa",
        "base_reward_pct": 1.5,
        "amazon_reward_pct": 1.5,
        "flipkart_reward_pct": 5.0,
        "annual_fee": 500.0,
    },
    {
        "bank_name": "Axis",
        "card_name": "Axis Ace",
        "card_network": "visa",
        "base_reward_pct": 2.0,
        "amazon_reward_pct": 5.0,
        "flipkart_reward_pct": 4.0,
        "annual_fee": 499.0,
    },
    {
        "bank_name": "Axis",
        "card_name": "Axis Atlas",
        "card_network": "visa",
        "base_reward_pct": 2.0,
        "amazon_reward_pct": 2.0,
        "flipkart_reward_pct": 2.0,
        "annual_fee": 5000.0,
    },
    {
        "bank_name": "Amex",
        "card_name": "Amex MRCC",
        "card_network": "amex",
        "base_reward_pct": 1.0,
        "amazon_reward_pct": 1.0,
        "flipkart_reward_pct": 1.0,
        "annual_fee": 3500.0,
    },
    {
        "bank_name": "Amex",
        "card_name": "Amex SmartEarn",
        "card_network": "amex",
        "base_reward_pct": 0.5,
        "amazon_reward_pct": 10.0,
        "flipkart_reward_pct": 10.0,
        "annual_fee": 495.0,
    },
    {
        "bank_name": "OneCard",
        "card_name": "OneCard Metal",
        "card_network": "visa",
        "base_reward_pct": 5.0,
        "amazon_reward_pct": 5.0,
        "flipkart_reward_pct": 5.0,
        "annual_fee": 0.0,
    },
    {
        "bank_name": "IDFC",
        "card_name": "IDFC FIRST Classic",
        "card_network": "visa",
        "base_reward_pct": 0.75,
        "amazon_reward_pct": 0.75,
        "flipkart_reward_pct": 0.75,
        "annual_fee": 0.0,
    },
    {
        "bank_name": "AU",
        "card_name": "AU Ixigo",
        "card_network": "rupay",
        "base_reward_pct": 1.0,
        "amazon_reward_pct": 1.0,
        "flipkart_reward_pct": 1.0,
        "annual_fee": 0.0,
    },
    {
        "bank_name": "Kotak",
        "card_name": "Kotak 811 #DreamDifferent",
        "card_network": "visa",
        "base_reward_pct": 1.0,
        "amazon_reward_pct": 1.0,
        "flipkart_reward_pct": 1.0,
        "annual_fee": 0.0,
    },
]

# ────────────────────────────────────────────
# Authorized Sellers (real Indian e-com sellers)
# ────────────────────────────────────────────
AUTHORIZED_SELLERS: list[dict] = [
    # Amazon.in
    {"retailer": "amazon_in", "seller_name": "Appario Retail", "brand_scope": "all", "trust_tier": "official"},
    {"retailer": "amazon_in", "seller_name": "Cloudtail India", "brand_scope": "all", "trust_tier": "official"},
    {"retailer": "amazon_in", "seller_name": "RetailNet", "brand_scope": "all", "trust_tier": "premium"},
    {"retailer": "amazon_in", "seller_name": "Cocoblu Retail", "brand_scope": "all", "trust_tier": "premium"},
    {"retailer": "amazon_in", "seller_name": "SuperComNet", "brand_scope": "all", "trust_tier": "authorized"},
    {"retailer": "amazon_in", "seller_name": "ClickTech Retail", "brand_scope": "all", "trust_tier": "authorized"},
    {"retailer": "amazon_in", "seller_name": "SVPeripherals", "brand_scope": "electronics", "trust_tier": "authorized"},
    {"retailer": "amazon_in", "seller_name": "Treasure Haul Online", "brand_scope": "electronics", "trust_tier": "authorized"},
    # Flipkart
    {"retailer": "flipkart", "seller_name": "RetailNet", "brand_scope": "all", "trust_tier": "official"},
    {"retailer": "flipkart", "seller_name": "SuperComNet", "brand_scope": "all", "trust_tier": "premium"},
    {"retailer": "flipkart", "seller_name": "OmniTechRetail", "brand_scope": "electronics", "trust_tier": "authorized"},
    {"retailer": "flipkart", "seller_name": "TrueComRetail", "brand_scope": "electronics", "trust_tier": "authorized"},
    # Croma
    {"retailer": "croma", "seller_name": "Croma - A Tata Enterprise", "brand_scope": "all", "trust_tier": "official"},
    {"retailer": "croma", "seller_name": "Infiniti Retail", "brand_scope": "all", "trust_tier": "official"},
]


def seed() -> None:
    with Session(engine) as session:
        # Idempotency check
        if session.execute(select(CreditCard).limit(1)).scalar_one_or_none():
            print("Database already seeded - skipping.")
            return

        for cc_data in CREDIT_CARDS:
            session.add(CreditCard(id=uuid.uuid4(), **cc_data))
        print(f"  + {len(CREDIT_CARDS)} credit cards")

        for seller_data in AUTHORIZED_SELLERS:
            session.add(AuthorizedSeller(id=uuid.uuid4(), **seller_data))
        print(f"  + {len(AUTHORIZED_SELLERS)} authorized sellers")

        session.commit()
        print("Seed complete. Products & prices will come from live extraction.")


if __name__ == "__main__":
    seed()
