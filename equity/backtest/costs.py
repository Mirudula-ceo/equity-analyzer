"""Indian cash-equity (delivery) transaction costs.

Defaults in settings.toml reflect typical discount-broker delivery charges
(rates change over time; current rates are applied to the whole history,
which is a stated simplification).
"""
from __future__ import annotations


def charges(side: str, value: float, c: dict) -> dict:
    """Statutory + broker charges for one order of `value` rupees.
    side: 'buy' or 'sell'. Slippage is handled separately via execution price."""
    if value <= 0:
        return {k: 0.0 for k in ("brokerage", "stt", "exchange", "sebi", "stamp", "gst", "dp", "total")}
    brokerage = min(value * c["brokerage_pct"] / 100, c["brokerage_max_per_order"])
    stt = value * c["stt_pct"] / 100
    exchange = value * c["exchange_txn_pct"] / 100
    sebi = value * c["sebi_fee_per_crore"] / 1e7
    stamp = value * c["stamp_duty_buy_pct"] / 100 if side == "buy" else 0.0
    gst = (brokerage + exchange + sebi) * c["gst_pct"] / 100
    dp = c["dp_charge_per_sell"] if side == "sell" else 0.0
    total = brokerage + stt + exchange + sebi + stamp + gst + dp
    return {"brokerage": brokerage, "stt": stt, "exchange": exchange, "sebi": sebi,
            "stamp": stamp, "gst": gst, "dp": dp, "total": total}


def exec_price(side: str, price: float, c: dict) -> float:
    """Execution price after slippage (buy higher, sell lower)."""
    s = c["slippage_bps"] / 10_000
    return price * (1 + s) if side == "buy" else price * (1 - s)


def max_affordable_shares(cash: float, price: float, c: dict) -> int:
    """Largest whole number of shares whose cost incl. slippage and charges fits in cash."""
    if price <= 0 or cash <= 0:
        return 0
    p = exec_price("buy", price, c)
    n = int(cash // p)
    while n > 0 and n * p + charges("buy", n * p, c)["total"] > cash + 1e-9:
        n -= 1
    return n
