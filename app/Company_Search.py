"""Page 1 - Company Search"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import con, setup, universe_symbols
from equity import db
from equity.data.collect import fetch_fundamentals, fetch_prices

setup("Company Search")
st.caption("Local Indian-equity research tool · data from Yahoo Finance (unofficial) · "
           "for research only, not investment advice.")

syms, uni = universe_symbols()
c1, c2 = st.columns([2, 1])
with c1:
    typed = st.text_input("Type an NSE symbol (e.g. TCS, RELIANCE, HDFCBANK)",
                          value=st.session_state.get("symbol", "")).strip().upper()
with c2:
    st.write("")
    st.write("")
    fetch = st.button("Download / refresh data", type="primary", disabled=not typed)

if fetch and typed:
    c = con()
    with st.spinner(f"Downloading {typed} from Yahoo Finance..."):
        n = fetch_prices(c, typed)
        rep = fetch_fundamentals(c, typed) if n else {}
    c.close()
    if n:
        st.success(f"{typed}: {n:,} price days; statements: {rep}")
        st.session_state["symbol"] = typed
    else:
        st.error(f"No price data returned for {typed}.NS - check the symbol (see fetch log on Data Quality page).")

matches = [s for s in syms if typed and typed in s] if typed else syms
if typed and typed in syms:
    st.session_state["symbol"] = typed

sym = st.session_state.get("symbol")
if sym:
    c = con()
    px = db.read_prices(c, [sym])
    info = db.read_info(c, sym)
    fin = db.read_financials(c, sym)
    c.close()
    if px.empty:
        st.info(f"No stored data for {sym}. Click **Download / refresh data**.")
    else:
        st.subheader(f"{info.get('longName') or sym}  ·  {sym}")
        m = st.columns(5)
        last = px.sort_values("date").iloc[-1]
        m[0].metric("Last close (₹)", f"{last['close']:,.2f}", help=f"on {last['date'].date()}")
        m[1].metric("Sector", info.get("sector") or "n/a")
        m[2].metric("Industry", (info.get("industry") or "n/a")[:28])
        m[3].metric("Price history", f"{px['date'].min().year}–{px['date'].max().year}")
        yrs = fin[fin["freq"] == "annual"]["period_end"].nunique() if not fin.empty else 0
        m[4].metric("Annual statements", f"{yrs} yrs")
        rng = st.radio("Range", ["1Y", "3Y", "5Y", "All"], index=2, horizontal=True)
        p = px.sort_values("date")
        if rng != "All":
            p = p[p["date"] >= p["date"].max() - pd.DateOffset(years=int(rng[0]))]
        fig = go.Figure(go.Scatter(x=p["date"], y=p["close"], mode="lines", name="Close (traded price)",
                                   line=dict(width=1.6)))
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="₹")
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Source: {px['source'].iloc[0]}. Retrieved {px['retrieved_at'].max()}.")
        st.page_link("pages/2_27_Metric_Report.py", label="Open the 27-metric report →", icon="📋")

st.divider()
st.subheader("Universe")
if uni.empty:
    st.info("No universe loaded yet. Run **run_full_pipeline.bat** once to download Nifty 50 data.")
else:
    st.caption(f"Source: {uni['source'].iloc[0]} · as of {uni['as_of'].iloc[0]}")
    st.dataframe(uni[uni["symbol"].isin(matches)][["symbol", "company", "industry"]],
                 hide_index=True, width="stretch", height=300)
