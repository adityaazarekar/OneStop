"""
Product matcher: links incoming payloads & discovered competitor quotes to canonical_products.

Upgraded with:
- RapidFuzz `token_set_ratio` matching + strict accessory & variant guards
- Category-adaptive matching (electronics vs fashion/beauty/home/appliances)
- Brand extraction bug fix: NEVER returns "Generic" when a real brand exists in title/JSON-LD
- Seller sanitization: rejects UI/nav junk like "Become a Seller"
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from rapidfuzz import fuzz
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_product import CanonicalProduct
from app.models.retailer_listing import RetailerListing
from app.schemas.extraction import ClientPayload, VariantAttributes


CRITICAL_VARIANT_KEYS = ("ram_gb", "storage_gb", "screen_size_inches", "color")
NON_ELECTRONIC_CATEGORIES = {"fashion", "beauty", "home", "groceries"}
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

ACCESSORY_KEYWORDS = (
    "case",
    "cover",
    "screen protector",
    "tempered glass",
    "glass",
    "skin",
    "cable",
    "adapter",
    "charger",
    "pouch",
    "strap",
    "remote",
    "stand",
    "mount",
    "wall mount",
    "sleeve",
    "protector",
    "bumper",
    "stylus",
    "replacement remote",
)

BAD_SELLER_STRINGS = (
    "become a seller",
    "sell on",
    "seller hub",
    "login",
    "sign in",
    "more",
    "cart",
    "my account",
    "help",
    "download app",
    "customer care",
    "track order",
    "wishlist",
    "register",
    "advertise",
    "gift cards",
    "24x7 customer care",
)

# Comprehensive brand registry (exact canonical casing) — checked before any fallback
KNOWN_BRANDS: list[str] = [
    "Apple", "Samsung", "LG", "Sony", "OnePlus", "Xiaomi", "Redmi", "POCO", "Realme",
    "Nothing", "Motorola", "Google", "Vivo", "Oppo", "iQOO", "Honor", "Infinix", "Tecno",
    "HP", "Dell", "Lenovo", "Asus", "Acer", "MSI", "Gigabyte", "Microsoft", "BenQ", "ViewSonic",
    "boAt", "JBL", "Bose", "Sennheiser", "Marshall", "Skullcandy", "Noise", "Fire-Boltt", "Boult",
    "Dyson", "Philips", "Bosch", "Siemens", "Whirlpool", "Voltas", "Godrej", "Haier", "IFB",
    "Daikin", "Blue Star", "Carrier", "Hitachi", "Panasonic", "Toshiba", "Hisense", "TCL", "Vu",
    "Sharp", "Midea", "Lloyd", "O General", "Mitsubishi", "Bajaj", "Havells", "Crompton",
    "Prestige", "Pigeon", "Butterfly", "Atomberg", "Orient", "Usha", "Kent", "Aquaguard", "Eureka Forbes",
    "Canon", "Nikon", "Fujifilm", "GoPro", "DJI", "Insta360", "Garmin", "Fitbit",
    "Logitech", "Razer", "Corsair", "Keychron", "HyperX", "SteelSeries", "SanDisk", "WD", "Seagate",
    "Crucial", "Kingston", "TP-Link", "Netgear", "APC", "Zebronics", "Portronics", "Anker", "Belkin",
    "Nike", "Adidas", "Puma", "Reebok", "New Balance", "ASICS", "Skechers", "Under Armour",
    "Converse", "Vans", "Crocs", "Birkenstock", "Woodland", "Bata", "Campus", "Red Tape",
    "Zara", "H&M", "Uniqlo", "Levi's", "Allen Solly", "Van Heusen", "Peter England", "Louis Philippe",
    "US Polo Assn", "Tommy Hilfiger", "Calvin Klein", "Ralph Lauren", "Lacoste", "Superdry",
    "Fabindia", "Biba", "W", "Aurelia", "Manyavar", "HRX", "Roadster", "Wrogn", "Snitch", "Souled Store",
    "Maybelline", "L'Oreal", "Lakme", "MAC", "Clinique", "Estee Lauder", "CeraVe", "Cetaphil",
    "Minimalist", "The Ordinary", "Dot & Key", "Plum", "Mamaearth", "Derma Co", "Neutrogena",
    "Nivea", "Bioderma", "Forest Essentials", "Kama Ayurveda", "Nykaa", "Sugar", "Kay Beauty",
    "Casio", "Titan", "Fossil", "Seiko", "Tissot", "Fastrack", "Timex", "G-Shock",
    "IKEA", "Wakefit", "Sleepwell", "Pepperfry", "Urban Ladder", "Nilkamal", "Cello", "Milton",
    "Lenskart", "Ray-Ban", "Oakley", "Vincent Chase", "Samsonite", "American Tourister", "Wildcraft",
    "Mokobara", "Safari", "VIP", "Decathlon", "Quechua",
]

_KNOWN_BRAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<!\w){re.escape(b)}(?!\w)", re.I), b)
    for b in sorted(KNOWN_BRANDS, key=len, reverse=True)
]

OFFICIAL_D2C_DOMAINS = (
    "lg.com", "samsung.com", "apple.com", "sony.co.in", "nike.com", "adidas.co.in",
    "puma.com", "boat-lifestyle.com", "oneplus.in", "mi.com", "realme.com",
    "dyson.in", "dell.com", "hp.com", "lenovo.com", "asus.com", "lenskart.com",
    "wakefit.co", "beminimalist.co", "mokobara.com", "gonoise.com", "thesouledstore.com",
)


def extract_clean_brand(
    raw_brand: str | None = None,
    title: str | None = None,
    jsonld_brand: str | None = None,
    domain: str | None = None,
) -> str:
    """
    Resolve the real product brand without ever returning 'Generic' when a genuine
    brand exists in raw_brand, jsonld_brand, or title.
    """
    invalid = {"", "generic", "unknown", "none", "null", "n/a", "unbranded", "other", "brand"}

    # 1. Explicit JSON-LD or payload brand (cleaned)
    for candidate in (jsonld_brand, raw_brand):
        if not candidate:
            continue
        cleaned = re.sub(r"^(?:Visit the |Brand:\s*|by\s+)", "", str(candidate).strip(), flags=re.I)
        cleaned = re.sub(r"\s+Store$", "", cleaned, flags=re.I).strip()
        if cleaned.lower() not in invalid and len(cleaned) <= 50:
            # Canonicalize casing if it's a known brand
            for pat, canonical in _KNOWN_BRAND_PATTERNS:
                if pat.fullmatch(cleaned):
                    return canonical
            return cleaned

    # 2. Match known brands inside title — pick earliest occurrence (longest on tie)
    #    so "Sony WH-1000XM5 Wireless Noise Cancelling" matches "Sony", not "Noise".
    t = (title or "").strip()
    if t:
        best_match: tuple[int, int, str] | None = None
        for pat, canonical in _KNOWN_BRAND_PATTERNS:
            m = pat.search(t)
            if m:
                key = (m.start(), -len(canonical))
                if best_match is None or key < (best_match[0], best_match[1]):
                    best_match = (m.start(), -len(canonical), canonical)
        if best_match is not None:
            return best_match[2]

        # 3. Leading capitalized token(s) in title (e.g. "LG 1.5 Ton 5 Star..." or "Sony Bravia...")
        first_token = re.split(r"[\s\-|/,:(]+", t)[0].strip()
        stop_first = {
            "new", "latest", "best", "buy", "combo", "pack", "set", "men", "women",
            "mens", "womens", "unisex", "kids", "boys", "girls", "mini", "pro",
            "smart", "wireless", "bluetooth", "digital", "automatic", "portable",
            "refurbished", "renewed", "used", "generic", "original", "official",
        }
        if (
            first_token
            and 2 <= len(first_token) <= 24
            and first_token.lower() not in stop_first
            and not first_token.isdigit()
            and any(ch.isalpha() for ch in first_token)
        ):
            return first_token

    # 4. Official D2C domain fallback
    if domain:
        d = domain.lower().replace("www.", "")
        for known_d in OFFICIAL_D2C_DOMAINS:
            if d.endswith(known_d):
                base = known_d.split(".")[0]
                for pat, canonical in _KNOWN_BRAND_PATTERNS:
                    if pat.fullmatch(base):
                        return canonical
                return base.upper() if len(base) <= 3 else base.title()

    return ""


def sanitize_seller_name(raw_seller: str | None, url: str | None = None, retailer: str | None = None) -> str | None:
    """
    Reject UI/navigation strings like 'Become a Seller', 'Login', 'More', 'Cart'.
    Default official D2C brand sites to 'Official Direct Store'.
    """
    domain = ""
    if url:
        m = re.search(r"https?://(?:www\.)?([^/]+)", url.lower())
        if m:
            domain = m.group(1)

    if raw_seller:
        cleaned = re.sub(r"\s+", " ", raw_seller).strip()
        # Strip leading/trailing noise
        cleaned = re.sub(r"^(?:sold\s+by|seller)\s*:?\s*", "", cleaned, flags=re.I).strip()
        cleaned = re.sub(r"\s*\d\.\d\s*★?.*$", "", cleaned).strip()
        low = cleaned.lower()

        is_junk = (
            not cleaned
            or len(cleaned) < 2
            or len(cleaned) > 80
            or any(bad in low for bad in BAD_SELLER_STRINGS)
            or low in {"seller", "store", "home", "menu", "search", "account", "offers"}
        )
        if not is_junk:
            return cleaned

    # Official D2C brand stores default to "Official Direct Store"
    if domain and any(domain.endswith(d) for d in OFFICIAL_D2C_DOMAINS):
        return "Official Direct Store"

    marketplace_keys = {"amazon_in", "flipkart", "myntra", "ajio", "nykaa", "meesho", "snapdeal", "tatacliq", "jiomart"}
    if retailer and retailer not in marketplace_keys and retailer not in {"croma", "reliance_digital", "vijaysales"}:
        return "Official Direct Store"

    return None


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
    if any(p in u for p in ("/cart/", "/smart-wagon", "/checkout", "localhost", "/s?", "/search?", "/search/")):
        return True, "URL is a cart, checkout, or search results page"

    return False, ""


def extract_variant_specs_from_title(title: str) -> dict:
    """Extract storage, RAM, screen size, capacity/volume, tier modifier, and accessory flags."""
    specs: dict = {}
    if not title:
        return specs

    # Storage: 128 GB, 128GB, 1 TB, 1TB (avoid matching RAM like '8GB RAM')
    ram_m = re.search(r"\b(4|6|8|12|16|18|24|32|64)\s*(?:GB|gb)\s*RAM\b", title, re.I)
    if ram_m:
        specs["ram_gb"] = int(ram_m.group(1))

    # Strip RAM portion before finding storage so "8GB RAM 256GB" reads 256
    title_no_ram = re.sub(r"\b\d+\s*GB\s*RAM\b", "", title, flags=re.I)
    sm = re.search(r"\b(32|64|128|256|512)\s*(?:GB|gb)\b", title_no_ram)
    if sm:
        specs["storage_gb"] = int(sm.group(1))
    else:
        tb_m = re.search(r"\b(1|2|4)\s*(?:TB|tb)\b", title_no_ram)
        if tb_m:
            specs["storage_gb"] = int(tb_m.group(1)) * 1024

    # Screen size for TVs / Monitors / Laptops: e.g. 55 inch, 55-inch, 55", 13.3 inch, 139 cm (55 inch)
    scr_m = re.search(r'\b(1[1-8](?:\.\d)?|2[1-9]|3[24]|4[023]|5[05]|65|75|85)\s*(?:"|inch(?:es)?|-inch)\b', title, re.I)
    if scr_m:
        specs["screen_size_inches"] = float(scr_m.group(1))

    # AC tonnage or Refrigerator/Washer capacity
    ton_m = re.search(r"\b(0\.8|1\.0|1\.2|1\.5|1\.8|2\.0|1|2)\s*Ton\b", title, re.I)
    if ton_m:
        specs["tonnage"] = float(ton_m.group(1))
    vol_m = re.search(r"\b(\d+(?:\.\d+)?)\s*(kg|kilograms?|g|gm|gms|grams?|l|ltr|litres?|liters?|ml)\b", title, re.I)
    if vol_m:
        qty = float(vol_m.group(1))
        u = vol_m.group(2).lower()
        if u.startswith("k"):
            specs["volume"] = f"{int(round(qty * 1000))}g"
        elif u.startswith("g"):
            specs["volume"] = f"{int(round(qty))}g"
        elif u.startswith("l"):
            specs["volume"] = f"{int(round(qty * 1000))}ml"
        elif u == "ml":
            specs["volume"] = f"{int(round(qty))}ml"

    # Tier modifiers
    t_lower = title.lower()
    if "pro max" in t_lower or "pro-max" in t_lower:
        specs["tier"] = "pro_max"
    elif re.search(r"\bpro\b", t_lower):
        specs["tier"] = "pro"
    elif re.search(r"\bultra\b", t_lower):
        specs["tier"] = "ultra"
    elif re.search(r"\bplus\b", t_lower):
        specs["tier"] = "plus"
    elif re.search(r"\bmini\b", t_lower):
        specs["tier"] = "mini"

    # Accessories check
    for acc in ACCESSORY_KEYWORDS:
        if re.search(rf"\b{re.escape(acc)}\b", t_lower):
            specs["is_accessory"] = True
            break

    return specs


def _variant_specs_match(incoming: dict, existing: dict, category: str = "electronics") -> bool:
    """Critical specs must match; category-adaptive so fashion/beauty don't require RAM/storage."""
    # Never match an accessory to a non-accessory
    if bool(incoming.get("is_accessory")) != bool(existing.get("is_accessory")):
        return False

    # Tier (Pro vs Pro Max vs Ultra vs Base)
    if incoming.get("tier") and existing.get("tier"):
        if incoming["tier"] != existing["tier"]:
            return False
    elif (incoming.get("tier") is not None) != (existing.get("tier") is not None):
        return False

    if category in NON_ELECTRONIC_CATEGORIES:
        # For fashion / beauty / home, check volume/size/color only when both present
        for key in ("volume", "size", "color"):
            inc_val = incoming.get(key)
            ext_val = existing.get(key)
            if inc_val is not None and ext_val is not None:
                if str(inc_val).lower() != str(ext_val).lower():
                    return False
        return True

    # Electronics & appliances: enforce storage, RAM, screen_size, tonnage, volume, color when both present
    for key in ("ram_gb", "storage_gb", "screen_size_inches", "tonnage", "volume", "color"):
        inc_val = incoming.get(key)
        ext_val = existing.get(key)
        if inc_val is not None and ext_val is not None:
            if str(inc_val).lower() != str(ext_val).lower():
                return False
    return True


