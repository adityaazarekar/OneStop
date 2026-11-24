"use client";

import * as React from "react";
import { X } from "lucide-react";

export interface SheetProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  variant?: "drawer" | "dialog";
}

export function Sheet({
  open,
  onClose,
  title,
  description,
  children,
  variant = "drawer",
}: SheetProps) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-end bg-black/35"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {variant === "dialog" ? (
        <div className="m-auto w-full max-w-md mx-4 rounded-[12px] border border-border bg-surface shadow-popover overflow-hidden">
          <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border">
            <div>
              <h2 className="text-[16px] font-semibold text-text">{title}</h2>
              {description && (
                <p className="text-[13px] text-muted mt-0.5">{description}</p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="min-w-[44px] min-h-[44px] -mr-2 -mt-2 flex items-center justify-center rounded-[8px] text-muted hover:text-text hover:bg-surface-2 transition-colors duration-150"
            >
              <X strokeWidth={1.5} className="w-4 h-4" />
            </button>
          </div>
          <div className="p-5">{children}</div>
        </div>
      ) : (
        <div className="w-full max-w-md h-full bg-surface border-l border-border shadow-popover flex flex-col">
          <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border">
            <div>
              <h2 className="text-[16px] font-semibold text-text">{title}</h2>
              {description && (
                <p className="text-[13px] text-muted mt-0.5">{description}</p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="min-w-[44px] min-h-[44px] -mr-2 -mt-2 flex items-center justify-center rounded-[8px] text-muted hover:text-text hover:bg-surface-2 transition-colors duration-150"
            >
              <X strokeWidth={1.5} className="w-4 h-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-5">{children}</div>
        </div>
      )}
    </div>
  );
}
