"""Page 8 - Research Inbox: NSE lists, event imports, filings to review, alerts, freshness."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from common import con, setup  # noqa: E402
from equity.research import announcements, events, listings, monitor  # noqa: E402
from equity.research.constants import EVIDENCE_HELP, SOURCE_TYPES  # noqa: E402

setup("Research Inbox")

# ---------------------------------------------------------------- alerts
c = con()
al = monitor.read_alerts(c, unseen_only=True)
st.subheader(f"1. Watchlist alerts ({len(al)} new)")
wl = monitor.watchlist(c)
st.caption("Watchlist: " + (", ".join(wl) if wl else "empty — add companies with the toggle on **Company Research**."))
if not al.empty:
    st.dataframe(al[["created_at", "symbol", "alert_type", "message"]], hide_index=True, width="stretch")
    if st.button("Mark all as seen"):
        monitor.mark_alerts_seen(c)
        st.rerun()

# ---------------------------------------------------------------- NSE lists
st.subheader("2. Official NSE company lists")
summ = listings.listing_summary(c)
if summ.empty:
    st.info("Not loaded yet. The Nifty 50 list is used until then.")
else:
    st.dataframe(summ, hide_index=True, width="stretch")
    st.caption("Counts come from NSE's own files on each refresh — never from a fixed number. "
               "“not in latest NSE list” can mean delisted, suspended, merged or renamed.")
if st.button("Refresh NSE lists and announcements now"):
    with st.spinner("Downloading from NSE..."):
        r1 = listings.refresh_listings(c)
        r2 = announcements.refresh_inbox(c)
    st.write(r1, r2)
    st.caption("If NSE blocks the download, open the same file in your browser, save it into data/manual/ "
               "(EQUITY_L.csv, SME_EQUITY_L.csv, eq_etfseclist.csv, symbolchange.csv) and refresh again.")

# ---------------------------------------------------------------- imports
st.subheader("3. Add reviewed events and research notes")
st.markdown(
    "Fill `data/manual/company_events.csv` (copy `company_events_TEMPLATE.csv`) and "
    "`data/manual/research_coverage.csv`, then upload here or re-run the pipeline. Each row is checked: unknown "
    "symbols, company-name/ISIN mismatches, plans marked as completed and bad dates are rejected with a reason.")
with st.expander("Allowed values"):
    st.markdown("**evidence_status**: " + "; ".join(f"*{k}* – {v}" for k, v in EVIDENCE_HELP.items()))
    st.markdown("**source_type**: " + "; ".join(f"`{k}` – {v}" for k, v in SOURCE_TYPES.items()))
u1, u2 = st.columns(2)
up = u1.file_uploader("company_events.csv", type="csv")
if up is not None and u1.button("Import events"):
    rep = events.import_events(c, pd.read_csv(up, dtype=str))
    st.write(rep.counts)
    st.dataframe(rep.rows, hide_index=True, width="stretch")
up2 = u2.file_uploader("research_coverage.csv", type="csv")
if up2 is not None and u2.button("Import research notes"):
    st.dataframe(events.import_coverage(c, pd.read_csv(up2, dtype=str)), hide_index=True, width="stretch")

# ---------------------------------------------------------------- inbox
st.subheader("4. Exchange filings waiting for review")
state = st.radio("Show", ["to review", "unmatched company", "routine", "reviewed", "all"], horizontal=True)
inbox = announcements.read_inbox(c, state=None if state == "all" else state)
st.caption("Suggested categories come from keywords and are only hints. Read the filing, then add an event row in "
           "your own words. Filings are never scored.")
if inbox.empty:
    st.info("Nothing here.")
else:
    st.dataframe(inbox[["published", "symbol", "suggested_category", "title", "link", "review_state", "guid"]],
                 hide_index=True, width="stretch", height=350,
                 column_config={"link": st.column_config.LinkColumn("Filing")})
    g = st.text_input("Mark a filing as reviewed (paste its guid)")
    if g and st.button("Mark reviewed"):
        announcements.mark_reviewed(c, g.strip())
        st.rerun()

# ---------------------------------------------------------------- verification & freshness
st.subheader("5. Events due for re-verification")
stale = events.stale_verifications(c)
st.dataframe(stale, hide_index=True, width="stretch") if not stale.empty else st.caption("None.")
st.subheader("6. Data freshness")
st.dataframe(monitor.freshness(c), hide_index=True, width="stretch")
c.close()
