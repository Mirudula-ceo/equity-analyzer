"""Page 3 - Stock Ranking"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common import con, setup, universe_symbols  # noqa: E402
from equity.analysis import rank_symbols  # noqa: E402
from equity.config import load_settings  # noqa: E402

setup("Stock Ranking")
cfg = load_settings()
st.markdown(
    f"""**How the score works** – Pass = {cfg['scoring']['points']['Pass']}, Watch = {cfg['scoring']['points']['Watch']},
Fail = 0, **Insufficient Data = 0** (still counted). *Not Applicable* metrics (e.g. Debt-to-Equity for banks) are
removed from the denominator. **Conservative score** = points ÷ applicable metrics. Companies with coverage below
{cfg['scoring']['min_coverage_for_rank'] * 100:.0f}% are listed but not ranked. Thresholds: `config/settings.toml`."""
)
syms, _ = universe_symbols()
chosen = st.multiselect("Companies", syms, default=syms)
as_of = st.date_input("Point-in-time date (optional)", value=None)


@st.cache_data(show_spinner="Scoring companies...")
def compute(symbols, as_of):
    c = con()
    t, d = rank_symbols(c, list(symbols), as_of)
    c.close()
    return t, d


if chosen:
    t, d = compute(tuple(chosen), pd.Timestamp(as_of) if as_of else None)
    show = t[["rank", "symbol", "name", "sector", "conservative_score", "score_on_evaluated", "coverage",
              "pass", "watch", "fail", "insufficient", "not_applicable"]].copy()
    for col in ("conservative_score", "score_on_evaluated", "coverage"):
        show[col] = (show[col] * 100).round(1)
    st.dataframe(show, hide_index=True, width="stretch", height=600,
                 column_config={"conservative_score": st.column_config.ProgressColumn(
                     "Conservative score %", min_value=0, max_value=100, format="%.1f"),
                     "coverage": st.column_config.NumberColumn("Coverage %"),
                     "score_on_evaluated": st.column_config.NumberColumn("Score on evaluated %")})
    st.download_button("Download ranking CSV", t.to_csv(index=False), "ranking.csv")
    with st.expander("Metric-by-metric status matrix"):
        m = d.pivot_table(index="symbol", columns="id", values="status", aggfunc="first")
        icon = {"Pass": "🟢", "Watch": "🟡", "Fail": "🔴", "Insufficient Data": "⚪", "Not Applicable": "➖"}
        st.dataframe(m.map(lambda s: icon.get(s, "")), width="stretch")
        st.caption("Columns are metric numbers 1–27 (see 27-Metric Report).")
