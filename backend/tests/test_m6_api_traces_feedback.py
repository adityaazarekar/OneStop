"""
M6 Integration & Latency Tests:
- Database migration `004_matching_tables` (`attribute_aliases`, `serp_cache`, `decision_traces`, `review_queue`)
- `POST /api/v1/matching/stage1` (<500ms warm p95, no LLM, `decision_traces` persistence, Winner Guardrail:
  only `EXACT` + `in_stock` + `listed_price > 0` can win `lowest_verified_price`)
- `POST /api/v1/matching/feedback` ("Wrong variant?" feedback endpoint -> `review_queue` table)
- `GET /api/v1/matching/traces`
"""
from __future__ import annotations

import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import engine
from app.main import app


@pytest.mark.asyncio
async def test_m6_stage1_matching_decision_traces_and_feedback_endpoints():
    engine.sync_engine.pool.dispose()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        req_body = {
            "payload": {
                "retailer": "amazon_in",
                "url": "https://www.amazon.in/dp/B0CHX1W1XY",
                "title": "Apple iPhone 15 (Black, 128 GB)",
                "brand": "Apple",
                "sku": "B0CHX1W1XY",
                "listed_price": 69999.0,
                "in_stock": True,
                "variant_specs": {
                    "selected_attrs": {
                        "color": {"value": "Black", "source": "dom_selected", "span": "Black"},
                        "storage": {"value": "128 GB", "source": "dom_selected", "span": "128 GB"},
                    }
                },
            },
            "candidates": [
                # 1. Cheaper but WRONG color (Blue vs Black) -> VARIANT, MUST NOT win lowest_verified_price!
                {
                    "retailer": "flipkart",
                    "url": "https://www.flipkart.com/apple-iphone-15-blue-128-gb/p/itm1",
                    "title": "Apple iPhone 15 (Blue, 128 GB)",
                    "brand": "Apple",
                    "sku": "MOBBLUE128",
                    "listed_price": 64999.0,
                    "in_stock": True,
                },
                # 2. Cheaper but UNVERIFIED color (missing color) -> UNVERIFIED, MUST NOT win lowest_verified_price!
                {
                    "retailer": "vijaysales",
                    "url": "https://www.vijaysales.com/apple-iphone-15-128-gb/p/2",
                    "title": "Apple iPhone 15 128 GB Smartphone",
                    "brand": "Apple",
                    "sku": "VS128",
                    "listed_price": 65999.0,
                    "in_stock": True,
                },
                # 3. Exact variant match (Black, 128 GB) in stock -> EXACT, wins lowest_verified_price (67990)!
                {
                    "retailer": "croma",
                    "url": "https://www.croma.com/apple-iphone-15-black-128-gb/p/3",
                    "title": "Apple iPhone 15 (Black, 128 GB)",
                    "brand": "Apple",
                    "sku": "CR128BLK",
                    "listed_price": 67990.0,
                    "in_stock": True,
                },
                # 4. Exact variant match but OUT OF STOCK -> MUST NOT win lowest_verified_price!
                {
                    "retailer": "reliance_digital",
                    "url": "https://www.reliancedigital.in/apple-iphone-15-black-128-gb/p/4",
                    "title": "Apple iPhone 15 (Black, 128 GB)",
                    "brand": "Apple",
                    "sku": "RD128BLK",
                    "listed_price": 61999.0,
                    "in_stock": False,
                },
            ],
        }

        # Warm p95 latency benchmark across 5 warm calls (< 500ms gate)
        latencies: list[float] = []
        last_data = None
        for _ in range(5):
            t0 = time.perf_counter()
            resp = await client.post("/api/v1/matching/stage1", json=req_body)
            latencies.append((time.perf_counter() - t0) * 1000.0)
            assert resp.status_code == 200
            last_data = resp.json()

        lat_sorted = sorted(latencies)
        p95_warm_ms = lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95))]
        assert p95_warm_ms < 500.0, f"Stage-1 warm p95 {p95_warm_ms:.1f}ms breached 500ms floor"

        assert last_data is not None
        decs = {d["retailer"]: d for d in last_data["decisions"]}
        assert decs["flipkart"]["bucket"] == "variant_mismatch"
        assert any("color" in b for b in decs["flipkart"]["variant_diff_badges"])
        assert decs["vijaysales"]["bucket"] == "unverified"
        assert any("couldn't confirm: color" in b for b in decs["vijaysales"]["variant_diff_badges"])
        assert decs["croma"]["bucket"] == "exact_match"
        assert decs["reliance_digital"]["bucket"] == "exact_match"

        # Winner Guardrail: Only EXACT + in-stock (Croma at 67990) wins lowest_verified_price
        assert last_data["lowest_verified_price"] == 67990.0
        assert last_data["lowest_verified_store"] == "croma"

        # Verify decision_traces endpoint returns persisted traces
        trace_id = decs["flipkart"]["trace_id"]
        assert trace_id is not None
        traces_resp = await client.get("/api/v1/matching/traces?limit=5")
        assert traces_resp.status_code == 200
        traces_list = traces_resp.json()
        assert len(traces_list) >= 4

        # Submit "Wrong variant?" feedback -> review_queue table
        fb_resp = await client.post(
            "/api/v1/matching/feedback",
            json={
                "decision_trace_id": trace_id,
                "source_url": req_body["payload"]["url"],
                "cand_url": req_body["candidates"][0]["url"],
                "reported_issue": "Color mismatch (Blue shown next to Black)",
                "source_spec_json": last_data["source_spec"],
            },
        )
        assert fb_resp.status_code == 200
        fb_data = fb_resp.json()
        assert fb_data["status"] == "pending"
        assert uuid.UUID(fb_data["review_queue_id"])
