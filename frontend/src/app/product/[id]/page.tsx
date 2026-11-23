"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ExternalLink,
  ShieldCheck,
  TrendingDown,
  AlertTriangle,
  Calendar,
  CreditCard as CardIcon,
  CheckCircle2,
  XCircle,
  HelpCircle,
  Sparkles,
  ArrowRight,
  RefreshCw,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { CardWalletBar } from "@/components/CardWalletBar";
import {
  fetchProductDetails,
  fetchProductHistory,
  recalculateEffectivePrice,
  compareExchange,
} from "@/lib/api";
import {
  AnalysisResponse,
  ExchangeComparisonResponse,
  PriceHistoryResponse,
  ProductDetails,
} from "@/lib/types";
import { formatINR, retailerLabel, trustColorClass, verdictBadge } from "@/lib/utils";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function ProductDecisionPage({ params }: PageProps) {
  const resolvedParams = use(params);
  const productId = resolvedParams.id;

  const [product, setProduct] = useState<ProductDetails | null>(null);
  const [history, setHistory] = useState<PriceHistoryResponse | null>(null);
  const [historyDays, setHistoryDays] = useState(90);
  const [loading, setLoading] = useState(true);
  const [activeCardIds, setActiveCardIds] = useState<string[]>([]);

  // Effective price state
  const [effectiveData, setEffectiveData] = useState<any>(null);
  const [recalculating, setRecalculating] = useState(false);

  // Exchange Arbitrage State
  const [newDevicePrice, setNewDevicePrice] = useState<number>(0);
  const [marketplaceQuote, setMarketplaceQuote] = useState<number>(10000);
  const [externalQuote, setExternalQuote] = useState<number>(11500);
  const [exchangeResult, setExchangeResult] = useState<ExchangeComparisonResponse | null>(null);

  useEffect(() => {
    async function loadData() {
      setLoading(true);
      const [prod, hist] = await Promise.all([
        fetchProductDetails(productId),
        fetchProductHistory(productId, historyDays),
      ]);
      setProduct(prod);
      setHistory(hist);
      setLoading(false);

      if (prod && prod.listings.length > 0) {
        const lowest = Math.min(...prod.listings.map((l) => l.current_listed_price || 999999));
        setNewDevicePrice(lowest);
      }
    }
    loadData();
  }, [productId, historyDays]);

  // Recalculate effective prices when cards or product changes
  useEffect(() => {
    if (!product || product.listings.length === 0) return;
    const cheapestListing = product.listings.reduce((prev, curr) =>
      (curr.current_listed_price || 999999) < (prev.current_listed_price || 999999) ? curr : prev
    );

    if (!cheapestListing.current_listed_price) return;

    setRecalculating(true);
    recalculateEffectivePrice(
      cheapestListing.current_listed_price,
      activeCardIds,
      cheapestListing.retailer,
      0,
      0,
      cheapestListing.bank_offers || []
    )
      .then(setEffectiveData)
      .catch(console.warn)
      .finally(() => setRecalculating(false));
  }, [product, activeCardIds]);

  // Exchange comparison computation
  const handleExchangeCompare = async () => {
    if (!newDevicePrice) return;
    try {
      const res = await compareExchange(newDevicePrice, marketplaceQuote, externalQuote);
      setExchangeResult(res);
    } catch (err) {
      console.warn(err);
    }
  };

  if (loading) {
    return (
      <div className="py-24 text-center space-y-3">
        <div className="w-8 h-8 border-2 border-brand-primary border-t-transparent rounded-full animate-spin mx-auto" />
        <p className="text-sm text-slate-400">Loading verified product intelligence...</p>
      </div>
    );
  }

  if (!product) {
    return (
      <div className="py-20 text-center space-y-4">
        <AlertTriangle className="w-12 h-12 text-amber-400 mx-auto" />
        <h2 className="text-xl font-bold text-white">Product Not Found</h2>
        <p className="text-sm text-slate-400">
          This product ID does not exist in the database.
        </p>
        <Link
          href="/"
          className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold bg-brand-primary text-white"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Analyzer</span>
        </Link>
      </div>
    );
  }

  const cheapestListing = product.listings.reduce((prev, curr) =>
    (curr.current_listed_price || 999999) < (prev.current_listed_price || 999999) ? curr : prev
  );

  const bestCard = effectiveData?.best_card;
  const missedSavings = effectiveData?.missed_savings;

  // Chart data formatting
  const chartPoints = (history?.observations || []).map((pt) => ({
    time: new Date(pt.observed_at).toLocaleDateString("en-IN", {
      month: "short",
      day: "numeric",
    }),
    price: pt.listed_price,
    retailer: retailerLabel(pt.retailer),
  }));

  return (
    <div className="space-y-8 animate-fade-in">
      {/* ── Breadcrumb / Header ── */}
      <div>
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-400 hover:text-white transition-colors mb-3"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Back to Live Analyzer</span>
        </Link>

        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs uppercase font-extrabold tracking-widest text-brand-primary">
                {product.brand}
              </span>
              <span className="text-slate-600">·</span>
              <span className="text-xs text-slate-400 capitalize">{product.category}</span>
              {product.mpn_or_ean && (
                <>
                  <span className="text-slate-600">·</span>
                  <span className="text-xs text-slate-500 font-mono">EAN: {product.mpn_or_ean}</span>
                </>
              )}
            </div>

            <h1 className="text-2xl sm:text-3xl font-black text-white mt-1">
              {product.model_name}
            </h1>

            {/* Spec pills */}
            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              {Object.entries(product.variant_specs || {}).map(([key, val]) => (
                <span
                  key={key}
                  className="text-xs px-2.5 py-0.5 rounded-md bg-surface-card border border-surface-border text-slate-300 font-medium"
                >
                  <strong className="text-slate-400 capitalize">{key.replace(/_/g, " ")}:</strong>{" "}
                  {String(val)}
                </span>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2">
            {cheapestListing.listing_url && (
              <a
                href={cheapestListing.listing_url}
                target="_blank"
                rel="noreferrer"
                className="px-4 py-2.5 rounded-xl text-xs font-bold bg-gradient-to-r from-brand-primary to-brand-secondary text-white hover:opacity-90 flex items-center gap-1.5 shadow-lg shadow-brand-primary/20"
              >
                <span>Buy on {retailerLabel(cheapestListing.retailer)}</span>
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            )}
          </div>
        </div>
      </div>

      {/* ── Executive Decision Recommendation Banner ── */}
      {product.llm_summary && (
        <div className="p-5 rounded-2xl bg-gradient-to-r from-emerald-950/40 via-surface-card to-emerald-950/20 border border-emerald-500/40 shadow-xl space-y-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-emerald-500/20 pb-3">
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-1 rounded-lg bg-emerald-500 text-slate-950 font-black text-xs uppercase tracking-wider flex items-center gap-1">
                <Sparkles className="w-3.5 h-3.5" />
                <span>AI Recommendation</span>
              </span>
              <span className="text-sm font-bold text-white">
                Pick <strong className="text-emerald-400">{product.llm_summary.best_store}</strong> · {product.llm_summary.best_seller}
              </span>
            </div>
            <div className="text-right">
              <span className="text-xs text-slate-400 block sm:inline mr-2">Optimal Net Checkout:</span>
              <span className="text-xl font-black text-emerald-400">{formatINR(product.llm_summary.final_net_price)}</span>
            </div>
          </div>

          {product.llm_summary.why_this_wins && (
            <p className="text-xs text-emerald-200/90 font-medium">
              💡 <strong>Why this wins:</strong> {product.llm_summary.why_this_wins}
            </p>
          )}

          {product.llm_summary.summary_bullets && product.llm_summary.summary_bullets.length > 0 && (
            <ul className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs text-slate-300">
              {product.llm_summary.summary_bullets.map((b, idx) => (
                <li key={idx} className="flex items-start gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0 mt-0.5" />
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          )}

          {product.llm_summary.key_caveat && (
            <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-200 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
              <span><strong>Caveat:</strong> {product.llm_summary.key_caveat}</span>
            </div>
          )}
        </div>
      )}

      {/* ── Card Wallet Bar ── */}
      <CardWalletBar onCardsChanged={(ids) => setActiveCardIds(ids)} />

      {/* ── Effective Price Hero Card ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 p-6 rounded-2xl bg-gradient-to-br from-surface-card via-surface-card to-surface-border/30 border border-surface-border shadow-xl space-y-6">
          <div className="flex items-center justify-between border-b border-surface-border pb-4">
            <div>
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
                CORE ARBITRAGE ENGINE
              </span>
              <h2 className="text-lg font-bold text-white flex items-center gap-2 mt-0.5">
                <span>Effective Checkout Price Breakdown</span>
                {recalculating && (
                  <RefreshCw className="w-3.5 h-3.5 text-brand-primary animate-spin" />
                )}
              </h2>
            </div>

            <span className="text-xs text-slate-400">
              Retailer: <strong className="text-white">{retailerLabel(cheapestListing.retailer)}</strong>
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="p-4 rounded-xl bg-surface border border-surface-border">
              <span className="text-xs text-slate-400">Listed Price</span>
              <div className="text-xl font-bold text-slate-300 mt-1">
                {formatINR(cheapestListing.current_listed_price)}
              </div>
              {cheapestListing.current_mrp && (
                <div className="text-[11px] text-slate-500 line-through">
                  MRP {formatINR(cheapestListing.current_mrp)}
                </div>
              )}
            </div>

            <div className="p-4 rounded-xl bg-surface border border-surface-border">
              <span className="text-xs text-slate-400">Bank Instant Discount</span>
              <div className="text-xl font-bold text-indigo-400 mt-1">
                -{formatINR(bestCard?.bank_discount || 0)}
              </div>
              <div className="text-[11px] text-slate-500">
                {bestCard?.offer_summary || "No active bank offer"}
              </div>
            </div>

            <div className="p-4 rounded-xl bg-surface border border-brand-primary/40 bg-brand-primary/5">
              <span className="text-xs font-semibold text-emerald-400">
                Net Price with Your Card
              </span>
              <div className="text-2xl font-black text-emerald-400 mt-1">
                {formatINR(bestCard?.effective_price || cheapestListing.current_listed_price)}
              </div>
              <div className="text-[11px] text-slate-300 truncate">
                {bestCard ? `🏆 ${bestCard.card_name} (${bestCard.reward_pct}%)` : "No card selected"}
              </div>
            </div>
          </div>

          {/* Missed Savings Callout */}
          {missedSavings && (
            <div className="p-4 rounded-xl bg-gradient-to-r from-amber-500/15 to-orange-500/15 border border-amber-500/40 text-amber-200 text-xs flex items-start gap-3">
              <Sparkles className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
              <div>
                <strong className="text-amber-300 font-bold text-sm block">
                  Missed Savings Opportunity!
                </strong>
                <p className="mt-0.5 text-slate-300">
                  You could save an additional{" "}
                  <strong className="text-white font-bold">
                    {formatINR(missedSavings.savings_vs_best_owned)}
                  </strong>{" "}
                  if you held the{" "}
                  <strong className="text-amber-300">{missedSavings.card_name}</strong> (Effective Net Price:{" "}
                  {formatINR(missedSavings.effective_price)}).
                </p>
              </div>
            </div>
          )}
        </div>

        {/* ── Buy vs Wait Verdict Card ── */}
        <div className="p-6 rounded-2xl bg-surface-card border border-surface-border shadow-xl flex flex-col justify-between space-y-4">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              INDIAN SALE CALENDAR SIGNAL
            </span>
            <h2 className="text-lg font-bold text-white mt-0.5">Buy or Wait?</h2>

            {product.buy_wait ? (
              <div className="mt-4 space-y-3">
                <div
                  className={`inline-flex items-center gap-2 px-3 py-1 rounded-xl text-xs font-black border ${
                    verdictBadge(product.buy_wait.verdict).border
                  } ${verdictBadge(product.buy_wait.verdict).text} ${
                    verdictBadge(product.buy_wait.verdict).bg
                  }`}
                >
                  <span>{verdictBadge(product.buy_wait.verdict).emoji}</span>
                  <span>{product.buy_wait.verdict.replace(/_/g, " ")}</span>
                </div>

                <p className="text-xs text-slate-300 leading-relaxed">
                  {product.buy_wait.reasoning}
                </p>

                {product.buy_wait.next_sale_name && (
                  <div className="p-3 rounded-xl bg-surface border border-surface-border text-xs text-indigo-300 font-medium flex items-center gap-2">
                    <Calendar className="w-4 h-4 text-indigo-400 flex-shrink-0" />
                    <span>
                      <strong>{product.buy_wait.next_sale_name}</strong> begins in{" "}
                      <strong>{product.buy_wait.days_to_next_sale} days</strong>.
                    </span>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-xs text-slate-400 mt-3">
                Price tracking started. Minimum 3 observations needed for full historical percentiles.
              </p>
            )}
          </div>

          <div className="pt-3 border-t border-surface-border/60 text-[11px] text-slate-500">
            Observations logged: {history?.observations.length || 0}
          </div>
        </div>
      </div>

      {/* ── Dynamic Offer Stacking & Optimization Card ── */}
      {product.stacked_offers && product.stacked_offers.steps && product.stacked_offers.steps.length > 0 && (
        <div className="p-6 rounded-2xl bg-surface-card border border-surface-border shadow-xl space-y-4">
          <div className="flex items-center justify-between border-b border-surface-border pb-3">
            <div>
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
                TRANSPARENT CHECKOUT MATH
              </span>
              <h2 className="text-lg font-bold text-white flex items-center gap-2 mt-0.5">
                <Sparkles className="w-4 h-4 text-brand-primary" />
                <span>Dynamic Live Offer Stacking Engine</span>
              </h2>
            </div>
            <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-bold">
              Total Saved: {formatINR(product.stacked_offers.total_savings)}
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-surface-border text-slate-400 uppercase text-[10px] tracking-wider">
                  <th className="py-2.5 px-3 font-bold">Step</th>
                  <th className="py-2.5 px-3 font-bold">Description</th>
                  <th className="py-2.5 px-3 font-bold">Type</th>
                  <th className="py-2.5 px-3 font-bold text-right">Adjustment</th>
                  <th className="py-2.5 px-3 font-bold text-right">Running Total</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-border/40">
                <tr className="text-slate-300">
                  <td className="py-2.5 px-3 font-bold text-white">0. Base Price</td>
                  <td className="py-2.5 px-3">Original Store Listed Price</td>
                  <td className="py-2.5 px-3">
                    <span className="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                      Catalog Price
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono">—</td>
                  <td className="py-2.5 px-3 text-right font-black text-white font-mono">
                    {formatINR(product.stacked_offers.original_listed_price)}
                  </td>
                </tr>
                {product.stacked_offers.steps.map((st, idx) => {
                  const isFee = st.verification_status === "fee";
                  return (
                    <tr key={idx} className="hover:bg-surface/40 transition-colors">
                      <td className="py-2.5 px-3 font-bold text-slate-200">{st.step_name}</td>
                      <td className="py-2.5 px-3 text-slate-400">{st.description}</td>
                      <td className="py-2.5 px-3">
                        <span className={`text-[10px] px-2 py-0.5 rounded border font-semibold ${
                          isFee
                            ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
                            : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                        }`}>
                          {st.verification_status.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className={`py-2.5 px-3 text-right font-black font-mono ${isFee ? "text-rose-400" : "text-emerald-400"}`}>
                        {isFee ? "+" : "-"}{formatINR(st.amount_deducted)}
                      </td>
                      <td className="py-2.5 px-3 text-right font-black text-white font-mono">
                        {formatINR(st.running_price)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Alternative Optimization Paths */}
          {product.stacked_offers.alternative_paths && product.stacked_offers.alternative_paths.length > 0 && (
            <div className="pt-3 border-t border-surface-border space-y-2">
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider block">
                Alternative Discount & Payment Optimization Paths
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {product.stacked_offers.alternative_paths.map((ap, idx) => (
                  <div key={idx} className="p-3 rounded-xl bg-surface border border-brand-primary/20 space-y-1 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-brand-primary">{ap.title}</span>
                      <span className="font-black text-emerald-400">Save ~{formatINR(ap.potential_savings)}</span>
                    </div>
                    <p className="text-slate-300">{ap.method} (Net: <strong>{formatINR(ap.net_price)}</strong>)</p>
                    <p className="text-[11px] text-slate-500">Eligibility: {ap.eligibility}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Fair Multi-Store Ranking (Unbiased Ranking Across All Sites) ── */}
      {product.ranked_store_choices && product.ranked_store_choices.length > 0 && (
        <div className="p-6 rounded-2xl bg-surface-card border border-surface-border shadow-xl space-y-4">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              UNBIASED CROSS-PLATFORM ARBITRAGE
            </span>
            <h2 className="text-lg font-bold text-white mt-0.5">Fair Multi-Store Ranking</h2>
            <p className="text-xs text-slate-400">
              Ranked dynamically by Effective Price (55%), Peace of Mind (30%), and Return/Warranty Terms (15%)
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-surface-border text-slate-400 uppercase text-[10px] tracking-wider">
                  <th className="py-3 px-4 font-bold">Store</th>
                  <th className="py-3 px-4 font-bold">Rank Badge</th>
                  <th className="py-3 px-4 font-bold">Seller & Terms</th>
                  <th className="py-3 px-4 font-bold">Peace of Mind</th>
                  <th className="py-3 px-4 font-bold">Effective Price</th>
                  <th className="py-3 px-4 font-bold text-right">Store Link</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-border/50">
                {product.ranked_store_choices.map((r, idx) => {
                  const badgeColor = r.is_overall_winner
                    ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                    : r.rank_badge === "Lowest Net Price"
                    ? "bg-indigo-500/20 text-indigo-400 border-indigo-500/30"
                    : "bg-amber-500/20 text-amber-400 border-amber-500/30";
                  return (
                    <tr
                      key={idx}
                      className={`hover:bg-surface/50 transition-colors ${
                        r.is_overall_winner ? "bg-emerald-500/5 font-semibold" : ""
                      }`}
                    >
                      <td className="py-3.5 px-4 font-bold text-white flex items-center gap-2">
                        <span>{r.store_name}</span>
                        {r.is_overall_winner && (
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-400 font-extrabold border border-emerald-500/30">
                            WINNER
                          </span>
                        )}
                      </td>
                      <td className="py-3.5 px-4">
                        {r.rank_badge && (
                          <span className={`text-[10px] px-2 py-0.5 rounded font-extrabold border ${badgeColor}`}>
                            {r.rank_badge}
                          </span>
                        )}
                      </td>
                      <td className="py-3.5 px-4 text-slate-300">
                        <div>{r.seller_name || "Official / Direct"}</div>
                        <div className="text-[10px] text-slate-500">{r.return_terms} · {r.warranty_terms}</div>
                      </td>
                      <td className="py-3.5 px-4">
                        <span className="font-bold text-slate-200">{r.peace_of_mind_score}/100</span>
                      </td>
                      <td className="py-3.5 px-4 font-black text-white text-sm">
                        {formatINR(r.effective_price)}
                      </td>
                      <td className="py-3.5 px-4 text-right">
                        {r.url && (
                          <a
                            href={r.url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-surface border border-surface-border text-slate-200 hover:text-white hover:border-slate-500 transition-colors"
                          >
                            <span>Open</span>
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Dynamic Multi-Seller Fact-Check Card ── */}
      {product.seller_report && product.seller_report.sellers && product.seller_report.sellers.length > 0 && (
        <div className="p-6 rounded-2xl bg-surface-card border border-surface-border shadow-xl space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-surface-border pb-3">
            <div>
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
                SELLER FORENSICS & REPUTATION ENGINE
              </span>
              <h2 className="text-lg font-bold text-white flex items-center gap-2 mt-0.5">
                <ShieldCheck className="w-5 h-5 text-brand-primary" />
                <span>Dynamic Multi-Seller Fact-Check</span>
              </h2>
            </div>
            {product.seller_report.recommended_seller && (
              <div className="text-xs px-3 py-1 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 font-semibold">
                ⭐ Recommended: <strong>{product.seller_report.recommended_seller}</strong>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {product.seller_report.sellers.map((s, idx) => {
              const scoreColor = s.peace_of_mind_score >= 70
                ? "text-emerald-400 border-emerald-500/30 bg-emerald-500/10"
                : s.peace_of_mind_score >= 40
                ? "text-amber-400 border-amber-500/30 bg-amber-500/10"
                : "text-rose-400 border-rose-500/30 bg-rose-500/10";
              const verdictColor = s.verdict === "Recommended"
                ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/30"
                : s.verdict === "Proceed with Caution"
                ? "text-amber-400 bg-amber-500/10 border-amber-500/30"
                : "text-rose-400 bg-rose-500/10 border-rose-500/30";
              return (
                <div key={idx} className="p-4 rounded-xl bg-surface border border-surface-border space-y-3 flex flex-col justify-between">
                  <div>
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <h3 className="font-bold text-white text-sm flex items-center gap-1.5">
                          <span>{s.seller_name}</span>
                          {s.is_buy_box_winner && (
                            <span className="text-[10px] px-1.5 py-0.2 rounded bg-brand-primary/20 text-brand-primary border border-brand-primary/30">
                              Buy Box
                            </span>
                          )}
                        </h3>
                        <p className="text-[11px] text-slate-400 mt-0.5">
                          {s.fulfillment_type || "Direct Merchant"} · {s.return_policy}
                        </p>
                      </div>
                      <span className={`text-xs px-2 py-0.5 rounded-lg border font-black ${scoreColor}`}>
                        {s.peace_of_mind_score}/100
                      </span>
                    </div>

                    <div className="mt-3 space-y-1 text-xs">
                      {s.pros.map((p, pIdx) => (
                        <div key={pIdx} className="text-emerald-400 flex items-center gap-1 text-[11px]">
                          <CheckCircle2 className="w-3 h-3 flex-shrink-0" />
                          <span>{p}</span>
                        </div>
                      ))}
                      {s.cons.map((c, cIdx) => (
                        <div key={cIdx} className="text-rose-400 flex items-center gap-1 text-[11px]">
                          <XCircle className="w-3 h-3 flex-shrink-0" />
                          <span>{c}</span>
                        </div>
                      ))}
                    </div>

                    {s.risk_assessment && (
                      <div className="mt-2 text-[11px] text-amber-300/90 bg-amber-500/10 p-2 rounded-lg border border-amber-500/20">
                        ⚠️ {s.risk_assessment}
                      </div>
                    )}
                  </div>

                  <div className="pt-2 border-t border-surface-border/60 flex items-center justify-between">
                    <span className={`text-[10px] px-2 py-0.5 rounded font-extrabold border ${verdictColor}`}>
                      {s.verdict}
                    </span>
                    {s.price && (
                      <span className="font-black text-white text-xs">
                        {formatINR(s.price)}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Cross-Store Comparison Table ── */}
      <div className="p-6 rounded-2xl bg-surface-card border border-surface-border shadow-xl space-y-4">
        <div>
          <h2 className="text-lg font-bold text-white">Cross-Store Verified Listings</h2>
          <p className="text-xs text-slate-400">
            Strict variant matching guarantees you never compare mismatched storage or RAM
          </p>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-surface-border text-slate-400 uppercase text-[10px] tracking-wider">
                <th className="py-3 px-4 font-bold">Store</th>
                <th className="py-3 px-4 font-bold">Seller</th>
                <th className="py-3 px-4 font-bold">Match</th>
                <th className="py-3 px-4 font-bold">Listed Price</th>
                <th className="py-3 px-4 font-bold">Stock</th>
                <th className="py-3 px-4 font-bold text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-border/50">
              {product.listings.map((l, idx) => {
                const isCheapest = idx === 0;
                return (
                  <tr
                    key={l.id || idx}
                    className={`hover:bg-surface/50 transition-colors ${
                      isCheapest ? "bg-emerald-500/5" : ""
                    }`}
                  >
                    <td className="py-3.5 px-4 font-bold text-white flex items-center gap-2">
                      <span>{retailerLabel(l.retailer)}</span>
                      {isCheapest && (
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-400 font-extrabold border border-emerald-500/30">
                          CHEAPEST
                        </span>
                      )}
                    </td>

                    <td className="py-3.5 px-4 text-slate-300">
                      <div>{l.seller_name || "Official / Direct"}</div>
                      {l.trust_score != null && (
                        <span
                          className={`text-[9px] px-1 rounded font-semibold border ${
                            trustColorClass(l.trust_score).border
                          } ${trustColorClass(l.trust_score).text} ${
                            trustColorClass(l.trust_score).bg
                          }`}
                        >
                          Trust {l.trust_score}/100
                        </span>
                      )}
                    </td>

                    <td className="py-3.5 px-4 text-slate-400">
                      {Math.round((l.match_confidence || 1.0) * 100)}% verified
                    </td>

                    <td className="py-3.5 px-4 font-black text-white">
                      {formatINR(l.current_listed_price)}
                    </td>

                    <td className="py-3.5 px-4">
                      {l.in_stock ? (
                        <span className="text-emerald-400 font-semibold flex items-center gap-1">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          <span>In Stock</span>
                        </span>
                      ) : (
                        <span className="text-rose-400 font-semibold flex items-center gap-1">
                          <XCircle className="w-3.5 h-3.5" />
                          <span>Out of Stock</span>
                        </span>
                      )}
                    </td>

                    <td className="py-3.5 px-4 text-right">
                      {l.listing_url && (
                        <a
                          href={l.listing_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-surface border border-surface-border text-slate-200 hover:text-white hover:border-slate-500 transition-colors"
                        >
                          <span>Store</span>
                          <ExternalLink className="w-3 h-3" />
                        </a>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Price History Chart (Recharts) ── */}
      <div className="p-6 rounded-2xl bg-surface-card border border-surface-border shadow-xl space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <TrendingDown className="w-5 h-5 text-indigo-400" />
              <span>TimescaleDB Price History</span>
            </h2>
            <p className="text-xs text-slate-400">
              Real price observations recorded by OneStop crawlers & extension users
            </p>
          </div>

          <div className="flex items-center gap-1 bg-surface p-1 rounded-xl border border-surface-border self-start sm:self-auto">
            {[30, 90, 180].map((d) => (
              <button
                key={d}
                onClick={() => setHistoryDays(d)}
                className={`px-3 py-1 rounded-lg text-xs font-bold transition-colors ${
                  historyDays === d
                    ? "bg-brand-primary text-white"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                {d}D
              </button>
            ))}
          </div>
        </div>

        {chartPoints.length > 0 ? (
          <div className="h-64 w-full pt-4">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartPoints} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#272738" vertical={false} />
                <XAxis dataKey="time" stroke="#64748b" fontSize={11} tickLine={false} />
                <YAxis
                  stroke="#64748b"
                  fontSize={11}
                  tickLine={false}
                  tickFormatter={(val) => `₹${(val / 1000).toFixed(0)}k`}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const data = payload[0].payload;
                      return (
                        <div className="p-3 rounded-xl bg-surface-card border border-surface-border shadow-xl text-xs space-y-1">
                          <p className="text-slate-400">{data.time}</p>
                          <p className="text-base font-black text-white">{formatINR(data.price)}</p>
                          <p className="text-[10px] text-indigo-400 font-semibold">{data.retailer}</p>
                        </div>
                      );
                    }
                    return null;
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="price"
                  stroke="#6366f1"
                  strokeWidth={2.5}
                  fillOpacity={1}
                  fill="url(#priceGradient)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="p-12 text-center text-xs text-slate-500 rounded-xl bg-surface">
            Not enough price points yet. Observations are recorded each time users view this product.
          </div>
        )}
      </div>

      {/* ── Exchange Arbitrage Calculator ── */}
      <div className="p-6 rounded-2xl bg-surface-card border border-surface-border shadow-xl space-y-6">
        <div>
          <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
            CONSUMER PROTECTION & ARBITRAGE
          </span>
          <h2 className="text-lg font-bold text-white mt-0.5">
            Trade-in Arbitrage: Marketplace vs External Buyback
          </h2>
          <p className="text-xs text-slate-400">
            Marketplace exchange quotes (Amazon/Flipkart) face a 15%–25% doorstep inspection haircut.
            See whether selling on Cashify/InstaCash saves you more.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="text-xs font-semibold text-slate-300 block mb-1.5">
              New Device Price (₹)
            </label>
            <input
              type="number"
              value={newDevicePrice}
              onChange={(e) => setNewDevicePrice(Number(e.target.value))}
              className="w-full p-2.5 rounded-xl bg-surface border border-surface-border text-sm text-white focus:outline-none focus:border-brand-primary"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-300 block mb-1.5">
              Marketplace Exchange Quote (₹)
            </label>
            <input
              type="number"
              value={marketplaceQuote}
              onChange={(e) => setMarketplaceQuote(Number(e.target.value))}
              className="w-full p-2.5 rounded-xl bg-surface border border-surface-border text-sm text-white focus:outline-none focus:border-brand-primary"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-300 block mb-1.5">
              External Buyback Quote (Cashify/OLX) (₹)
            </label>
            <input
              type="number"
              value={externalQuote}
              onChange={(e) => setExternalQuote(Number(e.target.value))}
              className="w-full p-2.5 rounded-xl bg-surface border border-surface-border text-sm text-white focus:outline-none focus:border-brand-primary"
            />
          </div>
        </div>

        <button
          onClick={handleExchangeCompare}
          className="px-5 py-2.5 rounded-xl text-xs font-bold bg-brand-primary text-white hover:bg-brand-primary/90 transition-colors flex items-center gap-1.5"
        >
          <span>Calculate True Upgrade Cost</span>
          <ArrowRight className="w-3.5 h-3.5" />
        </button>

        {exchangeResult && (
          <div className="p-4 rounded-xl bg-surface border border-surface-border space-y-4">
            <div className="p-3 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-xs text-indigo-300">
              💡 <strong>Recommendation:</strong> {exchangeResult.reasoning}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="p-3 rounded-lg bg-surface-card border border-surface-border space-y-1 text-xs">
                <span className="font-bold text-slate-300">Marketplace Exchange Option</span>
                <p className="text-slate-400">Gross quote: {formatINR(exchangeResult.marketplace_option.gross_value)}</p>
                <p className="text-amber-400">Doorstep haircut: -{exchangeResult.marketplace_option.haircut_pct}%</p>
                <p className="text-white font-bold">
                  Net upgrade cost: {formatINR(exchangeResult.marketplace_option.effective_upgrade_cost)}
                </p>
              </div>

              <div className="p-3 rounded-lg bg-surface-card border border-surface-border space-y-1 text-xs">
                <span className="font-bold text-slate-300">External Buyback Option</span>
                <p className="text-slate-400">Gross quote: {formatINR(exchangeResult.external_option.gross_value)}</p>
                <p className="text-emerald-400">Standard haircut: -{exchangeResult.external_option.haircut_pct}%</p>
                <p className="text-white font-bold">
                  Net upgrade cost: {formatINR(exchangeResult.external_option.effective_upgrade_cost)}
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
