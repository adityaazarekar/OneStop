import type { Metadata } from "next";
import "./globals.css";
import { Navbar } from "@/components/Navbar";

export const metadata: Metadata = {
  title: "OneStop — India E-Commerce Price Intelligence & Consumer Trust",
  description:
    "Real-time Indian shopping decision engine: cross-store price comparison, credit card reward optimizer, seller forensics & buy-or-wait signals.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-background text-foreground flex flex-col antialiased">
        <Navbar />
        <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {children}
        </main>
        <footer className="border-t border-surface-border py-8 text-center text-xs text-slate-500">
          <p>© 2026 OneStop Engine — ₹0 Proxy Architecture for Indian E-Commerce</p>
        </footer>
      </body>
    </html>
  );
}
