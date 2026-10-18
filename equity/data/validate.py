"""Data-quality checks run after download and shown on the Data Quality page."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..metrics.pit import ALIASES

KEY_ITEMS = ["revenue", "operating_income", "net_income", "total_assets", "equity", "total_debt",
             "cash", "current_liabilities", "cfo", "capex", "shares", "interest_expense", "ebitda"]


def check_publication_dates(fin: pd.DataFrame, today=None) -> pd.DataFrame:
    """Flag rows whose publication date is impossible (before the period ended) or in the future."""
    if fin.empty:
        return pd.DataFrame(columns=["symbol", "period_end", "publication_date", "problem"])
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    f = fin.drop_duplicates(["symbol", "freq", "period_end", "publication_date"])
    bad = []
    for r in f.itertuples():
        if pd.isna(r.publication_date):
            bad.append((r.symbol, r.period_end, r.publication_date, "missing publication date"))
        elif r.publication_date < r.period_end:
            bad.append((r.symbol, r.period_end, r.publication_date, "published before period end (impossible)"))
        elif r.period_end > today:
            bad.append((r.symbol, r.period_end, r.publication_date, "period end in the future"))
    return pd.DataFrame(bad, columns=["symbol", "period_end", "publication_date", "problem"])


def price_jumps(prices: pd.DataFrame, splits: pd.DataFrame, threshold=0.35) -> pd.DataFrame:
    """Daily moves larger than threshold not explained by a split (possible bad data
    or a corporate action missing from the source)."""
    out = []
    split_keys = set()
    if splits is not None and not splits.empty:
        split_keys = {(r.symbol, pd.Timestamp(r.date)) for r in splits.itertuples()}
    for s, g in prices.groupby("symbol"):
        g = g.sort_values("date")
        r = g["close"].pct_change()
        for d, v in zip(g["date"], r):
            if pd.notna(v) and abs(v) > threshold and (s, pd.Timestamp(d)) not in split_keys:
                out.append((s, d, v))
    return pd.DataFrame(out, columns=["symbol", "date", "daily_change"])


def coverage(fin: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    rows = []
    for s in symbols:
        f = fin[(fin["symbol"] == s) & (fin["freq"] == "annual")] if not fin.empty else fin
        row = {"symbol": s, "annual_years": f["period_end"].nunique() if not f.empty else 0,
               "first_period": f["period_end"].min().date() if not f.empty else None,
               "last_period": f["period_end"].max().date() if not f.empty else None}
        items = set(f["line_item"]) if not f.empty else set()
        for k in KEY_ITEMS:
            row[k] = any(a in items for a in ALIASES[k])
        rows.append(row)
    return pd.DataFrame(rows)


def repair_price_spikes(px: pd.DataFrame, max_jump=0.40, revert_tol=0.15, max_len=5,
                        split_dates=()) -> tuple[pd.DataFrame, list]:
    """Fix obvious bad ticks: a jump > max_jump that reverts within `max_len` days to
    within `revert_tol` of the pre-jump price (e.g. Yahoo's NIFTYBEES 2019-12-19 quote
    of 13.02 instead of ~130). Bad rows are replaced by the previous good row.
    px: DataFrame indexed by date with at least 'close'. Returns (fixed, list_of_dates)."""
    px = px.sort_index().copy()
    c = px["close"].to_numpy(dtype=float)
    bad = []
    split_dates = {pd.Timestamp(d) for d in split_dates}
    i = 1
    while i < len(c):
        prev = c[i - 1]
        if prev > 0 and abs(c[i] / prev - 1) > max_jump and px.index[i] not in split_dates:
            for j in range(i + 1, min(i + 1 + max_len, len(c))):
                if abs(c[j] / prev - 1) <= revert_tol:
                    bad.extend(px.index[i:j])
                    cols = [k for k in ("open", "high", "low", "close", "adj_close") if k in px]
                    px.iloc[i:j, [px.columns.get_loc(k) for k in cols]] = np.nan
                    i = j
                    break
        i += 1
    if bad:
        px = px.ffill()
    return px, bad
