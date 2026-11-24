"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";

export interface AccordionProps {
  title: string;
  summary: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export function Accordion({ title, summary, defaultOpen = false, children }: AccordionProps) {
  const [open, setOpen] = React.useState(defaultOpen);
  const contentId = React.useId();

  return (
    <div className="rounded-[12px] border border-border bg-surface">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => setOpen((prev) => !prev)}
        className="w-full min-h-[56px] px-5 py-3.5 flex items-center justify-between gap-4 text-left hover:bg-surface-2/60 transition-colors duration-150 rounded-[12px]"
      >
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold text-text">{title}</div>
          {!open && (
            <div className="text-[13px] text-muted truncate mt-0.5">{summary}</div>
          )}
        </div>
        <ChevronDown
          strokeWidth={1.5}
          className={`w-4 h-4 text-muted flex-shrink-0 transition-transform duration-150 ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open && (
        <div
          id={contentId}
          className="px-5 pb-5 pt-2 border-t border-border"
        >
          {children}
        </div>
      )}
    </div>
  );
}
