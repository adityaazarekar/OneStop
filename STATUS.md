# OneStop Truth Audit (`STATUS.md`)

**Branch:** `v4` (branched from `origin/v3` commit `3528c3491113e6f454489e04ff4555e250614533`)  
**Baseline Test Run:** `pytest -v` -> **68 passed** in 23.44s (unit/mocked + in-memory tests).  
**Frontend Typecheck:** `cd frontend && npx tsc --noEmit` -> **0 errors**.  
**Infrastructure:** Docker Compose `db` (TimescaleDB PG16 on `5433`), `redis` (`6379`), `searxng` (`8080`, returning live JSON results).

---

## Feature Audit Table

| Feature | Exists in Code (file) | Runs? (how verified) | Status | Notes / Observed Behaviour |
|---|---|---|---|---|
| **Base Scraper & Cache** | `backend/app/scrapers/base_scraper.py` | Code + live probe | **Broken (Fragile)** | Only treats HTTP `403/429/503` as blocked; caches Amazon HTTP 200 3.7 KB soft-block HTML in Redis for 6h. No pincode/warmup/profile rotation. |
| **Amazon.in Scraper** | `backend/app/scrapers/amazon_in.py` | Live probe + `pytest` | **Broken (Partial)** | Fails with `ScraperParseError` whenever Amazon returns HTTP 200 soft-block page; no `images` or multi-seller extraction. |
| **Flipkart Scraper** | `backend/app/scrapers/flipkart.py` | Live probe | **Broken (Partial)** | Extracts `"Become a Seller"` via `a[href*='seller']` and `mrp=None` due to stale CSS classes; ignores `window.__INITIAL_STATE__`; no `images` / `sellers` list. |
| **Croma Scraper** | `backend/app/scrapers/croma.py` | Code inspection (`get_scraper`) | **Stub / Fragile** | Uses `BeautifulSoup` + CSS selectors; untested against live Croma SPA/WAF in this audit. |
| **Reliance Digital Scraper** | `backend/app/scrapers/reliance_digital.py` | Code inspection (`get_scraper`) | **Stub / Fragile** | Uses `BeautifulSoup` + CSS selectors; untested against live Reliance Digital in this audit. |
| **Generic Scraper** | `backend/app/scrapers/generic.py` | Code inspection + `pytest` | **Working (Limited)** | JSON-LD / OpenGraph / basic CSS fallback. |
| **Universal Scraper (4-Tier)** | `backend/app/services/universal_scraper.py` | `pytest` (mocked) + code | **Working (Fragile)** | Tier 0 Shopify/WooCommerce JSON, Tier 1 `scrapling.Fetcher` + `extruct`, Tier 2 `StealthySession`, Tier 3 `instructor` LLM rescue. `analyze_url` calls this *before* dedicated scrapers. |
| **Rate Guard & Circuit Breaker** | `backend/app/services/rate_guard.py` | `pytest` (`test_rate_guard_semaphore_and_circuit_breaker`) | **Working (Partial)** | Exists in `v3`/`v4`! `BROWSER_SEMAPHORE(2)`, `aiolimiter` (`1 req/2.5s` **in-memory only**, not cross-worker Redis), Redis 30m `cb:{domain}` breaker. Not wired into `BaseScraper`. |
| **Product Matcher & Identity** | `backend/app/services/product_matcher.py`, `product_identity.py` | `pytest` + `EXPLAIN ANALYZE` | **Broken (Slow)** | Exact `(retailer, sku)` lookup exists, but `pg_trgm` fallback uses `WHERE similarity(model_name, :q) > 0.3` which forces a **Seq Scan** instead of using the GIN index (`%` operator). |
| **Card Optimizer** | `backend/app/services/card_optimizer.py` | `pytest` (`TestBasicFlatDiscount`..`TestMultipleCardsRanking`, 15 tests) | **Working** | Deterministic cash discount + reward math; `CreditCard` queried twice in `routes.py` when `user_card_ids` empty. |
| **Offer Stacking & Live T&C** | `backend/app/services/offer_stack_engine.py`, `live_terms_engine.py` | `pytest` (`test_offer_stack_engine.py`, `test_universal_zero_hardcoding.py`) | **Working** | Strict cash-vs-points separation, 5-gate teaser/EMI filtering, `Crawl4AI` + `Trafilatura` + `instructor` T&C verification. |
| **Seller Forensics** | `backend/app/services/seller_forensics.py` | `pytest` + pipeline tests | **Working** | Evaluates `AuthorizedSeller` table + review scam signals + multi-seller pros/cons. |
| **Buy / Wait Engine** | `backend/app/services/buy_wait_engine.py` | `pytest` + DB query | **Working** | Uses TimescaleDB `price_observations` percentiles (`p10`, `median_90d`) + Indian sale calendar. |
| **Quick Commerce Engine** | `backend/app/services/quick_commerce_engine.py` | `pytest` (`test_quick_commerce_unit_price_fees_and_pincode_location`) | **Working (Discovery-Dependent)** | Pincode centroid resolver, unit price math, checkout fees; live quotes depend on SearXNG + `universal_scraper`. |
| **Warranty Vault (PDF)** | `backend/app/services/vault_service.py` | Code + runtime check (`hasattr(pdfplumber, 'open_from_bytes') == False`) | **Broken** | Line 31 calls `pdfplumber.open_from_bytes(file_bytes)` which **does not exist** in `pdfplumber` (must be `pdfplumber.open(io.BytesIO(...))`). |
| **Exchange Arbitrage** | `backend/app/services/exchange_arbitrage.py` | Code inspection (`POST /api/v1/compare-exchange`) | **Working** | Pure deterministic math comparing marketplace vs external buyback haircuts. |
| **Temporal Knowledge Graph** | `backend/app/services/kg_service.py`, `models/knowledge_graph.py`, `alembic/versions/002_temporal_knowledge_graph.py` | `pytest` (`test_temporal_knowledge_graph_crud_multihop_and_expiration`) + DB migration | **Working** | `kg_entities` + `kg_edges` with char 3-gram NumPy embeddings and temporal `valid_from`/`valid_to` expiration. |
| **SSE Streaming & ARQ Worker** | `backend/app/worker.py`, `routes.py` (`/compare-all-stores`, `/stream-compare/{job_id}`) | `pytest` (`test_arq_fallback_sse_event_streaming`) | **Working (In-Process)** | `launch_compare_all_stores_job` spawns in-process `asyncio.create_task` rather than enqueuing to external ARQ worker; SSE streams via in-memory queue + Redis pub/sub. |
| **Store Discovery (SearXNG)** | `backend/app/services/shopping_discovery.py`, `searxng/settings.yml` | Live `GET http://localhost:8080/search?q=iphone+15&format=json` (`200 OK`) | **Working** | SearXNG running in Docker on `:8080` and returning JSON results. |
| **LLM Parser** | `backend/app/services/llm_parser.py` | Code + `pytest` (mocked) | **Broken (Fragile)** | `import asyncio` exists on `v3`/`v4`, timeout is 6s/12s (needs 4s hard limit), `openai` lazily imported on first call (~1.6s hit), `/health` lacks `llm` status, no structured `llm_mode` log. |
| **Chrome Extension** | `extension/` (`manifest.json`, `background.js`, `content_script.js`, `dom_extractor.js`, `offscreen.js`, `popup.js`) | Code inspection (MV3 unpacked) | **Working (Unpacked)** | Stage 1 posts DOM payload; Stage 2 calls `/discover-stores` then falls back to offscreen HTML + DDG parsing. |
| **Frontend: Home (`/`)** | `frontend/src/app/page.tsx` | `tsc --noEmit` + HTTP 200 check | **Working** | Calls `POST /api/v1/analyze-url` and `GET /api/v1/products/recent`. |
| **Frontend: Product (`/product/[id]`)** | `frontend/src/app/product/[id]/page.tsx` | `tsc --noEmit` + code inspection | **Working (on v3/v4)** | `LiveOfferRaw` is imported in `v3`/`v4` `routes.py`; streams live stores & QC via `EventSource` SSE. |
| **Frontend: Vault (`/vault`)** | `frontend/src/app/vault/page.tsx` | `tsc --noEmit` + code inspection | **Blocked by Backend Bug** | UI exists; uploads fail until `vault_service.py` `open_from_bytes` is fixed. |

