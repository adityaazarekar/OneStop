/**
 * OneStop Background Service Worker (Manifest V3)
 *
 * Strict Two-Stage Anti-Ban & Universal Store Discovery:
 * - Stage 1 (Auto on product page load, 45s timeout): 0 extra store requests!
 *   Sends active-tab DOM payload to FastAPI `/analyze-client-payload`.
 * - Stage 2 (On-demand "Compare All Stores" click):
 *   1. Calls backend `POST /api/v1/discover-stores` (SearXNG + 3-Tier Scrapling + RapidFuzz).
 *   2. If backend discovery fails or returns 0 quotes, falls back to the anti-ban offscreen
 *      HTML fetcher (`credentials: "omit"`, 900–1600ms jitter, 6h cache, 30m circuit breaker).
 *   3. Merges/deduplicates quotes by URL and posts to `/analyze-client-payload`.
 */

const API_BASE = "http://localhost:8000/api/v1";
const CACHE_TTL_MS = 6 * 60 * 60 * 1000; // 6 hours
const CIRCUIT_BREAKER_MS = 30 * 60 * 1000; // 30 minutes

const SEARCH_URL_BUILDERS = {
  amazon_in: (q) => `https://www.amazon.in/s?k=${encodeURIComponent(q)}`,
  flipkart: (q) => `https://www.flipkart.com/search?q=${encodeURIComponent(q)}`,
  croma: (q) => `https://www.croma.com/searchB?q=${encodeURIComponent(q)}%3Arelevance&text=${encodeURIComponent(q)}`,
  reliance_digital: (q) => `https://www.reliancedigital.in/search?q=${encodeURIComponent(q)}:relevance`,
  vijaysales: (q) => `https://www.vijaysales.com/search/${encodeURIComponent(q)}`,
  tatacliq: (q) => `https://www.tatacliq.com/search/?searchCategory=all&text=${encodeURIComponent(q)}`,
  jiomart: (q) => `https://www.jiomart.com/search/${encodeURIComponent(q)}`,
  myntra: (q) => `https://www.myntra.com/${encodeURIComponent(q.replace(/\s+/g, "-"))}`,
  ajio: (q) => `https://www.ajio.com/search/?text=${encodeURIComponent(q)}`,
  nykaa: (q) => `https://www.nykaa.com/search/result/?q=${encodeURIComponent(q)}`,
};

const RETAILER_DOMAINS = {
  amazon_in: "amazon.in",
  flipkart: "flipkart.com",
  croma: "croma.com",
  reliance_digital: "reliancedigital.in",
  vijaysales: "vijaysales.com",
  tatacliq: "tatacliq.com",
  jiomart: "jiomart.com",
  myntra: "myntra.com",
  ajio: "ajio.com",
  nykaa: "nykaa.com",
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ── Offscreen Document Management ────────────

async function ensureOffscreenDocument() {
  if (chrome.offscreen) {
    if (await chrome.offscreen.hasDocument?.()) return;
    const contexts = await chrome.runtime.getContexts?.({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
    if (contexts && contexts.length > 0) return;
    await chrome.offscreen.createDocument({
      url: "offscreen.html",
      reasons: ["DOM_PARSER"],
      justification: "Parse fetched retailer HTML for cross-store price comparison",
    });
  }
}

async function parseViaOffscreen(retailer, html) {
  await ensureOffscreenDocument();
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "PARSE_RETAILER_HTML", retailer, html }, (response) => {
      if (chrome.runtime.lastError) {
        resolve([]);
      } else {
        resolve(response?.quotes || []);
      }
    });
  });
}

async function parseDdgViaOffscreen(retailer, domain, html) {
  await ensureOffscreenDocument();
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "PARSE_DDG_HTML", retailer, domain, html }, (response) => {
      if (chrome.runtime.lastError) {
        resolve([]);
      } else {
        resolve(response?.quotes || []);
      }
    });
  });
}

// ── Circuit Breakers & 6h Cache ──────────────

async function getCircuitBreakers() {
  const store = await chrome.storage.local.get(["circuitBreakers"]);
  return store.circuitBreakers || {};
}

