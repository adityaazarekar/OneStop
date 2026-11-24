# OneStop

E-commerce price intelligence platform for Indian retail stores (Amazon.in, Flipkart, and D2C / discovered storefronts). Combines live product extraction, `pg_trgm` canonical product matching, credit card reward & offer stacking optimization, seller trust forensics, TimescaleDB buy/wait history, and a warranty invoice vault.

## Architecture & Ports

| Service | Host Port | Purpose |
| :--- | :---: | :--- |
| **Frontend** (`Next.js`) | `3000` | Web dashboard, product analytics (`/product/[id]`), warranty vault (`/vault`) |
| **Backend** (`FastAPI`) | `8000` | Analysis pipeline, scrapers, optimizer engines, `/health` |
| **PostgreSQL + TimescaleDB** (`PG16`) | `5433` | Product catalog, listings, `pg_trgm` matching, hypertable price history |
| **Redis** (`7-alpine`) | `6379` | Page & parsed-payload caches, LLM cache, cross-worker rate limits, circuit breakers |
| **SearXNG** | `8080` | Meta-search engine used for cross-store price discovery |

> [!NOTE]
> Host PostgreSQL port is mapped to **`5433`** (`5433:5432`) so it does not conflict with a local Windows PostgreSQL service on `5432`.

## Prerequisites

- **Docker Desktop** (with Docker Compose v2)
- **Python 3.12+** (only needed for running local `.venv` scripts, `pytest`, or `scripts/smoke.py`)
- **Node.js 20+** (only needed if running `frontend/` outside Docker)
- **Google Chrome** / **Chromium** (only needed to test the browser extension)

## Quick Start (Full Docker Stack)

1. Create your environment file from `.env.example`:
   ```powershell
   Copy-Item .env.example .env
   ```
   Optional: add `OPENROUTER_API_KEY` in `.env` for LLM variant parsing, review analysis, and executive summaries. When omitted or if an LLM call times out (>4s), the backend automatically falls back to its deterministic parser/summary path.

   If you set `ENV=production`, you **must** override `APP_SECRET_KEY` from `"change-me-in-production"`, or the backend will refuse to start.

2. Build and start all services (`db`, `redis`, `searxng`, `backend`, `frontend`):
   ```powershell
   docker compose up --build -d
   ```

3. Seed reference data (credit card catalog, sample products, bank offers):
   ```powershell
   docker compose exec backend python seed.py
   ```
   *(The `backend` container automatically runs `alembic upgrade head` on startup.)*

4. Verify endpoints:
   - Backend health: `http://localhost:8000/health`
   - Frontend dashboard: `http://localhost:3000`

## Local Development Setup (Host `.venv` + Infra Containers)

If you prefer running `uvicorn` and `next` directly on Windows while keeping DB/Redis/SearXNG in Docker:

1. Start infrastructure services:
   ```powershell
   docker compose up -d db redis searxng
   ```

2. Set up the Python virtual environment and run migrations + seed:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip.exe install -r backend\requirements.txt
   .\.venv\Scripts\playwright.exe install chromium
   cd backend
   ..\.venv\Scripts\alembic.exe upgrade head
   ..\.venv\Scripts\python.exe seed.py
   ```

3. Start the FastAPI backend:
   ```powershell
   cd backend
   ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

4. Start the Next.js frontend:
   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

## Running Tests & Smoke Suite

### Full Backend `pytest` Suite
Unit tests, optimizer tests, offline HTML fixture tests, and PostgreSQL integration tests:
```powershell
cd backend
..\.venv\Scripts\pytest.exe -v
```
*(Ensure Docker `db` on port `5433` and `redis` on `6379` are running for integration tests; offline parser tests in `tests/test_parsers_offline.py` run cleanly without network access.)*

### Live 10-URL Smoke Suite
Requires the backend running on `http://127.0.0.1:8000` (override target via `ONESTOP_API_URL`):
```powershell
.\.venv\Scripts\python.exe scripts/smoke.py
```
Reads URLs from `scripts/smoke_urls.txt` (5 Amazon.in + 5 Flipkart URLs), posts each to `POST /api/v1/analyze-url`, prints status/latency/price/MRP/seller/stock/verdict/LLM mode, and exits non-zero if fewer than 80% pass.

## Chrome Extension

1. Open `chrome://extensions` in Chrome.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select the [`extension/`](./extension/) directory.
4. Ensure the backend is running on `http://localhost:8000` (configurable via extension popup/storage) and browse any Amazon.in or Flipkart product page.

## Environment Variables (`.env.example`)

| Variable | Default / Example | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+asyncpg://onestop:onestop_dev@localhost:5433/onestop` | Async SQLAlchemy DSN (host port `5433`) |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://onestop:onestop_dev@localhost:5433/onestop` | Sync psycopg2 DSN for Alembic & scripts |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL for caches, rate limiter, circuit breakers |
| `ENV` | `development` | `development`, `production`, or `test` |
| `SCRAPER_MODE` | `live` | `live`, `mock` (serves offline fixtures), or `extension_only` |
| `SCRAPER_PROXY_URL` | `""` | Optional HTTP/HTTPS proxy URL injected into `curl_cffi` and Playwright |
| `OPENROUTER_API_KEY` | `""` | Optional OpenRouter key for LLM calls (4s timeout + deterministic fallback) |
| `OPENROUTER_MODEL` | `google/gemini-2.5-flash` | OpenRouter model identifier |
| `SEARXNG_URL` | `http://localhost:8080` | SearXNG base URL (`http://searxng:8080` inside Compose) |
| `SERPER_API_KEY` | `""` | Optional Serper API fallback key |
| `DEFAULT_PINCODE` | `411001` | Delivery pincode sent via Amazon session cookies |
| `APP_SECRET_KEY` | `change-me-in-production` | App secret key (startup error in `ENV=production` if unchanged) |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed CORS origins |
