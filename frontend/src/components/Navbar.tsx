"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ShieldCheck, TrendingDown, FileText, Chrome } from "lucide-react";

export function Navbar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 backdrop-blur-md bg-background/80 border-b border-surface-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2 group">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-brand-primary to-brand-secondary flex items-center justify-center text-white font-bold text-lg shadow-lg shadow-brand-primary/25 group-hover:scale-105 transition-transform">
            ₹
          </div>
          <div>
            <span className="text-lg font-black tracking-tight text-white flex items-center gap-1.5">
              OneStop
              <span className="text-[10px] uppercase font-bold tracking-widest px-1.5 py-0.5 rounded bg-brand-primary/20 text-brand-primary border border-brand-primary/30">
                PROTOTYPE
              </span>
            </span>
            <p className="text-[10px] text-slate-400 font-medium">
              Price Intelligence & Consumer Trust
            </p>
          </div>
        </Link>

        <nav className="flex items-center gap-1 sm:gap-2">
          <Link
            href="/"
            className={`px-3 py-1.5 rounded-lg text-sm font-semibold flex items-center gap-1.5 transition-colors ${
              pathname === "/"
                ? "bg-surface-card text-white border border-surface-border"
                : "text-slate-400 hover:text-white hover:bg-surface-card/50"
            }`}
          >
            <TrendingDown className="w-4 h-4 text-brand-primary" />
            <span>Analyzer</span>
          </Link>

          <Link
            href="/vault"
            className={`px-3 py-1.5 rounded-lg text-sm font-semibold flex items-center gap-1.5 transition-colors ${
              pathname === "/vault"
                ? "bg-surface-card text-white border border-surface-border"
                : "text-slate-400 hover:text-white hover:bg-surface-card/50"
            }`}
          >
            <FileText className="w-4 h-4 text-brand-accent" />
            <span>Warranty Vault</span>
          </Link>

          <a
            href="#extension-info"
            onClick={(e) => {
              if (pathname !== "/") {
                // allow normal navigation
              } else {
                e.preventDefault();
                document.getElementById("extension-info")?.scrollIntoView({ behavior: "smooth" });
              }
            }}
            className="hidden md:flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 transition-colors"
          >
            <Chrome className="w-3.5 h-3.5" />
            <span>₹0 Proxy Extension Active</span>
          </a>
        </nav>
      </div>
    </header>
  );
}
