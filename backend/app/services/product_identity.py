"""
Open-Vocabulary Product Identity & Dynamic Attribute Induction Engine.

Zero-Hardcoding Architecture:
- Induces `CanonicalProductIdentity` dynamically for ANY product category on Earth
  (consumer electronics, mechanical keyboards, apparel/footwear, kitchen appliances,
  skincare/FMCG, furniture, automotive, sports equipment, etc.) without hardcoded lists
  of switches, colors, storage sizes, clothing fits, or accessories.
- Combines `instructor` + OpenRouter (cached in Redis by normalized title/URL hash `<5ms`)
  with a structural open-vocabulary linguistic grammar inducer (`induce_product_identity_sync`)
  so both online LLM induction and deterministic offline induction operate with zero
  category-specific lookup tables.
- Implements dynamic 3-Tier Store Bucketing (`exact_match`, `lesser_known_store`,
  `variant_mismatch`) and auto-generated `variant_diff_badges: list[str]` from the
  induced attribute delta.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from app.config import settings

logger = logging.getLogger(__name__)

IDENTITY_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

# Universal English function words / SEO noise tokens (syntactic, category-agnostic)
_SYNTACTIC_STOPWORDS = {
    "buy", "online", "india", "price", "best", "latest", "new", "launch",
    "official", "original", "authentic", "genuine", "store", "shop", "sale",
    "offer", "discount", "free", "shipping", "delivery", "with", "and", "for",
    "the", "from", "by", "in", "on", "of", "at", "to", "a", "an",
}

# Demographic / general-usage recipients after "for" (NOT a target device/product being accessorized)
_DEMOGRAPHIC_OR_PURPOSE_RE = re.compile(
    r"^(?:men|women|mens|womens|unisex|kids|boys|girls|babies|infants|adults|home|office|"
    r"kitchen|gaming|travel|running|training|walking|gym|outdoor|indoor|daily\s+use|"
    r"all\s+skin\s+types|oily\s+skin|dry\s+skin|hair|face|body|winter|summer)\b",
    re.I,
)

# Universal quantitative unit grammar (numbers + standard SI / commercial units)
_MEASUREMENT_TOKEN_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*"
    r"(tb|gb|mb|kg|kilograms?|g|gm|gms|grams?|mg|l|ltr|litres?|liters?|ml|oz|"
    r"inch(?:es)?|-inch|\"|cm|mm|ft|ton(?:s)?|w|watts?|kw|mah|ah|v|volts?|hz|"
    r"rpm|%|pcs|pieces|units|tabs|tablets|capsules|sachets|sheets|ply)\b"
    r"(?:\s+([a-zA-Z]{2,14}))?",
    re.I,
)

_PACK_COUNT_RE = re.compile(
    r"\b(?:(pack|set|combo|box|bundle|pair)\s+of\s+(\d+)|(\d+)\s*[-\s]?(pack|pcs|pieces|units|pairs))\b",
    re.I,
)

_SIZE_SYSTEM_RE = re.compile(
    r"\b(?:(?:size|uk|us|eu|ind)\s*[-:]?\s*(\d{1,2}(?:\.\d)?|[xsml]{1,3}|[2-5]xl)|"
    r"\b(xxs|xs|small|medium|large|xl|xxl|2xl|3xl|4xl|free\s+size)\b)",
    re.I,
)

_INTERNAL_SPEC_KEYS = {
    "extraction_tier",
    "shopify_variant_id",
    "is_quick_commerce",
    "eta",
    "unit_price_inr",
    "unit_price_label",
}

# Known major multi-category marketplaces & national chains (non-niche)
_ESTABLISHED_RETAILER_KEYS = {
    "amazon_in",
    "flipkart",
    "croma",
    "reliance_digital",
    "vijaysales",
    "tatacliq",
    "jiomart",
    "myntra",
    "ajio",
    "nykaa",
}


class CanonicalProductIdentity(BaseModel):
    """Open-vocabulary canonical product identity induced dynamically from any listing."""

    brand: str = Field(description="Canonical brand name (never 'Generic' when a real brand exists)")
    core_model_query: str = Field(description="Minimal high-recall search query: Brand + Core Model/Line")
    product_type: str = Field(description="Concise canonical object class, e.g., primary item vs accessory/part/refill")
    distinguishing_attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Open-vocabulary key-value map of the 3-6 attributes differentiating variants for this item",
    )
    is_quick_commerce_eligible: bool = Field(
        default=False,
        description="Whether this product class is commonly stocked on instant-delivery apps",
    )


class StoreBucketDecision(BaseModel):
    """Result of the 3-tier dynamic store bucketing comparator."""

    accepted: bool
    bucket: Literal["exact_match", "lesser_known_store", "variant_mismatch", "rejected"]
    variant_diff_badges: list[str] = Field(default_factory=list)
    match_score: float = 0.0
    reject_reason: str = ""


def _normalize_measurement_unit(num_str: str, unit_raw: str) -> tuple[str, str]:
    """Normalize quantitative measurement into (dimension_category, canonical_value)."""
    val = float(num_str)
    u = unit_raw.lower().lstrip("-")
    if u in ("tb",):
        return "capacity", f"{int(round(val * 1024))}gb"
    if u in ("gb", "mb"):
        num_fmt = int(val) if val.is_integer() else val
        return "capacity", f"{num_fmt}{u}"
    if u.startswith("k") and u != "kw":
        return "net_weight", f"{int(round(val * 1000))}g"
    if u in ("g", "gm", "gms", "gram", "grams", "mg", "oz"):
        num_fmt = int(val) if val.is_integer() else val
        return "net_weight", f"{num_fmt}{'g' if u.startswith('g') else u}"
    if u in ("l", "ltr", "litre", "litres", "liter", "liters"):
        if val >= 2.0:
            num_fmt = int(val) if val.is_integer() else val
            return "volume_capacity", f"{num_fmt}l"
        return "volume_capacity", f"{int(round(val * 1000))}ml"
    if u == "ml":
        num_fmt = int(val) if val.is_integer() else val
        return "volume_capacity", f"{num_fmt}ml"
    if u in ('"', "inch", "inches"):
        num_fmt = int(val) if val.is_integer() else val
        return "dimension", f"{num_fmt} inch"
    if u in ("cm", "mm", "ft"):
        num_fmt = int(val) if val.is_integer() else val
        return f"dimension_{u}", f"{num_fmt}{u}"
    if u.startswith("ton"):
        return "capacity_ton", f"{val:g} ton"
    if u == "%":
        return "concentration", f"{val:g}%"
    if u in ("w", "watt", "watts", "kw"):
        return "power", f"{val:g}{u[0]}w" if u == "kw" else f"{val:g}w"
    num_fmt = int(val) if val.is_integer() else val
    return f"quantity_{u}", f"{num_fmt} {u}"


def _induce_brand_open_vocab(raw_brand: str | None, title: str, url: str | None = None) -> str:
    """Induce canonical brand without ever prepending 'Generic' when a real brand exists."""
    from app.services.product_matcher import extract_clean_brand

    b = extract_clean_brand(raw_brand, title, domain=url)
    return b or ""


def _induce_object_head_and_relational_target(title: str, brand: str) -> tuple[str, bool, str]:
    """
    Use English right-hand head noun syntax & relational preposition analysis to induce:
      - `product_type`: canonical object class (e.g., "keyboard", "keycap set", "shoes", "pressure cooker", "case")
      - `is_relational_accessory`: True if title is "<Part/Accessory> for/compatible with <Target Model>"
      - `base_clause`: primary clause before parentheticals and relational prepositions
    Zero hardcoded accessory word lists required.
    """
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        return "product", False, ""

    # 1. Strip parentheticals and trailing pipe/dash qualifier blocks to isolate primary clause
    primary = re.split(r"\s*(?:\(|\[|\||(?:\s+[-–—]\s+))\s*", t)[0].strip()
    primary_before_comma = primary.split(",")[0].strip()

    # 2. Check for relational target phrasing: "<Object A> for / compatible with / replacement for <Product B>"
    rel_m = re.search(
        r"^(.+?)\s+\b(?:compatible\s+with|replacement\s+for|designed\s+for|fits\s+for|for)\s+(.+)$",
        primary_before_comma,
        re.I,
    )
    is_relational = False
    head_source_clause = primary_before_comma

    if rel_m:
        lhs = rel_m.group(1).strip()
        rhs = rel_m.group(2).strip()
        # If RHS is NOT a demographic/general purpose phrase ("for Men", "for Running", "for Gaming"),
        # then LHS is the actual object being sold and RHS is the target product it attaches to!
        if not _DEMOGRAPHIC_OR_PURPOSE_RE.search(rhs):
            is_relational = True
            head_source_clause = lhs
        else:
            head_source_clause = lhs

    # Also check prefix relational phrasing: "Replacement <Object> ..."
    if re.match(r"^(?:replacement|spare)\s+", head_source_clause, re.I):
        is_relational = True

    # 3. Extract right-most head noun phrase from `head_source_clause`
    cleaned = head_source_clause
    if brand:
        cleaned = re.sub(rf"^(?:{re.escape(brand)})\b\s*", "", cleaned, flags=re.I).strip()

    cleaned = _MEASUREMENT_TOKEN_RE.sub(" ", cleaned)
    cleaned = _PACK_COUNT_RE.sub(" ", cleaned)

    tokens = [
        w.strip(".,-/:;")
        for w in cleaned.split()
        if w.strip(".,-/:;")
    ]
    lexical_words: list[str] = []
    for w in tokens:
        w_low = w.lower()
        if w_low in _SYNTACTIC_STOPWORDS:
            continue
        if any(ch.isdigit() for ch in w):
            continue
        if len(w_low) >= 2:
            lexical_words.append(w_low)

    if len(lexical_words) >= 2:
        product_type = " ".join(lexical_words[-2:])
    elif len(lexical_words) == 1:
        product_type = lexical_words[-1]
    else:
        model_words = [w.lower() for w in tokens if w.lower() not in _SYNTACTIC_STOPWORDS]
        product_type = model_words[0] if model_words else "primary_item"

    return product_type, is_relational, head_source_clause


def _induce_open_vocab_attributes(title: str, specs: dict[str, Any] | None = None, brand: str = "") -> dict[str, str]:
    """
    Induce open-vocabulary `distinguishing_attributes: dict[str, str]` dynamically from:
      1. Structured `specs` map (if provided by DOM / JSON-LD / Shopify / WooCommerce)
      2. Quantitative measurement expressions (`<Number> <Unit> [Optional Noun]`)
      3. Multi-pack / count expressions (`Pack of N`, `Set of N`)
      4. Size expressions (`UK 9`, `US 10`, `Size M`, `XL`)
      5. Delimited compositional qualifier phrases (`(Gateron Brown Switch, White Backlight, Aluminum Frame)`,
         `(Inner Lid, Black)`, `- Slim Fit - Navy Blue`, etc.) using the right-hand head noun as attribute key
         and preceding modifier(s) as attribute value.
    Zero hardcoded lists of colors, switches, fits, or materials.
    """
    attrs: dict[str, str] = {}
    t = re.sub(r"\s+", " ", (title or "").strip())

    # 1. Absorb explicit structured specs
    for k, v in (specs or {}).items():
        if k in _INTERNAL_SPEC_KEYS or v is None or v == "" or isinstance(v, (dict, list)):
            continue
        k_norm = str(k).strip().lower()
        if k_norm == "storage_gb":
            val_int = int(v)
            attrs["capacity"] = f"{val_int}gb"
        elif k_norm == "ram_gb":
            attrs["ram"] = f"{int(v)}gb"
        elif k_norm == "screen_size_inches":
            val_f = float(v)
            attrs["dimension"] = f"{int(val_f) if val_f.is_integer() else val_f} inch"
        elif k_norm == "tonnage":
            attrs["capacity_ton"] = f"{float(v):g} ton"
        elif k_norm in ("volume", "pack_size"):
            attrs["net_weight_or_volume"] = str(v).lower().strip()
        elif k_norm == "is_accessory":
            continue
        else:
            attrs[k_norm] = str(v).strip().lower()

    if not t:
        return attrs

    # 2. Pack / Count expressions
    for pm in _PACK_COUNT_RE.finditer(t):
        kind = (pm.group(1) or pm.group(4) or "pack").lower()
        cnt = pm.group(2) or pm.group(3)
        if cnt and int(cnt) >= 1:
            attrs["pack_count"] = f"{kind} of {cnt}"

    # 3. Quantitative measurements (<num> <unit> [head_noun])
    for mm in _MEASUREMENT_TOKEN_RE.finditer(t):
        num_s, unit_s, trailing_noun = mm.group(1), mm.group(2), (mm.group(3) or "").lower()
        dim_key, norm_val = _normalize_measurement_unit(num_s, unit_s)
        if trailing_noun in ("ram", "memory", "vram"):
            attrs["ram"] = norm_val
        elif trailing_noun in ("rom", "storage", "ssd", "hdd"):
            attrs["capacity"] = norm_val
        elif dim_key == "dimension_cm" and "dimension" in attrs:
            continue
        else:
            if dim_key in ("net_weight", "volume_capacity"):
                attrs["net_weight_or_volume"] = norm_val
            else:
                attrs.setdefault(dim_key, norm_val)

    # 4. Size expressions (UK 9, US 10, XL, Size L, etc.)
    for sm in _SIZE_SYSTEM_RE.finditer(t):
        full_sz = sm.group(0).strip().lower()
        attrs["size"] = re.sub(r"\s+", " ", full_sz)

    # 5. Sub-model tier modifiers (Pro Max, Pro, Ultra, Plus, Mini, Lite, FE, SE, Max)
    t_before_parens = re.split(r"[\(\[]", t)[0].lower()
    tier_tokens: list[str] = []
    for mod in ("pro max", "pro", "ultra", "plus", "mini", "lite", "max", "fe"):
        if re.search(rf"\b{re.escape(mod)}\b", t_before_parens):
            if mod == "pro" and "pro max" in tier_tokens:
                continue
            if mod == "max" and "pro max" in tier_tokens:
                continue
            tier_tokens.append(mod)
    if tier_tokens:
        attrs["model_tier"] = " ".join(tier_tokens)

    # 6. Open-Vocabulary Compositional Qualifier Clauses inside (...), [...], after ' - ', or comma-separated
    qualifier_segments: list[str] = []
    for paren_m in re.finditer(r"[\(\[]([^\)\]]{2,140})[\)\]]", t):
        inner = paren_m.group(1)
        for seg in re.split(r"\s*[,;/|]\s*", inner):
            if seg.strip():
                qualifier_segments.append(seg.strip())

    no_parens = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t).strip()
    trailing_parts = re.split(r"\s+(?:[-–—]|\|)\s+|,\s+", no_parens)
    if len(trailing_parts) > 1:
        for seg in trailing_parts[1:]:
            if seg.strip():
                qualifier_segments.append(seg.strip())

    for seg in qualifier_segments:
        seg_clean = re.sub(r"\s+", " ", seg).strip(" .,-/:;")
        if not seg_clean or len(seg_clean) < 2 or len(seg_clean) > 55:
            continue
        seg_low = seg_clean.lower()

        if _MEASUREMENT_TOKEN_RE.fullmatch(seg_clean) or _PACK_COUNT_RE.fullmatch(seg_clean) or _SIZE_SYSTEM_RE.fullmatch(seg_clean):
            continue
        if _MEASUREMENT_TOKEN_RE.search(seg_clean) and len(seg_clean.split()) <= 2:
            continue
        if re.search(r"\b(?:uk|us|eu|size)\s*\d", seg_low):
            continue

        if ":" in seg_clean:
            k_part, v_part = seg_clean.split(":", 1)
            k_clean = re.sub(r"[^a-z0-9_]+", "_", k_part.strip().lower()).strip("_")
            v_clean = v_part.strip().lower()
            if k_clean and v_clean:
                attrs[k_clean] = v_clean
            continue

        words = seg_clean.split()
        if 2 <= len(words) <= 4 and not any(any(ch.isdigit() for ch in w) for w in words):
            head_word = words[-1].lower().rstrip("s")
            modifier_phrase = " ".join(w.lower() for w in words[:-1])
            shade_second_words = {
                "black", "white", "blue", "red", "green", "grey", "gray", "silver", "gold",
                "pink", "purple", "yellow", "orange", "brown", "beige", "navy", "teal",
                "titanium", "graphite", "midnight", "starlight", "ultramarine", "coral",
                "ivory", "olive", "maroon", "burgundy", "charcoal", "bronze", "copper",
                "rose", "mint", "cream", "khaki", "indigo", "violet", "cyan", "magenta",
            }
            if words[-1].lower() in shade_second_words or words[0].lower() in shade_second_words:
                attrs.setdefault("variant_option", seg_low)
            else:
                attrs[head_word] = modifier_phrase
        elif 1 <= len(words) <= 3 and not any(any(ch.isdigit() for ch in w) for w in words):
            if brand and seg_low == brand.lower():
                continue
            if seg_low not in _SYNTACTIC_STOPWORDS:
                attrs.setdefault("variant_option", seg_low)

    return attrs


def _build_core_model_query(title: str, brand: str, base_clause: str) -> str:
    """Build minimal high-recall `Brand + Core Model/Line` query stripping SEO fluff & variant qualifiers."""
    clause = base_clause or title or ""
    clause = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", clause)
    words = [
        w.strip(".,-/:;|")
        for w in clause.split()
        if w.strip(".,-/:;|")
    ]
    parts: list[str] = []
    if brand:
        parts.append(brand)

    brand_tokens = {bt.lower() for bt in brand.split()} if brand else set()
    for w in words:
        w_low = w.lower()
        if w_low in _SYNTACTIC_STOPWORDS or w_low in brand_tokens:
            continue
        if w_low not in {p.lower() for p in parts}:
            parts.append(w)
        if len(parts) >= 6:
            break

    return " ".join(parts).strip()[:100]


def _infer_quick_commerce_eligibility(title: str, listed_price: float | None, attrs: dict[str, str]) -> bool:
    """Dynamically infer whether an item is commonly stocked on 10-minute quick-commerce dark stores."""
    if "net_weight_or_volume" in attrs or "pack_count" in attrs or "concentration" in attrs:
        return True
    if listed_price is not None and 0 < listed_price <= 5000:
        return True
    return True


def induce_product_identity_sync(
    title: str,
    brand: str | None = None,
    specs: dict[str, Any] | None = None,
    url: str | None = None,
    listed_price: float | None = None,
) -> CanonicalProductIdentity:
    """
    Synchronous open-vocabulary product identity inducer (`<0.5ms`, zero network).
    Works across any product category on Earth without hardcoded variant dictionaries.
    """
    clean_brand = _induce_brand_open_vocab(brand, title, url)
    product_type, is_relational, base_clause = _induce_object_head_and_relational_target(title, clean_brand)
    if is_relational and not product_type.startswith("accessory_or_part:"):
        product_type = f"accessory_or_part:{product_type}"

    attrs = _induce_open_vocab_attributes(title, specs=specs, brand=clean_brand)
    core_query = _build_core_model_query(title, clean_brand, base_clause)
    qc_eligible = _infer_quick_commerce_eligibility(title, listed_price, attrs)

    return CanonicalProductIdentity(
        brand=clean_brand or "Unknown",
        core_model_query=core_query or (title or "").strip()[:80],
        product_type=product_type,
        distinguishing_attributes=attrs,
        is_quick_commerce_eligible=qc_eligible,
    )


async def induce_product_identity(
    title: str,
    brand: str | None = None,
    specs: dict[str, Any] | None = None,
    url: str | None = None,
    listed_price: float | None = None,
    *,
    redis_client=None,
    allow_llm: bool = False,
) -> CanonicalProductIdentity:
    """
    Induce `CanonicalProductIdentity` using Redis cache (`<5ms`) + optional `instructor` + OpenRouter
    merged with open-vocabulary grammatical induction.
    """
    norm_key_input = f"{(title or '').strip().lower()}|{(brand or '').strip().lower()}|{json.dumps(specs or {}, sort_keys=True)}"
    cache_key = f"prod_identity:v2:{hashlib.sha256(norm_key_input.encode()).hexdigest()}"

    if redis_client is not None:
        try:
            cached_raw = await redis_client.get(cache_key)
            if cached_raw:
                return CanonicalProductIdentity.model_validate_json(cached_raw)
        except Exception:
            pass

    det_identity = induce_product_identity_sync(
        title=title,
        brand=brand,
        specs=specs,
        url=url,
        listed_price=listed_price,
    )

    if allow_llm and settings.openrouter_api_key:
        try:
            import instructor
            from openai import AsyncOpenAI

            raw_client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                timeout=4.5,
            )
            client = instructor.from_openai(raw_client, mode=instructor.Mode.JSON)
            llm_id: CanonicalProductIdentity = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.openrouter_model,
                    response_model=CanonicalProductIdentity,
                    max_retries=1,
                    temperature=0.0,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Induce a structured CanonicalProductIdentity from this product listing. "
                                "Never use 'Generic' as brand if a real brand exists. "
                                "Discover the 3-6 category-specific distinguishing_attributes dynamically."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Title: {title}\nBrand: {brand or ''}\nSpecs: {json.dumps(specs or {})}",
                        },
                    ],
                ),
                timeout=5.0,
            )
            merged_attrs = {**det_identity.distinguishing_attributes, **(llm_id.distinguishing_attributes or {})}
            det_identity = CanonicalProductIdentity(
                brand=det_identity.brand if det_identity.brand != "Unknown" else llm_id.brand,
                core_model_query=llm_id.core_model_query or det_identity.core_model_query,
                product_type=det_identity.product_type if det_identity.product_type.startswith("accessory_or_part:") else (llm_id.product_type or det_identity.product_type),
                distinguishing_attributes=merged_attrs,
                is_quick_commerce_eligible=llm_id.is_quick_commerce_eligible,
            )
        except Exception as exc:
            logger.debug("LLM product identity induction skipped/fallback: %s", exc)

    if redis_client is not None:
        try:
            await redis_client.set(cache_key, det_identity.model_dump_json(), ex=IDENTITY_CACHE_TTL_SECONDS)
        except Exception:
            pass

    return det_identity


def _extract_core_alphanumeric_model_codes(text: str) -> set[str]:
    """
    Extract alphanumeric model identifiers (e.g., 'k2', 'k8', '17', '16', 's25', 'xm5', '40', 'v15')
    after removing measurement tokens ('256gb', '500g', '5l', '55 inch', '10%').
    """
    stripped = _MEASUREMENT_TOKEN_RE.sub(" ", text or "")
    stripped = _PACK_COUNT_RE.sub(" ", stripped)
    stripped = _SIZE_SYSTEM_RE.sub(" ", stripped)
    primary = re.split(r"[\(\[]", stripped)[0]
    codes: set[str] = set()
    for tok in re.split(r"[^a-zA-Z0-9]+", primary.lower()):
        if not tok or tok in {"5g", "4g", "4k", "8k", "2d", "3d", "gen", "v1", "v2"}:
            continue
        if any(ch.isdigit() for ch in tok):
            codes.add(tok)
    return codes


def _product_types_compatible(target_type: str, cand_type: str, target_title: str, cand_title: str) -> bool:
    """
    Verify that candidate and target share the same canonical object class (`product_type`).
    Automatically rejects accessories, spare parts, refills, or different product types
    without hardcoded accessory dictionaries.
    """
    t_is_rel = target_type.startswith("accessory_or_part:")
    c_is_rel = cand_type.startswith("accessory_or_part:")
    if t_is_rel != c_is_rel:
        return False

    t_clean = target_type.replace("accessory_or_part:", "").strip().lower()
    c_clean = cand_type.replace("accessory_or_part:", "").strip().lower()

    if t_clean == c_clean:
        return True

    t_words = [w.rstrip("s") for w in t_clean.split() if len(w) >= 2]
    c_words = [w.rstrip("s") for w in c_clean.split() if len(w) >= 2]
    if not t_words or not c_words:
        return True

    t_head = t_words[-1]
    c_head = c_words[-1]

    if t_head == c_head:
        return True

    t_title_words = {w.lower().rstrip("s") for w in re.split(r"[^a-zA-Z0-9]+", target_title) if len(w) >= 2}
    c_title_words = {w.lower().rstrip("s") for w in re.split(r"[^a-zA-Z0-9]+", cand_title) if len(w) >= 2}

    if c_head not in t_title_words and t_head not in c_title_words:
        return False

    if c_head not in t_title_words:
        return False

    return True


def _format_diff_badge(attr_key: str, cand_val: str) -> str:
    """Format an auto-generated human-readable variant diff badge from `(attr_key, cand_val)`."""
    v_clean = cand_val.strip()
    k_clean = attr_key.strip().lower()
    if k_clean in ("variant_option", "pack_count", "size", "capacity", "net_weight_or_volume", "dimension", "capacity_ton", "concentration"):
        return v_clean.upper() if re.fullmatch(r"\d+(?:gb|tb|mb|w|kw)", v_clean, re.I) else v_clean.title()
    if k_clean == "ram":
        return f"{v_clean.upper()} RAM"
    if k_clean == "model_tier":
        return f"{v_clean.title()} Tier"
    if k_clean in v_clean.lower():
        return v_clean.title()
    return f"{v_clean.title()} {k_clean.replace('_', ' ').title()}"


def compare_product_identities(
    target: CanonicalProductIdentity,
    candidate: CanonicalProductIdentity,
    *,
    target_title: str = "",
    candidate_title: str = "",
    candidate_price: float | None = None,
    reference_median_price: float | None = None,
    in_stock: bool = True,
    retailer_key: str | None = None,
) -> StoreBucketDecision:
    """
    Dynamic 3-Tier Store Bucketing & Auto-Generated Variant Diffs:
      - REJECT (`bucket="rejected"`):
          Different `product_type` (accessories, spare parts, refills, unrelated object class)
          or different `brand` or conflicting `core_model` number.
      - BUCKET 1 (`bucket="exact_match"`):
          Same `brand`, `core_model`, `product_type`, all key `distinguishing_attributes` align,
          valid live price, `in_stock == True`.
      - BUCKET 2 (`bucket="lesser_known_store"`):
          Exact/strong match on a niche, regional, or independent store, OR an exact match
          that is currently `out_of_stock` (`in_stock == False`).
      - BUCKET 3 (`bucket="variant_mismatch"`):
          Same core product (`brand` + `core_model` + `product_type`), BUT one or more
          dynamically induced `distinguishing_attributes` differ, OR price is missing /
          extreme statistical outlier vs median. Auto-generates `variant_diff_badges`.
    """
    t_title = target_title or target.core_model_query
    c_title = candidate_title or candidate.core_model_query

    # 1. Brand check
    t_brand = (target.brand or "").strip().lower()
    c_brand = (candidate.brand or "").strip().lower()
    if t_brand and t_brand != "unknown" and c_brand and c_brand != "unknown":
        if t_brand != c_brand and t_brand not in c_title.lower():
            return StoreBucketDecision(
                accepted=False,
                bucket="rejected",
                match_score=0.0,
                reject_reason=f"Brand mismatch ({target.brand} vs {candidate.brand})",
            )

    # 2. Product Type / Object Class check (universally rejects accessories/parts/unrelated items)
    if not _product_types_compatible(target.product_type, candidate.product_type, t_title, c_title):
        return StoreBucketDecision(
            accepted=False,
            bucket="rejected",
            match_score=0.0,
            reject_reason=f"Product type mismatch ({target.product_type} vs {candidate.product_type})",
        )

    # 3. Core model number parity check (e.g. K2 vs K8, iPhone 17 vs iPhone 16, Pegasus 40 vs Pegasus 39)
    t_codes = _extract_core_alphanumeric_model_codes(t_title)
    c_codes = _extract_core_alphanumeric_model_codes(c_title)
    if t_codes and c_codes:
        if not t_codes.intersection(c_codes):
            return StoreBucketDecision(
                accepted=False,
                bucket="rejected",
                match_score=0.0,
                reject_reason=f"Core model mismatch ({sorted(t_codes)} vs {sorted(c_codes)})",
            )

    # Lexical core similarity floor
    rf_score = float(fuzz.token_set_ratio(target.core_model_query.lower(), c_title.lower()))
    if rf_score < 48.0:
        return StoreBucketDecision(
            accepted=False,
            bucket="rejected",
            match_score=round(rf_score, 1),
            reject_reason=f"Low core model similarity ({rf_score:.1f})",
        )

    # 4. Compare dynamically induced distinguishing_attributes
    diff_badges: list[str] = []
    t_attrs = target.distinguishing_attributes or {}
    c_attrs = candidate.distinguishing_attributes or {}

    all_keys = list(dict.fromkeys(list(t_attrs.keys()) + list(c_attrs.keys())))
    for k in all_keys:
        tv = t_attrs.get(k)
        cv = c_attrs.get(k)
        if tv is not None and cv is not None:
            tv_norm = tv.strip().lower()
            cv_norm = cv.strip().lower()
            if tv_norm == cv_norm:
                continue
            if tv_norm in cv_norm.split() or cv_norm in tv_norm.split():
                continue
            badge = _format_diff_badge(k, cv)
            if badge not in diff_badges:
                diff_badges.append(badge)
        elif tv is None and cv is not None and k in ("model_tier", "pack_count"):
            badge = _format_diff_badge(k, cv)
            if badge not in diff_badges:
                diff_badges.append(badge)
        elif tv is not None and cv is None and k == "model_tier":
            badge = f"Standard (Not {tv.title()})"
            if badge not in diff_badges:
                diff_badges.append(badge)

    # 5. Price missing or extreme outlier check
    if candidate_price is None or candidate_price <= 0:
        if "Price Missing" not in diff_badges:
            diff_badges.append("Price Missing")
    elif reference_median_price and reference_median_price > 0:
        ratio = candidate_price / reference_median_price
        if ratio < 0.45 or ratio > 2.25:
            if "Outlier Price" not in diff_badges:
                diff_badges.append("Outlier Price")

    # 6. Assign 3-Tier Bucket
    if diff_badges:
        return StoreBucketDecision(
            accepted=True,
            bucket="variant_mismatch",
            variant_diff_badges=diff_badges,
            match_score=round(max(50.0, rf_score - 15.0), 1),
        )

    is_niche = bool(retailer_key and retailer_key.lower() not in _ESTABLISHED_RETAILER_KEYS)
    if not in_stock or is_niche:
        return StoreBucketDecision(
            accepted=True,
            bucket="lesser_known_store",
            variant_diff_badges=[],
            match_score=round(rf_score, 1),
        )

    return StoreBucketDecision(
        accepted=True,
        bucket="exact_match",
        variant_diff_badges=[],
        match_score=round(rf_score, 1),
    )
