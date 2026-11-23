/**
 * OneStop Offscreen Document (DOM Parser)
 *
 * Runs in an isolated document context where DOMParser, document, and window
 * are fully supported. Safely parses fetched retailer HTML and returns structured
 * CompetitorQuote objects to background.js.
 */

// ── Price parser ─────────────────────────────

function parseIndianPrice(text) {
  if (!text) return null;
  const cleaned = text.replace(/,/g, "").replace(/[^\d.]/g, "");
  const num = parseFloat(cleaned);
  return isNaN(num) || num <= 0 ? null : num;
}

// ── Retailer HTML Parsers ────────────────────

function parseAmazon(doc) {
  const results = [];
  const items = doc.querySelectorAll('[data-component-type="s-search-result"]');
  for (let i = 0; i < Math.min(items.length, 3); i++) {
    const el = items[i];
    const titleEl = el.querySelector("h2 a span") || el.querySelector("h2 span") || el.querySelector("h2");
    const priceEl = el.querySelector(".a-price .a-offscreen") || el.querySelector(".a-price-whole");
    const linkEl = el.querySelector("h2 a") || el.querySelector("a.a-link-normal");
    if (!titleEl || !priceEl) continue;
    const href = linkEl ? (linkEl.getAttribute("href") || "") : "";
    const url = href.startsWith("http") ? href : "https://www.amazon.in" + href;
    const asinMatch = href.match(/\/dp\/([A-Z0-9]{10})/) || el.getAttribute("data-asin");
    const sku = typeof asinMatch === "string" ? asinMatch : (asinMatch ? asinMatch[1] : "");
    results.push({
      retailer: "amazon_in",
      title: titleEl.textContent.trim(),
      listed_price: parseIndianPrice(priceEl.textContent),
      url: url,
      sku: sku,
      in_stock: true,
    });
  }
  return results;
}

function parseFlipkart(doc) {
  const results = [];
  // 1. Try JSON-LD first
  const ldScripts = doc.querySelectorAll('script[type="application/ld+json"]');
  for (const script of ldScripts) {
    try {
      const data = JSON.parse(script.textContent);
      const items = data.itemListElement || (Array.isArray(data) ? data : []);
      for (let i = 0; i < Math.min(items.length, 3); i++) {
        const item = items[i].item || items[i];
        if (!item || !item.name) continue;
        const offers = item.offers ? (Array.isArray(item.offers) ? item.offers[0] : item.offers) : {};
        const price = parseFloat(offers.price) || null;
        if (price) {
          results.push({
            retailer: "flipkart",
            title: item.name,
            listed_price: price,
            url: item.url || item["@id"] || "",
            sku: (item.url || "").match(/pid=([A-Z0-9]+)/)?.[1] || "",
            in_stock: true,
          });
        }
      }
      if (results.length > 0) return results;
    } catch (e) { /* skip */ }
  }

  // 2. DOM fallback
  const cards = doc.querySelectorAll('a[href*="/p/"]');
  for (let i = 0; i < Math.min(cards.length, 6); i++) {
    const a = cards[i];
    const titleEl = a.querySelector("div.KzDlHZ") || a.querySelector("div.s1Q9rs") || a.querySelector("div._4rR01T") || a.querySelector("div.wjcEIp");
    const priceEl = a.querySelector("div.Nx9bqj") || a.querySelector("div._30jeq3");
    if (!titleEl || !priceEl) continue;
    const href = a.getAttribute("href") || "";
    const url = href.startsWith("http") ? href : "https://www.flipkart.com" + href;
    const sku = href.match(/pid=([A-Z0-9]+)/)?.[1] || "";
    // Avoid duplicates
    if (!results.some(r => r.sku === sku)) {
      results.push({
        retailer: "flipkart",
        title: titleEl.textContent.trim(),
        listed_price: parseIndianPrice(priceEl.textContent),
        url: url,
        sku: sku,
        in_stock: true,
      });
    }
    if (results.length >= 3) break;
  }
  return results;
}

function parseCroma(doc) {
  const results = [];
  // 1. Try JSON-LD
  const ldScripts = doc.querySelectorAll('script[type="application/ld+json"]');
  for (const script of ldScripts) {
    try {
      const data = JSON.parse(script.textContent);
      const items = Array.isArray(data) ? data : [data];
      for (const item of items) {
        if (item["@type"] === "Product" && item.name && item.offers) {
          const off = Array.isArray(item.offers) ? item.offers[0] : item.offers;
          const price = parseFloat(off.price) || null;
          if (price) {
            results.push({
              retailer: "croma",
              title: item.name,
              listed_price: price,
              url: item.url || "",
              sku: item.sku || "",
              in_stock: true,
            });
          }
        }
      }
      if (results.length > 0) return results.slice(0, 3);
    } catch (e) { /* skip */ }
  }

  // 2. DOM fallback
  const cards = doc.querySelectorAll('li.product-item, div.product-item, a[href*="/p/"]');
  for (let i = 0; i < Math.min(cards.length, 5); i++) {
    const card = cards[i];
    const titleEl = card.querySelector("h3, .product-title, .product-name") || card;
    const priceEl = card.querySelector(".amount, .new-price, .pdp-price");
    if (!priceEl) continue;
    const linkEl = card.closest("a") || card.querySelector("a");
    const href = linkEl ? (linkEl.getAttribute("href") || "") : "";
    const url = href.startsWith("http") ? href : "https://www.croma.com" + href;
    const sku = href.match(/\/p\/(\d+)/)?.[1] || "";
    results.push({
      retailer: "croma",
      title: titleEl.textContent.trim().substring(0, 200),
      listed_price: parseIndianPrice(priceEl.textContent),
      url: url,
      sku: sku,
      in_stock: true,
    });
    if (results.length >= 3) break;
  }
  return results;
}

