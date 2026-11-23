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
      * { box-sizing: border-box; margin: 0; padding: 0; }
      :host { all: initial; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #0f172a; }

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
        background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
        color: #ffffff;
        padding: 10px 18px;
        border-radius: 999px;
        cursor: pointer;
        font-size: 13px;
        font-weight: 700;
        box-shadow: 0 6px 24px rgba(79, 70, 229, 0.45);
        display: flex;
        align-items: center;
        gap: 8px;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        user-select: none;
        flex-shrink: 0;
        border: 1px solid rgba(255,255,255,0.2);
      }
      .os-pill:hover { transform: translateY(-1px); box-shadow: 0 8px 28px rgba(79, 70, 229, 0.6); }
      .os-pill.os-loading { opacity: 0.9; }
      .os-pill .os-pill-text { white-space: nowrap; }

      .os-drawer {
        pointer-events: auto;
        display: none;
        flex-direction: column;
        width: 430px;
        max-width: min(430px, calc(100vw - 32px));
        max-height: calc(100vh - 100px);
        background: #ffffff;
        color: #0f172a;
        border-radius: 16px;
        box-shadow: 0 16px 48px rgba(15, 23, 42, 0.28);
        border: 1px solid #e2e8f0;
        font-size: 13px;
        overflow: hidden;
      }
      .os-drawer.os-open { display: flex; animation: osSlideUp 0.22s ease; }
      @keyframes osSlideUp { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: translateY(0); } }

      .os-header {
        background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
        color: #ffffff;
        padding: 13px 16px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-shrink: 0;
        gap: 8px;
      }
      .os-header-title { font-weight: 700; font-size: 14px; min-width: 0; overflow-wrap: anywhere; }
      .os-close {
        background: rgba(255,255,255,0.2);
        border: none;
        color: #ffffff;
        width: 28px;
        height: 28px;
        border-radius: 50%;
        cursor: pointer;
        font-size: 15px;
        flex-shrink: 0;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .os-close:hover { background: rgba(255,255,255,0.35); }

      .os-body {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
        overflow-x: hidden;
        background: #ffffff;
        color: #0f172a;
      }
      .os-body::-webkit-scrollbar { width: 6px; }
      .os-body::-webkit-scrollbar-track { background: #f8fafc; }
      .os-body::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 6px; }

      .os-section { padding: 14px 16px; border-bottom: 1px solid #f1f5f9; overflow-wrap: anywhere; }
      .os-section:last-child { border-bottom: none; }
      .os-section-title { font-weight: 700; font-size: 11px; text-transform: uppercase; color: #4f46e5; letter-spacing: 0.6px; margin-bottom: 8px; }

      .os-tier-grid {
        display: grid;
        grid-template-columns: 1fr;
        gap: 8px;
        margin-bottom: 10px;
      }
      .os-tier-card {
        background: #f8fafc;
        border: 1.5px solid #e2e8f0;
        border-radius: 10px;
        padding: 9px 11px;
      }
      .os-tier-card.os-tier-cash {
        background: #eff6ff;
        border-color: #93c5fd;
      }
      .os-tier-card.os-tier-net {
        background: #f0fdf4;
        border-color: #86efac;
      }
      .os-tier-label {
        font-size: 10px;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.4px;
        color: #475569;
      }
      .os-tier-amt {
        font-size: 20px;
        font-weight: 800;
        color: #0f172a;
        line-height: 1.2;
        margin-top: 2px;
      }
      .os-tier-meta {
        font-size: 11px;
        color: #475569;
        margin-top: 3px;
        line-height: 1.4;
      }
      .os-collapsible {
        margin-top: 10px;
        border: 1px dashed #cbd5e1;
        border-radius: 10px;
        padding: 8px 10px;
        background: #f8fafc;
      }
      .os-collapsible summary {
        cursor: pointer;
        font-size: 11.5px;
        font-weight: 700;
        color: #475569;
        user-select: none;
      }
      .os-badge-none { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }
      .os-badge-current { background: #e0e7ff; color: #3730a3; margin-left: 4px; }
      .os-row { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-size: 12px; padding: 3px 0; }
      .os-row > * { min-width: 0; }

      .os-route-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 9px 10px;
        margin-top: 7px;
      }
      .os-route-card.os-muted { opacity: 0.65; background: #f8fafc; }
      .os-badge {
        display: inline-block;
        font-size: 10px;
        font-weight: 700;
        padding: 2px 6px;
        border-radius: 4px;
        text-transform: uppercase;
      }
      .os-badge-page { background: #dcfce7; color: #166534; }
      .os-badge-tc { background: #dbeafe; color: #1e40af; }
      .os-badge-est { background: #fef3c7; color: #92400e; }
      .os-badge-cap { background: #fee2e2; color: #991b1b; }

      .os-verdict-box {
        background: #eef2ff;
        border: 1px solid #c7d2fe;
        border-radius: 10px;
        padding: 10px 12px;
        margin-top: 10px;
        font-size: 12px;
        color: #1e1b4b;
        line-height: 1.5;
      }

      .os-exec-box {
        background: #f0fdf4;
        border: 1px solid #86efac;
        border-radius: 10px;
        padding: 10px 12px;
      }
      .os-exec-bullets { margin-top: 6px; padding-left: 16px; font-size: 12px; color: #065f46; line-height: 1.45; }
      .os-caveat { margin-top: 6px; font-size: 11px; color: #92400e; background: #fef3c7; padding: 5px 8px; border-radius: 6px; border-left: 3px solid #f59e0b; }

      .os-btn-compare {
        width: 100%;
        padding: 10px 14px;
        background: linear-gradient(135deg, #10b981 0%, #059669 100%);
        color: #ffffff;
        border: none;
        border-radius: 10px;
        font-weight: 700;
        font-size: 13px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
      }
      .os-btn-compare:hover { opacity: 0.94; }
      .os-btn-compare:disabled { opacity: 0.6; cursor: not-allowed; }

      .os-table-wrap { width: 100%; overflow-x: auto; margin-top: 6px; }
      .os-table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
      .os-table th, .os-table td { text-align: left; padding: 6px 6px; border-bottom: 1px solid #f1f5f9; white-space: nowrap; }
      .os-table th { color: #64748b; font-weight: 700; font-size: 10.5px; text-transform: uppercase; }
      .os-table tr.os-win { background: #f0fdf4; }

      .os-link { color: #4f46e5; text-decoration: none; font-weight: 600; }
      .os-link:hover { text-decoration: underline; }

      .os-seller-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 9px 10px; margin-top: 6px; }
      .os-pro { color: #047857; font-size: 11px; }
      .os-con { color: #b91c1c; font-size: 11px; }

      .os-dashboard-link {
        display: block;
        width: 100%;
        text-align: center;
        padding: 12px;
        background: #f8fafc;
        color: #4f46e5;
        text-decoration: none;
        font-weight: 700;
        font-size: 13px;
        border-top: 1px solid #e2e8f0;
        flex-shrink: 0;
      }
      .os-dashboard-link:hover { background: #eef2ff; }

      .os-error { padding: 16px; color: #991b1b; font-size: 13px; }
      .os-btn-retry {
        margin-top: 10px;
        padding: 8px 14px;
        background: #4f46e5;
        color: #fff;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        font-size: 12px;
        cursor: pointer;
      }
      .spinner {
        display: inline-block;
        width: 12px;
        height: 12px;
        border: 2px solid rgba(255,255,255,0.35);
        border-top: 2px solid #fff;
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
        <span>💰</span>
        <span class="os-pill-text">OneStop Analyzing...</span>
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

  function verificationBadge(status, lastVerifiedAt) {
    const dt = lastVerifiedAt ? new Date(lastVerifiedAt).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "";
    if (status === "page_verified") return `<span class="os-badge os-badge-page">Page Verified</span>`;
    if (status === "live_tc_verified" || status === "issuer_tc_verified") {
      return `<span class="os-badge os-badge-tc">Official T&amp;C Verified${dt ? " • " + esc(dt) : ""}</span>`;
    }
    return `<span class="os-badge os-badge-none">No Verified Offer</span>`;
  }

  function renderResult(data) {
    ensureWidget();
    host.style.display = "block";
    pill.classList.remove("os-loading");

    if (!data || !data.success || !data.analysis) {
      pill.querySelector(".os-pill-text").textContent = "OneStop ⚠️";
      drawer.innerHTML = `
        <div class="os-header">
          <span class="os-header-title">OneStop Intelligence</span>
          <button class="os-close" id="osClose">✕</button>
        </div>
        <div class="os-body">
          <div class="os-error">
            <div style="font-weight:700;margin-bottom:4px;">Analysis Delayed</div>
            <div style="line-height:1.5;">${esc(data?.error || "Could not reach backend on localhost:8000.")}</div>
            <button class="os-btn-retry" id="osBtnRetry">Retry Analysis 🔄</button>
          </div>
        </div>
      `;
      shadow.getElementById("osClose")?.addEventListener("click", () => drawer.classList.remove("os-open"));
      shadow.getElementById("osBtnRetry")?.addEventListener("click", () => runExtractionForCurrentPage(true));
      return;
    }

    const a = data.analysis;
    const cvp = a.cash_vs_points || null;
    const trust = a.trust_report || null;
    const bw = a.buy_wait || null;

    const pillAmt = cvp ? cvp.checkout_payable_inr : a.listed_price;
    pill.querySelector(".os-pill-text").textContent = `Cash: ${fmt(pillAmt)}`;

    let bodyHtml = "";

    // ── Section 1: 3-Tier Price Display (Listed -> Checkout Cash -> Effective Net) ──
    if (cvp) {
      const availRoutes = (cvp.routes || []).filter((r) => r.available);
      const bestNetRoute = availRoutes.length
        ? [...availRoutes].sort((x, y) => x.effective_cost_if_cash_inr - y.effective_cost_if_cash_inr)[0]
        : null;
      const bestNetPrice = cvp.best_user_card_net_price_cash || cvp.checkout_payable_inr;
      const rwWin = bestNetRoute?.rewards || {};
      const winExcl = (bestNetRoute?.exclusions || [])[0] || "";

      bodyHtml += `<div class="os-section">
        <div class="os-section-title">💳 3-Tier Price Breakdown (Your Card Wallet)</div>
        <div class="os-tier-grid">
          <div class="os-tier-card">
            <div class="os-tier-label">1. Listed Price</div>
            <div class="os-tier-amt">${fmt(cvp.listed_price)}</div>
            <div class="os-tier-meta">Catalog store price before offers &amp; fees</div>
          </div>

          <div class="os-tier-card os-tier-cash">
            <div class="os-tier-label" style="color:#1d4ed8;">2. Cheapest Checkout Cash Price (With Your Cards/Offers)</div>
            <div class="os-tier-amt" style="color:#1e3a8a;">${fmt(cvp.checkout_payable_inr)}</div>
            <div class="os-tier-meta">
              <div class="os-row"><span>Verified Instant Bank Discount</span><strong style="color:${cvp.instant_bank_discount_inr > 0 ? '#059669' : '#64748b'};">-${fmt(cvp.instant_bank_discount_inr)}</strong></div>
              ${cvp.winning_instant_offer_summary ? `<div style="font-size:10.5px;color:#047857;">✓ ${esc(cvp.winning_instant_offer_summary)}</div>` : ""}
              ${cvp.coupon_discount_inr > 0 ? `<div class="os-row"><span>Verified Coupon</span><strong style="color:#059669;">-${fmt(cvp.coupon_discount_inr)}</strong></div>` : ""}
              ${cvp.mandatory_fees_inr > 0 ? `<div class="os-row"><span>Mandatory Fees</span><strong style="color:#dc2626;">+${fmt(cvp.mandatory_fees_inr)}</strong></div>` : ""}
              ${cvp.user_wallet_empty ? `<div style="font-size:10.5px;color:#b45309;margin-top:2px;">Tip: Select your cards in the OneStop popup to match instant bank offers.</div>` : ""}
            </div>
          </div>

          <div class="os-tier-card os-tier-net">
            <div class="os-tier-label" style="color:#047857;">3. Cheapest Effective Net Price (After Verified Card Rewards)</div>
            <div class="os-tier-amt" style="color:#065f46;">${fmt(bestNetPrice)}</div>
            <div class="os-tier-meta">
              ${bestNetRoute && (rwWin.cash_value_inr > 0 || bestNetRoute.instant_discount_inr > 0) ? `
                <div><strong>Winner:</strong> ${esc(bestNetRoute.method_name)} ${verificationBadge(bestNetRoute.verification_status)}</div>
                ${rwWin.points_earned > 0 ? `<div><strong>Rate:</strong> ${esc(rwWin.points_earned)} pts · ${esc(rwWin.conversion_ratio_cash || "")}${rwWin.conversion_ratio_travel && rwWin.conversion_ratio_travel !== rwWin.conversion_ratio_cash ? " | " + esc(rwWin.conversion_ratio_travel) : ""}</div>` : ""}
                ${rwWin.monthly_cap_inr != null ? `<div><strong>Cap:</strong> ${rwWin.cap_applied ? '<span class="os-badge os-badge-cap">Cap Hit</span> ' : ""}${fmt(rwWin.monthly_cap_inr)}/mo</div>` : ""}
                ${winExcl ? `<div style="color:#92400e;"><strong>T&amp;C Catch:</strong> ${esc(winExcl)}</div>` : ""}
              ` : `<div>${cvp.user_wallet_empty ? "No cards selected in My Card Wallet." : "No active verified post-purchase rewards found for your selected cards."}</div>`}
            </div>
          </div>
        </div>

        <div style="font-size:11px;font-weight:700;color:#475569;margin-bottom:4px;">YOUR WALLET PAYMENT ROUTES</div>`;

      (cvp.routes || []).forEach((r) => {
        const rw = r.rewards || {};
        const links = (r.source_urls || [])
          .map((u, idx) => {
            const href = safeHref(u);
            return href ? `<a href="${href}" target="_blank" rel="noopener noreferrer" class="os-link">T&amp;C #${idx + 1}</a>` : "";
          })
          .filter(Boolean)
          .join(" · ");

        bodyHtml += `
          <div class="os-route-card ${r.available ? "" : "os-muted"}">
            <div class="os-row">
              <div>
                <div style="font-weight:700;font-size:12px;">${esc(r.route_label)}</div>
                <div style="font-size:11px;color:#475569;">${esc(r.method_name)}</div>
              </div>
              <div style="text-align:right;flex-shrink:0;">
                ${verificationBadge(r.verification_status)}
                ${rw.cap_applied ? `<span class="os-badge os-badge-cap" style="margin-left:3px;">Capped</span>` : ""}
              </div>
            </div>
            ${r.available ? `
              <div style="margin-top:6px;font-size:11.5px;color:#334155;">
                <div class="os-row"><span>Checkout Cash</span><strong>${fmt(r.checkout_payable_inr)}</strong></div>
                <div class="os-row"><span>Verified Rewards (Cash / Travel)</span><strong style="color:${rw.cash_value_inr > 0 ? '#059669' : '#64748b'};">${fmt(rw.cash_value_inr)} / ${fmt(rw.travel_value_inr)}</strong></div>
                <div class="os-row"><span>Effective Net (Cash / Optimal)</span><strong>${fmt(r.effective_cost_if_cash_inr)} / ${fmt(r.effective_cost_if_optimal_redemption_inr)}</strong></div>
                ${rw.points_earned > 0 ? `<div style="font-size:10.5px;color:#64748b;margin-top:2px;">Pts: ${esc(rw.points_earned)} · ${esc(rw.conversion_ratio_cash)}${rw.conversion_ratio_travel && rw.conversion_ratio_travel !== rw.conversion_ratio_cash ? " | " + esc(rw.conversion_ratio_travel) : ""}</div>` : ""}
                ${rw.processing_fee_inr > 0 ? `<div style="font-size:10.5px;color:#b91c1c;">Voucher Processing Fee: +${fmt(rw.processing_fee_inr)}</div>` : ""}
                ${r.verification_status === "no_verified_offer" ? `<div style="font-size:10.5px;color:#64748b;margin-top:2px;">${esc(r.summary || "No active verified offer found")}</div>` : ""}
                ${links ? `<div style="font-size:10.5px;margin-top:3px;">Sources: ${links}</div>` : ""}
              </div>
            ` : `<div style="font-size:11px;color:#64748b;margin-top:4px;">${esc(r.summary || "No active verified offer found")}</div>`}
          </div>`;
      });

      const unowned = cvp.unowned_card_routes || [];
      if (unowned.length > 0) {
        bodyHtml += `
          <details class="os-collapsible">
            <summary>Other Cards (Not in Your Wallet) · ${unowned.length} verified offer${unowned.length > 1 ? "s" : ""}</summary>
            <div style="margin-top:6px;">
              ${unowned.slice(0, 4).map((u) => `
                <div class="os-route-card">
                  <div class="os-row">
                    <strong>${esc(u.method_name)}</strong>
                    ${verificationBadge(u.verification_status)}
                  </div>
                  <div class="os-row" style="margin-top:3px;"><span>Checkout Cash</span><strong>${fmt(u.checkout_payable_inr)}</strong></div>
                  <div class="os-row"><span>Effective Net</span><strong style="color:#059669;">${fmt(u.effective_cost_if_cash_inr)}</strong></div>
                  <div style="font-size:10.5px;color:#64748b;margin-top:2px;">${esc(u.summary || "")}</div>
                </div>
              `).join("")}
            </div>
          </details>`;
      }

      if (cvp.efficiency_verdict) {
        bodyHtml += `<div class="os-verdict-box"><strong>Efficiency Verdict:</strong> ${esc(cvp.efficiency_verdict)}</div>`;
      }
      bodyHtml += `</div>`;
    }

    // ── Section 2: AI Executive Summary ──
    if (a.llm_summary) {
      const s = a.llm_summary;
      bodyHtml += `<div class="os-section">
        <div class="os-section-title">🤖 Executive AI Summary</div>
        <div class="os-exec-box">
          <div style="font-size:12px;font-weight:700;color:#065f46;">${esc(s.why_this_wins || "")}</div>
          ${s.summary_bullets && s.summary_bullets.length ? `
            <ul class="os-exec-bullets">
              ${s.summary_bullets.slice(0, 4).map((b) => `<li>${esc(b)}</li>`).join("")}
            </ul>` : ""}
          ${s.key_caveat ? `<div class="os-caveat">⚠️ ${esc(s.key_caveat)}</div>` : ""}
        </div>
      </div>`;
    }

    // ── Section 3: All Online Stores & D2C Marketplaces + Quick Commerce (10-Min Delivery) ──
    bodyHtml += `<div class="os-section">
      <div class="os-row" style="margin-bottom:8px;">
        <div class="os-section-title" style="margin-bottom:0;">🏪 All Online Stores &amp; D2C Marketplaces</div>
        <div style="display:flex;align-items:center;gap:4px;font-size:11px;">
          <span style="color:#64748b;">Pincode:</span>
          <input id="osPincodeInput" type="text" maxlength="6" value="${esc(userPincode)}" style="width:62px;padding:2px 6px;border:1px solid #cbd5e1;border-radius:6px;font-size:11px;font-weight:700;color:#0f172a;" />
        </div>
      </div>`;

    if (isComparing || streamProgressMsg) {
      bodyHtml += `
        <div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:6px 10px;margin-bottom:8px;font-size:11px;color:#3730a3;display:flex;align-items:center;gap:6px;">
          ${isComparing ? '<span class="spinner" style="border-color:#818cf8;border-top-color:#312e81;"></span>' : '<span>✓</span>'}
          <span>${esc(streamProgressMsg || ("Checking stores & 10-min apps for pincode " + userPincode + "..."))}</span>
        </div>`;
    }

    const ranked = a.ranked_store_choices || [];
    if (ranked.length > 0) {
      bodyHtml += `
        <div class="os-table-wrap">
          <table class="os-table">
            <thead>
              <tr><th>Store</th><th>Checkout Cash</th><th>Your Card Net</th><th>Link</th></tr>
            </thead>
            <tbody>
              ${ranked.map((st) => {
                const href = safeHref(st.url);
                const cashP = st.checkout_cash_price || st.listed_price;
                const netP = st.best_user_card_net_price || st.effective_price;
                return `
                  <tr class="${st.is_overall_winner ? "os-win" : ""}">
                    <td>
                      <strong>${esc(st.store_name)}</strong>
                      ${st.is_current_store ? `<span class="os-badge os-badge-current">Current Page</span>` : ""}
                      <div style="font-size:10px;color:#64748b;">${esc(st.seller_name)} · Trust ${esc(st.peace_of_mind_score)}/100</div>
                    </td>
                    <td><strong>${fmt(cashP)}</strong></td>
                    <td style="color:#047857;"><strong>${fmt(netP)}</strong></td>
                    <td>${st.is_current_store ? `<span style="font-size:10.5px;color:#64748b;">Here</span>` : (href ? `<a href="${href}" target="_blank" rel="noopener noreferrer" class="os-link">Open →</a>` : "—")}</td>
                  </tr>`;
              }).join("")}
            </tbody>
          </table>
        </div>`;
    }

    const qcList = (quickCommerceQuotes && quickCommerceQuotes.length > 0)
      ? quickCommerceQuotes
      : (a.quick_commerce_quotes || []);
    bodyHtml += `
      <div style="margin-top:10px;padding-top:8px;border-top:1px dashed #e2e8f0;">
        <div class="os-section-title" style="color:#059669;margin-bottom:6px;">⚡ Quick Commerce (10-Min Delivery · ${esc(userPincode)})</div>
        ${qcList.length > 0 ? `
          <div class="os-table-wrap">
            <table class="os-table">
              <thead>
                <tr><th>App / ETA</th><th>Unit Price</th><th>Fees</th><th>Total Payable</th><th>Link</th></tr>
              </thead>
              <tbody>
                ${qcList.map((qc) => {
                  const qHref = safeHref(qc.url);
                  const feesTotal = (qc.handling_fee_inr || 0) + (qc.delivery_fee_inr || 0) + (qc.surge_fee_inr || 0) + (qc.small_cart_fee_inr || 0);
                  return `
                    <tr>
                      <td>
                        <strong>${esc(qc.app_name)}</strong>
                        <div style="font-size:10px;color:#059669;font-weight:700;">⚡ ${esc(qc.eta_minutes_label || "10 mins")}</div>
                      </td>
                      <td>${esc(qc.unit_price_label || qc.pack_size || "—")}</td>
                      <td style="color:#b91c1c;">+${fmt(feesTotal)}</td>
                      <td><strong>${fmt(qc.total_payable_inr || qc.selling_price)}</strong></td>
                      <td>${qHref ? `<a href="${qHref}" target="_blank" rel="noopener noreferrer" class="os-link">Open →</a>` : "—"}</td>
                    </tr>`;
                }).join("")}
              </tbody>
            </table>
          </div>
        ` : `<div style="font-size:11px;color:#64748b;">Click Compare below to check Blinkit, Zepto, Swiggy Instamart, BigBasket Now &amp; JioMart Quick for pincode ${esc(userPincode)}.</div>`}
      </div>
      <button class="os-btn-compare" id="osBtnCompare" style="margin-top:8px;" ${isComparing ? "disabled" : ""}>
        ${isComparing ? '<span class="spinner"></span> Streaming Stores &amp; 10-Min Apps...' : "🔍 Compare All Stores &amp; 10-Min Apps"}
      </button>
      <div style="font-size:10.5px;color:#64748b;margin-top:4px;text-align:center;">
        Live SSE Streaming · 15-Domain Universal + Quick Commerce · 30m Circuit Breaker
      </div>
    </div>`;

    // ── Section 4: Seller Forensics ──
    if (a.seller_report && a.seller_report.sellers && a.seller_report.sellers.length > 0) {
      const sr = a.seller_report;
      bodyHtml += `<div class="os-section">
        <div class="os-section-title">🛡️ Seller Forensics</div>`;
      sr.sellers.slice(0, 3).forEach((s) => {
        bodyHtml += `
          <div class="os-seller-card">
            <div class="os-row">
              <strong>${esc(s.seller_name)}</strong>
              <span class="os-badge ${s.peace_of_mind_score >= 70 ? "os-badge-page" : "os-badge-est"}">${esc(s.peace_of_mind_score)}/100</span>
            </div>
            <div style="font-size:11px;color:#64748b;margin-top:2px;">${esc(s.fulfillment_type || "Standard Dispatch")} · ${esc(s.return_policy || "Standard Returns")}</div>
            ${(s.pros || []).slice(0, 2).map((p) => `<div class="os-pro">✓ ${esc(p)}</div>`).join("")}
            ${(s.cons || []).slice(0, 2).map((c) => `<div class="os-con">✕ ${esc(c)}</div>`).join("")}
          </div>`;
      });
      bodyHtml += `</div>`;
    } else if (trust) {
      bodyHtml += `<div class="os-section">
        <div class="os-section-title">🛡️ Seller Forensics</div>
        <div class="os-row"><strong>${esc(trust.seller_name)}</strong><span class="os-badge os-badge-page">${esc(trust.trust_score)}/100</span></div>
      </div>`;
    }

    // ── Section 5: Buy / Wait Verdict ──
    if (bw) {
      bodyHtml += `<div class="os-section">
        <div class="os-section-title">📊 Buy or Wait?</div>
        <div style="font-weight:700;font-size:12px;margin-bottom:4px;">${esc((bw.verdict || "").replace(/_/g, " "))}</div>
        <div style="font-size:12px;color:#475569;line-height:1.45;">${esc(bw.reasoning || "")}</div>
      </div>`;
    }

    const dashUrl = a.canonical_product_id
      ? `http://localhost:3000/product/${encodeURIComponent(a.canonical_product_id)}`
      : "";

    drawer.innerHTML = `
      <div class="os-header">
        <span class="os-header-title">OneStop · ${esc(a.brand && a.brand !== "Unknown" ? a.brand : retailerLabel(currentPayload?.retailer))}</span>
        <button class="os-close" id="osClose">✕</button>
      </div>
      <div class="os-body">${bodyHtml}</div>
      ${dashUrl ? `<a href="${dashUrl}" target="_blank" rel="noopener noreferrer" class="os-dashboard-link">Open Full Cash-vs-Points Dashboard →</a>` : ""}
    `;

    shadow.getElementById("osClose")?.addEventListener("click", () => drawer.classList.remove("os-open"));
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
                  is_current_store: false,
                  seller_name: q.seller_name || "Verified Store",
                  peace_of_mind_score: 78,
                  rank_badge: "Live Verified",
                  is_overall_winner: false,
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
