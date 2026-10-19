# Equity Analyzer (Indian equities, runs locally)

A local tool that downloads free NSE data, evaluates companies on a 27-metric framework,
ranks them, runs historical portfolio backtests and shows everything in a dashboard.
**Research tool only – not investment advice.**

## Everyday use
| What you want | Double-click |
|---|---|
| Open the dashboard | `launch_dashboard.bat` (opens http://localhost:8501 in your browser) |
| Refresh all data + re-run backtests | `run_full_pipeline.bat` (~5–15 min) |
| Re-run the automated tests | `run_tests.bat` |
| First-time install (already done) | `setup.bat` |

Close the black dashboard window to stop the dashboard.

## Folder guide
- `config/settings.toml` – all thresholds, costs, backtest settings (edit with Notepad).
- `config/qualitative_evidence.toml` – where you record reviewed ratings for the 6 qualitative metrics.
- `data/manual/shareholding_TEMPLATE.csv` – template for promoter holding/pledge figures copied from official filings
  (rename to `shareholding.csv`).
- `data/manual/company_events_TEMPLATE.csv`, `research_coverage_TEMPLATE.csv` – templates for reviewed company events
  and “where we looked” notes (copy to `company_events.csv` / `research_coverage.csv`).
- `data/equity.db` – the local SQLite database (prices, statements, sources, retrieval dates).
- `reports/RUN_REPORT.md` – latest summary; `reports/backtests/<run>/` – full transaction/holding history per run.
- `docs/LIMITATIONS.md` – what the data can and cannot support.
- `docs/COMPANY_RESEARCH_MODULE.md` – the Company Research, Important Events & Ranking module (requirement → implementation).
- `equity/` – Python code (data, metrics, backtest). `app/` – dashboard. `tests/` – automated tests.

## Company research (new)
- **Company Research** page: rank, *Why this rank?*, valuation vs sector peers, business & future plans, a permanent
  dated timeline of important events with sources, risks, and data freshness.
- **Stock Ranking** page: Top 10/25/50/90/100/custom, rank across the universe or inside a filter, per-metric rank,
  sector, market cap, valuation, growth/profitability/debt and event filters.
- **Research Inbox** page: official NSE company lists, exchange filings to review, CSV imports, watchlist alerts.
- Events are **research context only** — they never change a score or rank.

## How results should be read
- **Insufficient Data never counts as a pass.** The *conservative score* gives it 0 points.
- Every backtest is labelled **partial** – see `docs/LIMITATIONS.md` for why a full 27-metric,
  survivorship-free, 10-year backtest is not possible with free data.

## Online version (Streamlit Community Cloud)
Main file: `app/Company_Search.py`. The repository includes a data snapshot (`data/equity.db`, downloaded 16 Oct 2026) so the
online app works without contacting Yahoo Finance. Refreshing data from the online app may fail because Yahoo often blocks
cloud servers; refresh on a PC with `run_full_pipeline.bat` and upload the new `data/equity.db`.