function parseRelianceDigital(doc) {
  const results = [];
  const cards = doc.querySelectorAll('li.grid, div.sp, a[href*="/p/"]');
  for (let i = 0; i < Math.min(cards.length, 5); i++) {
    const card = cards[i];
    const titleEl = card.querySelector(".sp__name, h2, h3") || card;
    const priceEl = card.querySelector(".sp__price, span.sc-gHboao");
    if (!priceEl) continue;
    const linkEl = card.closest("a") || card.querySelector("a");
    const href = linkEl ? (linkEl.getAttribute("href") || "") : "";
    const url = href.startsWith("http") ? href : "https://www.reliancedigital.in" + href;
    const sku = href.match(/\/p\/(\d+)/)?.[1] || "";
    results.push({
      retailer: "reliance_digital",
      title: titleEl.textContent.trim().substring(0, 200),
      listed_price: parseIndianPrice(priceEl.textContent),
      url: url,
      sku: sku,
      in_stock: true,
      seller_name: "Reliance Digital",
      seller_rating: 4.7,
      return_policy: "7 Days Replacement",
      warranty_notes: "1 Year Official Brand Warranty",
      delivery_fee: 0,
    });
    if (results.length >= 3) break;
  }
  return results;
}

function parseVijaySales(doc) {
  const results = [];
  // 1. JSON-LD
  const ldScripts = doc.querySelectorAll('script[type="application/ld+json"]');
  for (const script of ldScripts) {
    try {
      const data = JSON.parse(script.textContent);
      const items = Array.isArray(data) ? data : (data.itemListElement || [data]);
      for (const item of items) {
        const prod = item.item || item;
        if (prod.name && prod.offers) {
          const off = Array.isArray(prod.offers) ? prod.offers[0] : prod.offers;
          const price = parseFloat(off.price) || null;
          if (price) {
            results.push({
              retailer: "vijaysales",
              title: prod.name,
              listed_price: price,
              url: prod.url || (prod["@id"] && prod["@id"].startsWith("http") ? prod["@id"] : ""),
              sku: prod.sku || "",
              in_stock: true,
              seller_name: "Vijay Sales Official",
              seller_rating: 4.6,
              return_policy: "7 Days Replacement / Return",
              warranty_notes: "1 Year Official Warranty",
              delivery_fee: 0,
            });
          }
        }
      }
      if (results.length > 0) return results.slice(0, 3);
    } catch (e) { /* skip */ }
  }

  // 2. DOM fallback
  const cards = doc.querySelectorAll('.vj-prod-card, .v-card, .product-card, a[href*="/product/"], div.item');
  for (let i = 0; i < Math.min(cards.length, 6); i++) {
    const card = cards[i];
    const titleEl = card.querySelector(".vj-prod-title, .product-title, h2, h3, .name") || card;
    const priceEl = card.querySelector(".vj-price, .prod-price, .price, .final-price, span.price");
    if (!priceEl) continue;
    const linkEl = card.closest("a") || card.querySelector("a");
    const href = linkEl ? (linkEl.getAttribute("href") || "") : "";
    const url = href.startsWith("http") ? href : "https://www.vijaysales.com" + href;
    const sku = href.match(/\/product\/([a-zA-Z0-9-]+)/)?.[1] || "";
    results.push({
      retailer: "vijaysales",
      title: titleEl.textContent.trim().substring(0, 200),
      listed_price: parseIndianPrice(priceEl.textContent),
      url: url,
      sku: sku,
      in_stock: true,
      seller_name: "Vijay Sales Official",
      seller_rating: 4.6,
      return_policy: "7 Days Replacement / Return",
      warranty_notes: "1 Year Official Warranty",
      delivery_fee: 0,
    });
    if (results.length >= 3) break;
  }
  return results;
}

