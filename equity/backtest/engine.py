"""Event-driven daily portfolio backtester.

Timeline for every rebalance:
  1. Signal is computed after the close of the rebalance date `d` using only
     information available on or before `d` (strategy receives `d`).
  2. Orders execute at the OPEN of the next trading day (no same-day look-ahead).
  3. Sells execute before buys; buys are sized so cash never goes negative
     after slippage and all charges.

Corporate actions (applied at the start of each ex-date):
  * Splits/bonus: shares x ratio, average cost / ratio; fractional
    entitlement paid out as cash at the previous close.
  * Dividends: cash += shares x dividend per share (credited on ex-date;
    actual payment comes ~30 days later and dividend tax is ignored).

Prices must be the actual traded (split-unadjusted) prices.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .costs import charges, exec_price, max_affordable_shares


@dataclass
class BacktestParams:
    initial_capital: float = 500_000.0
    start: str | pd.Timestamp = "2016-01-01"
    end: str | pd.Timestamp | None = None
    rebalance: str = "Q"                 # M, Q, A
    max_stocks: int = 10
    tolerance: float = 0.02              # skip trims/top-ups smaller than 2% of portfolio
    costs: dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    equity: pd.DataFrame                 # date, value, cash, invested
    transactions: pd.DataFrame
    holdings: pd.DataFrame               # snapshot after each rebalance
    dividends: pd.DataFrame
    corporate_actions: pd.DataFrame
    warnings: list
    final_positions: pd.DataFrame
    params: BacktestParams


def rebalance_dates(calendar: pd.DatetimeIndex, freq: str) -> list[pd.Timestamp]:
    """Last trading day of each period in the calendar."""
    s = pd.Series(calendar, index=calendar)
    key = {"M": calendar.to_period("M"), "Q": calendar.to_period("Q"), "A": calendar.to_period("Y")}[freq]
    return [pd.Timestamp(x) for x in s.groupby(key).max().tolist()]


class Backtester:
    def __init__(self, prices: dict[str, pd.DataFrame], splits: pd.DataFrame | None,
                 dividends: pd.DataFrame | None, strategy: Callable[[pd.Timestamp], list[str]],
                 params: BacktestParams, calendar: pd.DatetimeIndex | None = None):
        """prices: symbol -> DataFrame(index=date, columns open, close)
        splits: columns symbol, date, ratio  | dividends: columns symbol, date, amount"""
        self.px = {s: df.sort_index() for s, df in prices.items() if df is not None and not df.empty}
        self.splits = splits if splits is not None else pd.DataFrame(columns=["symbol", "date", "ratio"])
        self.divs = dividends if dividends is not None else pd.DataFrame(columns=["symbol", "date", "amount"])
        self.strategy = strategy
        self.p = params
        if calendar is None:
            calendar = pd.DatetimeIndex(sorted(set().union(*[df.index for df in self.px.values()])))
        start = pd.Timestamp(params.start)
        end = pd.Timestamp(params.end) if params.end else calendar.max()
        self.cal = calendar[(calendar >= start) & (calendar <= end)]
        self.close = pd.DataFrame({s: df["close"] for s, df in self.px.items()}).reindex(self.cal).ffill()
        self.open = pd.DataFrame({s: df["open"] for s, df in self.px.items()}).reindex(self.cal)
        self.last_px_date = {s: df["close"].dropna().index.max() for s, df in self.px.items()}

    # ------------------------------------------------------------------
    def run(self) -> BacktestResult:
        c = self.p.costs
        cash = float(self.p.initial_capital)
        shares: dict[str, float] = {}
        avg_cost: dict[str, float] = {}      # per share, incl. buy charges & slippage
        tx, eq, hold, divlog, calog, warns = [], [], [], [], [], []
        rdates = set(rebalance_dates(self.cal, self.p.rebalance))
        split_map = {(r.symbol, pd.Timestamp(r.date)): float(r.ratio) for r in self.splits.itertuples()}
        div_map: dict = {}
        for r in self.divs.itertuples():
            div_map[(r.symbol, pd.Timestamp(r.date))] = div_map.get((r.symbol, pd.Timestamp(r.date)), 0) + float(r.amount)
        pending: list[str] | None = None
        signal_date = None
        prev_close = None

        for i, d in enumerate(self.cal):
            # 1) corporate actions on ex-date
            for s in list(shares):
                ratio = split_map.get((s, d))
                if ratio and ratio > 0:
                    new = shares[s] * ratio
                    whole = math.floor(new + 1e-9)
                    frac = new - whole
                    pc = prev_close[s] / ratio if prev_close is not None and not pd.isna(prev_close.get(s)) else 0.0
                    if frac > 1e-9:
                        cash += frac * pc
                    calog.append({"date": d, "symbol": s, "type": "split/bonus", "ratio": ratio,
                                  "shares_before": shares[s], "shares_after": whole, "cash_in_lieu": frac * pc})
                    avg_cost[s] = avg_cost[s] * shares[s] / whole if whole else 0.0
                    shares[s] = whole
                    if whole == 0:
                        del shares[s]
                amt = div_map.get((s, d))
                if amt and s in shares:
                    cash += shares[s] * amt
                    divlog.append({"date": d, "symbol": s, "per_share": amt, "shares": shares[s],
                                   "amount": shares[s] * amt})

            # 2) execute pending orders at today's open
            if pending is not None:
                cash = self._execute(d, signal_date, pending, shares, avg_cost, cash, tx, warns)
                pv = cash + sum(n * self._last_close(s, d) for s, n in shares.items())
                for s, n in shares.items():
                    v = n * self._last_close(s, d)
                    hold.append({"rebalance_date": signal_date, "trade_date": d, "symbol": s, "shares": n,
                                 "price": self._last_close(s, d), "value": v, "weight": v / pv if pv else 0,
                                 "avg_cost": avg_cost[s]})
                hold.append({"rebalance_date": signal_date, "trade_date": d, "symbol": "CASH", "shares": None,
                             "price": None, "value": cash, "weight": cash / pv if pv else 0, "avg_cost": None})
                pending = None

            # 3) mark to market at close
            closes = self.close.loc[d]
            invested = sum(n * closes[s] for s, n in shares.items() if not pd.isna(closes[s]))
            for s in shares:
                if self.last_px_date[s] < d and not any(w.startswith(f"{s}: no prices") for w in warns):
                    warns.append(f"{s}: no prices after {self.last_px_date[s].date()} while held "
                                 "(suspended/delisted?) - valued at last close.")
            if cash < -1e-6:
                raise AssertionError(f"Cash went negative on {d}: {cash}")
            eq.append({"date": d, "value": cash + invested, "cash": cash, "invested": invested})
            prev_close = closes

            # 4) generate signal after close on rebalance dates (except last day)
            if d in rdates and i < len(self.cal) - 1:
                picks = list(self.strategy(d))[: self.p.max_stocks]
                pending, signal_date = picks, d

        last = self.cal[-1]
        fp = pd.DataFrame([{"symbol": s, "shares": n, "avg_cost": avg_cost[s], "last_price": self._last_close(s, last),
                            "market_value": n * self._last_close(s, last),
                            "unrealized_pnl": n * (self._last_close(s, last) - avg_cost[s])}
                           for s, n in shares.items()])
        return BacktestResult(pd.DataFrame(eq).set_index("date"), pd.DataFrame(tx), pd.DataFrame(hold),
                              pd.DataFrame(divlog), pd.DataFrame(calog), warns, fp, self.p)

    # ------------------------------------------------------------------
    def _last_close(self, s, d):
        v = self.close.at[d, s] if s in self.close.columns else np.nan
        return 0.0 if pd.isna(v) else float(v)

    def _execute(self, d, signal_date, picks, shares, avg_cost, cash, tx, warns):
        c = self.p.costs
        opens = self.open.loc[d]
        tradable = lambda s: s in opens.index and not pd.isna(opens[s]) and opens[s] > 0  # noqa: E731
        picks = [s for s in picks if s in self.px]
        # value portfolio at today's open (fallback last close for untradable)
        def px(s):
            return float(opens[s]) if tradable(s) else self._last_close(s, d)
        pv = cash + sum(n * px(s) for s, n in shares.items())
        n_target = len(picks)
        target_val = pv / n_target if n_target else 0.0
        tol = self.p.tolerance * pv

        def record(s, side, n, price):
            p_exec = exec_price(side, price, c)
            gross = n * p_exec
            ch = charges(side, gross, c)
            slip = abs(p_exec - price) * n
            row = {"signal_date": signal_date, "date": d, "symbol": s, "side": side, "shares": n,
                   "open_price": price, "exec_price": p_exec, "gross_value": gross, "slippage": slip,
                   **{f"cost_{k}": v for k, v in ch.items() if k != "total"}, "charges": ch["total"],
                   "realized_pnl": None}
            return row, gross, ch["total"]

        # sells: exits and trims
        for s in sorted(shares):
            want = 0.0 if s not in picks else target_val
            cur_val = shares[s] * px(s)
            if s in picks and cur_val - want <= tol:
                continue
            if not tradable(s):
                warns.append(f"{d.date()}: could not sell {s} (no open price); kept.")
                continue
            n = shares[s] if s not in picks else math.floor((cur_val - want) / px(s))
            if n <= 0:
                continue
            row, gross, ch = record(s, "sell", n, float(opens[s]))
            proceeds = gross - ch
            row["realized_pnl"] = proceeds - n * avg_cost[s]
            row["cost_basis"] = n * avg_cost[s]
            row["exit_type"] = "full" if n == shares[s] else "trim"
            cash += proceeds
            shares[s] -= n
            if shares[s] <= 0:
                del shares[s]
                del avg_cost[s]
            tx.append(row)

        # buys: new positions and top-ups (largest shortfall first)
        need = []
        for s in picks:
            cur_val = shares.get(s, 0) * px(s)
            if target_val - cur_val > tol or (s not in shares):
                need.append((target_val - cur_val, s))
        for _, s in sorted(need, reverse=True):
            if not tradable(s):
                warns.append(f"{d.date()}: could not buy {s} (no open price).")
                continue
            price = float(opens[s])
            cur_val = shares.get(s, 0) * price
            budget = min(target_val - cur_val, cash)
            n = max_affordable_shares(budget, price, c)
            if n <= 0:
                continue
            row, gross, ch = record(s, "buy", n, price)
            total = gross + ch
            if total > cash + 1e-9:          # safety; should not happen
                continue
            old_n = shares.get(s, 0)
            avg_cost[s] = (avg_cost.get(s, 0) * old_n + total) / (old_n + n)
            shares[s] = old_n + n
            cash -= total
            tx.append(row)
        return cash
