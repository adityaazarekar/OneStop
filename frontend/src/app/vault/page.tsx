"use client";

import { useEffect, useState, useRef } from "react";
import {
  FileText,
  UploadCloud,
  CheckCircle2,
  AlertCircle,
  Clock,
  Store,
  Calendar,
  Sparkles,
  ShieldCheck,
  AlertTriangle,
} from "lucide-react";
import { fetchVaultItems, uploadInvoice } from "@/lib/api";
import { VaultItem } from "@/lib/types";
import { formatINR, retailerLabel } from "@/lib/utils";

export default function WarrantyVaultPage() {
  const [email, setEmail] = useState("user@example.com");
  const [items, setItems] = useState<VaultItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const saved = localStorage.getItem("onestop_user_email");
    if (saved) setEmail(saved);
  }, []);

  const loadVault = async (targetEmail: string) => {
    setLoading(true);
    try {
      const data = await fetchVaultItems(targetEmail);
      setItems(data);
    } catch (err) {
      console.warn(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (email) loadVault(email);
  }, [email]);

  const handleEmailChange = (newEmail: string) => {
    setEmail(newEmail);
    localStorage.setItem("onestop_user_email", newEmail);
  };

  const handleFileDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleUpload(e.dataTransfer.files[0]);
    }
  };

  const handleUpload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setUploadError("Please upload a PDF invoice.");
      return;
    }

    setUploading(true);
    setUploadError(null);
    setUploadSuccess(null);

    try {
      const uploaded = await uploadInvoice(file, email);
      setUploadSuccess(`Successfully extracted and registered: ${uploaded.product_name}`);
      loadVault(email);
    } catch (err: any) {
      setUploadError(err.message || "Invoice processing failed. Ensure backend has PDF text parser active.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-8 animate-fade-in max-w-5xl mx-auto">
      {/* ── Page Header ── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-surface-border pb-6">
        <div>
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 mb-2">
            <ShieldCheck className="w-3.5 h-3.5" />
            <span>AI Warranty & Consumer Protection Vault</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-black text-white tracking-tight">
            Warranty & Invoice Vault
          </h1>
          <p className="text-xs sm:text-sm text-slate-400 mt-1">
            Upload Amazon/Flipkart/Croma PDF invoices. OpenRouter AI parses serials, dates, and warranties automatically.
          </p>
        </div>

        {/* User Email Filter */}
        <div className="flex items-center gap-2 bg-surface-card p-2 rounded-xl border border-surface-border">
          <span className="text-xs text-slate-400 pl-2">Account:</span>
          <input
            type="email"
            value={email}
            onChange={(e) => handleEmailChange(e.target.value)}
            className="p-1.5 rounded-lg bg-surface border border-surface-border text-xs text-white focus:outline-none focus:border-brand-primary"
            placeholder="your-email@example.com"
          />
        </div>
      </div>

      {/* ── Drag & Drop Uploader ── */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleFileDrop}
        onClick={() => fileInputRef.current?.click()}
        className="p-8 sm:p-12 rounded-2xl border-2 border-dashed border-surface-border hover:border-brand-primary/60 bg-surface-card/60 hover:bg-surface-card transition-all cursor-pointer text-center space-y-3"
      >
        <input
          type="file"
          ref={fileInputRef}
          accept=".pdf"
          className="hidden"
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0) {
              handleUpload(e.target.files[0]);
            }
          }}
        />

        <div className="w-12 h-12 rounded-2xl bg-brand-primary/10 border border-brand-primary/20 flex items-center justify-center text-brand-primary mx-auto">
          {uploading ? (
            <div className="w-6 h-6 border-2 border-brand-primary border-t-transparent rounded-full animate-spin" />
          ) : (
            <UploadCloud className="w-6 h-6" />
          )}
        </div>

        <div>
          <h3 className="text-sm font-bold text-white">
            {uploading ? "Extracting invoice fields with OpenRouter..." : "Drop PDF Tax Invoice Here"}
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            or click to browse from your computer (max 10MB)
          </p>
        </div>

        <div className="flex items-center justify-center gap-4 text-[11px] text-slate-500 pt-2">
          <span>✅ Amazon.in PDF</span>
          <span>✅ Flipkart Tax Invoice</span>
          <span>✅ Croma E-Receipt</span>
        </div>
      </div>

      {/* Upload Feedback */}
      {uploadSuccess && (
        <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          <span>{uploadSuccess}</span>
        </div>
      )}

      {uploadError && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
          <span>{uploadError}</span>
        </div>
      )}

      {/* ── Registered Vault Items Grid ── */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <FileText className="w-4 h-4 text-brand-accent" />
            <span>Registered Warranties ({items.length})</span>
          </h2>
          <span className="text-xs text-slate-500">Sorted by nearest expiration</span>
        </div>

        {loading ? (
          <div className="p-12 text-center text-xs text-slate-500">
            Loading vault items...
          </div>
        ) : items.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {items.map((item) => {
              const isUrgent = item.days_remaining <= 30 && item.days_remaining >= 0;
              const isExpired = item.days_remaining < 0;

              return (
                <div
                  key={item.id}
                  className={`p-5 rounded-2xl bg-surface-card border transition-all space-y-4 ${
                    isUrgent
                      ? "border-amber-500/50 bg-amber-500/5"
                      : isExpired
                      ? "border-rose-500/30 opacity-75"
                      : "border-surface-border"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-[10px]">
                        {item.retailer && (
                          <span className="px-1.5 py-0.5 rounded bg-surface border border-surface-border text-slate-300 font-semibold uppercase">
                            {retailerLabel(item.retailer)}
                          </span>
                        )}
                        {item.invoice_number && (
                          <span className="text-slate-500 font-mono">
                            Inv #{item.invoice_number}
                          </span>
                        )}
                      </div>
                      <h3 className="text-sm font-bold text-white mt-1 line-clamp-2">
                        {item.product_name}
                      </h3>
                      {item.seller_name && (
                        <p className="text-[11px] text-slate-400 mt-0.5">
                          Sold by: <span className="text-slate-300">{item.seller_name}</span>
                        </p>
                      )}
                    </div>

                    <div className="text-right flex-shrink-0">
                      <div className="text-sm font-black text-white">
                        {formatINR(item.price_paid)}
                      </div>
                      <div className="text-[10px] text-slate-500">Price Paid</div>
                    </div>
                  </div>

                  {/* Warranty Expiry Indicator */}
                  <div className="p-3 rounded-xl bg-surface border border-surface-border space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-slate-400 flex items-center gap-1.5">
                        <Clock className="w-3.5 h-3.5" />
                        <span>Warranty Status</span>
                      </span>

                      {isExpired ? (
                        <span className="font-bold text-rose-400">Expired</span>
                      ) : isUrgent ? (
                        <span className="font-bold text-amber-400 flex items-center gap-1">
                          <AlertTriangle className="w-3 h-3" />
                          <span>Expiring in {item.days_remaining} days!</span>
                        </span>
                      ) : (
                        <span className="font-bold text-emerald-400">
                          {item.days_remaining} days remaining
                        </span>
                      )}
                    </div>

                    {/* Progress bar */}
                    <div className="w-full h-1.5 bg-surface-border rounded-full overflow-hidden">
                      <div
                        className={`h-full transition-all ${
                          isExpired
                            ? "bg-rose-500 w-full"
                            : isUrgent
                            ? "bg-amber-400"
                            : "bg-emerald-400"
                        }`}
                        style={{
                          width: isExpired
                            ? "100%"
                            : `${Math.min(
                                100,
                                Math.max(5, (item.days_remaining / (item.warranty_months * 30)) * 100)
                              )}%`,
                        }}
                      />
                    </div>

                    <div className="flex items-center justify-between text-[10px] text-slate-500 pt-0.5">
                      <span>Purchased: {item.purchase_date}</span>
                      <span>Expires: {item.warranty_expiry_date}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="p-12 text-center rounded-2xl bg-surface-card border border-surface-border space-y-2 text-xs text-slate-500">
            <Store className="w-8 h-8 text-slate-600 mx-auto" />
            <p>No warranty invoices uploaded for this email yet.</p>
            <p className="text-[11px] text-slate-600">
              Drag and drop an electronic tax invoice PDF above to get automated expiry tracking!
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
