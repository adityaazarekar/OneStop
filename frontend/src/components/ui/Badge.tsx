import * as React from "react";
import { SemanticTone } from "@/lib/utils";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: SemanticTone;
}

const toneClasses: Record<SemanticTone, string> = {
  neutral: "bg-surface-2 text-muted border-border",
  accent: "bg-accent-subtle text-accent border-accent/30",
  warn: "bg-warn-subtle text-warn border-warn/30",
  danger: "bg-danger-subtle text-danger border-danger/30",
};

export function Badge({ tone = "neutral", className = "", children, ...props }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-[8px] border px-2 py-0.5 text-[12px] font-medium leading-tight whitespace-nowrap ${toneClasses[tone]} ${className}`}
      {...props}
    >
      {children}
    </span>
  );
}
