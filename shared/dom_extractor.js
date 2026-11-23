/**
 * OneStop Shared DOM Extractor — Vanilla JS
 *
 * Runs inside BOTH the Chrome Extension (content_script) and
 * Android WebView (via evaluateJavascript). Detects the retailer
 * by hostname and extracts live product data from the current page DOM.
 *
 * Returns a JSON object matching the backend ClientPayload schema.
 */
(function () {
  "use strict";

  // ─── Utilities ───────────────────────────

  function qs(sel) {
    return document.querySelector(sel);
  }

  function qsa(sel) {
    return Array.from(document.querySelectorAll(sel));
  }

  function txt(sel) {
    var el = qs(sel);
    return el ? el.textContent.trim() : null;
  }

  function txts(sel) {
    return qsa(sel)
      .map(function (el) {
        return el.textContent.trim();
      })
      .filter(function (t) {
        return t.length > 0;
      });
  }

  function parsePrice(text) {
    if (!text) return null;
    var cleaned = text.replace(/,/g, "").replace(/[^\d.]/g, "");
    var num = parseFloat(cleaned);
    return isNaN(num) || num <= 0 ? null : num;
  }

  function metaContent(property) {
    var el =
      qs('meta[property="' + property + '"]') ||
      qs('meta[name="' + property + '"]');
    return el ? (el.getAttribute("content") || "").trim() : null;
  }

  function getJsonLd() {
    var scripts = qsa('script[type="application/ld+json"]');
    for (var i = 0; i < scripts.length; i++) {
      try {
        var data = JSON.parse(scripts[i].textContent);
        if (Array.isArray(data)) {
          for (var j = 0; j < data.length; j++) {
            if (data[j]["@type"] === "Product") return data[j];
          }
        } else if (data && data["@type"] === "Product") {
          return data;
        }
      } catch (e) {
        /* skip malformed JSON-LD */
      }
    }
    return null;
  }

  function findTextMatching(regex, maxResults) {
    maxResults = maxResults || 15;
    var results = [];
    var walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
      null,
      false
    );
    var node;
    while ((node = walker.nextNode())) {
      if (regex.test(node.textContent)) {
        var parent = node.parentElement;
        if (parent) {
          var container =
            parent.closest("li") || parent.closest("div") || parent;
          var t = container.textContent.trim();
          if (t.length > 15 && results.indexOf(t) === -1) {
            results.push(t.substring(0, 800));
          }
        }
      }
      if (results.length >= maxResults) break;
    }
    return results;
  }

  // ─── Retailer Detection ──────────────────

  function detectRetailer() {
    var h = window.location.hostname.toLowerCase();
    if (h.indexOf("amazon.in") !== -1) return "amazon_in";
    if (h.indexOf("flipkart.com") !== -1) return "flipkart";
    if (h.indexOf("croma.com") !== -1) return "croma";
    if (h.indexOf("reliancedigital.in") !== -1) return "reliance_digital";
    return null;
  }

  // ─── Amazon.in Extractor ─────────────────

  function extractAmazon() {
    var title = txt("#productTitle") || metaContent("og:title") || "";
    var asinMatch = window.location.pathname.match(/\/dp\/([A-Z0-9]{10})/);
    var asinInput = qs('input[name="ASIN"]');
    var sku = asinMatch
      ? asinMatch[1]
      : asinInput
        ? asinInput.value
        : "UNKNOWN";

    // Price: multiple fallbacks
    var listedPrice =
      parsePrice(txt("#corePrice_feature_div .a-price .a-offscreen")) ||
      parsePrice(txt(".priceToPay .a-offscreen")) ||
      parsePrice(txt("#priceblock_ourprice")) ||
      parsePrice(txt("#priceblock_dealprice")) ||
      parsePrice(txt("span.a-price-whole"));

    var mrp =
      parsePrice(txt(".basisPrice .a-offscreen")) ||
      parsePrice(txt(".a-price.a-text-price .a-offscreen"));

    // Stock
    var availText = (txt("#availability") || "").toLowerCase();
    var inStock =
      availText.indexOf("unavailable") === -1 &&
      availText.indexOf("out of stock") === -1;

    // Seller
    var sellerName =
      txt("#sellerProfileTriggerId") || txt("#merchant-info a") || null;
    if (!sellerName) {
      var mi = qs("#merchant-info");
      if (mi) {
        var m = mi.textContent.match(/(?:sold by|Sold by)\s+(.+?)(?:\.|$)/i);
        if (m) sellerName = m[1].trim();
      }
    }

    // Brand
    var brandEl = qs("#bylineInfo");
    var brand = brandEl
      ? brandEl.textContent
          .trim()
          .replace(/^(Visit the |Brand:\s*)/i, "")
          .replace(/ Store$/i, "")
          .trim()
      : null;

    // Bank offers
    var bankOffers = [];
    var liveOffers = [];
    var offerContainers = qsa(
      "#soWidget_feature_div .a-truncate-full, " +
        "#itembox-InstallmentCalculator .a-list-item, " +
        "#oferDisplayGroups .a-list-item, " +
        "#instantBankDiscount_feature_div"
    );
    offerContainers.forEach(function (el) {
      var t = el.textContent.trim();
      if (
        t.length > 15 &&
        /bank|cashback|discount|emi|instant|upi/i.test(t)
      ) {
        var cleanT = t.substring(0, 800);
        bankOffers.push(cleanT);
        liveOffers.push({
          title: cleanT.split("\n")[0].substring(0, 80),
          offer_type: /emi/i.test(cleanT) ? "emi" : /cashback|upi/i.test(cleanT) ? "cashback" : "bank",
          raw_text: cleanT,
          source: "live_page",
          conditions: "",
        });
      }
    });
    if (bankOffers.length === 0) {
      var fallbackMatches = findTextMatching(/Bank\s+Offer|No\s+Cost\s+EMI/i, 15);
      bankOffers = fallbackMatches;
      fallbackMatches.forEach(function (m) {
        liveOffers.push({
          title: m.substring(0, 60),
          offer_type: "bank",
          raw_text: m,
          source: "live_page",
          conditions: "",
        });
      });
    }

    // Coupons
    var coupons = [];
    var couponEl = qs("#couponBadgeRegularVpc, #vpcButton, label[for*='coupon']");
    if (couponEl) {
      var cText = couponEl.textContent.trim().substring(0, 600);
      coupons.push(cText);
      liveOffers.push({
        title: "Coupon Checkbox",
        offer_type: "coupon",
        raw_text: cText,
        source: "live_page",
        conditions: "Must apply checkbox on page",
      });
    }

    // Exchange
    var exchangeOffers = [];
    var exchangeEl = qs("#exchange-device-title, #tradeInButton_feature_div");
    if (exchangeEl)
      exchangeOffers.push(exchangeEl.textContent.trim().substring(0, 600));

    // Fulfillment & Return Policy
    var fulfillment = txt("#fulfillerInfo_feature_div") || txt("#tabular-buybox-truncate-1") || (txt("#merchant-info") && /fulfilled/i.test(txt("#merchant-info")) ? "Fulfilled by Amazon" : "Merchant Dispatch");
    var returnPolicy = txt("#productSupportAndReturnPolicy-feature_div") || txt("#icon-farm-container") || "10 Days Replacement / Warranty";
    var warrantyNotes = txt("[data-feature-name='warranty']") || "1 Year Manufacturer Warranty";

    // Delivery / Handling Fees
    var delivText = txt("#deliveryMessageMirId") || txt("#mir-layout-DELIVERY_BLOCK") || "";
    var delivFee = 0;
    var feeM = delivText.match(/(?:delivery|shipping|fee)[:\s]*(?:₹|Rs\.?)\s*([\d,]+)/i);
    if (feeM) delivFee = parsePrice(feeM[1]) || 0;

    // Dynamic sellers list on page
    var sellers = [];
    if (sellerName) {
      sellers.push({
        seller_name: sellerName,
        price: listedPrice,
        rating: null,
        review_count: null,
        fulfillment: fulfillment,
        return_policy: returnPolicy.substring(0, 100),
        warranty_notes: warrantyNotes.substring(0, 100),
        is_buy_box_winner: true,
      });
    }

    // Reviews
    var reviews = [];
    qsa('[data-hook="review-collapsed"] span, .review-text-content span')
      .slice(0, 10)
      .forEach(function (el) {
        var t = el.textContent.trim();
        if (t.length > 20) reviews.push(t.substring(0, 500));
      });

    // Specs
    var specs = {};
    qsa("#feature-bullets .a-list-item").forEach(function (li) {
      var t = li.textContent.trim();
      var ramM = t.match(/(\d+)\s*GB\s*RAM/i);
      if (ramM) specs.ram_gb = parseInt(ramM[1]);
      var storM = t.match(/(\d+)\s*(?:GB|TB)\s*(?:ROM|Storage|SSD)/i);
      if (storM) specs.storage_gb = parseInt(storM[1]);
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
      raw_bank_offers: bankOffers,
      raw_coupons: coupons,
      raw_exchange_offers: exchangeOffers,
      raw_live_offers: liveOffers,
      delivery_fee: delivFee,
      handling_fee: 0.0,
      recent_reviews: reviews,
      variant_specs: specs,
    };
  }

  // ─── Flipkart Extractor ──────────────────

  function extractFlipkart() {
    var jsonld = getJsonLd();

    // Title
    var title = "";
    if (jsonld) title = jsonld.name || "";
    if (!title) title = txt("span.VU-ZEz") || txt("h1 span") || txt("h1") || metaContent("og:title") || "";

    // FSN
    var sku = "UNKNOWN";
    var pidMatch = window.location.search.match(/pid=([A-Z0-9]+)/);
    if (pidMatch) {
      sku = pidMatch[1];
    } else {
      var pathParts = window.location.pathname.split("/");
      var last = pathParts[pathParts.length - 1];
      if (/^[A-Z0-9]{16}$/.test(last)) sku = last;
    }

    // Prices
    var listedPrice = null;
    if (jsonld && jsonld.offers) {
      var offers = Array.isArray(jsonld.offers) ? jsonld.offers[0] : jsonld.offers;
      listedPrice = parseFloat(offers.price) || null;
    }
    if (!listedPrice) {
      listedPrice =
        parsePrice(txt("div.Nx9bqj")) ||
        parsePrice(txt("div._30jeq3")) ||
        parsePrice(txt("div._16Jk6d"));
    }
    var mrp =
      parsePrice(txt("div.yRaY8j")) ||
      parsePrice(txt("div._3I9_wc"));

    // Stock
    var inStock = true;
    if (jsonld && jsonld.offers) {
      var avail = (Array.isArray(jsonld.offers) ? jsonld.offers[0] : jsonld.offers).availability || "";
      inStock = avail.toLowerCase().indexOf("outofstock") === -1;
    }
    var bodyText = document.body.textContent.substring(0, 5000).toLowerCase();
    if (bodyText.indexOf("currently unavailable") !== -1) inStock = false;

    // Brand
    var brand = null;
    if (jsonld && jsonld.brand) brand = jsonld.brand.name || null;

    // Seller
    var sellerName = txt("div#sellerName span span") || txt("div._1RLviY span") || null;
    if (!sellerName) {
      var soldByNodes = findTextMatching(/Sold\s+by/i, 1);
      if (soldByNodes.length > 0) {
        var m = soldByNodes[0].match(/Sold\s+by\s*:?\s*(.+?)(?:\s{2,}|$)/i);
        if (m) sellerName = m[1].trim();
      }
    }

    // Bank offers
    var bankOffers = findTextMatching(/Bank\s*Offer/i, 15);
    var bankKw = /hdfc|icici|axis|sbi|kotak|amex|no cost emi|cashback|instant/i;
    qsa("li").forEach(function (li) {
      var t = li.textContent.trim();
      if (bankKw.test(t) && t.indexOf("₹") !== -1 && bankOffers.indexOf(t) === -1) {
        bankOffers.push(t.substring(0, 800));
      }
    });

    var liveOffers = [];
    bankOffers.forEach(function (bo) {
      liveOffers.push({
        title: bo.split("\n")[0].substring(0, 80),
        offer_type: /emi/i.test(bo) ? "emi" : /cashback|upi/i.test(bo) ? "cashback" : "bank",
        raw_text: bo,
        source: "live_page",
        conditions: "",
      });
    });

    // Coupons
    var coupons = findTextMatching(/coupon|extra\s+off/i, 5);
    coupons.forEach(function (co) {
      liveOffers.push({
        title: "Flipkart Special Offer",
        offer_type: "coupon",
        raw_text: co,
        source: "live_page",
        conditions: "",
      });
    });

    // Exchange
    var exchangeOffers = findTextMatching(/exchange/i, 3).filter(function (t) {
      return t.indexOf("₹") !== -1;
    });

    // Delivery & Handling Fees
    var delivText = txt("div._2P_LDn") || txt("span._2P_LDn") || "";
    var delivFee = 0;
    var feeM = delivText.match(/(?:delivery|shipping)[:\s]*(?:₹|Rs\.?)\s*([\d,]+)/i);
    if (feeM) delivFee = parsePrice(feeM[1]) || 0;

    // Seller rating & Return policy & Warranty
    var sellerRating = parsePrice(txt("div#sellerName .XQDdHH") || txt("div._1RLviY .XQDdHH"));
    var returnPolicy = txt("li._3XINqE") || "7 Days Replacement / Return Policy";
    var warrantyNotes = txt("div._3hSWtk") || txt("div._12k-EH") || "1 Year Manufacturer Warranty";

    var sellers = [];
    if (sellerName) {
      sellers.push({
        seller_name: sellerName,
        price: listedPrice,
        rating: sellerRating,
        review_count: null,
        fulfillment: /assured/i.test(document.body.textContent.substring(0, 4000)) ? "Flipkart Assured" : "Standard Dispatch",
        return_policy: returnPolicy.substring(0, 100),
        warranty_notes: warrantyNotes.substring(0, 100),
        is_buy_box_winner: true,
      });
    }

    // Reviews
    var reviews = [];
    qsa("div.t-ZTKy, div._6K-7Co, p._2-N8zT")
      .slice(0, 10)
      .forEach(function (el) {
        var t = el.textContent.trim();
        if (t.length > 20) reviews.push(t.substring(0, 500));
      });

    // Specs
    var specs = {};
    qsa("table._14cfVK tr, div._3Rrcbo tr").forEach(function (row) {
      var cells = row.querySelectorAll("td");
      if (cells.length >= 2) {
        specs[cells[0].textContent.trim().toLowerCase().replace(/ /g, "_")] =
          cells[1].textContent.trim();
      }
    });

    return {
      title: title,
      sku: sku,
      brand: brand,
      listed_price: listedPrice,
      mrp: mrp,
      in_stock: inStock,
      seller_name: sellerName,
      seller_rating: sellerRating,
      sellers: sellers,
      raw_bank_offers: bankOffers.slice(0, 15),
      raw_coupons: coupons.slice(0, 5),
      raw_exchange_offers: exchangeOffers.slice(0, 3),
      raw_live_offers: liveOffers,
      delivery_fee: delivFee,
      handling_fee: 0.0,
      recent_reviews: reviews,
      variant_specs: specs,
    };
  }

  // ─── Croma Extractor ─────────────────────

  function extractCroma() {
    var jsonld = getJsonLd();
    var title = "";
    if (jsonld) title = jsonld.name || "";
    if (!title)
      title =
        txt("h1.pd-title") || txt("h1.product-title") || txt("h1") || metaContent("og:title") || "";

    var skuMatch = window.location.pathname.match(/\/p\/(\d+)/);
    var sku = skuMatch ? skuMatch[1] : "UNKNOWN";

    var listedPrice = null;
    if (jsonld && jsonld.offers) {
      var off = Array.isArray(jsonld.offers) ? jsonld.offers[0] : jsonld.offers;
      listedPrice = parseFloat(off.price) || null;
    }
    if (!listedPrice)
      listedPrice =
        parsePrice(txt("span.pdp-price span")) ||
        parsePrice(txt("span.new-price")) ||
        parsePrice(txt("span.amount"));
    var mrp =
      parsePrice(txt("span.old-price")) ||
      parsePrice(txt("span.mrpPrice"));

    var inStock = true;
    var bt = document.body.textContent.substring(0, 5000).toLowerCase();
    if (bt.indexOf("out of stock") !== -1 || bt.indexOf("currently unavailable") !== -1)
      inStock = false;

    var brand = null;
    if (jsonld && jsonld.brand) brand = jsonld.brand.name || null;
    if (!brand) brand = txt("a.brand-name") || txt("span.brand-name");

    var bankOffers = findTextMatching(/Bank\s*Offer|Cashback|Instant\s+Discount/i, 15);
    var coupons = findTextMatching(/coupon|promo\s*code/i, 5);

    var liveOffers = [];
    bankOffers.forEach(function (bo) {
      liveOffers.push({
        title: bo.split("\n")[0].substring(0, 80),
        offer_type: /emi/i.test(bo) ? "emi" : /cashback|upi/i.test(bo) ? "cashback" : "bank",
        raw_text: bo,
        source: "live_page",
        conditions: "",
      });
    });
    coupons.forEach(function (co) {
      liveOffers.push({
        title: "Croma Special Coupon",
        offer_type: "coupon",
        raw_text: co,
        source: "live_page",
        conditions: "",
      });
    });

    var sellers = [
      {
        seller_name: "Croma - A Tata Enterprise",
        price: listedPrice,
        rating: 4.8,
        review_count: null,
        fulfillment: "Direct Croma Store / Tata Fulfillment",
        return_policy: "14 Days Return Policy",
        warranty_notes: "1 Year Manufacturer Warranty",
        is_buy_box_winner: true,
      },
    ];

    var reviews = [];
    qsa("div.review-text, p.review-comment, div.user-review")
      .slice(0, 10)
      .forEach(function (el) {
        var t = el.textContent.trim();
        if (t.length > 20) reviews.push(t.substring(0, 500));
      });

    return {
      title: title,
      sku: sku,
      brand: brand,
      listed_price: listedPrice,
      mrp: mrp,
      in_stock: inStock,
      seller_name: "Croma - A Tata Enterprise",
      seller_rating: 4.8,
      sellers: sellers,
      raw_bank_offers: bankOffers,
      raw_coupons: coupons,
      raw_exchange_offers: [],
      raw_live_offers: liveOffers,
      delivery_fee: 0.0,
      handling_fee: 0.0,
      recent_reviews: reviews,
      variant_specs: {},
    };
  }

  // ─── Reliance Digital Extractor ──────────

  function extractRelianceDigital() {
    var jsonld = getJsonLd();
    var title = "";
    if (jsonld) title = jsonld.name || "";
    if (!title)
      title =
        txt("h1.pdp__product-title") ||
        txt("h1.product-name") ||
        txt("h1") ||
        metaContent("og:title") ||
        "";

    var skuMatch = window.location.pathname.match(/\/p\/(\d+)/);
    var sku = skuMatch ? skuMatch[1] : "UNKNOWN";

    var listedPrice = null;
    if (jsonld && jsonld.offers) {
      var off = Array.isArray(jsonld.offers) ? jsonld.offers[0] : jsonld.offers;
      listedPrice = parseFloat(off.price) || null;
    }
    if (!listedPrice)
      listedPrice =
        parsePrice(txt("span.pdp__offerPrice")) ||
        parsePrice(txt("span.pdp__priceSection__price"));
    var mrp =
      parsePrice(txt("span.pdp__mrp")) ||
      parsePrice(txt("span.pdp__priceSection__mrp"));

    var inStock = true;
    var bt = document.body.textContent.substring(0, 5000).toLowerCase();
    if (bt.indexOf("out of stock") !== -1 || bt.indexOf("sold out") !== -1)
      inStock = false;

    var brand = null;
    if (jsonld && jsonld.brand) brand = jsonld.brand.name || null;
    if (!brand) brand = txt("a.pdp__brandName") || txt("span.pdp__brandName");

    var bankOffers = findTextMatching(/Bank\s*Offer|Cashback|Instant/i, 15);
    var coupons = findTextMatching(/coupon|promo|extra\s+off/i, 5);

    var liveOffers = [];
    bankOffers.forEach(function (bo) {
      liveOffers.push({
        title: bo.split("\n")[0].substring(0, 80),
        offer_type: /emi/i.test(bo) ? "emi" : /cashback|upi/i.test(bo) ? "cashback" : "bank",
        raw_text: bo,
        source: "live_page",
        conditions: "",
      });
    });
    coupons.forEach(function (co) {
      liveOffers.push({
        title: "Reliance Digital Coupon",
        offer_type: "coupon",
        raw_text: co,
        source: "live_page",
        conditions: "",
      });
    });

    var sellers = [
      {
        seller_name: "Reliance Digital - Reliance Retail",
        price: listedPrice,
        rating: 4.7,
        review_count: null,
        fulfillment: "Direct Reliance Retail Fulfillment",
        return_policy: "7 Days Replacement / Return Policy",
        warranty_notes: "1 Year Official Brand Warranty",
        is_buy_box_winner: true,
      },
    ];

    var reviews = [];
    qsa("div.review-text, div.user-review-text, p.review-content")
      .slice(0, 10)
      .forEach(function (el) {
        var t = el.textContent.trim();
        if (t.length > 20) reviews.push(t.substring(0, 500));
      });

    return {
      title: title,
      sku: sku,
      brand: brand,
      listed_price: listedPrice,
      mrp: mrp,
      in_stock: inStock,
      seller_name: "Reliance Digital",
      seller_rating: 4.7,
      sellers: sellers,
      raw_bank_offers: bankOffers,
      raw_coupons: coupons,
      raw_exchange_offers: [],
      raw_live_offers: liveOffers,
      delivery_fee: 0.0,
      handling_fee: 0.0,
      recent_reviews: reviews,
      variant_specs: {},
    };
  }

  // ─── Generic / Universal Extractor ───────

  function extractGeneric() {
    var jsonld = getJsonLd();
    var title = "";
    if (jsonld && jsonld.name) title = jsonld.name;
    if (!title) title = metaContent("og:title") || metaContent("twitter:title") || txt("h1") || document.title || "";

    var brand = null;
    if (jsonld && jsonld.brand) {
      brand = typeof jsonld.brand === "string" ? jsonld.brand : jsonld.brand.name || null;
    }
    if (!brand) brand = metaContent("product:brand") || metaContent("og:brand");

    var listedPrice = null;
    if (jsonld && jsonld.offers) {
      var off = Array.isArray(jsonld.offers) ? jsonld.offers[0] : jsonld.offers;
      listedPrice = parseFloat(off.price) || null;
    }
    if (!listedPrice) {
      listedPrice =
        parsePrice(metaContent("product:price:amount")) ||
        parsePrice(metaContent("og:price:amount")) ||
        parsePrice(txt('[itemprop="price"]')) ||
        parsePrice(txt(".price, .product-price, .final-price, .special-price, .pdp-price"));
    }

    var mrp =
      parsePrice(txt(".mrp, .regular-price, .original-price, .strike, del, s")) ||
      parsePrice(metaContent("product:original_price:amount"));

    var inStock = true;
    if (jsonld && jsonld.offers) {
      var avail = (Array.isArray(jsonld.offers) ? jsonld.offers[0] : jsonld.offers).availability || "";
      if (avail.toLowerCase().indexOf("outofstock") !== -1) inStock = false;
    }

    var sku = "GENERIC-" + Math.abs(window.location.pathname.split("/").reduce(function(a, b){ return ((a << 5) - a) + b.charCodeAt(0) | 0; }, 0)).toString(36);
    if (jsonld && jsonld.sku) sku = String(jsonld.sku);

    var bankOffers = findTextMatching(/Bank\s*Offer|Cashback|Instant\s+Discount|EMI/i, 10);
    var coupons = findTextMatching(/coupon|promo|code|flat\s+₹/i, 5);

    var liveOffers = [];
    bankOffers.forEach(function (bo) {
      liveOffers.push({
        title: bo.split("\n")[0].substring(0, 80),
        offer_type: /emi/i.test(bo) ? "emi" : /cashback|upi/i.test(bo) ? "cashback" : "bank",
        raw_text: bo,
        source: "live_page",
        conditions: "",
      });
    });
    coupons.forEach(function (co) {
      liveOffers.push({
        title: "Promo Discount",
        offer_type: "coupon",
        raw_text: co,
        source: "live_page",
        conditions: "",
      });
    });

    var sellerName = window.location.hostname.replace(/^www\./, "");
    var sellers = [
      {
        seller_name: sellerName,
        price: listedPrice,
        rating: null,
        review_count: null,
        fulfillment: "Direct Online Store",
        return_policy: "Standard Store Return Policy",
        warranty_notes: "Standard Brand Warranty",
        is_buy_box_winner: true,
      },
    ];

    return {
      title: title.trim(),
      sku: sku,
      brand: brand,
      listed_price: listedPrice,
      mrp: mrp,
      in_stock: inStock,
      seller_name: sellerName,
      seller_rating: null,
      sellers: sellers,
      raw_bank_offers: bankOffers,
      raw_coupons: coupons,
      raw_exchange_offers: [],
      raw_live_offers: liveOffers,
      delivery_fee: 0.0,
      handling_fee: 0.0,
      recent_reviews: [],
      variant_specs: {},
    };
  }

  // ─── Main Entry Point ───────────────────

  var retailer = detectRetailer();
  var isGeneric = false;
  if (!retailer) {
    retailer = window.location.hostname.replace(/^www\./, "").replace(/[^a-zA-Z0-9]/g, "_").toLowerCase();
    isGeneric = true;
  }

  var extractors = {
    amazon_in: extractAmazon,
    flipkart: extractFlipkart,
    croma: extractCroma,
    reliance_digital: extractRelianceDigital,
  };

  var data = (!isGeneric && extractors[retailer]) ? extractors[retailer]() : extractGeneric();
  data.retailer = retailer;
  data.url = window.location.href;
  data.extracted_at = new Date().toISOString();
  data.source = "extension";

  var payload = JSON.stringify(data);

  // Chrome Extension: send to background script
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
    chrome.runtime.sendMessage({ type: "ONESTOP_PAYLOAD", payload: data });
  }

  // Android WebView: callback via injected interface
  if (typeof AndroidBridge !== "undefined" && AndroidBridge.onResult) {
    AndroidBridge.onResult(payload);
  }

  return payload;
})();