async function triggerCircuitBreaker(retailer, reason) {
  const breakers = await getCircuitBreakers();
  breakers[retailer] = {
    pausedUntil: Date.now() + CIRCUIT_BREAKER_MS,
    reason: reason || "Rate Limit / Verification Triggered",
  };
  await chrome.storage.local.set({ circuitBreakers: breakers });
}

async function isRetailerPaused(retailer) {
  const breakers = await getCircuitBreakers();
  const info = breakers[retailer];
  if (!info) return false;
  if (Date.now() < info.pausedUntil) return info;
  delete breakers[retailer];
  await chrome.storage.local.set({ circuitBreakers: breakers });
  return false;
}

async function getCachedSearch(query) {
  const store = await chrome.storage.local.get(["searchCache"]);
  const cache = store.searchCache || {};
  const entry = cache[query];
  if (entry && Date.now() - entry.timestamp < CACHE_TTL_MS) {
    return entry.quotes;
  }
  return null;
}

async function setCachedSearch(query, quotes) {
  const store = await chrome.storage.local.get(["searchCache"]);
  const cache = store.searchCache || {};
  cache[query] = { quotes, timestamp: Date.now() };
  await chrome.storage.local.set({ searchCache: cache });
}

// ── Query & Variant Matching ─────────────────

