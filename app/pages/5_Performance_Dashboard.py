"""Page 5 - Performance Dashboard"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from common import setup  # noqa: E402
from equity.backtest.performance import drawdown, period_returns  # noqa: E402
from equity.config import REPORTS_DIR  # noqa: E402

setup("Performance Dashboard")
root = REPORTS_DIR / "backtests"
runs = sorted([p for p in root.glob("*") if (p / "summary.json").exists()], reverse=True) if root.exists() else []
if not runs:
    st.info("No backtests yet. Run one on the **Historical Backtest** page.")
    st.stop()


def label(p):
    s = json.loads((p / "summary.json").read_text(encoding="utf-8"))
    return f"{p.name} · {s['label']}"


default = next((i for i, p in enumerate(runs) if p.name == st.session_state.get("last_run")), 0)
run = st.selectbox("Backtest run", runs, index=default, format_func=label)
s = json.loads((run / "summary.json").read_text(encoding="utf-8"))
eq = pd.read_csv(run / "equity_curve.csv", parse_dates=["date"], index_col="date")
tx = pd.read_csv(run / "transactions.csv", parse_dates=["date", "signal_date"]) if (run / "transactions.csv").stat().st_size > 2 else pd.DataFrame()
hold = pd.read_csv(run / "holdings.csv") if (run / "holdings.csv").stat().st_size > 2 else pd.DataFrame()
fp = pd.read_csv(run / "final_positions.csv") if (run / "final_positions.csv").stat().st_size > 2 else pd.DataFrame()
r = s["report"]
p, b, rel, t = r["portfolio"], r.get("benchmark", {}), r.get("relative", {}), r["trades"]

st.error("PARTIAL BACKTEST – " + s["label"] + ". Not a validated test of the full 27-metric strategy. "
         "Universe = current Nifty 50 members (survivorship bias).")

f = lambda x: "n/a" if x is None or pd.isna(x) else f"{x * 100:.2f}%"  # noqa: E731
k = st.columns(6)
k[0].metric("Final value", f"₹{p['end_value']:,.0f}", f"{f(p['total_return'])} total")
k[1].metric("CAGR", f(p["cagr"]), f"{f(rel.get('excess_cagr'))} vs benchmark" if rel else None)
k[2].metric("Max drawdown", f(p["max_drawdown"]))
k[3].metric("Volatility", f(p["volatility"]))
k[4].metric("Sharpe", f"{p['sharpe']:.2f}")
k[5].metric("Sortino", f"{p['sortino']:.2f}")
k = st.columns(6)
k[0].metric("Benchmark CAGR", f(b.get("cagr")))
k[1].metric("Win rate (sells)", f(t.get("win_rate")))
k[2].metric("Avg gain / loss", f"{f(t.get('avg_gain_pct'))} / {f(t.get('avg_loss_pct'))}")
k[3].metric("Turnover / yr", f"{r['turnover']:.2f}x")
k[4].metric("Costs + slippage", f"₹{r['costs']['charges_total'] + r['costs']['slippage_total']:,.0f}")
k[5].metric("Dividends", f"₹{r['dividends_received']:,.0f}")
st.caption(f"Realised P&L ₹{r['pnl']['realized']:,.0f} · Unrealised ₹{r['pnl']['unrealized']:,.0f} · "
           f"Period {p['start']} → {p['end']} · Benchmark: {s['benchmark']} · Risk-free {s['params']['risk_free_rate'] * 100:.1f}%")

fig = go.Figure()
fig.add_scatter(x=eq.index, y=eq["value"], name="Portfolio")
if "benchmark" in eq and eq["benchmark"].notna().any():
    fig.add_scatter(x=eq.index, y=eq["benchmark"], name="Benchmark (Nifty 50 TR proxy)")
fig.update_layout(height=400, title="Equity curve (₹)", margin=dict(l=10, r=10, t=40, b=10))
st.plotly_chart(fig, width="stretch")

c1, c2 = st.columns(2)
dd = drawdown(eq["value"])
fig = go.Figure(go.Scatter(x=dd.index, y=dd * 100, fill="tozeroy", name="Portfolio"))
if "benchmark" in eq and eq["benchmark"].notna().any():
    bdd = drawdown(eq["benchmark"].dropna())
    fig.add_scatter(x=bdd.index, y=bdd * 100, name="Benchmark")
fig.update_layout(height=320, title="Drawdown (%)", margin=dict(l=10, r=10, t=40, b=10))
c1.plotly_chart(fig, width="stretch")
yr = period_returns(eq["value"], "Y")
ann = pd.DataFrame({"Portfolio": yr * 100})
if "benchmark" in eq and eq["benchmark"].notna().any():
    ann["Benchmark"] = period_returns(eq["benchmark"].dropna(), "Y") * 100
ann.index = ann.index.astype(str)
c2.plotly_chart(px.bar(ann, barmode="group", title="Annual returns (%)", height=320), width="stretch")

mr = period_returns(eq["value"], "M")
heat = pd.DataFrame({"y": mr.index.year, "m": mr.index.month, "r": mr.values * 100}).pivot(index="y", columns="m", values="r")
fig = px.imshow(heat, text_auto=".1f", color_continuous_scale="RdYlGn", color_continuous_midpoint=0,
                aspect="auto", title="Monthly returns (%)", labels=dict(x="Month", y="Year"))
st.plotly_chart(fig, width="stretch")

st.subheader("Holdings by rebalance date")
if not hold.empty:
    dates = sorted(hold["rebalance_date"].unique(), reverse=True)
    d = st.selectbox("Rebalance date", dates)
    h = hold[hold["rebalance_date"] == d].copy()
    h["weight"] = (h["weight"] * 100).round(2)
    st.dataframe(h, hide_index=True, width="stretch")
st.subheader("Open positions at end (unrealised)")
st.dataframe(fp, hide_index=True, width="stretch")
st.subheader("Complete transaction history")
st.dataframe(tx, hide_index=True, width="stretch", height=400)
if not tx.empty:
    st.download_button("Download transactions CSV", tx.to_csv(index=False), "transactions.csv")
with st.expander("Warnings and biases"):
    for w in s["warnings"]:
        st.write("⚠️ " + w)
    for n in s["bias_notes"]:
        st.write("• " + n)
