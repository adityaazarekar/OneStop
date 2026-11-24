"""
M2 Unit Tests: Source-side selected-variant capture across saved retailer fixtures.

Gate verification:
- On saved pages per retailer (Amazon.in, Flipkart, Myntra, Shopify), selected color/size/storage
  are extracted 100% with `Source.DOM_SELECTED` / `Source.URL_PARAM` / `Source.HYDRATION`.
- When a page has unselected variant options (e.g. Amazon "Select Size" dropdown, Myntra unselected
  size buttons, or Shopify multi-variant JSON without `?variant=<id>`), color/size are explicitly
  UNKNOWN so `comparator.decide` returns `Bucket.UNVERIFIED` and never assumes a parent default.
- `shared/dom_extractor.js` and `extension/dom_extractor.js` are byte-identical.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.matching.comparator import decide
from app.matching.contracts import Attr, AttrKind, Bucket, Evidence, Source, VariantSpec, Verdict
from app.matching.spec_builder import build_variant_spec
from app.schemas.extraction import ClientPayload
from app.scrapers.amazon_in import AmazonInScraper
from app.scrapers.flipkart import FlipkartScraper
from app.services.universal_scraper import parse_shopify_product_json

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
VARIANTS_DIR = FIXTURES_DIR / "variants"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_shared_and_extension_dom_extractor_byte_identical():
    shared_bytes = (REPO_ROOT / "shared" / "dom_extractor.js").read_bytes()
    ext_bytes = (REPO_ROOT / "extension" / "dom_extractor.js").read_bytes()
    assert shared_bytes == ext_bytes, "shared/dom_extractor.js and extension/dom_extractor.js must be byte-identical"


def test_amazon_saved_fixture_selected_twister_capture():
    """Real saved Amazon.in PDP (iPhone 17 Pro 512 GB Deep Blue): twister captures Deep Blue & 512 GB with DOM_SELECTED."""
    url = "https://www.amazon.in/dp/B0FQF5DG3P"
    html = (FIXTURES_DIR / "amazon_product.html").read_text(encoding="utf-8", errors="replace")
    payload = AmazonInScraper()._parse_product_page(html, url)

    assert payload.variant_specs.get("color") == "Deep Blue"
    assert payload.variant_specs.get("storage_gb") == 512

    spec = build_variant_spec(payload, html=html)
    assert "color" in spec.attrs
    assert spec.attrs["color"].raw == "Deep Blue"
    assert spec.attrs["color"].evidence.source == Source.DOM_SELECTED

    assert "storage" in spec.attrs
    assert spec.attrs["storage"].norm == "512 GB"
    assert spec.attrs["storage"].evidence.source == Source.DOM_SELECTED


def test_flipkart_saved_fixture_selected_variant_capture():
    """Real saved Flipkart PDP (Apple iPhone 15 Black, 128 GB): captures Black & 128 GB."""
    url = "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm3234701b0863a?pid=MOBHQV9YR4CBTD9D"
    html = (FIXTURES_DIR / "flipkart_product.html").read_text(encoding="utf-8", errors="replace")
    payload = FlipkartScraper()._parse_product_page(html, url)

    assert payload.variant_specs.get("color") == "Black"
    assert payload.variant_specs.get("storage_gb") == 128

    spec = build_variant_spec(payload, html=html)
    assert spec.attrs["color"].norm == "black"
    assert spec.attrs["storage"].norm == "128 GB"


def test_amazon_unselected_size_dropdown_emits_unknown_never_parent_default():
    """When Amazon size dropdown is at 'Select Size', size is explicitly UNKNOWN so comparator returns UNVERIFIED."""
    url = "https://www.amazon.in/dp/B09NIKEPEG40"
    html = (VARIANTS_DIR / "amazon_unselected_size.html").read_text(encoding="utf-8")
    payload = AmazonInScraper()._parse_product_page(html, url)
    spec = build_variant_spec(payload, html=html)

    assert spec.attrs["color"].norm == "black"
    assert spec.attrs["color"].evidence.source == Source.DOM_SELECTED
    # Size must be present as an empty/UNKNOWN attribute so downstream never guesses UK 8
    assert "size" in spec.attrs
    assert spec.attrs["size"].norm is None
    assert spec.attrs["size"].num is None
    assert spec.attrs["size"].raw == ""

    cand_uk8 = VariantSpec(
        url="https://other.in/pegasus-40",
        title="Nike Air Zoom Pegasus 40 Black UK 8",
        brand="Nike",
        core_model=spec.core_model,
        attrs={
            "color": Attr(
                name="color",
                kind=AttrKind.COLOR,
                raw="Black",
                norm="black",
                evidence=Evidence(source=Source.DOM_SELECTED, span="Black", confidence=0.99),
            ),
            "size": Attr(
                name="size",
                kind=AttrKind.SIZE,
                raw="UK 8",
                norm="UK:8",
                 unit="UK",
                evidence=Evidence(source=Source.DOM_SELECTED, span="UK 8", confidence=0.99),
            ),
        },
    )
    decision = decide(spec, cand_uk8)
    assert decision.bucket == Bucket.UNVERIFIED
    assert any(d.name == "size" and d.verdict == Verdict.UNKNOWN for d in decision.diffs)


def test_myntra_selected_vs_unselected_size_buttons():
    """Myntra selected size button 'M' extracts ALPHA:M with DOM_SELECTED; unselected buttons emit UNKNOWN."""
    html_sel = (VARIANTS_DIR / "myntra_selected_m.html").read_text(encoding="utf-8")
    p_sel = ClientPayload(
        retailer="myntra",
        url="https://www.myntra.com/tshirts/levis/12345/buy",
        title="Levi's Men Navy Blue 511 Slim Crew T-Shirt",
        brand="Levi's",
        sku="12345",
        listed_price=1199.0,
        in_stock=True,
    )
    spec_sel = build_variant_spec(p_sel, html=html_sel)
    assert spec_sel.attrs["size"].norm == "ALPHA:M"
    assert spec_sel.attrs["size"].evidence.source == Source.DOM_SELECTED
    assert spec_sel.attrs["color"].norm == "navy"
    assert spec_sel.attrs["color"].evidence.source == Source.DOM_SELECTED

    html_unsel = (VARIANTS_DIR / "myntra_unselected_size.html").read_text(encoding="utf-8")
    spec_unsel = build_variant_spec(p_sel, html=html_unsel)
    assert "size" in spec_unsel.attrs
    assert spec_unsel.attrs["size"].norm is None
    assert spec_unsel.attrs["size"].raw == ""

    # Comparing selected M against unselected candidate must be UNVERIFIED, never EXACT
    decision = decide(spec_sel, spec_unsel)
    assert decision.bucket == Bucket.UNVERIFIED


def test_shopify_multi_variant_url_param_selection_vs_unselected_parent():
    """
    Shopify multi-variant JSON:
    - URL with `?variant=402` selects White / L at ₹9,499 (never hijacked by cheaper ₹8,499 Black / M).
    - URL without `?variant=` marks color and size as UNKNOWN so it never falsely matches Black / M as EXACT.
    """
    data = json.loads((VARIANTS_DIR / "shopify_multi_variant.json").read_text(encoding="utf-8"))

    # 1. Explicit ?variant=402 in URL
    p_402 = parse_shopify_product_json(
        data,
        "https://keychron.in/products/keychron-k2-wireless-mechanical-keyboard?variant=402",
    )
    assert p_402 is not None
    assert p_402.listed_price == 9499.0
    assert p_402.sku == "K2-WHT-L"
    spec_402 = build_variant_spec(p_402)
    assert spec_402.attrs["color"].norm == "white"
    assert spec_402.attrs["color"].evidence.source == Source.URL_PARAM
    assert spec_402.attrs["size"].norm == "ALPHA:L"
    assert spec_402.attrs["size"].evidence.source == Source.URL_PARAM

    # 2. Unselected parent URL (no ?variant=)
    p_parent = parse_shopify_product_json(
        data,
        "https://keychron.in/products/keychron-k2-wireless-mechanical-keyboard",
    )
    assert p_parent is not None
    spec_parent = build_variant_spec(p_parent)
    assert spec_parent.attrs["color"].norm is None
    assert spec_parent.attrs["size"].norm is None

    # Comparing specific variant (White / L) against unselected parent URL must be UNVERIFIED, never EXACT
    dec = decide(spec_402, spec_parent)
    assert dec.bucket == Bucket.UNVERIFIED
