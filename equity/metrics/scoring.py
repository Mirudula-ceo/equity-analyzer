"""Transparent scoring of metric results.

conservative_score = points earned / number of APPLICABLE metrics
    (Insufficient Data earns 0 points but still counts in the denominator,
     so missing data can never make a company look better)
coverage = evaluated metrics / applicable metrics
"""
from __future__ import annotations

import pandas as pd

from ..config import load_settings
from .engine import FAIL, INSUFFICIENT, NA, PASS, WATCH, MetricResult


def score(results: list[MetricResult], cfg: dict | None = None) -> dict:
    cfg = cfg or load_settings()
    pts = cfg["scoring"]["points"]
    applicable = [r for r in results if r.status != NA]
    evaluated = [r for r in applicable if r.status in (PASS, WATCH, FAIL)]
    earned = sum(pts[r.status] for r in evaluated)
    n_app = len(applicable)
    return {
        "points": earned,
        "applicable": n_app,
        "evaluated": len(evaluated),
        "insufficient": sum(r.status == INSUFFICIENT for r in results),
        "not_applicable": sum(r.status == NA for r in results),
        "pass": sum(r.status == PASS for r in results),
        "watch": sum(r.status == WATCH for r in results),
        "fail": sum(r.status == FAIL for r in results),
        "conservative_score": earned / n_app if n_app else 0.0,
        "score_on_evaluated": earned / len(evaluated) if evaluated else None,
        "coverage": len(evaluated) / n_app if n_app else 0.0,
    }


def rank(table: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """table: one row per symbol with score() fields. Adds 'eligible' and 'rank'."""
    cfg = cfg or load_settings()
    t = table.copy()
    t["eligible"] = t["coverage"] >= cfg["scoring"]["min_coverage_for_rank"]
    t = t.sort_values(["eligible", "conservative_score", "coverage"], ascending=[False, False, False])
    t["rank"] = range(1, len(t) + 1)
    t.loc[~t["eligible"], "rank"] = pd.NA
    return t