function buildSearchQuery(payload) {
  const parts = [];
  const seen = new Set();
  const addTok = (w) => {
    const low = w.toLowerCase();
    if (!seen.has(low)) {
      seen.add(low);
      parts.push(w);
    }
  };
  if (payload.brand && payload.brand.toLowerCase() !== "generic") addTok(payload.brand);

  const titleWords = (payload.title || "")
    .replace(/\(.*?\)/g, " ")
    .replace(/[^a-zA-Z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 1 && !/^(with|and|the|for|in|on|of|a|an|to|by|generic)$/i.test(w));

  titleWords.slice(0, 6).forEach(addTok);

  const specs = payload.variant_specs || {};
  if (specs.storage_gb) addTok(specs.storage_gb + "GB");
  if (specs.ram_gb) addTok(specs.ram_gb + "GB");
  return parts.join(" ").trim().substring(0, 100);
}

function isVariantMatch(payload, candidateTitle) {
  if (!candidateTitle) return false;
  const tCand = candidateTitle.toLowerCase();
  const tTarget = (payload.title || "").toLowerCase();

  const accessories = ["case", "cover", "screen protector", "tempered glass", "glass", "skin", "cable", "adapter", "charger", "pouch", "strap", "remote", "stand"];
  const targetHasAcc = accessories.some((a) => tTarget.includes(a));
  const candHasAcc = accessories.some((a) => tCand.includes(a));
  if (candHasAcc && !targetHasAcc) return false;

  const tiers = ["pro max", "ultra", "plus", "mini"];
  for (const tier of tiers) {
    if (tTarget.includes(tier) !== tCand.includes(tier)) return false;
  }
  const targetPro = !tTarget.includes("pro max") && /\bpro\b/.test(tTarget);
  const candPro = !tCand.includes("pro max") && /\bpro\b/.test(tCand);
  if (targetPro !== candPro) return false;

  const specs = payload.variant_specs || {};
  let targetStorage = specs.storage_gb;
  if (!targetStorage) {
    const sm = tTarget.match(/\b(32|64|128|256|512)\s*gb\b/i);
    if (sm) targetStorage = parseInt(sm[1], 10);
  }
  if (targetStorage) {
    const candStorages = [32, 64, 128, 256, 512, 1024].filter((sz) => {
      const re = sz === 1024 ? /\b(?:1\s*tb|1024\s*gb)\b/i : new RegExp(`\\b${sz}\\s*gb\\b`, "i");
      return re.test(tCand);
    });
    if (candStorages.length > 0 && !candStorages.includes(targetStorage)) return false;
  }
  return true;
}

function pickTargetsForCategory(payload) {
  const t = (payload.title || "").toLowerCase();
  const isFashion = /shirt|t-shirt|jeans|kurta|saree|dress|jacket|sneaker|shoe|sandal|loafer/.test(t);
  const isBeauty = /serum|sunscreen|moisturizer|shampoo|lipstick|perfume|cream|lotion|cleanser/.test(t);
  if (isFashion) return ["amazon_in", "flipkart", "myntra", "ajio", "tatacliq"];
  if (isBeauty) return ["amazon_in", "flipkart", "nykaa", "myntra", "tatacliq"];
  return ["amazon_in", "flipkart", "croma", "reliance_digital", "vijaysales", "tatacliq", "jiomart"];
}

// ── Anti-Ban Offscreen Fallback Fetcher ──────

async function fetchCompetitorsAntiBan(payload) {
  const currentRetailer = payload.retailer;
  const query = buildSearchQuery(payload);
  if (!query || query.length < 3) return [];

  const cached = await getCachedSearch(query);
  if (cached) return cached;

  const targets = pickTargetsForCategory(payload).filter(
    (r) => r !== currentRetailer && SEARCH_URL_BUILDERS[r]
  );

  const quotes = [];
  for (const retailer of targets) {
    const breaker = await isRetailerPaused(retailer);
    if (breaker) {
      quotes.push({
        retailer,
        title: "Paused (Rate Limit Protection)",
        listed_price: null,
        url: "",
        sku: "",
        in_stock: false,
        paused: true,
      });
      continue;
    }

    await sleep(Math.floor(900 + Math.random() * 700));

    let parsedQuotes = [];
    try {
      const resp = await fetch(SEARCH_URL_BUILDERS[retailer](query), {
        method: "GET",
        headers: {
          Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        },
        credentials: "omit",
        signal: AbortSignal.timeout(9000),
      });

      if (resp.status === 403 || resp.status === 429 || resp.status === 503) {
        await triggerCircuitBreaker(retailer, `HTTP ${resp.status}`);
      } else if (resp.ok) {
        const html = await resp.text();
        const lower = html.toLowerCase();
        if (!/enter the characters you see below|captcha|unusual traffic|automated queries|robot check/.test(lower)) {
          parsedQuotes = await parseViaOffscreen(retailer, html);
        }
      }
    } catch (_) {}

    if (!parsedQuotes || parsedQuotes.length === 0) {
      const domain = RETAILER_DOMAINS[retailer];
      if (domain) {
        try {
          const ddgResp = await fetch(
            `https://html.duckduckgo.com/html/?q=site%3A${domain}+${encodeURIComponent(query)}`,
            {
              method: "GET",
              headers: { Accept: "text/html", "Accept-Language": "en-IN,en;q=0.9" },
              credentials: "omit",
              signal: AbortSignal.timeout(9000),
            }
          );
          if (ddgResp.ok) {
            parsedQuotes = await parseDdgViaOffscreen(retailer, domain, await ddgResp.text());
          }
        } catch (_) {}
      }
    }

    if (parsedQuotes && parsedQuotes.length > 0) {
      const valid = parsedQuotes.filter((q) => isVariantMatch(payload, q.title) && q.listed_price > 0);
      if (valid.length > 0) {
        valid[0].source = "extension_offscreen";
        quotes.push(valid[0]);
      }
    }
  }

  await setCachedSearch(query, quotes);
  return quotes;
}

// ── Message Handlers ─────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "ONESTOP_PAYLOAD") {
    handleStageOnePayload(message.payload, sender.tab?.id, message.requestUrl).then(sendResponse);
    return true;
  }
  if (message.type === "ONESTOP_COMPARE_CROSS_STORE") {
    handleStageTwoCompare(message.payload, sender.tab?.id, message.requestUrl).then(sendResponse);
    return true;
  }
  if (message.type === "ONESTOP_ANALYZE_TAB") {
    analyzeActiveTab().then(sendResponse);
    return true;
  }
});

