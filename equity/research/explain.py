"""Explainable rank and valuation (requirements sections 6, 7, 8).

Every statement is derived from the SAME MetricResult list and score() output
that produced the rank, and `check` re-computes the score from the parts so an
explanation that disagrees with the calculation is caught (tests enforce it).
Events are listed as context only - they are never part of the score.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..config import load_settings
from ..metrics.engine import DEF_BY_KEY, DEFS, FAIL, INSUFFICIENT, NA, PASS, WATCH, MetricResult
from ..metrics.scoring import score
from .constants import GUIDANCE

LOWER_IS_BETTER = {"working_capital", "earnings_quality", "debt_to_equity", "promoter_pledge",
                   "pe", "pb", "ev_ebitda", "peg"}
VALUATION_KEYS = ["pe", "pb", "ev_ebitda", "peg", "fcf_yield", "margin_of_safety"]
STATUS_ORDER = {PASS: 0, WATCH: 1, FAIL: 2, INSUFFICIENT: 3, NA: 4}
CATEGORY_ORDER = list(dict.fromkeys(d.category for d in DEFS))


def _pts(cfg):
    return cfg["scoring"]["points"]


def category_breakdown(results: list[MetricResult], cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_settings()
    pts = _pts(cfg)
    rows = []
    for cat in CATEGORY_ORDER:
        rs = [r for r in results if r.category == cat]
        app = [r for r in rs if r.status != NA]
        earned = sum(pts.get(r.status, 0.0) for r in app)
        rows.append({"category": cat, "applicable": len(app),
                     "pass": sum(r.status == PASS for r in rs), "watch": sum(r.status == WATCH for r in rs),
                     "fail": sum(r.status == FAIL for r in rs),
                     "insufficient": sum(r.status == INSUFFICIENT for r in rs),
                     "points": earned, "max_points": len(app) * pts[PASS],
                     "share": earned / (len(app) * pts[PASS]) if app else None})
    return pd.DataFrame(rows)


def missing_data_impact(results: list[MetricResult], sc: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or load_settings()
    pts = _pts(cfg)
    miss = [r for r in results if r.status == INSUFFICIENT]
    n_app = sc["applicable"]
    at_risk = len(miss) * pts[PASS]
    return {"count": len(miss),
            "metrics": [{"id": r.id, "name": r.name, "why": r.explanation} for r in miss],
            "points_withheld_max": at_risk,
            "score_now": sc["conservative_score"],
            "score_if_all_missing_passed": (sc["points"] + at_risk) / n_app if n_app else None,
            "score_on_evaluated_only": sc["score_on_evaluated"]}


def _fmt_pct(x):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.0f}%"


def explain_rank(results: list[MetricResult], sc: dict, rank_row: dict | None = None,
                 n_ranked: int | None = None, scope: str = "", prev: dict | None = None,
                 events: pd.DataFrame | None = None, cfg: dict | None = None,
                 current_settings_hash: str | None = None) -> dict:
    """Plain-language 'Why this rank?' built only from the actual scoring inputs."""
    cfg = cfg or load_settings()
    pts = _pts(cfg)
    cats = category_breakdown(results, cfg)
    scored = cats[cats["applicable"] > 0].sort_values("share", ascending=False)
    miss = missing_data_impact(results, sc, cfg)

    def items(status):
        return [f"#{r.id} {r.name}: {r.display} ({r.status})" for r in results if r.status == status]

    rank = None if rank_row is None else rank_row.get("rank")
    eligible = bool(rank_row.get("eligible")) if rank_row is not None else None
    of = f" of {n_ranked} ranked" if n_ranked else ""
    scope_txt = f" ({scope})" if scope else ""
    if rank_row is not None and not eligible:
        head = (f"Not ranked{scope_txt}: data coverage {_fmt_pct(sc['coverage'])} is below the "
                f"{_fmt_pct(cfg['scoring']['min_coverage_for_rank'])} minimum, so the score is shown but not ranked.")
    else:
        head = (f"{'Rank ' + str(int(rank)) + of if rank is not None and not pd.isna(rank) else 'Score'}{scope_txt}: "
                f"conservative score {_fmt_pct(sc['conservative_score'])} = {sc['points']:g} points ÷ "
                f"{sc['applicable']} applicable metrics (Pass {pts[PASS]:g}, Watch {pts[WATCH]:g}, Fail 0, "
                f"Insufficient Data 0).")
    lines = [head]
    if len(scored) >= 2:
        best, worst = scored.iloc[0], scored.iloc[-1]
        lines.append(f"Strongest area: {best['category']} ({best['points']:g}/{best['max_points']:g} points). "
                     f"Weakest area: {worst['category']} ({worst['points']:g}/{worst['max_points']:g}).")
    if miss["count"]:
        lines.append(f"{miss['count']} metric(s) lack data and earn 0 - up to {miss['points_withheld_max']:g} points "
                     f"withheld (score would be at most {_fmt_pct(miss['score_if_all_missing_passed'])} if all of "
                     f"them passed).")
    change = rank_change(results, sc, rank_row, prev, cfg, current_settings_hash)
    if change:
        lines.append(change["text"])
    ctx = event_context(events)
    if ctx["text"]:
        lines.append(ctx["text"])

    # consistency check: rebuild the score from the per-category parts
    rebuilt = cats["points"].sum()
    check = {"points_from_categories": rebuilt, "points_in_score": sc["points"],
             "applicable_from_categories": int(cats["applicable"].sum()), "applicable_in_score": sc["applicable"],
             "matches": bool(np.isclose(rebuilt, sc["points"]) and cats["applicable"].sum() == sc["applicable"]
                             and np.isclose(score(results, cfg)["conservative_score"], sc["conservative_score"]))}
    return {"headline": head, "text": " ".join(lines), "lines": lines,
            "positives": items(PASS), "watch": items(WATCH), "negatives": items(FAIL),
            "missing": miss, "categories": cats, "change": change, "context": ctx, "check": check}


def rank_change(results, sc, rank_row, prev: dict | None, cfg, current_settings_hash=None) -> dict | None:
    """Why the rank moved since the previous saved ranking snapshot."""
    if not prev:
        return None
    pts = _pts(cfg)
    old = json.loads(prev["statuses_json"]) if isinstance(prev.get("statuses_json"), str) else prev.get("statuses", {})
    now = {r.key: r.status for r in results}
    moves = []
    for k, st in now.items():
        o = old.get(k)
        if o is not None and o != st:
            moves.append({"key": k, "name": DEF_BY_KEY[k].name, "from": o, "to": st,
                          "points": pts.get(st, 0.0) - pts.get(o, 0.0)})
    d_points = sc["points"] - (prev.get("points") or 0.0)
    explained = sum(m["points"] for m in moves)
    r_now = None if rank_row is None or pd.isna(rank_row.get("rank")) else int(rank_row["rank"])
    r_old = None if prev.get("rank") is None or pd.isna(prev.get("rank")) else int(prev["rank"])
    parts = [f"Since {prev.get('snapshot_date')}: rank {r_old if r_old else 'unranked'} → {r_now if r_now else 'unranked'}, "
             f"score {_fmt_pct(prev.get('conservative_score'))} → {_fmt_pct(sc['conservative_score'])}."]
    if moves:
        parts.append("Metric changes: " + "; ".join(f"{m['name']} {m['from']}→{m['to']} ({m['points']:+g})"
                                                    for m in moves) + ".")
    elif r_now != r_old:
        parts.append("This company's metric results did not change; the rank moved because other companies' "
                     "scores changed (or companies were added/removed).")
    hash_changed = bool(current_settings_hash and prev.get("settings_hash") and
                        prev["settings_hash"] != current_settings_hash)
    if hash_changed:
        parts.append("Scoring thresholds in settings.toml changed between the two rankings.")
    if prev.get("applicable") is not None and prev["applicable"] != sc["applicable"]:
        parts.append(f"Applicable metrics changed {prev['applicable']} → {sc['applicable']}.")
    return {"text": " ".join(parts), "moves": moves, "points_delta": d_points,
            "points_explained_by_metric_moves": explained,
            "fully_explained": bool(np.isclose(d_points, explained)) or hash_changed,
            "rank_from": r_old, "rank_to": r_now}


def event_context(events: pd.DataFrame | None) -> dict:
    if events is None or events.empty:
        return {"text": "", "proposed": [], "risks": []}
    prop = events[(events["business_stage"] == "proposed") | (events["evidence_status"] == GUIDANCE)]
    prop = prop[~prop["status"].isin(["Completed", "Cancelled"])]
    risky = events[events["status"].isin(["Delayed", "Cancelled"]) |
                   events["category"].isin(["Debt / default", "Litigation / regulatory action"])]
    txt = [f"Context only (not scored): {len(events)} recorded event(s)."]
    if not prop.empty:
        unq = prop[prop["impact_quantified"].fillna(0).astype(int) == 0]
        txt.append(f"{len(prop)} proposed plan(s) still pending" +
                   (f", {len(unq)} without a confirmed revenue contribution." if len(unq) else "."))
    if not risky.empty:
        txt.append(f"{len(risky)} risk-type event(s) to review.")
    return {"text": " ".join(txt), "proposed": prop["display"].tolist() if "display" in prop else [],
            "risks": risky["display"].tolist() if "display" in risky else []}


# ---------------------------------------------------------------- valuation & peers
def valuation_view(results: list[MetricResult], pit, peers: pd.DataFrame | None = None,
                   sector: str | None = None) -> dict:
    """Price/timestamp, data period and valuation metrics vs sector peers.

    peers: metric-detail frame from rank_symbols() (symbol, key, value, status) joined with 'sector'.
    """
    by = {r.key: r for r in results}
    periods = pit.periods()
    rows = []
    for k in VALUATION_KEYS:
        r = by.get(k)
        if r is None:
            continue
        med, n = None, 0
        if peers is not None and not peers.empty and sector:
            pv = peers[(peers["key"] == k) & (peers["sector"] == sector) &
                       peers["status"].isin([PASS, WATCH, FAIL])]["value"].astype(float).dropna()
            n, med = len(pv), (float(pv.median()) if len(pv) else None)
        rel = None
        if med is not None and r.value is not None and r.status in (PASS, WATCH, FAIL):
            lower = k in LOWER_IS_BETTER
            cheaper = r.value < med if lower else r.value > med
            rel = ("cheaper than" if cheaper else "more expensive than") + " sector median"
        rows.append({"metric": r.name, "value": r.display, "status": r.status,
                     "sector_median": med, "peers": n, "vs_peers": rel or "-", "rule": r.criteria})
    return {"price": pit.price(), "price_date": pit.price_date(), "market_cap": pit.market_cap(),
            "latest_period": periods[-1] if periods else None,
            "periods": [p.strftime("%Y-%m") for p in periods], "source": pit.source_note(),
            "table": pd.DataFrame(rows)}


def metric_rank(details: pd.DataFrame, key: str) -> pd.DataFrame:
    """Rank companies on ONE metric: status first (Pass > Watch > Fail > no data), then value."""
    d = details[details["key"] == key][["symbol", "value", "display", "status"]].copy()
    if d.empty:
        return d.assign(metric_rank=pd.Series(dtype=float))
    d["_s"] = d["status"].map(STATUS_ORDER)
    v = pd.to_numeric(d["value"], errors="coerce")
    d["_v"] = v if key in LOWER_IS_BETTER else -v
    d = d.sort_values(["_s", "_v"], na_position="last")
    d["metric_rank"] = np.where(d["status"].isin([PASS, WATCH, FAIL]), range(1, len(d) + 1), np.nan)
    return d.drop(columns=["_s", "_v"]).reset_index(drop=True)
