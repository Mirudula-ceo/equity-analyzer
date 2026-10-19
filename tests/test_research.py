"""Tests for the Company Research, Important Events & Ranking module.
All companies, filings and numbers here are SYNTHETIC test inputs."""
import io
import json

import pandas as pd
import pytest

from conftest import flat_prices, good_company_year, make_fin
from equity import db
from equity.analysis import analyze_symbol
from equity.metrics.engine import FAIL, INSUFFICIENT, PASS, WATCH, MetricEngine
from equity.metrics.pit import PITData
from equity.metrics.scoring import rank, score
from equity.research import announcements, events, explain, listings, monitor, service
from equity.research.constants import NOT_IN_LATEST

TODAY = "2026-10-19"


# ---------------------------------------------------------------- fixtures
def csv(text):
    return pd.read_csv(io.StringIO(text))


MAINBOARD = csv("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
                "ACME,Acme Motors Limited,EQ,22-JUL-1998,1,1,INE000A01011,2\n"
                "BETA,Beta Pharma Limited,EQ,08-FEB-1995,5,1,INE000B01011,5\n"
                "GAMMA,Gamma Steel Limited,BE,01-JAN-2010,5,1,INE000C01011,10\n")
SME = csv("SYMBOL,NAME_OF_COMPANY,SERIES,DATE_OF_LISTING,PAID_UP_VALUE,MARKET_LOT,ISIN_NUMBER,FACE_VALUE\n"
          "TINY,Tiny Foods Limited,SM,05-03-2024,10,1000,INE000D01011,10\n")
ETF = csv("Symbol,Underlying,SecurityName,DateofListing,MarketLot,ISINNumber,FaceValue\n"
          "NIFTYX,Nifty 50 Index,Example Nifty ETF,2015-01-02,1,INF000X01011,10\n")


@pytest.fixture
def con(cfg):
    c = db.connect(":memory:")
    listings.apply_listing(c, listings.parse_listing(MAINBOARD, "MAINBOARD"), "MAINBOARD", "test")
    listings.apply_listing(c, listings.parse_listing(SME, "SME"), "SME", "test")
    listings.apply_listing(c, listings.parse_listing(ETF, "ETF"), "ETF", "test")
    yield c
    c.close()


def ev(**kw):
    base = dict(symbol="ACME", title="Acme plans electric two-wheeler launch", category="Emerging business plan",
                published_date="2025-03-01", status="Announced", evidence_status="Management guidance",
                summary="Own-words summary.", source_name="Acme IR", source_type="investor_relations",
                source_url="https://acme.example/ir/1", expected_timeline="2028-2030")
    base.update(kw)
    return base


def imp(con, *rows):
    return events.import_events(con, pd.DataFrame(list(rows)), today=TODAY).rows


# ---------------------------------------------------------------- section 1: listings
def test_listing_parsers_handle_each_nse_file_format():
    m = listings.parse_listing(MAINBOARD, "MAINBOARD")
    assert m["symbol"].tolist() == ["ACME", "BETA", "GAMMA"]
    assert m.loc[0, "isin"] == "INE000A01011" and m.loc[0, "listing_date"] == "1998-07-22"
    s = listings.parse_listing(SME, "SME")
    assert s.loc[0, "listing_date"] == "2024-03-05" and s.loc[0, "series"] == "SM"
    e = listings.parse_listing(ETF, "ETF")
    assert e.loc[0, "name"] == "Example Nifty ETF" and e.loc[0, "listing_date"] == "2015-01-02"


def test_company_count_comes_from_the_list_not_a_constant(con):
    s = listings.listing_summary(con)
    assert s["companies"].sum() == 5
    bigger = pd.concat([MAINBOARD, csv("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, "
                                       "ISIN NUMBER, FACE VALUE\nNEWCO,New Co Limited,EQ,01-OCT-2026,1,1,INE9,1\n")])
    r = listings.apply_listing(con, listings.parse_listing(bigger, "MAINBOARD"), "MAINBOARD", "test")
    assert r["listed"] == 4 and r["new_since_last_refresh"] == 1 and r["new_symbols"] == ["NEWCO"]


