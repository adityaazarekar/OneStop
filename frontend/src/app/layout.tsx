import type { Metadata } from "next";
import { Geist, Geist_Mono, Instrument_Serif } from "next/font/google";
import "./globals.css";
import { CardWalletProvider } from "@/components/CardWalletProvider";
import { Navbar } from "@/components/Navbar";

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
});

const instrumentSerif = Instrument_Serif({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-instrument-serif",
});

export const metadata: Metadata = {
  title: "OneStop — Calm, Verified Price Intelligence",
  description:
    "Check whether to buy now or wait, compare verified stores, and see your real checkout price with the cards you own.",
};

const themeScript = `
(function() {
  try {
    var saved = localStorage.getItem('onestop_theme');
    var dark = saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches);
    if (dark) document.documentElement.classList.add('dark');
    else document.documentElement.classList.remove('dark');
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geist.variable} ${geistMono.variable} ${instrumentSerif.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen bg-bg text-text flex flex-col font-sans">
        <CardWalletProvider>
          <Navbar />
          <main className="flex-1 w-full max-w-[1080px] mx-auto px-4 sm:px-6 py-8 sm:py-12">
            {children}
          </main>
          <footer className="border-t border-border py-6 text-center text-[13px] text-muted">
            <div className="max-w-[1080px] mx-auto px-4 sm:px-6">
              OneStop · Verified Indian store prices &amp; warranty records
            </div>
          </footer>
        </CardWalletProvider>
      </body>
    </html>
  );
}
