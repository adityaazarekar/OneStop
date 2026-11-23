"""
Stage 1 ARQ Async Worker + Zero-Friction In-Process Fallback + Real-Time SSE Event Bus.

Implements:
- `WorkerSettings` for `arq` connected to `settings.redis_url`:
    1. `run_universal_store_discovery(ctx, job_id, product_payload, location)`
    2. `run_quick_commerce_compare(ctx, job_id, product_payload, location)`
    3. `audit_issuer_terms_job(ctx, card_or_route_key, merchant_domain)`
- Progressive SSE streaming bus (`publish_sse_event`, `stream_job_events`):
  Publishes each resolved store, quick-commerce quote, and completion summary over both
  Redis Pub/Sub (`sse:job:{job_id}`) and an in-memory `asyncio.Queue` event buffer so
  `GET /api/v1/stream-compare/{job_id}` streams events in real time with zero timeouts
  even when only `uvicorn` is running without a separate `arq` worker process.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import redis.asyncio as aioredis
from arq.connections import RedisSettings

from app.config import settings
from app.schemas.extraction import ClientPayload, CompetitorQuote, QuickCommerceQuote

logger = logging.getLogger(__name__)

# In-process event replay + live subscribers so SSE works seamlessly even if Redis pub/sub or ARQ worker is single-process
_JOB_EVENT_HISTORY: dict[str, list[dict[str, Any]]] = {}
_JOB_SUBSCRIBERS: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
_JOB_FINISHED: set[str] = set()
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _parse_redis_settings(url: str) -> RedisSettings:
    parsed = urlparse(url or "redis://localhost:6379/0")
    db = int((parsed.path or "/0").lstrip("/") or 0)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=db,
        password=parsed.password,
    )


async def _get_redis() -> aioredis.Redis | None:
    try:
        return aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
        )
    except Exception:
        return None


async def publish_sse_event(
    job_id: str,
    event_type: str,
    data: dict[str, Any],
    *,
    redis_client: aioredis.Redis | None = None,
) -> None:
    """Publish a real-time SSE event for `job_id` to in-memory subscribers and Redis Pub/Sub."""
    envelope = {
        "job_id": job_id,
        "event": event_type,
        "data": data,
        "ts": round(time.time(), 3),
    }
    history = _JOB_EVENT_HISTORY.setdefault(job_id, [])
    history.append(envelope)
    if event_type == "complete":
        _JOB_FINISHED.add(job_id)

    for q in list(_JOB_SUBSCRIBERS.get(job_id, [])):
        try:
            q.put_nowait(envelope)
        except Exception:
            pass

    r = redis_client or await _get_redis()
    if r is not None:
        try:
            await r.publish(f"sse:job:{job_id}", json.dumps(envelope))
            await r.rpush(f"sse:history:{job_id}", json.dumps(envelope))
            await r.expire(f"sse:history:{job_id}", 600)
        except Exception:
            pass


async def stream_job_events(
    job_id: str,
    *,
    timeout_seconds: float = 35.0,
) -> AsyncIterator[str]:
    """
    Yield Server-Sent Events (`event: <type>\\ndata: <json>\\n\\n`) for `job_id`.
    Replays any events already emitted before the client connected, then streams live events
    until `complete` or `timeout_seconds`.
    """
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _JOB_SUBSCRIBERS.setdefault(job_id, []).append(q)
    seen_count = 0

    try:
        # 1. Replay already-emitted events from in-memory buffer or Redis list
        existing = list(_JOB_EVENT_HISTORY.get(job_id, []))
        if not existing:
            r = await _get_redis()
            if r is not None:
                try:
                    raw_items = await r.lrange(f"sse:history:{job_id}", 0, -1)
                    existing = [json.loads(x) for x in raw_items]
                except Exception:
                    existing = []

        for env in existing:
            seen_count += 1
            ev = env.get("event", "message")
            payload_json = json.dumps(env.get("data", {}))
            yield f"event: {ev}\ndata: {payload_json}\n\n"
            if ev == "complete":
                return

        if job_id in _JOB_FINISHED:
            return

        # 2. Stream live events as each store/quick-commerce task resolves
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining = max(0.5, min(4.0, deadline - time.monotonic()))
            try:
                env = await asyncio.wait_for(q.get(), timeout=remaining)
                ev = env.get("event", "message")
                payload_json = json.dumps(env.get("data", {}))
                yield f"event: {ev}\ndata: {payload_json}\n\n"
                if ev == "complete":
                    return
            except asyncio.TimeoutError:
                if job_id in _JOB_FINISHED:
                    return
                # Send SSE keep-alive heartbeat
                yield ": heartbeat\n\n"
    finally:
        subs = _JOB_SUBSCRIBERS.get(job_id, [])
        if q in subs:
            subs.remove(q)


async def run_universal_store_discovery(
    ctx: dict[str, Any],
    job_id: str,
    product_payload: dict[str, Any],
    location: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """ARQ job: discover & verify up to 15 universal stores, streaming each verified store via SSE."""
    from app.services.shopping_discovery import discover_stores

    payload = ClientPayload.model_validate(product_payload)
    r = await _get_redis()

    async def _on_store(quote: CompetitorQuote) -> None:
        await publish_sse_event(
            job_id,
            "store_quote",
            {"quote": quote.model_dump()},
            redis_client=r,
        )

    await publish_sse_event(
        job_id,
        "progress",
        {"stage": "universal_discovery_started", "message": f"Probing universal stores for {payload.title[:60]}..."},
        redis_client=r,
    )
    res = await discover_stores(
        payload,
        max_stores=15,
        on_quote_callback=_on_store,
        redis_client=r,
    )
    return [q.model_dump() for q in res.quotes]


async def run_quick_commerce_compare(
    ctx: dict[str, Any],
    job_id: str,
    product_payload: dict[str, Any],
    location: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """ARQ job: discover & verify 10-min Quick Commerce quotes for user's pincode, streaming via SSE."""
    from app.services.quick_commerce_engine import discover_quick_commerce_quotes, sanitize_pincode

    payload = ClientPayload.model_validate(product_payload)
    pin = sanitize_pincode((location or {}).get("pincode"))
    r = await _get_redis()

    async def _on_qc(quote: QuickCommerceQuote) -> None:
        await publish_sse_event(
            job_id,
            "quick_commerce_quote",
            {"quote": quote.model_dump()},
            redis_client=r,
        )

    await publish_sse_event(
        job_id,
        "progress",
        {"stage": "quick_commerce_started", "message": f"Checking 10-min apps for pincode {pin}..."},
        redis_client=r,
    )
    quotes = await discover_quick_commerce_quotes(
        payload,
        pincode=pin,
        on_quote_callback=_on_qc,
        redis_client=r,
    )
    return [q.model_dump() for q in quotes]


