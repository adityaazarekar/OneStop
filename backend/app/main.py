from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes import router as api_v1_router
from app.config import settings
from app.scrapers.base_scraper import close_scraper_sessions
from app.services.llm_parser import get_llm_parser, get_llm_status

logger = logging.getLogger(__name__)

_app_logger = logging.getLogger("app")
_app_logger.setLevel(logging.INFO)
if not _app_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setLevel(logging.INFO)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _app_logger.addHandler(_handler)
_app_logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Startup / shutdown hooks."""
    # Preload OpenAI client & LLMParser singleton so first request pays 0ms import overhead
    try:
        import openai  # noqa: F401
        await get_llm_parser()
    except Exception as exc:
        logger.warning("LLMParser startup preload skipped: %s", exc)
    yield
    await close_scraper_sessions()


app = FastAPI(
    title="OneStop - Price Intelligence Platform",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    dur_ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Process-Time-Ms"] = f"{dur_ms:.1f}"
    logger.info("HTTP %s %s -> %d (%.1fms)", request.method, request.url.path, response.status_code, dur_ms)
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"^chrome-extension://.*$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "scraper_mode": settings.scraper_mode,
        "llm": get_llm_status(),
    }

