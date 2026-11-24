"""Phase 1 regression tests: LLM 4s timeout/health, Amazon block detection, Flipkart structured parse, matcher GIN %, mock mode, vault PDF."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas.extraction import BankOfferRule, ClientPayload, LiveOfferRaw
from app.scrapers.base_scraper import BaseScraper, ScraperBlockedError, is_block_page
from app.scrapers.flipkart import FlipkartScraper
from app.services.llm_parser import LLMParser, get_llm_status


def test_health_exposes_llm_status():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "llm" in data
    assert data["llm"] in ("ok", "no_key", "degraded")


@pytest.mark.asyncio
async def test_llm_parser_4s_timeout_and_fallback_when_no_key():
    orig_key = settings.openrouter_api_key
    try:
        settings.openrouter_api_key = ""
        parser = LLMParser(redis_client=None)
        assert get_llm_status() == "no_key"
        variant, offers, reviews = await asyncio.gather(
            parser.parse_variant_attributes("Apple iPhone 15 128GB Black", ""),
            parser.parse_bank_offers(["Flat ₹4,000 instant discount on HDFC Credit Card"]),
            parser.analyze_reviews(["Genuine sealed box"]),
        )
        assert variant.brand == "Apple"
        assert len(offers) >= 1 and offers[0].bank_name == "HDFC"
        assert reviews.trust_impact == 0
    finally:
        settings.openrouter_api_key = orig_key


def test_bank_offer_rule_coerces_null_floats():
    rule = BankOfferRule.model_validate({
        "bank_name": "HDFC Bank",
        "card_type": "credit",
        "min_txn_amount": 50000,
        "discount_flat": 4000,
        "discount_pct": None,
        "max_discount_cap": None,
        "excluded_cards": None,
        "summary": None,
    })
    assert rule.discount_pct == 0.0
    assert rule.max_discount_cap == 0.0
    assert rule.excluded_cards == []
    assert rule.summary == ""


def test_amazon_block_page_detection_and_large_flipkart_not_flagged():
    tiny_block = "<html><title>Robot Check</title><body>Enter the characters you see below</body></html>"
    assert is_block_page(tiny_block, "amazon_in", "https://www.amazon.in/dp/B0FQF5DG3P") is True

    # >20KB Amazon page without #productTitle is a soft block
    missing_title_amazon = "<html><body>" + ("x" * 25000) + "</body></html>"
    assert is_block_page(missing_title_amazon, "amazon_in", "https://www.amazon.in/dp/B0FQF5DG3P") is True

    # >20KB Amazon page WITH #productTitle is valid
    valid_amazon = '<html><body><span id="productTitle">iPhone 15</span>' + ("x" * 25000) + "</body></html>"
    assert is_block_page(valid_amazon, "amazon_in", "https://www.amazon.in/dp/B0FQF5DG3P") is False

    # >120KB Flipkart JS bundle containing "captcha" string must NOT be flagged as block
    large_flipkart = "<html><body><script>var captchaEnabled=false;</script>" + ("x" * 130000) + "</body></html>"
    assert is_block_page(large_flipkart, "flipkart", "https://www.flipkart.com/x/p/itm123?pid=MOB123") is False


@pytest.mark.asyncio
async def test_never_cache_block_page_and_rotate_profiles():
    class DummyScraper(BaseScraper):
        retailer = "amazon_in"

        async def extract_product(self, url: str):
            html = await self.fetch_page(url, auto_cache=False)
            return html

        async def search_products(self, query: str):
            return []

    scraper = DummyScraper()
    seen_profiles = []

    async def fake_curl(url: str, impersonate: str = "chrome", force_fresh_session: bool = False):
        seen_profiles.append(impersonate)
        return "<html><title>Captcha</title>Enter the characters you see below</html>"

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock()

    with (
        patch("app.scrapers.base_scraper.get_redis", return_value=fake_redis),
        patch("app.scrapers.base_scraper.is_circuit_open", new=AsyncMock(return_value=(False, ""))),
        patch("app.scrapers.base_scraper.trip_circuit_breaker", new=AsyncMock()),
        patch("app.scrapers.base_scraper.acquire_domain_rate_limit", new=AsyncMock()),
        patch("app.scrapers.base_scraper._async_jitter", new=AsyncMock()),
        patch.object(scraper, "_fetch_curl_cffi", side_effect=fake_curl),
        patch.object(scraper, "_fetch_playwright", side_effect=ScraperBlockedError("u", 200)),
    ):
        with pytest.raises(ScraperBlockedError):
            await scraper.fetch_page("https://www.amazon.in/dp/B0FQF5DG3P", auto_cache=True)

    assert len(seen_profiles) == 3
    assert len(set(seen_profiles)) == 3  # rotated 3 distinct profiles
    fake_redis.set.assert_not_called()  # block page never cached


def test_flipkart_structured_json_avoids_become_a_seller_and_extracts_mrp_images():
    state = {
        "multiWidgetState": {
            "pageDataResponse": {
                "pageContext": {
                    "productId": "MOBTEST123",
                    "isFaAvailable": True,
                    "fdpEventTracking": {
                        "events": {"psi": {"ppd": {"finalPrice": 69999, "mrp": 74900}}}
                    },
                }
            },
            "widgetsData": {
                "slots": [
                    {
                        "slotData": {
                            "widget": {
                                "viewType": "pp_delivery_widget_v2",
                                "data": {
                                    "dlsData": {
                                        "default_fk_pp_delivery_widget_seller_v2_0": {
                                            "value": {
                                                "label_0": {"value": {"text": "Seller: TrueComRetail"}},
                                                "label_1": {"value": {"text": "4.8"}},
                                            }
                                        }
                                    }
                                },
                            }
                        }
                    }
                ]
            },
        }
    }
    jsonld = {
        "@type": "Product",
        "name": "Apple iPhone 15 (Black, 128 GB)",
        "brand": {"name": "APPLE"},
        "image": ["https://rukmini1.flixcart.com/img1.jpeg", "https://rukmini1.flixcart.com/img2.jpeg"],
        "offers": {"price": 69999, "availability": "https://schema.org/InStock"},
    }
    html = (
        f'<html><head><script type="application/ld+json">{json.dumps(jsonld)}</script></head>'
        f'<body><a href="/sell-online">Become a Seller</a>'
        f'<script>window.__INITIAL_STATE__ = {json.dumps(state)};</script></body></html>'
    )
    fk = FlipkartScraper()
    payload = fk._parse_product_page(html, "https://www.flipkart.com/apple-iphone-15/p/itm123?pid=MOBTEST123")
    assert payload.seller_name == "TrueComRetail"
    assert payload.seller_rating == 4.8
    assert payload.mrp == 74900.0
    assert payload.listed_price == 69999.0
    assert len(payload.images) == 2
    assert len(payload.sellers) == 1 and payload.sellers[0].seller_name == "TrueComRetail"


@pytest.mark.asyncio
async def test_scraper_mode_mock_serves_fixture(tmp_path):
    class MockableScraper(BaseScraper):
        retailer = "test_mock"

        async def extract_product(self, url: str):
            return await self.fetch_page(url)

        async def search_products(self, query: str):
            return []

    fixture = tmp_path / "test_mock_product.html"
    fixture.write_text("<html><body><h1>Mock Product</h1></body></html>", encoding="utf-8")

    orig_mode = settings.scraper_mode
    try:
        settings.scraper_mode = "mock"
        with patch("app.scrapers.base_scraper._FIXTURES_DIR", tmp_path):
            s = MockableScraper()
            html = await s.fetch_page("https://example.com/p/123")
            assert "Mock Product" in html
    finally:
        settings.scraper_mode = orig_mode


@pytest.mark.asyncio
async def test_product_matcher_uses_pg_trgm_percent_operator():
    from app.services.product_matcher import match_or_create

    executed_sqls = []
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.mappings.return_value = []

    session = AsyncMock()

    async def capture_execute(stmt, *args, **kwargs):
        executed_sqls.append(str(stmt))
        return mock_result

    session.execute = AsyncMock(side_effect=capture_execute)
    session.add = MagicMock()
    session.flush = AsyncMock()

    payload = ClientPayload(
        retailer="amazon_in",
        url="https://www.amazon.in/dp/B0TEST1234",
        title="Test Phone X1 128GB",
        brand="TestBrand",
        sku="B0TEST1234",
        listed_price=29999.0,
    )
    await match_or_create(session, payload, None)
    joined = "\n".join(executed_sqls)
    assert "SET LOCAL pg_trgm.similarity_threshold" in joined
    assert "model_name % :q" in joined


def test_routes_imports_live_offer_raw():
    from app.api.v1 import routes

    assert hasattr(routes, "LiveOfferRaw")


@pytest.mark.asyncio
async def test_vault_service_uses_bytesio_not_open_from_bytes():
    from app.services.vault_service import process_invoice

    fake_page = MagicMock()
    fake_page.extract_text.return_value = "Product: Test Laptop\nTotal: 55000\nInvoice No: INV-999\n2026-01-15"
    fake_pdf = MagicMock()
    fake_pdf.pages = [fake_page]
    fake_pdf.__enter__ = MagicMock(return_value=fake_pdf)
    fake_pdf.__exit__ = MagicMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    orig_key = settings.openrouter_api_key
    settings.openrouter_api_key = ""
    try:
        mock_parser = LLMParser(redis_client=None)
        with (
            patch("pdfplumber.open", return_value=fake_pdf) as mock_open,
            patch("app.services.vault_service.get_llm_parser", return_value=mock_parser),
        ):
            item = await process_invoice(mock_session, b"%PDF-1.4 dummy", "user@example.com")
            mock_open.assert_called_once()
            assert item.product_name == "Test Laptop"
            assert item.price_paid == 55000.0
    finally:
        settings.openrouter_api_key = orig_key
