"""Offline parser regression tests against captured live Amazon.in and Flipkart HTML fixtures."""
from __future__ import annotations

from pathlib import Path

from app.scrapers.amazon_in import AmazonInScraper
from app.scrapers.base_scraper import is_block_page
from app.scrapers.flipkart import FlipkartScraper

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

AZ_URL = "https://www.amazon.in/dp/B0FQF5DG3P"
FK_URL = "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm3234701b0863a?pid=MOBHQV9YR4CBTD9D"


def test_amazon_product_fixture_offline():
    html = (FIXTURES_DIR / "amazon_product.html").read_text(encoding="utf-8", errors="replace")
    assert is_block_page(html, "amazon_in", AZ_URL) is False

    payload = AmazonInScraper()._parse_product_page(html, AZ_URL)
    assert "iPhone 17 Pro" in payload.title
    assert payload.listed_price == 150900.0
    assert payload.mrp == 154900.0
    assert payload.seller_name == "Clicktech Retail Private Ltd"
    assert payload.in_stock is True
    assert len(payload.images) >= 1


def test_amazon_block_fixture_detected_offline():
    html = (FIXTURES_DIR / "amazon_block.html").read_text(encoding="utf-8", errors="replace")
    assert len(html) < 20_000
    assert is_block_page(html, "amazon_in", AZ_URL) is True


def test_flipkart_product_fixture_offline():
    html = (FIXTURES_DIR / "flipkart_product.html").read_text(encoding="utf-8", errors="replace")
    assert is_block_page(html, "flipkart", FK_URL) is False

    payload = FlipkartScraper()._parse_product_page(html, FK_URL)
    assert payload.title == "Apple iPhone 15 (Black, 128 GB)"
    assert payload.listed_price == 69999.0
    assert payload.mrp == 74900.0
    assert payload.seller_name is not None
    assert "become a seller" not in payload.seller_name.lower()
    assert payload.seller_name == "TrueComRetail"
    assert payload.in_stock is True
    assert len(payload.images) >= 1
