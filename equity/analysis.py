"""High-level helpers used by the dashboard and scripts."""
from __future__ import annotations

import pandas as pd

from . import db
from .config import load_settings
from .metrics.engine import MetricEngine, load_qualitative, results_frame
from .metrics.pit import PITData
from .metrics.scoring import rank, score


def build_pit(con, symbol: str, as_of=None, prices=None, actions=None, fin=None) -> PITData:
    prices = prices if prices is not None else db.read_prices(con, [symbol])
    actions = actions if actions is not None else db.read_actions(con, [symbol])
    fin = fin if fin is not None else db.read_financials(con, symbol)
    sp = actions[(actions["action_type"] == "split")] if not actions.empty else actions
    splits = pd.Series(sp["value"].values, index=sp["date"]) if not sp.empty else pd.Series(dtype=float)
    info = db.read_info(con, symbol)
    return PITData(symbol, fin, prices[["date", "close"]] if not prices.empty else
                   pd.DataFrame(columns=["date", "close"]), splits, as_of, info)


def analyze_symbol(con, symbol: str, as_of=None, cfg=None, qual=None):
    cfg = cfg or load_settings()
    pit = build_pit(con, symbol, as_of)
    eng = MetricEngine(pit, cfg, qual if qual is not None else load_qualitative(),
                       db.read_shareholding(con, symbol))
    res = eng.run()
    return res, score(res, cfg), pit


def rank_symbols(con, symbols: list[str], as_of=None, cfg=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = cfg or load_settings()
    qual = load_qualitative()
    rows, details = [], []
    for s in symbols:
        res, sc, pit = analyze_symbol(con, s, as_of, cfg, qual)
        rows.append({"symbol": s, "name": pit.info.get("longName") or pit.info.get("shortName") or s,
                     "sector": pit.info.get("sector"), **sc})
        f = results_frame(res)
        f.insert(0, "symbol", s)
        details.append(f)
    table = rank(pd.DataFrame(rows), cfg) if rows else pd.DataFrame()
    return table, (pd.concat(details, ignore_index=True) if details else pd.DataFrame())
