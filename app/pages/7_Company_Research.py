"""Page 7 - Company Research: every NSE company - rank, why, valuation, plans, events, latest news, website."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from common import (STATUS_ICON, cached_announcements, cached_news, cached_scan, con, fmt_cr,  # noqa: E402
                    ranking, setup)
from equity import db  # noqa: E402
from equity.research import events as ev_mod  # noqa: E402
from equity.research import listings, monitor, news, service, website  # noqa: E402
from equity.research.constants import EVIDENCE_HELP, SOURCE_TYPES  # noqa: E402

setup("Company Research")
st.caption("Every NSE-listed company: rank, the reasons behind it, valuation, latest news and what the company "
           "plans next — with sources and dates. News and events are research context only: they never change "
           "the 27-metric score or rank.")

c = con()
uni = service.research_universe(c)
c.close()
src = ranking()
table, details = src["table"], src["details"]
if uni.empty:
    st.warning("No companies loaded yet.")
    st.stop()

# ---------------------------------------------------------------- company picker
left, right = st.columns([3, 1])
with left:
    segs = sorted(uni["segment"].dropna().unique())
    seg = st.multiselect("Segments", segs, default=[s for s in segs if s != "ETF"] or segs)
    view = uni[uni["segment"].isin(seg)] if seg else uni
    scored = set(table["symbol"]) if not table.empty else set()
    labels = {r.symbol: f"{r.symbol} — {r.name}" + ("" if r.symbol in scored or r.data_downloaded
                                                     else "  (not scored)") for r in view.itertuples()}
    syms = list(labels)
    default = st.session_state.get("symbol")
    sym = st.selectbox(f"Company — type to search ({len(view):,} listed in the current NSE list, "
                       f"{len(scored & set(syms)):,} scored)", syms,
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

c = con()
ident = listings.company_identity(c, sym)
name = ident.get("name") or sym
rep = None
if src["kind"] == "snapshot":
    rep = service.snapshot_report(c, sym, src)
if rep is None and sym in set(db.symbols_with_prices(c)):
    rep = service.company_report(c, sym, table if src["kind"] == "live" else None,
                                 details if src["kind"] == "live" else None)
aliases = listings.symbol_aliases(c, sym)
evs = ev_mod.read_events(c, sym)
checklist = ev_mod.research_checklist(c, sym)
has_local = sym in set(db.symbols_with_prices(c))
c.close()

st.subheader(f"{name} · {sym}")

# ---------------------------------------------------------------- summary card + why
if rep is None:
    elig = listings.is_eligible(ident) if ident else (False, "")
    st.info(f"**Not scored yet.** {ident.get('segment', '')} · series {ident.get('series') or '-'} · "
            f"ISIN {ident.get('isin') or '-'}. "
            + ("" if elig[0] else f"Reason: {elig[1]}. ")
            + "Scores for every eligible NSE company are refreshed weekly by GitHub Actions; this company was "
              "not in the last run or Yahoo had no data for it. News, website and events below still work.")
    sc = why = val = row = None
    info, prof = {}, {}
else:
    sc, why, val, row = rep["score"], rep["why"], rep["valuation"], rep["rank_row"]
    info, prof = rep["info"], rep.get("profile") or {}
    k = st.columns(6)
    k[0].metric("Company", ident.get("segment") or "-", help=f"ISIN {ident.get('isin') or 'n/a'} · {rep['eligible'][1]}")
    k[1].metric("Price (₹)", f"{val['price']:,.2f}" if val["price"] else "n/a",
                help=f"Close on {pd.Timestamp(val['price_date']).date() if val['price_date'] is not None else 'n/a'}")
    rank_txt = ("not ranked" if row is None or pd.isna(row.get("rank")) else
                f"{int(row['rank'])} / {int(table['eligible'].sum()):,}")
    k[2].metric("Overall rank", rank_txt, help=f"Ranking: {src['label']}")
    k[3].metric("27-metric score", f"{sc['conservative_score'] * 100:.0f}%", help="Points ÷ applicable metrics")
    k[4].metric("Data coverage", f"{sc['coverage'] * 100:.0f}%", help="Evaluated ÷ applicable metrics")
    fresh = rep["freshness"]
    k[5].metric("Last updated", str(fresh["last_updated"].dropna().iloc[0])[:10] if fresh["last_updated"].notna().any()
                else "n/a", help=" · ".join(f"{r.dataset}: {r.state}" for r in fresh.itertuples()))
    bad = fresh[~fresh["state"].astype(str).str.startswith("OK")]
    if not bad.empty:
        st.caption("Data status: " + " · ".join(f"{r.dataset}: {r.state}" for r in bad.itertuples()))

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
        with st.expander(f"🔴 Negative ({len(why['negatives'])}) · 🟡 Watch ({len(why['watch'])})"):
            st.markdown("\n".join(f"- {x}" for x in why["negatives"] + why["watch"]) or "None")
    with cc[2]:
        with st.expander(f"⚪ Missing data ({why['missing']['count']}) — see *Website & Missing Data*"):
            st.markdown("\n".join(f"- #{m['id']} {m['name']}" for m in why["missing"]["metrics"]) or "None")

# ---------------------------------------------------------------- latest developments (timeline + news)
st.markdown("#### Important developments & latest news")
_all_ann = cached_announcements()
ann = _all_ann[_all_ann["symbol"].isin(aliases)] if not _all_ann.empty else _all_ann
heads, news_err = cached_news(name, sym, 30)
top = []
for e in evs.head(3).itertuples():
    top.append(f"- **{e.sort_date}** · timeline · {e.display} *({e.evidence_status})*")
for a in ann.head(3).itertuples():
    top.append(f"- **{pd.Timestamp(a.published).date() if pd.notna(a.published) else ''}** · NSE filing · "
               f"[{a.subject}]({a.link})" + (f" · *{a.category_hint}*" if isinstance(a.category_hint, str) else ""))
for h in heads.head(4).itertuples():
    top.append(f"- **{pd.Timestamp(h.published).date()}** · {h.publisher} · [{h.title}]({h.link}) *(reported)*")
st.markdown("\n".join(top) if top else "Nothing found in the last 30 days." + (f" ({news_err})" if news_err else ""))

tabs = st.tabs(["Overview", "27 Metrics", "Valuation", "Business & Future Plans", "Important Events",
                "Latest News", "Website & Missing Data", "Financials", "Risks", "Sources"])

# ---------------------------------------------------------------- Overview
with tabs[0]:
    o = st.columns(4)
    o[0].metric("Sector", info.get("sector") or "n/a")
    o[1].metric("Industry", (info.get("industry") or "n/a")[:30])
    o[2].metric("Market cap", fmt_cr(val["market_cap"]) if val else "n/a")
    o[3].metric("Listed since", ident.get("listing_date") or "n/a")
    if prof.get("longBusinessSummary"):
        st.markdown(f"**Business (Yahoo Finance profile):** {prof['longBusinessSummary']}")
    if prof.get("fullTimeEmployees"):
        st.caption(f"Employees: {int(float(prof['fullTimeEmployees'])):,} · Website: {prof.get('website') or 'n/a'}")
    if why:
        st.dataframe(why["categories"].assign(share=lambda d: (d["share"] * 100).round(0)), hide_index=True,
                     width="stretch", column_config={"share": st.column_config.ProgressColumn(
                         "Points earned %", min_value=0, max_value=100, format="%.0f")})
        h = rep["history"]
        if len(h) >= 2:
            fig = go.Figure(go.Scatter(x=h["snapshot_date"], y=h["rank"], mode="lines+markers"))
            fig.update_layout(height=240, yaxis_autorange="reversed", yaxis_title="Rank",
                              margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- 27 metrics
with tabs[1]:
    if rep is None:
        st.info("Not scored yet.")
    else:
        df = pd.DataFrame([r.to_dict() for r in rep["results"]])
        t = df[["id", "category", "name", "display", "status", "explanation"]].copy()
        t["status"] = t["status"].map(lambda s: f"{STATUS_ICON[s]} {s}")
        st.dataframe(t, hide_index=True, width="stretch", height=600)

# ---------------------------------------------------------------- Valuation
with tabs[2]:
    if val is None or not val["price"]:
        st.info("No price available.")
    else:
        st.markdown(f"**Price** ₹{val['price']:,.2f} on {pd.Timestamp(val['price_date']).date()} · **Market cap** "
                    f"{fmt_cr(val['market_cap'])} · **Financial data**: latest annual period "
                    f"{', '.join(val['periods'][-1:]) or 'none'}")
        vt = val["table"].copy()
        if not vt.empty:
            vt["status"] = vt["status"].map(lambda s: f"{STATUS_ICON[s]} {s}")
            vt["sector_median"] = vt["sector_median"].map(lambda x: "-" if x is None or x != x else f"{x:,.2f}")
            st.dataframe(vt, hide_index=True, width="stretch")
        st.caption(f"Peers = companies in the same sector ({info.get('sector') or 'n/a'}) in the ranking "
                   f"({src['label']}). " + val["source"])

# ---------------------------------------------------------------- Business & future plans
with tabs[3]:
    st.caption("Plans the company has announced are shown as plans — with their execution status — never as a "
               "running business.")
    if not evs.empty:
        prop = evs[evs["business_stage"] == "proposed"]
        done = evs[evs["business_stage"] == "existing"]
        st.markdown("**Proposed / future plans**\n" + ("\n".join(
            f"- {e.display} *Next milestone: {e.next_milestone or 'n/a'}*" for e in prop.itertuples()) or "- none recorded"))
        st.markdown("**Developments in the existing business**\n" + ("\n".join(
            f"- {e.sort_date}: {e.display}" for e in done.itertuples()) or "- none recorded"))
    st.markdown("**Research checklist** — where we looked and what is still unknown")
    st.dataframe(checklist, hide_index=True, width="stretch")
    st.caption("“Not found” means not found in the sources searched — not proof the company did not do it.")

# ---------------------------------------------------------------- Important events (permanent timeline)
with tabs[4]:
    if evs.empty:
        st.info("No reviewed events on the permanent timeline yet. Use **Latest News** below to pick items, "
                "download them as rows, add your own summary and import them on **Research Inbox**.")
    else:
        c = con()
        for e in evs.itertuples():
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
                srcs = ev_mod.event_sources(c, e.event_id)
                st.markdown("**Sources**\n" + "\n".join(
                    f"- [{s.source_name}]({s.url}) · {SOURCE_TYPES.get(s.source_type, s.source_type)} · published "
                    f"{s.published_date}" for s in srcs.itertuples()))
                hist = ev_mod.event_history(c, e.event_id)
                if len(hist):
                    st.dataframe(hist[["published_date", "field", "old_value", "new_value", "note"]],
                                 hide_index=True, width="stretch")
        c.close()

# ---------------------------------------------------------------- Latest news
with tabs[5]:
    days = st.select_slider("Period", [7, 30, 90, 365], value=30, format_func=lambda d: f"last {d} days")
    if days != 30:
        heads, news_err = cached_news(name, sym, days)
    st.markdown("**Official NSE announcements** (exchange filings)")
    if ann.empty:
        st.caption("None in the stored feed for this company. The feed is refreshed every weekday by GitHub "
                   "Actions and keeps a rolling history.")
    else:
        a2 = ann[ann["published"] >= pd.Timestamp.now() - pd.Timedelta(days=days)] if days < 365 else ann
        st.dataframe(a2[["published", "category_hint", "subject", "link"]], hide_index=True, width="stretch",
                     column_config={"link": st.column_config.LinkColumn("Filing"),
                                    "category_hint": "Suggested category"})
    st.markdown(f"**News headlines** — search for “{news.short_name(name)}” (Google News)")
    if news_err:
        st.warning(news_err)
    elif heads.empty:
        st.caption("No headlines found for this period.")
    else:
        st.dataframe(heads[["published", "publisher", "category_hint", "title", "link"]], hide_index=True,
                     width="stretch", column_config={"link": st.column_config.LinkColumn("Article"),
                                                     "category_hint": "Suggested category"})
        st.caption("Headlines are *Reported / unconfirmed*, may include articles that only mention the company, "
                   "and are never scored. To keep one on the permanent timeline, download it as a row, write your "
                   "own summary, and import on **Research Inbox**.")
        both = pd.concat([ann.head(20), heads], ignore_index=True)
        st.download_button("Download as company_events.csv rows", news.to_event_rows(both, sym).to_csv(index=False),
                           f"{sym}_events_to_review.csv")

# ---------------------------------------------------------------- Website & missing data
with tabs[6]:
    st.caption("For the metrics the numbers cannot answer, this reads the company's own website (homepage and "
               "investor-relations pages) and lists the official documents to check. It does not score them — "
               "a rating still needs someone to read the document.")
    known = prof.get("website") or info.get("website")
    override = website.manual_website(sym)
    url = st.text_input("Company website", value=override.get("website") or known or "",
                        help="From the company's Yahoo Finance profile, or data/manual/company_websites.csv. "
                             "You can type a different address.")
    if url and st.button("Read the company website", type="primary"):
        st.session_state[f"scan_{sym}"] = cached_scan(url, override.get("ir_url") or prof.get("irWebsite"))
    scan = st.session_state.get(f"scan_{sym}")
    results = rep["results"] if rep else []
    if scan:
        st.markdown(f"**{scan['title'] or scan['website']}** — {scan['description'] or ''}")
        st.caption(f"Pages read: {', '.join(scan['pages_read']) or 'none'}")
        for err in scan["errors"]:
            st.warning(err)
    rows = website.evidence_for_missing(results, scan or {}, prof)
    if not results:
        st.info("This company has not been scored yet, so the missing metrics are unknown; the website "
                "documents are listed below.")
    elif not rows:
        st.success("No metric is missing data for this company.")
    for r in rows:
        with st.expander(f"{r['metric']} — {r['status'] if scan else 'press “Read the company website”'}"):
            st.markdown(f"**What to look for:** {r['what_to_read']}")
            if r["proxy"]:
                st.markdown(f"**Indicator (not scored):** {r['proxy']}")
            for d in r["documents"]:
                st.markdown(f"- {d['document']}: [{d['text'] or d['url']}]({d['url']})" + (" (PDF)" if d["pdf"] else ""))
    if scan and scan["docs"]:
        with st.expander(f"All {len(scan['docs'])} documents/pages found on the website"):
            st.dataframe(pd.DataFrame(scan["docs"])[["document", "text", "url", "pdf", "found_on"]], hide_index=True,
                         width="stretch", column_config={"url": st.column_config.LinkColumn("Link")})
    st.caption("After reading a document, record the rating with its source in config/qualitative_evidence.toml "
               "(qualitative metrics) or data/manual/shareholding.csv (promoter holding & pledge). Those are the "
               "only inputs that change the score.")

# ---------------------------------------------------------------- Financials
with tabs[7]:
    c = con()
    fin = db.read_financials(c, sym) if has_local else pd.DataFrame()
    c.close()
    if fin.empty:
        st.info("Detailed statements are stored only for the Nifty 50 companies kept in the local database "
                "(the all-NSE snapshot keeps results, not full statements). Use Company Search → Download on a PC.")
    else:
        keep = ["Total Revenue", "Operating Income", "EBITDA", "Net Income Common Stockholders", "Operating Cash Flow",
                "Capital Expenditure", "Total Debt", "Stockholders Equity", "Cash And Cash Equivalents"]
        a = fin[(fin["freq"] == "annual") & fin["line_item"].isin(keep)]
        p = a.pivot_table(index="line_item", columns=a["period_end"].dt.strftime("%Y-%m"), values="value",
                          aggfunc="first").reindex(keep).dropna(how="all")
        st.dataframe((p / 1e7).round(0), width="stretch")
        st.caption("₹ crore · Yahoo Finance via yfinance")

# ---------------------------------------------------------------- Risks
with tabs[8]:
    if why:
        st.markdown("**Failed metrics**\n" + ("\n".join(f"- {x}" for x in why["negatives"]) or "- none"))
    risk_ev = ev_mod.open_risk_events(evs)
    st.markdown("**Risk events on the timeline**\n" + ("\n".join(
        f"- {e.sort_date}: {e.display} ({e.evidence_status})" for e in risk_ev.itertuples())
        if not risk_ev.empty else "- none recorded"))
    risky_news = heads[heads.get("category_hint", pd.Series(dtype=str)).isin(
        ["Debt / default", "Litigation / regulatory action", "Project delay / cancellation"])] if not heads.empty else heads
    if not risky_news.empty:
        st.markdown("**Risk-type headlines (unconfirmed)**\n" + "\n".join(
            f"- {pd.Timestamp(h.published).date()} · {h.publisher}: [{h.title}]({h.link})" for h in risky_news.itertuples()))
    if why:
        st.markdown(f"**Missing data** — {why['missing']['count']} metric(s) could not be evaluated; the score treats "
                    "them as 0, so the true picture may be better *or* worse.")

# ---------------------------------------------------------------- Sources
with tabs[9]:
    st.markdown(f"- **Company identity:** {ident.get('source', 'n/a')}\n"
                f"- **Scores:** {rep['pit'].source_note() if rep else 'not scored'}\n"
                f"- **Ranking:** {src['label']}" + (f", generated {str(src['as_of'])[:16]}" if src["as_of"] else "")
                + "\n- **Exchange announcements:** NSE corporate-announcement RSS (stored by GitHub Actions)\n"
                  "- **News headlines:** Google News search (headline, publisher, date and link only)")
    if rep:
        st.dataframe(rep["freshness"], hide_index=True, width="stretch")
    if not evs.empty:
        c = con()
        allsrc = pd.concat([ev_mod.event_sources(c, e) for e in evs["event_id"]], ignore_index=True)
        c.close()
        allsrc["source_type"] = allsrc["source_type"].map(lambda s: SOURCE_TYPES.get(s, s))
        st.dataframe(allsrc[["published_date", "source_name", "source_type", "url", "event_id"]], hide_index=True,
                     width="stretch", column_config={"url": st.column_config.LinkColumn("Link")})
