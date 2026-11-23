/**
 * OneStop Universal Shared DOM Extractor — Vanilla JS
 *
 * Runs inside BOTH the Chrome Extension (via window.OneStopExtractDOM) and
 * Android WebView (via evaluateJavascript / AndroidBridge).
 *
 * Layer 1: Structured Data & Embedded SPA State (JSON-LD Product/@graph, #__NEXT_DATA__,
 *          inline Myntra __myx, Ajio/Nykaa __PRELOADED_STATE__, Shopify product meta).
 * Layer 2: Dedicated Indian & Universal D2C DOM Extractors (Amazon.in, Flipkart, Croma,
 *          Reliance Digital, Vijay Sales, Tata CLiQ, JioMart, Myntra, Ajio, Nykaa, D2C).
 * Fixes:
 *   - Strictly scopes seller extraction to Buy Box containers & rejects UI/nav strings
 *     ("Become a Seller", "Sell on", "Login", "More", "Cart"). Defaults D2C to "Official Direct Store".
 *   - Captures mandatory fees (Protect Promise Fee, Secured Packaging Fee, Handling/Platform Fee,
 *     shipping) alongside exact raw_text in raw_live_offers.
 *   - Never emits "Generic" as brand when a real brand exists in JSON-LD / meta / title.
 */
