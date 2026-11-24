from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes import router as api_v1_router
from app.config import settings
from app.services.llm_parser import get_llm_parser, get_llm_status

logger = logging.getLogger(__name__)


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


app = FastAPI(
    title="OneStop - Price Intelligence Platform",
    version="0.1.0",
    lifespan=lifespan,
)

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