def compute_image_phash_from_bytes(image_bytes: bytes) -> str | None:
    """Compute perceptual image hash (`imagehash.phash`) via Pillow; returns hex string or None."""
    if not image_bytes:
        return None
    try:
        import io
        import imagehash
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def compare_image_phashes(phash_a: str | None, phash_b: str | None, max_hamming_distance: int = 14) -> tuple[bool, int | None]:
    """
    Compare two hex perceptual image hashes (`imagehash.phash`).
    Gracefully returns `(True, None)` if either hash is unavailable.
    """
    if not phash_a or not phash_b:
        return True, None
    try:
        import imagehash

        ha = imagehash.hex_to_hash(phash_a)
        hb = imagehash.hex_to_hash(phash_b)
        dist = int(ha - hb)
        return dist <= max_hamming_distance, dist
    except Exception:
        return True, None


def classify_store_candidate(
    target_title: str,
    candidate_title: str,
    *,
    target_brand: str | None = None,
    target_specs: dict | None = None,
    candidate_specs: dict | None = None,
    candidate_price: float | None = None,
    reference_median_price: float | None = None,
    in_stock: bool = True,
    retailer_key: str | None = None,
    target_image_phash: str | None = None,
    candidate_image_phash: str | None = None,
):
    """
    Generative-Lexical Cascade for 3-Tier Dynamic Store Bucketing:
      - Induces open-vocabulary `CanonicalProductIdentity` for target and candidate
      - Rejects different `product_type` (accessories, spare parts, refills) or different `brand`/`core_model`
      - Buckets accepted candidates into `exact_match`, `lesser_known_store`, or `variant_mismatch`
        with auto-generated `variant_diff_badges: list[str]`.
    """
    from app.services.product_identity import (
        StoreBucketDecision,
        compare_product_identities,
        induce_product_identity_sync,
    )

    if not target_title or not candidate_title:
        return StoreBucketDecision(
            accepted=False,
            bucket="rejected",
            match_score=0.0,
            reject_reason="Empty title",
        )

    t_specs_combined = {**extract_variant_specs_from_title(target_title), **(target_specs or {})}
    c_specs_combined = {**extract_variant_specs_from_title(candidate_title), **(candidate_specs or {})}

    # Fast accessory guard from explicit specs or lexical head check
    if bool(c_specs_combined.get("is_accessory")) != bool(t_specs_combined.get("is_accessory")):
        return StoreBucketDecision(
            accepted=False,
            bucket="rejected",
            match_score=0.0,
            reject_reason="Product type mismatch (accessory vs primary item)",
        )

    phash_ok, phash_dist = compare_image_phashes(target_image_phash, candidate_image_phash)
    if not phash_ok:
        return StoreBucketDecision(
            accepted=False,
            bucket="rejected",
            match_score=0.0,
            reject_reason=f"Perceptual image hash mismatch (hamming distance {phash_dist})",
        )

    target_id = induce_product_identity_sync(
        title=target_title,
        brand=target_brand,
        specs=t_specs_combined,
    )
    cand_id = induce_product_identity_sync(
        title=candidate_title,
        brand=None,
        specs=c_specs_combined,
        listed_price=candidate_price,
    )

    decision = compare_product_identities(
        target_id,
        cand_id,
        target_title=target_title,
        candidate_title=candidate_title,
        candidate_price=candidate_price if candidate_price is not None else 1.0,
        reference_median_price=reference_median_price,
        in_stock=in_stock,
        retailer_key=retailer_key,
    )
    if decision.accepted and phash_dist is not None and phash_dist <= 6:
        decision.match_score = min(100.0, round(decision.match_score + 5.0, 1))
    return decision


