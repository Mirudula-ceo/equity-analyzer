"""Page 6 - Data Quality"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common import con, setup, universe_symbols  # noqa: E402
from equity import db  # noqa: E402
from equity.data.validate import check_publication_dates, coverage, price_jumps  # noqa: E402
from equity.metrics.engine import DEFS  # noqa: E402

setup("Data Quality")
c = con()
syms, uni = universe_symbols()
fin = db.read_financials(c)
prices = db.read_prices(c)
acts = db.read_actions(c)
log = pd.read_sql_query("SELECT * FROM fetch_log ORDER BY id DESC", c)
c.close()

st.subheader("1. Metric automation status")
kind_note = {"quantitative": "Automated from Yahoo statements / prices",
             "qualitative": "Manual review required (config/qualitative_evidence.toml)",
             "external": "No free automatable source – manual CSV import (data/manual/shareholding.csv)"}
st.dataframe(pd.DataFrame([{"#": d.id, "Metric": d.name, "Type": d.kind, "Automation": kind_note[d.kind]}
                           for d in DEFS]), hide_index=True, width="stretch", height=400)

st.subheader("2. Historical coverage of financial statements")
if fin.empty:
    st.warning("No financial statements stored yet.")
else:
    cov = coverage(fin, syms)
    st.dataframe(cov, hide_index=True, width="stretch")
    st.caption("Yahoo Finance typically returns only the last ~4 annual statements, so 5-year trends and "
               "long fundamental backtests are not possible from this source.")

st.subheader("3. Price history coverage")
if not prices.empty:
    ps = prices.groupby("symbol")["date"].agg(first="min", last="max", days="count").reset_index()
    st.dataframe(ps, hide_index=True, width="stretch", height=300)
    jumps = price_jumps(prices, acts[acts["action_type"] == "split"] if not acts.empty else None)
    st.markdown(f"**Daily moves > 35% not explained by a recorded split/bonus:** {len(jumps)}")
    if not jumps.empty:
        st.dataframe(jumps, hide_index=True)

st.subheader("4. Publication-date checks")
pub = check_publication_dates(fin) if not fin.empty else pd.DataFrame()
st.write(f"Impossible or missing publication dates: **{len(pub)}**")
st.caption("All publication dates are ESTIMATED as period end + lag (settings.toml), because Yahoo does not "
           "provide the actual results-announcement date.")

st.subheader("5. Download problems")
bad = log[log["status"].isin(["error", "empty", "fallback"])]
st.dataframe(bad, hide_index=True, width="stretch")
with st.expander("Full fetch log"):
    st.dataframe(log, hide_index=True, width="stretch")

st.subheader("6. Source limitations & backtesting biases")
st.markdown("""
- **Yahoo Finance (yfinance)** is unofficial and may change or rate-limit without notice. It provides daily prices,
  dividends and splits (bonus issues appear as splits) for ~10+ years, but only **~4 years of annual** and ~5 quarters
  of financial statements, shown as currently reported (possibly restated), with **no announcement dates**.
- **Forward P/E** is a current analyst-consensus snapshot; no history, so it is never used in backtests.
- **Promoter holding & pledge** (SEBI shareholding pattern): NSE/BSE websites use anti-bot protection; we do not
  bypass it. Add verified figures to `data/manual/shareholding.csv` to enable these metrics.
- **Qualitative metrics** (business quality, TAM, moat, management, order book, customer concentration) need human
  judgement with cited evidence.
- **Index membership:** only the *current* Nifty 50 list is available free → **survivorship bias** in all backtests.
- **Delisted companies** are absent from Yahoo; mergers (e.g. HDFC Ltd into HDFC Bank, 2023) and demergers
  (e.g. Tata Motors, 2025) may show gaps or odd histories.
- **Benchmark:** NIFTYBEES ETF adjusted close is used as a Nifty 50 total-return proxy (includes ETF expense ratio
  and tracking error). The official Nifty 50 TRI is not available from Yahoo.
- Bad price ticks that revert within days are repaired in backtests and listed as warnings; un-adjusted demergers are handled via `config/corporate_events.csv`.
- Costs use today's charge rates for the whole history; taxes on gains and dividends are ignored.
""")
