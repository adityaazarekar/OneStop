from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://onestop:onestop_dev@localhost:5433/onestop"
    database_url_sync: str = "postgresql+psycopg2://onestop:onestop_dev@localhost:5433/onestop"
    redis_url: str = "redis://localhost:6379/0"
    env: Literal["development", "production", "test"] = "development"
    scraper_mode: Literal["live", "mock"] = "live"
    scraper_proxy_url: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    llm_model: str = "google/gemini-2.5-flash"
    searxng_url: str = "http://localhost:8080"
    serper_api_key: str = ""
    serp_mode: Literal["live", "replay"] = "live"
    max_serper_credits_per_product: int = 3
    max_llm_usd_per_run: float = 0.50
    default_pincode: str = "411001"
    quickcommerce_api_key: str = ""
    scrapingdog_api_key: str = ""
    app_secret_key: str = "change-me-in-production"
    cors_origins: list[str] = Field(default=["http://localhost:3000", "http://127.0.0.1:3000"])

    model_config = {
        "env_file": [
            str(_BACKEND_DIR / ".env"),
            str(_ROOT_DIR / ".env"),
            ".env",
            "../.env",
        ],
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
