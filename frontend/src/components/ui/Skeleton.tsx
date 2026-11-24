import * as React from "react";

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`animate-pulse rounded-[8px] bg-surface-2 ${className}`}
    />
  );
}