def score_candidate_match(
    target_title: str,
    candidate_title: str,
    target_brand: str | None = None,
    target_specs: dict | None = None,
    min_ratio: float = 68.0,
    target_image_phash: str | None = None,
    candidate_image_phash: str | None = None,
) -> tuple[bool, float, str]:
    """
    Multimodal Exact SKU & Variant Verification:
      1. Hard Spec & Product-Type Blockers (Accessory guard, Brand, Dynamic Attributes)
      2. Model Number Parity
      3. Perceptual Image Hashing (`imagehash.phash` via Pillow, gracefully skipped when unavailable)
      4. `RapidFuzz` (`token_set_ratio`)
    Returns (is_exact_match, score_0_to_100, reject_reason).
    """
    if not target_title or not candidate_title:
        return False, 0.0, "Empty title"

    category = _guess_category(target_title)
    t_specs = {**extract_variant_specs_from_title(target_title), **(target_specs or {})}
    c_specs = extract_variant_specs_from_title(candidate_title)

    # 1. Accessory & Open-Vocabulary Product-Type rejection
    if c_specs.get("is_accessory") and not t_specs.get("is_accessory"):
        return False, 0.0, "Candidate is an accessory"

    decision = classify_store_candidate(
        target_title,
        candidate_title,
        target_brand=target_brand,
        target_specs=t_specs,
        candidate_specs=c_specs,
        candidate_price=1.0,
        in_stock=True,
        target_image_phash=target_image_phash,
        candidate_image_phash=candidate_image_phash,
    )
    if not decision.accepted:
        return False, 0.0, decision.reject_reason
    if decision.bucket == "variant_mismatch":
        return False, 0.0, f"Variant spec mismatch ({', '.join(decision.variant_diff_badges)})"

    # 2. Brand consistency check when target has a real brand
    clean_target_brand = extract_clean_brand(target_brand, target_title)
    clean_cand_brand = extract_clean_brand(None, candidate_title)
    if clean_target_brand and clean_cand_brand:
        if clean_target_brand.lower() != clean_cand_brand.lower():
            if clean_target_brand.lower() not in candidate_title.lower():
                return False, 0.0, f"Brand mismatch ({clean_target_brand} vs {clean_cand_brand})"

    # 3. Variant spec parity (storage, RAM, screen size, tonnage, pack volume/weight, tier)
    if not _variant_specs_match(c_specs, t_specs, category=category):
        return False, 0.0, "Variant spec mismatch (storage/RAM/screen/volume/tier)"

    # 4. Perceptual Image Hash check (when both phashes are available)
    phash_ok, phash_dist = compare_image_phashes(target_image_phash, candidate_image_phash)
    if not phash_ok:
        return False, 0.0, f"Perceptual image hash mismatch (hamming distance {phash_dist})"

    # 5. RapidFuzz token_set_ratio
    score = float(fuzz.token_set_ratio(target_title.lower(), candidate_title.lower()))
    if phash_dist is not None and phash_dist <= 6:
        score = min(100.0, score + 5.0)
    if score < min_ratio:
        return False, round(score, 1), f"Low RapidFuzz score ({score:.1f} < {min_ratio})"

    return True, round(score, 1), ""


