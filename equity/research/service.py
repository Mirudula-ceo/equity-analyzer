"""Glue between the 27-metric engine and the research module (used by the dashboard and pipeline)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import db
from ..analysis import analyze_symbol, rank_symbols
from ..config import load_settings
from ..metrics.engine import FAIL, PASS, WATCH
from . import events as ev_mod
from . import explain, listings, monitor


def research_universe(con, cfg: dict | None = None) -> pd.DataFrame:
    """Every company that gets a research page: the official NSE lists (all segments) if loaded,
    otherwise the Nifty universe already in the database. Adds eligibility and data availability."""
    cfg = cfg or load_settings()
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
    n_ev = pd.read_sql_query("SELECT symbol, COUNT(*) AS events FROM events GROUP BY symbol", con)
    lst = lst.merge(n_ev, on="symbol", how="left")
    lst["events"] = lst["events"].fillna(0).astype(int)
    return lst


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
    return {"symbol": symbol, "identity": ident, "info": pit.info, "results": res, "score": sc, "pit": pit,
            "rank_row": row, "why": why, "valuation": val, "events": events,
            "checklist": ev_mod.research_checklist(con, symbol),
            "freshness": monitor.freshness(con, symbol, cfg),
            "history": monitor.rank_history(con, symbol, label),
            "eligible": listings.is_eligible(ident, cfg) if ident else (False, "unknown company")}


def rank_universe(con, symbols: list[str] | None = None, cfg: dict | None = None):
    symbols = symbols if symbols is not None else scorable_symbols(con, cfg)
    return rank_symbols(con, symbols, None, cfg)
