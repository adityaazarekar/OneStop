"use client";

import { useState } from "react";
import {
  ChevronDown,
  ExternalLink,
  Gift,
  RefreshCw,
  Sparkles,
  Tag,
  Wallet,
} from "lucide-react";
import { CashVsPointsReport, PaymentRouteAnalysis, VerificationStatus } from "@/lib/types";
import { formatINR } from "@/lib/utils";

interface Props {
  report: CashVsPointsReport;
  retailerLabel: string;
  refreshingTerms?: boolean;
  onRefreshLiveTerms?: () => void;
}

type RedemptionMode = "cash" | "travel";

function safeUrl(u: string): string | null {
  try {
    const parsed = new URL(u);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.toString();
    return null;
  } catch {
    return null;
  }
}

function verificationBadge(status: VerificationStatus) {
  if (status === "page_verified") {
    return {
      label: "Page Verified",
      cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/35",
    };
  }
  if (status === "live_tc_verified") {
    return {
      label: "Live T&C Verified",
      cls: "bg-sky-500/15 text-sky-300 border-sky-500/35",
    };
  }
  return {
    label: "No Active Verified Offer",
    cls: "bg-slate-800 text-slate-400 border-slate-700",
  };
}

function routeEffectiveCost(route: PaymentRouteAnalysis, mode: RedemptionMode): number {
  if (mode === "cash") {
    return route.effective_cost_if_cash_inr;
  }
  const travelNet =
    route.checkout_payable_inr +
    (route.rewards.processing_fee_inr || 0) -
    Math.max(route.rewards.travel_value_inr || 0, route.rewards.cash_value_inr || 0);
  return Math.max(0, Math.round(travelNet * 100) / 100);
}

