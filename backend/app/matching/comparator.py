"""
Deterministic Exact-Variant Comparator (`decide(src, cand) -> MatchDecision`).

Pure functions, zero I/O, zero network, asymmetric trust:
- Identity check: GTIN, Brand, Core Model (casefolded, token-order-insensitive, tier/number strictness)
- Per-attribute verification across union of attribute names by `AttrKind`:
  QUANTITY, COUNT, COLOR, SIZE, IDENT, ENUM, SET
- Pack configuration check (`unit_size * pack_qty` + `unit_price`): different pack configs
  with equal total quantity (e.g., `2 x 500 ml` vs `1 L`) classify as `Bucket.VARIANT`
- One-sided attribute -> `Verdict.UNKNOWN` -> `Bucket.UNVERIFIED` (never `Bucket.EXACT`)
- GTIN equal + attribute/name conflict -> `Bucket.UNVERIFIED` + `gtin_attr_conflict` + `gtin_collision_suspect`
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal

from rapidfuzz import fuzz

from app.matching.contracts import (
    Attr,
    AttrDiff,
    AttrKind,
    Bucket,
    MatchDecision,
    VariantSpec,
    Verdict,
)
from app.matching.normalize import (
    delta_e_cie76,
    normalize_color,
    normalize_condition,
    normalize_core_model,
    normalize_quantity,
    normalize_size,
    storage_values_match,
)

_STORAGE_ATTR_NAMES = {"storage", "rom", "ssd", "hdd", "internal_storage", "capacity_storage"}
_QUANTITY_PACK_ATTR_NAMES = {"unit_size", "volume", "weight", "net_weight", "net_volume"}


def _normalize_ident(raw: str | None) -> str:
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9]+", "", raw.casefold())


def _is_effectively_empty(attr: Attr | None) -> bool:
    """Return True if an attribute is missing or its value was dropped (e.g. unverified LLM_SPAN)."""
    if attr is None:
        return True
    if not (attr.raw or "").strip() and attr.norm is None and attr.num is None:
        return True
    return False


def _display_val(attr: Attr | None) -> str | None:
    if _is_effectively_empty(attr):
        return None
    assert attr is not None
    return attr.norm or attr.raw


def _compare_core_models(
    src_model: str,
    cand_model: str,
    attr_tokens: set[str] | None = None,
) -> Verdict:
    """
    Compare `core_model` strings:
    - Normalize (casefold, strip punctuation/hyphens, token-order-insensitive).
    - Exact token-set & token-multiset equality -> MATCH.
    - If token difference consists solely of tokens already captured in `attrs` (e.g. color/unit), -> MATCH.
    - If one side has extra/different tokens that change the model (e.g. "iPhone 15" vs "iPhone 15 Pro",
      or different alphanumeric model numbers like "K2" vs "K8", "15" vs "16"), return MISMATCH.
    - If fuzzy ratio >= 85.0 on minor orthographic variation without conflicting model numbers/tokens -> AMBIGUOUS.
    - Else -> MISMATCH.
    """
    s_str, s_toks = normalize_core_model(src_model)
    c_str, c_toks = normalize_core_model(cand_model)
    if not s_toks or not c_toks:
        return Verdict.UNKNOWN

    if s_toks == c_toks or set(s_toks) == set(c_toks):
        return Verdict.MATCH

    s_set = set(s_toks)
    c_set = set(c_toks)
    if attr_tokens:
        s_stripped = {t for t in s_set if t not in attr_tokens} or s_set
        c_stripped = {t for t in c_set if t not in attr_tokens} or c_set
        if s_stripped == c_stripped:
            return Verdict.MATCH
        s_set, c_set = s_stripped, c_stripped

    # Check alphanumeric/numeric model tokens: any difference in numeric tokens is a hard MISMATCH
    s_nums = {t for t in s_set if any(ch.isdigit() for ch in t)}
    c_nums = {t for t in c_set if any(ch.isdigit() for ch in t)}
    if s_nums != c_nums:
        return Verdict.MISMATCH

    # Subset with extra distinct word tokens (e.g. "iphone 15" vs "iphone 15 pro", "s24" vs "s24 ultra")
    # Any extra whole word token in core_model when one is a strict subset of the other is a distinct model/edition
    sym_diff = s_set.symmetric_difference(c_set)
    if s_set.issubset(c_set) or c_set.issubset(s_set):
        return Verdict.MISMATCH

    # Check if difference is only a minor typo / spelling variant with high fuzzy similarity (>= 0.85)
    # and same token count where each differing token has high character similarity (>= 0.85)
    ratio = fuzz.ratio(s_str, c_str) / 100.0
    if ratio >= 0.85 and len(s_set) == len(c_set):
        s_only = sorted(s_set - c_set)
        c_only = sorted(c_set - s_set)
        if len(s_only) == 1 and len(c_only) == 1 and (fuzz.ratio(s_only[0], c_only[0]) / 100.0) >= 0.80:
            return Verdict.AMBIGUOUS

    return Verdict.MISMATCH


def _compare_quantity_attr(name: str, src_attr: Attr, cand_attr: Attr) -> Verdict:
    is_storage = name.lower() in _STORAGE_ATTR_NAMES or (
        (src_attr.unit or "").upper() in ("GB", "TB")
        and "ram" not in name.lower()
    )
    if is_storage and storage_values_match(src_attr, cand_attr):
        return Verdict.MATCH

    s_num, s_unit, _ = (
        (src_attr.num, src_attr.unit, src_attr.norm)
        if src_attr.num is not None and src_attr.unit
        else normalize_quantity(src_attr.raw, name)
    )
    c_num, c_unit, _ = (
        (cand_attr.num, cand_attr.unit, cand_attr.norm)
        if cand_attr.num is not None and cand_attr.unit
        else normalize_quantity(cand_attr.raw, name)
    )

    if s_num is None or c_num is None or not s_unit or not c_unit:
        return Verdict.UNKNOWN

    if s_unit.lower() != c_unit.lower():
        return Verdict.MISMATCH

    if abs(float(s_num) - float(c_num)) <= 1e-3:
        return Verdict.MATCH
    return Verdict.MISMATCH


def _compare_count_attr(src_attr: Attr, cand_attr: Attr) -> Verdict:
    def _to_int(a: Attr) -> int | None:
        if a.num is not None:
            return int(a.num)
        m = re.search(r"\b(\d+)\b", a.raw or "")
        return int(m.group(1)) if m else None

    s_cnt = _to_int(src_attr)
    c_cnt = _to_int(cand_attr)
    if s_cnt is None or c_cnt is None:
        return Verdict.UNKNOWN
    return Verdict.MATCH if s_cnt == c_cnt else Verdict.MISMATCH


def _compare_color_attr(
    src_attr: Attr,
    cand_attr: Attr,
    aliases: Mapping[str, str] | None = None,
) -> Verdict:
    s_canon, s_lab = normalize_color(src_attr.norm or src_attr.raw, aliases=aliases)
    c_canon, c_lab = normalize_color(cand_attr.norm or cand_attr.raw, aliases=aliases)

    if s_canon is not None and c_canon is not None:
        if s_canon == c_canon:
            return Verdict.MATCH
        if s_lab is not None and c_lab is not None and delta_e_cie76(s_lab, c_lab) < 5.0:
            return Verdict.MATCH
        return Verdict.MISMATCH

    # If both are the exact same raw marketing string (and not aliased to something else)
    s_raw_clean = " ".join((src_attr.norm or src_attr.raw or "").strip().casefold().split())
    c_raw_clean = " ".join((cand_attr.norm or cand_attr.raw or "").strip().casefold().split())
    if s_raw_clean and s_raw_clean == c_raw_clean and s_canon is None and c_canon is None:
        return Verdict.MATCH

    # Unresolved marketing name -> UNKNOWN
    return Verdict.UNKNOWN


def _compare_size_attr(src_attr: Attr, cand_attr: Attr) -> Verdict:
    s_sys, _, s_norm = normalize_size(src_attr.norm or src_attr.raw)
    c_sys, _, c_norm = normalize_size(cand_attr.norm or cand_attr.raw)

    if s_sys is None or c_sys is None or s_norm is None or c_norm is None:
        return Verdict.UNKNOWN

    if s_sys != c_sys:
        return Verdict.MISMATCH

    return Verdict.MATCH if s_norm == c_norm else Verdict.MISMATCH


def _compare_ident_attr(src_attr: Attr, cand_attr: Attr) -> Verdict:
    s_id = _normalize_ident(src_attr.norm or src_attr.raw)
    c_id = _normalize_ident(cand_attr.norm or cand_attr.raw)
    if not s_id or not c_id:
        return Verdict.UNKNOWN
    return Verdict.MATCH if s_id == c_id else Verdict.MISMATCH


def _compare_enum_attr(name: str, src_attr: Attr, cand_attr: Attr) -> Verdict:
    if name.lower() == "condition":
        s_val = normalize_condition(src_attr.norm or src_attr.raw)
        c_val = normalize_condition(cand_attr.norm or cand_attr.raw)
    else:
        s_val = " ".join((src_attr.norm or src_attr.raw or "").strip().casefold().split())
        c_val = " ".join((cand_attr.norm or cand_attr.raw or "").strip().casefold().split())
    if not s_val or not c_val:
        return Verdict.UNKNOWN
    return Verdict.MATCH if s_val == c_val else Verdict.MISMATCH


def _compare_set_attr(src_attr: Attr, cand_attr: Attr) -> Verdict:
    def _to_set(a: Attr) -> set[str]:
        raw = a.norm or a.raw or ""
        return {
            " ".join(part.strip().casefold().split())
            for part in re.split(r"[,;|+]+", raw)
            if part.strip()
        }

    s_set = _to_set(src_attr)
    c_set = _to_set(cand_attr)
    if not s_set or not c_set:
        return Verdict.UNKNOWN
    return Verdict.MATCH if s_set == c_set else Verdict.MISMATCH


def _compare_single_attr(
    name: str,
    src_attr: Attr | None,
    cand_attr: Attr | None,
    aliases: Mapping[str, str] | None = None,
) -> Verdict:
    if _is_effectively_empty(src_attr) or _is_effectively_empty(cand_attr):
        return Verdict.UNKNOWN
    assert src_attr is not None and cand_attr is not None

    kind = src_attr.kind
    if kind != cand_attr.kind:
        return Verdict.MISMATCH

    if kind == AttrKind.QUANTITY:
        return _compare_quantity_attr(name, src_attr, cand_attr)
    if kind == AttrKind.COUNT:
        return _compare_count_attr(src_attr, cand_attr)
    if kind == AttrKind.COLOR:
        return _compare_color_attr(src_attr, cand_attr, aliases=aliases)
    if kind == AttrKind.SIZE:
        return _compare_size_attr(src_attr, cand_attr)
    if kind == AttrKind.IDENT:
        return _compare_ident_attr(src_attr, cand_attr)
    if kind == AttrKind.ENUM:
        return _compare_enum_attr(name, src_attr, cand_attr)
    if kind == AttrKind.SET:
        return _compare_set_attr(src_attr, cand_attr)
    return Verdict.UNKNOWN


def _compute_pack_totals_and_unit_price(
    spec: VariantSpec,
) -> tuple[Decimal | None, Decimal | None, str | None, Decimal | None]:
    """
    Extract `(pack_qty, unit_size_num, base_unit, total_quantity)` from `spec.attrs`.
    Also supports computing `unit_price` when `spec.listed_price` is provided.
    """
    pack_attr = spec.attrs.get("pack_qty") or spec.attrs.get("pieces") or spec.attrs.get("count")
    pack_num: Decimal | None = None
    if pack_attr and not _is_effectively_empty(pack_attr):
        if pack_attr.num is not None:
            pack_num = Decimal(str(pack_attr.num))
        else:
            m = re.search(r"\b(\d+)\b", pack_attr.raw or "")
            if m:
                pack_num = Decimal(m.group(1))

    size_attr: Attr | None = None
    for k in ("unit_size", "volume", "weight", "net_weight", "net_volume"):
        if k in spec.attrs and not _is_effectively_empty(spec.attrs[k]):
            size_attr = spec.attrs[k]
            break

    size_num: Decimal | None = None
    base_unit: str | None = None
    if size_attr is not None:
        if size_attr.num is not None and size_attr.unit:
            size_num = Decimal(str(size_attr.num))
            base_unit = size_attr.unit
        else:
            size_num, base_unit, _ = normalize_quantity(size_attr.raw, size_attr.name)

    if size_num is not None:
        effective_pack = pack_num if pack_num is not None else Decimal("1")
        total_qty = effective_pack * size_num
        return effective_pack, size_num, base_unit, total_qty

    if pack_num is not None:
        return pack_num, Decimal("1"), "unit", pack_num

    return None, None, None, None


def _compute_unit_price(
    listed_price: Decimal | None,
    total_qty: Decimal | None,
    base_unit: str | None,
) -> tuple[Decimal | None, str | None]:
    if listed_price is None or listed_price <= 0 or total_qty is None or total_qty <= 0 or not base_unit:
        return None, None
    u = base_unit.lower()
    if u in ("ml", "g"):
        # Per 100ml or 100g
        up = ((listed_price / total_qty) * Decimal("100")).quantize(Decimal("0.01"))
        return up, f"INR/100{u}"
    up = (listed_price / total_qty).quantize(Decimal("0.01"))
    return up, f"INR/{base_unit}"


def decide(
    src: VariantSpec,
    cand: VariantSpec,
    *,
    aliases: Mapping[str, str] | None = None,
) -> MatchDecision:
    """
    Pure, deterministic variant comparator implementing the exact M1 + Addendum v2 specification.
    """
    reasons: list[str] = []
    deciding_confidences: list[float] = []

    # ── 1. Identity Evaluation ──
    src_gtin = _normalize_ident(src.gtin)
    cand_gtin = _normalize_ident(cand.gtin)
    both_gtin = bool(src_gtin and cand_gtin)
    gtin_equal = both_gtin and (src_gtin == cand_gtin)

    if both_gtin and not gtin_equal:
        return MatchDecision(
            bucket=Bucket.REJECTED,
            identity=Verdict.MISMATCH,
            diffs=[],
            confidence=1.0,
            reasons=["gtin_differs"],
            judged_by="deterministic",
        )

    # Product type guard (when both present)
    if src.product_type and cand.product_type:
        st = " ".join(src.product_type.strip().casefold().split())
        ct = " ".join(cand.product_type.strip().casefold().split())
        if st != ct:
            if gtin_equal:
                reasons.extend(["gtin_attr_conflict", "gtin_collision_suspect", "product_type_differs"])
            else:
                return MatchDecision(
                    bucket=Bucket.REJECTED,
                    identity=Verdict.MISMATCH,
                    diffs=[],
                    confidence=1.0,
                    reasons=["product_type_mismatch"],
                    judged_by="deterministic",
                )

    # Brand check
    s_brand = _normalize_ident(src.brand)
    c_brand = _normalize_ident(cand.brand)
    if s_brand and c_brand:
        brand_verdict = Verdict.MATCH if s_brand == c_brand else Verdict.MISMATCH
    else:
        brand_verdict = Verdict.UNKNOWN

    # Core model check (strip tokens already represented in explicit attributes)
    shared_attr_tokens: set[str] = set()
    for spec_o in (src, cand):
        for a in spec_o.attrs.values():
            for raw_s in (a.raw, a.norm):
                if raw_s:
                    for tok in re.split(r"[^a-z0-9]+", raw_s.casefold()):
                        if len(tok) >= 2 and tok != "alpha":
                            shared_attr_tokens.add(tok)
    model_verdict = _compare_core_models(src.core_model, cand.core_model, attr_tokens=shared_attr_tokens)

    if gtin_equal:
        if brand_verdict == Verdict.MISMATCH or model_verdict == Verdict.MISMATCH:
            for flag in ("gtin_attr_conflict", "gtin_collision_suspect"):
                if flag not in reasons:
                    reasons.append(flag)
            identity_verdict = Verdict.AMBIGUOUS
        else:
            identity_verdict = Verdict.MATCH
    else:
        if brand_verdict == Verdict.MISMATCH:
            return MatchDecision(
                bucket=Bucket.REJECTED,
                identity=Verdict.MISMATCH,
                diffs=[],
                confidence=1.0,
                reasons=["brand_mismatch"],
                judged_by="deterministic",
            )
        if model_verdict == Verdict.MISMATCH:
            return MatchDecision(
                bucket=Bucket.REJECTED,
                identity=Verdict.MISMATCH,
                diffs=[],
                confidence=1.0,
                reasons=["core_model_mismatch"],
                judged_by="deterministic",
            )
        if model_verdict == Verdict.AMBIGUOUS:
            identity_verdict = Verdict.AMBIGUOUS
            reasons.append("core_model_ambiguous")
        elif model_verdict == Verdict.UNKNOWN:
            identity_verdict = Verdict.UNKNOWN
            reasons.append("core_model_unknown")
        elif brand_verdict == Verdict.UNKNOWN:
            identity_verdict = Verdict.UNKNOWN
            reasons.append("brand_unknown")
        else:
            identity_verdict = Verdict.MATCH

    # ── 2. Pack Configuration & Total Quantity / Unit Price Calculation ──
    s_pack, s_unit_sz, s_base_u, s_total_qty = _compute_pack_totals_and_unit_price(src)
    c_pack, c_unit_sz, c_base_u, c_total_qty = _compute_pack_totals_and_unit_price(cand)
    up_src, up_unit_src = _compute_unit_price(src.listed_price, s_total_qty, s_base_u)
    up_cand, up_unit_cand = _compute_unit_price(cand.listed_price, c_total_qty, c_base_u)
    unit_price_unit = up_unit_src or up_unit_cand

    # ── 3. Per-Attribute Evaluation across Union of Attribute Names ──
    all_attr_names = list(dict.fromkeys(list(src.attrs.keys()) + list(cand.attrs.keys())))

    # Check if pack configuration differs when one side has implicit pack_qty=1 (e.g. "2 x 500 ml" vs "1 L")
    pack_config_handled = False
    if (
        s_pack is not None
        and c_pack is not None
        and s_unit_sz is not None
        and c_unit_sz is not None
        and s_base_u
        and c_base_u
        and s_base_u.lower() == c_base_u.lower()
        and ("pack_qty" in src.attrs or "pack_qty" in cand.attrs)
        and ("pack_qty" not in src.attrs or "pack_qty" not in cand.attrs)
    ):
        # One side specified explicit pack_qty (e.g. 2 x 500 ml) while the other specified single container (e.g. 1 L)
        if s_pack != c_pack or s_unit_sz != c_unit_sz:
            pack_config_handled = True

    diffs: list[AttrDiff] = []
    has_mismatch = False
    unknown_attrs: list[str] = []

    for name in all_attr_names:
        if pack_config_handled and name in ("pack_qty", "unit_size", "volume", "weight"):
            continue
        s_attr = src.attrs.get(name)
        c_attr = cand.attrs.get(name)
        v = _compare_single_attr(name, s_attr, c_attr, aliases=aliases)

        if s_attr and not _is_effectively_empty(s_attr):
            deciding_confidences.append(s_attr.evidence.confidence)
        if c_attr and not _is_effectively_empty(c_attr):
            deciding_confidences.append(c_attr.evidence.confidence)

        if v == Verdict.MISMATCH:
            has_mismatch = True
            diffs.append(
                AttrDiff(
                    name=name,
                    source_value=_display_val(s_attr),
                    cand_value=_display_val(c_attr),
                    verdict=Verdict.MISMATCH,
                )
            )
            reasons.append(f"attr_mismatch:{name}")
        elif v == Verdict.UNKNOWN:
            unknown_attrs.append(name)
            diffs.append(
                AttrDiff(
                    name=name,
                    source_value=_display_val(s_attr),
                    cand_value=_display_val(c_attr),
                    verdict=Verdict.UNKNOWN,
                )
            )

    if pack_config_handled:
        has_mismatch = True
        s_cfg = f"{int(s_pack)} x {s_unit_sz:g} {s_base_u}" if s_pack and s_unit_sz else None
        c_cfg = f"{int(c_pack)} x {c_unit_sz:g} {c_base_u}" if c_pack and c_unit_sz else None
        diffs.append(
            AttrDiff(
                name="pack_config",
                source_value=s_cfg,
                cand_value=c_cfg,
                verdict=Verdict.MISMATCH,
            )
        )
        reasons.append("pack_config_differs")
        if s_total_qty is not None and c_total_qty is not None and s_total_qty == c_total_qty:
            reasons.append("equal_total_quantity_different_pack_config")
    elif (
        s_pack is not None
        and c_pack is not None
        and s_total_qty is not None
        and c_total_qty is not None
        and s_total_qty == c_total_qty
        and s_pack != c_pack
        and "pack_config_differs" not in reasons
    ):
        reasons.append("pack_config_differs")
        reasons.append("equal_total_quantity_different_pack_config")

    confidence = round(min(deciding_confidences), 4) if deciding_confidences else 1.0

    # ── 4. Aggregate Bucket Decision ──
    if gtin_equal and (has_mismatch or "gtin_attr_conflict" in reasons):
        for flag in ("gtin_attr_conflict", "gtin_collision_suspect"):
            if flag not in reasons:
                reasons.insert(0, flag)
        return MatchDecision(
            bucket=Bucket.UNVERIFIED,
            identity=identity_verdict,
            diffs=diffs,
            confidence=confidence,
            reasons=reasons,
            judged_by="deterministic",
            total_quantity_source=s_total_qty,
            total_quantity_cand=c_total_qty,
            unit_price_source=up_src,
            unit_price_cand=up_cand,
            unit_price_unit=unit_price_unit,
        )

    if has_mismatch:
        return MatchDecision(
            bucket=Bucket.VARIANT,
            identity=identity_verdict,
            diffs=diffs,
            confidence=confidence,
            reasons=reasons,
            judged_by="deterministic",
            total_quantity_source=s_total_qty,
            total_quantity_cand=c_total_qty,
            unit_price_source=up_src,
            unit_price_cand=up_cand,
            unit_price_unit=unit_price_unit,
        )

    if unknown_attrs or identity_verdict in (Verdict.UNKNOWN, Verdict.AMBIGUOUS):
        if unknown_attrs:
            reasons.append(f"couldn't confirm: {', '.join(unknown_attrs)}")
        return MatchDecision(
            bucket=Bucket.UNVERIFIED,
            identity=identity_verdict,
            diffs=diffs,
            confidence=confidence,
            reasons=reasons,
            judged_by="deterministic",
            total_quantity_source=s_total_qty,
            total_quantity_cand=c_total_qty,
            unit_price_source=up_src,
            unit_price_cand=up_cand,
            unit_price_unit=unit_price_unit,
        )

    return MatchDecision(
        bucket=Bucket.EXACT,
        identity=Verdict.MATCH,
        diffs=[],
        confidence=confidence,
        reasons=["all_attributes_verified"],
        judged_by="deterministic",
        total_quantity_source=s_total_qty,
        total_quantity_cand=c_total_qty,
        unit_price_source=up_src,
        unit_price_cand=up_cand,
        unit_price_unit=unit_price_unit,
    )
