"""Load data from SQLite, run a backtest, save the report."""
from __future__ import annotations

import json
import uuid
from datetime import datetime

import pandas as pd

from .. import db
from ..config import CONFIG_DIR, REPORTS_DIR, load_settings
from ..data.validate import repair_price_spikes
from .engine import Backtester, BacktestParams
from .performance import full_report
from .strategies import FundamentalStrategy, LowVolatilityStrategy, MomentumStrategy

STRATEGIES = {"momentum": MomentumStrategy, "low_volatility": LowVolatilityStrategy,
              "fundamental": FundamentalStrategy}

BIAS_NOTES = [
    "SURVIVORSHIP BIAS: the universe is TODAY's index constituents applied to the whole history. "
    "Free historical Nifty membership lists were not available, so stocks that were dropped "
    "from the index (often poor performers) are missing. Results are likely flattered.",
    "Delisted companies are not in Yahoo Finance and cannot be included.",
    "Current brokerage/statutory charge rates are applied to the whole period.",
    "Dividends are credited on the ex-date and dividend tax is ignored; capital-gains tax is ignored.",
    "Financial statement publication dates are ESTIMATED (period end + lag), and Yahoo shows "
    "the latest (possibly restated) figures rather than as-first-published numbers.",
]


def load_market(con, symbols):
    px = db.read_prices(con, symbols)
    acts = db.read_actions(con, symbols)
    split_days = acts[acts["action_type"] == "split"].groupby("symbol")["date"].apply(list).to_dict() \
        if not acts.empty else {}
    prices, repairs = {}, []
    for s, g in px.groupby("symbol"):
        fixed, bad = repair_price_spikes(g.set_index("date")[["open", "close", "adj_close"]],
                                         split_dates=split_days.get(s, []))
        prices[s] = fixed
        if bad:
            repairs.append(f"{s}: replaced {len(bad)} bad price tick(s) on "
                           f"{', '.join(str(pd.Timestamp(d).date()) for d in bad)} with previous close")
    sp = acts[acts["action_type"] == "split"].rename(columns={"value": "ratio"})[["symbol", "date", "ratio"]] \
        if not acts.empty else None
    dv = acts[acts["action_type"] == "dividend"].rename(columns={"value": "amount"})[["symbol", "date", "amount"]] \
        if not acts.empty else None
    # Corporate events Yahoo has not adjusted for (e.g. demergers). The spun-off
    # shares are credited as cash worth (previous close - ex-date open) per share.
    ev_path = CONFIG_DIR / "corporate_events.csv"
    if ev_path.exists():
        ev = pd.read_csv(ev_path)
        extra = []
        for e in ev.itertuples():
            if e.type != "demerger" or e.symbol not in prices:
                continue
            p = prices[e.symbol]
            d = pd.Timestamp(e.ex_date)
            before, on = p[p.index < d], p[p.index == d]
            if before.empty or on.empty:
                continue
            prev_close, ex_open = float(before["close"].iloc[-1]), float(on["open"].iloc[0])
            if prev_close > 0 and ex_open / prev_close - 1 < -0.15:     # history NOT adjusted by source
                extra.append({"symbol": e.symbol, "date": d, "amount": prev_close - ex_open})
                repairs.append(f"{e.symbol}: demerger on {d.date()} - spun-off shares credited as cash "
                               f"₹{prev_close - ex_open:,.2f}/share (prev close - ex-date open; approximation)")
        if extra:
            dv = pd.concat([dv, pd.DataFrame(extra)], ignore_index=True) if dv is not None else pd.DataFrame(extra)
    return prices, sp, dv, repairs


