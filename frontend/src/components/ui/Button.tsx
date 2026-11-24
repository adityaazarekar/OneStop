"use client";

import * as React from "react";

type ButtonVariant = "primary" | "secondary" | "outline" | "ghost";
type ButtonSize = "md" | "sm";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-[#FAF9F6] dark:text-[#121210] border border-accent hover:bg-accent-hover hover:border-accent-hover",
  secondary:
    "bg-surface-2 text-text border border-border hover:bg-border/60",
  outline:
    "bg-surface text-text border border-border hover:bg-surface-2",
  ghost:
    "bg-transparent text-muted border border-transparent hover:text-text hover:bg-surface-2",
};

const sizeClasses: Record<ButtonSize, string> = {
  md: "min-h-[44px] px-4 text-[14px]",
  sm: "min-h-[44px] sm:min-h-[36px] px-3 text-[13px]",
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", size = "md", className = "", type = "button", ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={`inline-flex items-center justify-center gap-2 rounded-[8px] font-medium transition-colors duration-150 disabled:opacity-50 disabled:pointer-events-none whitespace-nowrap ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}
      {...props}
    />
  )
);
Button.displayName = "Button";
