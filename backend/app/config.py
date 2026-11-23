from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://onestop:onestop_dev@localhost:5432/onestop"
    database_url_sync: str = "postgresql+psycopg2://onestop:onestop_dev@localhost:5432/onestop"
    redis_url: str = "redis://localhost:6379/0"
    scraper_mode: Literal["live", "mock"] = "live"
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    searxng_url: str = "http://localhost:8080"
    serper_api_key: str = ""
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