def test_dropped_symbol_is_flagged_and_first_seen_is_kept(con):
    first = con.execute("SELECT first_seen FROM nse_listings WHERE symbol='ACME'").fetchone()[0]
    r = listings.apply_listing(con, listings.parse_listing(MAINBOARD.iloc[:1], "MAINBOARD"), "MAINBOARD", "t2",
                               now="2030-01-01T00:00:00Z")
    assert r["dropped_symbols"] == ["BETA", "GAMMA"]
    st = dict(con.execute("SELECT symbol, status FROM nse_listings WHERE segment='MAINBOARD'").fetchall())
    assert st["BETA"] == NOT_IN_LATEST and st["ACME"] == "active"
    assert con.execute("SELECT first_seen FROM nse_listings WHERE symbol='ACME'").fetchone()[0] == first


def test_failed_download_keeps_previous_list_and_uses_manual_copy(con, cfg, tmp_path):
    def blocked(*a, **k):
        raise ConnectionError("blocked")
    r = listings.refresh_listings(con, cfg, get=blocked, manual_dir=tmp_path)
    assert "unavailable" in r["MAINBOARD"]["status"]
    assert listings.listing_summary(con)["companies"].sum() == 5          # nothing wiped
    MAINBOARD.to_csv(tmp_path / "EQUITY_L.csv", index=False)
    r = listings.refresh_listings(con, cfg, get=blocked, manual_dir=tmp_path)
    assert r["MAINBOARD"]["listed"] == 3 and "manual copy" in r["MAINBOARD"]["source"]


def test_eligibility_rules(con, cfg):
    assert set(listings.eligible_symbols(con, cfg)) == {"ACME", "BETA", "GAMMA"}   # SME/ETF listed but not scored
    ok, why = listings.is_eligible({"segment": "SME", "series": "SM", "status": "active"}, cfg)
    assert not ok and "not scored" in why
    ok, _ = listings.is_eligible({"segment": "MAINBOARD", "series": "IL", "status": "active"}, cfg)
    assert not ok


def test_symbol_change_resolution(con):
    ch = listings.parse_symbol_changes(csv("SM_KEY_SYMBOL,SM_NAME,SM_NEW_SYMBOL,SM_APPLICABLE_FROM\n"
                                           "OLDACME,Acme Motors,ACME,14-OCT-2025\n"))
    ch["source"], ch["retrieved_at"] = "t", "t"
    db.upsert_df(con, "symbol_changes", ch)
    assert listings.resolve_symbol(con, "OLDACME") == "ACME"
    assert listings.symbol_aliases(con, "ACME") == ["ACME", "OLDACME"]
    r = imp(con, ev(symbol="OLDACME"))
    assert r.loc[0, "action"] == "created" and r.loc[0, "symbol"] == "ACME" and "changed to ACME" in r.loc[0, "message"]


# ---------------------------------------------------------------- sections 3-5: events
def test_identity_checks_reject_wrong_company(con):
    r = imp(con, ev(company_name_in_source="Zeta Pharma Ltd"),
            ev(isin_in_source="INE999Z01011", source_url="https://x/2"),
            ev(symbol="NOPE", source_url="https://x/3"),
            ev(company_name_in_source="ACME MOTORS LTD.", source_url="https://x/4"))
    assert r["action"].tolist() == ["rejected", "rejected", "rejected", "created"]
    assert "identity mismatch" in r.loc[0, "message"] and "ISIN" in r.loc[1, "message"]
    assert "unknown symbol" in r.loc[2, "message"]


