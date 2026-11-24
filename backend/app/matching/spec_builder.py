"""
Source & Candidate `VariantSpec` Builder (`spec_builder.py`).

Extracts `VariantSpec` with full `Evidence` provenance (`DOM_SELECTED > URL_PARAM > JSONLD > HYDRATION > TITLE_PARSE > LLM_SPAN`):
- Captures explicitly selected variant options from DOM twisters/swatches, URL parameters (`?variant=`, `pid=`, `/dp/<ASIN>`),
  hydration state (`window.__INITIAL_STATE__`, `__NEXT_DATA__`), and JSON-LD.
- Never collapses multi-variant JSON-LD `offers[]` or Shopify `variants[]` to the cheapest variant when a specific
  variant is selected or when variant selection is ambiguous.
- If a page exposes selectable color/size options (e.g. "Select Size" dropdown or multi-variant options without a
  selected state) and the selected value cannot be determined, emits an explicit empty/UNKNOWN `Attr` so downstream
  `comparator.decide` returns `Verdict.UNKNOWN` (`Bucket.UNVERIFIED`) and never assumes a parent default.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

from selectolax.lexbor import LexborHTMLParser

from app.matching.contracts import (
    Attr,
    AttrKind,
    Evidence,
    Source,
    VariantSpec,
    select_highest_precedence_attr,
    verify_llm_span,
)
from app.matching.normalize import (
    normalize_color,
    normalize_condition,
    normalize_quantity,
    normalize_size,
    parse_pack_and_quantity,
    parse_ram_and_storage,
)
from app.schemas.extraction import ClientPayload

_SOURCE_MAP: dict[str, Source] = {
    "dom_selected": Source.DOM_SELECTED,
    "url_param": Source.URL_PARAM,
    "jsonld": Source.JSONLD,
    "hydration": Source.HYDRATION,
    "title_parse": Source.TITLE_PARSE,
    "llm_span": Source.LLM_SPAN,
}

_SOURCE_CONFIDENCE: dict[Source, float] = {
    Source.DOM_SELECTED: 0.99,
    Source.URL_PARAM: 0.97,
    Source.JSONLD: 0.94,
    Source.HYDRATION: 0.92,
    Source.TITLE_PARSE: 0.85,
    Source.LLM_SPAN: 0.75,
}

_UNSELECTED_PLACEHOLDERS = {
    "",
    "select",
    "select size",
    "select color",
    "select colour",
    "choose an option",
    "choose size",
    "choose color",
    "none",
    "n/a",
    "null",
    "undefined",
}

_KNOWN_COLOR_WORDS = {
    "black", "white", "blue", "red", "green", "grey", "gray", "silver", "gold",
    "pink", "purple", "yellow", "orange", "brown", "beige", "navy", "teal",
    "titanium", "graphite", "midnight", "starlight", "ultramarine", "coral",
    "ivory", "olive", "maroon", "burgundy", "charcoal", "bronze", "copper",
    "rose", "mint", "cream", "khaki", "indigo", "violet", "cyan", "magenta",
    "deep blue", "natural titanium", "desert titanium", "black titanium",
    "white titanium", "space black", "space grey", "space gray", "alpine green",
    "sierra blue", "pacific blue", "phantom black", "marble gray", "onyx black",
    "cobalt violet", "amber yellow", "jade green", "sapphire blue", "mist blue",
    "lavender", "sage", "peony", "obsidian", "porcelain", "hazel", "lemongrass",
    "cosmic orange", "arctic white", "matte black", "jet black", "rose gold",
}


def _make_unknown_attr(name: str, kind: AttrKind, source: Source = Source.DOM_SELECTED) -> Attr:
    """Create an explicit UNKNOWN attribute marker (empty raw/norm/num) when selection is indeterminate."""
    return Attr(
        name=name,
        kind=kind,
        raw="",
        norm=None,
        num=None,
        unit=None,
        evidence=Evidence(source=source, span=None, confidence=0.0),
    )


def _build_typed_attr(
    name: str,
    raw_val: str | None,
    source: Source,
    span: str | None = None,
    confidence: float | None = None,
) -> Attr | None:
    if raw_val is None:
        return None
    cleaned = " ".join(str(raw_val).strip().split())
    if not cleaned or cleaned.lower() in _UNSELECTED_PLACEHOLDERS:
        return None

    conf = confidence if confidence is not None else _SOURCE_CONFIDENCE.get(source, 0.85)
    ev = Evidence(source=source, span=span or cleaned, confidence=conf)
    key = name.strip().lower()

    if key in ("storage", "storage_gb", "rom", "ssd", "internal_storage"):
        # If raw_val is bare integer from storage_gb (e.g. "128" or "512"), append GB
        q_in = f"{cleaned}GB" if cleaned.isdigit() else cleaned
        num, unit, norm = normalize_quantity(q_in, "storage")
        if num is None:
            parsed = parse_ram_and_storage(q_in, evidence=ev)
            if "storage" in parsed:
                return parsed["storage"]
        return Attr(name="storage", kind=AttrKind.QUANTITY, raw=q_in, norm=norm, num=num, unit=unit, evidence=ev)

    if key in ("ram", "ram_gb"):
        q_in = f"{cleaned}GB" if cleaned.isdigit() else cleaned
        num, unit, norm = normalize_quantity(q_in, "ram")
        if num is None:
            parsed = parse_ram_and_storage(f"{q_in} RAM", evidence=ev)
            if "ram" in parsed:
                return parsed["ram"]
        return Attr(name="ram", kind=AttrKind.QUANTITY, raw=q_in, norm=norm, num=num, unit=unit, evidence=ev)

    if key in ("unit_size", "volume", "weight", "net_weight", "net_volume", "screen_size", "screen_size_inches"):
        q_in = f"{cleaned} inch" if (key.startswith("screen") and re.fullmatch(r"\d+(?:\.\d+)?", cleaned)) else cleaned
        num, unit, norm = normalize_quantity(q_in, key)
        attr_name = "screen_size" if key.startswith("screen") else "unit_size"
        return Attr(name=attr_name, kind=AttrKind.QUANTITY, raw=q_in, norm=norm, num=num, unit=unit, evidence=ev)

    if key in ("pack_qty", "pack_count", "pieces", "count", "net_quantity"):
        m = re.search(r"\b(\d+)\b", cleaned)
        num_val = Decimal(m.group(1)) if m else None
        norm_val = str(int(num_val)) if num_val is not None else None
        return Attr(name="pack_qty", kind=AttrKind.COUNT, raw=cleaned, norm=norm_val, num=num_val, unit="count", evidence=ev)

    if key in ("color", "colour"):
        canon, _ = normalize_color(cleaned)
        return Attr(name="color", kind=AttrKind.COLOR, raw=cleaned, norm=canon, num=None, unit=None, evidence=ev)

    if key == "size":
        sys_code, num_val, norm_val = normalize_size(cleaned)
        return Attr(name="size", kind=AttrKind.SIZE, raw=cleaned, norm=norm_val, num=num_val, unit=sys_code, evidence=ev)

    if key in ("condition", "connectivity", "edition", "region"):
        norm_val = normalize_condition(cleaned) if key == "condition" else cleaned.casefold()
        return Attr(name=key, kind=AttrKind.ENUM, raw=cleaned, norm=norm_val, num=None, unit=None, evidence=ev)

    if key in ("mpn", "model_number", "part_number", "asin", "fsn"):
        return Attr(name="mpn" if key != "asin" else "asin", kind=AttrKind.IDENT, raw=cleaned, norm=cleaned.upper(), num=None, unit=None, evidence=ev)

    if key in ("bundle", "in_the_box", "sales_package"):
        return Attr(name="bundle", kind=AttrKind.SET, raw=cleaned, norm=cleaned.casefold(), num=None, unit=None, evidence=ev)

    return None


def extract_selected_variant_from_html(
    html: str,
    url: str = "",
    retailer: str | None = None,
) -> tuple[dict[str, list[Attr]], set[str]]:
    """
    Inspect saved/live PDP HTML for explicitly selected variant attributes and unselected variation widgets.
    Returns `(attr_candidates_by_name, explicitly_unselected_attr_names)`.
    """
    candidates: dict[str, list[Attr]] = {}
    unselected_dimensions: set[str] = set()

    def _add(attr: Attr | None) -> None:
        if attr is not None:
            candidates.setdefault(attr.name, []).append(attr)

    if not html:
        return candidates, unselected_dimensions

    tree = LexborHTMLParser(html)

    # ── 1. URL Parameters (Source.URL_PARAM) ──
    if url:
        parsed_u = urlparse(url)
        qparams = parse_qs(parsed_u.query)
        for p_key, attr_name in (("color", "color"), ("colour", "color"), ("size", "size"), ("storage", "storage")):
            if p_key in qparams and qparams[p_key]:
                _add(_build_typed_attr(attr_name, qparams[p_key][0], Source.URL_PARAM, span=f"{p_key}={qparams[p_key][0]}"))

    # ── 2. Amazon Selected Twister & Product Overview (Source.DOM_SELECTED) ──
    az_color_selectors = [
        "#inline-twister-expanded-dimension-text-color_name",
        "#variation_color_name .selection",
        "tr.po-color td.a-span9 span",
        "tr.po-color td.a-span9",
    ]
    for sel in az_color_selectors:
        node = tree.css_first(sel)
        if node:
            val = node.text(strip=True)
            if val and val.lower() not in _UNSELECTED_PLACEHOLDERS:
                _add(_build_typed_attr("color", val, Source.DOM_SELECTED, span=val))
                break
            elif val and val.lower() in _UNSELECTED_PLACEHOLDERS:
                unselected_dimensions.add("color")

    az_size_selectors = [
        "#inline-twister-expanded-dimension-text-size_name",
        "#variation_size_name .selection",
        "select#native_dropdown_selected_size_name option[selected]",
        "tr.po-memory_storage_capacity td.a-span9 span",
        "tr.po-size td.a-span9 span",
    ]
    for sel in az_size_selectors:
        node = tree.css_first(sel)
        if node:
            val = node.text(strip=True)
            if val and val.lower() not in _UNSELECTED_PLACEHOLDERS:
                # Could be storage (e.g. "512 GB", "128GB", "8GB+256GB") or apparel/shoe size ("UK 9", "Medium", "XL")
                rs = parse_ram_and_storage(val, evidence=Evidence(source=Source.DOM_SELECTED, span=val, confidence=0.99))
                if rs:
                    for a in rs.values():
                        _add(a)
                elif re.search(r"\b\d+\s*(?:GB|TB)\b", val, re.I):
                    _add(_build_typed_attr("storage", val, Source.DOM_SELECTED, span=val))
                else:
                    _add(_build_typed_attr("size", val, Source.DOM_SELECTED, span=val))
                break
            elif val and val.lower() in _UNSELECTED_PLACEHOLDERS:
                unselected_dimensions.add("size")

    # Check if Amazon size dropdown exists with "Select" placeholder and no valid selection
    az_dropdown = tree.css_first("select#native_dropdown_selected_size_name, #dropdown_selected_size_name")
    if az_dropdown and "size" not in candidates and "storage" not in candidates:
        dd_txt = az_dropdown.text(strip=True).lower()
        if "select" in dd_txt:
            unselected_dimensions.add("size")

    # Amazon RAM row
    ram_node = tree.css_first("tr.po-ram_memory_installed_size td.a-span9 span, tr.po-ram_memory_installed_size td.a-span9")
    if ram_node:
        _add(_build_typed_attr("ram", ram_node.text(strip=True), Source.DOM_SELECTED))

    # ── 3. Flipkart Selected Swatches, Spec Tables & Hydration (Source.DOM_SELECTED / Source.HYDRATION) ──
    for swatch_node in tree.css("a._1fGeJ5._2UVyXR, li[id*='swatch-'] a._2UVyXR, [data-testid*='swatch'][aria-selected='true']"):
        val = swatch_node.text(strip=True)
        if not val:
            continue
        if re.search(r"\b\d+\s*(?:GB|TB)\b", val, re.I):
            _add(_build_typed_attr("storage", val, Source.DOM_SELECTED, span=val))
        elif normalize_size(val)[0] is not None:
            _add(_build_typed_attr("size", val, Source.DOM_SELECTED, span=val))
        else:
            _add(_build_typed_attr("color", val, Source.DOM_SELECTED, span=val))

    for row in tree.css("table._14cfVK tr, div._3Rrcbo tr, #productDetails_techSpec_section_1 tr"):
        cells = row.css("th, td")
        if len(cells) >= 2:
            k = cells[0].text(strip=True).lower()
            v = cells[1].text(strip=True)
            if not v:
                continue
            if k in ("color", "colour", "brand color"):
                _add(_build_typed_attr("color", v, Source.DOM_SELECTED, span=v))
            elif k in ("internal storage", "rom", "ssd capacity", "storage"):
                _add(_build_typed_attr("storage", v, Source.DOM_SELECTED, span=v))
            elif k in ("ram", "system memory"):
                _add(_build_typed_attr("ram", v, Source.DOM_SELECTED, span=v))
            elif k in ("model number", "part number", "item model number"):
                _add(_build_typed_attr("mpn", v, Source.DOM_SELECTED, span=v))
            elif k in ("net quantity", "pack of", "number of items"):
                _add(_build_typed_attr("pack_qty", v, Source.DOM_SELECTED, span=v))

    # Flipkart window.__INITIAL_STATE__ hydration subtitle (e.g. "Black, 128 GB")
    m_fk = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>", html, re.S)
    if m_fk:
        try:
            fk_state = json.loads(m_fk.group(1))
            pc = ((fk_state.get("multiWidgetState") or {}).get("pageDataResponse") or {}).get("pageContext") or {}
            titles = pc.get("titles") or {}
            for sub_key in ("subtitle", "coSubtitle"):
                sub_val = str(titles.get(sub_key) or "").strip()
                if sub_val:
                    for part in [p.strip() for p in sub_val.split(",") if p.strip()]:
                        if re.search(r"\b\d+\s*(?:GB|TB)\b", part, re.I):
                            _add(_build_typed_attr("storage", part, Source.HYDRATION, span=sub_val))
                        elif normalize_color(part)[0] is not None or part.lower() in _KNOWN_COLOR_WORDS:
                            _add(_build_typed_attr("color", part, Source.HYDRATION, span=sub_val))
        except Exception:
            pass

    # ── 4. Universal / Myntra / Ajio / Shopify Selected Swatch Buttons (Source.DOM_SELECTED) ──
    selected_size_btns = tree.css(
        ".size-buttons-size-button-selected, "
        "[data-testid*='size'][aria-selected='true'], "
        "[data-testid*='size'][aria-checked='true'], "
        ".swatch-element.selected[data-option='size'], "
        "button.selected[data-size]"
    )
    for btn in selected_size_btns:
        val = btn.attributes.get("data-size") or btn.text(strip=True)
        if val:
            _add(_build_typed_attr("size", val, Source.DOM_SELECTED, span=val))

    # If Myntra/apparel size buttons exist on page, but NONE has `.size-buttons-size-button-selected` / `aria-selected='true'`
    all_size_btns = tree.css(".size-buttons-size-button, [data-testid*='size-btn']")
    if all_size_btns and not selected_size_btns and "size" not in candidates:
        unselected_dimensions.add("size")

    selected_color_btns = tree.css(
        "[data-testid*='color'][aria-selected='true'], "
        "[data-testid*='color'][aria-checked='true'], "
        ".swatch-element.selected[data-option='color'], "
        ".colour-listItem.selected, "
        "[data-selected-color]"
    )
    for btn in selected_color_btns:
        val = btn.attributes.get("data-selected-color") or btn.attributes.get("title") or btn.text(strip=True)
        if val:
            _add(_build_typed_attr("color", val, Source.DOM_SELECTED, span=val))

    return candidates, unselected_dimensions


def _extract_title_attributes_and_core_model(
    title: str,
    brand: str | None,
) -> tuple[str, dict[str, Attr]]:
    """
    Deterministically parse attributes from `title` with `Source.TITLE_PARSE` and strip
    brand + variant attributes (color, storage, RAM, size, pack_qty, condition) to produce
    the clean `core_model` string.
    """
    ev = Evidence(source=Source.TITLE_PARSE, span=title, confidence=_SOURCE_CONFIDENCE[Source.TITLE_PARSE])
    attrs: dict[str, Attr] = {}
    if not title:
        return "", attrs

    # 1. Condition
    t_low = title.lower()
    if any(k in t_low for k in ("refurbished", "renewed", "reconditioned")):
        attrs["condition"] = Attr(name="condition", kind=AttrKind.ENUM, raw="Refurbished", norm="refurbished", evidence=ev)
    elif any(k in t_low for k in ("open box", "open-box", "unboxed")):
        attrs["condition"] = Attr(name="condition", kind=AttrKind.ENUM, raw="Open Box", norm="open_box", evidence=ev)

    # 2. RAM + Storage
    for k, a in parse_ram_and_storage(title, evidence=ev).items():
        attrs[k] = a

    # 3. Pack + Unit Size (for grocery/personal care/appliances/multipacks)
    for k, a in parse_pack_and_quantity(title, evidence=ev).items():
        if k == "unit_size" and ("storage" in attrs or "ram" in attrs):
            continue
        attrs[k] = a

    # 4. Parenthetical and comma/hyphen segments for Color & Size
    segments: list[str] = []
    for pm in re.finditer(r"[\(\[]([^\)\]]+)[\)\]]", title):
        for part in re.split(r"\s*[,;/|]\s*", pm.group(1)):
            if part.strip():
                segments.append(part.strip())

    no_parens = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", title)
    for part in re.split(r"\s+(?:[-–—]|\|)\s+|,\s+", no_parens)[1:]:
        if part.strip():
            segments.append(part.strip())

    detected_color_raw: str | None = None
    detected_size_raw: str | None = None

    for seg in segments:
        seg_clean = " ".join(seg.strip().split())
        seg_low = seg_clean.lower()
        # Check size first (e.g. "UK 9", "US 10", "Size M", "XL")
        if re.match(r"^(?:size\s*[-:]?\s*)?(?:(?:uk|us|eu|ind|cm)\s*[-:]?\s*\d{1,2}(?:\.\d)?|xxs|xs|small|medium|large|xl|xxl|2xl|3xl|4xl|free\s+size|[sml])$", seg_low, re.I):
            if "size" not in attrs:
                a_sz = _build_typed_attr("size", seg_clean, Source.TITLE_PARSE, span=seg_clean)
                if a_sz:
                    attrs["size"] = a_sz
                    detected_size_raw = seg_clean
            continue

        # Check color
        canon_c, _ = normalize_color(seg_clean)
        if canon_c is not None or seg_low in _KNOWN_COLOR_WORDS:
            if "color" not in attrs:
                a_col = _build_typed_attr("color", seg_clean, Source.TITLE_PARSE, span=seg_clean)
                if a_col:
                    attrs["color"] = a_col
                    detected_color_raw = seg_clean

    # Also scan full title for explicit "UK 9" / "US 10" or known colors if not inside parentheticals
    if "size" not in attrs:
        sz_m = re.search(r"\b((?:UK|US|EU|IND)\s*[-:]?\s*\d{1,2}(?:\.\d)?|Size\s+(?:XXS|XS|S|M|L|XL|XXL|2XL|3XL))\b", title, re.I)
        if sz_m:
            a_sz = _build_typed_attr("size", sz_m.group(1), Source.TITLE_PARSE, span=sz_m.group(1))
            if a_sz:
                attrs["size"] = a_sz
                detected_size_raw = sz_m.group(1)

    if "color" not in attrs:
        # Sort known color words longest-first so "Deep Blue" / "Natural Titanium" match before "Blue"
        for cw in sorted(_KNOWN_COLOR_WORDS, key=len, reverse=True):
            m_col = re.search(rf"\b({re.escape(cw)})\b", title, re.I)
            if m_col:
                a_col = _build_typed_attr("color", m_col.group(1), Source.TITLE_PARSE, span=m_col.group(1))
                if a_col:
                    attrs["color"] = a_col
                    detected_color_raw = m_col.group(1)
                    break

    # 5. Build clean `core_model` by stripping brand, parentheticals, colon/pipe descriptions, and variant tokens
    core = re.split(r"\s*(?:\:|(?:\s+\|\s+)|(?:\s+[-–—]\s+))\s*", no_parens)[0].strip()
    core = core.split(",")[0].strip()
    if brand:
        core = re.sub(rf"^\s*{re.escape(brand)}\b\s*", "", core, flags=re.I).strip()
    if detected_color_raw:
        core = re.sub(rf"\b{re.escape(detected_color_raw)}\b", " ", core, flags=re.I)
    if detected_size_raw:
        core = re.sub(rf"\b{re.escape(detected_size_raw)}\b", " ", core, flags=re.I)

    # Strip storage/RAM/quantity/pack expressions from core_model
    core = re.sub(r"\b\d+(?:\.\d+)?\s*(?:TB|GB|MB|kg|g|gm|ml|l|ltr)\b(?:\s*(?:RAM|ROM|Storage|SSD))?", " ", core, flags=re.I)
    core = re.sub(r"\b(?:pack|set|combo)\s+of\s+\d+\b", " ", core, flags=re.I)
    core = re.sub(r"\b(?:refurbished|renewed|open\s+box|unboxed)\b", " ", core, flags=re.I)
    core = " ".join(core.split()).strip(" -–—,/|:")

    if not core:
        core = " ".join((title or "").split()[:4])

    return core, attrs


def build_variant_spec(
    payload: ClientPayload,
    html: str | None = None,
    *,
    source_text_for_llm: str | None = None,
) -> VariantSpec:
    """
    Build a canonical `VariantSpec` from a `ClientPayload` (and optional raw `html`),
    enforcing evidence precedence (`DOM_SELECTED > URL_PARAM > JSONLD > HYDRATION > TITLE_PARSE > LLM_SPAN`)
    and emitting explicit `UNKNOWN` attributes whenever a selectable variant dimension on the page
    is unselected/indeterminate.
    """
    candidates: dict[str, list[Attr]] = {}
    unselected_dims: set[str] = set()

    def _push(attr: Attr | None) -> None:
        if attr is None:
            return
        if attr.evidence.source == Source.LLM_SPAN:
            verified = verify_llm_span(attr, source_text_for_llm or payload.title or "")
            if verified is None:
                return
        candidates.setdefault(attr.name, []).append(attr)

    # 1. Extract from raw HTML if available
    if html:
        html_cands, html_unselected = extract_selected_variant_from_html(html, payload.url, payload.retailer)
        for k, alist in html_cands.items():
            for a in alist:
                _push(a)
        unselected_dims.update(html_unselected)

    # 2. Extract from `payload.variant_specs` (including `selected_attrs` emitted by `dom_extractor.js`)
    vspecs = payload.variant_specs or {}
    sel_attrs = vspecs.get("selected_attrs")
    if isinstance(sel_attrs, dict):
        for k, meta in sel_attrs.items():
            if not isinstance(meta, dict):
                continue
            src_str = str(meta.get("source") or "dom_selected").lower()
            val = meta.get("value")
            span = meta.get("span")
            if src_str == "unknown" or val is None or str(val).strip().lower() in _UNSELECTED_PLACEHOLDERS:
                unselected_dims.add(k.lower())
            else:
                src_enum = _SOURCE_MAP.get(src_str, Source.DOM_SELECTED)
                _push(_build_typed_attr(k, str(val), src_enum, span=str(span or val)))

    for k, v in vspecs.items():
        if k in ("selected_attrs", "extraction_tier", "shopify_variant_id", "is_quick_commerce", "eta", "unit_price_inr", "unit_price_label"):
            continue
        if v is None or isinstance(v, (dict, list)):
            continue
        # Attributes in `variant_specs` come from DOM/spec table when extracted by scraper/extension
        _push(_build_typed_attr(k, str(v), Source.DOM_SELECTED))

    # 3. Title parse fallback (`Source.TITLE_PARSE`) & `core_model`
    core_model, title_attrs = _extract_title_attributes_and_core_model(payload.title or "", payload.brand)
    for k, a in title_attrs.items():
        _push(a)

    # 4. Resolve each attribute by highest evidence precedence
    final_attrs: dict[str, Attr] = {}
    for k, alist in candidates.items():
        if alist:
            final_attrs[k] = select_highest_precedence_attr(alist)

    # 5. If any dimension was explicitly detected as unselected on the page (and not resolved by DOM_SELECTED/URL_PARAM),
    # emit an explicit UNKNOWN attribute so downstream never guesses the parent default!
    for dim in unselected_dims:
        existing = final_attrs.get(dim)
        if existing is None or existing.evidence.source == Source.TITLE_PARSE:
            kind = AttrKind.COLOR if dim == "color" else (AttrKind.SIZE if dim == "size" else AttrKind.QUANTITY)
            final_attrs[dim] = _make_unknown_attr(dim, kind, source=Source.DOM_SELECTED)

    gtin = str(vspecs.get("gtin") or vspecs.get("gtin13") or vspecs.get("ean") or "").strip() or None
    mpn = str(vspecs.get("mpn") or vspecs.get("model_number") or "").strip() or None
    images = list(payload.images or ([] if not payload.image_url else [payload.image_url]))
    price_dec = Decimal(str(payload.listed_price)) if payload.listed_price and payload.listed_price > 0 else None

    return VariantSpec(
        url=payload.url or "",
        title=payload.title or "",
        brand=payload.brand,
        product_type=str(vspecs.get("product_type") or "").strip() or None,
        core_model=core_model,
        gtin=gtin,
        mpn=mpn,
        attrs=final_attrs,
        image_urls=images,
        listed_price=price_dec,
    )
