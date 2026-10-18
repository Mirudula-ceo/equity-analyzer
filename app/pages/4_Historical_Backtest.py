"""Page 4 - Historical Backtest"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common import setup, universe_symbols  # noqa: E402
from equity.backtest.runner import BIAS_NOTES, run_backtest  # noqa: E402
from equity.config import load_settings  # noqa: E402

setup("Historical Backtest")
cfg = load_settings()
bt = cfg["backtest"]
st.warning("**This is not a full 27-metric backtest.** Free sources do not provide 10 years of point-in-time "
           "fundamentals, historical index membership, shareholding or qualitative data. Every run below is "
           "labelled *partial* and lists its biases.")

strategies = {
    "momentum": "Price momentum 12-1 (price data only, ~10 years)",
    "low_volatility": "Low volatility (price data only, ~10 years)",
    "fundamental": "27-metric score, point-in-time (quantitative metrics only; ~3 years of statements)",
}
c1, c2, c3 = st.columns(3)
strategy = c1.selectbox("Strategy", list(strategies), format_func=strategies.get)
capital = c2.number_input("Starting capital (₹)", 10_000, 100_000_000, int(bt["initial_capital"]), step=50_000)
n = c3.slider("Maximum stocks", 1, 30, int(bt["max_stocks"]))
c4, c5, c6 = st.columns(3)
start = c4.date_input("Start date", pd.Timestamp(bt["start_date"]))
end = c5.date_input("End date (empty = latest)", value=None)
freq = c6.selectbox("Rebalancing", ["Q", "M", "A"], format_func={"Q": "Quarterly", "M": "Monthly", "A": "Annual"}.get)
syms, _ = universe_symbols()

if st.button("Run backtest", type="primary"):
    with st.spinner("Running backtest (fundamental strategy can take a few minutes)..."):
        try:
            out = run_backtest(strategy, syms, {"initial_capital": capital, "max_stocks": n,
                                                "start_date": str(start), "end_date": str(end) if end else None,
                                                "rebalance_frequency": freq}, progress=lambda m: None)
            st.session_state["last_run"] = out["run_id"]
            p = out["report"]["portfolio"]
            st.success(f"Done: ₹{p['start_value']:,.0f} → ₹{p['end_value']:,.0f} "
                       f"(CAGR {p['cagr'] * 100:.2f}%). Saved to {out['folder']}")
            if out["missing_symbols"]:
                st.info(f"No price data for: {', '.join(out['missing_symbols'])}")
            for w in out["warnings"][:20]:
                st.caption("⚠️ " + w)
            st.page_link("pages/5_Performance_Dashboard.py", label="View performance →", icon="📊")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Backtest failed: {exc}")

st.subheader("Assumptions & biases")
st.markdown("\n".join(f"- {b}" for b in BIAS_NOTES))
st.markdown(f"- Signals are computed at the close of each rebalance date; trades execute at the **next day's open**.\n"
            f"- Costs: STT {cfg['costs']['stt_pct']}%, stamp {cfg['costs']['stamp_duty_buy_pct']}% (buy), exchange "
            f"{cfg['costs']['exchange_txn_pct']}%, SEBI ₹{cfg['costs']['sebi_fee_per_crore']}/cr, GST {cfg['costs']['gst_pct']}%, "
            f"DP ₹{cfg['costs']['dp_charge_per_sell']}/sell, slippage {cfg['costs']['slippage_bps']} bps, brokerage "
            f"{cfg['costs']['brokerage_pct']}% (max ₹{cfg['costs']['brokerage_max_per_order']}).\n"
            f"- Equal weight; trims/top-ups smaller than {bt.get('tolerance', 0.02) * 100:.0f}% of the portfolio are skipped. No leverage, no F&O.")