(function () {
  "use strict";

  // ─── Utilities ───────────────────────────

  function qs(sel, root) {
    try {
      return (root || document).querySelector(sel);
    } catch (_) {
      return null;
    }
  }

  function qsa(sel, root) {
    try {
      return Array.from((root || document).querySelectorAll(sel));
    } catch (_) {
      return [];
    }
  }

  function txt(sel, root) {
    var el = qs(sel, root);
    return el ? el.textContent.replace(/\s+/g, " ").trim() : null;
  }

  function parsePrice(text) {
    if (text == null) return null;
    if (typeof text === "number") return isNaN(text) || text <= 0 ? null : text;
    var s = String(text);
    // Match first Indian-formatted number (e.g. 76,990 or 1,19,990.00)
    var m = s.match(/(\d[\d,]*(?:\.\d{1,2})?)/);
    if (!m) return null;
    var num = parseFloat(m[1].replace(/,/g, ""));
    return isNaN(num) || num <= 0 ? null : num;
  }

  function metaContent(property) {
    var el =
      qs('meta[property="' + property + '"]') ||
      qs('meta[name="' + property + '"]');
    return el ? (el.getAttribute("content") || "").trim() : null;
  }

  // ─── Seller Sanitization ("Become a Seller" bug fix) ───

  var BAD_SELLER_PATTERNS = [
    /become\s+a\s+seller/i,
    /sell\s+on\b/i,
    /seller\s+hub/i,
    /\blogin\b/i,
    /\bsign\s+in\b/i,
    /^more$/i,
    /\bcart\b/i,
    /my\s+account/i,
    /\bhelp\b/i,
    /download\s+app/i,
    /customer\s+care/i,
    /wishlist/i,
    /track\s+order/i,
    /advertise/i,
    /gift\s+cards/i,
  ];

  var MARKETPLACE_RETAILERS = {
    amazon_in: true,
    flipkart: true,
    myntra: true,
    ajio: true,
    nykaa: true,
    tatacliq: true,
    jiomart: true,
    meesho: true,
    snapdeal: true,
  };

  function sanitizeSeller(raw, retailer) {
    if (raw) {
      var s = String(raw).replace(/\s+/g, " ").trim();
      s = s.replace(/^(?:sold\s+by|seller)\s*:?\s*/i, "").trim();
      s = s.replace(/\s*\d\.\d\s*★?.*$/, "").trim();
      var bad = !s || s.length < 2 || s.length > 80;
      if (!bad) {
        for (var i = 0; i < BAD_SELLER_PATTERNS.length; i++) {
          if (BAD_SELLER_PATTERNS[i].test(s)) {
            bad = true;
            break;
          }
        }
      }
      if (!bad && !/^(seller|store|home|menu|search|account|offers)$/i.test(s)) {
        return s;
      }
    }
    if (retailer && !MARKETPLACE_RETAILERS[retailer] && retailer !== "croma" && retailer !== "reliance_digital" && retailer !== "vijaysales") {
      return "Official Direct Store";
    }
    return null;
  }

  // ─── Brand Cleanup (No "Generic LG" bug) ───

  var KNOWN_BRANDS = [
    "Apple", "Samsung", "LG", "Sony", "OnePlus", "Xiaomi", "Redmi", "POCO", "Realme",
    "Nothing", "Motorola", "Google", "Vivo", "Oppo", "iQOO", "HP", "Dell", "Lenovo",
    "Asus", "Acer", "MSI", "Microsoft", "BenQ", "boAt", "JBL", "Bose", "Sennheiser",
    "Marshall", "Noise", "Dyson", "Philips", "Bosch", "Whirlpool", "Voltas", "Godrej",
    "Haier", "IFB", "Daikin", "Blue Star", "Carrier", "Hitachi", "Panasonic", "Hisense",
    "TCL", "Vu", "Canon", "Nikon", "GoPro", "Logitech", "SanDisk", "Nike", "Adidas",
    "Puma", "Reebok", "New Balance", "ASICS", "Skechers", "Crocs", "Zara", "H&M",
    "Uniqlo", "Levi's", "Allen Solly", "Van Heusen", "Peter England", "Louis Philippe",
    "Maybelline", "L'Oreal", "Lakme", "CeraVe", "Cetaphil", "Minimalist", "The Ordinary",
    "Dot & Key", "Plum", "Mamaearth", "Neutrogena", "Nivea", "Casio", "Titan", "Fossil",
    "Wakefit", "Pepperfry", "Lenskart", "Mokobara", "Samsonite", "Decathlon",
  ];

  function extractCleanBrand(rawBrand, title, jsonldBrand) {
    var invalid = /^(|generic|unknown|none|null|n\/a|unbranded|other|brand)$/i;
    var candidates = [jsonldBrand, rawBrand];
    for (var i = 0; i < candidates.length; i++) {
      var c = candidates[i];
      if (!c) continue;
      if (typeof c === "object" && c.name) c = c.name;
      var cleaned = String(c)
        .replace(/^(?:Visit the |Brand:\s*|by\s+)/i, "")
        .replace(/\s+Store$/i, "")
        .trim();
      if (!invalid.test(cleaned) && cleaned.length <= 50) {
        return cleaned;
      }
    }
    var t = (title || "").trim();
    if (t) {
      var bestIdx = Infinity;
      var bestBrand = null;
      for (var j = 0; j < KNOWN_BRANDS.length; j++) {
        var b = KNOWN_BRANDS[j];
        var esc = b.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        var bm = t.match(new RegExp("(^|[^a-zA-Z0-9])" + esc + "([^a-zA-Z0-9]|$)", "i"));
        if (bm) {
          var pos = bm.index + (bm[1] ? bm[1].length : 0);
          if (pos < bestIdx || (pos === bestIdx && (!bestBrand || b.length > bestBrand.length))) {
            bestIdx = pos;
            bestBrand = b;
          }
        }
      }
      if (bestBrand) return bestBrand;
      var first = t.split(/[\s\-|/,:(]+/)[0];
      if (first && first.length >= 2 && first.length <= 24 && !/^\d+$/.test(first) && !/^(new|latest|best|buy|men|women|unisex|kids|smart|generic)$/i.test(first)) {
        return first;
      }
    }
    return null;
  }

  // ─── Layer 1: Structured Data & Embedded SPA State ───

  function _findProductInNode(node) {
    if (!node || typeof node !== "object") return null;
    if (Array.isArray(node)) {
      for (var i = 0; i < node.length; i++) {
        var found = _findProductInNode(node[i]);
        if (found) return found;
      }
      return null;
    }
    var t = node["@type"];
    var types = Array.isArray(t) ? t : (t ? [t] : []);
    for (var k = 0; k < types.length; k++) {
      if (String(types[k]).toLowerCase() === "product") return node;
    }
    if (Array.isArray(node["@graph"])) {
      return _findProductInNode(node["@graph"]);
    }
    return null;
  }

  function getJsonLd() {
    var scripts = qsa('script[type="application/ld+json"]');
    for (var i = 0; i < scripts.length; i++) {
      try {
        var data = JSON.parse(scripts[i].textContent);
        var prod = _findProductInNode(data);
        if (prod) return prod;
      } catch (_) {}
    }
    return null;
  }

  function getEmbeddedSpaProduct() {
    var res = { title: null, brand: null, price: null, mrp: null, seller: null, sku: null, inStock: true };

    // 1. Next.js #__NEXT_DATA__
    var nextEl = qs("#__NEXT_DATA__");
    if (nextEl && nextEl.textContent) {
      try {
        var nd = JSON.parse(nextEl.textContent);
        var pp = (nd && nd.props && nd.props.pageProps) || {};
        var prod = pp.product || pp.productData || (pp.initialData && pp.initialData.product) || (pp.pdpData);
        if (prod && typeof prod === "object") {
          res.title = prod.name || prod.title || null;
          res.brand = (prod.brand && typeof prod.brand === "object") ? prod.brand.name : (prod.brand || null);
          res.price = parsePrice(prod.sellingPrice || prod.offerPrice || prod.discountedPrice || prod.price);
          res.mrp = parsePrice(prod.mrp || prod.listPrice || prod.originalPrice);
          res.seller = prod.sellerName || prod.seller || null;
          res.sku = prod.sku || prod.id || null;
        }
      } catch (_) {}
    }

    // 2. Inline <script> assignments (isolated content-script world cannot read page window.__myx directly)
    if (!res.price) {
      var scripts = qsa("script:not([src])");
      for (var i = 0; i < scripts.length; i++) {
        var code = scripts[i].textContent || "";
        if (code.length < 40 || code.length > 1500000) continue;

        // Myntra window.__myx
        if (code.indexOf("window.__myx") !== -1) {
          var myxM = code.match(/window\.__myx\s*=\s*(\{[\s\S]*\})\s*;?\s*$/);
          if (myxM) {
            try {
              var myx = JSON.parse(myxM[1]);
              var pdp = myx && myx.pdpData;
              if (pdp) {
                res.title = res.title || pdp.name || null;
                res.brand = res.brand || (pdp.brand && pdp.brand.name) || null;
                res.price = res.price || parsePrice(pdp.price && (pdp.price.discounted || pdp.price.mrp));
                res.mrp = res.mrp || parsePrice(pdp.price && pdp.price.mrp);
                res.sku = res.sku || (pdp.id ? String(pdp.id) : null);
                if (Array.isArray(pdp.sellers) && pdp.sellers[0]) {
                  res.seller = res.seller || pdp.sellers[0].sellerName || null;
                }
                break;
              }
            } catch (_) {}
          }
        }

        // Nykaa / Ajio window.__PRELOADED_STATE__
        if (code.indexOf("__PRELOADED_STATE__") !== -1) {
          var preM = code.match(/__PRELOADED_STATE__\s*=\s*(\{[\s\S]*?\})\s*;?(?:\n|$)/);
          if (preM) {
            try {
              var st = JSON.parse(preM[1]);
              var pd = (st.product && st.product.productDetails) || (st.productPage && st.productPage.product) || null;
              if (pd) {
                res.title = res.title || pd.name || pd.title || null;
                res.brand = res.brand || pd.brandName || pd.brand || null;
                res.price = res.price || parsePrice(pd.finalPrice || pd.offerPrice || (pd.price && pd.price.value));
                res.mrp = res.mrp || parsePrice(pd.mrp || (pd.wasPriceData && pd.wasPriceData.value));
                res.sku = res.sku || (pd.sku || pd.code || pd.id ? String(pd.sku || pd.code || pd.id) : null);
                break;
              }
            } catch (_) {}
          }
        }

        // Shopify ShopifyAnalytics.meta.product / var meta = {"product":...}
        if (code.indexOf("ShopifyAnalytics") !== -1 || code.indexOf("meta.product") !== -1) {
          var shopM = code.match(/"product"\s*:\s*(\{"id":[\s\S]*?\}\s*\})/);
          if (shopM) {
            try {
              var sp = JSON.parse(shopM[1]);
              res.brand = res.brand || sp.vendor || null;
              res.sku = res.sku || (sp.id ? String(sp.id) : null);
              if (Array.isArray(sp.variants) && sp.variants[0]) {
                res.title = res.title || sp.variants[0].name || null;
                // Shopify Analytics stores price in paise/cents (divide by 100 when > 100000 and integer)
                var rawP = parseFloat(sp.variants[0].price);
                if (!isNaN(rawP) && rawP > 0) {
                  res.price = rawP > 100000 && rawP % 100 === 0 ? rawP / 100 : rawP;
                }
              }
              break;
            } catch (_) {}
          }
        }
      }
    }
    return res;
  }

  // ─── Mandatory Fees & Live Offer Extraction ───

  var CROSS_SELL_COUPON_BLOCK_RE =
    /\b(?:additional\s+benefits|unlock|air\s*fryer|next\s+purchase|next\s+order|future\s+order|combo|buy\s+together|extra\s+off\s+on\s+select|spotify|youtube|ott|subscription|partner\s+offer|price\s+inclusive|inclusive\s+of\s+(?:cashback|coupon)|already\s+applied|exchange|with\s+this\s+item|frequently\s+bought|add-?on)\b/i;

  function isCrossSellOrInvalidCoupon(text) {
    if (!text) return true;
    var t = String(text).replace(/\s+/g, " ").trim();
    if (t.length < 4 || t.length > 350) return true;
    if (CROSS_SELL_COUPON_BLOCK_RE.test(t)) return true;
    return false;
  }

  function isInsideBlockedPromoSection(el) {
    var cur = el;
    for (var depth = 0; cur && depth < 5; depth++) {
      var heading = qs("h2, h3, h4, [class*='header'], [class*='title']", cur);
      if (heading) {
        var ht = (heading.textContent || "").trim();
        if (/additional\s+benefits|partner\s+offers?|frequently\s+bought|buy\s+together|combo\s+offers?/i.test(ht)) {
          return true;
        }
      }
      cur = cur.parentElement;
    }
    return false;
  }

  function extractAdvertisedLowestPrice(rootEl) {
    var bodyTxt = ((rootEl || document.body) ? ((rootEl || document.body).innerText || (rootEl || document.body).textContent) : "");
    var flatTxt = bodyTxt.replace(/\s+/g, " ").substring(0, 80000);
    var m = flatTxt.match(/(?:lowest\s+price\s+for\s+you|best\s+price\s+for\s+you|buy\s+at)[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)/i);
    if (m) return parsePrice(m[1]);
    var m2 = flatTxt.match(/(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:lowest\s+price\s+for\s+you|with\s+offers)/i);
    if (m2) return parsePrice(m2[1]);
    return null;
  }

  function extractMandatoryFees(rootEl) {
    var deliveryFee = 0;
    var handlingFee = 0;
    var feeOffers = [];
    var seen = {};
    var rawBody = (rootEl || document.body) ? ((rootEl || document.body).innerText || (rootEl || document.body).textContent || "") : "";
    var bodyTxt = rawBody.replace(/\s+/g, " ").substring(0, 150000);

    // 1. Handling / Packaging / Protect Promise / Offer Handling / Platform Fees (both amount-first AND label-first)
    var handlingSpecs = [
      {
        label: "Protect Promise Fee",
        res: [
          /\+?\s*(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?protect\s+promise\s+fee/i,
          /protect\s+promise\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)/i,
        ],
      },
      {
        label: "Secured Packaging Fee",
        res: [
          /\+?\s*(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?secured\s+packaging\s+fee/i,
          /secured\s+packaging\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)/i,
        ],
      },
      {
        label: "Offer Handling Fee",
        res: [
          /\+?\s*(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?offer\s+handling\s+fee/i,
          /offer\s+handling\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)/i,
        ],
      },
      {
        label: "Platform Fee",
        res: [
          /\+?\s*(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?platform\s+fee/i,
          /platform\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)/i,
        ],
      },
      {
        label: "Handling Fee",
        res: [
          /\+?\s*(?:₹|Rs\.?|INR)\s*([\d,]+)\s*(?:for\s+)?(?:payment\s+)?handling\s+fee/i,
          /(?:payment\s+)?handling\s+fee[^₹\d]{0,30}(?:₹|Rs\.?|INR)\s*([\d,]+)/i,
        ],
      },
    ];
    for (var i = 0; i < handlingSpecs.length; i++) {
      if (seen[handlingSpecs[i].label]) continue;
      for (var rIdx = 0; rIdx < handlingSpecs[i].res.length; rIdx++) {
        var m = bodyTxt.match(handlingSpecs[i].res[rIdx]);
        if (m) {
          var val = parsePrice(m[1]);
          if (val && val > 0 && val <= 2000) {
            seen[handlingSpecs[i].label] = true;
            handlingFee += val;
            feeOffers.push({
              title: handlingSpecs[i].label,
              offer_type: "fee",
              raw_text: m[0].replace(/\s+/g, " ").trim().substring(0, 200),
              source: "live_page",
              conditions: "Mandatory fee charged at checkout",
              payment_type: "fee",
              discount_inr: -val,
            });
            break;
          }
        }
      }
    }

    // 2. Delivery / Shipping fee (FREE delivery => 0)
    if (!/\bfree\s+delivery\b|\bfree\s+shipping\b/i.test(bodyTxt.substring(0, 15000))) {
      var dm = bodyTxt.match(/(?:delivery|shipping)\s*(?:charge|fee)s?[^₹\d]{0,20}(?:₹|Rs\.?|INR)\s*([\d,]+)/i);
      if (dm) {
        var dVal = parsePrice(dm[1]);
        if (dVal && dVal > 0 && dVal <= 2000) {
          deliveryFee = dVal;
          feeOffers.push({
            title: "Delivery Fee",
            offer_type: "fee",
            raw_text: dm[0].replace(/\s+/g, " ").trim().substring(0, 200),
            source: "live_page",
            conditions: "Delivery charge shown on page",
            payment_type: "fee",
            discount_inr: -dVal,
          });
        }
      }
    }

    return { deliveryFee: deliveryFee, handlingFee: handlingFee, feeOffers: feeOffers };
  }

  function findTextMatching(regex, maxResults, skipBlockedPromo) {
    maxResults = maxResults || 12;
    var results = [];
    if (!document.body) return results;
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    var node;
    while ((node = walker.nextNode())) {
      if (regex.test(node.textContent)) {
        var parent = node.parentElement;
        if (parent) {
          if (skipBlockedPromo && isInsideBlockedPromoSection(parent)) continue;
          var container = parent.closest("li") || parent.closest("div") || parent;
          var t = container.textContent.replace(/\s+/g, " ").trim();
          if (t.length > 12 && t.length < 450 && results.indexOf(t) === -1) {
            results.push(t);
          }
        }
      }
      if (results.length >= maxResults) break;
    }
    return results;
  }

  var BANK_OFFER_SIGNAL_RE =
    /\b(?:hdfc|icici|sbi|axis|amex|american\s+express|kotak|onecard|one\s+card|idfc|hsbc|rbl|bob|bank\s+of\s+baroda|federal|indusind|yes\s+bank|rupay|bank\s*offer|instant\s+discount|wow\s+deal|cashback|no\s+cost\s+emi|upi|google\s*pay|gpay|phonepe|paytm|cred|super\.money|bhim)\b/i;
  var OFFER_AMOUNT_SIGNAL_RE = /(?:₹|Rs\.?|INR)\s*[\d,]+|\d+(?:\.\d+)?\s*%/i;

  function parseStructuredOfferMeta(rawLine) {
    var line = String(rawLine || "").replace(/\s+/g, " ").trim();
    var bankM = line.match(
      /\b(flipkart\s+axis(?:\s+bank)?|flipkart\s+sbi|amazon\s+pay\s+icici|google\s*pay|gpay|phonepe|paytm|cred|super\.money|bhim|flipkart\s+upi|hdfc(?:\s+bank)?|icici(?:\s+bank)?|sbi(?:\s+card|\s+bank)?|axis(?:\s+bank)?|kotak(?:\s+bank)?|amex|american\s+express|onecard|idfc|hsbc|rbl|bob|indusind|yes\s+bank|federal|rupay|upi)\b/i
    );
    var low = line.toLowerCase();
    var paymentType = "bank";
    if (/upi|google\s*pay|gpay|phonepe|paytm|cred|super\.money|bhim/.test(low)) paymentType = "upi";
    else if (/no\s+cost\s+emi|\bemi\b/.test(low) && !/non-emi/.test(low)) paymentType = "emi";
    else if (/debit\s*card/.test(low) && !/credit\s*card/.test(low)) paymentType = "debit_card";
    else if (/credit\s*card/.test(low)) paymentType = "credit_card";

    var discInr = null;
    // Strip min spend before pulling discount amount
    var noMin = line.replace(/(?:min(?:imum)?\s*(?:purchase|spend|order|txn|transaction)[^₹\d]{0,20}|orders?\s+above\s*)(?:₹|Rs\.?|INR)?\s*[\d,]+/gi, "");
    var amtM = noMin.match(/(?:₹|Rs\.?|INR)\s*([\d,]+)/i);
    if (amtM) discInr = parsePrice(amtM[1]);

    return {
      bank_or_app: bankM ? bankM[1].replace(/\s+/g, " ").trim() : null,
      payment_type: paymentType,
      discount_inr: discInr,
    };
  }

  function cleanSingleOfferTileText(el) {
    if (!el) return "";
    // Concatenate visible child text nodes with spaces + bank logo img[alt] (handles Flipkart multi-node WOW DEAL tiles)
    var text = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    var imgs = qsa("img[alt]", el);
    for (var i = 0; i < imgs.length; i++) {
      var alt = (imgs[i].getAttribute("alt") || "").trim();
      if (alt && BANK_OFFER_SIGNAL_RE.test(alt) && text.toLowerCase().indexOf(alt.toLowerCase()) === -1) {
        text = text + " " + alt;
      }
    }
    text = text.replace(/\s*(?:see\s+details|view\s+plans|t&c|terms\s+&\s+conditions|apply|\d+\s+offers?\s*[>›]?)\s*$/gi, "").trim();
    return text;
  }

  function pushCleanOfferLines(rawText, out, maxCount) {
    if (!rawText) return;
    var limit = maxCount || 15;
    var normalizedWhole = String(rawText).replace(/\s+/g, " ").trim();
    normalizedWhole = normalizedWhole.replace(/\s*(?:see\s+details|view\s+plans|t&c|terms\s+&\s+conditions|apply|\d+\s+offers?\s*[>›]?)\s*$/gi, "").trim();

    // If the whole tile is already a compact single offer (e.g. "₹891 off\nHDFC\nCredit Card"),
    // keep it intact as ONE joined string instead of splitting on \n+ and destroying both halves!
    if (
      normalizedWhole.length >= 12 &&
      normalizedWhole.length <= 240 &&
      !CROSS_SELL_COUPON_BLOCK_RE.test(normalizedWhole) &&
      BANK_OFFER_SIGNAL_RE.test(normalizedWhole) &&
      OFFER_AMOUNT_SIGNAL_RE.test(normalizedWhole)
    ) {
      var amountMatches = normalizedWhole.match(/(?:₹|Rs\.?|INR)\s*[\d,]+/g) || [];
      if (amountMatches.length <= 2) {
        if (out.indexOf(normalizedWhole) === -1) {
          out.push(normalizedWhole);
        }
        return;
      }
    }

    var parts = String(rawText)
      .split(/\n+|\s*[•●▪]\s*|\s{3,}|(?<=\.)\s+(?=(?:Flat|Get|Upto|Up\s+to|Extra|Additional|\d+%|HDFC|ICICI|SBI|Axis|Kotak|Amex|OneCard|Bank\s+Offer|Flipkart))/i);
    for (var i = 0; i < parts.length; i++) {
      var line = parts[i].replace(/\s+/g, " ").trim();
      line = line.replace(/\s*(?:see\s+details|view\s+plans|t&c|terms\s+&\s+conditions|apply|\d+\s+offers?\s*[>›]?)\s*$/gi, "").trim();
      if (line.length < 12 || line.length > 420) continue;
      if (CROSS_SELL_COUPON_BLOCK_RE.test(line)) continue;
      if (!BANK_OFFER_SIGNAL_RE.test(line)) continue;
      if (!OFFER_AMOUNT_SIGNAL_RE.test(line)) continue;
      if (out.indexOf(line) === -1) {
        out.push(line);
        if (out.length >= limit) return;
      }
    }
  }

  function extractMultiNodeOfferTiles(maxCount) {
    var out = [];
    var limit = maxCount || 15;
    if (!document.body) return out;
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    var node;
    var visitedEls = [];
    while ((node = walker.nextNode())) {
      var nt = (node.textContent || "").trim();
      if (!nt || nt.length > 180) continue;
      if (!/(?:₹|Rs\.?|INR)\s*[\d,]+\s*off|instant\s+discount|wow\s+deal|cashback/i.test(nt)) continue;
      var cur = node.parentElement;
      if (!cur || isInsideBlockedPromoSection(cur)) continue;
      // Walk up 1..4 levels to find the offer tile holding BOTH the ₹ amount AND the bank/UPI name
      for (var up = 0; cur && up < 5; up++) {
        if (visitedEls.indexOf(cur) !== -1) break;
        var tileTxt = cleanSingleOfferTileText(cur);
        if (tileTxt.length > 320) break;
        if (
          tileTxt.length >= 12 &&
          BANK_OFFER_SIGNAL_RE.test(tileTxt) &&
          OFFER_AMOUNT_SIGNAL_RE.test(tileTxt) &&
          !CROSS_SELL_COUPON_BLOCK_RE.test(tileTxt)
        ) {
          visitedEls.push(cur);
          if (out.indexOf(tileTxt) === -1) {
            out.push(tileTxt);
          }
          break;
        }
        cur = cur.parentElement;
      }
      if (out.length >= limit) break;
    }
    return out;
  }

  function extractOffersFromSelectors(selectors, maxCount) {
    var out = [];
    var limit = maxCount || 15;
    for (var s = 0; s < selectors.length; s++) {
      var els = qsa(selectors[s]);
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (isInsideBlockedPromoSection(el)) continue;
        var txtVal = cleanSingleOfferTileText(el);
        if (txtVal.length > 320) {
          var children = qsa("li, p, .a-truncate-full, .a-list-item, [class*='offer']", el);
          for (var c = 0; c < children.length; c++) {
            if (isInsideBlockedPromoSection(children[c])) continue;
            pushCleanOfferLines(cleanSingleOfferTileText(children[c]), out, limit);
            if (out.length >= limit) return out;
          }
        } else {
          pushCleanOfferLines(txtVal, out, limit);
          if (out.length >= limit) return out;
        }
      }
    }
    return out;
  }

  function collectLiveOffers(bankOffers, coupons, feeOffers) {
    var live = [];
    var seen = {};
    function pushOffer(title, type, raw, cond, extra) {
      var key = raw.substring(0, 120);
      if (seen[key]) return;
      seen[key] = true;
      var item = {
        title: title.substring(0, 80),
        offer_type: type,
        raw_text: raw.substring(0, 600),
        source: "live_page",
        conditions: cond || "",
      };
      if (extra) {
        if (extra.bank_or_app) item.bank_or_app = extra.bank_or_app;
        if (extra.payment_type) item.payment_type = extra.payment_type;
        if (extra.discount_inr != null) item.discount_inr = extra.discount_inr;
        if (extra.coupon_scope) item.coupon_scope = extra.coupon_scope;
      }
      live.push(item);
    }
    (bankOffers || []).forEach(function (bo) {
      var meta = parseStructuredOfferMeta(bo);
      var type =
        meta.payment_type === "upi"
          ? "upi"
          : meta.payment_type === "emi"
          ? "emi"
          : /cashback/i.test(bo)
          ? "cashback"
          : "bank";
      pushOffer(bo.split("\n")[0], type, bo, "", meta);
    });
    (coupons || []).forEach(function (co) {
      if (isCrossSellOrInvalidCoupon(co)) return;
      pushOffer("Page Coupon", "coupon", co, "Apply coupon on page/checkout", {
        payment_type: "coupon",
        coupon_scope: "current_item_coupon",
      });
    });
    (feeOffers || []).forEach(function (fo) {
      pushOffer(fo.title, "fee", fo.raw_text, fo.conditions, {
        payment_type: "fee",
        discount_inr: fo.discount_inr,
      });
    });
    return live;
  }

  // ─── Retailer Detection ──────────────────

  function detectRetailer() {
    var h = window.location.hostname.toLowerCase();
    if (h.indexOf("amazon.in") !== -1) return "amazon_in";
    if (h.indexOf("flipkart.com") !== -1) return "flipkart";
    if (h.indexOf("croma.com") !== -1) return "croma";
    if (h.indexOf("reliancedigital.in") !== -1) return "reliance_digital";
    if (h.indexOf("vijaysales.com") !== -1) return "vijaysales";
    if (h.indexOf("tatacliq.com") !== -1) return "tatacliq";
    if (h.indexOf("jiomart.com") !== -1) return "jiomart";
    if (h.indexOf("myntra.com") !== -1) return "myntra";
    if (h.indexOf("ajio.com") !== -1) return "ajio";
    if (h.indexOf("nykaa.com") !== -1) return "nykaa";
    if (h.indexOf("meesho.com") !== -1) return "meesho";
    if (h.indexOf("snapdeal.com") !== -1) return "snapdeal";
    if (h.indexOf("pepperfry.com") !== -1) return "pepperfry";
    if (h.indexOf("lenskart.com") !== -1) return "lenskart";
    if (h.indexOf("boat-lifestyle.com") !== -1) return "boat";
    if (h.indexOf("lg.com") !== -1) return "lg_official";
    if (h.indexOf("samsung.com") !== -1) return "samsung_official";
    if (h.indexOf("apple.com") !== -1) return "apple_official";
    if (h.indexOf("sony.co.in") !== -1) return "sony_official";
    if (h.indexOf("nike.com") !== -1) return "nike_official";
    if (h.indexOf("adidas.co.in") !== -1) return "adidas_official";
    return h.replace(/^www\./, "").replace(/[^a-z0-9]/g, "_").substring(0, 20) || "generic";
  }

  // ─── Amazon.in Extractor ─────────────────

  function extractAmazon() {
    var jsonld = getJsonLd();
    var title = txt("#productTitle") || (jsonld && jsonld.name) || metaContent("og:title") || "";
    var asinMatch = window.location.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/);
    var asinInput = qs('input[name="ASIN"]');
    var sku = asinMatch ? asinMatch[1] : (asinInput && asinInput.value) ? asinInput.value : "UNKNOWN";

    var listedPrice =
      parsePrice(txt("#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen")) ||
      parsePrice(txt("#corePrice_feature_div .a-price .a-offscreen")) ||
      parsePrice(txt(".priceToPay .a-offscreen")) ||
      parsePrice(txt("#priceblock_ourprice")) ||
      parsePrice(txt("#priceblock_dealprice")) ||
      parsePrice(txt("#corePriceDisplay_desktop_feature_div .a-price-whole")) ||
      parsePrice(txt("span.a-price-whole"));

    var mrp =
      parsePrice(txt(".basisPrice .a-offscreen")) ||
      parsePrice(txt(".a-price.a-text-price .a-offscreen"));

    var availText = (txt("#availability") || "").toLowerCase();
    var inStock = availText.indexOf("unavailable") === -1 && availText.indexOf("out of stock") === -1;

    // Strictly Buy Box scoped seller
    var rawSeller =
      txt("#sellerProfileTriggerId") ||
      txt("#merchant-info a") ||
      txt("#tabular-buybox .tabular-buybox-text[tabular-attribute-name='Sold by'] a") ||
      txt("#tabular-buybox .tabular-buybox-text[tabular-attribute-name='Sold by']") ||
      null;
    if (!rawSeller) {
      var mi = qs("#merchant-info");
      if (mi) {
        var m = mi.textContent.match(/sold\s+by\s+(.+?)(?:\.|\sand\s|$)/i);
        if (m) rawSeller = m[1].trim();
      }
    }
    var sellerName = sanitizeSeller(rawSeller, "amazon_in");
    var brand = extractCleanBrand(txt("#bylineInfo"), title, jsonld && jsonld.brand);

    // Live On-Page Bank, Cashback, EMI & Side-Sheet Offers
    var bankOffers = extractOffersFromSelectors([
      "#itembox-InstantBankDiscount .a-truncate-full",
      "#itembox-InstantBankDiscount .offers-items-content",
      "#itembox-InstantBankDiscount",
      "#vsxoffers_feature_div .a-truncate-full",
      "#vsxoffers_feature_div .vsx-offers-desktop-lv__item",
      "#vsxoffers_feature_div .offers-items-content",
      "#itembox-Cashback .a-truncate-full",
      "#itembox-Cashback",
      "#itembox-NoCostEmi .a-truncate-full",
      "#itembox-NoCostEmi",
      "#instantBankDiscount_feature_div",
      "#soWidget_feature_div .a-truncate-full",
      "#offerDisplayGroups .a-list-item",
      "#sopp_feature_div .a-list-item",
      "#all-offers-display .a-section",
      ".a-popover-content .a-list-item",
      "[id*='InstantBankDiscount']",
    ], 15);
    if (bankOffers.length < 3) {
      findTextMatching(/(?:HDFC|ICICI|SBI|Axis|Amex|Kotak|OneCard|Instant\s+Discount|Bank\s+Offer|Cashback)/i, 12).forEach(function (t) {
        pushCleanOfferLines(t, bankOffers, 15);
      });
    }

    var coupons = [];
    var couponEl = qs("#couponBadgeRegularVpc, #vpcButton, label[for*='coupon']");
    if (couponEl) {
      var ct = couponEl.textContent.replace(/\s+/g, " ").trim();
      if (ct.length > 3) coupons.push(ct.substring(0, 400));
    }

    var exchangeOffers = [];
    var exEl = qs("#exchange-device-title, #tradeInButton_feature_div");
    if (exEl) exchangeOffers.push(exEl.textContent.replace(/\s+/g, " ").trim().substring(0, 400));

    var fees = extractMandatoryFees(qs("#desktop_buybox") || document.body);
    var liveOffers = collectLiveOffers(bankOffers, coupons, fees.feeOffers);

    var fulfillment = txt("#fulfillerInfo_feature_div") || txt("#tabular-buybox-truncate-0") || "Fulfilled by Amazon";
    var returnPolicy = txt("#productSupportAndReturnPolicy-feature_div") || "10 Days Replacement / Warranty";
    var warrantyNotes = txt("[data-feature-name='warranty']") || "1 Year Manufacturer Warranty";

    var sellers = sellerName ? [{
      seller_name: sellerName,
      price: listedPrice,
      rating: null,
      review_count: null,
      fulfillment: fulfillment.substring(0, 80),
      return_policy: returnPolicy.substring(0, 100),
      warranty_notes: warrantyNotes.substring(0, 100),
      is_buy_box_winner: true,
    }] : [];

    var reviews = [];
    qsa('[data-hook="review-collapsed"] span, .review-text-content span').slice(0, 8).forEach(function (el) {
      var t = el.textContent.replace(/\s+/g, " ").trim();
      if (t.length > 20) reviews.push(t.substring(0, 400));
    });

    var specs = {};
    qsa("#feature-bullets .a-list-item").forEach(function (li) {
      var t = li.textContent.trim();
      var ramM = t.match(/(\d+)\s*GB\s*RAM/i);
      if (ramM) specs.ram_gb = parseInt(ramM[1], 10);
      var storM = t.match(/(\d+)\s*(?:GB|TB)\s*(?:ROM|Storage|SSD)/i);
      if (storM) specs.storage_gb = parseInt(storM[1], 10) * (/tb/i.test(storM[0]) ? 1024 : 1);
    });

    return {
      title: title,
      sku: sku,
      brand: brand,
      listed_price: listedPrice,
      mrp: mrp,
      in_stock: inStock,
      seller_name: sellerName,
      seller_rating: null,
      sellers: sellers,
      raw_bank_offers: bankOffers.slice(0, 12),
      raw_coupons: coupons,
      raw_exchange_offers: exchangeOffers,
      raw_live_offers: liveOffers,
      delivery_fee: fees.deliveryFee,
      handling_fee: fees.handlingFee,
      recent_reviews: reviews,
      variant_specs: specs,
    };
  }

  // ─── Flipkart Extractor ──────────────────

  function extractFlipkart() {
    var jsonld = getJsonLd();
    var title = (jsonld && jsonld.name) || txt("span.VU-ZEz") || txt("h1.yhB1nd") || txt("h1") || metaContent("og:title") || "";

    var sku = "UNKNOWN";
    var pidMatch = window.location.search.match(/pid=([A-Z0-9]+)/i);
    if (pidMatch) sku = pidMatch[1];
    else {
      var pm = window.location.pathname.match(/\/p\/(itm[a-z0-9]+)/i);
      if (pm) sku = pm[1];
    }

    var listedPrice = null;
    if (jsonld && jsonld.offers) {
      var off = Array.isArray(jsonld.offers) ? jsonld.offers[0] : jsonld.offers;
      listedPrice = parsePrice(off && off.price);
    }
    if (!listedPrice) {
      listedPrice =
        parsePrice(txt("div.Nx9bqj.CxhGGd")) ||
        parsePrice(txt("div.Nx9bqj")) ||
        parsePrice(txt("div._30jeq3._16Jk6d")) ||
        parsePrice(txt("div._30jeq3"));
    }
    var mrp = parsePrice(txt("div.yRaY8j")) || parsePrice(txt("div._3I9_wc"));

    var inStock = true;
    if (jsonld && jsonld.offers) {
      var avail = (Array.isArray(jsonld.offers) ? jsonld.offers[0] : jsonld.offers).availability || "";
      if (/outofstock/i.test(avail)) inStock = false;
    }
    if (/currently\s+unavailable|sold\s+out/i.test(document.body.textContent.substring(0, 4000))) {
      inStock = false;
    }

    var brand = extractCleanBrand(null, title, jsonld && jsonld.brand);

    // Strictly Buy Box scoped seller — NEVER pick nav bar "Become a Seller"
    var sellerBox = qs("#sellerName") || qs("div._1RLviY") || qs("[id*='sellerName']");
    var rawSeller = sellerBox ? (txt("span span", sellerBox) || txt("span", sellerBox) || sellerBox.textContent) : null;
    var sellerName = sanitizeSeller(rawSeller, "flipkart");
    var sellerRating = sellerBox ? parsePrice(txt(".XQDdHH, ._3LWZlK", sellerBox)) : null;

    var bankOffers = extractOffersFromSelectors([
      "li.kF1mHg",
      "li._16eZjr",
      "div.cPHDOP li",
      "[class*='bankOffer'] li",
      "[class*='offer'] li",
      "[class*='wow'] div",
      "[data-testid*='offer']",
    ], 15);
    // Multi-node WOW DEAL / Bank offer tile walker (handles split ₹891 off / HDFC / Credit Card & ₹1,101 off / Google Pay UPI tiles)
    extractMultiNodeOfferTiles(15).forEach(function (t) {
      if (bankOffers.indexOf(t) === -1 && bankOffers.length < 15) {
        bankOffers.push(t);
      }
    });
    if (bankOffers.length < 3) {
      findTextMatching(/Bank\s*Offer|WOW\s*Deal|Apply\s*offers?|Instant\s+Discount|HDFC|ICICI|SBI|Axis|Kotak|Amex|OneCard|UPI|Google\s*Pay|PhonePe/i, 12, true).forEach(function (t) {
        pushCleanOfferLines(t, bankOffers, 15);
      });
    }

    // Strict current-item coupons only — reject cross-sell ("Unlock Air fryer coupon"), partner offers, and "price inclusive of"
    var coupons = findTextMatching(/apply\s+(?:₹|Rs\.?)?\s*[\d,]+\s*coupon|extra\s+(?:₹|Rs\.?)\s*[\d,]+\s+off/i, 4, true).filter(function (c) {
      return !isCrossSellOrInvalidCoupon(c);
    });
    var advertisedLowestPrice = extractAdvertisedLowestPrice(document.body);
    var exchangeOffers = findTextMatching(/exchange/i, 3, true).filter(function (t) { return /₹|Rs/i.test(t); });
    var fees = extractMandatoryFees(document.body);
    var liveOffers = collectLiveOffers(bankOffers, coupons, fees.feeOffers);

    var returnPolicy = txt("li._3XINqE") || "7 Days Service Center / Replacement";
    var warrantyNotes = txt("div._3hSWtk") || txt("div._12k-EH") || "1 Year Manufacturer Warranty";

    var sellers = sellerName ? [{
      seller_name: sellerName,
      price: listedPrice,
      rating: sellerRating,
      review_count: null,
      fulfillment: /assured/i.test(document.body.textContent.substring(0, 5000)) ? "Flipkart Assured" : "Standard Dispatch",
      return_policy: returnPolicy.substring(0, 100),
      warranty_notes: warrantyNotes.substring(0, 100),
      is_buy_box_winner: true,
    }] : [];

    var reviews = [];
    qsa("div.ZmyHeo, div.t-ZTKy, p.z9E0IG").slice(0, 8).forEach(function (el) {
      var t = el.textContent.replace(/\s+/g, " ").trim();
      if (t.length > 20) reviews.push(t.substring(0, 400));
    });

    return {
      title: title,
      sku: sku,
      brand: brand,
      listed_price: listedPrice,
      mrp: mrp,
      retailer_advertised_lowest_price: advertisedLowestPrice,
      in_stock: inStock,
      seller_name: sellerName,
      seller_rating: sellerRating,
      sellers: sellers,
      raw_bank_offers: bankOffers.slice(0, 12),
      raw_coupons: coupons.slice(0, 4),
      raw_exchange_offers: exchangeOffers.slice(0, 3),
      raw_live_offers: liveOffers,
      delivery_fee: fees.deliveryFee,
      handling_fee: fees.handlingFee,
      recent_reviews: reviews,
      variant_specs: {},
    };
  }

  // ─── Universal Extractor (Myntra, Ajio, Nykaa, Croma, Reliance, Vijay Sales, Tata CLiQ, JioMart, Shopify, D2C) ───

  function extractUniversal(retailer) {
    var jsonld = getJsonLd();
    var spa = getEmbeddedSpaProduct();

    // Title
    var title =
      (jsonld && jsonld.name) ||
      spa.title ||
      txt("h1.pdp-title") ||
      txt("h1.prod-name") ||
      txt("h1.pd-title") ||
      txt("h1.product-title") ||
      txt("h1") ||
      metaContent("og:title") ||
      document.title ||
      "";
    // Myntra splits brand (h1.pdp-title) and product name (h1.pdp-name)
    if (retailer === "myntra") {
      var mBrand = txt("h1.pdp-title");
      var mName = txt("h1.pdp-name");
      if (mBrand && mName) title = mBrand + " " + mName;
    }

    // SKU
    var sku = (jsonld && (jsonld.sku || jsonld.mpn || jsonld.gtin13)) || spa.sku || "";
    if (!sku) {
      var sm = window.location.pathname.match(/(?:\/p\/|\/product\/|\/products\/|\/p-|\/)(\d{5,14}|[a-zA-Z0-9_-]{6,32})(?:\/buy)?\/?$/i);
      sku = sm ? sm[1] : (window.location.pathname.replace(/[^a-zA-Z0-9]/g, "").slice(-24) || "SKU-WEB");
    }

    // Price
    var listedPrice = null;
    var mrp = null;
    var inStock = true;
    var rawSeller = spa.seller || null;

    if (jsonld && jsonld.offers) {
      var offersArr = Array.isArray(jsonld.offers) ? jsonld.offers : [jsonld.offers];
      for (var i = 0; i < offersArr.length; i++) {
        var o = offersArr[i];
        if (!o) continue;
        var op = parsePrice(o.price || o.lowPrice);
        if (op && (!listedPrice || op < listedPrice)) listedPrice = op;
        var hp = parsePrice(o.highPrice);
        if (hp && (!mrp || hp > mrp)) mrp = hp;
        if (o.availability && /outofstock|soldout/i.test(o.availability)) inStock = false;
        if (o.seller) rawSeller = rawSeller || (typeof o.seller === "object" ? o.seller.name : o.seller);
      }
    }
    if (!listedPrice) listedPrice = spa.price;
    if (!listedPrice) {
      listedPrice =
        parsePrice(metaContent("product:price:amount")) ||
        parsePrice(metaContent("og:price:amount")) ||
        parsePrice(txt(".pdp-price strong")) ||
        parsePrice(txt(".prod-sp")) ||
        parsePrice(txt("span.pdp-price span")) ||
        parsePrice(txt("span.new-price")) ||
        parsePrice(txt("[itemprop='price']")) ||
        parsePrice(txt("[data-testid='price']")) ||
        parsePrice(txt(".offer-price, .selling-price, .product-price, .price-item--sale, .price-item--regular"));
    }
    if (!listedPrice) {
      // Fallback: first visible ₹ price near top of main content
      var mainTxt = ((qs("main") || document.body).textContent || "").substring(0, 6000);
      var pm = mainTxt.match(/(?:₹|Rs\.?)\s*([\d,]{2,}(?:\.\d{1,2})?)/);
      if (pm) listedPrice = parsePrice(pm[1]);
    }

    if (!mrp) {
      mrp =
        spa.mrp ||
        parsePrice(txt(".pdp-mrp s, .pdp-mrp, .prod-cp, .old-price, .mrpPrice, del, s"));
      if (mrp && listedPrice && mrp < listedPrice) mrp = null;
    }

    if (/out\s+of\s+stock|sold\s+out|currently\s+unavailable/i.test((document.body.textContent || "").substring(0, 4500))) {
      var hasBuyBtn = !!qsa("button, a").find(function (b) {
        return /add\s+to\s+(bag|cart)|buy\s+now/i.test(b.textContent || "") && !b.disabled;
      });
      if (!hasBuyBtn) inStock = false;
    }

    var brand = extractCleanBrand(
      metaContent("product:brand") || txt(".pdp-title, .brand-name, [itemprop='brand']") || spa.brand,
      title,
      jsonld && jsonld.brand
    );

    // Scoped seller (never UI nav)
    if (!rawSeller) {
      if (retailer === "croma") rawSeller = "Croma - A Tata Enterprise";
      else if (retailer === "reliance_digital") rawSeller = "Reliance Retail Limited";
      else if (retailer === "vijaysales") rawSeller = "Vijay Sales";
      else {
        rawSeller = txt(".supplier-productSellerName, .seller-name, [itemprop='seller']");
      }
    }
    var sellerName = sanitizeSeller(rawSeller, retailer);

    var bankOffers = extractOffersFromSelectors([
      ".pdp-offers-offer",
      ".pdp-offers-container li",
      ".bank-offers li",
      ".offers-list li",
      ".offer-card",
      "[class*='bank-offer']",
      "[class*='offer-item']",
      "[class*='offerCard']",
      "[data-testid*='offer']",
    ], 15);
    if (bankOffers.length < 3) {
      findTextMatching(/Bank\s*Offer|Instant\s+Discount|Cashback|No\s+Cost\s+EMI|UPI\s+Offer|HDFC|ICICI|SBI|Axis|Kotak|Amex|OneCard/i, 12).forEach(function (t) {
        pushCleanOfferLines(t, bankOffers, 15);
      });
    }
    var coupons = findTextMatching(/apply\s+(?:₹|Rs\.?)?\s*[\d,]+\s*coupon|promo\s*code|use\s+code/i, 4, true).filter(function (c) {
      return !isCrossSellOrInvalidCoupon(c);
    });
    var advertisedLowestPrice = extractAdvertisedLowestPrice(document.body);
    var fees = extractMandatoryFees(document.body);
    var liveOffers = collectLiveOffers(bankOffers, coupons, fees.feeOffers);

    var sellers = sellerName ? [{
      seller_name: sellerName,
      price: listedPrice,
      rating: null,
      review_count: null,
      fulfillment: MARKETPLACE_RETAILERS[retailer] ? "Marketplace Dispatch" : "Official Direct Store",
      return_policy: "Standard Return Policy",
      warranty_notes: "Manufacturer Warranty",
      is_buy_box_winner: true,
    }] : [];

    return {
      title: title.replace(/\s+/g, " ").trim().substring(0, 300),
      sku: String(sku).substring(0, 64),
      brand: brand,
      listed_price: listedPrice,
      mrp: mrp,
      retailer_advertised_lowest_price: advertisedLowestPrice,
      in_stock: inStock,
      seller_name: sellerName,
      seller_rating: null,
      sellers: sellers,
      raw_bank_offers: bankOffers.slice(0, 12),
      raw_coupons: coupons.slice(0, 4),
      raw_exchange_offers: [],
      raw_live_offers: liveOffers,
      delivery_fee: fees.deliveryFee,
      handling_fee: fees.handlingFee,
      recent_reviews: [],
      variant_specs: {},
    };
  }

  // ─── Reusable Entry Point ────────────────

  function extractDOM() {
    var retailer = detectRetailer();
    var data;
    if (retailer === "amazon_in") {
      data = extractAmazon();
    } else if (retailer === "flipkart") {
      data = extractFlipkart();
    } else {
      data = extractUniversal(retailer);
    }

    data.retailer = retailer;
    data.url = window.location.href;
    data.extracted_at = new Date().toISOString();
    data.source = "extension";
    return data;
  }

  // Expose globally for content_script.js (SPA watcher) and popup scripting calls
  window.OneStopExtractDOM = extractDOM;

  // Android WebView bridge support (no-op in Chrome Extension)
  if (typeof AndroidBridge !== "undefined" && AndroidBridge.onResult) {
    try {
      AndroidBridge.onResult(JSON.stringify(extractDOM()));
    } catch (_) {}
  }
})();
