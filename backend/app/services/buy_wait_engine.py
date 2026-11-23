"""
Buy-or-Wait engine with Indian sale calendar heuristic.

Cold-start safe: returns INSUFFICIENT_HISTORY when < 3 observations exist.
Never invents fake historical lows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class BuyWaitVerdict:
    verdict: str  # BUY_NOW | WAIT | OVERPRICED | FAIR_PRICE | INSUFFICIENT_HISTORY
    current_price: float
    p10: float | None  # 10th percentile of 90-day prices
    median_90d: float | None
    all_time_low: float | None
    observation_count: int
    next_sale_name: str | None = None
    days_to_next_sale: int | None = None
    reasoning: str = ""


# Indian sale calendar (month, day_start, day_end, name)
SALE_CALENDAR: list[tuple[int, int, int, str]] = [
    (1, 15, 20, "Republic Day Sale"),
    (3, 20, 25, "Holi Sale"),
    (7, 15, 25, "Amazon Prime Day / Flipkart Sale"),
    (8, 1, 15, "Independence Day Sale"),
    (9, 27, 30, "Big Billion Days / Great Indian Festival"),
    (10, 1, 10, "Big Billion Days / Great Indian Festival"),
    (11, 20, 30, "Black Friday / Pre-Diwali Sale"),
]


def _days_to_next_sale(today: date) -> tuple[str, int]:
    """Return (sale_name, days_until) for the nearest upcoming sale."""
    best_name = ""
    best_days = 999
    year = today.year

    for y_offset in range(2):
        yr = year + y_offset
        for month, d_start, d_end, name in SALE_CALENDAR:
            try:
                sale_start = date(yr, month, d_start)
            except ValueError:
                continue
            delta = (sale_start - today).days
            if delta < 0:
                continue
            if delta < best_days:
                best_days = delta
                best_name = name

    return best_name, best_days


async def get_buy_wait_verdict(
    session: AsyncSession,
    listing_id: str,
    current_price: float,
) -> BuyWaitVerdict:
    """Compute a deterministic buy/wait verdict."""
    today = date.today()

    # Fetch 90-day stats from TimescaleDB
    row = await session.execute(
        text("""
            SELECT
                count(*) AS cnt,
                percentile_cont(0.10) WITHIN GROUP (ORDER BY listed_price) AS p10,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY listed_price) AS median_price,
                min(listed_price) AS all_time_low
            FROM price_observations
            WHERE listing_id = :lid
              AND observed_at >= now() - interval '90 days'
        """),
        {"lid": listing_id},
    )
    stats = row.mappings().first()
    cnt = int(stats["cnt"]) if stats and stats["cnt"] else 0
    p10 = float(stats["p10"]) if stats and stats["p10"] else None
    median_90d = float(stats["median_price"]) if stats and stats["median_price"] else None
    atl = float(stats["all_time_low"]) if stats and stats["all_time_low"] else None

    sale_name, days_to_sale = _days_to_next_sale(today)

    # ── Cold start ──
    if cnt < 3:
        verdict = "INSUFFICIENT_HISTORY"
        reasoning = "Price tracking just started. "
        if days_to_sale <= 21:
            reasoning += f"However, {sale_name} is in {days_to_sale} days — consider waiting."
            verdict = "WAIT"
        else:
            reasoning += "Not enough data to judge pricing."
        return BuyWaitVerdict(
            verdict=verdict,
            current_price=current_price,
            p10=None,
            median_90d=None,
            all_time_low=None,
            observation_count=cnt,
            next_sale_name=sale_name if days_to_sale <= 60 else None,
            days_to_next_sale=days_to_sale if days_to_sale <= 60 else None,
            reasoning=reasoning,
        )

    # ── Full analysis ──
    ratio = current_price / p10 if p10 and p10 > 0 else 1.0

    if ratio <= 1.05:
        verdict = "BUY_NOW"
        reasoning = (
            f"Current price ₹{current_price:,.0f} is within 5% of the 90-day best "
            f"(P10 = ₹{p10:,.0f}). This is an excellent deal."
        )
    elif days_to_sale <= 21:
        verdict = "WAIT"
        reasoning = (
            f"{sale_name} is {days_to_sale} days away. "
            f"Current ₹{current_price:,.0f} vs P10 ₹{p10:,.0f} — likely to drop during the sale."
        )
    elif ratio > 1.20:
        verdict = "OVERPRICED"
        reasoning = (
            f"Current ₹{current_price:,.0f} is {((ratio - 1) * 100):.0f}% above the 90-day best "
            f"(P10 = ₹{p10:,.0f}). Price has been significantly lower recently."
        )
    else:
        verdict = "FAIR_PRICE"
        reasoning = (
            f"Current ₹{current_price:,.0f} is fair — {((ratio - 1) * 100):.0f}% above P10 ₹{p10:,.0f}. "
            f"No major sale imminent ({sale_name} in {days_to_sale}d)."
        )

    return BuyWaitVerdict(
        verdict=verdict,
        current_price=current_price,
        p10=p10,
        median_90d=median_90d,
        all_time_low=atl,
        observation_count=cnt,
        next_sale_name=sale_name if days_to_sale <= 60 else None,
        days_to_next_sale=days_to_sale if days_to_sale <= 60 else None,
        reasoning=reasoning,
    )