def run_backtest(strategy: str = "momentum", symbols: list[str] | None = None,
                 overrides: dict | None = None, save: bool = True, progress=print) -> dict:
    cfg = load_settings()
    bt = {**cfg["backtest"], **(overrides or {})}
    con = db.connect()
    if symbols is None:
        symbols = db.read_universe(con, "NIFTY50")["symbol"].tolist()
    bench_sym = cfg["benchmark"]["total_return_proxy"]
    prices, sp, dv, repairs = load_market(con, symbols + [bench_sym])
    bench = prices.pop(bench_sym, None)
    if bench is not None:
        bench = bench["adj_close"]
    missing = [s for s in symbols if s not in prices]
    adj = pd.DataFrame({s: df["adj_close"] for s, df in prices.items()})
    calendar = bench.index if bench is not None else None

    start = pd.Timestamp(bt["start_date"])
    if strategy == "momentum":
        strat = MomentumStrategy(adj, bt["momentum_lookback_months"], bt["momentum_skip_months"],
                                 bt["min_price_history_days"])
    elif strategy == "low_volatility":
        strat = LowVolatilityStrategy(adj, bt["min_price_history_days"])
    elif strategy == "fundamental":
        fin_all = db.read_financials(con)
        fin = {s: g for s, g in fin_all.groupby("symbol") if s in prices}
        pr = {s: df.reset_index()[["date", "close"]] for s, df in prices.items()}
        spl = {}
        if sp is not None:
            for s, g in sp.groupby("symbol"):
                spl[s] = pd.Series(g["ratio"].values, index=g["date"])
        info = {s: db.read_info(con, s) for s in fin}
        shs = {s: db.read_shareholding(con, s) for s in fin}
        strat = FundamentalStrategy(fin, pr, spl, info, cfg, cfg["scoring"]["min_coverage_for_rank"], shs,
                                    min_names=int(bt["max_stocks"]))
        # Start at the first quarter-end where enough companies can be scored
        ann = fin_all[fin_all["freq"] == "annual"]
        if ann.empty:
            raise RuntimeError("No financial statements in the database. Run data collection first.")
        first_pub = ann.groupby("symbol")["publication_date"].min().median()
        start = max(start, pd.Timestamp(first_pub))
    else:
        raise ValueError(strategy)

    params = BacktestParams(bt["initial_capital"], start, bt.get("end_date"), bt["rebalance_frequency"],
                            int(bt["max_stocks"]), bt.get("tolerance", 0.02), cfg["costs"])
    progress(f"Running {strategy} backtest on {len(prices)} stocks from {start.date()} ...")
    res = Backtester({s: df[["open", "close"]] for s, df in prices.items()}, sp, dv, strat, params,
                     calendar).run()
    first_trade = res.transactions["date"].min() if not res.transactions.empty else None
    if first_trade is not None:
        # Measure performance from the first investment (the period spent waiting for
        # enough history is excluded so cash drag doesn't distort CAGR).
        sig = res.transactions["signal_date"].min()
        res.equity = res.equity[res.equity.index >= sig]
    rep = full_report(res, bench, bt["risk_free_rate"])
    label = getattr(strat, "label", strategy)
    out = {
        "run_id": datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4],
        "strategy": strategy, "label": label, "is_full_27_metric_backtest": False,
        "universe": f"NIFTY50 current constituents ({len(symbols)} symbols; {len(missing)} without price data)",
        "missing_symbols": missing, "params": {**bt, "start_used": str(start.date())},
        "first_trade": str(first_trade.date()) if first_trade is not None else None,
        "benchmark": f"{bench_sym} adjusted close (Nifty 50 ETF, dividends reinvested, after ETF fees)",
        "report": rep, "warnings": repairs + res.warnings, "data_repairs": repairs, "bias_notes": BIAS_NOTES,
    }
    if save:
        d = REPORTS_DIR / "backtests" / out["run_id"]
        d.mkdir(parents=True, exist_ok=True)
        res.equity.assign(benchmark=(bench.reindex(res.equity.index).ffill() / bench.reindex(res.equity.index).ffill().dropna().iloc[0]
                                     * bt["initial_capital"]) if bench is not None else None).to_csv(d / "equity_curve.csv")
        res.transactions.to_csv(d / "transactions.csv", index=False)
        res.holdings.to_csv(d / "holdings.csv", index=False)
        res.dividends.to_csv(d / "dividends.csv", index=False)
        res.corporate_actions.to_csv(d / "corporate_actions.csv", index=False)
        res.final_positions.to_csv(d / "final_positions.csv", index=False)
        if strategy == "fundamental":
            pd.DataFrame(strat.log).to_csv(d / "fundamental_scores_by_date.csv", index=False)
        with open(d / "summary.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
        con.execute("INSERT OR REPLACE INTO backtest_runs VALUES (?,?,?,?,?)",
                    (out["run_id"], datetime.now().isoformat(timespec="seconds"), label,
                     json.dumps(out["params"], default=str), json.dumps(out, default=str)))
        con.commit()
        out["folder"] = str(d)
    con.close()
    out["_result"] = res
    out["_bench"] = bench
    return out