---

## Claims vs Reality Discrepancies

1. **Cron / Scheduled Price Checks**: **Missing** — `WorkerSettings` in `backend/app/worker.py` has `functions = [...]` only and zero `cron_jobs` or scheduler definitions anywhere in the repo.
2. **External ARQ Queue Dispatch**: `launch_compare_all_stores_job` runs as an in-process `asyncio.create_task` inside FastAPI (`worker.py` L308) rather than calling `arq.create_pool(...).enqueue_job(...)`.
3. **`SCRAPER_MODE` (`live` | `mock`)**: Defined in `config.py` and exposed on `/health`, but **never read by any scraper**.
4. **Cross-Worker Rate Limiting**: `rate_guard.py` uses in-memory `aiolimiter.AsyncLimiter` (`_DOMAIN_LIMITERS` dict), which is per-process, not Redis-backed across workers.
5. **Playwright Fallback**: `playwright install chromium` is missing from `backend/Dockerfile`, and `SCRAPER_PROXY_URL` does not exist.
6. **Env vars unused vs `.env.example`**: `GEMINI_API_KEY`, `QUICKCOMMERCE_API_KEY` (declared in `config.py`, unused by default), `SCRAPINGDOG_API_KEY` (declared in `config.py`, unused by default).
7. **Image Perceptual Hashing**: `imagehash` / `compare_image_phashes` exists in `product_matcher.py` for offline hash comparison when hashes are supplied, and does **not** download images in the live request path.
