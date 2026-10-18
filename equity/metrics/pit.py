"""Point-in-time access to financial statements and prices.

Every lookup takes an `as_of` date and only returns information whose
(estimated) publication date is on or before it. This is the single place
that enforces "no look-ahead" for fundamentals.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Alternative names Yahoo uses for the same line item (first match wins)
ALIASES: dict[str, list[str]] = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "cogs": ["Cost Of Revenue", "Reconciled Cost Of Revenue"],
    "gross_profit": ["Gross Profit"],
    "operating_income": ["Operating Income", "Total Operating Income As Reported"],
    "ebit": ["EBIT", "Operating Income"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "net_income": ["Net Income Common Stockholders", "Net Income",
                   "Net Income From Continuing Operation Net Minority Interest"],
    "eps": ["Diluted EPS", "Basic EPS"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
    "pretax": ["Pretax Income"],
    "tax": ["Tax Provision"],
    "total_assets": ["Total Assets"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
    "total_debt": ["Total Debt"],
    "long_term_debt": ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "current_assets": ["Current Assets"],
    "current_liabilities": ["Current Liabilities"],
    "receivables": ["Accounts Receivable", "Receivables"],
    "inventory": ["Inventory"],
    "payables": ["Accounts Payable", "Payables"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
    "cfo": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure"],
}


@dataclass
class PITData:
    """Financials + prices for one company, filtered to what was known `as_of`."""
    symbol: str
    financials: pd.DataFrame            # long format from db.read_financials
    prices: pd.DataFrame                # date, close (raw traded price)
    splits: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    as_of: pd.Timestamp | None = None
    info: dict = field(default_factory=dict)

    def __post_init__(self):
        self.as_of = pd.Timestamp(self.as_of) if self.as_of is not None else (
            self.prices["date"].max() if not self.prices.empty else pd.Timestamp.today().normalize())
        f = self.financials
        if not f.empty:
            f = f[(f["freq"] == "annual") & (f["publication_date"] <= self.as_of)]
        self._annual = self._wide(f)
        self._src = f

    @staticmethod
    def _wide(f: pd.DataFrame) -> pd.DataFrame:
        if f.empty:
            return pd.DataFrame()
        w = f.pivot_table(index="period_end", columns="line_item", values="value", aggfunc="last")
        return w.sort_index()

    # ---------------------------------------------------------------- series
    def series(self, key: str, years: int = 5) -> pd.Series:
        """Annual series for a logical item (oldest -> newest), last `years` periods."""
        w = self._annual
        if w.empty:
            return pd.Series(dtype=float)
        for name in ALIASES.get(key, [key]):
            if name in w.columns:
                s = w[name].dropna()
                if not s.empty:
                    s = s.iloc[-years:]
                    s.name = name
                    return s
        return pd.Series(dtype=float)

    def latest(self, key: str) -> float | None:
        s = self.series(key, 1)
        return float(s.iloc[-1]) if not s.empty else None

    def periods(self) -> list[pd.Timestamp]:
        return list(self._annual.index[-5:]) if not self._annual.empty else []

    def source_note(self) -> str:
        if self._src.empty:
            return "No published annual statements available as of this date."
        pe = sorted(self._src["period_end"].dt.strftime("%Y-%m").unique())
        src = self._src["source"].iloc[0]
        ret = self._src["retrieved_at"].max()
        est = bool(self._src["publication_date_estimated"].max())
        return (f"{src}; annual periods {', '.join(pe)}; retrieved {ret}"
                + ("; publication dates estimated (period end + lag)" if est else ""))

    # ---------------------------------------------------------------- market
    def price(self) -> float | None:
        p = self.prices
        if p.empty:
            return None
        p = p[p["date"] <= self.as_of]
        return float(p["close"].iloc[-1]) if not p.empty else None

    def price_date(self):
        p = self.prices[self.prices["date"] <= self.as_of]
        return p["date"].iloc[-1] if not p.empty else None

    def restatement_factor(self) -> float:
        """Yahoo restates ALL historical share counts to the share basis on the day the
        data was downloaded (verified: e.g. RELIANCE FY2023 shows post-2024-bonus count).
        Prices we store are the actually-traded prices. To match a traded price on
        `as_of`, divide restated share counts by the splits/bonuses that happened after
        `as_of`. (This only undoes the source's restatement; it is not used as a signal.)"""
        f = 1.0
        if self.splits is not None and not self.splits.empty:
            for d, r in self.splits.items():
                if d > self.as_of and r > 0:
                    f *= r
        return f

    def shares_now(self) -> float | None:
        """Share count on the as_of basis (latest published balance sheet)."""
        s = self.series("shares", 1)
        if s.empty:
            return None
        return float(s.iloc[-1]) / self.restatement_factor()

    def market_cap(self) -> float | None:
        p, sh = self.price(), self.shares_now()
        if p is None or sh is None or sh <= 0:
            return None
        return p * sh


def safe_div(a, b):
    """Division that returns None for missing values or a zero denominator."""
    try:
        if a is None or b is None:
            return None
        a, b = float(a), float(b)
        if np.isnan(a) or np.isnan(b) or b == 0:
            return None
        return a / b
    except (TypeError, ValueError):
        return None


def cagr(first, last, years) -> float | None:
    """Compound annual growth. Undefined (None) if start or end <= 0 or years <= 0."""
    if first is None or last is None or years is None or years <= 0:
        return None
    if first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0
