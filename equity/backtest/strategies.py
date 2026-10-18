"""Stock-selection strategies. Each is called with a date `d` and must only use
information available on or before `d`."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..metrics.engine import MetricEngine
from ..metrics.pit import PITData
from ..metrics.scoring import score


class MomentumStrategy:
    """PARTIAL (price-only) strategy: top-N by 12-1 month total return.
    Uses Yahoo adjusted close; ratios between two past dates depend only on
    dividends/splits between those dates, so there is no look-ahead."""
    label = "Price momentum 12-1 (partial: price data only)"

    def __init__(self, adj_close: pd.DataFrame, lookback_m=12, skip_m=1, min_days=260):
        self.ac = adj_close.sort_index()
        self.lb, self.skip, self.min_days = lookback_m, skip_m, min_days
        self.last_scores = None

    def __call__(self, d) -> list[str]:
        hist = self.ac.loc[:d]
        if hist.empty:
            return []
        end = hist.index[hist.index <= d - pd.DateOffset(months=self.skip)]
        start = hist.index[hist.index <= d - pd.DateOffset(months=self.lb)]
        if len(end) == 0 or len(start) == 0:
            return []
        e, s = hist.loc[end[-1]], hist.loc[start[-1]]
        valid = hist.notna().sum() >= self.min_days
        alive = hist.iloc[-1].notna()                    # still trading at d
        mom = (e / s - 1)[valid & alive].dropna()
        self.last_scores = mom.sort_values(ascending=False)
        return list(self.last_scores.index)


class LowVolatilityStrategy:
    """PARTIAL (price-only) strategy: lowest 1-year daily volatility."""
    label = "Low volatility 1y (partial: price data only)"

    def __init__(self, adj_close: pd.DataFrame, min_days=260):
        self.ac, self.min_days = adj_close.sort_index(), min_days

    def __call__(self, d):
        hist = self.ac.loc[:d].iloc[-252:]
        valid = (self.ac.loc[:d].notna().sum() >= self.min_days) & hist.iloc[-1].notna()
        vol = hist.pct_change(fill_method=None).std()[valid].dropna()
        return list(vol.sort_values().index)


class FundamentalStrategy:
    """Ranks by the conservative 27-metric score computed POINT-IN-TIME.
    Only statements whose estimated publication date <= d are visible.
    Qualitative and shareholding metrics are Insufficient (score 0) unless
    supplied with dated evidence, so this is a partial fundamental test."""
    label = "27-metric score, point-in-time (partial: quantitative metrics only)"

    def __init__(self, fin: dict, prices: dict, splits: dict, info: dict, cfg: dict,
                 min_coverage: float, shareholding: dict | None = None, min_names: int = 1):
        self.fin, self.prices, self.splits, self.info = fin, prices, splits, info
        self.cfg, self.min_cov, self.sh = cfg, min_coverage, shareholding or {}
        self.min_names = min_names
        self.prev: list[str] = []
        self.log: list[dict] = []

    def evaluate(self, d):
        rows = []
        for s, f in self.fin.items():
            p = self.prices.get(s)
            if p is None or p.empty or p[p["date"] <= d].empty:
                continue
            if p[p["date"] <= d]["date"].max() < d - pd.Timedelta(days=10):
                continue                      # not trading at d
            pit = PITData(s, f, p, self.splits.get(s, pd.Series(dtype=float)), d, self.info.get(s, {}))
            # Snapshot-only fields (forward P/E, insiders) are not point-in-time: hide them
            pit.info = {k: v for k, v in pit.info.items() if k in ("sector", "industry", "longName")}
            eng = MetricEngine(pit, self.cfg, qualitative={}, shareholding=self.sh.get(s))
            sc = score(eng.run(), self.cfg)
            rows.append({"date": d, "symbol": s, **sc})
        return pd.DataFrame(rows)

    def __call__(self, d):
        t = self.evaluate(d)
        if t.empty:
            return []
        t = t[t["coverage"] >= self.min_cov].sort_values(["conservative_score", "coverage"], ascending=False)
        self.log.extend(t.to_dict("records"))
        if len(t) < self.min_names:
            # Too few companies can be scored yet: don't build a concentrated portfolio
            # from 1-2 names; keep previous holdings (or stay in cash at the start).
            return list(self.prev)
        self.prev = list(t["symbol"])
        return self.prev
