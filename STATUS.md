# OneStop Truth Audit (`STATUS.md`)

**Branch:** `v4` (branched from `origin/v3` `3528c3491113e6f454489e04ff4555e250614533`)  
**PostgreSQL Port (Windows host):** `5433` (`onestop-db-1`, TimescaleDB PG16, migrated through `003_price_obs_index`)  
**Docker Stack:** `db` (`5433->5432`), `redis` (`6379`), `searxng` (`8080`), `backend` (`8000`), `frontend` (`3000`)

---

## Feature Audit Table

| Feature | Exists in Code (file) | Runs? (how verified) | Status | Notes / Observed Behaviour |
|---|---|---|---|---|
| **Base Scraper & Cache** | `backend/app/scrapers/base_scraper.py` | `pytest` (`test_phase1_fixes.py`, `test_phase2_speed.py`, `test_parsers_offline.py`) + live 10-URL runs | **Working** | Detects soft-blocks (`<20KB`, captcha keywords `<120KB`, missing `#productTitle` on Amazon `/dp/`), never caches blocks/failed parses, rotates `chrome`/`chrome124`/`safari180`/`edge101` with 300–800ms jitter, pooled `AsyncSession`, fast `resolve_url_redirects`, `LexborHTMLParser` `<30ms` parse, 30m `parsed_payload` + 6h `page` caches, `SCRAPER_MODE=mock`, `SCRAPER_PROXY_URL`. |
| **Amazon.in Scraper** | `backend/app/scrapers/amazon_in.py` | Live 5×3 run + `smoke.py` + `test_parsers_offline.py` | **Working** | Improved from **1/15 (6.7%)** to **15/15 (100%)** on live runs via homepage warmup + glow pincode `411001` (`session-id`, `i18n-prefs`, `lc-acbin`) + profile rotation. Extracts title, price, MRP, seller, stock, and images via `LexborHTMLParser`. |
| **Flipkart Scraper** | `backend/app/scrapers/flipkart.py` | Live 5-URL run + `smoke.py` + `test_parsers_offline.py` | **Working** | Extracts structured JSON (`window.__INITIAL_STATE__` + JSON-LD) via `LexborHTMLParser` (`~22–30ms`). Real `seller_name` (never `"Become a Seller"`), real `mrp`, `images`, `sellers`, and `in_stock` (`False` verified on OOS phones). |
| **Croma Scraper** | `backend/app/scrapers/croma.py` | Code inspection (`get_scraper`) | **Stub / Fragile** | Uses `BeautifulSoup` + CSS selectors; untested against live Croma SPA/WAF in this audit. |
| **Reliance Digital Scraper** | `backend/app/scrapers/reliance_digital.py` | Code inspection (`get_scraper`) | **Stub / Fragile** | Uses `BeautifulSoup` + CSS selectors; untested against live Reliance Digital in this audit. |
| **Generic Scraper** | `backend/app/scrapers/generic.py` | Code inspection + `pytest` | **Working (Limited)** | JSON-LD / OpenGraph / basic CSS fallback. |
| **Universal Scraper (4-Tier)** | `backend/app/services/universal_scraper.py` | `pytest` (mocked) + code inspection | **Working (Fragile)** | Tier 0 Shopify/WooCommerce JSON, Tier 1 `scrapling.Fetcher` + `extruct`, Tier 2 `StealthySession` (`patchright` binary not installed by default on Windows), Tier 3 `instructor` LLM rescue (`max_tokens` omitted). Now used as fallback behind dedicated Amazon/Flipkart scrapers in `/analyze-url`. |
| **Rate Guard & Circuit Breaker** | `backend/app/services/rate_guard.py` | `pytest` (`test_phase2_speed.py`, `test_universal_and_cash_points.py`) + Redis check | **Working** | Redis atomic `SET rate_limit:{domain} 1 PX <ms> NX` cross-worker rate limiter (with in-memory `AsyncLimiter` fallback), Redis 30m `cb:{domain}` circuit breaker wired into `BaseScraper.fetch_page`, `BROWSER_SEMAPHORE(2)`. |
| **Product Matcher & Identity** | `backend/app/services/product_matcher.py`, `product_identity.py` | `pytest` + 10k-row PG16 `EXPLAIN ANALYZE` + SQL query count | **Working** | Exact `(retailer, external_sku)` + `mpn_or_ean` lookup first; `pg_trgm` fallback uses `SET LOCAL pg_trgm.similarity_threshold = 0.3` and `WHERE model_name % :q` (**Bitmap Index Scan** on GIN index in `1.986ms` at 10k rows). Self-heals stale `"Add to your order"` titles. Total pipeline SQL queries reduced to **10** (cold new product) / **8** (repeat). |
| **Card Optimizer** | `backend/app/services/card_optimizer.py` | `pytest` (`TestBasicFlatDiscount`..`TestMultipleCardsRanking`) + live `POST /calculate-effective-price` (`200`) | **Working** | Deterministic cash discount + reward math; `routes.py` reduced to single `CreditCard` query. |
| **Offer Stacking & Live T&C** | `backend/app/services/offer_stack_engine.py`, `live_terms_engine.py` | `pytest` (`test_offer_stack_engine.py`, `test_universal_zero_hardcoding.py`) | **Working** | Strict cash-vs-points separation, 5-gate teaser/EMI filtering, `Crawl4AI` + `Trafilatura` + `instructor` T&C verification. |
| **Seller Forensics** | `backend/app/services/seller_forensics.py` | `pytest` + pipeline E2E run | **Working** | Evaluates `AuthorizedSeller` table + review scam signals + multi-seller pros/cons. |
| **Buy / Wait Engine** | `backend/app/services/buy_wait_engine.py` | `pytest` + `GET /api/v1/products/{id}/history` (`200`) | **Working** | Uses TimescaleDB `price_observations` (`ix_price_observations_listing_observed_desc` + 1h dedupe) + Indian sale calendar. |
| **Quick Commerce Engine** | `backend/app/services/quick_commerce_engine.py` | `pytest` (`test_quick_commerce_unit_price_fees_and_pincode_location`) | **Working (Discovery-Dependent)** | Pincode centroid resolver, unit price math, checkout fees; live quotes depend on SearXNG + `universal_scraper`. |
| **Warranty Vault (PDF)** | `backend/app/services/vault_service.py` | `pytest` + live `POST /vault/upload-invoice` & `GET /vault/items` (`200`) | **Working** | Fixed to `pdfplumber.open(io.BytesIO(file_bytes))`; verified with real valid PDF invoice upload + persisted retrieval. |
| **Exchange Arbitrage** | `backend/app/services/exchange_arbitrage.py` | Code inspection (`POST /api/v1/compare-exchange`) | **Working** | Deterministic buyback haircut comparison. |
| **Temporal Knowledge Graph** | `backend/app/services/kg_service.py`, `models/knowledge_graph.py`, `002_temporal_knowledge_graph.py` | `pytest` + DB migration | **Working** | `kg_entities` + `kg_edges` with char 3-gram NumPy embeddings and temporal expiration. |
| **SSE Streaming & ARQ Worker** | `backend/app/worker.py`, `routes.py` (`/compare-all-stores`, `/stream-compare/{job_id}`) | `pytest` (`test_arq_fallback_sse_event_streaming`) | **Working (In-Process)** | `launch_compare_all_stores_job` still spawns in-process `asyncio.create_task` rather than enqueuing to external ARQ worker; SSE streams via in-memory queue + Redis pub/sub. |
| **Store Discovery (SearXNG)** | `backend/app/services/shopping_discovery.py`, `searxng/settings.yml` | Live SearXNG (`200 OK`) + fallback check (`localhost:19999` -> graceful warning) | **Working** | Docker SearXNG running on `:8080` returning JSON; unreachable URL degrades cleanly with warning log. |
| **LLM Parser** | `backend/app/services/llm_parser.py` | Live OpenRouter key + no-key fallback + `pytest` | **Working** | `import asyncio`, hard `4.0s` per-call timeout, parallel `asyncio.gather`, `max_tokens=1024`, null coercion on `BankOfferRule` & `VariantAttributes`, Redis caching of successes & fallbacks (`300s`), structured `llm_mode=llm\|fallback` logs, startup OpenAI preload, `/health` `llm` (`ok\|no_key\|degraded`). |
| **Chrome Extension** | `extension/` (`manifest.json`, `background.js`, `dom_extractor.js`, `offscreen.js`) | Code inspection (MV3 unpacked) | **Working (Code-Verified; Manual Chrome Load)** | Stage 1 posts DOM payload; Stage 2 calls `/discover-stores` + offscreen fallback. Not driven by headless browser test in `v4`. |
| **Frontend: Home (`/`)** | `frontend/src/app/page.tsx` | `npx tsc --noEmit` + Dockerized `GET http://127.0.0.1:3000/` (`200`) | **Working** | Calls `POST /api/v1/analyze-url` and `GET /api/v1/products/recent`. |
| **Frontend: Product (`/product/[id]`)** | `frontend/src/app/product/[id]/page.tsx` | `npx tsc --noEmit` + `GET /api/v1/products/{id}` (`200`) + `GET /product/{id}` (`200`) | **Working** | `LiveOfferRaw` imported in `routes.py`; returns `200` for products created via `/analyze-url`. |
| **Frontend: Vault (`/vault`)** | `frontend/src/app/vault/page.tsx` | `npx tsc --noEmit` + Dockerized `GET /vault` (`200`) + API PDF upload (`200`) | **Working** | End-to-end backend PDF parsing fixed and verified. |

---

## Known Gaps / Remaining Limitations

1. **Cron / Scheduled Price Checks**: **Missing** — `WorkerSettings` in `backend/app/worker.py` has `functions = [...]` only and zero `cron_jobs` or scheduler definitions.
2. **External ARQ Queue Dispatch**: `launch_compare_all_stores_job` runs as an in-process `asyncio.create_task` inside FastAPI (`worker.py`) rather than enqueuing to an external ARQ worker process.
3. **Playwright vs Patchright**: Playwright Chromium (`playwright install --with-deps chromium`) is installed in both `.venv` and `backend/Dockerfile`, while `scrapling`'s `StealthySession` (`universal_scraper.py` Tier 2) uses `patchright` whose browser binary is not installed by default.
4. **Croma & Reliance Digital Scrapers**: CSS-based scrapers remain untested against live stores in this sprint.
5. **Image Perceptual Hashing**: `imagehash` / `compare_image_phashes` exists in `product_matcher.py` for offline hash comparison and does **not** download images in the live request path.
