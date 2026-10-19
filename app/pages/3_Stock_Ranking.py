"""Page 3 - Stock Ranking (with research filters)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common import con, ranking, scorable, setup  # noqa: E402
from equity.analysis import rank_symbols  # noqa: E402
from equity.config import load_settings  # noqa: E402
from equity.metrics.engine import DEFS, FAIL, INSUFFICIENT, PASS, WATCH  # noqa: E402
from equity.research import events as ev_mod  # noqa: E402
from equity.research import monitor  # noqa: E402
from equity.research.constants import CATEGORIES, EVENT_STATUSES  # noqa: E402
from equity.research.service import Filters, apply_filters  # noqa: E402

setup("Stock Ranking")
cfg = load_settings()
st.markdown(
    f"""**How the score works** – Pass = {cfg['scoring']['points']['Pass']}, Watch = {cfg['scoring']['points']['Watch']},
Fail = 0, **Insufficient Data = 0** (still counted). *Not Applicable* metrics (e.g. Debt-to-Equity for banks) are
removed from the denominator. **Conservative score** = points ÷ applicable metrics. Companies with coverage below
{cfg['scoring']['min_coverage_for_rank'] * 100:.0f}% are listed but not ranked. Thresholds: `config/settings.toml`.
News and events are **never** part of the score."""
)

src = ranking()
syms, _ = scorable()
if src["table"].empty:
    st.warning("No scored companies yet. The weekly GitHub Action (or `run_full_pipeline.bat`) fills this in.")
    st.stop()
if src["kind"] == "snapshot":
    m = src["meta"]
    st.success(f"**All NSE companies:** {m.get('companies_scored', len(src['table'])):,} eligible companies scored, "
               f"{m.get('companies_ranked', int(src['table']['eligible'].sum())):,} ranked · snapshot "
               f"{str(m.get('generated_at', ''))[:16].replace('T', ' ')} UTC (refreshed weekly by GitHub Actions).")
as_of = st.date_input("Point-in-time date (optional – re-scores only the companies with full local data)",
                      value=None)


@st.cache_data(show_spinner="Scoring companies...")
def compute_pit(symbols, as_of):
    c = con()
    t, d = rank_symbols(c, list(symbols), as_of)
    c.close()
    return t, d


if as_of:
    t, d = compute_pit(syms, pd.Timestamp(as_of))
    label = f"point-in-time {as_of} ({len(syms)} companies with full local data)"
else:
    t, d, label = src["table"], src["details"], src["label"]
c = con()
events = ev_mod.read_events(c, as_of=pd.Timestamp(as_of) if as_of else None)
prev_rank = {}
for s in t["symbol"]:
    p = monitor.previous_snapshot(c, s, label)
    if p:
        prev_rank[s] = p["rank"]
c.close()

# ---------------------------------------------------------------- filters
with st.expander("Filters", expanded=True):
    a, b, cc = st.columns(3)
    n_max = max(int(t["rank"].notna().sum()), 1)
    show_all = a.checkbox("Show all companies", value=False)
    top_n = None if show_all else int(a.number_input(
        "Show top N (any number: 9, 99, 250 …)", min_value=1, max_value=max(n_max, 1),
        value=min(10, n_max), step=1))
    a.caption("Quick picks: " + " · ".join(str(n) for n in cfg["research"]["top_n_options"]))
    scope = b.radio("Rank within", ["Whole eligible universe", "Only the filtered companies"],
                    help="Universe: keep each company's overall rank. Filtered: re-rank 1..N inside the filter.")
    metric_names = {"overall": "Overall 27-metric score", **{dd.key: f"#{dd.id} {dd.name}" for dd in DEFS}}
    rank_by = cc.selectbox("Rank by", list(metric_names), format_func=metric_names.get)

    a, b, cc = st.columns(3)
    sectors = a.multiselect("Sector", sorted(t["sector"].dropna().unique()))
    inds = b.multiselect("Industry", sorted(t[t["sector"].isin(sectors) if sectors else slice(None)]["industry"]
                                            .dropna().unique()))
    mc = (t["market_cap"].dropna() / 1e7)
    mrange = cc.slider("Market cap (₹ crore)", 0, int(mc.max()) + 1 if len(mc) else 1,
                       (0, int(mc.max()) + 1 if len(mc) else 1)) if len(mc) else (None, None)

    a, b, cc = st.columns(3)
    pe_max = a.number_input("Max P/E (0 = no limit)", 0.0, 1000.0, 0.0, step=5.0)
    groups = {"Growth (revenue, EPS)": ["revenue_growth", "profit_eps_growth"],
              "Profitability (ROCE, ROE, margin)": ["roce", "roe", "operating_margin"],
              "Debt (D/E, interest cover)": ["debt_to_equity", "interest_coverage"]}
    need = b.multiselect("Must Pass", list(groups), help="Every metric in the group must be Pass")
    allow_watch = b.checkbox("…or Watch", value=False)
    min_cov = cc.slider("Min data coverage %", 0, 100, 0) / 100

    a, b, cc = st.columns(3)
    ev_cats = a.multiselect("Has events in category", CATEGORIES)
    ev_stat = b.multiselect("Event status", EVENT_STATUSES)
    ev_since = cc.date_input("Event date from", value=None)
    upd = st.date_input("Price updated on/after", value=None)

status_filters = {}
for g in need:
    for k in groups[g]:
        status_filters[k] = [PASS, WATCH] if allow_watch else [PASS]
f = Filters(top_n=top_n, scope="view" if scope.startswith("Only") else "universe", sectors=sectors,
            industries=inds, mcap_min_cr=mrange[0] if mrange[0] else None,
            mcap_max_cr=mrange[1] if len(mc) and mrange[1] < int(mc.max()) + 1 else None,
            pe_max=pe_max or None, status_filters=status_filters, event_categories=ev_cats,
            event_statuses=ev_stat, event_since=ev_since.isoformat() if ev_since else None,
            min_coverage=min_cov or None, updated_since=upd.isoformat() if upd else None, rank_by=rank_by)
rows, sentence = apply_filters(t, d, events, f)
st.info(f"**{sentence}** Ranking: {label}.")

rows["change"] = rows.apply(lambda r: None if r["symbol"] not in prev_rank or pd.isna(prev_rank[r["symbol"]])
                            or pd.isna(r["overall_rank"]) else int(prev_rank[r["symbol"]] - r["overall_rank"]), axis=1)
n_ev = events.groupby("symbol").size() if not events.empty else pd.Series(dtype=int)
rows["events"] = rows["symbol"].map(n_ev).fillna(0).astype(int)
rows["mcap_cr"] = (rows["market_cap"] / 1e7).round(0)
show = rows[["rank_shown", "overall_rank", "change", "symbol", "name", "sector", "industry", "mcap_cr",
             "conservative_score", "coverage", "pass", "watch", "fail", "insufficient", "events",
             "price_date", "latest_period"]].copy()
for col in ("conservative_score", "coverage"):
    show[col] = (show[col] * 100).round(1)
st.dataframe(show, hide_index=True, width="stretch", height=600,
             column_config={"rank_shown": st.column_config.NumberColumn("Rank (shown)", format="%d"),
                            "overall_rank": st.column_config.NumberColumn("Overall rank", format="%d"),
                            "change": st.column_config.NumberColumn("Δ since last snapshot", format="%+d",
                                                                    help="Positive = moved up"),
                            "conservative_score": st.column_config.ProgressColumn(
                                "Score %", min_value=0, max_value=100, format="%.1f"),
                            "coverage": st.column_config.NumberColumn("Coverage %"),
                            "mcap_cr": st.column_config.NumberColumn("Mkt cap ₹ cr", format="%,d"),
                            "events": st.column_config.NumberColumn("Events", help="Recorded events (context only)")})
st.download_button("Download this view (CSV)", rows.to_csv(index=False), "ranking_view.csv")
pick_sym = st.selectbox("Open a company's research page", [""] + rows["symbol"].tolist())
if pick_sym:
    st.session_state["symbol"] = pick_sym
    st.switch_page("pages/7_Company_Research.py")

with st.expander("Metric-by-metric status matrix"):
    m = d[d["symbol"].isin(rows["symbol"])].pivot_table(index="symbol", columns="id", values="status", aggfunc="first")
    icon = {PASS: "🟢", WATCH: "🟡", FAIL: "🔴", INSUFFICIENT: "⚪", "Not Applicable": "➖"}
    st.dataframe(m.map(lambda s: icon.get(s, "")), width="stretch")
    st.caption("Columns are metric numbers 1–27 (see 27-Metric Report).")
