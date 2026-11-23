"""
Product matcher: links incoming payloads to canonical_products.

1. Exact match on mpn_or_ean
2. pg_trgm similarity on model_name + strict equality on critical variant specs
3. Create new canonical product if no match found
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_product import CanonicalProduct
from app.models.retailer_listing import RetailerListing
from app.schemas.extraction import ClientPayload, VariantAttributes


CRITICAL_VARIANT_KEYS = ("ram_gb", "storage_gb", "color")
SIMILARITY_THRESHOLD = 0.3

GENERIC_PAGE_PHRASES = [
    "all categories",
    "buy products online",
    "online shopping site",
    "online shopping in india",
    "search results",
    "deal of the day",
    "never overpay",
    "for you",
    "smart-wagon",
    "cart",
    "checkout",
    "login",
    "sign in",
    "your account",
    "amazon.in",
    "flipkart.com",
]


def is_generic_or_invalid_listing(
    title: str | None,
    listed_price: float | None,
    url: str | None = None,
) -> tuple[bool, str]:
    """Check whether a payload represents a generic/category/cart/empty page rather than a real product."""
    if listed_price is None or listed_price <= 0:
        return True, "Listed price is missing or <= ₹0"

    t = (title or "").strip().lower()
    if not t or len(t) < 4:
        return True, "Product title is empty or too short"

    for phrase in GENERIC_PAGE_PHRASES:
        if phrase in t:
            return True, f"Generic page title detected: '{phrase}'"

    u = (url or "").lower()
    if any(p in u for p in ("/cart/", "/smart-wagon", "/checkout", "localhost", "/s?", "/search?")):
        return True, "URL is a cart, checkout, or search results page"

    return False, ""


def extract_variant_specs_from_title(title: str) -> dict:
    """Extract storage, RAM, tier modifier, and accessory flags from title text."""
    import re
    specs: dict = {}
    if not title:
        return specs

    # Storage: 128 GB, 128GB, 1 TB, 1TB
    sm = re.search(r"\b(32|64|128|256|512)\s*(?:GB|gb)\b", title)
    if sm:
        specs["storage_gb"] = int(sm.group(1))
    else:
        tb_m = re.search(r"\b(1|2)\s*(?:TB|tb)\b", title)
        if tb_m:
            specs["storage_gb"] = int(tb_m.group(1)) * 1024

    # RAM: e.g. 8GB RAM
    rm = re.search(r"\b(4|6|8|12|16|18|24|32|64)\s*(?:GB|gb)\s*RAM\b", title, re.I)
    if rm:
        specs["ram_gb"] = int(rm.group(1))

    # Modifiers
    t_lower = title.lower()
    if "pro max" in t_lower:
        specs["tier"] = "pro_max"
    elif "pro" in t_lower:
        specs["tier"] = "pro"
    elif "ultra" in t_lower:
        specs["tier"] = "ultra"
    elif "plus" in t_lower:
        specs["tier"] = "plus"
    elif "mini" in t_lower:
        specs["tier"] = "mini"

    # Accessories check
    accessories = ("case", "cover", "screen protector", "tempered glass", "skin", "cable", "adapter", "charger", "pouch", "strap")
    for acc in accessories:
        if re.search(rf"\b{acc}\b", t_lower):
            specs["is_accessory"] = True
            break

    return specs


def _variant_specs_match(incoming: dict, existing: dict) -> bool:
    """Critical specs must be equal; never merge 128GB with 256GB or Pro with non-Pro."""
    # Never match an accessory to a non-accessory
    if incoming.get("is_accessory") and not existing.get("is_accessory"):
        return False
    if existing.get("is_accessory") and not incoming.get("is_accessory"):
        return False

    # Tier (Pro vs Pro Max vs Ultra vs Base)
    if incoming.get("tier") and existing.get("tier"):
        if incoming["tier"] != existing["tier"]:
            return False
    elif (incoming.get("tier") is not None) != (existing.get("tier") is not None):
        return False

    for key in CRITICAL_VARIANT_KEYS:
        inc_val = incoming.get(key)
        ext_val = existing.get(key)
        if inc_val is not None and ext_val is not None:
            if str(inc_val).lower() != str(ext_val).lower():
                return False
    return True


async def match_or_create(
    session: AsyncSession,
    payload: ClientPayload,
    variant: VariantAttributes | None = None,
) -> tuple[CanonicalProduct, RetailerListing, float]:
    """
    Match payload to a canonical product, creating one if needed.
    Returns (product, listing, match_confidence).
    """
    brand = variant.brand if variant else (payload.brand or "")
    model_name = variant.model_name if variant else payload.title
    mpn = None
    if variant:
        mpn = None  # MPN comes from specs
    specs = payload.variant_specs or {}
    if variant:
        specs = {
            "ram_gb": variant.ram_gb,
            "storage_gb": variant.storage_gb,
            "color": variant.color,
            "condition": variant.condition,
        }

    # ── 1. Exact MPN/EAN match ──
    if mpn:
        stmt = select(CanonicalProduct).where(CanonicalProduct.mpn_or_ean == mpn)
        result = await session.execute(stmt)
        product = result.scalar_one_or_none()
        if product:
            listing = await _upsert_listing(session, product, payload, 1.0)
            return product, listing, 1.0

    # ── 2. pg_trgm similarity + variant spec check ──
    if model_name:
        rows = await session.execute(
            text("""
                SELECT id, brand, model_name, mpn_or_ean, variant_specs,
                       similarity(model_name, :q) AS sim
                FROM canonical_products
                WHERE similarity(model_name, :q) > :threshold
                ORDER BY sim DESC
                LIMIT 20
            """),
            {"q": model_name, "threshold": SIMILARITY_THRESHOLD},
        )
        for row in rows.mappings():
            existing_specs = row["variant_specs"] or {}
            if _variant_specs_match(specs, existing_specs):
                stmt = select(CanonicalProduct).where(CanonicalProduct.id == row["id"])
                product = (await session.execute(stmt)).scalar_one()
                confidence = round(float(row["sim"]), 2)
                listing = await _upsert_listing(session, product, payload, confidence)
                return product, listing, confidence

    # ── 3. Create new ──
    product = CanonicalProduct(
        id=uuid.uuid4(),
        brand=brand,
        model_name=model_name,
        mpn_or_ean=mpn,
        category=_guess_category(payload.title),
        variant_specs={k: v for k, v in specs.items() if v is not None},
    )
    session.add(product)
    await session.flush()
    listing = await _upsert_listing(session, product, payload, 1.0)
    return product, listing, 1.0


async def _upsert_listing(
    session: AsyncSession,
    product: CanonicalProduct,
    payload: ClientPayload,
    confidence: float,
) -> RetailerListing:
    stmt = select(RetailerListing).where(
        RetailerListing.retailer == payload.retailer,
        RetailerListing.external_sku == payload.sku,
    )
    result = await session.execute(stmt)
    listing = result.scalar_one_or_none()

    if listing:
        listing.current_listed_price = payload.listed_price or listing.current_listed_price
        listing.current_mrp = payload.mrp or listing.current_mrp
        listing.in_stock = payload.in_stock
        listing.seller_name = payload.seller_name or listing.seller_name
        listing.seller_rating = payload.seller_rating
        listing.match_confidence = confidence
        listing.last_checked_at = datetime.now(timezone.utc)
    else:
        listing = RetailerListing(
            id=uuid.uuid4(),
            canonical_product_id=product.id,
            retailer=payload.retailer,
            external_sku=payload.sku,
            listing_url=payload.url,
            current_listed_price=payload.listed_price or 0,
            current_mrp=payload.mrp,
            in_stock=payload.in_stock,
            seller_name=payload.seller_name,
            seller_rating=payload.seller_rating,
            match_confidence=confidence,
        )
        session.add(listing)

    await session.flush()
    return listing


def _guess_category(title: str) -> str:
    t = title.lower()
    if any(w in t for w in ("phone", "iphone", "galaxy", "redmi", "oneplus", "pixel")):
        return "smartphone"
    if any(w in t for w in ("laptop", "macbook", "thinkpad", "ideapad", "vivobook")):
        return "laptop"
    if any(w in t for w in ("headphone", "earbuds", "earphone", "wh-1000", "buds")):
        return "audio"
    if any(w in t for w in (" tv", "oled", "qled", "bravia", "smart tv")):
        return "tv"
    if any(w in t for w in ("tablet", "ipad", "tab s")):
        return "tablet"
    if any(w in t for w in ("watch", "band", "smartwatch")):
        return "wearable"
    return "electronics"
