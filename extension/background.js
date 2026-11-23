/**
 * OneStop Background Service Worker (Manifest V3)
 *
 * Implements strict Anti-Ban & Account Protection:
 * 1. Two-Stage Execution:
 *    - Stage 1 (Auto): Reads current tab DOM only. 0 extra network requests!
 *    - Stage 2 (On-Demand): Cross-store fetch ONLY when user clicks "Compare Live Across Stores".
 * 2. MV3 Offscreen API for safe DOM parsing (no DOMParser in service worker).
 * 3. Rate-Limit & IP Protection:
 *    - Sequential fetch with 900ms–1600ms random jitter (never parallel bursts).
 *    - { credentials: "omit" } so user session cookies are never transmitted.
 *    - 6-Hour search cache in chrome.storage.local.
 *    - 30-Minute per-retailer circuit breaker in chrome.storage.local on 403/429/503/CAPTCHA.
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
};

const RETAILER_DOMAINS = {
  amazon_in: "amazon.in",
  flipkart: "flipkart.com",
  croma: "croma.com",
  reliance_digital: "reliancedigital.in",
  vijaysales: "vijaysales.com",
  tatacliq: "tatacliq.com",
};

// ── Sleep with jitter ────────────────────────

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ── Offscreen Document Management ────────────

async function ensureOffscreenDocument() {
  if (chrome.offscreen) {
    if (await chrome.offscreen.hasDocument?.()) {
      return;
    }
    const contexts = await chrome.runtime.getContexts?.({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
    });
    if (contexts && contexts.length > 0) {
      return;
    }
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
    chrome.runtime.sendMessage(
      { type: "PARSE_RETAILER_HTML", retailer, html },
      (response) => {
        if (chrome.runtime.lastError) {
          console.warn("[OneStop] Offscreen parsing error:", chrome.runtime.lastError.message);
          resolve([]);
        } else {
          resolve(response?.quotes || []);
        }
      }
    );
  });
}

async function parseDdgViaOffscreen(retailer, domain, html) {
  await ensureOffscreenDocument();
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      { type: "PARSE_DDG_HTML", retailer, domain, html },
      (response) => {
        if (chrome.runtime.lastError) {
          console.warn("[OneStop] DDG Offscreen error:", chrome.runtime.lastError.message);
          resolve([]);
        } else {
          resolve(response?.quotes || []);
        }
      }
    );
  });
}

// ── Circuit Breakers & Cache in chrome.storage.local ──

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
  console.warn(`[OneStop] Circuit breaker tripped for ${retailer} until ${new Date(breakers[retailer].pausedUntil).toLocaleTimeString()}`);
}

async function isRetailerPaused(retailer) {
  const breakers = await getCircuitBreakers();
  const info = breakers[retailer];
  if (!info) return false;
  if (Date.now() < info.pausedUntil) {
    return info;
  }
  // Expired
  delete breakers[retailer];
  await chrome.storage.local.set({ circuitBreakers: breakers });
  return false;
}

async function getCachedSearch(query) {
  const store = await chrome.storage.local.get(["searchCache"]);
  const cache = store.searchCache || {};
  const entry = cache[query];
  if (entry && (Date.now() - entry.timestamp < CACHE_TTL_MS)) {
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

// ── Build Search Query ───────────────────────

function buildSearchQuery(payload) {
  const parts = [];
  if (payload.brand) parts.push(payload.brand);

  const titleWords = (payload.title || "")
    .replace(/\(.*?\)/g, " ")
    .replace(/[^a-zA-Z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 1 && !/^(with|and|the|for|in|on|of|a|an|to|by)$/i.test(w));

  parts.push(...titleWords.slice(0, 5));

  const specs = payload.variant_specs || {};
  if (specs.storage_gb) parts.push(specs.storage_gb + "GB");
  if (specs.ram_gb) parts.push(specs.ram_gb + "GB");

  return parts.join(" ").trim().substring(0, 100);
}

function isVariantMatch(payload, candidateTitle) {
  if (!candidateTitle) return false;
  const tCand = candidateTitle.toLowerCase();
  const tTarget = (payload.title || "").toLowerCase();

  // 1. Accessory check: reject if candidate is an accessory but target is a device
  const accessories = ["case", "cover", "screen protector", "tempered glass", "skin", "cable", "adapter", "charger", "pouch", "strap"];
  const targetHasAcc = accessories.some((a) => tTarget.includes(a));
  const candHasAcc = accessories.some((a) => tCand.includes(a));
  if (candHasAcc && !targetHasAcc) return false;

  // 2. Modifiers check: Pro Max, Pro, Ultra, Plus, Mini
  const targetIsProMax = tTarget.includes("pro max") || tTarget.includes("pro-max");
  const candIsProMax = tCand.includes("pro max") || tCand.includes("pro-max");
  if (targetIsProMax !== candIsProMax) return false;

  const targetIsUltra = tTarget.includes("ultra");
  const candIsUltra = tCand.includes("ultra");
  if (targetIsUltra !== candIsUltra) return false;

  const targetIsPlus = tTarget.includes("plus");
  const candIsPlus = tCand.includes("plus");
  if (targetIsPlus !== candIsPlus) return false;

  const targetIsPro = !targetIsProMax && (tTarget.includes("pro") || tTarget.includes(" pro "));
  const candIsPro = !candIsProMax && (tCand.includes("pro") || tCand.includes(" pro "));
  if (targetIsPro !== candIsPro) return false;

  // 3. Storage check: if target specifies 128GB, reject candidates with 256GB, 512GB, 64GB, 1TB
  const specs = payload.variant_specs || {};
  let targetStorage = specs.storage_gb;
  if (!targetStorage) {
    const sm = tTarget.match(/\b(32|64|128|256|512)\s*(?:gb)?\b/i);
    if (sm) targetStorage = parseInt(sm[1], 10);
  }
  if (targetStorage) {
    const candStorages = [32, 64, 128, 256, 512, 1024].filter((sz) => {
      const re = sz === 1024 ? /\b(?:1\s*tb|1024\s*gb)\b/i : new RegExp(`\\b${sz}\\s*gb\\b`, "i");
      return re.test(tCand);
    });
    if (candStorages.length > 0 && !candStorages.includes(targetStorage)) {
      return false; // Storage mismatch!
    }
  }

  return true;
}

// ── Anti-Ban Cross-Store Fetcher (Sequential with Jitter & Search Fallback) ──

async function fetchCompetitorsAntiBan(payload) {
  const currentRetailer = payload.retailer;
  const query = buildSearchQuery(payload);
  if (!query || query.length < 3) return [];

  // Check 6-hour cache
  const cached = await getCachedSearch(query);
  if (cached) {
    console.log("[OneStop] Serving cross-store quotes from 6-hour cache:", query);
    return cached;
  }

  const targets = [
    "amazon_in",
    "flipkart",
    "croma",
    "reliance_digital",
    "vijaysales",
    "tatacliq",
  ].filter((r) => r !== currentRetailer && SEARCH_URL_BUILDERS[r]);

  const quotes = [];

  // Execute sequentially with 900ms–1600ms random jitter
  for (const retailer of targets) {
    // Check circuit breaker
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

    // Anti-ban random jitter
    const jitterMs = Math.floor(900 + Math.random() * 700);
    await sleep(jitterMs);

    let parsedQuotes = [];
    const url = SEARCH_URL_BUILDERS[retailer](query);
    try {
      const resp = await fetch(url, {
        method: "GET",
        headers: {
          Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        },
        credentials: "omit", // NEVER send logged-in cookies
        signal: AbortSignal.timeout(9000),
      });

      if (resp.status === 403 || resp.status === 429 || resp.status === 503) {
        await triggerCircuitBreaker(retailer, `HTTP ${resp.status}`);
      } else if (resp.ok) {
        const html = await resp.text();
        const lower = html.toLowerCase();
        if (
          !lower.includes("enter the characters you see below") &&
          !lower.includes("captcha") &&
          !lower.includes("unusual traffic") &&
          !lower.includes("automated queries") &&
          !lower.includes("robot check")
        ) {
          parsedQuotes = await parseViaOffscreen(retailer, html);
        }
      }
    } catch (err) {
      console.warn(`[OneStop] Direct search for ${retailer} skipped:`, err.message);
    }

    // Fallback to DuckDuckGo search if direct search yielded no quotes
    if (!parsedQuotes || parsedQuotes.length === 0) {
      const domain = RETAILER_DOMAINS[retailer];
      if (domain) {
        try {
          const ddgUrl = `https://html.duckduckgo.com/html/?q=site%3A${domain}+${encodeURIComponent(query)}`;
          const ddgResp = await fetch(ddgUrl, {
            method: "GET",
            headers: {
              Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
              "Accept-Language": "en-IN,en;q=0.9",
            },
            credentials: "omit",
            signal: AbortSignal.timeout(9000),
          });
          if (ddgResp.ok) {
            const ddgHtml = await ddgResp.text();
            parsedQuotes = await parseDdgViaOffscreen(retailer, domain, ddgHtml);
          }
        } catch (ddgErr) {
          console.warn(`[OneStop] DDG search fallback for ${retailer} failed:`, ddgErr.message);
        }
      }
    }

    // Filter candidate quotes with strict variant matching
    if (parsedQuotes && parsedQuotes.length > 0) {
      const validForRetailer = parsedQuotes.filter((q) => isVariantMatch(payload, q.title));
      if (validForRetailer.length > 0) {
        quotes.push(validForRetailer[0]); // Pick top verified matching quote
      }
    }
  }

  // Cache results
  await setCachedSearch(query, quotes);
  return quotes;
}

// ── Message Handlers ─────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "ONESTOP_PAYLOAD") {
    // Stage 1 (Auto): Extract current tab only, 0 extra network calls!
    handleStageOnePayload(message.payload, sender.tab?.id).then(sendResponse);
    return true;
  }

  if (message.type === "ONESTOP_COMPARE_CROSS_STORE") {
    // Stage 2 (On-Demand): User explicitly clicked "Compare Live Across Stores"
    handleStageTwoCompare(message.payload, sender.tab?.id).then(sendResponse);
    return true;
  }

  if (message.type === "ONESTOP_ANALYZE_TAB") {
    analyzeActiveTab().then(sendResponse);
    return true;
  }
});

// Stage 1: Auto analysis (Zero extra requests)
async function handleStageOnePayload(payload, tabId) {
  try {
    const storage = await chrome.storage.local.get(["userCardIds"]);
    const userCardIds = storage.userCardIds || [];

    const body = {
      payload: payload,
      competitor_quotes: [], // No competitor quotes in stage 1
      user_card_ids: userCardIds,
    };

    const resp = await fetch(`${API_BASE}/analyze-client-payload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(45000),
    });

    if (!resp.ok) {
      const errText = await resp.text().catch(() => "Unknown error");
      const result = {
        success: false,
        error: `Backend returned ${resp.status}: ${errText.substring(0, 200)}`,
        payload: payload,
      };
      if (tabId) {
        chrome.tabs.sendMessage(tabId, { type: "ONESTOP_RESULT", data: result });
      }
      return result;
    }

    const analysis = await resp.json();
    const result = {
      success: true,
      analysis: analysis,
      payload: payload,
      competitor_quotes: [],
    };

    if (tabId) {
      chrome.tabs.sendMessage(tabId, { type: "ONESTOP_RESULT", data: result });
    }
    return result;
  } catch (err) {
    const isTimeout =
      err.name === "TimeoutError" ||
      err.name === "AbortError" ||
      /timed out|abort/i.test(err.message || "");
    const errorMsg = isTimeout
      ? "Backend took too long—try again or check uvicorn logs."
      : (err.message || "Network error — is OneStop backend running on localhost:8000?");

    const result = {
      success: false,
      error: errorMsg,
      payload: payload,
    };
    if (tabId) {
      chrome.tabs.sendMessage(tabId, { type: "ONESTOP_RESULT", data: result });
    }
    return result;
  }
}

// Stage 2: On-demand cross-store compare
async function handleStageTwoCompare(payload, tabId) {
  try {
    const quotes = await fetchCompetitorsAntiBan(payload);

    const storage = await chrome.storage.local.get(["userCardIds"]);
    const userCardIds = storage.userCardIds || [];

    // Filter out paused dummy entries before sending to backend
    const validQuotes = quotes.filter((q) => !q.paused && q.listed_price);

    const body = {
      payload: payload,
      competitor_quotes: validQuotes,
      user_card_ids: userCardIds,
    };

    const resp = await fetch(`${API_BASE}/analyze-client-payload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(45000),
    });

    let updatedAnalysis = null;
    if (resp.ok) {
      updatedAnalysis = await resp.json();
    }

    const result = {
      success: true,
      quotes: quotes,
      analysis: updatedAnalysis,
    };

    if (tabId) {
      chrome.tabs.sendMessage(tabId, { type: "ONESTOP_COMPETITOR_RESULTS", data: result });
    }
    return result;
  } catch (err) {
    const isTimeout =
      err.name === "TimeoutError" ||
      err.name === "AbortError" ||
      /timed out|abort/i.test(err.message || "");
    const errorMsg = isTimeout
      ? "Backend took too long—try again or check uvicorn logs."
      : (err.message || "Could not fetch competitor quotes");

    const result = {
      success: false,
      error: errorMsg,
      quotes: [],
    };
    if (tabId) {
      chrome.tabs.sendMessage(tabId, { type: "ONESTOP_COMPETITOR_RESULTS", data: result });
    }
    return result;
  }
}

// Popup universal trigger
async function analyzeActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return { success: false, error: "No active tab" };

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["dom_extractor.js"],
    });

    if (results && results[0] && results[0].result) {
      const payload = typeof results[0].result === "string"
        ? JSON.parse(results[0].result)
        : results[0].result;

      if (payload.error) {
        return { success: false, error: payload.error };
      }

      return handleStageOnePayload(payload, tab.id);
    }
    return { success: false, error: "Extraction returned no data" };
  } catch (err) {
    return { success: false, error: err.message };
  }
}
