"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Search,
  ExternalLink,
  ShieldCheck,
  TrendingDown,
  Sparkles,
  AlertTriangle,
  ArrowRight,
  Clock,
  Store,
  Layers,
  Chrome,
} from "lucide-react";
import { CardWalletBar } from "@/components/CardWalletBar";
import { analyzeUrl, fetchRecentProducts } from "@/lib/api";
import { AnalysisResponse, RecentProduct } from "@/lib/types";
import { formatINR, retailerLabel, trustColorClass, verdictBadge } from "@/lib/utils";

export default function HomePage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recentProducts, setRecentProducts] = useState<RecentProduct[]>([]);
  const [loadingRecent, setLoadingRecent] = useState(true);
  const [activeCardIds, setActiveCardIds] = useState<string[]>([]);
  const [analysisResult, setAnalysisResult] = useState<AnalysisResponse | null>(null);

  useEffect(() => {
    async function loadRecent() {
      try {
        const prods = await fetchRecentProducts(12);
        setRecentProducts(prods);
      } catch (err) {
        console.warn(err);
      } finally {
        setLoadingRecent(false);
      }
    }
    loadRecent();
  }, []);

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;

    setLoading(true);
    setError(null);
    setAnalysisResult(null);

    try {
      const result = await analyzeUrl(url.trim(), activeCardIds);
      setAnalysisResult(result);
      // Also refresh recent list
      fetchRecentProducts(12).then(setRecentProducts);
    } catch (err: any) {
      setError(
        err.message ||
          "Direct server scrape blocked or failed. Please open the link in Chrome and click the OneStop extension for ₹0 anti-ban extraction."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-10">
      {/* ── Hero Section ── */}
      <section className="text-center max-w-3xl mx-auto space-y-4 pt-4 sm:pt-8">
        <div className="inline-flex items-center gap-2 px-3.5 py-1 rounded-full text-xs font-semibold bg-brand-primary/10 text-brand-primary border border-brand-primary/20">
          <Sparkles className="w-3.5 h-3.5" />
          <span>India E-Commerce Decision & Arbitrage Engine</span>
        </div>

        <h1 className="text-3xl sm:text-5xl font-black text-white tracking-tight leading-tight">
          Never Overpay on <br className="hidden sm:inline" />
          <span className="bg-gradient-to-r from-indigo-400 via-purple-300 to-emerald-400 bg-clip-text text-transparent">
            Indian Electronics & Gadgets
          </span>
        </h1>

        <p className="text-sm sm:text-base text-slate-400 max-w-2xl mx-auto leading-relaxed">
          Paste any Amazon.in, Flipkart, Croma, Myntra, or D2C store URL. Strict{" "}
          <strong className="text-slate-200">Payable Today vs. Post-Purchase Rewards</strong>{" "}
          transparency — separating guaranteed checkout cash from card points, GyFTR vouchers,
          UPI offers, and verified cross-store prices.
        </p>

        {/* ── Paste-URL Bar ── */}
        <form onSubmit={handleAnalyze} className="pt-2">
          <div className="relative flex items-center shadow-2xl shadow-brand-primary/10 rounded-2xl overflow-hidden border border-surface-border bg-surface-card focus-within:border-brand-primary/60 transition-all">
            <Search className="w-5 h-5 text-slate-400 absolute left-4" />
            <input
              type="url"
              required
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="Paste Amazon.in, Flipkart, or Croma product URL..."
              className="w-full py-4 pl-12 pr-36 bg-transparent text-sm text-white placeholder-slate-500 focus:outline-none"
            />
            <button
              type="submit"
              disabled={loading}
              className="absolute right-2 px-5 py-2.5 rounded-xl font-bold text-xs bg-gradient-to-r from-brand-primary to-brand-secondary text-white hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-1.5"
            >
              {loading ? (
                <>
                  <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  <span>Analyzing...</span>
                </>
              ) : (
                <>
                  <span>Analyze Live</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </>
              )}
            </button>
          </div>
        </form>

        {/* Error / Fallback Guidance */}
        {error && (
          <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs text-left flex items-start gap-3">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5 text-amber-400" />
            <div>
              <p className="font-bold">Live Fetch Notice</p>
              <p className="mt-1 opacity-90">{error}</p>
              <div className="mt-2 text-[11px] font-semibold text-amber-200">
                💡 Tip: Use our unpacked Chrome Extension (`/extension`) to extract this product on your residential IP without any blocking!
              </div>
            </div>
          </div>
        )}
      </section>

      {/* ── Instant Analysis Result Card (if analyzed) ── */}
      {analysisResult && (
        <section className="p-6 rounded-2xl bg-surface-card border border-brand-primary/40 shadow-xl space-y-6 animate-fade-in">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-surface-border pb-4">
            <div>
              <span className="text-xs uppercase tracking-wider font-bold text-brand-primary">
                Live Extraction Complete
              </span>
              <h2 className="text-xl font-black text-white mt-1">
                {analysisResult.brand} {analysisResult.model_name}
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Category: {analysisResult.category} · Verified ID: {analysisResult.canonical_product_id.slice(0, 8)}...
              </p>
            </div>

            <Link
              href={`/product/${analysisResult.canonical_product_id}`}
              className="px-4 py-2 rounded-xl text-xs font-bold bg-brand-primary text-white hover:bg-brand-primary/90 flex items-center justify-center gap-1.5 self-start sm:self-center"
            >
              <span>View Full Decision Page</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </Link>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Effective Price Highlight */}
            <div className="p-4 rounded-xl bg-surface border border-surface-border space-y-2">
              <span className="text-xs font-bold text-slate-400 flex items-center gap-1.5">
                💳 Your Best Card Net Price
              </span>
              {analysisResult.best_card ? (
                <>
                  <div className="text-2xl font-black text-emerald-400">
                    {formatINR(analysisResult.best_card.effective_price)}
                  </div>
                  <div className="text-xs text-slate-300">
                    With <strong className="text-white">{analysisResult.best_card.card_name}</strong>
                  </div>
                  <div className="text-[11px] text-slate-400">
                    Listed {formatINR(analysisResult.listed_price)} · Bank disc -{formatINR(analysisResult.best_card.bank_discount)} · Reward -{formatINR(analysisResult.best_card.reward_value)}
                  </div>
                </>
              ) : (
                <div className="text-xl font-bold text-white">
                  {formatINR(analysisResult.listed_price)}
                  <p className="text-xs text-slate-400 font-normal mt-1">Select cards in your wallet to compute savings</p>
                </div>
              )}
            </div>

            {/* Seller Forensics */}
            <div className="p-4 rounded-xl bg-surface border border-surface-border space-y-2">
              <span className="text-xs font-bold text-slate-400 flex items-center gap-1.5">
                🛡️ Seller Forensics Score
              </span>
              {analysisResult.trust_report ? (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-2xl font-black text-white">
                      {analysisResult.trust_report.trust_score}/100
                    </span>
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase border ${
                        trustColorClass(analysisResult.trust_report.trust_score).border
                      } ${trustColorClass(analysisResult.trust_report.trust_score).text} ${
                        trustColorClass(analysisResult.trust_report.trust_score).bg
                      }`}
                    >
                      {analysisResult.trust_report.trust_tier}
                    </span>
                  </div>
                  <div className="text-xs text-slate-300">
                    Seller: <strong className="text-white">{analysisResult.trust_report.seller_name}</strong>
                  </div>
                  <div className="text-[11px] text-slate-400">
                    {analysisResult.trust_report.is_authorized
                      ? "✅ Official authorized distributor"
                      : "⚠️ Unknown third-party seller"}
                  </div>
                </>
              ) : (
                <div className="text-xs text-slate-400">No seller information available</div>
              )}
            </div>

            {/* Buy vs Wait */}
            <div className="p-4 rounded-xl bg-surface border border-surface-border space-y-2">
              <span className="text-xs font-bold text-slate-400 flex items-center gap-1.5">
                📊 Buy vs Wait Verdict
              </span>
              {analysisResult.buy_wait ? (
                <>
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs font-black px-2.5 py-1 rounded-lg border ${
                        verdictBadge(analysisResult.buy_wait.verdict).border
                      } ${verdictBadge(analysisResult.buy_wait.verdict).text} ${
                        verdictBadge(analysisResult.buy_wait.verdict).bg
                      }`}
                    >
                      {verdictBadge(analysisResult.buy_wait.verdict).emoji} {analysisResult.buy_wait.verdict.replace(/_/g, " ")}
                    </span>
                  </div>
                  <p className="text-xs text-slate-300 leading-snug">
                    {analysisResult.buy_wait.reasoning}
                  </p>
                  {analysisResult.buy_wait.next_sale_name && (
                    <div className="text-[11px] text-indigo-400 font-semibold">
                      📅 {analysisResult.buy_wait.next_sale_name} in {analysisResult.buy_wait.days_to_next_sale} days
                    </div>
                  )}
                </>
              ) : (
                <div className="text-xs text-slate-400">Price tracking just initialized</div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── Persistent Card Wallet Bar ── */}
      <section>
        <CardWalletBar onCardsChanged={(ids) => setActiveCardIds(ids)} />
      </section>

      {/* ── Recently Tracked Products Grid ── */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Layers className="w-5 h-5 text-brand-primary" />
              <span>Real Live Tracked Products</span>
            </h2>
            <p className="text-xs text-slate-400">
              Only authentic electronics stored in database via real-time extraction (zero mock data)
            </p>
          </div>
          <span className="text-xs text-slate-500 font-medium">
            {recentProducts.length} tracked
          </span>
        </div>

        {loadingRecent ? (
          <div className="p-12 text-center text-xs text-slate-500">
            Loading tracked products from database...
          </div>
        ) : recentProducts.filter((p) => p.lowest_price != null && p.lowest_price > 0).length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {recentProducts
              .filter((p) => p.lowest_price != null && p.lowest_price > 0)
              .map((p) => {
              const discountPct =
                p.lowest_price && p.highest_mrp && p.highest_mrp > p.lowest_price
                  ? Math.round(((p.highest_mrp - p.lowest_price) / p.highest_mrp) * 100)
                  : null;

              return (
                <Link
                  key={p.id}
                  href={`/product/${p.id}`}
                  className="group p-4 rounded-xl bg-surface-card border border-surface-border hover:border-brand-primary/50 transition-all flex flex-col justify-between"
                >
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-[10px] text-slate-400">
                      <span className="uppercase font-bold tracking-wider text-brand-primary">
                        {p.brand}
                      </span>
                      {p.trust_score != null && (
                        <span
                          className={`px-1.5 py-0.5 rounded font-bold border ${
                            trustColorClass(p.trust_score).border
                          } ${trustColorClass(p.trust_score).text} ${
                            trustColorClass(p.trust_score).bg
                          }`}
                        >
                          🛡️ {p.trust_score}
                        </span>
                      )}
                    </div>

                    <h3 className="text-sm font-bold text-white group-hover:text-brand-primary transition-colors line-clamp-2">
                      {p.model_name}
                    </h3>

                    {/* Specs badges */}
                    <div className="flex flex-wrap gap-1">
                      {p.variant_specs?.storage_gb && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface border border-surface-border text-slate-300">
                          {p.variant_specs.storage_gb}GB
                        </span>
                      )}
                      {p.variant_specs?.ram_gb && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface border border-surface-border text-slate-300">
                          {p.variant_specs.ram_gb}GB RAM
                        </span>
                      )}
                      {p.variant_specs?.color && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface border border-surface-border text-slate-300 capitalize">
                          {p.variant_specs.color}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="pt-4 mt-3 border-t border-surface-border/60 flex items-end justify-between">
                    <div>
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-base font-black text-white">
                          {formatINR(p.lowest_price)}
                        </span>
                        {p.highest_mrp && p.highest_mrp > (p.lowest_price || 0) && (
                          <span className="text-xs text-slate-500 line-through">
                            {formatINR(p.highest_mrp)}
                          </span>
                        )}
                      </div>
                      {discountPct && (
                        <span className="text-[10px] font-bold text-emerald-400">
                          {discountPct}% off MRP
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-1">
                      {p.retailers.map((r) => (
                        <span
                          key={r}
                          className="text-[9px] px-1.5 py-0.5 rounded bg-surface border border-surface-border text-slate-400"
                        >
                          {retailerLabel(r).split(".")[0]}
                        </span>
                      ))}
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        ) : (
          <div className="p-12 text-center rounded-2xl bg-surface-card border border-surface-border space-y-3">
            <Store className="w-10 h-10 text-slate-600 mx-auto" />
            <h3 className="text-sm font-bold text-slate-300">No Products Tracked Yet</h3>
            <p className="text-xs text-slate-500 max-w-md mx-auto">
              Our system enforces strict zero-fake-data rules. Paste any Amazon.in, Flipkart,
              or Croma URL above or use the OneStop Chrome Extension to extract and seed your first product!
            </p>
          </div>
        )}
      </section>

      {/* ── Extension Info Banner (#extension-info) ── */}
      <section
        id="extension-info"
        className="p-6 rounded-2xl bg-gradient-to-r from-brand-primary/10 via-brand-secondary/10 to-emerald-500/10 border border-brand-primary/20 space-y-4"
      >
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Chrome className="w-5 h-5 text-emerald-400" />
              <h3 className="text-base font-bold text-white">
                ₹0 Proxy Architecture via Chrome Extension (MV3)
              </h3>
            </div>
            <p className="text-xs text-slate-300 max-w-2xl">
              Indian e-commerce sites heavily rate-limit cloud datacenters. Our Chrome Extension runs
              <code className="text-indigo-300 mx-1">dom_extractor.js</code> on your residential IP,
              bypasses anti-bot walls safely with offscreen HTML parsing, and feeds real-time prices to
              this dashboard.
            </p>
          </div>

          <div className="text-xs font-semibold text-slate-300 bg-surface/80 px-4 py-2.5 rounded-xl border border-surface-border whitespace-nowrap self-start md:self-center">
            Load folder: <code className="text-brand-primary">/extension</code> in <code className="text-white">chrome://extensions</code>
          </div>
        </div>
      </section>
    </div>
  );
}
