"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { CreditCard, Moon, Sun } from "lucide-react";
import { useCardWallet } from "@/components/CardWalletProvider";

export function Navbar() {
  const pathname = usePathname();
  const { selectedIds, openWallet } = useCardWallet();
  const [dark, setDark] = React.useState(false);

  React.useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("onestop_theme", next ? "dark" : "light");
    } catch {
      // ignore
    }
  };

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-bg/95 backdrop-blur-sm">
      <div className="max-w-[1080px] mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-3">
        <Link
          href="/"
          className="text-[18px] font-semibold tracking-tight text-text hover:text-accent transition-colors duration-150"
        >
          OneStop
        </Link>

        <nav className="flex items-center gap-1 sm:gap-2" aria-label="Primary">
          <Link
            href="/"
            className={`min-h-[44px] sm:min-h-[36px] px-3 rounded-[8px] text-[14px] font-medium flex items-center transition-colors duration-150 ${
              pathname === "/"
                ? "bg-surface-2 text-text"
                : "text-muted hover:text-text hover:bg-surface-2/60"
            }`}
          >
            Home
          </Link>

          <Link
            href="/vault"
            className={`min-h-[44px] sm:min-h-[36px] px-3 rounded-[8px] text-[14px] font-medium flex items-center transition-colors duration-150 ${
              pathname === "/vault"
                ? "bg-surface-2 text-text"
                : "text-muted hover:text-text hover:bg-surface-2/60"
            }`}
          >
            Vault
          </Link>

          <button
            type="button"
            onClick={openWallet}
            className="min-h-[44px] sm:min-h-[36px] px-2.5 sm:px-3 rounded-[8px] border border-border bg-surface text-[13px] sm:text-[14px] font-medium text-text hover:bg-surface-2 transition-colors duration-150 flex items-center gap-1.5 whitespace-nowrap"
          >
            <CreditCard strokeWidth={1.5} className="w-4 h-4 text-muted" />
            <span>My cards</span>
            {selectedIds.length > 0 && (
              <span className="font-mono text-[12px] tabular-nums text-accent">
                {selectedIds.length}
              </span>
            )}
          </button>

          <button
            type="button"
            onClick={toggleTheme}
            aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
            className="min-w-[44px] min-h-[44px] sm:min-w-[36px] sm:min-h-[36px] rounded-[8px] border border-transparent text-muted hover:text-text hover:bg-surface-2 transition-colors duration-150 flex items-center justify-center"
          >
            {dark ? (
              <Sun strokeWidth={1.5} className="w-4 h-4" />
            ) : (
              <Moon strokeWidth={1.5} className="w-4 h-4" />
            )}
          </button>
        </nav>
      </div>
    </header>
  );
}
