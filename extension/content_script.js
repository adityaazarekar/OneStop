/**
 * OneStop Content Script (Manifest V3)
 *
 * Runs inside supported Indian e-commerce product pages.
 * Features:
 * 1. Isolated Shadow DOM Floating Pill & Decision Drawer.
 * 2. Two-Stage Execution:
 *    - Stage 1: Fast initial load with current tab data (Best Card Net Price, Seller Forensics, Buy/Wait).
 *    - Stage 2: On-demand "Compare Live Across Stores" with anti-ban safety.
 * 3. Direct link to Next.js Web Dashboard (/product/{id}).
 */
(function () {
  "use strict";

  if (document.getElementById("onestop-root")) return;

  let currentPayload = null;
  let currentAnalysis = null;
  let competitorQuotes = null;
  let isComparing = false;

  // ── Create Shadow DOM Host ───────────────

  const host = document.createElement("div");
  host.id = "onestop-root";
  host.style.cssText = "all:initial;position:fixed;bottom:20px;right:20px;z-index:2147483647;pointer-events:none;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;";
  document.body.appendChild(host);

  const shadow = host.attachShadow({ mode: "closed" });

  // ── Styles ───────────────────────────────

  const style = document.createElement("style");
  style.textContent = `
    * { box-sizing: border-box; margin: 0; padding: 0; }
    :host { all: initial; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }

    .os-wrapper {
      display: flex;
      flex-direction: column-reverse;
      align-items: flex-end;
      gap: 12px;
      max-height: calc(100vh - 40px);
      pointer-events: none;
    }

    .os-pill {
      pointer-events: auto;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: #fff;
      padding: 10px 18px;
      border-radius: 30px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
      box-shadow: 0 4px 20px rgba(102, 126, 234, 0.4);
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.3s ease;
      user-select: none;
      flex-shrink: 0;
    }
    .os-pill:hover { transform: scale(1.05); box-shadow: 0 6px 25px rgba(102, 126, 234, 0.6); }
    .os-pill .os-logo { font-size: 18px; }
    .os-pill .os-pill-text { white-space: nowrap; }
    .os-pill.os-loading { opacity: 0.85; }
    .os-pill.os-loading .os-pill-text::after { content: '...'; animation: dots 1.4s infinite; }
    @keyframes dots { 0% { content: '.'; } 33% { content: '..'; } 66% { content: '...'; } }

    .os-drawer {
      pointer-events: auto;
      display: none;
      width: 420px;
      max-width: min(420px, calc(100vw - 40px));
      max-height: calc(100vh - 100px);
      overflow-y: auto;
      overflow-x: hidden;
      background: #fff;
      border-radius: 16px;
      box-shadow: 0 12px 48px rgba(0,0,0,0.25);
      border: 1px solid rgba(0,0,0,0.08);
      font-size: 13px;
      color: #1a1a2e;
      position: relative;
    }
    .os-drawer.os-open { display: block; animation: slideUp 0.3s ease; }
    @keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }

    .os-drawer::-webkit-scrollbar {
      width: 6px;
    }
    .os-drawer::-webkit-scrollbar-track {
      background: #f8fafc;
      border-radius: 8px;
    }
    .os-drawer::-webkit-scrollbar-thumb {
      background: #cbd5e1;
      border-radius: 8px;
    }
    .os-drawer::-webkit-scrollbar-thumb:hover {
      background: #94a3b8;
    }

    .os-header {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: #fff;
      padding: 14px 18px;
      border-radius: 16px 16px 0 0;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .os-header-title { font-weight: 700; font-size: 15px; }
    .os-close { background: rgba(255,255,255,0.2); border: none; color: #fff; width: 28px; height: 28px; border-radius: 50%; cursor: pointer; font-size: 16px; display: flex; align-items: center; justify-content: center; }
    .os-close:hover { background: rgba(255,255,255,0.35); }

    .os-section { padding: 14px 18px; border-bottom: 1px solid #f0f0f5; }
    .os-section:last-child { border-bottom: none; }
    .os-section-title { font-weight: 700; font-size: 12px; text-transform: uppercase; color: #667eea; letter-spacing: 0.5px; margin-bottom: 8px; }

    .os-price-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
    .os-price-label { color: #666; }
    .os-price-value { font-weight: 600; }
    .os-price-best { font-size: 22px; font-weight: 800; color: #10b981; }
    .os-price-strike { text-decoration: line-through; color: #999; font-size: 13px; }
    .os-card-name { color: #667eea; font-weight: 600; }

    .os-missed {
      background: #fef3c7;
      border: 1px solid #f59e0b;
      border-radius: 8px;
      padding: 10px 12px;
      margin-top: 8px;
      font-size: 12px;
      color: #92400e;
    }
    .os-missed strong { color: #d97706; }

    .os-btn-compare {
      width: 100%;
      padding: 10px 14px;
      background: linear-gradient(135deg, #10b981 0%, #059669 100%);
      color: #fff;
      border: none;
      border-radius: 8px;
      font-weight: 600;
      font-size: 13px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      transition: opacity 0.2s;
    }
    .os-btn-compare:hover { opacity: 0.92; }
    .os-btn-compare:disabled { opacity: 0.6; cursor: not-allowed; }

    .os-competitor { display: flex; justify-content: space-between; align-items: center; padding: 7px 0; border-bottom: 1px solid #f5f5f5; }
    .os-competitor:last-child { border-bottom: none; }
    .os-comp-retailer { font-weight: 600; text-transform: capitalize; }
    .os-comp-price { font-weight: 700; }
    .os-comp-link { color: #667eea; text-decoration: none; font-size: 12px; font-weight: 600; margin-left: 6px; }
    .os-comp-cheapest { color: #10b981; }
    .os-comp-paused { font-size: 11px; color: #d97706; background: #fef3c7; padding: 2px 6px; border-radius: 4px; }

    .os-trust-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 20px;
      font-weight: 700;
      font-size: 14px;
    }
    .os-trust-high { background: #d1fae5; color: #065f46; }
    .os-trust-mid { background: #fef3c7; color: #92400e; }
    .os-trust-low { background: #fee2e2; color: #991b1b; }

    .os-warning { font-size: 12px; color: #b45309; margin-top: 4px; }

    .os-exec-box {
      background: linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 100%);
      border: 1px solid #10b981;
      border-radius: 12px;
      padding: 12px 14px;
      margin-bottom: 10px;
    }
    .os-exec-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: #10b981;
      color: #fff;
      font-size: 11px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 6px;
      text-transform: uppercase;
    }
    .os-exec-bullets {
      margin-top: 6px;
      padding-left: 16px;
      font-size: 12px;
      color: #065f46;
      line-height: 1.5;
    }
    .os-caveat {
      margin-top: 6px;
      font-size: 11px;
      color: #92400e;
      background: #fef3c7;
      padding: 4px 8px;
      border-radius: 6px;
      border-left: 3px solid #f59e0b;
    }

    .os-step-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 4px 0;
      font-size: 12px;
      border-bottom: 1px dashed #f0f0f5;
    }
    .os-step-row:last-child { border-bottom: none; }
    .os-step-deduct { color: #10b981; font-weight: 700; }
    .os-step-fee { color: #dc2626; font-weight: 700; }

    .os-alt-path {
      background: #eff6ff;
      border: 1px solid #93c5fd;
      border-radius: 8px;
      padding: 8px 10px;
      margin-top: 6px;
      font-size: 11px;
      color: #1e40af;
    }

    .os-seller-card {
      background: #f9fafb;
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 10px;
      margin-top: 8px;
    }
    .os-seller-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 4px;
    }
    .os-seller-name { font-weight: 700; font-size: 13px; color: #111827; }
    .os-seller-score {
      font-size: 11px;
      font-weight: 800;
      padding: 2px 6px;
      border-radius: 4px;
    }
    .os-score-high { background: #d1fae5; color: #065f46; }
    .os-score-mid { background: #fef3c7; color: #92400e; }
    .os-score-low { background: #fee2e2; color: #991b1b; }

    .os-pros-cons {
      margin-top: 4px;
      font-size: 11px;
      line-height: 1.4;
    }
    .os-pro { color: #059669; }
    .os-con { color: #dc2626; }

    .os-rank-card {
      background: #fff;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 8px 10px;
      margin-top: 6px;
    }
    .os-rank-card.os-winner {
      border: 1.5px solid #10b981;
      background: #f0fdf4;
    }
    .os-badge-pill {
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      padding: 1px 6px;
      border-radius: 4px;
    }
    .os-badge-best { background: #dcfce7; color: #15803d; border: 1px solid #86efac; }
    .os-badge-cheap { background: #e0e7ff; color: #3730a3; border: 1px solid #a5b4fc; }
    .os-badge-peace { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }

    .os-verdict-badge {
      display: inline-block;
      padding: 4px 12px;
      border-radius: 12px;
      font-weight: 700;
      font-size: 13px;
    }
    .os-buy { background: #d1fae5; color: #065f46; }
    .os-wait { background: #fef3c7; color: #92400e; }
    .os-overpriced { background: #fee2e2; color: #991b1b; }
    .os-fair { background: #e0e7ff; color: #3730a3; }
    .os-insufficient { background: #f3f4f6; color: #6b7280; }

    .os-verdict-text { margin-top: 6px; font-size: 12px; color: #555; line-height: 1.5; }

    .os-dashboard-link {
      display: block;
      width: 100%;
      text-align: center;
      padding: 12px;
      background: #f8fafc;
      color: #667eea;
      text-decoration: none;
      font-weight: 700;
      font-size: 13px;
      border-top: 1px solid #e2e8f0;
      border-radius: 0 0 16px 16px;
      transition: background 0.2s;
    }
    .os-dashboard-link:hover { background: #eef2ff; }

    .os-error { padding: 16px; color: #991b1b; font-size: 13px; }
    .os-error-title { font-weight: 700; margin-bottom: 4px; }

    .spinner {
      display: inline-block;
      width: 12px;
      height: 12px;
      border: 2px solid rgba(255,255,255,0.3);
      border-top: 2px solid #fff;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  `;
  shadow.appendChild(style);

  // ── Widget DOM Structure ─────────────────

  const wrapper = document.createElement("div");
  wrapper.className = "os-wrapper";
  wrapper.innerHTML = `
    <div class="os-pill os-loading" id="osPill">
      <span class="os-logo">💰</span>
      <span class="os-pill-text">OneStop Analyzing</span>
    </div>
    <div class="os-drawer" id="osDrawer"></div>
  `;
  shadow.appendChild(wrapper);

  const pill = shadow.getElementById("osPill");
  const drawer = shadow.getElementById("osDrawer");

  pill.addEventListener("click", () => {
    drawer.classList.toggle("os-open");
  });

  // ── Listen for messages from background.js ──

  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === "ONESTOP_RESULT") {
      currentPayload = message.data?.payload;
      currentAnalysis = message.data?.analysis;
      renderResult(message.data);
    } else if (message.type === "ONESTOP_COMPETITOR_RESULTS") {
      isComparing = false;
      if (message.data?.analysis) {
        currentAnalysis = message.data.analysis;
      }
      competitorQuotes = message.data?.quotes || [];
      renderResult({ success: true, analysis: currentAnalysis, payload: currentPayload });
    }
  });

  // ── Helpers ──────────────────────────────

  function fmt(n) {
    if (n == null) return "N/A";
    return "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }

  function retailerLabel(r) {
    const map = {
      amazon_in: "Amazon.in",
      flipkart: "Flipkart",
      croma: "Croma",
      reliance_digital: "Reliance Digital",
      vijaysales: "Vijay Sales",
      tatacliq: "Tata CLiQ",
    };
    return map[r] || r.replace(/_/g, " ");
  }

  // ── Render Drawer ────────────────────────

  function renderResult(data) {
    pill.classList.remove("os-loading");

    if (!data.success) {
      pill.querySelector(".os-pill-text").textContent = "OneStop ⚠️";
      drawer.innerHTML = `
        <div class="os-header">
          <span class="os-header-title">OneStop Intelligence</span>
          <button class="os-close" id="osClose">✕</button>
        </div>
        <div class="os-error">
          <div class="os-error-title">Analysis Delayed</div>
          <div style="margin-bottom:12px;line-height:1.5;">${data.error || "Could not reach backend. Is it running on localhost:8000?"}</div>
          <button id="osBtnRetry" style="padding:7px 14px;background:#667eea;color:#fff;border:none;border-radius:8px;font-weight:600;font-size:12px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;">
            <span>Retry Analysis</span>
            <span>🔄</span>
          </button>
        </div>
      `;
      shadow.getElementById("osClose").addEventListener("click", () => drawer.classList.remove("os-open"));
      shadow.getElementById("osBtnRetry")?.addEventListener("click", () => {
        pill.classList.add("os-loading");
        pill.querySelector(".os-pill-text").textContent = "OneStop Analyzing";
        drawer.classList.remove("os-open");
        if (currentPayload) {
          chrome.runtime.sendMessage({ type: "ONESTOP_PAYLOAD", payload: currentPayload });
        }
      });
      return;
    }

    const a = data.analysis;
    const best = a.best_card;
    const trust = a.trust_report;
    const bw = a.buy_wait;

    // Update pill text
    if (best) {
      pill.querySelector(".os-pill-text").textContent = `Best: ${fmt(best.effective_price)}`;
    } else {
      pill.querySelector(".os-pill-text").textContent = `${fmt(a.listed_price)}`;
    }

    let html = `
      <div class="os-header">
        <span class="os-header-title">OneStop Decision Engine</span>
        <button class="os-close" id="osClose">✕</button>
      </div>
    `;

    // ── Section 0: Executive Decision Recommendation (AI Summary) ──
    if (a.llm_summary) {
      const s = a.llm_summary;
      html += `
        <div class="os-section" style="padding-bottom:10px;">
          <div class="os-exec-box">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <span class="os-exec-badge">⭐ Pick ${retailerLabel(s.best_store)}</span>
              <span style="font-weight:800;color:#065f46;font-size:13px;">${fmt(s.final_net_price)}</span>
            </div>
            ${s.why_this_wins ? `<div style="font-size:12px;font-weight:600;color:#1e3a8a;margin-top:6px;">${s.why_this_wins}</div>` : ""}
            ${s.summary_bullets && s.summary_bullets.length > 0 ? `
              <ul class="os-exec-bullets">
                ${s.summary_bullets.map((b) => `<li>${b}</li>`).join("")}
              </ul>` : ""}
            ${s.key_caveat ? `<div class="os-caveat">⚠️ <strong>Caveat:</strong> ${s.key_caveat}</div>` : ""}
          </div>
        </div>
      `;
    }

    // ── Section 1: Best Effective Price & Dynamic Stacking ──
    html += `<div class="os-section">
      <div class="os-section-title">💳 Best Effective Price & Offer Stack</div>`;
    if (best) {
      html += `
        <div class="os-price-row">
          <span class="os-price-label">Listed Price</span>
          <span class="os-price-strike">${fmt(a.listed_price)}</span>
        </div>
        <div class="os-price-row">
          <span class="os-price-label">After Bank Discount</span>
          <span class="os-price-value">-${fmt(best.bank_discount)}</span>
        </div>
        <div class="os-price-row">
          <span class="os-price-label">Card Rewards (${best.reward_pct}%)</span>
          <span class="os-price-value">-${fmt(best.reward_value)}</span>
        </div>
        <div class="os-price-row" style="margin-top:6px;padding-top:6px;border-top:2px solid #e5e7eb">
          <span class="os-card-name">🏆 ${best.card_name}</span>
          <span class="os-price-best">${fmt(best.effective_price)}</span>
        </div>`;
      if (best.offer_summary) {
        html += `<div style="font-size:11px;color:#667eea;margin-top:4px">📋 ${best.offer_summary}</div>`;
      }
    } else {
      html += `<div style="color:#666">No cards configured. Set up your wallet in the extension popup.</div>`;
    }

    // Stacked Offers breakdown if available
    if (a.stacked_offers && a.stacked_offers.steps && a.stacked_offers.steps.length > 0) {
      const so = a.stacked_offers;
      html += `
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:8px 10px;margin-top:10px;">
          <div style="font-size:11px;font-weight:700;color:#475569;margin-bottom:6px;">⚡ STACK DEDUCTION STEPS:</div>`;
      so.steps.forEach((st) => {
        const isFee = st.verification_status === "fee";
        html += `
          <div class="os-step-row">
            <div>
              <span style="font-weight:600;">${st.step_name}</span>
              <div style="font-size:10px;color:#64748b;">${st.description}</div>
            </div>
            <div style="text-align:right;">
              <span class="${isFee ? 'os-step-fee' : 'os-step-deduct'}">${isFee ? '+' : '-'}${fmt(st.amount_deducted)}</span>
              <div style="font-size:10px;color:#94a3b8;">→ ${fmt(st.running_price)}</div>
            </div>
          </div>
        `;
      });
      html += `</div>`;

      if (so.alternative_paths && so.alternative_paths.length > 0) {
        html += `<div style="font-size:11px;font-weight:700;color:#64748b;margin-top:8px;">💡 Alternative Optimization Routes:</div>`;
        so.alternative_paths.forEach((ap) => {
          html += `
            <div class="os-alt-path">
              <strong>${ap.title}:</strong> ${ap.method} (Save ~${fmt(ap.potential_savings)}, Net: ${fmt(ap.net_price)})
              <div style="font-size:10px;color:#475569;margin-top:2px;">Eligibility: ${ap.eligibility}</div>
            </div>
          `;
        });
      }
    }

    if (a.missed_savings) {
      html += `
        <div class="os-missed">
          💡 <strong>Missed Saving:</strong> ${a.missed_savings.card_name} would save
          <strong>${fmt(a.missed_savings.savings_vs_best_owned)}</strong> more
          (net ${fmt(a.missed_savings.effective_price)})
        </div>`;
    }
    html += `</div>`;

    // ── Section 2: Fair Cross-Store Price Intelligence (Stage 2) ──
    html += `<div class="os-section">
      <div class="os-section-title">🏪 Cross-Store Price Intelligence</div>`;

    if (a.ranked_store_choices && a.ranked_store_choices.length > 0) {
      a.ranked_store_choices.forEach((st) => {
        const badgeCls = st.is_overall_winner
          ? 'os-badge-best'
          : st.rank_badge === 'Lowest Net Price'
          ? 'os-badge-cheap'
          : 'os-badge-peace';
        html += `
          <div class="os-rank-card ${st.is_overall_winner ? 'os-winner' : ''}">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <div style="display:flex;align-items:center;gap:6px;">
                <span style="font-weight:700;">${st.store_name}</span>
                ${st.rank_badge ? `<span class="os-badge-pill ${badgeCls}">${st.rank_badge}</span>` : ""}
              </div>
              <span style="font-weight:800;color:${st.is_overall_winner ? '#10b981' : '#1e293b'}">${fmt(st.effective_price)}</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#64748b;margin-top:4px;">
              <span>Score: <strong>${st.overall_score}/100</strong> (Peace of mind: ${st.peace_of_mind_score}/100)</span>
              ${st.url ? `<a href="${st.url}" target="_blank" class="os-comp-link">View →</a>` : ""}
            </div>
            <div style="font-size:10px;color:#94a3b8;margin-top:2px;">
              ${st.return_terms} · ${st.warranty_terms}
            </div>
          </div>
        `;
      });
    } else {
      const listings = a.listings || [];
      const validCompetitors = (competitorQuotes || []).filter((q) => q.listed_price || q.paused);

      if (validCompetitors.length > 0 || listings.length > 1) {
        // Display cross-store listings
        const allStores = [];
        listings.forEach((l) => {
          if (l.in_stock && l.listed_price > 0) {
            allStores.push({
              retailer: l.retailer,
              price: l.listed_price,
              url: l.listing_url,
              seller: l.seller_name,
            });
          }
        });

        // Add external competitor quotes if not already present
        validCompetitors.forEach((c) => {
          if (!allStores.some((s) => s.retailer === c.retailer)) {
            allStores.push({
              retailer: c.retailer,
              price: c.listed_price,
              url: c.url,
              seller: null,
              paused: c.paused,
            });
          }
        });

        allStores.sort((a, b) => (a.price || 999999) - (b.price || 999999));

        for (let i = 0; i < allStores.length; i++) {
          const item = allStores[i];
          const isCheapest = i === 0 && item.price;
          html += `
            <div class="os-competitor">
              <div>
                <span class="os-comp-retailer">${retailerLabel(item.retailer)}</span>
                ${item.seller ? `<span style="font-size:11px;color:#888"> · ${item.seller}</span>` : ""}
              </div>
              <div>
                ${item.paused
                  ? `<span class="os-comp-paused">Rate Limit Paused</span>`
                  : `<span class="os-comp-price ${isCheapest ? 'os-comp-cheapest' : ''}">${fmt(item.price)}</span>`
                }
                ${item.url ? `<a href="${item.url}" target="_blank" class="os-comp-link">View →</a>` : ""}
              </div>
            </div>`;
        }
      } else {
        // Stage 2 On-Demand CTA
        html += `
          <button class="os-btn-compare" id="osBtnCompare" ${isComparing ? "disabled" : ""}>
            ${isComparing ? '<span class="spinner"></span> Comparing (Anti-ban jitter)...' : '🔍 Compare Live Across Stores'}
          </button>
          <div style="font-size:11px;color:#888;margin-top:5px;text-align:center">
            🛡️ Safe sequential query with ₹0 proxy overhead
          </div>
        `;
      }
    }
    html += `</div>`;

    // ── Section 3: Dynamic Seller Forensics ──
    if (a.seller_report && a.seller_report.sellers && a.seller_report.sellers.length > 0) {
      const sr = a.seller_report;
      html += `<div class="os-section">
        <div class="os-section-title">🛡️ Seller Forensics & Peace of Mind</div>`;
      if (sr.recommended_seller) {
        html += `
          <div style="font-size:12px;color:#065f46;background:#d1fae5;padding:6px 10px;border-radius:8px;font-weight:600;margin-bottom:8px;">
            ⭐ Recommended: <strong>${sr.recommended_seller}</strong> — ${sr.recommended_reason}
          </div>
        `;
      }
      sr.sellers.forEach((s) => {
        const scoreCls = s.peace_of_mind_score >= 70 ? 'os-score-high' : s.peace_of_mind_score >= 40 ? 'os-score-mid' : 'os-score-low';
        html += `
          <div class="os-seller-card">
            <div class="os-seller-header">
              <span class="os-seller-name">${s.seller_name}${s.is_buy_box_winner ? ' (Buy Box)' : ''}</span>
              <span class="os-seller-score ${scoreCls}">Peace of Mind: ${s.peace_of_mind_score}/100</span>
            </div>
            <div style="font-size:11px;color:#64748b;">${s.fulfillment_type || 'Direct Dispatch'} · ${s.return_policy || 'Standard Returns'}</div>
            <div class="os-pros-cons">
              ${(s.pros || []).map((p) => `<div class="os-pro">✓ ${p}</div>`).join('')}
              ${(s.cons || []).map((c) => `<div class="os-con">✕ ${c}</div>`).join('')}
            </div>
            ${s.risk_assessment ? `<div style="font-size:10px;color:#dc2626;margin-top:4px;">⚠️ ${s.risk_assessment}</div>` : ''}
          </div>
        `;
      });
      html += `</div>`;
    } else if (trust) {
      const cls = trust.trust_score >= 70 ? "os-trust-high" : trust.trust_score >= 40 ? "os-trust-mid" : "os-trust-low";
      const emoji = trust.trust_score >= 70 ? "✅" : trust.trust_score >= 40 ? "⚠️" : "🚫";
      html += `<div class="os-section">
        <div class="os-section-title">🛡️ Seller Forensics</div>
        <div class="os-trust-badge ${cls}">${emoji} ${trust.trust_score}/100 — ${trust.trust_tier.toUpperCase()}</div>
        <div style="margin-top:4px;font-size:12px;color:#666">${trust.seller_name}${trust.is_authorized ? " (Authorized)" : ""}</div>`;
      if (trust.warnings && trust.warnings.length > 0) {
        html += trust.warnings.map((w) => `<div class="os-warning">${w}</div>`).join("");
      }
      html += `</div>`;
    }

    // ── Section 4: Buy / Wait ──
    if (bw) {
      const vcls = {
        BUY_NOW: "os-buy",
        WAIT: "os-wait",
        OVERPRICED: "os-overpriced",
        FAIR_PRICE: "os-fair",
        INSUFFICIENT_HISTORY: "os-insufficient",
      }[bw.verdict] || "os-fair";
      const vemoji = { BUY_NOW: "🟢", WAIT: "🟡", OVERPRICED: "🔴", FAIR_PRICE: "🔵", INSUFFICIENT_HISTORY: "⚪" }[bw.verdict] || "🔵";
      html += `<div class="os-section">
        <div class="os-section-title">📊 Buy or Wait?</div>
        <span class="os-verdict-badge ${vcls}">${vemoji} ${bw.verdict.replace(/_/g, " ")}</span>
        <div class="os-verdict-text">${bw.reasoning}</div>`;
      if (bw.next_sale_name && bw.days_to_next_sale != null) {
        html += `<div style="margin-top:6px;font-size:12px;color:#667eea;font-weight:600">📅 ${bw.next_sale_name} in ${bw.days_to_next_sale} days</div>`;
      }
      html += `</div>`;
    }

    // ── Section 5: Link to Web Dashboard ──
    if (a.canonical_product_id) {
      html += `
        <a href="http://localhost:3000/product/${a.canonical_product_id}" target="_blank" class="os-dashboard-link">
          Open Full Dashboard & Price History →
        </a>
      `;
    }

    drawer.innerHTML = html;

    // Attach listeners
    shadow.getElementById("osClose")?.addEventListener("click", () => drawer.classList.remove("os-open"));

    const btnCompare = shadow.getElementById("osBtnCompare");
    if (btnCompare) {
      btnCompare.addEventListener("click", () => {
        isComparing = true;
        renderResult(data);
        chrome.runtime.sendMessage({
          type: "ONESTOP_COMPARE_CROSS_STORE",
          payload: currentPayload,
        });
      });
    }
  }
})();
