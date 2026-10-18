"""Portfolio performance statistics."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def drawdown(values: pd.Series) -> pd.Series:
    return values / values.cummax() - 1.0


def stats(values: pd.Series, rf: float = 0.0) -> dict:
    v = values.dropna()
    if len(v) < 2:
        return {}
    r = v.pct_change().dropna()
    years = (v.index[-1] - v.index[0]).days / 365.25
    total = v.iloc[-1] / v.iloc[0] - 1
    cagr = (v.iloc[-1] / v.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std() * np.sqrt(TRADING_DAYS)
    rf_d = (1 + rf) ** (1 / TRADING_DAYS) - 1
    ex = r - rf_d
    sharpe = ex.mean() / r.std() * np.sqrt(TRADING_DAYS) if r.std() > 0 else np.nan
    downside = np.sqrt((np.minimum(ex, 0) ** 2).mean()) * np.sqrt(TRADING_DAYS)
    sortino = ex.mean() * TRADING_DAYS / downside if downside > 0 else np.nan
    dd = drawdown(v)
    return {"start": v.index[0].date(), "end": v.index[-1].date(), "years": years,
            "start_value": float(v.iloc[0]), "end_value": float(v.iloc[-1]),
            "total_return": total, "cagr": cagr, "volatility": vol, "sharpe": sharpe,
            "sortino": sortino, "max_drawdown": float(dd.min()),
            "max_drawdown_date": dd.idxmin().date()}


def period_returns(values: pd.Series, freq: str) -> pd.Series:
    """freq 'M' or 'Y'. Each period's return uses the prior period's last value as base
    (first period uses the starting value)."""
    v = values.dropna()
    key = v.index.to_period(freq)
    last = v.groupby(key).last()
    base = last.shift(1)
    base.iloc[0] = v.iloc[0]
    return last / base - 1


def relative(port: pd.Series, bench: pd.Series) -> dict:
    df = pd.concat([port, bench], axis=1, keys=["p", "b"]).dropna()
    if len(df) < 20:
        return {}
    r = df.pct_change().dropna()
    beta = r.cov().loc["p", "b"] / r["b"].var() if r["b"].var() > 0 else np.nan
    te = (r["p"] - r["b"]).std() * np.sqrt(TRADING_DAYS)
    sp, sb = stats(df["p"]), stats(df["b"])
    return {"excess_cagr": sp["cagr"] - sb["cagr"], "beta": beta, "tracking_error": te,
            "information_ratio": (sp["cagr"] - sb["cagr"]) / te if te > 0 else np.nan,
            "correlation": r["p"].corr(r["b"])}


def trade_stats(tx: pd.DataFrame) -> dict:
    if tx is None or tx.empty or "realized_pnl" not in tx:
        return {"completed_sells": 0}
    sells = tx[(tx["side"] == "sell") & tx["realized_pnl"].notna()]
    if sells.empty:
        return {"completed_sells": 0}
    pnl = sells["realized_pnl"].astype(float)
    ret = pnl / sells["cost_basis"].astype(float)
    gains, losses = ret[ret > 0], ret[ret <= 0]
    return {"completed_sells": int(len(sells)), "full_exits": int((sells["exit_type"] == "full").sum()),
            "win_rate": float((pnl > 0).mean()), "avg_gain_pct": float(gains.mean()) if len(gains) else np.nan,
            "avg_loss_pct": float(losses.mean()) if len(losses) else np.nan,
            "realized_pnl": float(pnl.sum())}


def turnover(tx: pd.DataFrame, equity: pd.Series) -> float:
    """Annualised one-way turnover = (buys + sells)/2 per year / average portfolio value."""
    if tx is None or tx.empty:
        return 0.0
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    traded = tx["gross_value"].sum() / 2
    return float(traded / years / equity.mean()) if years > 0 else np.nan


def full_report(res, bench: pd.Series | None, rf: float) -> dict:
    v = res.equity["value"]
    out = {"portfolio": stats(v, rf)}
    tx = res.transactions
    out["trades"] = trade_stats(tx)
    out["turnover"] = turnover(tx, v)
    cost_cols = [c for c in (tx.columns if not tx.empty else []) if c.startswith("cost_")]
    out["costs"] = {"charges_total": float(tx["charges"].sum()) if not tx.empty else 0.0,
                    "slippage_total": float(tx["slippage"].sum()) if not tx.empty else 0.0,
                    **({c: float(tx[c].sum()) for c in cost_cols})}
    out["dividends_received"] = float(res.dividends["amount"].sum()) if not res.dividends.empty else 0.0
    unreal = float(res.final_positions["unrealized_pnl"].sum()) if not res.final_positions.empty else 0.0
    out["pnl"] = {"realized": out["trades"].get("realized_pnl", 0.0), "unrealized": unreal,
                  "dividends": out["dividends_received"],
                  "total_change": float(v.iloc[-1] - v.iloc[0])}
    if bench is not None and not bench.dropna().empty:
        b = bench.reindex(v.index).ffill().dropna()
        b = b / b.iloc[0] * v.loc[b.index[0]]
        out["benchmark"] = stats(b, rf)
        out["relative"] = relative(v, b)
    return out