async def match_or_create(
    session: AsyncSession,
    payload: ClientPayload,
    variant: VariantAttributes | None = None,
) -> tuple[CanonicalProduct, RetailerListing, float]:
    """
    Match payload to a canonical product, creating one if needed.
    Returns (product, listing, match_confidence).
    """
    raw_brand = (variant.brand if variant else None) or payload.brand
    brand = (extract_clean_brand(raw_brand, payload.title, domain=payload.url) or "Unknown")[:100]
    model_name = ((variant.model_name if variant and variant.model_name else payload.title) or payload.title).strip()
    # Guard against "Generic LG ..." prefix bug
    if model_name.lower().startswith("generic ") and brand.lower() != "unknown":
        model_name = model_name[8:].strip()
    model_name = model_name[:255]
    mpn = (payload.variant_specs or {}).get("mpn") or (payload.variant_specs or {}).get("ean") or (payload.variant_specs or {}).get("gtin13")
    if mpn:
        mpn = str(mpn).strip()[:100] or None
    category = _guess_category(payload.title)[:50]
    retailer_clean = (payload.retailer or "generic")[:20]
    sku_clean = (payload.sku or "")[:100]

    specs = {**extract_variant_specs_from_title(payload.title), **(payload.variant_specs or {})}
    if variant:
        for k, v in (
            ("ram_gb", variant.ram_gb),
            ("storage_gb", variant.storage_gb),
            ("color", variant.color),
            ("screen_size_inches", variant.screen_size_inches),
            ("condition", variant.condition),
        ):
            if v is not None:
                specs[k] = v

    # Sanitize seller before persisting
    clean_seller = sanitize_seller_name(payload.seller_name, payload.url, payload.retailer)
    payload.seller_name = clean_seller

    # ── 0. Exact existing (retailer, external_sku) listing match — prevents orphan CanonicalProducts ──
    if sku_clean and sku_clean not in ("UNKNOWN", "UNKNOWN_FSN", "SKU-WEB"):
        exist_stmt = select(RetailerListing).where(
            RetailerListing.retailer == retailer_clean,
            RetailerListing.external_sku == sku_clean,
        )
        existing_listing = (await session.execute(exist_stmt)).scalar_one_or_none()
        if existing_listing is not None:
            prod_stmt = select(CanonicalProduct).where(CanonicalProduct.id == existing_listing.canonical_product_id)
            existing_prod = (await session.execute(prod_stmt)).scalar_one_or_none()
            if existing_prod is not None:
                if (existing_prod.brand or "").lower() in {"", "generic", "unknown"} and brand.lower() != "unknown":
                    existing_prod.brand = brand
                if (existing_prod.model_name or "").strip().lower() in {"", "add to your order", "unknown", "generic"} and model_name.lower() not in {"", "add to your order"}:
                    existing_prod.model_name = model_name
                listing = await _upsert_listing(session, existing_prod, payload, 1.0, existing_listing=existing_listing)
                return existing_prod, listing, 1.0

    # ── 1. Exact MPN/EAN match ──
    if mpn:
        stmt = select(CanonicalProduct).where(CanonicalProduct.mpn_or_ean == mpn)
        result = await session.execute(stmt)
        product = result.scalar_one_or_none()
        if product:
            listing = await _upsert_listing(session, product, payload, 1.0)
            return product, listing, 1.0

    # ── 2. pg_trgm GIN-indexed `%` similarity + RapidFuzz + variant spec check ──
    if model_name:
        await session.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {SIMILARITY_THRESHOLD}"))
        rows = await session.execute(
            text("""
                SELECT id, brand, model_name, mpn_or_ean, variant_specs, category,
                       similarity(model_name, :q) AS sim
                FROM canonical_products
                WHERE model_name % :q
                ORDER BY sim DESC
                LIMIT 20
            """),
            {"q": model_name},
        )
        for row in rows.mappings():
            existing_specs = row["variant_specs"] or {}
            row_cat = row.get("category") or category
            if _variant_specs_match(specs, existing_specs, category=row_cat):
                rf_score = fuzz.token_set_ratio(model_name.lower(), (row["model_name"] or "").lower())
                if rf_score >= 65.0 or float(row["sim"]) >= 0.55:
                    stmt = select(CanonicalProduct).where(CanonicalProduct.id == row["id"])
                    product = (await session.execute(stmt)).scalar_one()
                    # Repair legacy "Generic" brand on existing row if we now have a clean brand
                    if (product.brand or "").lower() in {"", "generic", "unknown"} and brand.lower() != "unknown":
                        product.brand = brand
                    confidence = round(max(float(row["sim"]), rf_score / 100.0), 2)
                    listing = await _upsert_listing(session, product, payload, confidence)
                    return product, listing, confidence

    # ── 3. Create new ──
    product = CanonicalProduct(
        id=uuid.uuid4(),
        brand=brand,
        model_name=model_name,
        mpn_or_ean=mpn,
        category=category,
        variant_specs={k: v for k, v in specs.items() if v is not None},
    )
    session.add(product)
    await session.flush()
    listing = await _upsert_listing(session, product, payload, 1.0, skip_lookup=True)
    return product, listing, 1.0


