/**
 * OneStop Content Script (Manifest V3 — Universal All-Rounder)
 *
 * 1. Zero-network `isProductPage()` heuristic: activates ONLY on genuine e-commerce
 *    product pages across any store (Amazon, Flipkart, Myntra, Ajio, Nykaa, Croma,
 *    Reliance Digital, Vijay Sales, Tata CLiQ, JioMart, Shopify/WooCommerce D2C),
 *    never on Google, YouTube, Gmail, Reddit, news, or search/category pages.
 * 2. SPA Route Watcher (`pushState`/`replaceState`/`popstate` + debounced MutationObserver)
 *    with up to 2.5s hydration wait and stale-response cancellation.
 * 3. Unclipped closed Shadow DOM Drawer with strict Payable Today vs. Post-Purchase
 *    Points transparency, All-Stores comparison ("Compare All Stores"), Seller Forensics,
 *    AI Executive Summary, and XSS-safe HTML escaping.
 */
(function () {
  "use strict";

  if (window.__OneStopContentLoaded) return;
  window.__OneStopContentLoaded = true;

  const BLACKLISTED_HOSTS = [
    "google.com", "google.co.in", "youtube.com", "youtu.be", "mail.google.com",
    "docs.google.com", "drive.google.com", "calendar.google.com", "meet.google.com",
    "reddit.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "linkedin.com", "whatsapp.com", "web.whatsapp.com", "telegram.org",
    "wikipedia.org", "github.com", "gitlab.com", "stackoverflow.com",
    "ndtv.com", "timesofindia.indiatimes.com", "hindustantimes.com",
    "indianexpress.com", "bbc.com", "cnn.com", "netflix.com", "hotstar.com",
    "primevideo.com", "spotify.com", "chatgpt.com", "claude.ai", "gemini.google.com",
  ];

  const NON_PRODUCT_PATHS = [
    "/cart", "/checkout", "/orders", "/my-orders", "/account", "/myaccount",
    "/login", "/signin", "/signup", "/register", "/search", "/s?", "/s/",
    "/all-categories", "/wishlist", "/bag", "/smart-wagon", "/help", "/support",
  ];

  const RETAILER_LABELS = {
    amazon_in: "Amazon.in",
    flipkart: "Flipkart",
    croma: "Croma",
    reliance_digital: "Reliance Digital",
    vijaysales: "Vijay Sales",
    tatacliq: "Tata CLiQ",
    jiomart: "JioMart",
    myntra: "Myntra",
    ajio: "Ajio",
    nykaa: "Nykaa",
    meesho: "Meesho",
    snapdeal: "Snapdeal",
    pepperfry: "Pepperfry",
    lenskart: "Lenskart",
    boat: "boAt Official",
    lg_official: "LG India Official",
    samsung_official: "Samsung India Official",
    apple_official: "Apple Store India",
    sony_official: "Sony India Official",
    nike_official: "Nike India Official",
    adidas_official: "Adidas India Official",
  };

  // ── Safe Escaping & Formatting ───────────

  function esc(val) {
    return String(val == null ? "" : val)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeHref(url) {
    if (!url || typeof url !== "string") return "";
    const trimmed = url.trim();
    return /^https?:\/\//i.test(trimmed) ? esc(trimmed) : "";
  }

  function fmt(n) {
    if (n == null || isNaN(Number(n))) return "N/A";
    return "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }

  function retailerLabel(r) {
    return RETAILER_LABELS[r] || String(r || "Store").replace(/_/g, " ");
  }

  // ── Zero-Network Product Page Detector ───

  function hasJsonLdProduct() {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (let i = 0; i < scripts.length; i++) {
      const txt = scripts[i].textContent || "";
      if (/"@type"\s*:\s*(?:"Product"|\[[^\]]*"Product")/i.test(txt)) {
        return true;
      }
    }
    return false;
  }

  function isProductPage() {
    const host = (window.location.hostname || "").toLowerCase();
    const port = window.location.port || "";
    const pathAndQuery = (window.location.pathname + window.location.search).toLowerCase();

    // Never activate on OneStop's own dashboard or non-shopping hosts
    if ((host === "localhost" || host === "127.0.0.1") && (port === "3000" || port === "8000" || port === "8080")) {
      return false;
    }
    if (BLACKLISTED_HOSTS.some((b) => host === b || host.endsWith("." + b))) {
      return false;
    }
    if (NON_PRODUCT_PATHS.some((p) => pathAndQuery.includes(p))) {
      return false;
    }
    // Root homepage without product slug
    if (window.location.pathname === "/" || window.location.pathname === "") {
      return false;
    }

    // Signal 1: JSON-LD @type: Product
    if (hasJsonLdProduct()) return true;

    // Signal 2: OpenGraph / product price meta
    const ogType = (document.querySelector('meta[property="og:type"]')?.getAttribute("content") || "").toLowerCase();
    if (ogType === "product" || ogType === "og:product" || document.querySelector('meta[property="product:price:amount"]')) {
      return true;
    }

    // Signal 3: Visible Buy Now / Add to Cart / Add to Bag button + visible ₹/Rs price
    const btns = document.querySelectorAll("button, input[type='submit'], a[role='button']");
    let hasBuyButton = false;
    for (let i = 0; i < Math.min(btns.length, 120); i++) {
      const t = (btns[i].textContent || btns[i].value || "").trim();
      if (/^(add\s+to\s+(cart|bag)|buy\s+now|shop\s+now)$/i.test(t) || /add\s+to\s+(cart|bag)|buy\s+now/i.test(t)) {
        if (t.length < 36) {
          hasBuyButton = true;
          break;
        }
      }
    }
    const bodyHead = (document.body?.innerText || "").substring(0, 8000);
    const hasPrice = /(?:₹|Rs\.?|INR)\s*[\d,]{2,}/i.test(bodyHead);
    if (hasBuyButton && hasPrice) return true;

    // Signal 4: Known product URL patterns combined with a price
    if (/\/(?:dp|gp\/product|p|product|products|pd|item)\/|\/\d{5,12}\/buy/i.test(window.location.pathname) && hasPrice) {
      return true;
    }

    return false;
  }

  // ── Shadow DOM Host (lazy-shown only on product pages) ──

  let currentPayload = null;
  let currentAnalysis = null;
  let competitorQuotes = null;
  let quickCommerceQuotes = [];
  let isComparing = false;
  let streamProgressMsg = "";
  let userPincode = "411001";
  let storeSortMode = "cheapest"; // "cheapest" | "fastest"
  let activeCompareEventSource = null;
  let activeUrl = "";
  let activeRequestId = 0;
  let host = null;
  let shadow = null;
  let pill = null;
  let drawer = null;

  try {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
      chrome.storage.local.get(["onestop_pincode"], (res) => {
        if (res && res.onestop_pincode && /^\d{6}$/.test(String(res.onestop_pincode))) {
          userPincode = String(res.onestop_pincode);
        }
      });
    }
  } catch (_) {}

  function ensureWidget() {
    if (host) return;

    host = document.createElement("div");
    host.id = "onestop-root";
    host.style.cssText =
      "all:initial;position:fixed;bottom:20px;right:20px;z-index:2147483647;pointer-events:none;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:none;";
    document.body.appendChild(host);

    shadow = host.attachShadow({ mode: "closed" });

    const style = document.createElement("style");
    style.textContent = `
      :host {
        --bg: #FAF9F6;
        --surface: #FFFFFF;
        --surface-2: #F3F1EC;
        --border: #E7E4DC;
        --text: #1C1B18;
        --muted: #6B675F;
        --accent: #0F5C47;
        --accent-hover: #0B4A39;
        --accent-subtle: #E6F0EC;
        --accent-fg: #FAF9F6;
        --warn: #B7791F;
        --warn-subtle: #FBF4E4;
        --danger: #B4412E;
        --danger-subtle: #FAECE9;
        all: initial;
        font-family: "Geist", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: var(--text);
      }
      @media (prefers-color-scheme: dark) {
        :host {
          --bg: #121210;
          --surface: #1A1A17;
          --surface-2: #22211E;
          --border: #2A2925;
          --text: #EDEBE4;
          --muted: #9A968C;
          --accent: #4FB08C;
          --accent-hover: #62BE9B;
          --accent-subtle: #1B2B25;
          --accent-fg: #121210;
          --warn: #D99B38;
          --warn-subtle: #282115;
          --danger: #DC6A56;
          --danger-subtle: #2B1A17;
        }
      }
      * { box-sizing: border-box; margin: 0; padding: 0; }

      .os-wrapper {
        display: flex;
        flex-direction: column-reverse;
        align-items: flex-end;
        gap: 10px;
        max-height: calc(100vh - 32px);
        pointer-events: none;
      }

      .os-pill {
        pointer-events: auto;
        background: var(--accent);
        color: var(--accent-fg);
        min-height: 44px;
        padding: 10px 16px;
        border-radius: 999px;
        cursor: pointer;
        font-size: 13px;
        font-weight: 500;
        font-variant-numeric: tabular-nums;
        box-shadow: 0 4px 16px rgba(28, 27, 24, 0.14);
        display: flex;
        align-items: center;
        gap: 8px;
        transition: background 150ms ease, transform 150ms ease;
        user-select: none;
        flex-shrink: 0;
        border: 1px solid var(--accent);
      }
      .os-pill:hover { background: var(--accent-hover); transform: translateY(-1px); }
      .os-pill.os-loading { opacity: 0.9; }
      .os-pill .os-pill-text { white-space: nowrap; }

      .os-drawer {
        pointer-events: auto;
        display: none;
        flex-direction: column;
        width: 390px;
        max-width: min(390px, calc(100vw - 32px));
        max-height: calc(100vh - 100px);
        background: var(--bg);
        color: var(--text);
        border-radius: 12px;
        box-shadow: 0 4px 20px rgba(28, 27, 24, 0.14);
        border: 1px solid var(--border);
        font-size: 13px;
        overflow: hidden;
      }
      .os-drawer.os-open { display: flex; }

      .os-header {
        background: var(--surface);
        color: var(--text);
        padding: 12px 16px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-shrink: 0;
        gap: 8px;
        border-bottom: 1px solid var(--border);
      }
      .os-header-title { font-weight: 600; font-size: 14px; min-width: 0; overflow-wrap: anywhere; }
      .os-close {
        background: transparent;
        border: 1px solid transparent;
        color: var(--muted);
        width: 32px;
        height: 32px;
        border-radius: 8px;
        cursor: pointer;
        font-size: 16px;
        flex-shrink: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 150ms ease, color 150ms ease;
      }
      .os-close:hover { background: var(--surface-2); color: var(--text); }

      .os-body {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
        overflow-x: hidden;
        background: var(--bg);
        color: var(--text);
      }

      .os-section { padding: 14px 16px; border-bottom: 1px solid var(--border); overflow-wrap: anywhere; }
      .os-section:last-child { border-bottom: none; }

      .os-verdict-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
      .os-verdict-reason { font-size: 13px; color: var(--text); line-height: 1.45; }

      .os-price-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        margin-top: 12px;
      }
      .os-price-box {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 10px 12px;
      }
      .os-price-box.os-best {
        border-color: var(--accent);
        background: var(--accent-subtle);
      }
      .os-price-label { font-size: 11px; color: var(--muted); font-weight: 500; }
      .os-price-val {
        font-family: "Geist Mono", ui-monospace, monospace;
        font-variant-numeric: tabular-nums;
        font-size: 18px;
        font-weight: 600;
        color: var(--text);
        margin-top: 2px;
      }
      .os-price-box.os-best .os-price-val { color: var(--accent); }
      .os-price-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }

      .os-badge {
        display: inline-block;
        font-size: 11px;
        font-weight: 500;
        padding: 2px 8px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--surface-2);
        color: var(--muted);
      }
      .os-badge-accent { background: var(--accent-subtle); color: var(--accent); border-color: var(--accent); }
      .os-badge-warn { background: var(--warn-subtle); color: var(--warn); border-color: var(--warn); }
      .os-badge-danger { background: var(--danger-subtle); color: var(--danger); border-color: var(--danger); }

      .os-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }

      .os-btn-compare {
        width: 100%;
        min-height: 44px;
        padding: 10px 14px;
        background: var(--accent);
        color: var(--accent-fg);
        border: 1px solid var(--accent);
        border-radius: 8px;
        font-weight: 500;
        font-size: 13px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        transition: background 150ms ease;
      }
      .os-btn-compare:hover { background: var(--accent-hover); }
      .os-btn-compare:disabled { opacity: 0.6; cursor: not-allowed; }

      .os-table-wrap { width: 100%; overflow-x: auto; margin-top: 10px; }
      .os-table { width: 100%; border-collapse: collapse; font-size: 12px; }
      .os-table th, .os-table td { text-align: left; padding: 7px 6px; border-bottom: 1px solid var(--border); }
      .os-table th { color: var(--muted); font-weight: 500; font-size: 11px; }
      .os-table tr.os-win { background: var(--accent-subtle); }
      .os-mono { font-family: "Geist Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; font-weight: 500; }

      .os-link { color: var(--accent); text-decoration: none; font-weight: 500; }
      .os-link:hover { text-decoration: underline; }

      .os-dashboard-link {
        display: block;
        width: 100%;
        text-align: center;
        padding: 12px;
        background: var(--surface);
        color: var(--accent);
        text-decoration: none;
        font-weight: 500;
        font-size: 13px;
        border-top: 1px solid var(--border);
        flex-shrink: 0;
        transition: background 150ms ease;
      }
      .os-dashboard-link:hover { background: var(--surface-2); }

      .os-error { padding: 16px; color: var(--danger); font-size: 13px; }
      .os-btn-retry {
        margin-top: 10px;
        min-height: 40px;
        padding: 8px 14px;
        background: var(--accent);
        color: var(--accent-fg);
        border: 1px solid var(--accent);
        border-radius: 8px;
        font-weight: 500;
        font-size: 13px;
        cursor: pointer;
      }
      .spinner {
        display: inline-block;
        width: 12px;
        height: 12px;
        border: 2px solid rgba(255,255,255,0.35);
        border-top: 2px solid currentColor;
        border-radius: 50%;
        animation: osSpin 0.8s linear infinite;
      }
      @keyframes osSpin { to { transform: rotate(360deg); } }
    `;
    shadow.appendChild(style);

    const wrapper = document.createElement("div");
    wrapper.className = "os-wrapper";
    wrapper.innerHTML = `
      <div class="os-pill os-loading" id="osPill">
        <span class="os-pill-text">OneStop · Checking...</span>
      </div>
      <div class="os-drawer" id="osDrawer"></div>
    `;
    shadow.appendChild(wrapper);

    pill = shadow.getElementById("osPill");
    drawer = shadow.getElementById("osDrawer");

    pill.addEventListener("click", () => {
      drawer.classList.toggle("os-open");
    });
  }

  function hideWidget() {
    if (host) host.style.display = "none";
    if (drawer) drawer.classList.remove("os-open");
  }

  function showWidgetLoading() {
    ensureWidget();
    host.style.display = "block";
    pill.classList.add("os-loading");
    pill.querySelector(".os-pill-text").textContent = "OneStop Analyzing...";
  }

  // ── Render Drawer ────────────────────────

  function verdictMeta(v) {
    switch (v) {
      case "BUY_NOW":
        return { label: "Buy now", cls: "os-badge-accent" };
      case "WAIT":
        return { label: "Wait", cls: "os-badge-warn" };
      case "OVERPRICED":
        return { label: "Overpriced", cls: "os-badge-danger" };
      default:
        return { label: "Fair price", cls: "" };
    }
  }

  function renderResult(data) {
    ensureWidget();
    host.style.display = "block";
    pill.classList.remove("os-loading");

    if (!data || !data.success || !data.analysis) {
      pill.querySelector(".os-pill-text").textContent = "OneStop · Retry";
      drawer.innerHTML = `
        <div class="os-header">
          <span class="os-header-title">OneStop</span>
          <button class="os-close" id="osClose" aria-label="Close">×</button>
        </div>
        <div class="os-body">
          <div class="os-error">
            <div style="font-weight:600;margin-bottom:4px;">Couldn't check this product</div>
            <div style="line-height:1.5;color:var(--muted);">${esc(data?.error || "Make sure the OneStop backend is running on localhost:8000.")}</div>
            <button class="os-btn-retry" id="osBtnRetry">Retry</button>
          </div>
        </div>
      `;
      shadow.getElementById("osClose")?.addEventListener("click", () => drawer.classList.remove("os-open"));
      shadow.getElementById("osBtnRetry")?.addEventListener("click", () => runExtractionForCurrentPage(true));
      return;
    }

    const a = data.analysis;
    const cvp = a.cash_vs_points || null;
    const bw = a.buy_wait || null;

    const priceToday = cvp ? cvp.checkout_payable_inr : a.listed_price;
    const bestPrice =
      a.llm_summary?.final_net_price ||
      cvp?.best_user_card_net_price_cash ||
      priceToday;
    const bestStore = a.llm_summary?.best_store ? retailerLabel(a.llm_summary.best_store) : retailerLabel(currentPayload?.retailer);

    pill.querySelector(".os-pill-text").textContent = `OneStop · ${fmt(bestPrice)}`;

    const vm = verdictMeta(bw?.verdict || "FAIR_PRICE");
    const reasonText =
      (bw?.reasoning || a.llm_summary?.why_this_wins || "Current price is within the usual range.").split(/(?<=[.!?])\s+/)[0];

    let bodyHtml = `
      <div class="os-section">
        <div class="os-verdict-row">
          <span class="os-badge ${vm.cls}">${esc(vm.label)}</span>
          <span style="font-size:11px;color:var(--muted);">${esc(bestStore)}</span>
        </div>
        <div class="os-verdict-reason">${esc(reasonText)}</div>

        <div class="os-price-grid">
          <div class="os-price-box">
            <div class="os-price-label">Price today</div>
            <div class="os-price-val">${fmt(priceToday)}</div>
            <div class="os-price-sub">${cvp?.instant_bank_discount_inr > 0 ? `Includes -${fmt(cvp.instant_bank_discount_inr)} off` : "At checkout"}</div>
          </div>
          <div class="os-price-box os-best">
            <div class="os-price-label" style="color:var(--accent);">Best price</div>
            <div class="os-price-val">${fmt(bestPrice)}</div>
            <div class="os-price-sub">${bestPrice < priceToday ? "After card rewards" : "Lowest verified"}</div>
          </div>
        </div>
      </div>
    `;

    // ── Compare Across Stores ──
    const ranked = (a.ranked_store_choices || []).filter((s) => s.store_bucket !== "variant_mismatch");
    const qcList =
      quickCommerceQuotes && quickCommerceQuotes.length > 0
        ? quickCommerceQuotes
        : a.quick_commerce_quotes || [];

    const combinedRows = [
      ...ranked.map((st) => ({
        store: st.store_name || retailerLabel(st.retailer),
        price: st.best_user_card_net_price || st.effective_price || st.listed_price,
        eta: st.in_stock === false ? "Out of stock" : st.eta_label || "2–4 days",
        inStock: st.in_stock !== false,
        url: st.url,
        isCurrent: !!st.is_current_store,
        isBest: !!st.is_overall_winner && st.in_stock !== false,
      })),
      ...qcList.map((qc) => ({
        store: `${qc.app_name} (10-min)`,
        price: qc.total_payable_inr || qc.selling_price,
        eta: qc.in_stock ? qc.eta_minutes_label || "10 mins" : "Out of stock",
        inStock: qc.in_stock !== false,
        url: qc.url,
        isCurrent: false,
        isBest: false,
      })),
    ].sort((x, y) => {
      if (x.inStock !== y.inStock) return x.inStock ? -1 : 1;
      return (x.price || 9999999) - (y.price || 9999999);
    });

    bodyHtml += `<div class="os-section">
      <div class="os-row" style="margin-bottom:8px;">
        <span style="font-size:13px;font-weight:600;">Stores</span>
        <div style="display:flex;align-items:center;gap:6px;font-size:12px;">
          <span style="color:var(--muted);">Pincode</span>
          <input id="osPincodeInput" type="text" maxlength="6" value="${esc(userPincode)}" style="width:66px;min-height:32px;padding:2px 6px;border:1px solid var(--border);border-radius:6px;background:var(--surface);font-family:'Geist Mono',monospace;font-size:12px;color:var(--text);" />
        </div>
      </div>

      <button class="os-btn-compare" id="osBtnCompare" ${isComparing ? "disabled" : ""}>
        ${isComparing ? '<span class="spinner"></span> Comparing across stores...' : "Compare across stores"}
      </button>

      ${streamProgressMsg ? `<div style="margin-top:8px;font-size:12px;color:var(--muted);">${esc(streamProgressMsg)}</div>` : ""}

      ${combinedRows.length > 0 ? `
        <div class="os-table-wrap">
          <table class="os-table">
            <thead>
              <tr><th>Store</th><th>Delivery</th><th>Price</th><th></th></tr>
            </thead>
            <tbody>
              ${combinedRows.slice(0, 6).map((row, idx) => `
                <tr class="${idx === 0 && row.inStock ? "os-win" : ""}">
                  <td>
                    <span style="font-weight:500;">${esc(row.store)}</span>
                    ${row.isCurrent ? `<span class="os-badge" style="margin-left:4px;">Here</span>` : ""}
                  </td>
                  <td style="color:${row.inStock ? "var(--muted)" : "var(--danger)"};">${esc(row.eta)}</td>
                  <td class="os-mono">${fmt(row.price)}</td>
                  <td style="text-align:right;">${
                    row.isCurrent
                      ? "—"
                      : safeHref(row.url)
                      ? `<a href="${safeHref(row.url)}" target="_blank" rel="noopener noreferrer" class="os-link">Open</a>`
                      : "—"
                  }</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : ""}
    </div>`;

    const dashUrl = a.canonical_product_id
      ? `http://localhost:3000/product/${encodeURIComponent(a.canonical_product_id)}`
      : "";

    drawer.innerHTML = `
      <div class="os-header">
        <span class="os-header-title">OneStop · ${esc(a.brand && a.brand !== "Unknown" ? a.brand : retailerLabel(currentPayload?.retailer))}</span>
        <button class="os-close" id="osClose" aria-label="Close">×</button>
      </div>
      <div class="os-body">${bodyHtml}</div>
      ${dashUrl ? `<a href="${dashUrl}" target="_blank" rel="noopener noreferrer" class="os-dashboard-link">View full analysis</a>` : ""}
    `;

    shadow.getElementById("osClose")?.addEventListener("click", () => drawer.classList.remove("os-open"));
    shadow.getElementById("osSortCheapest")?.addEventListener("click", () => {
      storeSortMode = "cheapest";
      renderResult(data);
    });
    shadow.getElementById("osSortFastest")?.addEventListener("click", () => {
      storeSortMode = "fastest";
      renderResult(data);
    });
    const pinInput = shadow.getElementById("osPincodeInput");
    if (pinInput) {
      pinInput.addEventListener("change", (ev) => {
        const val = String(ev.target.value || "").replace(/\D/g, "");
        if (val.length === 6) {
          userPincode = val;
          try {
            if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
              chrome.storage.local.set({ onestop_pincode: val });
            }
          } catch (_) {}
        }
      });
    }
    shadow.getElementById("osBtnCompare")?.addEventListener("click", () => {
      if (!currentPayload || isComparing) return;
      const pinVal = shadow.getElementById("osPincodeInput")?.value?.replace(/\D/g, "") || userPincode;
      if (pinVal.length === 6) userPincode = pinVal;
      currentPayload.pincode = userPincode;
      isComparing = true;
      streamProgressMsg = `Checking stores & 10-min apps for pincode ${userPincode}...`;
      renderResult(data);
      startLiveSseComparison(data);
    });
  }

  function startLiveSseComparison(data) {
    if (activeCompareEventSource) {
      try { activeCompareEventSource.close(); } catch (_) {}
      activeCompareEventSource = null;
    }
    fetch("http://localhost:8000/api/v1/compare-all-stores", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload: currentPayload, pincode: userPincode }),
    })
      .then((r) => r.json())
      .then((jobInfo) => {
        if (!jobInfo || !jobInfo.job_id) throw new Error("No job_id");
        const es = new EventSource(`http://localhost:8000/api/v1/stream-compare/${encodeURIComponent(jobInfo.job_id)}`);
        activeCompareEventSource = es;

        es.addEventListener("progress", (e) => {
          try {
            const d = JSON.parse(e.data);
            if (d.message) {
              streamProgressMsg = d.message;
              renderResult(data);
            }
          } catch (_) {}
        });

        es.addEventListener("store_quote", (e) => {
          try {
            const d = JSON.parse(e.data);
            const q = d.quote;
            if (q && currentAnalysis) {
              const list = currentAnalysis.ranked_store_choices || [];
              if (!list.some((s) => s.url === q.url || s.retailer === q.retailer)) {
                const cashP = Math.round((q.listed_price || 0) + (q.delivery_fee || 0));
                list.push({
                  retailer: q.retailer,
                  store_name: retailerLabel(q.retailer),
                  url: q.url,
                  listed_price: q.listed_price,
                  checkout_cash_price: cashP,
                  best_user_card_net_price: cashP,
                  effective_price: cashP,
                  in_stock: q.in_stock !== false,
                  is_current_store: false,
                  seller_name: q.seller_name || "Verified Store",
                  peace_of_mind_score: 78,
                  rank_badge: "Live Verified",
                  is_overall_winner: false,
                  store_bucket: q.store_bucket || (q.in_stock === false ? "lesser_known_store" : "exact_match"),
                  variant_diff_badges: q.variant_diff_badges || [],
                  eta_label: q.eta_label || (q.in_stock === false ? "Out of stock" : "2-4 days"),
                });
                currentAnalysis.ranked_store_choices = list;
                streamProgressMsg = `Verified ${retailerLabel(q.retailer)} (${fmt(cashP)})...`;
                renderResult({ success: true, analysis: currentAnalysis, payload: currentPayload });
              }
            }
          } catch (_) {}
        });

        es.addEventListener("quick_commerce_quote", (e) => {
          try {
            const d = JSON.parse(e.data);
            const qc = d.quote;
            if (qc) {
              if (!quickCommerceQuotes.some((x) => x.platform === qc.platform)) {
                quickCommerceQuotes.push(qc);
                streamProgressMsg = `Found ${qc.app_name} (${qc.eta_minutes_label}: ${fmt(qc.total_payable_inr)})...`;
                renderResult({ success: true, analysis: currentAnalysis, payload: currentPayload });
              }
            }
          } catch (_) {}
        });

        es.addEventListener("complete", (e) => {
          isComparing = false;
          try {
            const d = JSON.parse(e.data);
            streamProgressMsg = `Completed: ${d.stores_count || 0} online stores + ${d.quick_commerce_count || 0} quick-commerce apps checked.`;
          } catch (_) {
            streamProgressMsg = "Multi-store & Quick-Commerce check complete.";
          }
          es.close();
          activeCompareEventSource = null;
          renderResult({ success: true, analysis: currentAnalysis, payload: currentPayload });
        });

        es.onerror = () => {
          isComparing = false;
          es.close();
          activeCompareEventSource = null;
          renderResult({ success: true, analysis: currentAnalysis, payload: currentPayload });
        };
      })
      .catch(() => {
        // Fallback to background worker message
        chrome.runtime.sendMessage({
          type: "ONESTOP_COMPARE_CROSS_STORE",
          payload: currentPayload,
          requestUrl: activeUrl,
        });
      });
  }

  // ── Extraction Orchestration & Hydration Wait ──

  function waitForHydratedPayload(targetUrl, reqId, maxWaitMs) {
    const start = Date.now();
    const stepMs = 400;

    function attempt() {
      if (reqId !== activeRequestId || window.location.href !== targetUrl) return;
      if (!isProductPage()) {
        if (Date.now() - start < maxWaitMs) {
          setTimeout(attempt, stepMs);
        } else {
          hideWidget();
        }
        return;
      }

      if (typeof window.OneStopExtractDOM !== "function") return;
      const payload = window.OneStopExtractDOM();
      if (payload && payload.listed_price && payload.listed_price > 0 && payload.title && payload.title.length >= 4) {
        currentPayload = payload;
        showWidgetLoading();
        chrome.runtime.sendMessage({
          type: "ONESTOP_PAYLOAD",
          payload: payload,
          requestUrl: targetUrl,
          requestId: reqId,
        });
        return;
      }

      if (Date.now() - start < maxWaitMs) {
        setTimeout(attempt, stepMs);
      } else {
        hideWidget();
      }
    }

    attempt();
  }

  function runExtractionForCurrentPage(force) {
    const url = window.location.href;
    if (!force && url === activeUrl) return;
    activeUrl = url;
    activeRequestId += 1;
    currentPayload = null;
    currentAnalysis = null;
    competitorQuotes = null;
    isComparing = false;

    if (!isProductPage()) {
      hideWidget();
      // Give SPA routes up to 2.5s to hydrate product DOM before giving up
      waitForHydratedPayload(url, activeRequestId, 2500);
      return;
    }

    waitForHydratedPayload(url, activeRequestId, 2500);
  }

  // ── SPA Route Watcher (pushState / replaceState / popstate / debounced MutationObserver) ──

  const origPush = history.pushState;
  history.pushState = function () {
    const ret = origPush.apply(this, arguments);
    setTimeout(() => runExtractionForCurrentPage(false), 250);
    return ret;
  };
  const origReplace = history.replaceState;
  history.replaceState = function () {
    const ret = origReplace.apply(this, arguments);
    setTimeout(() => runExtractionForCurrentPage(false), 250);
    return ret;
  };
  window.addEventListener("popstate", () => setTimeout(() => runExtractionForCurrentPage(false), 250));

  let mutTimer = null;
  const observer = new MutationObserver(() => {
    if (window.location.href !== activeUrl) {
      clearTimeout(mutTimer);
      mutTimer = setTimeout(() => runExtractionForCurrentPage(false), 350);
    }
  });
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // ── Message Listener (ignores stale responses from old URLs) ──

  chrome.runtime.onMessage.addListener((message) => {
    if (message.requestUrl && message.requestUrl !== activeUrl) return;
    if (message.type === "ONESTOP_RESULT") {
      if (message.data?.payload) currentPayload = message.data.payload;
      if (message.data?.analysis) currentAnalysis = message.data.analysis;
      renderResult(message.data);
    } else if (message.type === "ONESTOP_COMPETITOR_RESULTS") {
      isComparing = false;
      if (message.data?.analysis) currentAnalysis = message.data.analysis;
      competitorQuotes = message.data?.quotes || [];
      if (currentAnalysis) {
        renderResult({ success: true, analysis: currentAnalysis, payload: currentPayload });
      }
    }
  });

  // Initial check
  runExtractionForCurrentPage(true);
})();