function parseTataCliq(doc) {
  const results = [];
  // 1. JSON-LD
  const ldScripts = doc.querySelectorAll('script[type="application/ld+json"]');
  for (const script of ldScripts) {
    try {
      const data = JSON.parse(script.textContent);
      const items = Array.isArray(data) ? data : (data.itemListElement || [data]);
      for (const item of items) {
        const prod = item.item || item;
        if (prod.name && prod.offers) {
          const off = Array.isArray(prod.offers) ? prod.offers[0] : prod.offers;
          const price = parseFloat(off.price) || null;
          if (price) {
            results.push({
              retailer: "tatacliq",
              title: prod.name,
              listed_price: price,
              url: prod.url || "",
              sku: prod.sku || "",
              in_stock: true,
              seller_name: "Tata CLiQ Luxury / Authorized Merchant",
              seller_rating: 4.7,
              return_policy: "7 Days Return Policy",
              warranty_notes: "1 Year Manufacturer Warranty",
              delivery_fee: 0,
            });
          }
        }
      }
      if (results.length > 0) return results.slice(0, 3);
    } catch (e) { /* skip */ }
  }

  // 2. DOM fallback
  const cards = doc.querySelectorAll('.ProductModule__base, a[href*="/p-"], div.productCard');
  for (let i = 0; i < Math.min(cards.length, 6); i++) {
    const card = cards[i];
    const titleEl = card.querySelector(".ProductDescription__title, h2, h3") || card;
    const priceEl = card.querySelector(".ProductDescription__price, .price, span.price");
    if (!priceEl) continue;
    const linkEl = card.closest("a") || card.querySelector("a");
    const href = linkEl ? (linkEl.getAttribute("href") || "") : "";
    const url = href.startsWith("http") ? href : "https://www.tatacliq.com" + href;
    const sku = href.match(/\/p-([a-zA-Z0-9]+)/)?.[1] || "";
    results.push({
      retailer: "tatacliq",
      title: titleEl.textContent.trim().substring(0, 200),
      listed_price: parseIndianPrice(priceEl.textContent),
      url: url,
      sku: sku,
      in_stock: true,
      seller_name: "Tata CLiQ Luxury / Authorized Merchant",
      seller_rating: 4.7,
      return_policy: "7 Days Return Policy",
      warranty_notes: "1 Year Manufacturer Warranty",
      delivery_fee: 0,
    });
    if (results.length >= 3) break;
  }
  return results;
}

function parseDuckDuckGo(doc, retailer, domain) {
  const results = [];
  const items = doc.querySelectorAll(".result, .web-result, div.results_links");
  for (let i = 0; i < Math.min(items.length, 6); i++) {
    const item = items[i];
    const linkEl = item.querySelector("a.result__a, .result__title a, a.result__url");
    if (!linkEl) continue;
    let href = linkEl.getAttribute("href") || "";
    const uddg = href.match(/uddg=([^&]+)/);
    if (uddg) {
      try {
        href = decodeURIComponent(uddg[1]);
      } catch (_) {}
    }
    if (!href.startsWith("http") || !href.includes(domain)) continue;

    // Filter out search or category pages
    if (/(?:\/s\?|\/search|\/category|\/all-categories)/i.test(href)) continue;

    const titleEl = item.querySelector(".result__title, .result__a, h2") || linkEl;
    const title = titleEl.textContent.trim();

    const snippetEl = item.querySelector(".result__snippet, .snippet");
    const snippet = snippetEl ? snippetEl.textContent : "";
    const combinedText = title + " " + snippet;

    // Price extraction: ₹76,990 or Rs. 76,990 or Rs 76990
    const priceMatch = combinedText.match(/(?:₹|Rs\.?|INR)\s*([0-9,]{3,}(?:\.[0-9]+)?)/i);
    const price = priceMatch ? parseIndianPrice(priceMatch[1]) : null;

    const skuMatch = href.match(/(?:\/dp\/|\/p\/|\/product\/|pid=|\/p-)([a-zA-Z0-9_-]+)/i);
    const sku = skuMatch ? skuMatch[1] : "";

    if (title && href) {
      results.push({
        retailer: retailer,
        title: title,
        listed_price: price,
        url: href,
        sku: sku,
        in_stock: true,
      });
    }
    if (results.length >= 2) break;
  }
  return results;
}

// ── Message Listener ─────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "PARSE_RETAILER_HTML") {
    const { retailer, html } = message;
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, "text/html");

      let quotes = [];
      if (retailer === "amazon_in") {
        quotes = parseAmazon(doc);
      } else if (retailer === "flipkart") {
        quotes = parseFlipkart(doc);
      } else if (retailer === "croma") {
        quotes = parseCroma(doc);
      } else if (retailer === "reliance_digital") {
        quotes = parseRelianceDigital(doc);
      } else if (retailer === "vijaysales") {
        quotes = parseVijaySales(doc);
      } else if (retailer === "tatacliq") {
        quotes = parseTataCliq(doc);
      }

      sendResponse({ success: true, quotes });
    } catch (err) {
      sendResponse({ success: false, error: err.message, quotes: [] });
    }
    return true; // asynchronous reply
  } else if (message.type === "PARSE_DDG_HTML") {
    const { retailer, domain, html } = message;
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, "text/html");
      const quotes = parseDuckDuckGo(doc, retailer, domain);
      sendResponse({ success: true, quotes });
    } catch (err) {
      sendResponse({ success: false, error: err.message, quotes: [] });
    }
    return true;
  }
});