async def _upsert_listing(
    session: AsyncSession,
    product: CanonicalProduct,
    payload: ClientPayload,
    confidence: float,
    existing_listing: RetailerListing | None = None,
    skip_lookup: bool = False,
) -> RetailerListing:
    retailer_clean = (payload.retailer or "generic")[:20]
    sku_clean = (payload.sku or f"SKU-{abs(hash(payload.url or payload.title)) % 10**8}")[:100]
    url_clean = (payload.url or "")[:1000]

    listing = existing_listing
    if listing is None and not skip_lookup:
        stmt = select(RetailerListing).where(
            RetailerListing.retailer == retailer_clean,
            RetailerListing.external_sku == sku_clean,
        )
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()

    clean_seller = sanitize_seller_name(payload.seller_name, payload.url, payload.retailer)
    if clean_seller:
        clean_seller = clean_seller[:255]

    if listing:
        listing.canonical_product_id = product.id
        listing.listing_url = url_clean or listing.listing_url
        listing.current_listed_price = payload.listed_price or listing.current_listed_price
        listing.current_mrp = payload.mrp or listing.current_mrp
        listing.in_stock = payload.in_stock
        if clean_seller:
            listing.seller_name = clean_seller
        listing.seller_rating = payload.seller_rating
        listing.match_confidence = confidence
        listing.last_checked_at = datetime.now(timezone.utc)
    else:
        listing = RetailerListing(
            id=uuid.uuid4(),
            canonical_product_id=product.id,
            retailer=retailer_clean,
            external_sku=sku_clean,
            listing_url=url_clean,
            current_listed_price=payload.listed_price or 0,
            current_mrp=payload.mrp,
            in_stock=payload.in_stock,
            seller_name=clean_seller,
            seller_rating=payload.seller_rating,
            match_confidence=confidence,
        )
        listing._is_new = True
        session.add(listing)

    await session.flush()
    return listing


