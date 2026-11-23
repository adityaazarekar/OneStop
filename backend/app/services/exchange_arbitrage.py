"""
Exchange/trade-in arbitrage comparator.

Compares marketplace exchange value (with doorstep haircut risk)
vs external buyback sites (Cashify, InstaCash, etc.).
Pure deterministic math — no LLM.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExchangeOption:
    label: str
    gross_value: float
    haircut_pct: float
    net_value: float
    effective_upgrade_cost: float


@dataclass
class ExchangeComparison:
    marketplace_option: ExchangeOption
    external_option: ExchangeOption
    recommendation: str  # "marketplace" | "external" | "skip_exchange"
    reasoning: str


# Doorstep pickup haircut: marketplace exchange quotes are
# reduced by 15-25% at doorstep inspection on average.
DEFAULT_MARKETPLACE_HAIRCUT_PCT = 20.0
DEFAULT_EXTERNAL_HAIRCUT_PCT = 5.0


def compare_exchange_options(
    new_device_price: float,
    marketplace_exchange_quote: float,
    external_buyback_quote: float,
    marketplace_haircut_pct: float = DEFAULT_MARKETPLACE_HAIRCUT_PCT,
    external_haircut_pct: float = DEFAULT_EXTERNAL_HAIRCUT_PCT,
) -> ExchangeComparison:
    """
    Compare marketplace exchange vs selling externally + buying new.

    marketplace_exchange_quote: "Up to ₹X off" shown on Amazon/Flipkart.
    external_buyback_quote:     Quote from Cashify/InstaCash/OLX average.
    """
    mp_net = round(
        marketplace_exchange_quote * (1 - marketplace_haircut_pct / 100.0), 2
    )
    mp_upgrade = round(new_device_price - mp_net, 2)

    ext_net = round(
        external_buyback_quote * (1 - external_haircut_pct / 100.0), 2
    )
    ext_upgrade = round(new_device_price - ext_net, 2)

    mp_opt = ExchangeOption(
        label="Marketplace Exchange",
        gross_value=marketplace_exchange_quote,
        haircut_pct=marketplace_haircut_pct,
        net_value=mp_net,
        effective_upgrade_cost=mp_upgrade,
    )
    ext_opt = ExchangeOption(
        label="External Buyback",
        gross_value=external_buyback_quote,
        haircut_pct=external_haircut_pct,
        net_value=ext_net,
        effective_upgrade_cost=ext_upgrade,
    )

    # Decision
    savings = mp_upgrade - ext_upgrade
    if abs(savings) < 500:
        rec = "marketplace"
        reason = (
            f"Both options within ₹500. Marketplace exchange is more convenient "
            f"(₹{mp_upgrade:,.0f} vs ₹{ext_upgrade:,.0f} upgrade cost)."
        )
    elif ext_upgrade < mp_upgrade:
        rec = "external"
        reason = (
            f"Sell externally for ₹{ext_net:,.0f} (after {external_haircut_pct}% haircut) "
            f"and buy new: ₹{ext_upgrade:,.0f} upgrade cost, saving ₹{savings:,.0f} "
            f"vs marketplace exchange."
        )
    else:
        rec = "marketplace"
        reason = (
            f"Use marketplace exchange: ₹{mp_upgrade:,.0f} upgrade cost "
            f"(₹{abs(savings):,.0f} cheaper than selling externally)."
        )

    # Edge case: both quotes are ₹0 or negative
    if marketplace_exchange_quote <= 0 and external_buyback_quote <= 0:
        rec = "skip_exchange"
        reason = "No exchange/buyback quotes available. Buy new outright."

    return ExchangeComparison(
        marketplace_option=mp_opt,
        external_option=ext_opt,
        recommendation=rec,
        reasoning=reason,
    )
