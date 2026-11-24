"""Live E2E smoke test against OneStop backend POST /api/v1/analyze-url."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

BASE_URL = os.environ.get("ONESTOP_API_URL", "http://127.0.0.1:8000").rstrip("/")
URLS_FILE = Path(__file__).resolve().parent / "smoke_urls.txt"


def _llm_label(summary: dict | None, health_llm: str) -> str:
    if not summary:
        return "none"
    why = (summary.get("why_this_wins") or "").strip()
    if why.startswith("Cheapest guaranteed upfront checkout of"):
        return "fallback"
    return "llm" if health_llm == "ok" else "fallback"


def main() -> int:
    urls = [
        line.strip()
        for line in URLS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not urls:
        print(f"No URLs found in {URLS_FILE}")
        return 1

    with httpx.Client(base_url=BASE_URL, timeout=45.0) as client:
        h = client.get("/health")
        if h.status_code != 200:
            print(f"FAIL /health -> HTTP {h.status_code}")
            return 1
        health_data = h.json()
        health_llm = health_data.get("llm", "unknown")
        print(f"Backend {BASE_URL} healthy: {health_data}\n")

        header = (
            f"{'RES':4s} | {'HTTP':4s} | {'TIME':>8s} | {'PRICE':>9s} | {'MRP':>9s} | "
            f"{'STK':5s} | {'VERDICT':8s} | {'LLM':8s} | {'SELLER':24s} | TITLE"
        )
        print(header)
        print("-" * len(header))

        passed = 0
        for url in urls:
            t0 = time.perf_counter()
            try:
                resp = client.post("/api/v1/analyze-url", params={"url": url})
                elapsed_ms = (time.perf_counter() - t0) * 1000
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                print(f"FAIL |  ERR | {elapsed_ms:6.1f}ms | {'-':>9s} | {'-':>9s} | {'-':5s} | {'-':8s} | {'-':8s} | {'-':24s} | {exc}")
                continue

            if resp.status_code != 200:
                print(
                    f"FAIL | {resp.status_code:4d} | {elapsed_ms:6.1f}ms | {'-':>9s} | {'-':>9s} | "
                    f"{'-':5s} | {'-':8s} | {'-':8s} | {'-':24s} | {resp.text[:50]}"
                )
                continue

            data = resp.json()
            title = (data.get("model_name") or "").strip()
            price = float(data.get("listed_price") or 0)
            listing = (data.get("listings") or [{}])[0]
            mrp = listing.get("mrp")
            seller = (listing.get("seller_name") or "").strip()
            in_stock = bool(listing.get("in_stock"))
            verdict = ((data.get("buy_wait") or {}).get("verdict") or "-").strip()
            llm_mode = _llm_label(data.get("llm_summary"), health_llm)

            ok = (
                bool(title)
                and title.lower() not in {"add to your order", "robot check", "unknown"}
                and price > 0
                and "become a seller" not in seller.lower()
            )
            tag = "PASS" if ok else "FAIL"
            if ok:
                passed += 1

            mrp_str = f"{float(mrp):,.0f}" if mrp else "-"
            print(
                f"{tag:4s} | {resp.status_code:4d} | {elapsed_ms:6.1f}ms | {price:9,.0f} | {mrp_str:>9s} | "
                f"{str(in_stock):5s} | {verdict:8s} | {llm_mode:8s} | {(seller or '-')[:24]:24s} | {title[:36]}"
            )

    rate = (passed / len(urls)) * 100.0
    print(f"\nSmoke summary: {passed}/{len(urls)} passed ({rate:.1f}%)")
    return 0 if rate >= 80.0 else 1


if __name__ == "__main__":
    sys.exit(main())