async def audit_issuer_terms_job(
    ctx: dict[str, Any],
    card_or_route_key: str,
    merchant_domain: str,
    method_type: str = "credit_card",
) -> dict[str, Any]:
    """
    ARQ job: background 2-speed Live T&C Auditor (`Trafilatura` -> `Crawl4AI` -> `Instructor` -> `kg_edges`).
    """
    from app.db.session import async_session_factory
    from app.services.kg_service import persist_verified_rule_to_kg
    from app.services.live_terms_engine import build_issuer_tc_query, get_or_verify_rule

    r = await _get_redis()
    query = build_issuer_tc_query(card_or_route_key, merchant_domain)
    rule = await get_or_verify_rule(
        route_kind=method_type,
        method_name=card_or_route_key,
        method_type=method_type,
        retailer=merchant_domain,
        search_query=query,
        allow_live_crawl=True,
        redis_client=r,
    )
    if rule.verification_status in ("issuer_tc_verified", "live_tc_verified"):
        try:
            async with async_session_factory() as session:
                await persist_verified_rule_to_kg(session, rule, merchant_domain)
                await session.commit()
        except Exception as exc:
            logger.warning("Failed to persist audited rule '%s' to KG: %s", card_or_route_key, exc)
    return rule.model_dump(mode="json")


async def launch_compare_all_stores_job(
    job_id: str,
    payload: ClientPayload,
    pincode: str | None = None,
) -> None:
    """
    Zero-Friction Orchestrator: runs Universal Store Discovery and Quick Commerce Compare
    concurrently in the background and emits a final `complete` SSE event when both finish.
    """
    from app.services.quick_commerce_engine import sanitize_pincode

    pin = sanitize_pincode(pincode)
    payload_dict = payload.model_dump(mode="json")
    loc = {"pincode": pin}

    async def _runner() -> None:
        r = await _get_redis()
        try:
            stores_res, qc_res = await asyncio.gather(
                run_universal_store_discovery({}, job_id, payload_dict, loc),
                run_quick_commerce_compare({}, job_id, payload_dict, loc),
                return_exceptions=True,
            )
            stores_list = stores_res if isinstance(stores_res, list) else []
            qc_list = qc_res if isinstance(qc_res, list) else []
            await publish_sse_event(
                job_id,
                "complete",
                {
                    "job_id": job_id,
                    "pincode": pin,
                    "stores_count": len(stores_list),
                    "quick_commerce_count": len(qc_list),
                    "stores": stores_list,
                    "quick_commerce": qc_list,
                },
                redis_client=r,
            )
        except Exception as exc:
            logger.warning("Background compare job %s failed: %s", job_id, exc)
            await publish_sse_event(
                job_id,
                "complete",
                {"job_id": job_id, "pincode": pin, "error": str(exc), "stores": [], "quick_commerce": []},
                redis_client=r,
            )

    task = asyncio.create_task(_runner())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


class WorkerSettings:
    """ARQ Worker configuration (`arq app.worker.WorkerSettings`)."""

    functions = [
        run_universal_store_discovery,
        run_quick_commerce_compare,
        audit_issuer_terms_job,
    ]
    redis_settings = _parse_redis_settings(settings.redis_url)
    max_jobs = 6
    job_timeout = 45
