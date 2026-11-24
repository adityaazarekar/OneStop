"use client";

import * as React from "react";

export interface ToggleOption<T extends string | number> {
  value: T;
  label: string;
}

export interface ToggleProps<T extends string | number> {
  options: ToggleOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
}

export function Toggle<T extends string | number>({
  options,
  value,
  onChange,
  ariaLabel,
}: ToggleProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="inline-flex items-center rounded-[8px] border border-border bg-surface-2 p-0.5"
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={String(opt.value)}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={`min-h-[36px] px-3 rounded-[6px] text-[13px] font-medium transition-colors duration-150 ${
              active
                ? "bg-surface text-text border border-border"
                : "text-muted hover:text-text border border-transparent"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