def _guess_category(title: str) -> str:
    t = (title or "").lower()
    if any(w in t for w in ("phone", "iphone", "galaxy s", "galaxy a", "galaxy m", "redmi", "oneplus", "pixel", "smartphone")):
        return "smartphone"
    if any(w in t for w in ("laptop", "macbook", "thinkpad", "ideapad", "vivobook", "zenbook", "inspiron", "pavilion", "victus")):
        return "laptop"
    if any(w in t for w in ("headphone", "earbuds", "earphone", "airpods", "wh-1000", "buds", "speaker", "soundbar")):
        return "audio"
    if any(w in t for w in (" tv", "oled", "qled", "bravia", "smart tv", "uhd tv", "led tv", "monitor")):
        return "tv"
    if any(w in t for w in ("tablet", "ipad", "tab s")):
        return "tablet"
    if any(w in t for w in ("watch", "band", "smartwatch")):
        return "wearable"
    if any(w in t for w in ("refrigerator", "fridge", "washing machine", "air conditioner", "split ac", "window ac", "microwave", "dishwasher", "geyser", "purifier", "vacuum")):
        return "appliances"
    if any(w in t for w in ("shirt", "t-shirt", "tshirt", "jeans", "trouser", "kurta", "saree", "dress", "jacket", "hoodie", "sneaker", "shoe", "sandal", "loafer", "denim", "jogger")):
        return "fashion"
    if any(w in t for w in ("serum", "sunscreen", "moisturizer", "cleanser", "face wash", "shampoo", "conditioner", "lipstick", "foundation", "perfume", "eau de parfum", "lotion", "cream", "toner")):
        return "beauty"
    if any(w in t for w in ("mattress", "sofa", "chair", "table", "bedsheet", "pillow", "curtain", "cookware", "mixer", "grinder")):
        return "home"
    return "electronics"
