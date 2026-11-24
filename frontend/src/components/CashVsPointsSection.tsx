"use client";

import { useState } from "react";
import { ChevronDown, ExternalLink, RefreshCw } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Toggle } from "@/components/ui/Toggle";
import { CashVsPointsReport, PaymentRouteAnalysis, VerificationStatus } from "@/lib/types";
import { formatINR } from "@/lib/utils";

interface Props {
  report: CashVsPointsReport;
  retailerLabel: string;
  refreshingTerms?: boolean;
  onRefreshLiveTerms?: () => void;
  onChooseCards?: () => void;
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

function verificationBadge(status: VerificationStatus): { label: string; tone: "accent" | "neutral" } {
  if (status === "page_verified") return { label: "Verified on page", tone: "accent" };
  if (status === "live_tc_verified" || status === "issuer_tc_verified") {
    return { label: "Verified T&C", tone: "accent" };
  }
  return { label: "No offer", tone: "neutral" };
}

function routeEffectiveCost(route: PaymentRouteAnalysis, mode: RedemptionMode): number {
  if (mode === "cash") return route.effective_cost_if_cash_inr;
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
  onChooseCards,
}: Props) {
  const [mode, setMode] = useState<RedemptionMode>("cash");
  const [expanded, setExpanded] = useState(false);

  const availableRoutes = (report.routes || []).filter((r) => r.available);
  const rankedAvailable = [...availableRoutes].sort(
    (a, b) => routeEffectiveCost(a, mode) - routeEffectiveCost(b, mode)
  );
  const winningRoute = rankedAvailable[0] || null;
  const bestNetPrice = winningRoute
    ? routeEffectiveCost(winningRoute, mode)
    : report.checkout_payable_inr;
  const rewardsLater = winningRoute
    ? mode === "cash"
      ? winningRoute.rewards.cash_value_inr
      : Math.max(winningRoute.rewards.travel_value_inr, winningRoute.rewards.cash_value_inr)
    : 0;

  return (
    <Card className="p-5 sm:p-6 space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-[20px] font-semibold text-text">Your best way to pay</h2>
          <p className="text-[13px] text-muted mt-0.5">
            Separates guaranteed money off at checkout from rewards earned later.
          </p>
        </div>

        <Toggle
          ariaLabel="Reward value mode"
          value={mode}
          onChange={setMode}
          options={[
            { value: "cash", label: "Cash value" },
            { value: "travel", label: "Travel value" },
          ]}
        />
      </div>

      {/* Plain 2-column Pay Today vs Rewards Later */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="rounded-[8px] border border-border bg-surface-2/50 p-4 space-y-1.5">
          <div className="text-[12px] font-medium text-muted">Pay today ({retailerLabel})</div>
          <div className="font-mono text-[24px] font-semibold tabular-nums text-text">
            {formatINR(report.checkout_payable_inr)}
          </div>
          <div className="text-[12px] text-muted">
            {report.instant_bank_discount_inr > 0
              ? `Includes ${formatINR(report.instant_bank_discount_inr)} instant bank discount`
              : report.coupon_discount_inr > 0
              ? `Includes ${formatINR(report.coupon_discount_inr)} coupon`
              : "Guaranteed checkout amount"}
          </div>
        </div>

        <div className="rounded-[8px] border border-accent/30 bg-accent-subtle/40 p-4 space-y-1.5">
          <div className="text-[12px] font-medium text-accent">
            Best price after rewards later
          </div>
          <div className="font-mono text-[24px] font-semibold tabular-nums text-accent">
            {formatINR(bestNetPrice)}
          </div>
          <div className="text-[12px] text-muted">
            {rewardsLater > 0 && winningRoute
              ? `${formatINR(rewardsLater)} back via ${winningRoute.method_name}`
              : "No extra card rewards matched"}
          </div>
        </div>
      </div>

      {report.user_wallet_empty && onChooseCards && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-[8px] border border-border bg-surface-2 px-3.5 py-2.5 text-[13px]">
          <span className="text-muted">No cards selected — add yours to see card savings.</span>
          <button
            type="button"
            onClick={onChooseCards}
            className="font-medium text-accent hover:underline min-h-[36px]"
          >
            Choose cards
          </button>
        </div>
      )}

      {/* Expandable route breakdown */}
      <div className="pt-1 border-t border-border">
        <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((prev) => !prev)}
            className="min-h-[40px] inline-flex items-center gap-1.5 text-[13px] font-medium text-text hover:text-accent transition-colors duration-150"
          >
            <span>
              {expanded
                ? "Hide payment details"
                : `Show payment details (${(report.routes || []).length} options)`}
            </span>
            <ChevronDown
              strokeWidth={1.5}
              className={`w-4 h-4 text-muted transition-transform duration-150 ${
                expanded ? "rotate-180" : ""
              }`}
            />
          </button>

          {onRefreshLiveTerms && (
            <button
              type="button"
              onClick={onRefreshLiveTerms}
              disabled={refreshingTerms}
              className="min-h-[36px] inline-flex items-center gap-1.5 text-[12px] text-muted hover:text-text disabled:opacity-50"
            >
              <RefreshCw
                strokeWidth={1.5}
                className={`w-3.5 h-3.5 ${refreshingTerms ? "animate-spin" : ""}`}
              />
              <span>{refreshingTerms ? "Checking terms..." : "Recheck terms"}</span>
            </button>
          )}
        </div>

        {expanded && (
          <div className="mt-3 space-y-2">
            {(report.routes || []).map((r, idx) => {
              const badge = verificationBadge(r.verification_status);
              const rw = r.rewards || {};
              const net = routeEffectiveCost(r, mode);
              const rew = mode === "cash" ? rw.cash_value_inr : Math.max(rw.travel_value_inr, rw.cash_value_inr);
              const firstSource = (r.source_urls || []).map(safeUrl).find(Boolean);

              return (
                <div
                  key={idx}
                  className={`rounded-[8px] border border-border p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${
                    r.available ? "bg-surface" : "bg-surface-2/60 opacity-75"
                  }`}
                >
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-medium text-text">{r.method_name}</span>
                      <Badge tone={badge.tone}>{badge.label}</Badge>
                      {rw.cap_applied && <Badge tone="warn">Cap reached</Badge>}
                    </div>
                    <div className="text-[12px] text-muted">
                      {r.route_label}
                      {r.summary ? ` · ${r.summary}` : ""}
                      {firstSource && (
                        <>
                          {" · "}
                          <a
                            href={firstSource}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-0.5 text-accent hover:underline"
                          >
                            <span>Terms</span>
                            <ExternalLink strokeWidth={1.5} className="w-3 h-3" />
                          </a>
                        </>
                      )}
                    </div>
                  </div>

                  {r.available && (
                    <div className="flex items-center gap-5 sm:text-right flex-shrink-0">
                      <div>
                        <div className="text-[11px] text-muted">Pay today</div>
                        <div className="font-mono text-[13px] font-medium tabular-nums text-text">
                          {formatINR(r.checkout_payable_inr)}
                        </div>
                      </div>
                      <div>
                        <div className="text-[11px] text-muted">Rewards</div>
                        <div className="font-mono text-[13px] font-medium tabular-nums text-accent">
                          {rew > 0 ? `-${formatINR(rew)}` : "₹0"}
                        </div>
                      </div>
                      <div>
                        <div className="text-[11px] text-muted">Best price</div>
                        <div className="font-mono text-[14px] font-semibold tabular-nums text-text">
                          {formatINR(net)}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Card>
  );
}
