"""Page 2 - 27-Metric Report"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from common import STATUS_ICON, con, pick_symbol, setup  # noqa: E402
from equity.analysis import analyze_symbol  # noqa: E402

setup("27-Metric Report")
sym = pick_symbol()
if not sym:
    st.stop()

as_of = st.date_input("Analysis date (point-in-time: only data published by this date is used)",
                      value=None, help="Leave empty for the latest data.")
c = con()
res, sc, pit = analyze_symbol(c, sym, pd.Timestamp(as_of) if as_of else None)
c.close()

st.subheader(f"{pit.info.get('longName') or sym} — as of {pd.Timestamp(pit.as_of).date()}")
k = st.columns(6)
k[0].metric("Conservative score", f"{sc['conservative_score'] * 100:.0f}%",
            help="Points ÷ applicable metrics. Insufficient Data earns 0.")
k[1].metric("Coverage", f"{sc['coverage'] * 100:.0f}%", help="Evaluated ÷ applicable metrics")
k[2].metric("🟢 Pass", sc["pass"])
k[3].metric("🟡 Watch", sc["watch"])
k[4].metric("🔴 Fail", sc["fail"])
k[5].metric("⚪ Insufficient", sc["insufficient"], help=f"Not applicable: {sc['not_applicable']}")

st.caption("Data source: " + pit.source_note())

df = pd.DataFrame([r.to_dict() for r in res])
table = df[["id", "category", "name", "display", "status"]].copy()
table["status"] = table["status"].map(lambda s: f"{STATUS_ICON[s]} {s}")
table.columns = ["#", "Category", "Metric", "Value", "Status"]
st.dataframe(table, hide_index=True, width="stretch", height=990)

st.subheader("Metric details")
for r in res:
    with st.expander(f"{r.id}. {r.name} — {STATUS_ICON[r.status]} {r.status} ({r.display})"):
        st.markdown(f"**Result:** {r.explanation}")
        st.markdown(f"**Formula:** {r.formula}  \n**Inputs:** {r.inputs}  \n**Method:** {r.method}  \n"
                    f"**Rule:** {r.criteria}  \n**Source:** {r.source}")
        tr = {k: v for k, v in (r.trend or {}).items() if isinstance(v, (int, float)) and v is not None}
        if len(tr) >= 2 and all(len(str(x)) == 7 for x in tr):
            fig = go.Figure(go.Bar(x=list(tr.keys()), y=list(tr.values())))
            fig.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, width="stretch", key=f"t{r.id}")
        elif r.trend:
            st.json(r.trend)
