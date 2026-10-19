"""Page 7 - Company Research: rank, why, valuation, business plans, events, risks, sources."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from common import STATUS_ICON, con, fmt_cr, ranked_universe, scorable, setup  # noqa: E402
from equity import db  # noqa: E402
from equity.research import events as ev_mod  # noqa: E402
from equity.research import monitor, service  # noqa: E402
from equity.research.constants import EVIDENCE_HELP, SOURCE_TYPES  # noqa: E402

setup("Company Research")
st.caption("Rank, the reasons behind it, valuation and what the company plans next — every statement with its "
           "source and date. Events are research context only: they never change the 27-metric score or rank.")

c = con()
uni = service.research_universe(c)
c.close()
if uni.empty:
    st.warning("No companies loaded. Run `run_full_pipeline.bat` once.")
    st.stop()

left, right = st.columns([3, 1])
with left:
    seg = st.multiselect("Segments", sorted(uni["segment"].dropna().unique()),
                         default=sorted(uni["segment"].dropna().unique()))
    view = uni[uni["segment"].isin(seg)] if seg else uni
    labels = {r.symbol: f"{r.symbol} — {r.name}" + ("" if r.data_downloaded else "  (no data downloaded)")
              for r in view.itertuples()}
    syms = list(labels)
    default = st.session_state.get("symbol")
    sym = st.selectbox(f"Company ({len(view):,} listed in the current list)", syms,
                       index=syms.index(default) if default in syms else 0, format_func=labels.get)
    st.session_state["symbol"] = sym
with right:
    st.write("")
    c = con()
    on_list = sym in monitor.watchlist(c)
    if st.toggle("On my watchlist", value=on_list, help="Watch-listed companies get rank-change and event alerts."):
        if not on_list:
            monitor.watch(c, sym)
    elif on_list:
        monitor.unwatch(c, sym)
    c.close()

syms_t, label = scorable()
table, details = ranked_universe(syms_t) if syms_t else (pd.DataFrame(), pd.DataFrame())
c = con()
if sym not in set(db.symbols_with_prices(c)):
    ident = service.listings.company_identity(c, sym)
    st.info(f"**{ident.get('name', sym)}** is listed ({ident.get('segment')}, series {ident.get('series')}) but its "
            "prices and statements have not been downloaded, so it has no score yet. Download it on **Company "
            "Search**, or run the pipeline with `--universe NSE_ALL`. Recorded events are still shown below.")
    evs = ev_mod.read_events(c, sym)
    if not evs.empty:
        st.dataframe(evs[["sort_date", "category", "display", "evidence_status"]], hide_index=True, width="stretch")
    st.dataframe(ev_mod.research_checklist(c, sym), hide_index=True, width="stretch")
    c.close()
    st.stop()

rep = service.company_report(c, sym, table, details)
c.close()
sc, why, val, row = rep["score"], rep["why"], rep["valuation"], rep["rank_row"]
info, ident = rep["info"], rep["identity"]

# ---------------------------------------------------------------- summary card
st.subheader(f"{ident.get('name') or info.get('longName') or sym} · {sym}")
k = st.columns(6)
k[0].metric("Company", ident.get("segment") or "-", help=f"ISIN {ident.get('isin') or 'n/a'} · {rep['eligible'][1]}")
k[1].metric("Price (₹)", f"{val['price']:,.2f}" if val["price"] else "n/a",
            help=f"Close on {pd.Timestamp(val['price_date']).date() if val['price_date'] is not None else 'n/a'}")
rank_txt = ("not ranked" if row is None or pd.isna(row.get("rank")) else
            f"{int(row['rank'])} / {int(table['eligible'].sum())}")
k[2].metric("Overall rank", rank_txt, help=f"Universe: {label}, eligible companies with coverage ≥ minimum")
k[3].metric("27-metric score", f"{sc['conservative_score'] * 100:.0f}%", help="Points ÷ applicable metrics")
k[4].metric("Data coverage", f"{sc['coverage'] * 100:.0f}%", help="Evaluated ÷ applicable metrics")
fresh = rep["freshness"]
last_upd = fresh.loc[fresh["dataset"] == "prices", "last_updated"].iloc[0]
k[5].metric("Last updated", str(last_upd)[:10] if last_upd else "n/a",
            help=" · ".join(f"{r.dataset}: {r.state}" for r in fresh.itertuples()))
bad = fresh[fresh["state"] != "OK"]
if not bad.empty:
    st.warning("Data status: " + " · ".join(f"**{r.dataset}** {r.state}" + (f" ({r.last_error[:80]})" if r.last_error
                                             else "") for r in bad.itertuples()))

st.markdown("#### Why this rank?")
for line in why["lines"]:
    st.markdown(f"- {line}")
if not why["check"]["matches"]:
    st.error("The explanation did not reproduce the score — please report this.")
cc = st.columns(3)
with cc[0]:
    with st.expander(f"🟢 Positive factors ({len(why['positives'])})"):
        st.markdown("\n".join(f"- {x}" for x in why["positives"]) or "None")
with cc[1]:
    with st.expander(f"🔴 Negative factors ({len(why['negatives'])}) · 🟡 Watch ({len(why['watch'])})"):
        st.markdown("\n".join(f"- {x}" for x in why["negatives"] + why["watch"]) or "None")
with cc[2]:
    with st.expander(f"⚪ Missing data ({why['missing']['count']})"):
        st.markdown("\n".join(f"- #{m['id']} {m['name']}: {m['why']}" for m in why["missing"]["metrics"]) or "None")

st.markdown("#### Important developments")
evs = rep["events"]
if evs.empty:
    st.caption("No events recorded for this company yet. Add reviewed rows to `data/manual/company_events.csv` "
               "(template next to it) and re-run the pipeline, or import on the **Research Inbox** page.")
else:
    for e in evs.head(5).itertuples():
        st.markdown(f"- **{e.sort_date}** · {e.category} · {e.display} *({e.evidence_status}; {e.sources} source(s))*")

tabs = st.tabs(["Overview", "27 Metrics", "Valuation", "Business & Future Plans", "Important Events",
                "Financials", "Risks", "Sources"])

# ---------------------------------------------------------------- Overview
with tabs[0]:
    o = st.columns(4)
    o[0].metric("Sector", info.get("sector") or "n/a")
    o[1].metric("Industry", (info.get("industry") or "n/a")[:30])
    o[2].metric("Market cap", fmt_cr(val["market_cap"]))
    o[3].metric("Listed since", ident.get("listing_date") or "n/a")
    st.dataframe(why["categories"].assign(share=lambda d: (d["share"] * 100).round(0)), hide_index=True,
                 width="stretch", column_config={"share": st.column_config.ProgressColumn(
                     "Points earned %", min_value=0, max_value=100, format="%.0f")})
    h = rep["history"]
    if len(h) >= 2:
        fig = go.Figure(go.Scatter(x=h["snapshot_date"], y=h["rank"], mode="lines+markers"))
        fig.update_layout(height=240, yaxis_autorange="reversed", yaxis_title="Rank",
                          margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")
    elif why["change"] is None:
        st.caption("Rank history appears after the pipeline has saved two ranking snapshots.")

# ---------------------------------------------------------------- 27 metrics
with tabs[1]:
    df = pd.DataFrame([r.to_dict() for r in rep["results"]])
    t = df[["id", "category", "name", "display", "status", "explanation"]].copy()
    t["status"] = t["status"].map(lambda s: f"{STATUS_ICON[s]} {s}")
    st.dataframe(t, hide_index=True, width="stretch", height=600)
    st.page_link("pages/2_27_Metric_Report.py", label="Formulas, inputs and trends → 27-Metric Report", icon="📋")

# ---------------------------------------------------------------- Valuation
with tabs[2]:
    st.markdown(f"**Price** ₹{val['price']:,.2f} on {pd.Timestamp(val['price_date']).date()} · **Market cap** "
                f"{fmt_cr(val['market_cap'])} · **Financial data**: annual periods {', '.join(val['periods']) or 'none'}"
                if val["price"] else "No price available.")
    vt = val["table"].copy()
    if not vt.empty:
        vt["status"] = vt["status"].map(lambda s: f"{STATUS_ICON[s]} {s}")
        vt["sector_median"] = vt["sector_median"].map(lambda x: "-" if x is None or x != x else f"{x:,.2f}")
        st.dataframe(vt, hide_index=True, width="stretch")
    st.caption(f"Peers = companies in the same sector ({info.get('sector') or 'n/a'}) within the scored universe; "
               "median of evaluated values. " + val["source"])

# ---------------------------------------------------------------- Business & future plans
with tabs[3]:
    st.caption("Plans the company has announced are shown as plans — with their execution status — never as a "
               "running business.")
    if not evs.empty:
        prop = evs[(evs["business_stage"] == "proposed")]
        done = evs[(evs["business_stage"] == "existing")]
        st.markdown("**Proposed / future plans**")
        st.markdown("\n".join(f"- {e.display} *Next milestone: {e.next_milestone or 'n/a'}*" for e in prop.itertuples())
                    or "None recorded.")
        st.markdown("**Developments in the existing business**")
        st.markdown("\n".join(f"- {e.sort_date}: {e.display}" for e in done.itertuples()) or "None recorded.")
    st.markdown("**Research checklist** — where we looked and what is still unknown")
    st.dataframe(rep["checklist"], hide_index=True, width="stretch")
    st.caption("“Not found” means not found in the sources searched — it is not proof the company did not do it.")

# ---------------------------------------------------------------- Important events
with tabs[4]:
    if evs.empty:
        st.info("No events recorded yet.")
    else:
        f1, f2, f3 = st.columns(3)
        cats = f1.multiselect("Category", sorted(evs["category"].unique()))
        stats = f2.multiselect("Status", sorted(evs["status"].dropna().unique()))
        yrs = pd.to_datetime(evs["sort_date"]).dt.year
        yr = f3.slider("Years", int(yrs.min()), int(yrs.max()), (int(yrs.min()), int(yrs.max()))) \
            if yrs.min() != yrs.max() else (int(yrs.min()), int(yrs.max()))
        e2 = evs[(yrs >= yr[0]) & (yrs <= yr[1])]
        if cats:
            e2 = e2[e2["category"].isin(cats)]
        if stats:
            e2 = e2[e2["status"].isin(stats)]
        c = con()
        for e in e2.itertuples():
            with st.expander(f"{e.sort_date} · {e.category} · {e.title} — {e.status} ({e.evidence_status})"):
                st.markdown(f"{e.display}\n\n**Summary:** {e.summary}")
                st.markdown(
                    f"| | |\n|---|---|\n| Event date | {e.event_date or 'n/a'} |\n| Published | {e.published_date} |\n"
                    f"| First captured | {e.first_captured} |\n| Last verified | {e.last_verified} |\n"
                    f"| Business relevance | {e.business_relevance or '-'} |\n| Segment | {e.segment_affected or '-'} |\n"
                    f"| Existing / proposed | {e.business_stage} |\n| Financial link | {e.financial_link or '-'} |\n"
                    f"| Impact quantified | {'yes: ' + (e.impact_note or '') if e.impact_quantified else 'no'} |\n"
                    f"| Already in 27-metric data | {e.reflected_in_metrics} |\n"
                    f"| Expected timeline | {e.expected_timeline or '-'} |\n| Next milestone | {e.next_milestone or '-'} |\n"
                    f"| Evidence | {e.evidence_status} — {EVIDENCE_HELP.get(e.evidence_status, '')} |\n"
                    f"| Event id | `{e.event_id}` |")
                src = ev_mod.event_sources(c, e.event_id)
                st.markdown("**Sources**\n" + "\n".join(
                    f"- [{s.source_name}]({s.url}) · {SOURCE_TYPES.get(s.source_type, s.source_type)} · published "
                    f"{s.published_date}" for s in src.itertuples()))
                hist = ev_mod.event_history(c, e.event_id)
                if len(hist):
                    st.dataframe(hist[["published_date", "field", "old_value", "new_value", "note"]],
                                 hide_index=True, width="stretch")
        c.close()

# ---------------------------------------------------------------- Financials
with tabs[5]:
    c = con()
    fin = db.read_financials(c, sym)
    c.close()
    if fin.empty:
        st.info("No statements stored.")
    else:
        keep = ["Total Revenue", "Operating Income", "EBITDA", "Net Income Common Stockholders", "Operating Cash Flow",
                "Capital Expenditure", "Total Debt", "Stockholders Equity", "Cash And Cash Equivalents"]
        a = fin[(fin["freq"] == "annual") & fin["line_item"].isin(keep)]
        p = a.pivot_table(index="line_item", columns=a["period_end"].dt.strftime("%Y-%m"), values="value",
                          aggfunc="first").reindex(keep).dropna(how="all")
        st.dataframe((p / 1e7).round(0), width="stretch")
        st.caption("₹ crore · " + rep["pit"].source_note())

# ---------------------------------------------------------------- Risks
with tabs[6]:
    st.markdown("**Failed metrics**\n" + ("\n".join(f"- {x}" for x in why["negatives"]) or "- none"))
    risk_ev = ev_mod.open_risk_events(evs)
    st.markdown("**Risk events (debt, litigation, delays, cancellations)**\n" +
                ("\n".join(f"- {e.sort_date}: {e.display} ({e.evidence_status})" for e in risk_ev.itertuples())
                 if not risk_ev.empty else "- none recorded"))
    st.markdown(f"**Missing data** — {why['missing']['count']} metric(s) could not be evaluated; the score treats "
                "them as 0, so the true picture may be better *or* worse.")

# ---------------------------------------------------------------- Sources
with tabs[7]:
    st.markdown(f"- **Company identity:** {ident.get('source', 'n/a')}\n"
                f"- **Prices & statements:** {rep['pit'].source_note()}\n"
                f"- **Valuation snapshot:** Yahoo Finance info retrieved {info.get('_retrieved_at', 'n/a')}")
    st.dataframe(rep["freshness"], hide_index=True, width="stretch")
    if not evs.empty:
        c = con()
        allsrc = pd.concat([ev_mod.event_sources(c, e) for e in evs["event_id"]], ignore_index=True)
        c.close()
        allsrc["source_type"] = allsrc["source_type"].map(lambda s: SOURCE_TYPES.get(s, s))
        st.dataframe(allsrc[["published_date", "source_name", "source_type", "url", "event_id"]], hide_index=True,
                     width="stretch", column_config={"url": st.column_config.LinkColumn("Link")})
