"use client";

import { useEffect, useState, use, useCallback } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowLeft,
  ExternalLink,
  Package,
  RefreshCw,
} from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useCardWallet } from "@/components/CardWalletProvider";
import { CashVsPointsSection } from "@/components/CashVsPointsSection";
import { Accordion } from "@/components/ui/Accordion";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { TableWrapper, Td, Th } from "@/components/ui/Table";
import { Toggle } from "@/components/ui/Toggle";
import {
  API_BASE,
  compareExchange,
  fetchProductDetails,
  fetchProductHistory,
  recalculateEffectivePrice,
  startCompareAllStores,
} from "@/lib/api";
import {
  ExchangeComparisonResponse,
  PriceHistoryResponse,
  ProductDetails,
  QuickCommerceQuote,
  StoreRankingChoice,
} from "@/lib/types";
import { formatINR, retailerLabel, trustTone, verdictInfo } from "@/lib/utils";

interface PageProps {
  params: Promise<{ id: string }>;
}

function firstSentence(text: string | null | undefined): string {
  if (!text) return "";
  const clean = text.trim();
  const idx = clean.search(/[.!?](\s|$)/);
  return idx > 0 ? clean.slice(0, idx + 1) : clean;
}

export default function ProductDecisionPage({ params }: PageProps) {
  const resolvedParams = use(params);
  const productId = resolvedParams.id;
  const { selectedIds, openWallet } = useCardWallet();

  const [product, setProduct] = useState<ProductDetails | null>(null);
  const [history, setHistory] = useState<PriceHistoryResponse | null>(null);
  const [historyDays, setHistoryDays] = useState<number>(90);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  const [effectiveData, setEffectiveData] = useState<any>(null);
  const [refreshingTerms, setRefreshingTerms] = useState(false);

  const [pincode, setPincode] = useState<string>("411001");
  const [streamingStores, setStreamingStores] = useState(false);
  const [streamStatus, setStreamStatus] = useState<string>("");
  const [quickCommerceQuotes, setQuickCommerceQuotes] = useState<QuickCommerceQuote[]>([]);
  const [storeSortMode, setStoreSortMode] = useState<"cheapest" | "fastest">("cheapest");

  const [newDevicePrice, setNewDevicePrice] = useState<number>(0);
  const [marketplaceQuote, setMarketplaceQuote] = useState<number>(10000);
  const [externalQuote, setExternalQuote] = useState<number>(11500);
  const [exchangeResult, setExchangeResult] = useState<ExchangeComparisonResponse | null>(null);
  const [exchangeLoading, setExchangeLoading] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const [prod, hist] = await Promise.all([
        fetchProductDetails(productId, selectedIds),
        fetchProductHistory(productId, historyDays),
      ]);
      setProduct(prod);
      setHistory(hist);
      if (prod && prod.listings.length > 0) {
        const lowest = Math.min(...prod.listings.map((l) => l.current_listed_price || 999999));
        setNewDevicePrice(lowest);
      }
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [productId, historyDays]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (!product || !product.listings || product.listings.length === 0) return;
    const cheapestListing = product.listings.reduce((prev, curr) =>
      (curr.current_listed_price || 999999) < (prev.current_listed_price || 999999) ? curr : prev
    );
    if (!cheapestListing.current_listed_price) return;

    Promise.all([
      recalculateEffectivePrice(
        cheapestListing.current_listed_price,
        selectedIds,
        cheapestListing.retailer,
        0,
        0,
        cheapestListing.bank_offers || []
      ),
      fetchProductDetails(productId, selectedIds),
    ])
      .then(([eff, refreshed]) => {
        setEffectiveData(eff);
        if (refreshed) {
          setProduct((prev) =>
            prev
              ? {
                  ...prev,
                  cash_vs_points: refreshed.cash_vs_points,
                  ranked_store_choices: refreshed.ranked_store_choices,
                  stacked_offers: refreshed.stacked_offers,
                }
              : prev
          );
        }
      })
      .catch(() => {});
  }, [selectedIds, productId]);

  const handleRefreshLiveTerms = async () => {
    setRefreshingTerms(true);
    try {
      const updated = await fetchProductDetails(productId, selectedIds, true);
      if (updated) setProduct(updated);
    } finally {
      setRefreshingTerms(false);
    }
  };

  const handleStreamAllStores = async () => {
    if (!product || streamingStores) return;
    const primaryListing = product.listings?.[0];
    const cleanPin = pincode.replace(/\D/g, "").slice(0, 6) || "411001";
    setPincode(cleanPin);
    setStreamingStores(true);
    setStreamStatus(`Checking stores for ${cleanPin}...`);

    try {
      const job = await startCompareAllStores(
        {
          retailer: primaryListing?.retailer || "store",
          url: primaryListing?.listing_url || "https://www.amazon.in",
          title: `${product.brand || ""} ${product.model_name || ""}`.trim(),
          brand: product.brand,
          listed_price: primaryListing?.current_listed_price || 1000,
          variant_specs: product.variant_specs || {},
          pincode: cleanPin,
        },
        cleanPin,
        selectedIds
      );

      const es = new EventSource(`${API_BASE}/stream-compare/${encodeURIComponent(job.job_id)}`);

      es.addEventListener("progress", (ev) => {
        try {
          const d = JSON.parse((ev as MessageEvent).data);
          if (d.message) setStreamStatus(d.message);
        } catch {}
      });

      es.addEventListener("store_quote", (ev) => {
        try {
          const d = JSON.parse((ev as MessageEvent).data);
          const q = d.quote;
          if (!q) return;
          setProduct((prev) => {
            if (!prev) return prev;
            const cur = [...(prev.ranked_store_choices || [])];
            if (cur.some((s) => s.url === q.url || s.retailer === q.retailer)) return prev;
            const cashP = Math.round((q.listed_price || 0) + (q.delivery_fee || 0));
            cur.push({
              retailer: q.retailer,
              store_name: retailerLabel(q.retailer),
              url: q.url,
              listed_price: q.listed_price,
              checkout_cash_price: cashP,
              best_user_card_net_price: cashP,
              effective_price: cashP,
              in_stock: q.in_stock !== false,
              is_current_store: false,
              seller_name: q.seller_name || "Verified seller",
              peace_of_mind_score: 78,
              return_terms: "Standard replacement",
              warranty_terms: "1 Year Brand Warranty",
              overall_score: 80,
              rank_badge: "Live",
              is_overall_winner: false,
              store_bucket: q.store_bucket || (q.in_stock === false ? "lesser_known_store" : "exact_match"),
              variant_diff_badges: q.variant_diff_badges || [],
              eta_label: q.eta_label || (q.in_stock === false ? "Out of stock" : "2–4 days"),
            });
            return { ...prev, ranked_store_choices: cur };
          });
        } catch {}
      });

      es.addEventListener("quick_commerce_quote", (ev) => {
        try {
          const d = JSON.parse((ev as MessageEvent).data);
          const qc = d.quote as QuickCommerceQuote;
          if (!qc) return;
          setQuickCommerceQuotes((prev) =>
            prev.some((x) => x.platform === qc.platform) ? prev : [...prev, qc]
          );
        } catch {}
      });

      es.addEventListener("complete", (ev) => {
        setStreamingStores(false);
        try {
          const d = JSON.parse((ev as MessageEvent).data);
          setStreamStatus(
            `Checked ${d.stores_count ?? 0} stores and ${d.quick_commerce_count ?? 0} quick-delivery apps for ${cleanPin}.`
          );
        } catch {
          setStreamStatus("Store comparison finished.");
        }
        es.close();
      });

      es.onerror = () => {
        setStreamingStores(false);
        es.close();
      };
    } catch {
      setStreamingStores(false);
      setStreamStatus("Couldn't reach live comparison right now.");
    }
  };

  const handleExchangeCompare = async () => {
    if (!newDevicePrice) return;
    setExchangeLoading(true);
    try {
      const res = await compareExchange(newDevicePrice, marketplaceQuote, externalQuote);
      setExchangeResult(res);
    } catch {
      // ignore
    } finally {
      setExchangeLoading(false);
    }
  };

  // ── Loading Skeleton ──
  if (loading) {
    return (
      <div className="space-y-6">
        <div className="space-y-2">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-5 w-48" />
        </div>
        <Skeleton className="h-36 w-full rounded-[12px]" />
        <Skeleton className="h-64 w-full rounded-[12px]" />
        <Skeleton className="h-64 w-full rounded-[12px]" />
      </div>
    );
  }

  // ── Error / Not Found State ──
  if (loadError || !product) {
    return (
      <Card className="p-8 text-center space-y-4 max-w-md mx-auto mt-10">
        <AlertCircle strokeWidth={1.5} className="w-6 h-6 text-danger mx-auto" />
        <div className="space-y-1">
          <h1 className="text-[20px] font-semibold text-text">Couldn&apos;t load this product.</h1>
          <p className="text-[13px] text-muted">
            {loadError
              ? "There was a problem reaching the backend."
              : "This product could not be found in the database."}
          </p>
        </div>
        <div className="flex items-center justify-center gap-3">
          <Button variant="outline" onClick={loadAll}>
            Retry
          </Button>
          <Link href="/">
            <Button>Back to Home</Button>
          </Link>
        </div>
      </Card>
    );
  }

  const primaryListing =
    product.listings && product.listings.length > 0
      ? product.listings.reduce((prev, curr) =>
          (curr.current_listed_price || 999999) < (prev.current_listed_price || 999999) ? curr : prev
        )
      : {
          retailer: "store",
          listing_url: "",
          listed_price: 0,
          current_listed_price: 0,
          current_mrp: null,
          in_stock: false,
          seller_name: null,
          trust_score: null,
        };

  const todayPrice = primaryListing.current_listed_price || primaryListing.listed_price || 0;
  const mrpPrice = primaryListing.current_mrp;
  const imageUrl =
    typeof product.variant_specs?.image_url === "string"
      ? product.variant_specs.image_url
      : null;

  // ── Verdict computation ──
  const verdict = verdictInfo(product.buy_wait?.verdict || "FAIR_PRICE");
  const bestCard = effectiveData?.best_card;
  const bestPriceValue =
    product.llm_summary?.final_net_price ||
    product.cash_vs_points?.best_user_card_net_price_cash ||
    bestCard?.effective_price ||
    todayPrice;
  const bestStoreName =
    product.llm_summary?.best_store
      ? retailerLabel(product.llm_summary.best_store)
      : retailerLabel(primaryListing.retailer);
  const verdictReason =
    firstSentence(product.buy_wait?.reasoning) ||
    firstSentence(product.llm_summary?.why_this_wins) ||
    "Current price is within the normal range for this product.";

  // ── Store Comparison rows (merged online + quick-commerce) ──
  const baseStores: StoreRankingChoice[] =
    product.ranked_store_choices && product.ranked_store_choices.length > 0
      ? product.ranked_store_choices
      : product.listings.map((l, idx) => ({
          retailer: l.retailer,
          store_name: retailerLabel(l.retailer),
          url: l.listing_url,
          listed_price: l.current_listed_price || l.listed_price,
          checkout_cash_price: l.current_listed_price || l.listed_price,
          best_user_card_net_price: l.current_listed_price || l.listed_price,
          effective_price: l.current_listed_price || l.listed_price,
          in_stock: l.in_stock,
          is_current_store: idx === 0,
          seller_name: l.seller_name || "Official / Direct",
          peace_of_mind_score: l.trust_score ?? 75,
          return_terms: "Standard replacement",
          warranty_terms: "1 Year Brand Warranty",
          overall_score: 80,
          rank_badge: idx === 0 ? "Best price" : "",
          is_overall_winner: idx === 0 && l.in_stock,
          store_bucket: "exact_match",
          eta_label: "2–4 days",
        }));

  type UnifiedRow = {
    key: string;
    store: string;
    subtext: string;
    price: number;
    delivery: string;
    trustScore: number;
    inStock: boolean;
    isBest: boolean;
    isQuick: boolean;
    url: string;
    etaRank: number;
  };

  const unifiedRows: UnifiedRow[] = [
    ...baseStores
      .filter((s) => s.store_bucket !== "variant_mismatch")
      .map((s, idx) => {
        const price = s.best_user_card_net_price ?? s.effective_price ?? s.listed_price;
        const eta = s.eta_label || (s.in_stock === false ? "Out of stock" : "2–4 days");
        const lowerEta = eta.toLowerCase();
        const etaRank =
          s.in_stock === false ? 9999 : lowerEta.includes("min") ? 1 : lowerEta.includes("1-2") ? 2 : 3;
        return {
          key: `store-${s.retailer}-${idx}`,
          store: s.store_name || retailerLabel(s.retailer),
          subtext: s.seller_name || "Official / Direct",
          price,
          delivery: s.in_stock === false ? "Out of stock" : eta,
          trustScore: s.peace_of_mind_score ?? 75,
          inStock: s.in_stock !== false,
          isBest: false,
          isQuick: false,
          url: s.url,
          etaRank,
        };
      }),
    ...quickCommerceQuotes.map((qc, idx) => ({
      key: `qc-${qc.platform}-${idx}`,
      store: qc.app_name,
      subtext: "10-min delivery · fees included",
      price: qc.total_payable_inr,
      delivery: qc.in_stock ? qc.eta_minutes_label : "Out of stock",
      trustScore: 85,
      inStock: qc.in_stock,
      isBest: false,
      isQuick: true,
      url: qc.url,
      etaRank: qc.in_stock ? 0 : 9999,
    })),
  ].sort((a, b) => {
    if (a.inStock !== b.inStock) return a.inStock ? -1 : 1;
    if (storeSortMode === "fastest" && a.etaRank !== b.etaRank) return a.etaRank - b.etaRank;
    return a.price - b.price;
  });

  if (unifiedRows.length > 0 && unifiedRows[0].inStock) {
    unifiedRows[0].isBest = true;
  }

  // ── Chart Data ──
  const chartPoints = (history?.observations || []).map((pt, idx) => ({
    idx,
    time: new Date(pt.observed_at).toLocaleDateString("en-IN", {
      month: "short",
      day: "numeric",
    }),
    price: pt.listed_price,
    retailer: retailerLabel(pt.retailer),
  }));

  const minPoint =
    chartPoints.length > 0
      ? chartPoints.reduce((min, p) => (p.price < min.price ? p : min), chartPoints[0])
      : null;
  const lastPoint = chartPoints.length > 0 ? chartPoints[chartPoints.length - 1] : null;

  // ── Accordion Summaries ──
  const offerCount = product.stacked_offers?.steps?.length || 0;
  const offersSummary =
    offerCount > 0 && product.stacked_offers
      ? `${offerCount} step${offerCount > 1 ? "s" : ""} · Save ${formatINR(product.stacked_offers.total_savings)}`
      : "No extra coupon or bank stack applied";

  const topSeller = product.seller_report?.sellers?.[0];
  const sellerScore = topSeller?.peace_of_mind_score ?? primaryListing.trust_score ?? 75;
  const sellerTrustInfo = trustTone(sellerScore);
  const sellerSummary = `Seller trust: ${sellerTrustInfo.label} · ${sellerScore}/100${
    topSeller?.seller_name || primaryListing.seller_name
      ? ` · ${topSeller?.seller_name || primaryListing.seller_name}`
      : ""
  }`;

  const warrantySummary =
    baseStores[0]?.warranty_terms
      ? `${baseStores[0].warranty_terms} · ${baseStores[0].return_terms}`
      : "1 Year Brand Warranty · Save your invoice in Vault";

  return (
    <div className="space-y-6">
      {/* ── a. Product Header ── */}
      <header className="space-y-4">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-[13px] font-medium text-muted hover:text-text transition-colors duration-150 min-h-[36px]"
        >
          <ArrowLeft strokeWidth={1.5} className="w-4 h-4" />
          <span>Home</span>
        </Link>

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-start gap-4 min-w-0">
            <div className="w-14 h-14 rounded-[8px] border border-border bg-surface-2 flex items-center justify-center flex-shrink-0 overflow-hidden">
              {imageUrl ? (
                <img
                  src={imageUrl}
                  alt={product.model_name}
                  className="w-full h-full object-contain p-1"
                />
              ) : (
                <Package strokeWidth={1.5} className="w-5 h-5 text-muted" />
              )}
            </div>

            <div className="min-w-0 space-y-1">
              <div className="text-[12px] text-muted">
                {product.brand} · {retailerLabel(primaryListing.retailer)}
                {!primaryListing.in_stock && " · Out of stock"}
              </div>
              <h1 className="text-[22px] sm:text-[28px] font-semibold text-text leading-tight">
                {product.model_name}
              </h1>
              <div className="flex items-baseline gap-2.5 pt-0.5">
                <span className="font-mono text-[20px] font-semibold tabular-nums text-text">
                  {formatINR(todayPrice)}
                </span>
                {mrpPrice && mrpPrice > todayPrice && (
                  <span className="font-mono text-[13px] tabular-nums text-muted line-through">
                    {formatINR(mrpPrice)}
                  </span>
                )}
                <span className="text-[12px] text-muted">price today</span>
              </div>
            </div>
          </div>

          {primaryListing.listing_url && (
            <a
              href={primaryListing.listing_url}
              target="_blank"
              rel="noreferrer"
              className="self-start sm:self-center flex-shrink-0"
            >
              <Button>
                <span>Open on {retailerLabel(primaryListing.retailer)}</span>
                <ExternalLink strokeWidth={1.5} className="w-4 h-4" />
              </Button>
            </a>
          )}
        </div>
      </header>

      {/* ── b. Verdict Card (primary visual anchor) ── */}
      <Card tone="accent" className="p-5 sm:p-6 space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="space-y-2 min-w-0">
            <Badge tone={verdict.tone}>{verdict.label}</Badge>
            <p className="text-[15px] text-text leading-relaxed">{verdictReason}</p>
          </div>

          <div className="sm:text-right flex-shrink-0 border-t sm:border-t-0 pt-3 sm:pt-0 border-border">
            <div className="text-[12px] text-muted">Best price</div>
            <div className="font-mono text-[28px] font-semibold tabular-nums text-accent leading-tight">
              {formatINR(bestPriceValue)}
            </div>
            <div className="text-[12px] text-muted mt-0.5">on {bestStoreName}</div>
          </div>
        </div>

        {selectedIds.length === 0 && (
          <div className="pt-2 border-t border-border flex flex-wrap items-center justify-between gap-2 text-[13px]">
            <span className="text-muted">No cards selected — add yours to see card savings.</span>
            <button
              type="button"
              onClick={openWallet}
              className="font-medium text-accent hover:underline min-h-[36px]"
            >
              Choose cards
            </button>
          </div>
        )}
      </Card>

      {/* ── c. Store Comparison ── */}
      <Card className="p-5 sm:p-6 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h2 className="text-[20px] font-semibold text-text">Store comparison</h2>
            <p className="text-[13px] text-muted mt-0.5">
              {streamStatus || `${unifiedRows.length} verified store${unifiedRows.length === 1 ? "" : "s"}`}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Toggle
              ariaLabel="Sort stores"
              value={storeSortMode}
              onChange={setStoreSortMode}
              options={[
                { value: "cheapest", label: "Cheapest" },
                { value: "fastest", label: "Fastest" },
              ]}
            />

            <input
              type="text"
              aria-label="Delivery pincode"
              maxLength={6}
              value={pincode}
              onChange={(e) => setPincode(e.target.value.replace(/\D/g, ""))}
              className="w-20 min-h-[40px] px-2.5 rounded-[8px] border border-border bg-surface text-center font-mono text-[13px] tabular-nums text-text focus:border-accent focus:outline-none"
              placeholder="411001"
            />

            <Button
              variant="outline"
              size="sm"
              onClick={handleStreamAllStores}
              disabled={streamingStores}
            >
              <RefreshCw
                strokeWidth={1.5}
                className={`w-3.5 h-3.5 ${streamingStores ? "animate-spin" : ""}`}
              />
              <span>{streamingStores ? "Checking..." : "Compare across stores"}</span>
            </Button>
          </div>
        </div>

        {unifiedRows.length === 0 && !streamingStores ? (
          <p className="text-[13px] text-muted py-4">
            No store listings found yet. Click Compare across stores to check live prices.
          </p>
        ) : (
          <>
            {/* Desktop Table */}
            <div className="hidden md:block">
              <TableWrapper>
                <thead>
                  <tr>
                    <Th>Store</Th>
                    <Th>Price</Th>
                    <Th>Delivery</Th>
                    <Th>Trust</Th>
                    <Th className="text-right">Link</Th>
                  </tr>
                </thead>
                <tbody>
                  {unifiedRows.map((row) => {
                    const trust = trustTone(row.trustScore);
                    return (
                      <tr
                        key={row.key}
                        className={row.isBest ? "bg-accent-subtle/40" : "hover:bg-surface-2/40"}
                      >
                        <Td>
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-text">{row.store}</span>
                            {row.isBest && <Badge tone="accent">Best</Badge>}
                            {row.isQuick && <Badge tone="neutral">10-min</Badge>}
                          </div>
                          <div className="text-[12px] text-muted">{row.subtext}</div>
                        </Td>
                        <Td className="font-mono font-medium tabular-nums text-text">
                          {formatINR(row.price)}
                        </Td>
                        <Td className={row.inStock ? "text-text" : "text-danger"}>
                          {row.delivery}
                        </Td>
                        <Td>
                          <span
                            title={`Seller trust score: ${row.trustScore}/100`}
                            className="inline-flex items-center gap-1.5 text-[13px]"
                          >
                            <Badge tone={trust.tone}>{trust.label}</Badge>
                            <span className="font-mono text-[12px] text-muted tabular-nums">
                              {row.trustScore}/100
                            </span>
                          </span>
                        </Td>
                        <Td className="text-right">
                          {row.url && (
                            <a
                              href={row.url}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-1 text-[13px] font-medium text-accent hover:underline"
                            >
                              <span>Open</span>
                              <ExternalLink strokeWidth={1.5} className="w-3.5 h-3.5" />
                            </a>
                          )}
                        </Td>
                      </tr>
                    );
                  })}

                  {streamingStores &&
                    [1, 2].map((n) => (
                      <tr key={`sk-${n}`}>
                        <Td><Skeleton className="h-4 w-28" /></Td>
                        <Td><Skeleton className="h-4 w-20" /></Td>
                        <Td><Skeleton className="h-4 w-16" /></Td>
                        <Td><Skeleton className="h-4 w-20" /></Td>
                        <Td className="text-right"><Skeleton className="h-4 w-12 ml-auto" /></Td>
                      </tr>
                    ))}
                </tbody>
              </TableWrapper>
            </div>

            {/* Mobile Stacked Rows (375px safe) */}
            <div className="md:hidden divide-y divide-border border-t border-border">
              {unifiedRows.map((row) => {
                const trust = trustTone(row.trustScore);
                return (
                  <div
                    key={row.key}
                    className={`py-3.5 space-y-2 ${row.isBest ? "bg-accent-subtle/30 -mx-5 px-5" : ""}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-[14px] font-medium text-text">{row.store}</span>
                          {row.isBest && <Badge tone="accent">Best</Badge>}
                          {row.isQuick && <Badge tone="neutral">10-min</Badge>}
                        </div>
                        <div className="text-[12px] text-muted truncate">{row.subtext}</div>
                      </div>
                      <div className="font-mono text-[15px] font-semibold tabular-nums text-text flex-shrink-0">
                        {formatINR(row.price)}
                      </div>
                    </div>

                    <div className="flex items-center justify-between gap-2 text-[12px]">
                      <div className="flex items-center gap-2 text-muted">
                        <span className={row.inStock ? "" : "text-danger"}>{row.delivery}</span>
                        <span>·</span>
                        <span>
                          Trust: {trust.label} ({row.trustScore}/100)
                        </span>
                      </div>
                      {row.url && (
                        <a
                          href={row.url}
                          target="_blank"
                          rel="noreferrer"
                          className="min-h-[44px] px-2 inline-flex items-center gap-1 font-medium text-accent"
                        >
                          <span>Open</span>
                          <ExternalLink strokeWidth={1.5} className="w-3.5 h-3.5" />
                        </a>
                      )}
                    </div>
                  </div>
                );
              })}

              {streamingStores && (
                <div className="py-3 space-y-2">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-3 w-24" />
                </div>
              )}
            </div>
          </>
        )}
      </Card>

      {/* ── d. Price History Chart ── */}
      <Card className="p-5 sm:p-6 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h2 className="text-[20px] font-semibold text-text">Price history</h2>
            <div className="flex flex-wrap items-center gap-4 text-[13px] text-muted mt-0.5">
              {minPoint && (
                <span>
                  Low:{" "}
                  <strong className="font-mono font-medium tabular-nums text-text">
                    {formatINR(minPoint.price)}
                  </strong>
                </span>
              )}
              {lastPoint && (
                <span>
                  Current:{" "}
                  <strong className="font-mono font-medium tabular-nums text-accent">
                    {formatINR(lastPoint.price)}
                  </strong>
                </span>
              )}
              {!minPoint && <span>Tracked from store checks over time</span>}
            </div>
          </div>

          <Toggle
            ariaLabel="Price history range"
            value={historyDays}
            onChange={setHistoryDays}
            options={[
              { value: 30, label: "30d" },
              { value: 90, label: "90d" },
              { value: 3650, label: "All" },
            ]}
          />
        </div>

        {chartPoints.length > 0 ? (
          <div className="h-56 w-full pt-2">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartPoints} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis
                  dataKey="time"
                  stroke="var(--muted)"
                  fontSize={12}
                  tickLine={false}
                  axisLine={{ stroke: "var(--border)" }}
                />
                <YAxis
                  stroke="var(--muted)"
                  fontSize={12}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(val) => `₹${Math.round(val / 1000)}k`}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const d = payload[0].payload;
                      return (
                        <div className="rounded-[8px] border border-border bg-surface px-3 py-2 shadow-popover text-[12px] space-y-0.5">
                          <div className="text-muted">
                            {d.time} · {d.retailer}
                          </div>
                          <div className="font-mono text-[14px] font-semibold tabular-nums text-text">
                            {formatINR(d.price)}
                          </div>
                        </div>
                      );
                    }
                    return null;
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="price"
                  stroke="var(--accent)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, fill: "var(--accent)", stroke: "var(--surface)", strokeWidth: 2 }}
                />
                {minPoint && (
                  <ReferenceDot
                    x={minPoint.time}
                    y={minPoint.price}
                    r={4}
                    fill="var(--surface)"
                    stroke="var(--accent)"
                    strokeWidth={2}
                  />
                )}
                {lastPoint && (
                  <ReferenceDot
                    x={lastPoint.time}
                    y={lastPoint.price}
                    r={4}
                    fill="var(--accent)"
                    stroke="var(--surface)"
                    strokeWidth={2}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="rounded-[8px] border border-border bg-surface-2/60 p-8 text-center">
            <p className="text-[13px] text-muted">
              Not enough price points yet. Prices are recorded each time this product is checked.
            </p>
          </div>
        )}
      </Card>

      {/* ── e. Your Best Way to Pay ── */}
      {product.cash_vs_points && (
        <CashVsPointsSection
          report={product.cash_vs_points}
          retailerLabel={retailerLabel(primaryListing.retailer)}
          refreshingTerms={refreshingTerms}
          onRefreshLiveTerms={handleRefreshLiveTerms}
          onChooseCards={openWallet}
        />
      )}

      {/* ── f. Collapsed Accordions (closed by default with 1-line summary) ── */}
      <div className="space-y-2.5">
        <Accordion title="Offers & card breakdown" summary={offersSummary}>
          {product.stacked_offers && product.stacked_offers.steps.length > 0 ? (
            <div className="space-y-4">
              <div className="divide-y divide-border text-[13px]">
                {product.stacked_offers.steps.map((st, idx) => {
                  const isFee = st.verification_status === "fee";
                  return (
                    <div key={idx} className="py-2.5 flex items-center justify-between gap-4">
                      <div>
                        <div className="font-medium text-text">{st.step_name}</div>
                        <div className="text-[12px] text-muted">{st.description}</div>
                      </div>
                      <div className="text-right font-mono tabular-nums flex-shrink-0">
                        <div className={isFee ? "text-danger" : "text-accent"}>
                          {isFee ? "+" : "-"}
                          {formatINR(st.amount_deducted)}
                        </div>
                        <div className="text-[12px] text-muted">{formatINR(st.running_price)}</div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {product.stacked_offers.alternative_paths?.length > 0 && (
                <div className="pt-2 border-t border-border space-y-2">
                  <div className="text-[12px] font-medium text-muted">Other card options</div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                    {product.stacked_offers.alternative_paths.map((ap, idx) => (
                      <div
                        key={idx}
                        className="rounded-[8px] border border-border bg-surface-2/50 p-3 text-[13px] space-y-1"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium text-text">{ap.title}</span>
                          <span className="font-mono text-accent tabular-nums">
                            Save {formatINR(ap.potential_savings)}
                          </span>
                        </div>
                        <div className="text-[12px] text-muted">{ap.eligibility}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-muted">
              No active bank or coupon stack found for this listing.
            </p>
          )}
        </Accordion>

        <Accordion title="Seller trust" summary={sellerSummary}>
          {product.seller_report && product.seller_report.sellers.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {product.seller_report.sellers.map((s, idx) => {
                const st = trustTone(s.peace_of_mind_score);
                return (
                  <div
                    key={idx}
                    className="rounded-[8px] border border-border bg-surface-2/40 p-3.5 space-y-2"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="text-[14px] font-medium text-text">{s.seller_name}</div>
                        <div className="text-[12px] text-muted">
                          {s.fulfillment_type || "Direct"} · {s.return_policy}
                        </div>
                      </div>
                      <Badge tone={st.tone}>
                        {st.label} · {s.peace_of_mind_score}/100
                      </Badge>
                    </div>
                    {s.pros.length > 0 && (
                      <ul className="text-[12px] text-muted space-y-0.5">
                        {s.pros.slice(0, 2).map((p, pIdx) => (
                          <li key={pIdx}>+ {p}</li>
                        ))}
                      </ul>
                    )}
                    {s.cons.length > 0 && (
                      <ul className="text-[12px] text-danger space-y-0.5">
                        {s.cons.slice(0, 2).map((c, cIdx) => (
                          <li key={cIdx}>- {c}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-[13px] text-muted">
              Primary seller: {primaryListing.seller_name || "Official / Direct"} (score{" "}
              <span className="font-mono tabular-nums">{sellerScore}/100</span>).
            </p>
          )}
        </Accordion>

        <Accordion
          title="Exchange value"
          summary="Compare store trade-in against external buyback quotes"
        >
          <div className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-[12px] text-muted mb-1">New device price (₹)</label>
                <input
                  type="number"
                  value={newDevicePrice}
                  onChange={(e) => setNewDevicePrice(Number(e.target.value))}
                  className="w-full min-h-[44px] px-3 rounded-[8px] border border-border bg-surface font-mono text-[14px] tabular-nums text-text focus:border-accent focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-[12px] text-muted mb-1">Store exchange quote (₹)</label>
                <input
                  type="number"
                  value={marketplaceQuote}
                  onChange={(e) => setMarketplaceQuote(Number(e.target.value))}
                  className="w-full min-h-[44px] px-3 rounded-[8px] border border-border bg-surface font-mono text-[14px] tabular-nums text-text focus:border-accent focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-[12px] text-muted mb-1">External buyback quote (₹)</label>
                <input
                  type="number"
                  value={externalQuote}
                  onChange={(e) => setExternalQuote(Number(e.target.value))}
                  className="w-full min-h-[44px] px-3 rounded-[8px] border border-border bg-surface font-mono text-[14px] tabular-nums text-text focus:border-accent focus:outline-none"
                />
              </div>
            </div>

            <Button variant="outline" onClick={handleExchangeCompare} disabled={exchangeLoading}>
              {exchangeLoading ? "Calculating..." : "Compare trade-in options"}
            </Button>

            {exchangeResult && (
              <div className="rounded-[8px] border border-border bg-surface-2/50 p-4 space-y-3 text-[13px]">
                <p className="text-text">{exchangeResult.reasoning}</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="rounded-[8px] border border-border bg-surface p-3 space-y-1">
                    <div className="font-medium text-text">Store exchange</div>
                    <div className="text-[12px] text-muted">
                      Haircut: -{exchangeResult.marketplace_option.haircut_pct}%
                    </div>
                    <div className="font-mono font-semibold tabular-nums text-text">
                      Net upgrade: {formatINR(exchangeResult.marketplace_option.effective_upgrade_cost)}
                    </div>
                  </div>
                  <div className="rounded-[8px] border border-border bg-surface p-3 space-y-1">
                    <div className="font-medium text-text">External buyback</div>
                    <div className="text-[12px] text-muted">
                      Haircut: -{exchangeResult.external_option.haircut_pct}%
                    </div>
                    <div className="font-mono font-semibold tabular-nums text-accent">
                      Net upgrade: {formatINR(exchangeResult.external_option.effective_upgrade_cost)}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </Accordion>

        <Accordion title="Warranty" summary={warrantySummary}>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-[13px]">
            <div className="space-y-1 text-muted">
              <p className="text-text font-medium">{warrantySummary}</p>
              <p>Upload your PDF tax invoice after buying to track expiry dates automatically.</p>
            </div>
            <Link href="/vault" className="flex-shrink-0">
              <Button variant="outline" size="sm">
                Open Vault
              </Button>
            </Link>
          </div>
        </Accordion>
      </div>
    </div>
  );
}
