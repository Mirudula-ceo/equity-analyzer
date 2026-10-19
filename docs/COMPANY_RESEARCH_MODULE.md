# Company Research, Important Events & Ranking module

**Goal:** for every NSE company show not only *what rank it has*, but *why*, *what it is worth* and *what it plans
next* — every statement with its source and date.

Original requirement (Tamil, verbatim): [`docs/requirements/company_research_requirements_ta.md`](requirements/company_research_requirements_ta.md).

**Scope rule:** this module sits **on top of** the existing engine. It does not change any of the 27 metric formulas,
thresholds, the conservative score or the ranking rule. In this repository the "27 strategies" of the requirement are
the **27 metrics** in `equity/metrics/engine.py` (the three *backtest* strategies are separate and unchanged).

---

## Where to find it

| | |
|---|---|
| **Company Research** page (`app/pages/7_Company_Research.py`) | Summary card, *Why this rank?*, *Important developments*, tabs: Overview · 27 Metrics · Valuation · Business & Future Plans · Important Events · Financials · Risks · Sources. Watchlist toggle. |
| **Stock Ranking** page (`app/pages/3_Stock_Ranking.py`) | Top 10/25/50/90/100/Custom, rank scope, per-metric rank, sector/industry, market cap, P/E, growth/profitability/debt, event filters, coverage, last-updated, change since last snapshot. |
| **Research Inbox** page (`app/pages/8_Research_Inbox.py`) | Alerts, NSE list status/refresh, CSV import with row-by-row results, exchange filings to review, events due for re-verification, data freshness. |
| Code | `equity/research/` — `constants.py`, `listings.py`, `events.py`, `announcements.py`, `explain.py`, `monitor.py`, `service.py` |
| Settings | `[research]` block at the end of `config/settings.toml` |
| Human input | `data/manual/company_events.csv`, `data/manual/research_coverage.csv` (templates beside them) |
| Tests | `tests/test_research.py` (25 tests) |

## Day-to-day workflow

1. `run_full_pipeline.bat` — now also refreshes the **official NSE lists** and the **exchange-announcement inbox**,
   re-imports the two manual CSVs, saves a **ranking snapshot** and creates **watchlist alerts**. A new
   *Company research module* section appears in `reports/RUN_REPORT.md`; row-level import results go to
   `reports/events_import_report.csv`.
2. Open **Research Inbox** → *Exchange filings waiting for review*. Read each filing; if it matters, add a row to
   `company_events.csv` **in your own words** (never paste article text).
3. When you look for something and do not find it, add a row to `research_coverage.csv` (`result = Not found`,
   with where you looked). This is what lets a page say “not found in X, Y” instead of silently implying “didn’t happen”.
4. To update an existing event (delay, new timeline, completion) add a new row with the same title, or put its
   `event_id` (shown on the event) in `event_ref` to be certain.

Everything that needs reading annual reports, presentations or news is **human-reviewed**, consistent with
`docs/LIMITATIONS.md`: the program does not scrape pages protected by login or anti-bot measures, and licensed
publications (Bloomberg, Mint, Moneycontrol, Tickertape, Screener) are cited by link, not copied.

---

## Requirement → implementation

