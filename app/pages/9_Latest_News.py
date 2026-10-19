"""Page 9 - Latest News: official NSE announcements for all companies + headlines for chosen companies."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common import cached_announcements, cached_news, con, ranking, setup  # noqa: E402
from equity.research import monitor  # noqa: E402
from equity.research.announcements import HINT_LABELS  # noqa: E402

setup("Latest News")
st.caption("Official exchange announcements for every NSE company, plus news headlines for the companies you "
           "choose. Context only — never part of a score or rank.")

src = ranking()
t = src["table"]
ann = cached_announcements()

st.subheader("NSE announcements — all companies")
if ann.empty:
    st.info("No announcements stored yet. GitHub Actions saves NSE's announcement feed every weekday morning "
            "(Actions → Refresh NSE data → Run workflow to fetch now).")
else:
    a, b, c3, d = st.columns(4)
    days = a.select_slider("Period", [1, 3, 7, 30, 90, 365], value=7, format_func=lambda x: f"last {x} days")
    cats = b.multiselect("Category", HINT_LABELS)
    only_top = c3.number_input("Only top-N ranked companies (0 = all)", 0, 5000, 0)
    hide_routine = d.checkbox("Hide routine / unclassified filings", value=True)
    listed_only = d.checkbox("Listed equity companies only", value=True,
                             help="Hides debt issuers, mutual funds and trusts that also file with NSE")
    x = ann[ann["published"] >= pd.Timestamp.now() - pd.Timedelta(days=days)]
    if cats:
        x = x[x["category_hint"].isin(cats)]
    if hide_routine:
        x = x[x["category_hint"].notna()]
    if listed_only:
        x = x[x["symbol"].notna()]
    if only_top and not t.empty:
        topset = set(t[t["rank"].notna()].sort_values("rank").head(int(only_top))["symbol"])
        x = x[x["symbol"].isin(topset)]
    rk = dict(zip(t["symbol"], t["rank"])) if not t.empty else {}
    x = x.assign(rank=x["symbol"].map(rk))
    st.caption(f"{len(x):,} announcements · stored feed covers {ann['published'].min():%d %b %Y} – "
               f"{ann['published'].max():%d %b %Y %H:%M}")
    st.dataframe(x[["published", "symbol", "rank", "company", "category_hint", "subject", "link"]], hide_index=True,
                 width="stretch", height=480,
                 column_config={"link": st.column_config.LinkColumn("Filing"), "category_hint": "Suggested category",
                                "rank": st.column_config.NumberColumn("Rank", format="%d")})
    pick = st.selectbox("Open a company's research page", [""] + sorted(x["symbol"].dropna().unique().tolist()))
    if pick:
        st.session_state["symbol"] = pick
        st.switch_page("pages/7_Company_Research.py")

st.subheader("News headlines")
c = con()
wl = monitor.watchlist(c)
c.close()
mode = st.radio("Companies", ["My watchlist", "Top-ranked"], horizontal=True)
if mode == "Top-ranked":
    n = st.number_input("How many top-ranked companies", 1, 25, 5)
    chosen = t[t["rank"].notna()].sort_values("rank").head(int(n))["symbol"].tolist() if not t.empty else []
else:
    chosen = wl
    if not wl:
        st.caption("Your watchlist is empty — add companies with the toggle on **Company Research**.")
names = dict(zip(t["symbol"], t["name"])) if not t.empty else {}
if chosen and st.button(f"Fetch headlines for {len(chosen)} companies"):
    allh, errs = [], []
    for s in chosen:
        h, err = cached_news(names.get(s, s), s, 7)
        if err:
            errs.append(f"{s}: {err}")
        allh.append(h.assign(symbol=s))
    for e in errs[:3]:
        st.warning(e)
    h = pd.concat(allh, ignore_index=True) if allh else pd.DataFrame()
    if h.empty:
        st.caption("No headlines in the last 7 days.")
    else:
        st.dataframe(h.sort_values("published", ascending=False)[["published", "symbol", "publisher", "category_hint",
                                                                   "title", "link"]],
                     hide_index=True, width="stretch", height=480,
                     column_config={"link": st.column_config.LinkColumn("Article")})
        st.caption("Headlines are reported/unconfirmed and may only mention the company in passing.")
