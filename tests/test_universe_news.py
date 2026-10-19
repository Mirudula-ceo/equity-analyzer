"""Tests for the all-NSE snapshot, latest news and company-website research. SYNTHETIC inputs only."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from conftest import flat_prices, good_company_year, make_fin
from equity import db
from equity.metrics.engine import MetricEngine, MetricResult
from equity.metrics.pit import PITData
from equity.metrics.scoring import score
from equity.research import explain, listings, news, service, snapshot, website

ROOT = Path(__file__).resolve().parent.parent


def test_real_nse_header_variants(tmp_path):
    # headers exactly as NSE published them in Oct 2026
    (tmp_path / "EQUITY_L.csv").write_text(
        "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
        "20MICRONS,20 Microns Limited,EQ,06-OCT-2008,5,1,INE144J01027,5\n")
    (tmp_path / "SME_EQUITY_L.csv").write_text(
        "SYMBOL,NAME_OF_COMPANY,SERIES,DATE_OF_LISTING,PAID_UP_VALUE,ISIN_NUMBER,FACE_VALUE,\n"
        "RKFAL,R.K. Fashion Accessories Limited,ST,12-Oct-26,10,INE2O2Y01016,10,\n")
    (tmp_path / "eq_etfseclist.csv").write_text(
        "Symbol,Underlying Asset,SecurityName,DateofListing,MarketLot,ISINNumber,FaceValue,ETF Underlying,Underlying Key\n"
        "NIFTYBEES,Nifty 50,NIPINDETFNIFTYBEES,08-Jan-02,1,INF204KB14I2,1,x,y\n")
    (tmp_path / "symbolchange.csv").write_text(
        "NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\nTata Motors Limited,TATAMOTORS,TMPV,14-OCT-2025\n")
    con = db.connect(":memory:")
    out = listings.load_manual_lists(con, tmp_path, as_of="2026-10-19")
    assert out["MAINBOARD"]["listed"] == 1 and out["SME"]["listed"] == 1 and out["ETF"]["listed"] == 1
    lst = listings.read_listings(con).set_index("symbol")
    assert lst.loc["RKFAL", "listing_date"] == "2026-10-12"
    assert lst.loc["NIFTYBEES", "isin"] == "INF204KB14I2"
    assert listings.resolve_symbol(con, "TATAMOTORS") == "TMPV"
    assert con.execute("SELECT company FROM symbol_changes").fetchone()[0] == "Tata Motors Limited"


def _results(cfg):
    fin = make_fin("GOOD", {f"{y}-03-31": good_company_year(1.12 ** (y - 2021)) for y in range(2021, 2026)})
    pit = PITData("GOOD", fin, flat_prices(), pd.Series(dtype=float), "2026-06-30", {"sector": "Technology"})
    return MetricEngine(pit, cfg, qualitative={}).run()


def test_snapshot_round_trip_reproduces_score_and_explanation(cfg, tmp_path):
    res = _results(cfg)
    d = snapshot.details_frame("GOOD", res)
    d.to_csv(tmp_path / "d.csv.gz", index=False, compression="gzip")
    back = snapshot.results_for("GOOD", pd.read_csv(tmp_path / "d.csv.gz"))
    assert [r.status for r in back] == [r.status for r in res]
    assert score(back, cfg) == score(res, cfg)
    w = explain.explain_rank(back, score(back, cfg), {"rank": 1, "eligible": True}, 10, cfg=cfg)
    assert w["check"]["matches"]


@pytest.fixture
def snap_dir(tmp_path, monkeypatch, cfg):
    res = _results(cfg)
    sc = score(res, cfg)
    t = pd.DataFrame([{"symbol": "GOOD", "name": "Good Ltd", "sector": "Technology", "industry": "Software",
                       "price": 500.0, "price_date": "2026-06-30", "market_cap": 5e10, "latest_period": "2025-03",
                       **sc, "eligible": True, "rank": 1}])
    t.to_csv(tmp_path / snapshot.FILES["scores"], index=False, compression="gzip")
    snapshot.details_frame("GOOD", res).to_csv(tmp_path / snapshot.FILES["details"], index=False, compression="gzip")
    pd.DataFrame([{"symbol": "GOOD", "name": "Good Ltd", "website": "good.example", "heldPercentInsiders": 0.6,
                   "companyOfficers": json.dumps([{"name": "A. Person", "title": "CEO"}])}]).to_csv(
        tmp_path / snapshot.FILES["profiles"], index=False, compression="gzip")
    (tmp_path / "meta.json").write_text(json.dumps({"generated_at": "2026-10-18T22:00:00Z", "companies_scored": 1}))
    monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path)
    return tmp_path


def test_snapshot_report_for_company_without_local_data(snap_dir, cfg):
    con = db.connect(":memory:")
    con.execute("INSERT INTO universe VALUES ('NIFTY50','GOOD','Good Ltd','IT','t','t','t')")
    src = service.ranking_source(con, cfg)
    assert src["kind"] == "snapshot" and len(src["table"]) == 1
    rep = service.snapshot_report(con, "GOOD", src, cfg)
    assert rep["rank_row"]["rank"] == 1 and rep["why"]["check"]["matches"]
    assert rep["why"]["headline"].startswith("Rank 1 of 1 ranked (all eligible NSE companies)")
    assert rep["valuation"]["price"] == 500.0 and rep["profile"]["website"] == "good.example"
    assert rep["profile"]["companyOfficers"][0]["title"] == "CEO"
    assert service.snapshot_report(con, "OTHER", src, cfg) is None


def test_top_n_any_number(cfg):
    from equity.metrics.scoring import rank
    t = rank(pd.DataFrame([{"symbol": f"S{i}", "conservative_score": 1 - i / 200, "coverage": 0.9,
                            "sector": "X", "industry": "X", "market_cap": 1e10, "points": 1, "applicable": 27}
                           for i in range(120)]), cfg)
    e = pd.DataFrame(columns=["symbol", "category", "status", "event_date", "published_date"])
    for n in (9, 99):
        rows, s = service.apply_filters(t, pd.DataFrame(columns=["symbol", "key", "status", "value"]), e,
                                        service.Filters(top_n=n))
        assert len(rows) == n and s.startswith(f"Top {n} by overall rank")


GN = """<rss><channel>
<item><title>Good Ltd bags Rs 500 crore order - Mint</title><link>https://news.example/1</link>
<pubDate>Mon, 19 Oct 2026 08:00:00 GMT</pubDate><source url="https://mint">Mint</source></item>
<item><title>Good Ltd shares rise - ET</title><link>https://news.example/2</link>
<pubDate>Sun, 18 Oct 2026 08:00:00 GMT</pubDate><source url="https://et">ET</source></item>
</channel></rss>"""


class _R:
    def __init__(self, text, code=200, ctype="text/html"):
        self.text, self.status_code, self.headers = text, code, {"content-type": ctype}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_google_news_parsing_and_labels():
    df, err = news.google_news("Good Limited", "GOOD", get=lambda *a, **k: _R(GN))
    assert err is None and df["title"].tolist()[0] == "Good Ltd bags Rs 500 crore order"
    assert df.loc[0, "publisher"] == "Mint" and df.loc[0, "category_hint"] == "Order / contract win"
    assert set(df["evidence"]) == {"Reported / unconfirmed"}
    rows = news.to_event_rows(df, "GOOD")
    assert (rows["summary"] == "").all() and rows.loc[0, "source_type"] == "financial_publication"
    assert news.short_name("Tata Consultancy Services Limited") == "Tata Consultancy Services"


def test_news_failure_is_reported_not_hidden():
    def boom(*a, **k):
        raise ConnectionError("Unable to connect to proxy")
    df, err = news.google_news("Good", "GOOD", get=boom)
    assert df.empty and "could not be reached" in err


SITE = {
    "https://good.example": '<title>Good Ltd</title><meta name="description" content="We make widgets">'
                            '<a href="/investors">Investor Relations</a><a href="/about">About Us</a>',
    "https://good.example/investors": '<a href="/f/ar26.pdf">Annual Report FY26</a>'
                                      '<a href="/f/shp.pdf">Shareholding Pattern - Sep 2026</a>'
                                      '<a href="/governance">Corporate Governance</a>',
    "https://good.example/governance": '<a href="/board">Board of Directors</a>',
}


def test_website_scan_finds_documents_for_missing_metrics(cfg):
    get = lambda u, **k: _R(SITE[u]) if u in SITE else _R("", 403)  # noqa: E731
    scan = website.scan_website("good.example", get=get)
    kinds = {d["document"] for d in scan["docs"]}
    assert {"Annual report", "Shareholding pattern", "Corporate governance / board"} <= kinds
    assert scan["description"] == "We make widgets"
    assert "https://good.example/investors" in scan["pages_read"]
    res = _results(cfg)
    ev = {r["key"]: r for r in website.evidence_for_missing(res, scan, {"heldPercentInsiders": 0.6})}
    assert ev["promoter_holding"]["documents"][0]["document"] == "Shareholding pattern"
    assert "Proxy only, not scored" in ev["promoter_holding"]["proxy"]
    assert ev["business_quality"]["documents"]                     # about page / annual report
    # numeric metrics are never in this list
    assert all(k in website.METRIC_EVIDENCE for k in ev)


def test_website_that_blocks_bots_is_reported():
    scan = website.scan_website("https://blocked.example", get=lambda u, **k: _R("", 403))
    assert scan["docs"] == [] and "refused automated access" in scan["errors"][0]


def test_publish_ranks_parts_and_refuses_tiny_runs(tmp_path, cfg):
    parts = tmp_path / "parts"
    parts.mkdir()
    res = _results(cfg)
    sc = score(res, cfg)
    pd.DataFrame([{"symbol": s, "name": s, "sector": "X", **{**sc, "conservative_score": v}} for s, v in
                  [("A", 0.5), ("B", 0.7)]]).to_csv(parts / "scores_0.csv", index=False)
    pd.concat([snapshot.details_frame(s, res) for s in "AB"]).to_csv(parts / "details_0.csv", index=False)
    pd.DataFrame([{"symbol": "A"}, {"symbol": "B"}]).to_csv(parts / "profiles_0.csv", index=False)
    syms = tmp_path / "symbols.txt"
    syms.write_text("A\nB\n")
    env = {"EQUITY_SNAPSHOT": str(tmp_path / "snap"), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "refresh_universe.py"), "publish", "--parts",
                        str(parts), "--symbols", str(syms)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    t = pd.read_csv(tmp_path / "snap" / snapshot.FILES["scores"])
    assert t.sort_values("rank")["symbol"].tolist() == ["B", "A"]
    assert json.loads((tmp_path / "snap" / "meta.json").read_text())["companies_scored"] == 2
    syms.write_text("\n".join(f"S{i}" for i in range(100)))        # only 2 of 100 scored -> keep previous
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "refresh_universe.py"), "publish", "--parts",
                        str(parts), "--symbols", str(syms)], capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "keeping previous snapshot" in r.stderr
