import * as React from "react";

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: "default" | "muted" | "accent";
}

const toneClasses: Record<NonNullable<CardProps["tone"]>, string> = {
  default: "bg-surface border-border",
  muted: "bg-surface-2 border-border",
  accent: "bg-surface border-accent/40",
};

export function Card({ tone = "default", className = "", children, ...props }: CardProps) {
  return (
    <div
      className={`rounded-[12px] border ${toneClasses[tone]} ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}
