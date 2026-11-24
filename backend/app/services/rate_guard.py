"""
Shared Browser Concurrency & Anti-Ban Rate Guard.

Enforces:
1. Global asyncio.Semaphore(2) shared across Scrapling StealthySession & Crawl4AI
   so headless Chromium instances never overload RAM or cause timeouts.
2. Per-domain aiolimiter.AsyncLimiter (max 1 request / 2.5 seconds per domain).
3. Redis 30-minute per-domain circuit breaker (`cb:{domain}`) with automatic
   in-memory fallback when Redis is unreachable (e.g. unit tests).
4. Tenacity retry wrapper that retries transient network/5xx errors while
   explicitly EXCLUDING HTTP 403 / 429 (which instead trip the circuit breaker).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import urlparse

import redis.asyncio as aioredis
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Shared across ALL headless browser engines (Scrapling StealthySession + Crawl4AI)
BROWSER_SEMAPHORE = asyncio.Semaphore(2)

# Per-domain rate limiters: max 1 request per 2.5 seconds per domain
_DOMAIN_LIMITERS: dict[str, AsyncLimiter] = {}

# 30-minute per-domain circuit breaker TTL
CIRCUIT_BREAKER_TTL_SECONDS = 30 * 60

# In-memory fallback circuit breaker when Redis is unavailable
_MEMORY_CB: dict[str, tuple[float, str]] = {}

_redis_client: aioredis.Redis | None = None


class RateLimitOrBlockedError(Exception):
    """Raised on HTTP 403 / 429 / CAPTCHA — MUST NOT be retried."""

    def __init__(self, domain: str, status_code: int | None = None, reason: str = "Blocked"):
        self.domain = domain
        self.status_code = status_code
        self.reason = reason
        super().__init__(f"[{domain}] {reason} (status={status_code})")


class CircuitOpenError(Exception):
    """Raised when a domain's 30-minute circuit breaker is currently open."""

    def __init__(self, domain: str, reason: str = "Circuit breaker active"):
        self.domain = domain
        self.reason = reason
        super().__init__(f"[{domain}] skipped: {reason}")


def extract_domain(url: str) -> str:
    """Normalize a URL to its lowercase registrable hostname (without leading www.)."""
    try:
        host = (urlparse(url).hostname or "").lower().strip()
        if host.startswith("www."):
            host = host[4:]
        return host or "unknown"
    except Exception:
        return "unknown"


def get_domain_limiter(domain: str) -> AsyncLimiter:
    """Return the per-domain AsyncLimiter (1 req / 2.0s)."""
    d = domain.lower().strip()
    limiter = _DOMAIN_LIMITERS.get(d)
    if limiter is None:
        limiter = AsyncLimiter(max_rate=1, time_period=2.0)
        _DOMAIN_LIMITERS[d] = limiter
    return limiter


async def acquire_domain_rate_limit(
    domain: str,
    interval_seconds: float = 2.0,
    redis_client: aioredis.Redis | None = None,
) -> None:
    """
    Cross-worker Redis per-domain rate limiter (~1 req / interval_seconds).
    Uses atomic `SET rate_limit:{domain} 1 PX <ms> NX` across workers, with
    automatic fallback to the per-process AsyncLimiter if Redis is unreachable.
    """
    d = domain.lower().strip()
    px_ms = max(200, int(interval_seconds * 1000))
    key = f"rate_limit:{d}"
    r = redis_client or await _get_redis()
    if r is not None:
        try:
            for _ in range(15):
                acquired = await r.set(key, "1", px=px_ms, nx=True)
                if acquired:
                    return
                pttl = await r.pttl(key)
                wait_s = min(interval_seconds, max(0.05, (pttl / 1000.0) if pttl and pttl > 0 else 0.25))
                await asyncio.sleep(wait_s)
            return
        except Exception:
            pass

    limiter = get_domain_limiter(d)
    async with limiter:
        return


async def _get_redis() -> aioredis.Redis | None:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=1.5,
            )
        except Exception:
            return None
    return _redis_client


async def is_circuit_open(domain: str, redis_client: aioredis.Redis | None = None) -> tuple[bool, str]:
    """Check whether `cb:{domain}` is tripped in Redis (or in-memory fallback)."""
    d = domain.lower().strip()
    key = f"cb:{d}"
    r = redis_client or await _get_redis()
    if r is not None:
        try:
            val = await r.get(key)
            if val:
                return True, str(val)
            return False, ""
        except Exception:
            pass

    entry = _MEMORY_CB.get(d)
    if entry:
        expires_at, reason = entry
        if time.time() < expires_at:
            return True, reason
        _MEMORY_CB.pop(d, None)
    return False, ""


async def trip_circuit_breaker(
    domain: str,
    reason: str = "HTTP 403/429",
    ttl_seconds: int = CIRCUIT_BREAKER_TTL_SECONDS,
    redis_client: aioredis.Redis | None = None,
) -> None:
    """Open `cb:{domain}` for 30 minutes to protect user IP/reputation."""
    d = domain.lower().strip()
    key = f"cb:{d}"
    _MEMORY_CB[d] = (time.time() + ttl_seconds, reason)
    logger.warning("Circuit breaker tripped for %s (%ds): %s", d, ttl_seconds, reason)
    r = redis_client or await _get_redis()
    if r is not None:
        try:
            await r.set(key, reason, ex=ttl_seconds)
        except Exception:
            pass


def _is_retryable_exception(exc: BaseException) -> bool:
    """Retry transient network/5xx errors ONLY; never retry 403/429/CircuitOpen."""
    if isinstance(exc, (RateLimitOrBlockedError, CircuitOpenError, asyncio.CancelledError)):
        return False
    status = getattr(exc, "status_code", None)
    if status is None and hasattr(exc, "response"):
        status = getattr(exc.response, "status_code", None)
    if status in (401, 403, 404, 429):
        return False
    return True


async def guarded_domain_call(
    url_or_domain: str,
    coro_factory: Callable[[], Awaitable[T]],
    *,
    use_browser_semaphore: bool = False,
    max_attempts: int = 2,
    redis_client: aioredis.Redis | None = None,
) -> T:
    """
    Execute an async network or browser call under:
    - Redis 30-min circuit breaker check (`cb:{domain}`)
    - Per-domain AsyncLimiter (1 req / 2.5s)
    - Optional global BROWSER_SEMAPHORE(2)
    - Tenacity retry excluding 403/429 (which trip the circuit breaker immediately)
    """
    domain = extract_domain(url_or_domain) if "://" in url_or_domain else url_or_domain.lower().strip()

    is_open, reason = await is_circuit_open(domain, redis_client=redis_client)
    if is_open:
        raise CircuitOpenError(domain, reason)

    limiter = get_domain_limiter(domain)

    async def _run_once() -> T:
        async with limiter:
            if use_browser_semaphore:
                async with BROWSER_SEMAPHORE:
                    return await coro_factory()
            return await coro_factory()

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=2.0),
            retry=retry_if_exception(_is_retryable_exception),
            reraise=True,
        ):
            with attempt:
                try:
                    return await _run_once()
                except RateLimitOrBlockedError as blocked:
                    await trip_circuit_breaker(domain, blocked.reason, redis_client=redis_client)
                    raise
    except RateLimitOrBlockedError:
        raise