### 1. Cover every NSE company
| Requirement | Implementation |
|---|---|
| Official list of NSE companies | `listings.refresh_listings()` downloads NSE's own `EQUITY_L.csv` (mainboard), `SME_EQUITY_L.csv`, `eq_etfseclist.csv`. If NSE blocks the download, a copy saved by hand to `data/manual/` with the same file name is used. |
| New listings, symbol changes, delistings | `first_seen` / `last_seen` per symbol; symbols missing from a successful download become **“not in latest NSE list”** (could be delisting, suspension, merger or rename — not guessed). `symbolchange.csv` feeds `resolve_symbol()` / `symbol_aliases()`, so events filed under an old symbol follow the company. A failed download never wipes the previous list. |
| A research page per company | Company Research page lists every company in the current list, including ones without downloaded data (they show events and the research checklist, and say why there is no score). |
| **Never hard-code 3,099** | Counts are `COUNT(*)` of the latest official files (Research Inbox §2 and the run report). Test: `test_company_count_comes_from_the_list_not_a_constant`. |
| Segments; which are eligible | `segment` = MAINBOARD / SME / ETF. Eligibility = `eligible_segments` and `eligible_series` in settings (default: mainboard, series EQ/BE/BZ). `run_all.py --universe NSE_ALL` downloads and scores every eligible company (slow: hours with Yahoo's rate limits). |

### 2. Sources
`source_type` on every event and research note: `exchange_filing`, `investor_relations`, `official_website`,
`subsidiary_brand_site`, `financial_publication`, `data_platform`. Official types can confirm evidence; publications
and data platforms alone produce a warning if marked *Confirmed*. Absence on one site is never recorded as absence —
see §3.

### 3. Research workflow and evidence states
Identity first (symbol → current symbol → official name/ISIN), then sources, each finding stored with source and
date. The six states are enforced vocabulary:

| State | Where it is used |
|---|---|
| Confirmed · Management guidance · Reported / unconfirmed · Conflicting | events |
| Not found · Not applicable (and the four above) | research notes (`research_coverage.csv`) |

The *Research checklist* on each company shows, per business-development topic: **Found** (n events) / **Not found
in the sources searched (not proof it did not happen)** / Not applicable / Conflicting / **Not yet researched**, with
the sources searched and dates.

### 4. Business developments
`category` covers: new sector/business entry, product/brand launch, emerging business plan (e.g. EV),
acquisition/merger, divestment, subsidiary/JV, capacity/factory expansion, capex/funding plan, order/contract win,
regulatory approval, management change, debt/default, litigation/regulatory action, project delay/cancellation.

**Plans are never shown as running businesses:** `business_stage` = existing/proposed. The import rejects
*Completed* on a proposed plan, *Completed* backed only by management guidance, and *Completed* with a future date.
Proposed items always display as
*“Management announced a proposed plan: … (2028–2030); current execution status: …”*.

### 5. Permanent timeline
All fields of the requirement table are stored (`events` + `event_sources` + `event_updates`): title, category,
event date, published date, **first captured** (set once, never changed), sources with links, own-words summary,
business relevance, timeline/milestones, status (Announced/Underway/Completed/Delayed/Cancelled), **last verified**.
Nothing is ever deleted; every change of status, evidence, timeline or summary is kept with its source and date.
Only our summary and metadata are stored — never article text. Same story from several sites → **one event** with
several sources (same URL, or similar title within 30 days, or near-identical title on a still-open event, or an
explicit `event_ref`). Older sources cannot overwrite newer status; a weaker repeat cannot downgrade *Confirmed*.
Open events not re-verified within 180 days are listed for review. History length: unlimited (≥10 years requested).

### 6. Valuation and “Why this rank?”
Valuation tab: price **with date**, market cap, financial periods used, P/E, P/B, EV/EBITDA, PEG, FCF yield, margin of
safety, each against the **sector median** of the scored universe. *Why this rank?*: rank of N, points ÷ applicable
metrics, strongest/weakest area, positive (Pass), negative (Fail), Watch, **missing-data impact** (points withheld and
the best possible score if all missing metrics passed), and **change since the previous snapshot** — which metrics
changed status and by how many points, or that the company was unchanged and other companies moved, or that
thresholds changed.

**The explanation is computed from the same results as the score and re-adds the parts**; the page shows an error if
they ever disagree (`check.matches`; tests `test_explanation_reproduces_the_score`,
`test_rank_change_is_explained_by_metric_moves`). The page also refuses to explain a ranking row whose score differs
from a fresh calculation.

### 7. Relevance of news
Each event records segment affected, existing vs proposed, financial link (revenue/margin/debt/capex/risk),
whether the impact is quantified (and the figure), whether it is **already reflected in the 27-metric data**, and the
next milestone. **News never moves the rank.** `research.event_scoring = false`; no event-scoring rule exists, so
events appear only as “Context only (not scored)”. Test: `test_events_never_change_score_or_rank`. Adding event
scoring later requires written, transparent rules first.

### 8. Filters and ranking options
See Stock Ranking above. Every view prints one sentence stating exactly what it shows, e.g.
*“Top 10 by rank re-calculated inside the filtered companies; filters: sector Technology. Universe: 50 scored
companies, 50 ranked.”* — so *Top 10 of the universe* vs *Top 10 within a sector* is never ambiguous. Both the
overall rank and the rank shown are columns.

### 9. Company page
Implemented as specified: summary card *Company | Price | Overall Rank | 27-metric Score | Data Coverage | Last
Updated*, then *Why this rank?* and *Important developments*, then the eight tabs.

### 10. Updates and alerts
| Requirement | Implementation |
|---|---|
| Separate schedules | `[research.refresh_days]`: prices 3, financials 100, listings 7, announcements 2, event re-verification 180. |
| Re-score on new results | Scores are always computed live from stored data; the pipeline re-scores after every download and saves a snapshot. |
| Timeline refresh | Every pipeline run re-imports the CSVs (idempotent). |
| Watchlist alerts | Rank move ≥ 5 places (or entering/leaving the ranking), alert-category events (acquisition, divestment, delay/cancellation, debt, litigation, management change), status *Delayed/Cancelled*, and matching filings waiting for review. Each alert is created once. |
| Visible stale/failed data | Freshness table on every company page and in the inbox: OK / Stale / Missing, plus “last refresh failed” with the error. |
| No wrong-company news | Symbol must be known; company name and ISIN in the source must match the official listing; unmatched filings stay in “unmatched company”. |
| No look-ahead | `read_events(as_of=…)` returns only events **published** by that date with the status known then. The ranking page's point-in-time date applies it. Backtests do not use events at all. |

---

## What still needs a person (or a paid source)

* Reading annual reports, presentations, call transcripts and news, and writing the event rows.
* NSE may block automated downloads from some networks; then save the official files by hand into `data/manual/`.
* Scoring all ~2,000+ eligible companies needs `--universe NSE_ALL` and several hours of Yahoo downloads; Yahoo
  coverage of small companies is patchy (they will show “Insufficient Data”, never a pass).
* Event scoring (§7) is deliberately off until transparent rules are agreed.
