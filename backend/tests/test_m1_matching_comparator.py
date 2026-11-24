"""
M1 Unit & Property Tests for `app.matching` (contracts, normalize, comparator).

Zero network calls. Tests all 12 required parametrized cases, Hypothesis properties
(idempotence, storage 1TB==1000GB==1024GB, 8GB+256GB == 8 GB / 256 GB), evidence precedence,
LLM span verification, and all comparator branches (>= 95% coverage on comparator.py).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings as hyp_settings, strategies as st

from app.matching.comparator import decide
from app.matching.contracts import (
    Attr,
    AttrKind,
    Bucket,
    Evidence,
    Source,
    VariantSpec,
    Verdict,
    select_highest_precedence_attr,
    verify_llm_span,
)
from app.matching.normalize import (
    delta_e_cie76,
    normalize_color,
    normalize_condition,
    normalize_core_model,
    normalize_quantity,
    normalize_size,
    parse_pack_and_quantity,
    parse_ram_and_storage,
    storage_values_match,
)


def _ev(source: Source = Source.DOM_SELECTED, span: str | None = None, conf: float = 0.98) -> Evidence:
    return Evidence(source=source, span=span, confidence=conf)


def _attr(
    name: str,
    kind: AttrKind,
    raw: str,
    *,
    norm: str | None = None,
    num: Decimal | None = None,
    unit: str | None = None,
    source: Source = Source.DOM_SELECTED,
    span: str | None = None,
    conf: float = 0.98,
) -> Attr:
    return Attr(
        name=name,
        kind=kind,
        raw=raw,
        norm=norm,
        num=num,
        unit=unit,
        evidence=_ev(source=source, span=span or raw, conf=conf),
    )


def _spec(
    *,
    core_model: str = "iPhone 15",
    brand: str | None = "Apple",
    product_type: str | None = "smartphone",
    gtin: str | None = None,
    mpn: str | None = None,
    attrs: dict[str, Attr] | None = None,
    listed_price: Decimal | None = None,
    title: str | None = None,
) -> VariantSpec:
    return VariantSpec(
        url="https://example.in/p/1",
        title=title or f"{brand or ''} {core_model}".strip(),
        brand=brand,
        product_type=product_type,
        core_model=core_model,
        gtin=gtin,
        mpn=mpn,
        attrs=attrs or {},
        listed_price=listed_price,
    )


# ── 1. Required M1 Parametrized Comparator Test Matrix (all 12 required cases) ──


@pytest.mark.parametrize(
    "case_name,src_spec,cand_spec,aliases,expected_bucket,expected_attr_verdict,expected_reason_substr",
    [
        (
            "128GB vs 256GB -> MISMATCH",
            _spec(attrs={"storage": _attr("storage", AttrKind.QUANTITY, "128GB")}),
            _spec(attrs={"storage": _attr("storage", AttrKind.QUANTITY, "256GB")}),
            None,
            Bucket.VARIANT,
            Verdict.MISMATCH,
            "attr_mismatch:storage",
        ),
        (
            "1TB vs 1000GB -> MATCH",
            _spec(attrs={"storage": _attr("storage", AttrKind.QUANTITY, "1TB")}),
            _spec(attrs={"storage": _attr("storage", AttrKind.QUANTITY, "1000GB")}),
            None,
            Bucket.EXACT,
            Verdict.MATCH,
            "all_attributes_verified",
        ),
        (
            "8GB+256GB vs 8 GB / 256 GB -> MATCH",
            _spec(attrs=parse_ram_and_storage("8GB+256GB")),
            _spec(attrs=parse_ram_and_storage("8 GB / 256 GB")),
            None,
            Bucket.EXACT,
            Verdict.MATCH,
            "all_attributes_verified",
        ),
        (
            "Pack of 2 vs Pack of 3 -> MISMATCH",
            _spec(
                brand="Nivea",
                core_model="Soft Light Moisturizer",
                product_type="cream",
                attrs={"pack_qty": _attr("pack_qty", AttrKind.COUNT, "Pack of 2", num=Decimal("2"))},
            ),
            _spec(
                brand="Nivea",
                core_model="Soft Light Moisturizer",
                product_type="cream",
                attrs={"pack_qty": _attr("pack_qty", AttrKind.COUNT, "Pack of 3", num=Decimal("3"))},
            ),
            None,
            Bucket.VARIANT,
            Verdict.MISMATCH,
            "attr_mismatch:pack_qty",
        ),
        (
            "2 x 500 ml vs 1 L -> VARIANT (config differs, unit price shown)",
            _spec(
                brand="Cetaphil",
                core_model="Gentle Skin Cleanser",
                product_type="cleanser",
                attrs=parse_pack_and_quantity("2 x 500 ml"),
                listed_price=Decimal("1200"),
            ),
            _spec(
                brand="Cetaphil",
                core_model="Gentle Skin Cleanser",
                product_type="cleanser",
                attrs=parse_pack_and_quantity("1 L"),
                listed_price=Decimal("1100"),
            ),
            None,
            Bucket.VARIANT,
            Verdict.MISMATCH,
            "pack_config_differs",
        ),
        (
            "Black vs Blue -> MISMATCH",
            _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Black")}),
            _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Blue")}),
            None,
            Bucket.VARIANT,
            Verdict.MISMATCH,
            "attr_mismatch:color",
        ),
        (
            "Midnight vs Black (no alias) -> UNKNOWN",
            _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Midnight")}),
            _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Black")}),
            None,
            Bucket.UNVERIFIED,
            Verdict.UNKNOWN,
            "couldn't confirm: color",
        ),
        (
            "Midnight vs Black (with alias) -> MATCH",
            _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Midnight")}),
            _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Black")}),
            {"midnight": "black"},
            Bucket.EXACT,
            Verdict.MATCH,
            "all_attributes_verified",
        ),
        (
            "UK 9 vs US 9 -> MISMATCH",
            _spec(
                brand="Nike",
                core_model="Pegasus 40",
                product_type="shoes",
                attrs={"size": _attr("size", AttrKind.SIZE, "UK 9")},
            ),
            _spec(
                brand="Nike",
                core_model="Pegasus 40",
                product_type="shoes",
                attrs={"size": _attr("size", AttrKind.SIZE, "US 9")},
            ),
            None,
            Bucket.VARIANT,
            Verdict.MISMATCH,
            "attr_mismatch:size",
        ),
        (
            "M vs Medium -> MATCH",
            _spec(
                brand="Levi's",
                core_model="511 Slim Crew Tee",
                product_type="t-shirt",
                attrs={"size": _attr("size", AttrKind.SIZE, "M")},
            ),
            _spec(
                brand="Levi's",
                core_model="511 Slim Crew Tee",
                product_type="t-shirt",
                attrs={"size": _attr("size", AttrKind.SIZE, "Medium")},
            ),
            None,
            Bucket.EXACT,
            Verdict.MATCH,
            "all_attributes_verified",
        ),
        (
            "size system unknown -> UNKNOWN",
            _spec(
                brand="Nike",
                core_model="Pegasus 40",
                product_type="shoes",
                attrs={"size": _attr("size", AttrKind.SIZE, "UK 9")},
            ),
            _spec(
                brand="Nike",
                core_model="Pegasus 40",
                product_type="shoes",
                attrs={"size": _attr("size", AttrKind.SIZE, "9")},
            ),
            None,
            Bucket.UNVERIFIED,
            Verdict.UNKNOWN,
            "couldn't confirm: size",
        ),
        (
            "iPhone 15 vs iPhone 15 Pro -> REJECTED",
            _spec(core_model="iPhone 15"),
            _spec(core_model="iPhone 15 Pro"),
            None,
            Bucket.REJECTED,
            Verdict.MISMATCH,
            "core_model_mismatch",
        ),
        (
            "new vs refurbished -> MISMATCH",
            _spec(attrs={"condition": _attr("condition", AttrKind.ENUM, "New")}),
            _spec(attrs={"condition": _attr("condition", AttrKind.ENUM, "Refurbished")}),
            None,
            Bucket.VARIANT,
            Verdict.MISMATCH,
            "attr_mismatch:condition",
        ),
        (
            "source has color, candidate has none -> UNKNOWN (bucket UNVERIFIED)",
            _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Black")}),
            _spec(attrs={}),
            None,
            Bucket.UNVERIFIED,
            Verdict.UNKNOWN,
            "couldn't confirm: color",
        ),
        (
            "GTIN equal, color differs -> UNVERIFIED + gtin_attr_conflict",
            _spec(
                gtin="0195949036057",
                attrs={"color": _attr("color", AttrKind.COLOR, "Black")},
            ),
            _spec(
                gtin="0195949036057",
                attrs={"color": _attr("color", AttrKind.COLOR, "Blue")},
            ),
            None,
            Bucket.UNVERIFIED,
            Verdict.MISMATCH,
            "gtin_attr_conflict",
        ),
    ],
)
def test_required_m1_comparator_cases(
    case_name: str,
    src_spec: VariantSpec,
    cand_spec: VariantSpec,
    aliases: dict[str, str] | None,
    expected_bucket: Bucket,
    expected_attr_verdict: Verdict,
    expected_reason_substr: str,
):
    decision = decide(src_spec, cand_spec, aliases=aliases)
    assert decision.bucket == expected_bucket, f"[{case_name}] expected {expected_bucket}, got {decision}"
    assert any(expected_reason_substr in r for r in decision.reasons), (
        f"[{case_name}] expected '{expected_reason_substr}' in {decision.reasons}"
    )
    if decision.diffs:
        assert decision.diffs[0].verdict == expected_attr_verdict

    if case_name.startswith("2 x 500 ml vs 1 L"):
        assert decision.total_quantity_source == Decimal("1000")
        assert decision.total_quantity_cand == Decimal("1000")
        assert decision.unit_price_source == Decimal("120.00")
        assert decision.unit_price_cand == Decimal("110.00")
        assert decision.unit_price_unit == "INR/100ml"

    if case_name.startswith("GTIN equal, color differs"):
        assert "gtin_attr_conflict" in decision.reasons
        assert "gtin_collision_suspect" in decision.reasons


def test_llm_span_not_found_in_input_dropped_to_unknown():
    """Required case: LLM span not found in input -> value dropped -> UNKNOWN."""
    page_text = "Apple iPhone 15 128GB in stock at official store."
    hallucinated_color = _attr(
        "color",
        AttrKind.COLOR,
        "Black",
        source=Source.LLM_SPAN,
        span="Color: Black",  # NOT a substring of page_text
        conf=0.70,
    )
    verified = verify_llm_span(hallucinated_color, page_text)
    assert verified is None

    # Verified span IS kept
    valid_storage = _attr(
        "storage",
        AttrKind.QUANTITY,
        "128GB",
        source=Source.LLM_SPAN,
        span="128GB",
        conf=0.80,
    )
    assert verify_llm_span(valid_storage, page_text) is not None

    # When hallucinated color is dropped, candidate has only storage -> comparing with source that has color+storage -> UNVERIFIED
    src = _spec(
        attrs={
            "storage": _attr("storage", AttrKind.QUANTITY, "128GB"),
            "color": _attr("color", AttrKind.COLOR, "Black"),
        }
    )
    cand_attrs = {"storage": valid_storage}
    if verified is not None:
        cand_attrs["color"] = verified
    cand = _spec(attrs=cand_attrs)

    decision = decide(src, cand)
    assert decision.bucket == Bucket.UNVERIFIED
    assert any(d.name == "color" and d.verdict == Verdict.UNKNOWN for d in decision.diffs)


def test_evidence_precedence_ordering():
    """DOM_SELECTED > URL_PARAM > JSONLD > HYDRATION > TITLE_PARSE > LLM_SPAN."""
    cands = [
        _attr("color", AttrKind.COLOR, "Red", source=Source.LLM_SPAN, conf=0.99),
        _attr("color", AttrKind.COLOR, "Blue", source=Source.TITLE_PARSE, conf=0.99),
        _attr("color", AttrKind.COLOR, "Green", source=Source.HYDRATION, conf=0.95),
        _attr("color", AttrKind.COLOR, "Yellow", source=Source.JSONLD, conf=0.95),
        _attr("color", AttrKind.COLOR, "Purple", source=Source.URL_PARAM, conf=0.90),
        _attr("color", AttrKind.COLOR, "Black", source=Source.DOM_SELECTED, conf=0.85),
    ]
    winner = select_highest_precedence_attr(cands)
    assert winner.raw == "Black"
    assert winner.evidence.source == Source.DOM_SELECTED

    with pytest.raises(ValueError):
        select_highest_precedence_attr([])


def test_storage_1tb_1000gb_1024gb_mutual_equivalence():
    """Verify '1TB' == '1000GB' == '1024GB' for storage in all pairings."""
    a_1tb = _attr("storage", AttrKind.QUANTITY, "1TB")
    a_1000gb = _attr("storage", AttrKind.QUANTITY, "1000GB")
    a_1024gb = _attr("storage", AttrKind.QUANTITY, "1024GB")
    a_512gb = _attr("storage", AttrKind.QUANTITY, "512GB")

    for left in (a_1tb, a_1000gb, a_1024gb):
        for right in (a_1tb, a_1000gb, a_1024gb):
            assert storage_values_match(left, right)
            dec = decide(_spec(attrs={"storage": left}), _spec(attrs={"storage": right}))
            assert dec.bucket == Bucket.EXACT

    assert not storage_values_match(a_1tb, a_512gb)


def test_comparator_additional_attr_kinds_and_identity_branches():
    """Cover IDENT, SET, GTIN differ, brand mismatch, missing brand, ambiguous core_model, and deltaE < 5 color match."""
    # 1. GTIN differs -> REJECTED
    d_gtin = decide(_spec(gtin="8901234567890"), _spec(gtin="8901234567899"))
    assert d_gtin.bucket == Bucket.REJECTED
    assert "gtin_differs" in d_gtin.reasons

    # 2. Brand differs -> REJECTED
    d_brand = decide(_spec(brand="Apple"), _spec(brand="Samsung"))
    assert d_brand.bucket == Bucket.REJECTED
    assert "brand_mismatch" in d_brand.reasons

    # 3. GTIN equal but brand/core_model differs -> UNVERIFIED + gtin_collision_suspect
    d_collision = decide(
        _spec(brand="Apple", core_model="iPhone 15", gtin="0195949036057"),
        _spec(brand="Spigen", core_model="Armor Case", product_type="case", gtin="0195949036057"),
    )
    assert d_collision.bucket == Bucket.UNVERIFIED
    assert "gtin_collision_suspect" in d_collision.reasons
    assert "gtin_attr_conflict" in d_collision.reasons

    # 4. Brand missing on one side -> UNVERIFIED
    d_nobrand = decide(_spec(brand="Apple"), _spec(brand=None))
    assert d_nobrand.bucket == Bucket.UNVERIFIED
    assert d_nobrand.identity == Verdict.UNKNOWN

    # 5. Core model slight typo (>= 0.85 ratio) -> AMBIGUOUS -> UNVERIFIED
    d_ambig = decide(
        _spec(brand="Logitech", core_model="MX Master 3S Wireless"),
        _spec(brand="Logitech", core_model="MX Master 3S Wireles"),
    )
    assert d_ambig.identity == Verdict.AMBIGUOUS
    assert d_ambig.bucket == Bucket.UNVERIFIED

    # 6. IDENT and SET attributes
    s_ident = _spec(
        attrs={
            "mpn": _attr("mpn", AttrKind.IDENT, "MTJV3HN/A"),
            "bundle": _attr("bundle", AttrKind.SET, "Earbuds, MagSafe Case, USB-C Cable"),
        }
    )
    c_ident_ok = _spec(
        attrs={
            "mpn": _attr("mpn", AttrKind.IDENT, "mtjv3hn-a"),
            "bundle": _attr("bundle", AttrKind.SET, "USB-C Cable, Earbuds, MagSafe Case"),
        }
    )
    c_ident_bad = _spec(
        attrs={
            "mpn": _attr("mpn", AttrKind.IDENT, "MTJV3HN/A"),
            "bundle": _attr("bundle", AttrKind.SET, "Earbuds, Lightning Case"),
        }
    )
    assert decide(s_ident, c_ident_ok).bucket == Bucket.EXACT
    assert decide(s_ident, c_ident_bad).bucket == Bucket.VARIANT

    # 7. Color CIELAB deltaE < 5 match (near-identical hex colors) and grey/gray equivalence
    d_gray = decide(
        _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Grey")}),
        _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Gray")}),
    )
    assert d_gray.bucket == Bucket.EXACT

    d_hex = decide(
        _spec(attrs={"color": _attr("color", AttrKind.COLOR, "#101010")}),
        _spec(attrs={"color": _attr("color", AttrKind.COLOR, "#121212")}),
    )
    assert d_hex.bucket == Bucket.EXACT

    # 8. Same unresolved marketing color on both sides
    d_same_mkt = decide(
        _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Natural Titanium")}),
        _spec(attrs={"color": _attr("color", AttrKind.COLOR, "natural titanium")}),
    )
    assert d_same_mkt.bucket == Bucket.EXACT

    # 9. Product type mismatch without GTIN -> REJECTED
    d_ptype = decide(
        _spec(product_type="smartphone"),
        _spec(product_type="phone case"),
    )
    assert d_ptype.bucket == Bucket.REJECTED
    assert "product_type_mismatch" in d_ptype.reasons

    # 10. Non-subset distinct core models & empty core model
    d_distinct_model = decide(
        _spec(brand="Sony", core_model="Bravia XR OLED"),
        _spec(brand="Sony", core_model="PlayStation DualSense Controller"),
    )
    assert d_distinct_model.bucket == Bucket.REJECTED
    d_empty_model = decide(_spec(core_model=""), _spec(core_model="iPhone 15"))
    assert d_empty_model.bucket == Bucket.UNVERIFIED

    # 11. Quantity incompatible units (500 ml vs 500 g) and unparseable quantity
    d_incomp_qty = decide(
        _spec(attrs={"unit_size": _attr("unit_size", AttrKind.QUANTITY, "500 ml")}),
        _spec(attrs={"unit_size": _attr("unit_size", AttrKind.QUANTITY, "500 g")}),
    )
    assert d_incomp_qty.bucket == Bucket.VARIANT
    d_unparse_qty = decide(
        _spec(attrs={"unit_size": _attr("unit_size", AttrKind.QUANTITY, "invalid_qty")}),
        _spec(attrs={"unit_size": _attr("unit_size", AttrKind.QUANTITY, "500 ml")}),
    )
    assert d_unparse_qty.bucket == Bucket.UNVERIFIED

    # 12. Count parsed from raw text without num, unparseable count, and unit price per count
    d_cnt_raw = decide(
        _spec(
            attrs={"pack_qty": _attr("pack_qty", AttrKind.COUNT, "Pack of 4")},
            listed_price=Decimal("400"),
        ),
        _spec(
            attrs={"pack_qty": _attr("pack_qty", AttrKind.COUNT, "4 pcs")},
            listed_price=Decimal("360"),
        ),
    )
    assert d_cnt_raw.bucket == Bucket.EXACT
    assert d_cnt_raw.unit_price_source == Decimal("100.00")
    assert d_cnt_raw.unit_price_unit == "INR/unit"

    d_cnt_unknown = decide(
        _spec(attrs={"pack_qty": _attr("pack_qty", AttrKind.COUNT, "multi")}),
        _spec(attrs={"pack_qty": _attr("pack_qty", AttrKind.COUNT, "2")}),
    )
    assert d_cnt_unknown.bucket == Bucket.UNVERIFIED

    # 13. Implicit pack_qty=1 ("2 x 500 ml" with explicit pack_qty vs "1 L" with only unit_size)
    d_implicit_pack = decide(
        _spec(
            attrs={
                "pack_qty": _attr("pack_qty", AttrKind.COUNT, "2"),
                "unit_size": _attr("unit_size", AttrKind.QUANTITY, "500 ml"),
            },
            listed_price=Decimal("500"),
        ),
        _spec(
            attrs={
                "unit_size": _attr("unit_size", AttrKind.QUANTITY, "1 L"),
            },
            listed_price=Decimal("480"),
        ),
    )
    assert d_implicit_pack.bucket == Bucket.VARIANT
    assert "pack_config_differs" in d_implicit_pack.reasons
    assert "equal_total_quantity_different_pack_config" in d_implicit_pack.reasons

    # 14. Size same system different number (UK 9 vs UK 10), non-condition ENUM, and kind mismatch
    d_uk_diff = decide(
        _spec(attrs={"size": _attr("size", AttrKind.SIZE, "UK 9")}),
        _spec(attrs={"size": _attr("size", AttrKind.SIZE, "UK 10")}),
    )
    assert d_uk_diff.bucket == Bucket.VARIANT

    d_enum_conn = decide(
        _spec(attrs={"connectivity": _attr("connectivity", AttrKind.ENUM, "Wi-Fi")}),
        _spec(attrs={"connectivity": _attr("connectivity", AttrKind.ENUM, "Wi-Fi + Cellular")}),
    )
    assert d_enum_conn.bucket == Bucket.VARIANT

    d_kind_mismatch = decide(
        _spec(attrs={"custom": _attr("custom", AttrKind.ENUM, "128")}),
        _spec(attrs={"custom": _attr("custom", AttrKind.COUNT, "128")}),
    )
    assert d_kind_mismatch.bucket == Bucket.VARIANT

    # 15. Empty / dropped attribute value handled as UNKNOWN
    d_empty_attr = decide(
        _spec(attrs={"color": _attr("color", AttrKind.COLOR, "   ")}),
        _spec(attrs={"color": _attr("color", AttrKind.COLOR, "Black")}),
    )
    assert d_empty_attr.bucket == Bucket.UNVERIFIED


# ── 2. Hypothesis Property Tests ──


@hyp_settings(max_examples=40, deadline=None)
@given(
    ram_gb=st.sampled_from([4, 6, 8, 12, 16, 24, 32, 64]),
    stor_gb=st.sampled_from([64, 128, 256, 512]),
)
def test_property_ram_plus_storage_formatting_invariance(ram_gb: int, stor_gb: int):
    """Property: 'XGB+YGB' and 'X GB / Y GB' always parse to identical normalized attributes and MATCH."""
    fmt_a = f"{ram_gb}GB+{stor_gb}GB"
    fmt_b = f"{ram_gb} GB / {stor_gb} GB"
    attrs_a = parse_ram_and_storage(fmt_a)
    attrs_b = parse_ram_and_storage(fmt_b)
    assert attrs_a["ram"].norm == attrs_b["ram"].norm
    assert attrs_a["storage"].norm == attrs_b["storage"].norm
    assert decide(_spec(attrs=attrs_a), _spec(attrs=attrs_b)).bucket == Bucket.EXACT


@hyp_settings(max_examples=40, deadline=None)
@given(
    val=st.integers(min_value=1, max_value=5000),
    unit=st.sampled_from(["g", "kg", "ml", "l", "cm", "mm", "GB", "TB"]),
)
def test_property_quantity_normalizer_idempotence(val: int, unit: str):
    """Property: normalizing an already-normalized quantity string yields the exact same (num, unit, norm)."""
    num1, unit1, norm1 = normalize_quantity(f"{val} {unit}")
    assert num1 is not None and unit1 is not None and norm1 is not None
    num2, unit2, norm2 = normalize_quantity(norm1)
    assert num1 == num2
    assert unit1 == unit2
    assert norm1 == norm2


@hyp_settings(max_examples=30, deadline=None)
@given(
    tb=st.integers(min_value=1, max_value=8),
)
def test_property_storage_tb_1000_1024_equivalence(tb: int):
    """Property: N TB matches both (N*1000) GB and (N*1024) GB."""
    a_tb = _attr("storage", AttrKind.QUANTITY, f"{tb}TB")
    a_1000 = _attr("storage", AttrKind.QUANTITY, f"{tb * 1000}GB")
    a_1024 = _attr("storage", AttrKind.QUANTITY, f"{tb * 1024}GB")
    assert decide(_spec(attrs={"storage": a_tb}), _spec(attrs={"storage": a_1000})).bucket == Bucket.EXACT
    assert decide(_spec(attrs={"storage": a_tb}), _spec(attrs={"storage": a_1024})).bucket == Bucket.EXACT
