"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertCircle, ArrowRight, Link2 } from "lucide-react";
import { useCardWallet } from "@/components/CardWalletProvider";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { analyzeUrl, fetchRecentProducts } from "@/lib/api";
import { RecentProduct } from "@/lib/types";
import { formatINR, retailerLabel } from "@/lib/utils";

export default function HomePage() {
  const router = useRouter();
  const { selectedIds } = useCardWallet();
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recentProducts, setRecentProducts] = useState<RecentProduct[]>([]);
  const [loadingRecent, setLoadingRecent] = useState(true);
  const [recentError, setRecentError] = useState(false);

  const loadRecent = async () => {
    setLoadingRecent(true);
    setRecentError(false);
    try {
      const prods = await fetchRecentProducts(12);
      setRecentProducts(prods);
    } catch {
      setRecentError(true);
    } finally {
      setLoadingRecent(false);
    }
  };

  useEffect(() => {
    loadRecent();
  }, []);

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() || loading) return;

    setLoading(true);
    setError(null);

    try {
      const result = await analyzeUrl(url.trim(), selectedIds);
      router.push(`/product/${result.canonical_product_id}`);
    } catch (err: any) {
      setError(
        err?.message ||
          "Couldn't check that link right now. Try again or use the Chrome extension on the store page."
      );
      setLoading(false);
    }
  };

  return (
    <div className="max-w-[760px] mx-auto space-y-12 pt-4 sm:pt-10">
      {/* ── Focal Input Section ── */}
      <section className="space-y-5">
        <div className="space-y-2.5">
          <h1 className="font-serif text-[40px] sm:text-[48px] leading-[1.08] tracking-tight text-text font-normal">
            Know the real price before you buy.
          </h1>
          <p className="text-[16px] text-muted leading-relaxed">
            Paste any Indian store link to compare verified prices, card savings, and whether to buy now or wait.
          </p>
        </div>

        <form onSubmit={handleAnalyze} className="space-y-3">
          <div className="flex flex-col sm:flex-row gap-2.5">
            <div className="relative flex-1">
              <Link2
                strokeWidth={1.5}
                className="w-4 h-4 text-muted absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none"
              />
              <input
                type="url"
                required
                aria-label="Product URL"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.amazon.in/dp/... or flipkart.com/..."
                className="w-full min-h-[48px] pl-10 pr-4 rounded-[8px] border border-border bg-surface text-[14px] text-text placeholder:text-muted focus:border-accent focus:outline-none transition-colors duration-150"
              />
            </div>

            <Button type="submit" disabled={loading} className="min-h-[48px] px-5">
              <span>{loading ? "Checking..." : "Check price"}</span>
              {!loading && <ArrowRight strokeWidth={1.5} className="w-4 h-4" />}
            </Button>
          </div>

          {error && (
            <div
              role="alert"
              className="rounded-[8px] border border-danger/30 bg-danger-subtle px-4 py-3 text-[13px] text-danger flex items-start justify-between gap-3"
            >
              <div className="flex items-start gap-2.5">
                <AlertCircle strokeWidth={1.5} className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
              <button
                type="button"
                onClick={() => setError(null)}
                className="text-[12px] font-medium underline flex-shrink-0"
              >
                Dismiss
              </button>
            </div>
          )}
        </form>
      </section>

      {/* ── Recent Products Compact List ── */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-[14px] font-semibold text-text">Recently checked</h2>
          {recentProducts.length > 0 && (
            <span className="text-[12px] text-muted tabular-nums">
              {recentProducts.length} items
            </span>
          )}
        </div>

        <Card className="overflow-hidden">
          {loadingRecent ? (
            <div className="divide-y divide-border">
              {[1, 2, 3, 4, 5].map((n) => (
                <div key={n} className="px-4 py-3.5 flex items-center justify-between gap-4">
                  <div className="space-y-1.5 flex-1">
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-3 w-1/3" />
                  </div>
                  <Skeleton className="h-4 w-20" />
                </div>
              ))}
            </div>
          ) : recentError ? (
            <div className="p-6 text-center space-y-3">
              <p className="text-[13px] text-muted">Couldn&apos;t load recent products.</p>
              <Button variant="outline" size="sm" onClick={loadRecent}>
                Retry
              </Button>
            </div>
          ) : recentProducts.length === 0 ? (
            <div className="p-8 text-center">
              <p className="text-[13px] text-muted">
                No products checked yet. Paste a product URL above to run your first check.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {recentProducts.map((item) => {
                const stores =
                  item.retailers && item.retailers.length > 0
                    ? item.retailers.map(retailerLabel).join(", ")
                    : "Store";
                return (
                  <li key={item.id}>
                    <Link
                      href={`/product/${item.id}`}
                      className="min-h-[56px] px-4 py-3 flex items-center justify-between gap-4 hover:bg-surface-2/60 transition-colors duration-150"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="text-[14px] font-medium text-text truncate">
                          {item.model_name}
                        </div>
                        <div className="text-[12px] text-muted truncate mt-0.5">
                          {item.brand} · {stores}
                          {!item.in_stock && " · Out of stock"}
                        </div>
                      </div>

                      <div className="flex items-center gap-3 flex-shrink-0">
                        <span className="font-mono text-[14px] font-medium tabular-nums text-text">
                          {item.lowest_price ? formatINR(item.lowest_price) : "—"}
                        </span>
                        <ArrowRight strokeWidth={1.5} className="w-4 h-4 text-muted" />
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </section>
    </div>
  );
}