// Stage 1: Auto analysis (Zero extra store requests)
async function handleStageOnePayload(payload, tabId, requestUrl) {
  try {
    if (!payload || !payload.listed_price || payload.listed_price <= 0) {
      return { success: false, error: "No valid product price detected on this page." };
    }
    const storage = await chrome.storage.local.get(["userCardIds"]);
    const userCardIds = storage.userCardIds || [];

    const resp = await fetch(`${API_BASE}/analyze-client-payload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        payload,
        competitor_quotes: [],
        user_card_ids: userCardIds,
      }),
      signal: AbortSignal.timeout(45000),
    });

    if (!resp.ok) {
      const errText = await resp.text().catch(() => "Unknown error");
      const result = { success: false, error: `Backend returned ${resp.status}: ${errText.substring(0, 200)}`, payload };
      if (tabId) chrome.tabs.sendMessage(tabId, { type: "ONESTOP_RESULT", data: result, requestUrl });
      return result;
    }

    const analysis = await resp.json();
    const result = { success: true, analysis, payload, competitor_quotes: [] };
    if (tabId) chrome.tabs.sendMessage(tabId, { type: "ONESTOP_RESULT", data: result, requestUrl });
    return result;
  } catch (err) {
    const isTimeout = err.name === "TimeoutError" || err.name === "AbortError" || /timed out|abort/i.test(err.message || "");
    const errorMsg = isTimeout
      ? "Backend took too long—try again or check uvicorn logs."
      : err.message || "Network error — is OneStop backend running on localhost:8000?";
    const result = { success: false, error: errorMsg, payload };
    if (tabId) chrome.tabs.sendMessage(tabId, { type: "ONESTOP_RESULT", data: result, requestUrl });
    return result;
  }
}

// Stage 2: Universal SearXNG + Scrapling Discovery with Offscreen Anti-Ban Fallback
async function handleStageTwoCompare(payload, tabId, requestUrl) {
  try {
    let discoveredQuotes = [];
    // 1. Primary: Backend SearXNG + 3-Tier Scrapling + RapidFuzz discovery
    try {
      const discResp = await fetch(`${API_BASE}/discover-stores`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload, max_stores: 8 }),
        signal: AbortSignal.timeout(45000),
      });
      if (discResp.ok) {
        const discData = await discResp.json();
        discoveredQuotes = discData.quotes || [];
      }
    } catch (discErr) {
      console.warn("[OneStop] Backend discover-stores unavailable, falling back to offscreen:", discErr.message);
    }

    // 2. Fallback: Anti-ban offscreen fetch if backend returned 0 quotes
    if (discoveredQuotes.length === 0) {
      discoveredQuotes = await fetchCompetitorsAntiBan(payload);
    }

    // Deduplicate by URL
    const seenUrls = new Set();
    const mergedQuotes = [];
    for (const q of discoveredQuotes) {
      const key = (q.url || q.retailer || "").toLowerCase();
      if (!key || seenUrls.has(key)) continue;
      seenUrls.add(key);
      mergedQuotes.push(q);
    }

    const storage = await chrome.storage.local.get(["userCardIds"]);
    const userCardIds = storage.userCardIds || [];
    const validQuotes = mergedQuotes.filter((q) => !q.paused && q.listed_price && q.listed_price > 0);

    const resp = await fetch(`${API_BASE}/analyze-client-payload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        payload,
        competitor_quotes: validQuotes,
        user_card_ids: userCardIds,
      }),
      signal: AbortSignal.timeout(45000),
    });

    const updatedAnalysis = resp.ok ? await resp.json() : null;
    const result = { success: true, quotes: mergedQuotes, analysis: updatedAnalysis };
    if (tabId) chrome.tabs.sendMessage(tabId, { type: "ONESTOP_COMPETITOR_RESULTS", data: result, requestUrl });
    return result;
  } catch (err) {
    const isTimeout = err.name === "TimeoutError" || err.name === "AbortError" || /timed out|abort/i.test(err.message || "");
    const errorMsg = isTimeout
      ? "Backend took too long—try again or check uvicorn logs."
      : err.message || "Could not compare across stores";
    const result = { success: false, error: errorMsg, quotes: [] };
    if (tabId) chrome.tabs.sendMessage(tabId, { type: "ONESTOP_COMPETITOR_RESULTS", data: result, requestUrl });
    return result;
  }
}

// Popup universal trigger
async function analyzeActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return { success: false, error: "No active tab" };

    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["dom_extractor.js"],
    });

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => (typeof window.OneStopExtractDOM === "function" ? window.OneStopExtractDOM() : null),
    });

    const payload = results && results[0] && results[0].result;
    if (!payload) return { success: false, error: "Could not extract product data from this page." };
    if (!payload.listed_price || payload.listed_price <= 0) {
      return { success: false, error: "No valid product price detected on this page." };
    }
    return handleStageOnePayload(payload, tab.id, tab.url);
  } catch (err) {
    return { success: false, error: err.message };
  }
}
