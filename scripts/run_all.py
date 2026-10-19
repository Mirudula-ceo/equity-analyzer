"""One-shot pipeline: download data -> verify -> analyse -> backtest -> report.

Usage (from the project folder, with the virtual environment active):
    python scripts/run_all.py            # full Nifty 50
    python scripts/run_all.py --skip-download
    python scripts/run_all.py --symbols TCS INFY RELIANCE
    python scripts/run_all.py --universe NSE_ALL   # every eligible NSE mainboard company (slow: hours)

Research module (docs/COMPANY_RESEARCH_MODULE.md): refreshes the official NSE company lists and the
exchange-announcement inbox, imports data/manual/company_events.csv and research_coverage.csv,
saves a ranking snapshot (for "why did the rank change?") and creates watchlist alerts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from equity import db  # noqa: E402
from equity.analysis import rank_symbols  # noqa: E402
from equity.backtest.runner import run_backtest  # noqa: E402
from equity.config import REPORTS_DIR, load_settings  # noqa: E402
from equity.data.collect import collect  # noqa: E402
from equity.data.validate import check_publication_dates, coverage, price_jumps  # noqa: E402
from equity.research import announcements, events, listings, monitor, service  # noqa: E402


def fmt_pct(x):
    return "n/a" if x is None or pd.isna(x) else f"{x * 100:.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--no-backtest", action="store_true")
    ap.add_argument("--universe", choices=["NIFTY50", "NSE_ALL"], default="NIFTY50",
                    help="NSE_ALL = every eligible company in the official NSE list (very slow download)")
    a = ap.parse_args()
    REPORTS_DIR.mkdir(exist_ok=True)
    cfg = load_settings()

    con = db.connect()
    research = {}
    if not a.skip_download:
        print("STEP 0/4  Refreshing official NSE company lists and exchange announcements...", flush=True)
        research["listings"] = listings.refresh_listings(con, cfg)
        research["inbox"] = announcements.refresh_inbox(con, cfg)
        for k, v in research.items():
            print("   ", k, v, flush=True)
        print("STEP 1/4  Downloading data (this takes ~5-10 minutes for Nifty 50)...", flush=True)
        to_get = a.symbols
        if a.universe == "NSE_ALL" and not to_get:
            to_get = listings.eligible_symbols(con, cfg)
            print(f"    NSE_ALL: {len(to_get)} eligible companies in the official list", flush=True)
            if not to_get:
                print("    Official NSE list unavailable - falling back to Nifty 50", flush=True)
                to_get = None
        collect(to_get, progress=lambda m: print("   ", m, flush=True))

    # human-reviewed research inputs (always re-imported; the import is idempotent)
    ev_rep = events.import_events_csv(con, cfg=cfg)
    cov_rep = events.import_coverage_csv(con)
    if ev_rep is not None:
        ev_rep.rows.to_csv(REPORTS_DIR / "events_import_report.csv", index=False)
        print(f"    company_events.csv: {ev_rep.counts}", flush=True)
    if cov_rep is not None:
        cov_rep.to_csv(REPORTS_DIR / "coverage_import_report.csv", index=False)

    uni = db.read_universe(con, "NIFTY50")
    symbols = a.symbols or service.scorable_symbols(con, cfg) or uni["symbol"].tolist()
    label = "CUSTOM" if a.symbols else service.universe_label(con)
    print("STEP 2/4  Verifying downloaded data...", flush=True)
    fin = db.read_financials(con)
    prices = db.read_prices(con)
    acts = db.read_actions(con)
    cov = coverage(fin, symbols)
    pub = check_publication_dates(fin)
    bench_syms = [cfg["benchmark"]["total_return_proxy"], cfg["benchmark"]["price_index"]]
    jumps = price_jumps(prices[prices["symbol"].isin(symbols + bench_syms)],
                        acts[acts["action_type"] == "split"] if not acts.empty else None)
    pstat = prices.groupby("symbol")["date"].agg(["min", "max", "count"]).reset_index()
    cov.to_csv(REPORTS_DIR / "data_coverage.csv", index=False)
    jumps.to_csv(REPORTS_DIR / "price_jumps_to_review.csv", index=False)
    pstat.to_csv(REPORTS_DIR / "price_coverage.csv", index=False)
    log = pd.read_sql_query("SELECT * FROM fetch_log ORDER BY id", con)
    errors = log[log["status"].isin(["error", "empty", "fallback"])]
    errors.to_csv(REPORTS_DIR / "fetch_problems.csv", index=False)

    print("STEP 3/4  Computing 27-metric reports and ranking...", flush=True)
    table, details = rank_symbols(con, symbols)
    table.to_csv(REPORTS_DIR / "sample_ranking.csv", index=False)
    details.to_csv(REPORTS_DIR / "sample_metric_details.csv", index=False)
    status_counts = details.pivot_table(index="name", columns="status", values="symbol",
                                        aggfunc="count", fill_value=0)
    order = details.drop_duplicates("name").set_index("name")["id"]
    status_counts = status_counts.loc[order.sort_values().index]
    status_counts.to_csv(REPORTS_DIR / "metric_availability.csv")
    snap = monitor.save_snapshot(con, table, details, label, cfg=cfg)
    n_alerts = monitor.generate_alerts(con, snap, cfg)

    runs = []
    if not a.no_backtest:
        print("STEP 4/4  Running backtests...", flush=True)
        for strat in ("momentum", "low_volatility", "fundamental"):
            try:
                r = run_backtest(strat, symbols, progress=lambda m: print("   ", m, flush=True))
                r.pop("_result"), r.pop("_bench")
                runs.append(r)
            except Exception as exc:  # noqa: BLE001
                print(f"    {strat} backtest failed: {exc}", flush=True)
                runs.append({"strategy": strat, "error": str(exc)})

    # ------------------------------------------------------------ markdown report
    L = [f"# Equity Analyzer – run report", f"Generated {datetime.now():%Y-%m-%d %H:%M}", "",
         "## Data collected",
         f"- Universe: {len(symbols)} symbols; source: {uni['source'].iloc[0] if not uni.empty else 'n/a'}",
         f"- Price history: {pstat['count'].sum():,} daily rows; earliest {pstat['min'].min().date() if not pstat.empty else 'n/a'}, "
         f"latest {pstat['max'].max().date() if not pstat.empty else 'n/a'}",
         f"- Annual statements per company: median {cov['annual_years'].median():.0f} years "
         f"(min {cov['annual_years'].min()}, max {cov['annual_years'].max()}) – 5 preferred",
         f"- Fetch problems logged: {len(errors)} (see fetch_problems.csv)",
         f"- Unexplained >35% daily price moves to review: {len(jumps)}",
         f"- Impossible publication dates: {len(pub)}", "",
         "## Metric availability across universe (count of companies per status)", "",
         status_counts.to_markdown(), "",
         "## Top 10 by conservative 27-metric score (current data)", "",
         table.head(10)[["rank", "symbol", "name", "conservative_score", "coverage", "pass", "watch",
                         "fail", "insufficient", "not_applicable"]].to_markdown(index=False), "",
         "## Company research module",
         f"- Ranking snapshot saved: {snap} ({label}); new watchlist alerts: {n_alerts}",
         "- Official NSE list: " + (", ".join(f"{r.segment} {r.status}: {r.companies}" for r in
                                               listings.listing_summary(con).itertuples()) or "not loaded"),
         f"- Events on timeline: {con.execute('SELECT COUNT(*) FROM events').fetchone()[0]}; "
         f"last import: {ev_rep.counts if ev_rep is not None else 'no data/manual/company_events.csv'}",
         f"- Exchange filings waiting for review: "
         f"{len(announcements.read_inbox(con, state='to review'))}",
         f"- Open events not re-verified within {cfg['research']['refresh_days']['events_reverify']} days: "
         f"{len(events.stale_verifications(con, cfg))}", "",
         "## Backtests executed"]
    for r in runs:
        if "error" in r:
            L += [f"### {r['strategy']}: FAILED – {r['error']}", ""]
            continue
        p, b, rel = r["report"]["portfolio"], r["report"].get("benchmark", {}), r["report"].get("relative", {})
        t = r["report"]["trades"]
        L += [f"### {r['label']}", f"- Run folder: reports/backtests/{r['run_id']}",
              f"- Period: {p.get('start')} → {p.get('end')} ({p.get('years', 0):.1f} yrs); first trade {r['first_trade']}",
              f"- ₹{p.get('start_value', 0):,.0f} → ₹{p.get('end_value', 0):,.0f}; total {fmt_pct(p.get('total_return'))}, "
              f"CAGR {fmt_pct(p.get('cagr'))}, max drawdown {fmt_pct(p.get('max_drawdown'))}, "
              f"volatility {fmt_pct(p.get('volatility'))}, Sharpe {p.get('sharpe', float('nan')):.2f}, "
              f"Sortino {p.get('sortino', float('nan')):.2f}",
              f"- Benchmark ({r['benchmark']}): CAGR {fmt_pct(b.get('cagr'))}, max drawdown {fmt_pct(b.get('max_drawdown'))}; "
              f"excess CAGR {fmt_pct(rel.get('excess_cagr'))}, beta {rel.get('beta', float('nan')):.2f}",
              f"- Trades: {t.get('completed_sells', 0)} sells, win rate {fmt_pct(t.get('win_rate'))}, "
              f"avg gain {fmt_pct(t.get('avg_gain_pct'))}, avg loss {fmt_pct(t.get('avg_loss_pct'))}; "
              f"turnover {r['report']['turnover']:.2f}x/yr",
              f"- Costs: charges ₹{r['report']['costs']['charges_total']:,.0f}, slippage ₹{r['report']['costs']['slippage_total']:,.0f}; "
              f"dividends ₹{r['report']['dividends_received']:,.0f}",
              f"- P&L: realised ₹{r['report']['pnl']['realized']:,.0f}, unrealised ₹{r['report']['pnl']['unrealized']:,.0f}",
              f"- Warnings: {len(r['warnings'])}", ""]
    if runs and "bias_notes" in runs[0]:
        L += ["## Known biases (apply to all backtests above)"] + [f"- {n}" for n in runs[0]["bias_notes"]]
    (REPORTS_DIR / "RUN_REPORT.md").write_text("\n".join(L), encoding="utf-8")
    with open(REPORTS_DIR / "latest_runs.json", "w", encoding="utf-8") as fh:
        json.dump(runs, fh, indent=2, default=str)
    print("\nDONE. Summary written to reports/RUN_REPORT.md", flush=True)
    con.close()


if __name__ == "__main__":
    main()