def test_future_plan_cannot_be_shown_as_running_business(con):
    r = imp(con, ev(status="Completed", business_stage="proposed", evidence_status="Confirmed"),
            ev(status="Completed", source_url="https://x/2"),
            ev(status="Completed", evidence_status="Confirmed", event_date="2029-01-01", source_url="https://x/3"))
    assert (r["action"] == "rejected").all()
    imp(con, ev())
    e = events.read_events(con, "ACME").iloc[0]
    assert e["business_stage"] == "proposed"
    assert e["display"].startswith("Management announced a proposed plan") and "2028-2030" in e["display"]
    assert "current execution status: Announced" in e["display"]


def test_bad_values_are_rejected_with_reasons(con):
    r = imp(con, ev(category="Rumour"), ev(evidence_status="Not found", source_url="https://x/2"),
            ev(published_date="2099-01-01", source_url="https://x/3"), ev(source_url="ftp://x"),
            ev(event_ref="EVdoesnotexist", source_url="https://x/5"), ev(summary=None, source_url="https://x/6"))
    assert (r["action"] == "rejected").all()
    assert "research_coverage" in r.loc[1, "message"]


def test_same_story_from_many_sources_is_one_event_and_updates_attach(con):
    r = imp(con, ev(),
            ev(title="Acme to launch electric two wheeler", source_name="Mint", source_type="financial_publication",
               source_url="https://mint.example/a", evidence_status="Reported / unconfirmed",
               published_date="2025-03-02"),
            ev(published_date="2026-02-01", status="Delayed", source_url="https://acme.example/ir/2",
               expected_timeline="2030-2031"))
    assert r["action"].tolist() == ["created", "merged", "updated"]
    assert r["event_id"].nunique() == 1
    e = events.read_events(con, "ACME")
    assert len(e) == 1 and e.loc[0, "sources"] == 3
    assert e.loc[0, "status"] == "Delayed" and e.loc[0, "expected_timeline"] == "2030-2031"
    assert e.loc[0, "business_stage"] == "proposed"           # a news row with no stage did not overwrite it
    h = events.event_history(con, e.loc[0, "event_id"])
    assert ("status", "Announced", "Delayed") in set(zip(h["field"], h["old_value"], h["new_value"]))
    # re-importing the same file is harmless
    again = imp(con, ev())
    assert again.loc[0, "action"] == "merged" and len(events.read_events(con, "ACME")) == 1


def test_older_source_does_not_override_newer_status(con):
    imp(con, ev(published_date="2026-02-01", status="Delayed"))
    r = imp(con, ev(published_date="2025-01-01", status="Announced", source_url="https://old.example"))
    assert r.loc[0, "action"] == "merged"
    assert events.read_events(con, "ACME").loc[0, "status"] == "Delayed"


def test_evidence_upgrades_only_with_official_source(con):
    imp(con, ev(evidence_status="Reported / unconfirmed", source_type="financial_publication",
                category="Acquisition / merger", title="Acme buys Delta"))
    imp(con, ev(evidence_status="Confirmed", source_type="exchange_filing", category="Acquisition / merger",
                title="Acme buys Delta", source_url="https://nse.example/f"))
    assert events.read_events(con, "ACME").loc[0, "evidence_status"] == "Confirmed"
    imp(con, ev(evidence_status="Reported / unconfirmed", source_type="financial_publication",
                category="Acquisition / merger", title="Acme buys Delta", source_url="https://news.example/z"))
    assert events.read_events(con, "ACME").loc[0, "evidence_status"] == "Confirmed"


def test_point_in_time_view_has_no_look_ahead(con):
    imp(con, ev(), ev(published_date="2026-02-01", status="Delayed", source_url="https://acme.example/ir/2",
                      expected_timeline="2030-2031"))
    assert events.read_events(con, "ACME", as_of="2025-02-28").empty
    mid = events.read_events(con, "ACME", as_of="2025-06-30").iloc[0]
    assert mid["status"] == "Announced" and mid["expected_timeline"] == "2028-2030"
    assert events.read_events(con, "ACME", as_of="2026-06-30").iloc[0]["status"] == "Delayed"


