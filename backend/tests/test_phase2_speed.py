"""Phase 2 optimization tests: fast redirect resolution, <100ms Flipkart Lexbor parse,
30-min ClientPayload cache, Redis rate limit & circuit breaker, proxy config, and 1h PriceObservation dedupe.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.routes import _run_analysis_pipeline
from app.config import settings
from app.models.canonical_product import CanonicalProduct
from app.models.price_observation import PriceObservation
from app.models.retailer_listing import RetailerListing
from app.schemas.extraction import ClientPayload, ExecutiveSummary
from app.services.buy_wait_engine import BuyWaitVerdict
from app.scrapers.base_scraper import BaseScraper, _proxy_dict, resolve_url_redirects
from app.scrapers.flipkart import FlipkartScraper
from app.services import rate_guard


@pytest.mark.asyncio
async def test_resolve_url_redirects_skips_canonical_and_resolves_shortlinks():
    with patch("httpx.AsyncClient") as mock_client_cls:
        u_az = "https://www.amazon.in/dp/B0FQF5DG3P"
        u_fk = "https://www.flipkart.com/apple-iphone-15/p/itm3234701b0863a?pid=MOBHQV9YR4CBTD9D"
        u_other = "https://www.croma.com/apple-iphone-15/p/300684"

        assert await resolve_url_redirects(u_az) == u_az
        assert await resolve_url_redirects(u_fk) == u_fk
        assert await resolve_url_redirects(u_other) == u_other
        mock_client_cls.assert_not_called()

        # Shortener link SHOULD perform fast HEAD resolution
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://www.amazon.in/dp/B0FQF5DG3P?ref=short"
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resolved = await resolve_url_redirects("https://amzn.in/d/abc123")
        assert resolved == "https://www.amazon.in/dp/B0FQF5DG3P?ref=short"
        mock_client.head.assert_called_once()


def test_flipkart_lexbor_parse_speed_under_100ms():
    state = {
        "multiWidgetState": {
            "pageDataResponse": {
                "pageContext": {
                    "productId": "MOBSPEED1",
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
        "image": ["https://rukmini1.flixcart.com/img1.jpeg"],
        "offers": {"price": 69999, "availability": "https://schema.org/InStock"},
    }
    # Pad DOM to >1MB to simulate heavy live Flipkart HTML
    filler = "<div class='noise'><span>padding</span></div>" * 25000
    html = (
        f'<html><head><script type="application/ld+json">{json.dumps(jsonld)}</script></head>'
        f"<body>{filler}"
        f"<script>window.__INITIAL_STATE__ = {json.dumps(state)};</script></body></html>"
    )
    assert len(html.encode("utf-8")) > 1_000_000

    fk = FlipkartScraper()
    t0 = time.perf_counter()
    payload = fk._parse_product_page(html, "https://www.flipkart.com/apple-iphone-15/p/itm123?pid=MOBSPEED1")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 100.0, f"Flipkart parse took {elapsed_ms:.1f}ms (>= 100ms)"
    assert payload.seller_name == "TrueComRetail"
    assert payload.mrp == 74900.0
    assert payload.listed_price == 69999.0


@pytest.mark.asyncio
async def test_payload_cache_30min_roundtrip():
    class DummyScraper(BaseScraper):
        retailer = "flipkart"

        async def extract_product(self, url: str):
            raise NotImplementedError

        async def search_products(self, query: str):
            return []

    store: dict[str, str] = {}
    recorded_ex: list[int | None] = []

    fake_redis = AsyncMock()

    async def fake_set(key: str, val: str, ex: int | None = None):
        store[key] = val
        recorded_ex.append(ex)

    async def fake_get(key: str):
        return store.get(key)

    fake_redis.set = AsyncMock(side_effect=fake_set)
    fake_redis.get = AsyncMock(side_effect=fake_get)

    scraper = DummyScraper()
    payload = ClientPayload(
        retailer="flipkart",
        url="https://www.flipkart.com/apple-iphone-15/p/itm123?pid=MOB123",
        sku="MOB123",
        title="Apple iPhone 15 (Black, 128 GB)",
        brand="Apple",
        listed_price=69999.0,
        mrp=74900.0,
        in_stock=True,
        seller_name="TrueComRetail",
        images=["https://rukmini1.flixcart.com/img1.jpeg"],
    )

    with patch("app.scrapers.base_scraper.get_redis", return_value=fake_redis):
        await scraper.cache_payload(payload.url, payload)
        assert any(k.startswith("parsed_payload:") for k in store)
        assert recorded_ex == [1800]

        cached = await scraper.get_cached_payload(payload.url)
        assert cached is not None
        assert cached.title == payload.title
        assert cached.listed_price == 69999.0
        assert cached.seller_name == "TrueComRetail"
        assert cached.images == ["https://rukmini1.flixcart.com/img1.jpeg"]


@pytest.mark.asyncio
async def test_rate_guard_redis_rate_limit_and_circuit_breaker():
    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(return_value=True)
    fake_redis.get = AsyncMock(return_value="HTTP 503")

    try:
        await rate_guard.acquire_domain_rate_limit(
            "example.com", interval_seconds=2.0, redis_client=fake_redis
        )
        fake_redis.set.assert_called_once_with("rate_limit:example.com", "1", px=2000, nx=True)

        await rate_guard.trip_circuit_breaker(
            "example.com", reason="HTTP 503", ttl_seconds=1800, redis_client=fake_redis
        )
        fake_redis.set.assert_any_call("cb:example.com", "HTTP 503", ex=1800)

        opened, reason = await rate_guard.is_circuit_open("example.com", redis_client=fake_redis)
        assert opened is True
        assert "HTTP 503" in reason
    finally:
        rate_guard._MEMORY_CB.pop("example.com", None)


def test_proxy_dict_config_injection():
    orig = settings.scraper_proxy_url
    try:
        settings.scraper_proxy_url = ""
        assert _proxy_dict() is None

        settings.scraper_proxy_url = "http://user:pass@proxy.local:8080"
        assert _proxy_dict() == {
            "http": "http://user:pass@proxy.local:8080",
            "https": "http://user:pass@proxy.local:8080",
        }
    finally:
        settings.scraper_proxy_url = orig


@pytest.mark.asyncio
async def test_price_observation_1h_dedupe_and_pipeline_query_budget():
    prod = CanonicalProduct(
        id=uuid.uuid4(),
        brand="Apple",
        model_name="iPhone 15",
        category="smartphones",
        variant_specs={"storage_gb": 128},
    )
    listing = RetailerListing(
        id=uuid.uuid4(),
        canonical_product_id=prod.id,
        retailer="amazon_in",
        external_sku="B0DEDUPE01",
        listing_url="https://www.amazon.in/dp/B0DEDUPE01",
        current_listed_price=69900.0,
        in_stock=True,
        seller_name="Appario",
        trust_score=90,
    )
    payload = ClientPayload(
        retailer="amazon_in",
        url=listing.listing_url,
        sku="B0DEDUPE01",
        title="Apple iPhone 15 128GB",
        brand="Apple",
        listed_price=69900.0,
        mrp=79900.0,
        in_stock=True,
        seller_name="Appario",
    )

    mock_llm = AsyncMock()
    mock_llm.parse_variant_attributes = AsyncMock(return_value=None)
    mock_llm.parse_bank_offers = AsyncMock(return_value=[])
    mock_llm.analyze_reviews = AsyncMock(return_value=None)
    mock_llm.generate_executive_summary = AsyncMock(
        return_value=ExecutiveSummary(
            best_store="amazon_in",
            best_seller="Appario",
            best_offer_stack="None",
            final_net_price=69900.0,
            why_this_wins="Lowest price",
            key_caveat="None",
            summary_bullets=["Buy now"],
        )
    )
    verdict = BuyWaitVerdict(
        verdict="BUY_NOW",
        current_price=69900.0,
        p10=68000.0,
        median_90d=71000.0,
        all_time_low=67000.0,
        observation_count=5,
        next_sale_name="None",
        days_to_next_sale=30,
        reasoning="Near low",
    )

    def _make_session(last_obs):
        added = []
        session = AsyncMock()
        res = MagicMock()
        res.first.return_value = last_obs
        res.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=res)
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        session.commit = AsyncMock()
        return session, added

    # 1. Identical price + stock within 1h -> skips PriceObservation insert
    s_dedupe, added_dedupe = _make_session(
        (69900.0, True, datetime.now(timezone.utc) - timedelta(minutes=15))
    )
    # 2. Price changed -> inserts new PriceObservation
    s_changed, added_changed = _make_session(
        (65000.0, True, datetime.now(timezone.utc) - timedelta(minutes=15))
    )

    with (
        patch("app.api.v1.routes.get_llm_parser", new=AsyncMock(return_value=mock_llm)),
        patch("app.api.v1.routes.match_or_create", new=AsyncMock(return_value=(prod, listing, 1.0))),
        patch("app.api.v1.routes.get_buy_wait_verdict", new=AsyncMock(return_value=verdict)),
    ):
        await _run_analysis_pipeline(payload, s_dedupe)
        assert not any(isinstance(o, PriceObservation) for o in added_dedupe)
        assert s_dedupe.execute.await_count <= 4

        await _run_analysis_pipeline(payload, s_changed)
        assert any(isinstance(o, PriceObservation) for o in added_changed)
        assert s_changed.execute.await_count <= 4
