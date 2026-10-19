"""Data collection from free public sources.

Sources used
------------
* Yahoo Finance via the `yfinance` library (unofficial; for personal research use):
  daily prices, dividends, splits/bonuses, ~4 years of annual and ~5 quarters
  of financial statements, and a current snapshot of valuation fields.
* niftyindices.com public CSV of *current* index constituents.
* Optional: data/manual/shareholding.csv for promoter holding / pledge (see docs).

What we deliberately do NOT do: scrape pages behind login, CAPTCHA or
anti-bot protection (e.g. NSE's JSON APIs, screener.in exports). Anything we
cannot obtain lawfully is recorded as unavailable.
"""
from __future__ import annotations

import io
import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ..config import CONFIG_DIR, PROJECT_ROOT, load_settings
from ..db import connect, log_fetch, now_utc, upsert_df

YAHOO = "Yahoo Finance via yfinance"
UA = {"User-Agent": "Mozilla/5.0 (personal research script; EquityAnalyzer)"}


def yahoo_symbol(symbol: str) -> str:
    """NSE symbol -> Yahoo ticker. Index/ETF tickers pass through."""
    if symbol.startswith("^") or symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    return f"{symbol}.NS"


# ------------------------------------------------------------------ universe
def fetch_universe(con, index_name: str = "NIFTY50", cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_settings()
    url = cfg["universe"]["nifty50_url" if index_name == "NIFTY50" else "nifty500_url"]
    df, source, as_of = None, None, None
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if "Symbol" not in df.columns:
            raise ValueError("unexpected CSV format")
        source, as_of = f"niftyindices.com ({url})", date.today().isoformat()
        log_fetch(con, index_name, "universe", "ok", f"{len(df)} constituents", source)
    except Exception as exc:  # noqa: BLE001
        if index_name != "NIFTY50":
            log_fetch(con, index_name, "universe", "error", str(exc), url)
            raise RuntimeError(f"Could not download {index_name} list: {exc}") from exc
        df = pd.read_csv(CONFIG_DIR / "nifty50_fallback.csv")
        source = "config/nifty50_fallback.csv (copy of niftyindices.com list downloaded 2026-10-16 - may be outdated)"
        as_of = "2026-10-16"
        log_fetch(con, index_name, "universe", "fallback", f"download failed: {exc}", source)
    out = pd.DataFrame({
        "index_name": index_name,
        "symbol": df["Symbol"].str.strip(),
        "company": df.get("Company Name", df["Symbol"]),
        "industry": df.get("Industry", ""),
        "source": source, "as_of": as_of, "retrieved_at": now_utc(),
    })
    con.execute("DELETE FROM universe WHERE index_name=?", (index_name,))
    upsert_df(con, "universe", out)
    return out


# ------------------------------------------------------------------ prices
def unadjust_for_splits(px: pd.DataFrame, splits: pd.Series) -> pd.DataFrame:
    """Yahoo's OHLC and dividends are already split-adjusted. Convert back to the
    prices actually traded on each day so the backtest can hold whole shares
    and apply the split on its ex-date (realistic corporate-action handling).

    raw(t) = adjusted(t) * product of split ratios with ex-date > t
    """
    px = px.copy()
    if splits is None or splits.empty:
        return px
    factor = pd.Series(1.0, index=px.index)
    for d, ratio in splits.items():
        if ratio and ratio > 0:
            factor[px.index < d] *= ratio
    for c in ("open", "high", "low", "close", "dividend"):
        if c in px:
            px[c] = px[c] * factor
    if "volume" in px:
        px["volume"] = px["volume"] / factor
    return px


def fetch_prices(con, symbol: str, years: int = 11, pause: float = 1.0) -> int:
    import yfinance as yf

    tk = yahoo_symbol(symbol)
    for attempt in range(3):
        try:
            start = (date.today() - timedelta(days=int(years * 365.25))).isoformat()
            hist = yf.Ticker(tk).history(start=start, auto_adjust=False,
                                         actions=True, raise_errors=True)
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                log_fetch(con, symbol, "prices", "error", str(exc), YAHOO)
                return 0
            time.sleep(pause * (attempt + 2) * 3)
    if hist is None or hist.empty:
        log_fetch(con, symbol, "prices", "empty", "no rows returned", YAHOO)
        return 0
    hist.index = pd.to_datetime(hist.index).tz_localize(None).normalize()
    hist = hist[~hist.index.duplicated(keep="last")]
    splits = hist.get("Stock Splits", pd.Series(dtype=float))
    splits = splits[splits > 0]
    px = pd.DataFrame({
        "open": hist["Open"], "high": hist["High"], "low": hist["Low"],
        "close": hist["Close"], "adj_close": hist["Adj Close"],
        "volume": hist["Volume"], "dividend": hist.get("Dividends", 0.0),
    })
    raw = unadjust_for_splits(px, splits)
    ts = now_utc()
    prices = pd.DataFrame({
        "symbol": symbol, "date": raw.index.strftime("%Y-%m-%d"),
        "open": raw["open"].values, "high": raw["high"].values, "low": raw["low"].values,
        "close": raw["close"].values, "adj_close": px["adj_close"].values,
        "volume": raw["volume"].values,
        "source": YAHOO + " (OHLC un-adjusted for splits; adj_close = Yahoo total-return adjusted)",
        "retrieved_at": ts,
    })
    con.execute("DELETE FROM prices WHERE symbol=?", (symbol,))
    n = upsert_df(con, "prices", prices)
    acts = []
    for d, v in raw["dividend"][raw["dividend"] > 0].items():
        acts.append((symbol, d.strftime("%Y-%m-%d"), "dividend", float(v), YAHOO, ts))
    for d, v in splits.items():
        acts.append((symbol, d.strftime("%Y-%m-%d"), "split", float(v),
                     YAHOO + " (bonus issues appear as splits)", ts))
    con.execute("DELETE FROM corporate_actions WHERE symbol=?", (symbol,))
    if acts:
        upsert_df(con, "corporate_actions", pd.DataFrame(
            acts, columns=["symbol", "date", "action_type", "value", "source", "retrieved_at"]))
    log_fetch(con, symbol, "prices", "ok",
              f"{n} days {prices['date'].iloc[0]}..{prices['date'].iloc[-1]}, "
              f"{len(splits)} splits, {int((raw['dividend'] > 0).sum())} dividends", YAHOO)
    return n


# ------------------------------------------------------------------ fundamentals
STATEMENTS = {
    ("income", "annual"): "income_stmt",
    ("balance", "annual"): "balance_sheet",
    ("cashflow", "annual"): "cashflow",
    ("income", "quarterly"): "quarterly_income_stmt",
    ("balance", "quarterly"): "quarterly_balance_sheet",
    ("cashflow", "quarterly"): "quarterly_cashflow",
}

INFO_KEYS = ["longName", "shortName", "sector", "industry", "currency", "financialCurrency",
             "marketCap", "enterpriseValue", "sharesOutstanding", "currentPrice",
             "previousClose", "trailingPE", "forwardPE", "priceToBook", "enterpriseToEbitda",
             "trailingEps", "forwardEps", "bookValue", "heldPercentInsiders",
             "heldPercentInstitutions", "totalDebt", "totalCash", "ebitda",
             "dividendYield", "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
             # company profile - shown on the research page, never scored
             "website", "irWebsite", "longBusinessSummary", "fullTimeEmployees", "companyOfficers",
             "address1", "city"]


def statements_to_long(df: pd.DataFrame, symbol: str, statement: str, freq: str,
                       currency: str, lag_days: int, retrieved_at: str) -> pd.DataFrame:
    """yfinance wide frame (rows=line items, cols=period ends) -> long rows with provenance."""
    if df is None or df.empty:
        return pd.DataFrame()
    rows = []
    for col in df.columns:
        pe = pd.Timestamp(col)
        for item, v in df[col].items():
            if v is not None and not pd.isna(v):
                rows.append({"line_item": str(item), "period_end": pe, "value": float(v)})
    if not rows:
        return pd.DataFrame()
    long = pd.DataFrame(rows)
    long["publication_date"] = (long["period_end"] + pd.Timedelta(days=lag_days)).dt.strftime("%Y-%m-%d")
    long["period_end"] = long["period_end"].dt.strftime("%Y-%m-%d")
    long["symbol"], long["statement"], long["freq"] = symbol, statement, freq
    long["currency"], long["source"] = currency, YAHOO
    long["publication_date_estimated"] = 1
    long["retrieved_at"] = retrieved_at
    long["value"] = long["value"].astype(float)
    return long[["symbol", "statement", "freq", "period_end", "line_item", "value", "currency",
                 "source", "publication_date", "publication_date_estimated", "retrieved_at"]]


def _profile_clean(keep: dict) -> dict:
    offs = keep.get("companyOfficers") or []
    keep["companyOfficers"] = [{"name": o.get("name"), "title": o.get("title")} for o in offs[:8]
                               if isinstance(o, dict)]
    if keep.get("longBusinessSummary"):
        keep["longBusinessSummary"] = str(keep["longBusinessSummary"])[:1500]
    return keep


def fetch_fundamentals(con, symbol: str, cfg: dict | None = None, quarterly: bool = True) -> dict:
    import yfinance as yf

    cfg = cfg or load_settings()
    t = yf.Ticker(yahoo_symbol(symbol))
    ts = now_utc()
    report = {}
    info = {}
    try:
        info = t.info or {}
    except Exception as exc:  # noqa: BLE001
        log_fetch(con, symbol, "info", "error", str(exc), YAHOO)
    keep = _profile_clean({k: info.get(k) for k in INFO_KEYS})
    currency = info.get("financialCurrency") or "INR"
    con.execute("INSERT OR REPLACE INTO company_info VALUES (?,?,?,?,?,?,?,?)",
                (symbol, keep.get("longName") or keep.get("shortName"), keep.get("sector"),
                 keep.get("industry"), currency, json.dumps(keep, default=str), YAHOO, ts))
    con.commit()
    for (stmt, freq), attr in STATEMENTS.items():
        if freq == "quarterly" and not quarterly:
            continue
        lag = cfg["data"][f"{freq}_publication_lag_days"]
        try:
            raw = getattr(t, attr)
            long = statements_to_long(raw, symbol, stmt, freq, currency, lag, ts)
            con.execute("DELETE FROM financials WHERE symbol=? AND statement=? AND freq=?",
                        (symbol, stmt, freq))
            n = upsert_df(con, "financials", long)
            periods = sorted(long["period_end"].unique()) if n else []
            report[f"{stmt}_{freq}"] = len(periods)
            log_fetch(con, symbol, f"{stmt}_{freq}", "ok" if n else "empty",
                      f"{len(periods)} periods: {', '.join(periods)}", YAHOO)
        except Exception as exc:  # noqa: BLE001
            report[f"{stmt}_{freq}"] = 0
            log_fetch(con, symbol, f"{stmt}_{freq}", "error", str(exc), YAHOO)
    return report


# ------------------------------------------------------------------ manual import
def import_manual_shareholding(con, path: Path | None = None) -> int:
    """Optional: data/manual/shareholding.csv with columns
    symbol,period_end,promoter_pct,promoter_pledged_pct,source,publication_date
    (percentages as numbers like 50.3). Only use figures you copied from the
    company's official BSE/NSE shareholding filing."""
    p = path or PROJECT_ROOT / "data" / "manual" / "shareholding.csv"
    if not p.exists():
        return 0
    df = pd.read_csv(p)
    need = {"symbol", "period_end", "promoter_pct", "promoter_pledged_pct", "source", "publication_date"}
    if not need.issubset(df.columns):
        raise ValueError(f"{p} must have columns {sorted(need)}")
    df = df[list(need)].copy()
    df["promoter_pct"] = df["promoter_pct"] / 100.0
    df["promoter_pledged_pct"] = df["promoter_pledged_pct"] / 100.0
    df["retrieved_at"] = now_utc()
    return upsert_df(con, "shareholding", df)


# ------------------------------------------------------------------ driver
def collect(symbols: list[str] | None = None, index_name: str = "NIFTY50",
            fundamentals: bool = True, progress=print) -> dict:
    cfg = load_settings()
    con = connect()
    if symbols is None:
        symbols = fetch_universe(con, index_name, cfg)["symbol"].tolist()
    pause = cfg["data"]["request_pause_seconds"]
    years = cfg["data"]["price_history_years"]
    summary = {}
    bench = [cfg["benchmark"]["total_return_proxy"], cfg["benchmark"]["price_index"]]
    for i, s in enumerate(list(symbols) + bench, 1):
        n = fetch_prices(con, s, years, pause)
        rep = {"price_days": n}
        if fundamentals and s not in bench:
            rep.update(fetch_fundamentals(con, s, cfg))
        summary[s] = rep
        progress(f"[{i}/{len(symbols) + len(bench)}] {s}: {rep}")
        time.sleep(pause)
    import_manual_shareholding(con)
    con.close()
    return summary