def test_not_found_is_not_reported_as_did_not_happen(con):
    cov = pd.DataFrame([{"symbol": "ACME", "topic": "Acquisition / merger", "source_type": "exchange_filing",
                         "searched_on": "2026-10-01", "result": "Not found", "note": "2016-2026"},
                        {"symbol": "ACME", "topic": "Order / contract win", "source_type": "official_website",
                         "result": "Not applicable"},
                        {"symbol": "ACME", "topic": "Bogus", "source_type": "official_website", "result": "x"}])
    out = events.import_coverage(con, cov, today=TODAY)
    assert out["action"].tolist() == ["recorded", "recorded", "rejected"]
    imp(con, ev())
    ck = events.research_checklist(con, "ACME").set_index("topic")
    assert "not proof" in ck.loc["Acquisition / merger", "verdict"]
    assert ck.loc["Order / contract win", "verdict"] == "Not applicable"
    assert ck.loc["Emerging business plan", "verdict"].startswith("Found: 1")
    assert ck.loc["Divestment", "verdict"] == "Not yet researched"


def test_stale_verification_listed(con, cfg):
    imp(con, ev(last_verified="2025-01-01"))
    assert len(events.stale_verifications(con, cfg, today=TODAY)) == 1


# ---------------------------------------------------------------- announcements inbox
RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Acme Motors Limited - Acquisition</title><description>Acquisition of 51% stake in Delta</description>
<link>https://nse.example/corporate/ACME_01102026.pdf</link><pubDate>Thu, 01 Oct 2026 10:00:00 +0530</pubDate></item>
<item><title>Beta Pharma Limited - Trading Window</title><description>Closure of trading window</description>
<link>https://nse.example/corporate/BETA_01102026.pdf</link><pubDate>Thu, 01 Oct 2026 11:00:00 +0530</pubDate></item>
<item><title>Unknown Co - Order</title><description>Receipt of order</description>
<link>https://nse.example/corporate/ZZZZ_01102026.pdf</link><pubDate>Thu, 01 Oct 2026 12:00:00 +0530</pubDate></item>
</channel></rss>"""


def test_announcement_inbox_suggests_but_never_publishes(con, cfg):
    r = announcements.refresh_inbox(con, cfg, xml_text=RSS)
    assert r == {"items": 3, "to_review": 1, "unmatched": 1}
    ib = announcements.read_inbox(con).set_index("symbol", drop=False)
    assert ib.loc["ACME", "suggested_category"] == "Acquisition / merger"
    assert ib.loc["BETA", "review_state"] == "routine"
    assert events.read_events(con).empty                     # nothing reaches the timeline automatically
    announcements.mark_reviewed(con, ib.loc["ACME", "guid"])
    announcements.refresh_inbox(con, cfg, xml_text=RSS)      # a refresh does not reset the review state
    assert announcements.read_inbox(con, "ACME").loc[0, "review_state"] == "reviewed"


# ---------------------------------------------------------------- sections 6-7: explanation
def _run(fin, cfg, price=500.0, qual=None):
    pit = PITData("GOOD", fin, flat_prices(price=price), pd.Series(dtype=float), "2026-06-30",
                  {"sector": "Technology", "industry": "Software"})
    res = MetricEngine(pit, cfg, qualitative=qual or {}).run()
    return res, score(res, cfg), pit


def test_explanation_reproduces_the_score(good_fin, cfg):
    res, sc, _ = _run(good_fin, cfg)
    w = explain.explain_rank(res, sc, {"rank": 3, "eligible": True}, 40, "test", cfg=cfg)
    assert w["check"]["matches"]
    assert w["headline"].startswith("Rank 3 of 40 ranked")
    assert f"{sc['points']:g} points ÷ {sc['applicable']}" in w["headline"]
    assert len(w["positives"]) == sc["pass"] and len(w["negatives"]) == sc["fail"] and len(w["watch"]) == sc["watch"]
    m = w["missing"]
    assert m["count"] == sc["insufficient"]
    assert m["score_if_all_missing_passed"] == pytest.approx((sc["points"] + sc["insufficient"]) / sc["applicable"])


def test_unranked_company_explained(good_fin, cfg):
    res, sc, _ = _run(good_fin, cfg)
    w = explain.explain_rank(res, sc, {"rank": pd.NA, "eligible": False}, 40, cfg=cfg)
    assert w["headline"].startswith("Not ranked") and "minimum" in w["headline"]


def test_rank_change_is_explained_by_metric_moves(good_fin, cfg):
    res, sc, _ = _run(good_fin, cfg)
    statuses = {r.key: r.status for r in res}
    flipped = next(r for r in res if r.status == PASS)
    old = dict(statuses, **{flipped.key: FAIL})
    prev = {"snapshot_date": "2026-07-01", "rank": 9, "points": sc["points"] - 1, "applicable": sc["applicable"],
            "conservative_score": (sc["points"] - 1) / sc["applicable"], "statuses_json": json.dumps(old),
            "settings_hash": "same"}
    ch = explain.rank_change(res, sc, {"rank": 4}, prev, cfg, "same")
    assert ch["fully_explained"] and ch["points_delta"] == pytest.approx(1.0)
    assert ch["moves"][0]["key"] == flipped.key and "rank 9 → 4" in ch["text"]
    prev2 = dict(prev, points=sc["points"], statuses_json=json.dumps(statuses), rank=7)
    ch2 = explain.rank_change(res, sc, {"rank": 4}, prev2, cfg, "same")
    assert "other companies" in ch2["text"] and ch2["fully_explained"]


def test_events_never_change_score_or_rank(cfg):
    con = db.connect(":memory:")
    fin = make_fin("ACME", {f"{y}-03-31": good_company_year(1.12 ** (y - 2021)) for y in range(2021, 2026)})
    fin["publication_date"] = fin["publication_date"].dt.strftime("%Y-%m-%d")
    fin["period_end"] = fin["period_end"].dt.strftime("%Y-%m-%d")
    db.upsert_df(con, "financials", fin)
    px = flat_prices().assign(symbol="ACME", open=500, high=500, low=500, adj_close=500, volume=1, source="T",
                              retrieved_at="t")
    px["date"] = px["date"].dt.strftime("%Y-%m-%d")
    db.upsert_df(con, "prices", px)
    con.execute("INSERT INTO universe VALUES ('NIFTY50','ACME','Acme Motors','Auto','t','t','t')")
    before = analyze_symbol(con, "ACME", "2026-06-30", cfg, {})[1]
    imp(con, ev(), ev(category="Order / contract win", title="Huge order", evidence_status="Confirmed",
                      source_type="exchange_filing", source_url="https://x/o", status="Completed",
                      event_date="2026-01-01", business_stage="existing"))
    assert len(events.read_events(con, "ACME")) == 2
    after = analyze_symbol(con, "ACME", "2026-06-30", cfg, {})[1]
    assert before == after
    assert cfg["research"]["event_scoring"] is False


def test_metric_rank_direction():
    d = pd.DataFrame([{"symbol": s, "key": "pe", "value": v, "display": "", "status": st}
                      for s, v, st in [("A", 30, WATCH), ("B", 12, PASS), ("C", 20, PASS), ("D", None, INSUFFICIENT)]])
    r = explain.metric_rank(d, "pe")
    assert r["symbol"].tolist() == ["B", "C", "A", "D"] and pd.isna(r.loc[3, "metric_rank"])
    d["key"], d["value"] = "roce", [0.30, 0.12, 0.20, None]
    assert explain.metric_rank(d, "roce")["symbol"].tolist()[:2] == ["C", "B"]


# ---------------------------------------------------------------- section 8: filters
def _table(cfg):
    rows = []
    for i, (s, sec, mc, cs) in enumerate([("A", "Tech", 5e11, .8), ("B", "Tech", 1e11, .6), ("C", "Bank", 9e11, .7),
                                          ("D", "Bank", 2e10, .2)]):
        rows.append({"symbol": s, "name": s, "sector": sec, "industry": sec, "market_cap": mc, "price_date": TODAY,
                     "points": cs * 27, "applicable": 27, "conservative_score": cs, "coverage": .2 if s == "D" else .8})
    t = rank(pd.DataFrame(rows), cfg)
    det = pd.DataFrame([{"symbol": s, "key": "pe", "value": v, "status": PASS} for s, v in zip("ABCD", [30, 15, 10, 8])])
    return t, det


def test_top_n_scope_is_explicit(cfg):
    t, d = _table(cfg)
    e = pd.DataFrame(columns=["symbol", "category", "status", "event_date", "published_date"])
    rows, s = service.apply_filters(t, d, e, service.Filters(top_n=1, sectors=["Bank"]))
    assert rows["symbol"].tolist() == ["C"] and rows.loc[0, "rank_shown"] == 2
    assert s.startswith("Top 1 by overall rank across the whole eligible universe; filters: sector Bank")
    rows, s = service.apply_filters(t, d, e, service.Filters(top_n=1, sectors=["Bank"], scope="view"))
    assert rows.loc[0, "rank_shown"] == 1 and "inside the filtered" in s
    rows, _ = service.apply_filters(t, d, e, service.Filters(mcap_min_cr=50_000, pe_max=20))
    assert rows["symbol"].tolist() == ["C"]
    rows, _ = service.apply_filters(t, d, e, service.Filters(min_coverage=0.5))
    assert "D" not in rows["symbol"].tolist()


def test_event_filter(con, cfg):
    t, d = _table(cfg)
    t.loc[t["symbol"] == "A", "symbol"] = "ACME"
    imp(con, ev(category="Acquisition / merger", title="Acme buys Delta", evidence_status="Confirmed",
                source_type="exchange_filing"))
    rows, _ = service.apply_filters(t, d, events.read_events(con),
                                    service.Filters(event_categories=["Acquisition / merger"]))
    assert rows["symbol"].tolist() == ["ACME"]


# ---------------------------------------------------------------- section 10: alerts & freshness
def test_alerts_for_watchlist_are_created_once(con, cfg):
    def snap(day, ranks):
        t = pd.DataFrame([{"symbol": s, "rank": r, "eligible": True, "points": 10, "applicable": 27,
                           "conservative_score": .4, "coverage": .8} for s, r in ranks.items()])
        d = pd.DataFrame([{"symbol": s, "key": "roe", "status": PASS} for s in ranks])
        return monitor.save_snapshot(con, t, d, "U", day, cfg)
    snap("2026-09-01", {"ACME": 20, "BETA": 3})
    sid = snap("2026-10-01", {"ACME": 8, "BETA": 4})
    monitor.watch(con, "ACME")
    monitor.watch(con, "BETA")
    imp(con, ev(status="Delayed"))
    n = monitor.generate_alerts(con, sid, cfg)
    a = monitor.read_alerts(con)
    assert n == 2 and set(a["alert_type"]) == {"rank change", "project delayed"}
    assert "20 → 8" in a[a["alert_type"] == "rank change"]["message"].iloc[0]
    assert monitor.generate_alerts(con, sid, cfg) == 0           # idempotent
    assert monitor.previous_snapshot(con, "ACME", "U", sid)["rank"] == 20


def test_freshness_shows_missing_stale_and_failed(con, cfg):
    con.execute("INSERT INTO prices (symbol,date,close,source,retrieved_at) VALUES ('ACME','2026-01-01',1,'t',"
                "'2026-01-01T00:00:00Z')")
    db.log_fetch(con, "ACME", "prices", "error", "HTTP 429", "yahoo")
    f = monitor.freshness(con, "ACME", cfg, today="2026-10-19").set_index("dataset")
    assert f.loc["prices", "state"] == "Stale · last refresh failed" and "429" in f.loc["prices", "last_error"]
    assert f.loc["exchange announcements", "state"] == "Missing"
    assert f.loc["NSE company list", "state"].startswith(("OK", "Stale"))
