"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, FileText, Plus, Upload } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Sheet } from "@/components/ui/Sheet";
import { Skeleton } from "@/components/ui/Skeleton";
import { fetchVaultItems, uploadInvoice } from "@/lib/api";
import { VaultItem } from "@/lib/types";
import { formatINR, retailerLabel } from "@/lib/utils";

export default function WarrantyVaultPage() {
  const [email, setEmail] = useState("user@example.com");
  const [items, setItems] = useState<VaultItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem("onestop_user_email");
      if (saved) setEmail(saved);
    } catch {}
  }, []);

  const loadVault = async (targetEmail: string) => {
    setLoading(true);
    setLoadError(false);
    try {
      const data = await fetchVaultItems(targetEmail);
      setItems(data);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (email) loadVault(email);
  }, [email]);

  const handleEmailChange = (newEmail: string) => {
    setEmail(newEmail);
    try {
      localStorage.setItem("onestop_user_email", newEmail);
    } catch {}
  };

  const handleUpload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setUploadError("Please select a PDF invoice file.");
      return;
    }

    setUploading(true);
    setUploadError(null);
    try {
      const uploaded = await uploadInvoice(file, email);
      setUploadSuccess(`Added ${uploaded.product_name} to your vault.`);
      setDialogOpen(false);
      loadVault(email);
    } catch (err: any) {
      setUploadError(err?.message || "Couldn't read that PDF. Please try another invoice.");
    } finally {
      setUploading(false);
    }
  };

  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleUpload(e.dataTransfer.files[0]);
    }
  };

  return (
    <div className="max-w-[840px] mx-auto space-y-6">
      {/* ── Header ── */}
      <header className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 border-b border-border pb-5">
        <div className="space-y-1">
          <h1 className="text-[28px] font-semibold text-text leading-tight">Warranty vault</h1>
          <p className="text-[14px] text-muted">
            Store PDF tax invoices and track warranty expiry dates in one place.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <input
            type="email"
            aria-label="Account email"
            value={email}
            onChange={(e) => handleEmailChange(e.target.value)}
            placeholder="you@example.com"
            className="min-h-[44px] px-3 rounded-[8px] border border-border bg-surface text-[13px] text-text placeholder:text-muted focus:border-accent focus:outline-none w-full sm:w-52"
          />
          <Button
            onClick={() => {
              setUploadError(null);
              setDialogOpen(true);
            }}
            className="w-full sm:w-auto"
          >
            <Plus strokeWidth={1.5} className="w-4 h-4" />
            <span>Add invoice</span>
          </Button>
        </div>
      </header>

      {uploadSuccess && (
        <div className="rounded-[8px] border border-accent/30 bg-accent-subtle px-4 py-3 text-[13px] text-accent flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 strokeWidth={1.5} className="w-4 h-4 flex-shrink-0" />
            <span>{uploadSuccess}</span>
          </div>
          <button
            type="button"
            onClick={() => setUploadSuccess(null)}
            className="text-[12px] font-medium underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* ── Vault Items List ── */}
      <section className="space-y-3">
        <div className="flex items-center justify-between text-[13px] text-muted">
          <span>Saved items ({items.length})</span>
          <span>Sorted by nearest expiry</span>
        </div>

        <Card className="overflow-hidden">
          {loading ? (
            <div className="divide-y divide-border">
              {[1, 2, 3].map((n) => (
                <div key={n} className="p-4 flex items-center justify-between gap-4">
                  <div className="space-y-1.5 flex-1">
                    <Skeleton className="h-4 w-2/3" />
                    <Skeleton className="h-3 w-1/3" />
                  </div>
                  <Skeleton className="h-6 w-24" />
                </div>
              ))}
            </div>
          ) : loadError ? (
            <div className="p-8 text-center space-y-3">
              <AlertCircle strokeWidth={1.5} className="w-5 h-5 text-danger mx-auto" />
              <p className="text-[13px] text-muted">Couldn&apos;t load your vault.</p>
              <Button variant="outline" size="sm" onClick={() => loadVault(email)}>
                Retry
              </Button>
            </div>
          ) : items.length === 0 ? (
            <div className="p-10 text-center space-y-3">
              <FileText strokeWidth={1.5} className="w-6 h-6 text-muted mx-auto" />
              <p className="text-[14px] text-muted">
                No invoices saved yet. Drop a PDF tax invoice to track its warranty countdown.
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setUploadError(null);
                  setDialogOpen(true);
                }}
              >
                Upload PDF invoice
              </Button>
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((item) => {
                const isExpired = item.days_remaining < 0;
                const isUrgent = item.days_remaining >= 0 && item.days_remaining <= 30;
                const badgeTone = isExpired ? "danger" : isUrgent ? "warn" : "accent";
                const countdownText = isExpired
                  ? "Expired"
                  : `${item.days_remaining}d left`;

                return (
                  <li
                    key={item.id}
                    className="p-4 sm:px-5 flex flex-col sm:flex-row sm:items-center justify-between gap-3"
                  >
                    <div className="min-w-0 space-y-1">
                      <div className="text-[14px] font-medium text-text truncate">
                        {item.product_name}
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
                        {item.retailer && <span>{retailerLabel(item.retailer)}</span>}
                        {item.invoice_number && (
                          <span className="font-mono tabular-nums">#{item.invoice_number}</span>
                        )}
                        <span>Purchased {item.purchase_date}</span>
                        <span>· Expires {item.warranty_expiry_date}</span>
                      </div>
                    </div>

                    <div className="flex items-center justify-between sm:justify-end gap-4 flex-shrink-0">
                      <span className="font-mono text-[14px] font-medium tabular-nums text-text">
                        {formatINR(item.price_paid)}
                      </span>
                      <Badge tone={badgeTone}>
                        <span className="font-mono tabular-nums">{countdownText}</span>
                      </Badge>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </section>

      {/* ── Upload PDF Dialog ── */}
      <Sheet
        open={dialogOpen}
        onClose={() => !uploading && setDialogOpen(false)}
        variant="dialog"
        title="Add PDF invoice"
        description="Drop a tax invoice PDF from Amazon, Flipkart, or Croma."
      >
        <div className="space-y-4">
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleFileDrop}
            onClick={() => !uploading && fileInputRef.current?.click()}
            className="rounded-[12px] border border-dashed border-border bg-surface-2/60 hover:bg-surface-2 hover:border-accent/60 transition-colors duration-150 p-8 text-center space-y-2 cursor-pointer"
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf"
              className="hidden"
              onChange={(e) => {
                if (e.target.files && e.target.files.length > 0) {
                  handleUpload(e.target.files[0]);
                }
              }}
            />
            <Upload strokeWidth={1.5} className="w-6 h-6 text-accent mx-auto" />
            <div className="text-[14px] font-medium text-text">
              {uploading ? "Reading invoice..." : "Drop PDF here or click to browse"}
            </div>
            <div className="text-[12px] text-muted">PDF up to 10 MB</div>
          </div>

          {uploadError && (
            <div
              role="alert"
              className="rounded-[8px] border border-danger/30 bg-danger-subtle px-3.5 py-2.5 text-[13px] text-danger flex items-start gap-2"
            >
              <AlertCircle strokeWidth={1.5} className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span>{uploadError}</span>
            </div>
          )}
        </div>
      </Sheet>
    </div>
  );
}
