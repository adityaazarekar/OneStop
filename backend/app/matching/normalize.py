"""
Pure, deterministic normalizers for product variant attributes.

Covers:
- Quantity & unit normalization via `pint` (`storage`, `ram`, `volume`, `weight`, `screen`)
- Storage dual-base equivalence (`1TB == 1000GB == 1024GB`)
- Composite RAM + storage splitting (`"8GB+256GB" == "8 GB / 256 GB"`)
- Multi-pack & combo expressions (`"Pack of N"`, `"N x 500 ml"`, `"Set of N"`, `"Combo of N"`)
- Color normalization via `webcolors` -> sRGB -> CIELAB (D65) + human-reviewed `attribute_aliases`
- Size normalization with explicit sizing system (`UK`, `US`, `EU`, `CM`, `ALPHA`)
- Condition & enum normalization (`new`, `refurbished`, `open_box`, `used`)
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import pint
import webcolors

from app.matching.contracts import Attr, AttrKind, Evidence, Source

_UREG = pint.UnitRegistry()

# ── Regexes for deterministic structural parsing ──

_RAM_STORAGE_COMBO_RE = re.compile(
    r"(?:^|\b)(\d{1,3})\s*(?:GB|gb)?\s*(?:RAM\s*)?(?:\+|/|\||,|\s+and\s+|\s+with\s+)\s*(\d{2,4})\s*(GB|TB|gb|tb)(?:\s*(?:ROM|Storage|SSD))?\b",
    re.I,
)

_EXPLICIT_RAM_RE = re.compile(r"\b(\d{1,3})\s*(GB|MB)\s*(?:RAM|Memory|Unified\s+Memory)\b", re.I)
_EXPLICIT_STORAGE_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(TB|GB|MB)\s*(?:ROM|Storage|SSD|NVMe|HDD|Internal)?\b",
    re.I,
)

_MULTIPACK_QTY_RE = re.compile(
    r"\b(\d+)\s*(?:x|×|\*)\s*(\d+(?:\.\d+)?)\s*(ml|l|ltr|litre|litres|liter|liters|g|gm|gms|gram|grams|kg|kilogram|kilograms|mg|oz)\b",
    re.I,
)

_PACK_OF_RE = re.compile(
    r"\b(?:(?:pack|set|combo|box|bundle|pair|case)\s+of\s+(\d+)|(\d+)\s*[-\s]?(?:pack|pcs|pieces|units|pairs|tablets|capsules|sachets|sheets|pods))\b",
    re.I,
)

_QUANTITY_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(tb|gb|mb|kg|kilograms?|g|gm|gms|grams?|mg|l|ltr|litres?|liters?|ml|oz|inch(?:es)?|-inch|\"|cm|mm)\s*$",
    re.I,
)

_SIZE_SYSTEM_PREFIX_RE = re.compile(
    r"^\s*(uk|us|eu|euro|cm|in|ind|india)\s*[-:/]?\s*(\d{1,2}(?:\.\d)?)\s*$",
    re.I,
)

_SIZE_SYSTEM_SUFFIX_RE = re.compile(
    r"^\s*(\d{1,2}(?:\.\d)?)\s*(uk|us|eu|cm)\s*$",
    re.I,
)

_BARE_NUMERIC_SIZE_RE = re.compile(r"^\s*(?:size\s*[-:]?\s*)?(\d{1,2}(?:\.\d)?)\s*$", re.I)

_ALPHA_SIZE_MAP: dict[str, str] = {
    "xxs": "XXS",
    "xx-small": "XXS",
    "extra extra small": "XXS",
    "xs": "XS",
    "x-small": "XS",
    "extra small": "XS",
    "s": "S",
    "small": "S",
    "m": "M",
    "med": "M",
    "medium": "M",
    "l": "L",
    "lrg": "L",
    "large": "L",
    "xl": "XL",
    "x-large": "XL",
    "extra large": "XL",
    "xxl": "XXL",
    "2xl": "XXL",
    "xx-large": "XXL",
    "double xl": "XXL",
    "3xl": "3XL",
    "xxxl": "3XL",
    "4xl": "4XL",
    "xxxxl": "4XL",
    "5xl": "5XL",
    "free size": "FREE_SIZE",
    "one size": "FREE_SIZE",
    "onesize": "FREE_SIZE",
}

_CONDITION_MAP: dict[str, str] = {
    "new": "new",
    "brand new": "new",
    "factory sealed": "new",
    "refurbished": "refurbished",
    "renewed": "refurbished",
    "certified refurbished": "refurbished",
    "reconditioned": "refurbished",
    "open box": "open_box",
    "open-box": "open_box",
    "unboxed": "open_box",
    "used": "used",
    "pre-owned": "used",
    "preowned": "used",
    "second hand": "used",
}

_UNIT_SYNONYMS: dict[str, str] = {
    "tb": "terabyte",
    "gb": "gigabyte",
    "mb": "megabyte",
    "kg": "kilogram",
    "kilogram": "kilogram",
    "kilograms": "kilogram",
    "g": "gram",
    "gm": "gram",
    "gms": "gram",
    "gram": "gram",
    "grams": "gram",
    "mg": "milligram",
    "oz": "ounce",
    "l": "liter",
    "ltr": "liter",
    "litre": "liter",
    "litres": "liter",
    "liter": "liter",
    "liters": "liter",
    "ml": "milliliter",
    "inch": "inch",
    "inches": "inch",
    "-inch": "inch",
    '"': "inch",
    "cm": "centimeter",
    "mm": "millimeter",
}

_BASE_UNIT_TARGET: dict[str, tuple[str, str]] = {
    "terabyte": ("gigabyte", "GB"),
    "gigabyte": ("gigabyte", "GB"),
    "megabyte": ("gigabyte", "GB"),
    "kilogram": ("gram", "g"),
    "gram": ("gram", "g"),
    "milligram": ("gram", "g"),
    "ounce": ("gram", "g"),
    "liter": ("milliliter", "ml"),
    "milliliter": ("milliliter", "ml"),
    "inch": ("mm", "mm"),
    "centimeter": ("mm", "mm"),
    "millimeter": ("mm", "mm"),
}


def _clean_decimal(val: float | Decimal | str) -> Decimal:
    d = Decimal(str(val)).quantize(Decimal("0.0001"))
    if d == 0:
        return Decimal("0")
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return Decimal(s)


def normalize_quantity(raw: str, attr_name: str = "") -> tuple[Decimal | None, str | None, str | None]:
    """
    Parse and convert a quantity expression to its canonical base unit using `pint`:
    - Mass -> `g`
    - Volume -> `ml`
    - Length/Screen -> `mm`
    - Digital storage/RAM -> `GB` (stored in decimal GB by default; comparator checks both 1000 and 1024 for TB)
    Returns `(num, base_unit_symbol, norm_string)`.
    """
    if not raw or not str(raw).strip():
        return None, None, None

    s = " ".join(str(raw).strip().split())
    # Strip trailing qualifiers like "ROM", "RAM", "SSD", "Storage"
    s_cleaned = re.sub(r"\s+(?:ROM|RAM|SSD|HDD|Storage|Internal|Memory)$", "", s, flags=re.I).strip()

    m = _QUANTITY_RE.match(s_cleaned)
    if not m:
        return None, None, None

    num_raw = Decimal(m.group(1))
    unit_key = m.group(2).lower()
    pint_unit = _UNIT_SYNONYMS.get(unit_key)
    if not pint_unit:
        return None, None, None

    target_pint, symbol = _BASE_UNIT_TARGET[pint_unit]

    # Digital storage: keep exact Decimal representation for 1000 vs 1024 checks
    if pint_unit == "terabyte":
        # Store canonical num in GB (1000-based decimal GB) and keep unit="GB"
        num_gb = _clean_decimal(num_raw * Decimal("1000"))
        return num_gb, "GB", f"{num_gb} GB"
    if pint_unit == "gigabyte":
        num_gb = _clean_decimal(num_raw)
        return num_gb, "GB", f"{num_gb} GB"
    if pint_unit == "megabyte":
        num_gb = _clean_decimal(num_raw / Decimal("1000"))
        return num_gb, "GB", f"{num_gb} GB"

    qty = _UREG.Quantity(float(num_raw), pint_unit).to(target_pint)
    base_num = _clean_decimal(round(float(qty.magnitude), 4))
    return base_num, symbol, f"{base_num} {symbol}"


def storage_values_match(attr_a: Attr, attr_b: Attr) -> bool:
    """
    Storage matches if equal under EITHER 1000-based or 1024-based TB->GB interpretation.
    Example: "1TB", "1000GB", and "1024GB" all mutually match.
    """
    def _candidate_gb_values(a: Attr) -> set[Decimal]:
        vals: set[Decimal] = set()
        if a.num is not None and (a.unit or "").upper() == "GB":
            vals.add(_clean_decimal(a.num))
        raw_s = (a.raw or "").strip()
        m = re.search(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb)\b", raw_s, re.I)
        if m:
            n = Decimal(m.group(1))
            u = m.group(2).lower()
            if u == "tb":
                vals.add(_clean_decimal(n * Decimal("1000")))
                vals.add(_clean_decimal(n * Decimal("1024")))
            elif u == "gb":
                vals.add(_clean_decimal(n))
                # If raw is 1024GB or 2048GB, also include 1000-based equivalent (1000GB / 2000GB)
                if n >= Decimal("1024") and (n % Decimal("1024") == 0):
                    tb_count = n / Decimal("1024")
                    vals.add(_clean_decimal(tb_count * Decimal("1000")))
            elif u == "mb":
                vals.add(_clean_decimal(n / Decimal("1000")))
                vals.add(_clean_decimal(n / Decimal("1024")))
        return vals

    set_a = _candidate_gb_values(attr_a)
    set_b = _candidate_gb_values(attr_b)
    if not set_a or not set_b:
        return False
    return bool(set_a.intersection(set_b))


def parse_ram_and_storage(
    raw: str,
    evidence: Evidence | None = None,
) -> dict[str, Attr]:
    """
    Deterministically normalize combined RAM+Storage strings such as:
    - "8GB+256GB"
    - "8 GB / 256 GB"
    - "8GB RAM, 256GB Storage"
    into canonical `{"ram": Attr(...), "storage": Attr(...)}`.
    """
    ev = evidence or Evidence(source=Source.TITLE_PARSE, span=raw, confidence=0.95)
    out: dict[str, Attr] = {}
    if not raw:
        return out

    combo_m = _RAM_STORAGE_COMBO_RE.search(raw)
    if combo_m:
        ram_val = Decimal(combo_m.group(1))
        stor_num = Decimal(combo_m.group(2))
        stor_unit = combo_m.group(3).upper()
        stor_raw = f"{stor_num}{stor_unit}"
        s_num, s_unit, s_norm = normalize_quantity(stor_raw, "storage")
        out["ram"] = Attr(
            name="ram",
            kind=AttrKind.QUANTITY,
            raw=f"{ram_val}GB",
            norm=f"{_clean_decimal(ram_val)} GB",
            num=_clean_decimal(ram_val),
            unit="GB",
            evidence=ev,
        )
        out["storage"] = Attr(
            name="storage",
            kind=AttrKind.QUANTITY,
            raw=stor_raw,
            norm=s_norm,
            num=s_num,
            unit=s_unit,
            evidence=ev,
        )
        return out

    ram_m = _EXPLICIT_RAM_RE.search(raw)
    if ram_m:
        r_raw = f"{ram_m.group(1)}{ram_m.group(2).upper()}"
        r_num, r_unit, r_norm = normalize_quantity(r_raw, "ram")
        out["ram"] = Attr(
            name="ram",
            kind=AttrKind.QUANTITY,
            raw=r_raw,
            norm=r_norm,
            num=r_num,
            unit=r_unit,
            evidence=ev,
        )

    cleaned_for_storage = _EXPLICIT_RAM_RE.sub(" ", raw)
    stor_m = _EXPLICIT_STORAGE_RE.search(cleaned_for_storage)
    if stor_m:
        s_raw = f"{stor_m.group(1)}{stor_m.group(2).upper()}"
        s_num, s_unit, s_norm = normalize_quantity(s_raw, "storage")
        out["storage"] = Attr(
            name="storage",
            kind=AttrKind.QUANTITY,
            raw=s_raw,
            norm=s_norm,
            num=s_num,
            unit=s_unit,
            evidence=ev,
        )

    return out


def parse_pack_and_quantity(
    raw: str,
    evidence: Evidence | None = None,
) -> dict[str, Attr]:
    """
    Parse multipack and unit-quantity strings into `pack_qty` (COUNT) and `unit_size` (QUANTITY):
    - "2 x 500 ml" -> pack_qty=2, unit_size=500 ml
    - "1 L" -> pack_qty=1, unit_size=1000 ml
    - "Pack of 3" -> pack_qty=3
    """
    ev = evidence or Evidence(source=Source.TITLE_PARSE, span=raw, confidence=0.95)
    out: dict[str, Attr] = {}
    if not raw:
        return out

    mp = _MULTIPACK_QTY_RE.search(raw)
    if mp:
        cnt = Decimal(mp.group(1))
        qty_raw = f"{mp.group(2)} {mp.group(3)}"
        q_num, q_unit, q_norm = normalize_quantity(qty_raw, "unit_size")
        out["pack_qty"] = Attr(
            name="pack_qty",
            kind=AttrKind.COUNT,
            raw=mp.group(0),
            norm=str(int(cnt)),
            num=cnt,
            unit="count",
            evidence=ev,
        )
        if q_num is not None:
            out["unit_size"] = Attr(
                name="unit_size",
                kind=AttrKind.QUANTITY,
                raw=qty_raw,
                norm=q_norm,
                num=q_num,
                unit=q_unit,
                evidence=ev,
            )
        return out

    po = _PACK_OF_RE.search(raw)
    if po:
        cnt_s = po.group(1) or po.group(2)
        cnt = Decimal(cnt_s)
        out["pack_qty"] = Attr(
            name="pack_qty",
            kind=AttrKind.COUNT,
            raw=po.group(0),
            norm=str(int(cnt)),
            num=cnt,
            unit="count",
            evidence=ev,
        )

    # Also check for standalone quantity (e.g. "1 L" or "500 ml" or "500ml (Pack of 2)")
    stripped = _PACK_OF_RE.sub(" ", raw).strip(" ()-,")
    q_num, q_unit, q_norm = normalize_quantity(stripped, "unit_size")
    if q_num is not None:
        out["unit_size"] = Attr(
            name="unit_size",
            kind=AttrKind.QUANTITY,
            raw=stripped,
            norm=q_norm,
            num=q_num,
            unit=q_unit,
            evidence=ev,
        )
        if "pack_qty" not in out:
            out["pack_qty"] = Attr(
                name="pack_qty",
                kind=AttrKind.COUNT,
                raw="1",
                norm="1",
                num=Decimal("1"),
                unit="count",
                evidence=ev,
            )

    return out


# ── Color Normalization (webcolors + sRGB->CIELAB + human attribute_aliases) ──

def _srgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """Convert 8-bit sRGB (0..255) to CIE L*a*b* (D65 illuminant)."""
    def _pivot_rgb(c: float) -> float:
        c = c / 255.0
        return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92

    r, g, b = _pivot_rgb(rgb[0]), _pivot_rgb(rgb[1]), _pivot_rgb(rgb[2])
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750) / 1.00000
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883

    def _pivot_xyz(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t) + (16.0 / 116.0)

    fx, fy, fz = _pivot_xyz(x), _pivot_xyz(y), _pivot_xyz(z)
    l_val = (116.0 * fy) - 16.0
    a_val = 500.0 * (fx - fy)
    b_val = 200.0 * (fy - fz)
    return (l_val, a_val, b_val)


def delta_e_cie76(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """Compute CIE76 Delta-E distance in CIELAB space."""
    return math.sqrt(
        (lab1[0] - lab2[0]) ** 2
        + (lab1[1] - lab2[1]) ** 2
        + (lab1[2] - lab2[2]) ** 2
    )


def normalize_color(
    raw: str,
    aliases: Mapping[str, str] | None = None,
) -> tuple[str | None, tuple[float, float, float] | None]:
    """
    Resolve a color string via human-reviewed `aliases` + `webcolors` CSS3 color table + CIELAB.
    Returns `(canonical_color_name_or_None, lab_tuple_or_None)`.
    Unresolved marketing names (e.g. 'Midnight', 'Starlight' when not in `aliases`) return `(None, None)`.
    """
    if not raw or not str(raw).strip():
        return None, None

    cleaned = " ".join(str(raw).strip().lower().split())
    if aliases:
        alias_map = {str(k).strip().lower(): str(v).strip().lower() for k, v in aliases.items()}
        if cleaned in alias_map:
            cleaned = alias_map[cleaned]

    css_candidate = cleaned.replace(" ", "").replace("-", "")
    try:
        if cleaned.startswith("#"):
            rgb_obj = webcolors.hex_to_rgb(cleaned)
        else:
            rgb_obj = webcolors.name_to_rgb(css_candidate)
        rgb = (int(rgb_obj.red), int(rgb_obj.green), int(rgb_obj.blue))
        lab = _srgb_to_lab(rgb)
        # Canonicalize grey/gray spelling
        canon = css_candidate.replace("grey", "gray")
        return canon, lab
    except ValueError:
        return None, None


# ── Size Normalization ──

def normalize_size(raw: str) -> tuple[str | None, Decimal | None, str | None]:
    """
    Parse size into `(system, numeric_value_or_None, canonical_norm_or_None)`.
    - "UK 9" -> ("UK", Decimal("9"), "UK:9")
    - "US 9" -> ("US", Decimal("9"), "US:9")
    - "M" / "Medium" -> ("ALPHA", None, "ALPHA:M")
    - "9" (no system) -> (None, Decimal("9"), None) -> UNKNOWN system
    """
    if not raw or not str(raw).strip():
        return None, None, None

    s = " ".join(str(raw).strip().split())
    s_low = re.sub(r"^size\s*[-:]?\s*", "", s.lower()).strip()

    if s_low in _ALPHA_SIZE_MAP:
        code = _ALPHA_SIZE_MAP[s_low]
        return "ALPHA", None, f"ALPHA:{code}"

    m_pre = _SIZE_SYSTEM_PREFIX_RE.match(s_low)
    if m_pre:
        sys_raw = m_pre.group(1).upper()
        sys_code = "EU" if sys_raw == "EURO" else ("IND" if sys_raw == "INDIA" else sys_raw)
        num_val = _clean_decimal(m_pre.group(2))
        return sys_code, num_val, f"{sys_code}:{num_val}"

    m_suf = _SIZE_SYSTEM_SUFFIX_RE.match(s_low)
    if m_suf:
        sys_code = m_suf.group(2).upper()
        num_val = _clean_decimal(m_suf.group(1))
        return sys_code, num_val, f"{sys_code}:{num_val}"

    m_bare = _BARE_NUMERIC_SIZE_RE.match(s_low)
    if m_bare:
        try:
            return None, _clean_decimal(m_bare.group(1)), None
        except InvalidOperation:
            return None, None, None

    return None, None, None


# ── Condition & Core Model Normalization ──

def normalize_condition(raw: str) -> str | None:
    """Normalize product condition string to canonical enum (`new`, `refurbished`, `open_box`, `used`)."""
    if not raw or not str(raw).strip():
        return None
    s = " ".join(str(raw).strip().lower().replace("_", " ").split())
    if s in _CONDITION_MAP:
        return _CONDITION_MAP[s]
    for k, v in _CONDITION_MAP.items():
        if re.search(rf"\b{re.escape(k)}\b", s):
            return v
    return s


def normalize_core_model(raw: str) -> tuple[str, tuple[str, ...]]:
    """
    Normalize `core_model` (casefold, strip punctuation/hyphens, token-order-insensitive).
    Returns `(canonical_sorted_string, sorted_tokens_tuple)`.
    """
    if not raw:
        return "", ()
    s = raw.casefold()
    tokens = [tok for tok in re.split(r"[^a-z0-9]+", s) if tok]
    sorted_tokens = tuple(sorted(tokens))
    return " ".join(sorted_tokens), sorted_tokens