export function CashVsPointsSection({
  report,
  retailerLabel,
  refreshingTerms = false,
  onRefreshLiveTerms,
}: Props) {
  const [mode, setMode] = useState<RedemptionMode>("cash");

  const availableRoutes = (report.routes || []).filter((r) => r.available);
  const rankedAvailable = [...availableRoutes].sort(
    (a, b) => routeEffectiveCost(a, mode) - routeEffectiveCost(b, mode)
  );
  const winningRoute = rankedAvailable[0] || null;
  const winningNet = winningRoute
    ? routeEffectiveCost(winningRoute, mode)
    : report.checkout_payable_inr;
  const winningRewardVal = winningRoute
    ? mode === "cash"
      ? winningRoute.rewards.cash_value_inr
      : Math.max(winningRoute.rewards.travel_value_inr, winningRoute.rewards.cash_value_inr)
    : 0;
  const unownedRoutes = report.unowned_card_routes || [];

  return (
    <section className="space-y-6">
      {/* ── Clear 3-Tier Price Display (Based Strictly on User's Selected Cards) ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* TIER 1: Listed Price */}
        <div className="p-6 rounded-2xl bg-surface-card border border-surface-border shadow-xl flex flex-col justify-between space-y-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-extrabold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
                <Tag className="w-3.5 h-3.5 text-slate-400" />
                <span>1. Listed Price</span>
              </span>
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-slate-300 font-semibold">
                {retailerLabel}
              </span>
            </div>
            <div className="text-3xl font-black text-white tracking-tight">
              {formatINR(report.listed_price)}
            </div>
            <p className="text-xs text-slate-400">
              Base catalog price on the product page before verified instant discounts, coupons, or mandatory fees.
            </p>
          </div>

          <div className="pt-3 border-t border-surface-border text-[11px] text-slate-400 space-y-1">
            <div className="flex justify-between">
              <span>Mandatory Fees</span>
              <span className={report.mandatory_fees_inr > 0 ? "text-rose-400 font-mono font-bold" : "text-slate-500 font-mono"}>
                +{formatINR(report.mandatory_fees_inr)}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Verified Page Coupon</span>
              <span className={report.coupon_discount_inr > 0 ? "text-emerald-400 font-mono font-bold" : "text-slate-500 font-mono"}>
                -{formatINR(report.coupon_discount_inr)}
              </span>
            </div>
          </div>
        </div>

        {/* TIER 2: Cheapest Checkout Cash Price (With Your Cards/Offers) */}
        <div className="p-6 rounded-2xl bg-surface-card border border-indigo-500/40 shadow-xl flex flex-col justify-between space-y-4">
          <div className="space-y-2">
            <span className="text-[11px] font-extrabold uppercase tracking-wider text-indigo-300 flex items-center gap-1.5">
              <Wallet className="w-3.5 h-3.5 text-indigo-400" />
              <span>2. Cheapest Checkout Cash Price</span>
            </span>
            <div className="text-3xl sm:text-4xl font-black text-white tracking-tight">
              {formatINR(report.checkout_payable_inr)}
            </div>
            <p className="text-xs text-slate-400">
              Exact cash payable today using <strong className="text-slate-200">your selected wallet cards</strong> &amp; live page offers. Points are never subtracted here.
            </p>
          </div>

          <div className="pt-3 border-t border-surface-border space-y-1.5 text-xs">
            <div className="flex items-center justify-between text-slate-300">
              <span>Verified Live Instant Bank Discount</span>
              <span
                className={`font-mono font-bold ${
                  report.instant_bank_discount_inr > 0 ? "text-emerald-400" : "text-slate-500"
                }`}
              >
                -{formatINR(report.instant_bank_discount_inr)}
              </span>
            </div>
            {report.winning_instant_offer_summary && (
              <div className="text-[11px] text-emerald-300 leading-snug">
                ✓ {report.winning_instant_offer_summary}
              </div>
            )}
            {report.user_wallet_empty && (
              <div className="text-[11px] text-amber-300">
                Select your owned cards in My Card Wallet above to match instant bank discounts.
              </div>
            )}
          </div>
        </div>

        {/* TIER 3: Cheapest Effective Net Price (After Verified Card Rewards) */}
        <div className="p-6 rounded-2xl bg-gradient-to-br from-indigo-950/40 via-surface-card to-emerald-950/20 border border-emerald-500/40 shadow-xl flex flex-col justify-between space-y-4">
          <div className="space-y-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] font-extrabold uppercase tracking-wider text-emerald-400 flex items-center gap-1.5">
                <Gift className="w-3.5 h-3.5" />
                <span>3. Cheapest Effective Net Price</span>
              </span>

              {/* Interactive Redemption Mode Toggle */}
              <div
                className="inline-flex rounded-xl p-0.5 bg-surface border border-surface-border"
                role="tablist"
                aria-label="Reward redemption mode"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "cash"}
                  onClick={() => setMode("cash")}
                  className={`px-2.5 py-1 rounded-lg text-[10.5px] font-bold transition-all ${
                    mode === "cash"
                      ? "bg-emerald-500 text-slate-950 shadow"
                      : "text-slate-400 hover:text-white"
                  }`}
                >
                  Cash
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "travel"}
                  onClick={() => setMode("travel")}
                  className={`px-2.5 py-1 rounded-lg text-[10.5px] font-bold transition-all ${
                    mode === "travel"
                      ? "bg-brand-primary text-white shadow"
                      : "text-slate-400 hover:text-white"
                  }`}
                >
                  Travel / Vouchers
                </button>
              </div>
            </div>

            <div className="flex items-baseline gap-2.5 flex-wrap">
              <div className="text-3xl sm:text-4xl font-black text-emerald-400 tracking-tight">
                {formatINR(winningNet)}
              </div>
              {winningRewardVal > 0 && (
                <span className="text-[11px] font-bold px-2 py-0.5 rounded-lg bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                  +{formatINR(winningRewardVal)} verified rewards
                </span>
              )}
            </div>

            {winningRoute && (winningRewardVal > 0 || winningRoute.instant_discount_inr > 0) ? (
              <div className="text-xs text-slate-300 space-y-1">
                <div>
                  Winning Owned Card: <strong className="text-emerald-300">{winningRoute.method_name}</strong>
                </div>
                {winningRoute.rewards.points_earned > 0 && (
                  <div className="text-[11px] text-slate-400">
                    {winningRoute.rewards.points_earned.toLocaleString("en-IN")} pts ·{" "}
                    {mode === "cash"
                      ? winningRoute.rewards.conversion_ratio_cash
                      : winningRoute.rewards.conversion_ratio_travel || winningRoute.rewards.conversion_ratio_cash}
                  </div>
                )}
                {winningRoute.rewards.monthly_cap_inr != null && (
                  <div className="text-[11px] text-amber-300">
                    Monthly Cap: {formatINR(winningRoute.rewards.monthly_cap_inr)}{" "}
                    {winningRoute.rewards.cap_applied ? "(Cap Hit on this order)" : "(Within cap)"}
                  </div>
                )}
                {winningRoute.exclusions.length > 0 && (
                  <div className="text-[11px] text-amber-200/90">
                    T&amp;C Catch: {winningRoute.exclusions[0]}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-xs text-slate-400">
                {report.user_wallet_empty
                  ? "No cards selected in My Card Wallet."
                  : "No active verified post-purchase rewards found for your selected cards. Click below to crawl live issuer T&Cs."}
              </p>
            )}
          </div>

          <div className="pt-3 border-t border-surface-border flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
            <span>
              {report.terms_last_verified_at
                ? `T&Cs verified: ${new Date(report.terms_last_verified_at).toLocaleDateString("en-IN")}`
                : "No cached live T&Cs yet"}
            </span>
            {onRefreshLiveTerms && (
              <button
                type="button"
                onClick={onRefreshLiveTerms}
                disabled={refreshingTerms}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface border border-surface-border hover:border-brand-primary/60 text-indigo-300 font-semibold disabled:opacity-50"
              >
                <RefreshCw className={`w-3 h-3 ${refreshingTerms ? "animate-spin" : ""}`} />
                <span>{refreshingTerms ? "Crawling Official T&Cs..." : "Verify Live T&Cs"}</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── Payment Strategy & T&C Matrix (Your Wallet Only) ── */}
      <div className="p-6 rounded-2xl bg-surface-card border border-surface-border shadow-xl space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-surface-border pb-3">
          <div>
            <span className="text-[11px] font-extrabold uppercase tracking-wider text-brand-primary">
              VERIFIED PAYMENT STRATEGY MATRIX (YOUR WALLET)
            </span>
            <h2 className="text-lg font-bold text-white mt-0.5">
              Direct Points vs. Cashback vs. Gift Voucher vs. UPI App
            </h2>
          </div>
          <span className="text-xs text-slate-400">
            Redemption Mode:{" "}
            <strong className="text-white">
              {mode === "cash" ? "Statement Cash" : "Travel / Vouchers"}
            </strong>
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {(report.routes || []).map((route) => {
            const effCost = routeEffectiveCost(route, mode);
            const isWinner =
              winningRoute && route.available && route.route_type === winningRoute.route_type;
            const badge = verificationBadge(route.verification_status);
            const rewardVal =
              mode === "cash"
                ? route.rewards.cash_value_inr
                : Math.max(route.rewards.travel_value_inr, route.rewards.cash_value_inr);
            const safeSources = (route.source_urls || [])
              .map((u) => safeUrl(u))
              .filter((u): u is string => Boolean(u));

            return (
              <div
                key={route.route_type}
                className={`p-4 rounded-xl border flex flex-col justify-between space-y-3 transition-all ${
                  !route.available
                    ? "bg-surface/40 border-surface-border/60 opacity-70"
                    : isWinner
                    ? "bg-emerald-950/20 border-emerald-500/60 ring-1 ring-emerald-500/30"
                    : "bg-surface border-surface-border"
                }`}
              >
                <div className="space-y-2.5">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-[11px] font-extrabold uppercase tracking-wide text-indigo-300">
                      {route.route_label}
                    </span>
                    {isWinner && (
                      <span className="text-[10px] font-black px-2 py-0.5 rounded-full bg-emerald-500 text-slate-950 uppercase">
                        Best
                      </span>
                    )}
                  </div>

                  <div className="text-sm font-bold text-white leading-snug">{route.method_name}</div>

                  <div className="flex flex-wrap gap-1.5">
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${badge.cls}`}>
                      {badge.label}
                    </span>
                    {route.rewards.cap_applied && (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded border bg-rose-500/15 text-rose-300 border-rose-500/35">
                        Monthly Cap Hit
                      </span>
                    )}
                  </div>

                  {route.available ? (
                    <div className="pt-2 border-t border-surface-border/60 space-y-1.5 text-xs">
                      <div className="flex justify-between">
                        <span className="text-slate-400">Checkout Cash</span>
                        <span className="font-mono font-bold text-white">
                          {formatINR(route.checkout_payable_inr)}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-400">
                          {mode === "cash" ? "Verified Cash Value" : "Verified Optimal Value"}
                        </span>
                        <span className={`font-mono font-bold ${rewardVal > 0 ? "text-emerald-400" : "text-slate-500"}`}>
                          +{formatINR(rewardVal)}
                        </span>
                      </div>
                      {route.rewards.processing_fee_inr > 0 && (
                        <div className="flex justify-between">
                          <span className="text-slate-400">Processing Fee</span>
                          <span className="font-mono font-bold text-rose-400">
                            +{formatINR(route.rewards.processing_fee_inr)}
                          </span>
                        </div>
                      )}
                      <div className="flex justify-between pt-1.5 border-t border-surface-border/50">
                        <span className="text-slate-300 font-semibold">Effective Net</span>
                        <span className="font-mono font-black text-emerald-300 text-sm">
                          {formatINR(effCost)}
                        </span>
                      </div>

                      {route.rewards.points_earned > 0 && (
                        <div className="pt-1.5 text-[11px] text-slate-400 space-y-0.5">
                          <div>
                            Earned:{" "}
                            <strong className="text-slate-200">
                              {route.rewards.points_earned.toLocaleString("en-IN")}
                            </strong>{" "}
                            {route.rewards.reward_type === "cashback" ? "₹ cashback" : "pts"}
                          </div>
                          {route.rewards.conversion_ratio_cash && (
                            <div className="text-[10.5px] text-slate-400">
                              Cash: {route.rewards.conversion_ratio_cash}
                            </div>
                          )}
                          {route.rewards.conversion_ratio_travel &&
                            route.rewards.conversion_ratio_travel !== route.rewards.conversion_ratio_cash && (
                              <div className="text-[10.5px] text-indigo-300">
                                Travel: {route.rewards.conversion_ratio_travel}
                              </div>
                            )}
                          {route.rewards.monthly_cap_inr != null && (
                            <div className="text-[10.5px] text-amber-300/90">
                              Monthly Cap: {formatINR(route.rewards.monthly_cap_inr)}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-400 pt-1">
                      {route.summary || "No active verified offer found"}
                    </p>
                  )}
                </div>

                {/* Expandable Verified T&Cs & Exclusions */}
                {(route.exclusions.length > 0 || safeSources.length > 0 || route.summary) && (
                  <details className="pt-2 border-t border-surface-border/60 text-[11px] text-slate-300">
                    <summary className="cursor-pointer font-semibold text-indigo-300 hover:text-indigo-200 select-none">
                      Exact Verified T&amp;Cs &amp; Exclusions
                    </summary>
                    <div className="mt-2 space-y-1.5 text-slate-400">
                      {route.summary && <p className="text-slate-300">{route.summary}</p>}
                      {route.exclusions.length > 0 && (
                        <ul className="list-disc pl-4 space-y-1">
                          {route.exclusions.map((ex, i) => (
                            <li key={i}>{ex}</li>
                          ))}
                        </ul>
                      )}
                      {route.last_verified_at && (
                        <div className="text-[10px] text-slate-500">
                          Verified: {new Date(route.last_verified_at).toLocaleString("en-IN")}
                        </div>
                      )}
                      {safeSources.length > 0 && (
                        <div className="flex flex-wrap gap-2 pt-1">
                          {safeSources.map((src, i) => (
                            <a
                              key={i}
                              href={src}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 text-indigo-400 hover:text-indigo-300 underline"
                            >
                              <span>Official Source #{i + 1}</span>
                              <ExternalLink className="w-3 h-3" />
                            </a>
                          ))}
                        </div>
                      )}
                    </div>
                  </details>
                )}
              </div>
            );
          })}
        </div>

        {/* Separate Collapsible "Other Cards (Not in Your Wallet)" */}
        {unownedRoutes.length > 0 && (
          <details className="p-4 rounded-xl bg-surface/60 border border-dashed border-surface-border">
            <summary className="cursor-pointer text-xs font-bold text-slate-300 hover:text-white flex items-center justify-between select-none">
              <span className="flex items-center gap-2">
                <ChevronDown className="w-4 h-4 text-slate-400" />
                <span>
                  Other Cards (Not in Your Wallet) — {unownedRoutes.length} verified offer
                  {unownedRoutes.length > 1 ? "s" : ""} (excluded from your primary price)
                </span>
              </span>
            </summary>
            <div className="mt-3 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {unownedRoutes.map((u, idx) => {
                const uBadge = verificationBadge(u.verification_status);
                return (
                  <div
                    key={idx}
                    className="p-3.5 rounded-xl bg-surface-card border border-surface-border space-y-2 text-xs"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-bold text-white">{u.method_name}</span>
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${uBadge.cls}`}>
                        {uBadge.label}
                      </span>
                    </div>
                    <div className="flex justify-between text-slate-400">
                      <span>Checkout Cash</span>
                      <span className="font-mono font-bold text-white">
                        {formatINR(u.checkout_payable_inr)}
                      </span>
                    </div>
                    <div className="flex justify-between text-slate-400">
                      <span>Effective Net</span>
                      <span className="font-mono font-bold text-emerald-400">
                        {formatINR(routeEffectiveCost(u, mode))}
                      </span>
                    </div>
                    {u.summary && <p className="text-[11px] text-slate-400">{u.summary}</p>}
                  </div>
                );
              })}
            </div>
          </details>
        )}

        {/* Plain-English Payment Efficiency Verdict */}
        {report.efficiency_verdict && (
          <div className="p-4 rounded-xl bg-indigo-950/40 border border-indigo-500/35 flex items-start gap-3">
            <Sparkles className="w-5 h-5 text-indigo-400 flex-shrink-0 mt-0.5" />
            <div className="space-y-1">
              <div className="text-xs font-extrabold uppercase tracking-wider text-indigo-300">
                Plain-English Payment Efficiency Verdict
              </div>
              <p className="text-xs sm:text-sm text-slate-200 leading-relaxed">
                {report.efficiency_verdict}
              </p>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
