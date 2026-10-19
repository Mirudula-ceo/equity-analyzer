"""Glue between the 27-metric engine and the research module (used by the dashboard and pipeline)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import db
from ..analysis import analyze_symbol, rank_symbols
from ..config import load_settings
from ..metrics.engine import FAIL, PASS, WATCH
from ..metrics.scoring import score
from . import events as ev_mod
from . import explain, listings, monitor, snapshot


def research_universe(con, cfg: dict | None = None) -> pd.DataFrame:
    """Every company that gets a research page: the official NSE lists (all segments) if loaded,
    otherwise the Nifty universe already in the database. Adds eligibility and data availability."""
    cfg = cfg or load_settings()
    ensure_lists(con)
    lst = listings.read_listings(con)
    if lst.empty:
        u = db.read_universe(con, "NIFTY50")
        lst = pd.DataFrame({"symbol": u["symbol"], "name": u["company"], "segment": "MAINBOARD", "series": None,
                            "isin": None, "listing_date": None, "status": "active",
                            "source": u["source"] + " (official NSE list not loaded yet)"})
    have = set(db.symbols_with_prices(con))
    elig = lst.apply(lambda r: listings.is_eligible(r, cfg), axis=1)
    lst["eligible"] = [e[0] for e in elig]
    lst["eligibility_note"] = [e[1] for e in elig]
    lst["data_downloaded"] = lst["symbol"].isin(have)
    snap = snapshot.load_scores()
    lst["scored_in_snapshot"] = lst["symbol"].isin(set(snap["symbol"])) if not snap.empty else False
    n_ev = pd.read_sql_query("SELECT symbol, COUNT(*) AS events FROM events GROUP BY symbol", con)
    lst = lst.merge(n_ev, on="symbol", how="left")
    lst["events"] = lst["events"].fillna(0).astype(int)
    return lst


def ensure_lists(con) -> None:
    """Load the official NSE lists kept in data/manual/ (refreshed daily by GitHub Actions) when the
    database has none or an older copy - no network needed, so it works on Streamlit Cloud."""
    f = listings.MANUAL_DIR / listings.MANUAL_FILES["MAINBOARD"]
    if not f.exists():
        return
    last = con.execute("SELECT MAX(retrieved_at) FROM nse_listings").fetchone()[0]
    file_time = pd.Timestamp(f.stat().st_mtime, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    if last is None or last < file_time:
        listings.load_manual_lists(con, as_of=snapshot.meta().get("generated_at", "")[:10] or None)


def ranking_source(con, cfg: dict | None = None) -> dict:
    """The ONE ranking every page uses: the all-NSE snapshot if present, else live scoring of the
    companies whose full data is in the local database."""
    if snapshot.available():
        m = snapshot.meta()
        return {"kind": "snapshot", "table": snapshot.load_scores(), "details": snapshot.load_details(),
                "label": "All NSE (snapshot)", "as_of": m.get("generated_at"), "meta": m}
    t, d = rank_universe(con, None, cfg)
    return {"kind": "live", "table": t, "details": d, "label": universe_label(con), "as_of": None, "meta": {}}


class SnapshotPrice:
    """Minimal stand-in for PITData when only snapshot results exist for a company."""

    def __init__(self, row: dict, meta: dict):
        self.row, self._meta, self.info = row, meta, {}

    def price(self):
        v = self.row.get("price")
        return None if v is None or pd.isna(v) else float(v)

    def price_date(self):
        v = self.row.get("price_date")
        return None if v is None or pd.isna(v) else pd.Timestamp(v)

    def market_cap(self):
        v = self.row.get("market_cap")
        return None if v is None or pd.isna(v) else float(v)

    def periods(self):
        v = self.row.get("latest_period")
        return [] if v is None or pd.isna(v) else [pd.Timestamp(str(v) + "-01")]

    def source_note(self):
        return (f"Yahoo Finance via yfinance, scored by the 27-metric engine in the all-NSE snapshot of "
                f"{str(self._meta.get('generated_at', 'n/a'))[:10]}; latest annual period "
                f"{self.row.get('latest_period') or 'n/a'}")


def scorable_symbols(con, cfg: dict | None = None) -> list[str]:
    u = research_universe(con, cfg)
    return u[u["eligible"] & u["data_downloaded"]]["symbol"].tolist()


def universe_label(con) -> str:
    return "NSE_ELIGIBLE" if not listings.read_listings(con).empty else "NIFTY50"


# ---------------------------------------------------------------- filters (section 8)
@dataclass
class Filters:
    top_n: int | None = None
    scope: str = "universe"                     # "universe" = keep overall rank; "view" = re-rank inside filter
    sectors: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    mcap_min_cr: float | None = None
    mcap_max_cr: float | None = None
    pe_max: float | None = None
    status_filters: dict = field(default_factory=dict)  # metric key -> list of allowed statuses
    event_categories: list[str] = field(default_factory=list)
    event_statuses: list[str] = field(default_factory=list)
    event_since: str | None = None
    min_coverage: float | None = None
    updated_since: str | None = None
    rank_by: str = "overall"                    # "overall" or a metric key


def apply_filters(table: pd.DataFrame, details: pd.DataFrame, events: pd.DataFrame, f: Filters) -> tuple[pd.DataFrame, str]:
    """Returns (rows, scope sentence). The sentence states exactly what 'Top N' is taken from."""
    t = table.copy()
    t["overall_rank"] = t["rank"]
    desc = []
    if f.sectors:
        t = t[t["sector"].isin(f.sectors)]
        desc.append("sector " + ", ".join(f.sectors))
    if f.industries:
        t = t[t["industry"].isin(f.industries)]
        desc.append("industry " + ", ".join(f.industries))
    mcap_cr = t["market_cap"] / 1e7
    if f.mcap_min_cr is not None:
        t = t[mcap_cr.reindex(t.index) >= f.mcap_min_cr]
        desc.append(f"market cap ≥ ₹{f.mcap_min_cr:,.0f} cr")
    if f.mcap_max_cr is not None:
        t = t[(t["market_cap"] / 1e7) <= f.mcap_max_cr]
        desc.append(f"market cap ≤ ₹{f.mcap_max_cr:,.0f} cr")
    if f.pe_max is not None:
        pe = details[(details["key"] == "pe") & details["status"].isin([PASS, WATCH, FAIL])].set_index("symbol")["value"]
        t = t[t["symbol"].map(pe).astype(float) <= f.pe_max]
        desc.append(f"P/E ≤ {f.pe_max:g}")
    for key, allowed in (f.status_filters or {}).items():
        if allowed:
            st = details[details["key"] == key].set_index("symbol")["status"]
            t = t[t["symbol"].map(st).isin(allowed)]
            desc.append(f"{key} in {'/'.join(allowed)}")
    if f.event_categories or f.event_statuses or f.event_since:
        e = events
        if f.event_categories:
            e = e[e["category"].isin(f.event_categories)]
        if f.event_statuses:
            e = e[e["status"].isin(f.event_statuses)]
        if f.event_since:
            e = e[e["event_date"].fillna(e["published_date"]) >= f.event_since]
        t = t[t["symbol"].isin(set(e["symbol"]))]
        desc.append("has matching recorded events")
    if f.min_coverage is not None:
        t = t[t["coverage"] >= f.min_coverage]
        desc.append(f"coverage ≥ {f.min_coverage:.0%}")
    if f.updated_since and "price_date" in t:
        t = t[pd.to_datetime(t["price_date"]) >= pd.Timestamp(f.updated_since)]
        desc.append(f"price updated since {f.updated_since}")

    if f.rank_by != "overall":
        mr = explain.metric_rank(details[details["symbol"].isin(t["symbol"])], f.rank_by).set_index("symbol")
        t["rank_shown"] = t["symbol"].map(mr["metric_rank"])
        basis = f"rank on metric '{f.rank_by}' (Pass > Watch > Fail, then value)"
    elif f.scope == "view":
        e = t[t["eligible"]].sort_values(["conservative_score", "coverage"], ascending=False)
        t["rank_shown"] = t["symbol"].map(dict(zip(e["symbol"], range(1, len(e) + 1))))
        basis = "rank re-calculated inside the filtered companies"
    else:
        t["rank_shown"] = t["overall_rank"]
        basis = "overall rank across the whole eligible universe"
    t = t.sort_values(["rank_shown", "conservative_score"], ascending=[True, False], na_position="last")
    if f.top_n:
        t = t[t["rank_shown"].notna()].head(f.top_n)
    n_univ = int(table["eligible"].sum())
    where = ("filters: " + "; ".join(desc)) if desc else "no filters"
    top = f"Top {f.top_n}" if f.top_n else "All"
    sentence = (f"{top} by {basis}; {where}. Universe: {len(table)} scored companies, {n_univ} ranked "
                f"(coverage ≥ {load_settings()['scoring']['min_coverage_for_rank']:.0%}).")
    return t.reset_index(drop=True), sentence


# ---------------------------------------------------------------- company report (section 9)
def company_report(con, symbol: str, table: pd.DataFrame | None = None, details: pd.DataFrame | None = None,
                   cfg: dict | None = None) -> dict:
    cfg = cfg or load_settings()
    ident = listings.company_identity(con, symbol)
    res, sc, pit = analyze_symbol(con, symbol, None, cfg)
    row = None
    n_ranked = None
    if table is not None and not table.empty and symbol in set(table["symbol"]):
        row = table[table["symbol"] == symbol].iloc[0].to_dict()
        n_ranked = int(table["eligible"].sum())
        if not np.isclose(row["conservative_score"], sc["conservative_score"]):
            row = None                               # stale ranking table - never explain a different calculation
    label = universe_label(con)
    prev = monitor.previous_snapshot(con, symbol, label)
    events = ev_mod.read_events(con, symbol)
    why = explain.explain_rank(res, sc, row, n_ranked, scope=f"{label} universe", prev=prev, events=events,
                               cfg=cfg, current_settings_hash=monitor.settings_hash(cfg))
    peers = None
    if details is not None and table is not None and not details.empty:
        peers = details.merge(table[["symbol", "sector"]], on="symbol", how="left")
    val = explain.valuation_view(res, pit, peers, pit.info.get("sector"))
    prof = {**{k: pit.info.get(k) for k in snapshot.PROFILE_COLS if pit.info.get(k) is not None},
            **{k: v for k, v in snapshot.profile(symbol).items() if v is not None}}
    return {"symbol": symbol, "identity": ident, "info": pit.info, "results": res, "score": sc, "pit": pit,
            "rank_row": row, "why": why, "valuation": val, "events": events,
            "checklist": ev_mod.research_checklist(con, symbol),
            "freshness": monitor.freshness(con, symbol, cfg),
            "history": monitor.rank_history(con, symbol, label), "profile": prof, "source_kind": "live",
            "eligible": listings.is_eligible(ident, cfg) if ident else (False, "unknown company")}


def snapshot_report(con, symbol: str, src: dict, cfg: dict | None = None) -> dict | None:
    """Company report built from the all-NSE snapshot (same keys as company_report)."""
    cfg = cfg or load_settings()
    t, d = src["table"], src["details"]
    if t.empty or symbol not in set(t["symbol"]):
        return None
    res = snapshot.results_for(symbol, d)
    sc = score(res, cfg)
    row = t[t["symbol"] == symbol].iloc[0].to_dict()
    if not np.isclose(row["conservative_score"], sc["conservative_score"]):
        row = None
    prof = snapshot.profile(symbol)
    pit = SnapshotPrice(row or t[t["symbol"] == symbol].iloc[0].to_dict(), src["meta"])
    pit.info = {"longName": prof.get("name") or (row or {}).get("name"), "sector": (row or {}).get("sector"),
                "industry": (row or {}).get("industry"), **prof}
    ident = listings.company_identity(con, symbol)
    prev = monitor.previous_snapshot(con, symbol, "All NSE (snapshot)")
    events = ev_mod.read_events(con, symbol)
    why = explain.explain_rank(res, sc, row, int(t["eligible"].sum()), scope="all eligible NSE companies",
                               prev=prev, events=events, cfg=cfg, current_settings_hash=monitor.settings_hash(cfg))
    peers = d.merge(t[["symbol", "sector"]], on="symbol", how="left")
    val = explain.valuation_view(res, pit, peers, pit.info.get("sector"))
    fresh = monitor.freshness(con, symbol, cfg)
    fresh = pd.concat([pd.DataFrame([{"dataset": "all-NSE scores (snapshot)", "last_updated": src["as_of"],
                                      "age_days": None, "refresh_limit_days": 7,
                                      "state": "OK" if src["as_of"] and (pd.Timestamp.now("UTC") - pd.Timestamp(
                                          src["as_of"])).days <= 8 else "Stale", "last_error": ""}]),
                       fresh[fresh["dataset"].isin(["NSE company list", "exchange announcements"])]],
                      ignore_index=True)
    return {"symbol": symbol, "identity": ident, "info": pit.info, "results": res, "score": sc, "pit": pit,
            "rank_row": row, "why": why, "valuation": val, "events": events,
            "checklist": ev_mod.research_checklist(con, symbol), "freshness": fresh,
            "history": monitor.rank_history(con, symbol, "All NSE (snapshot)"), "profile": prof,
            "eligible": listings.is_eligible(ident, cfg) if ident else (True, "scored in snapshot"),
            "source_kind": "snapshot"}


def rank_universe(con, symbols: list[str] | None = None, cfg: dict | None = None):
    symbols = symbols if symbols is not None else scorable_symbols(con, cfg)
    return rank_symbols(con, symbols, None, cfg)
