"""NSE-wide company coverage (requirements section 1).

* The list of securities comes from NSE's official public files (mainboard,
  SME, ETF). The count is whatever those files contain today - never a constant.
* New listings get first_seen; symbols that disappear from a successfully
  downloaded list are marked "not in latest NSE list" (could be a delisting,
  suspension, merger or symbol change - we do not guess which).
* If the download is blocked, a copy of the same file saved by hand into
  data/manual/ is used instead; nothing is invented.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import requests

from ..config import PROJECT_ROOT, load_settings
from ..db import log_fetch, now_utc, upsert_df
from .constants import ACTIVE, NOT_IN_LATEST

UA = {"User-Agent": "Mozilla/5.0 (personal research script; EquityAnalyzer)",
      "Accept": "text/csv,application/octet-stream,*/*"}
MANUAL_DIR = PROJECT_ROOT / "data" / "manual"
MANUAL_FILES = {"MAINBOARD": "EQUITY_L.csv", "SME": "SME_EQUITY_L.csv", "ETF": "eq_etfseclist.csv",
                "SYMBOL_CHANGES": "symbolchange.csv"}
URL_KEYS = {"MAINBOARD": "mainboard_url", "SME": "sme_url", "ETF": "etf_url",
            "SYMBOL_CHANGES": "symbol_change_url"}


def _norm(c: str) -> str:
    return re.sub(r"\s+", " ", str(c).replace("_", " ")).strip().upper()


def _pick(df: pd.DataFrame, *candidates: str) -> pd.Series | None:
    cols = {_norm(c): c for c in df.columns}
    for cand in candidates:
        if cand in cols:
            return df[cols[cand]]
    for cand in candidates:                      # loose match, e.g. "DATEOFLISTING"
        for k, c in cols.items():
            if k.replace(" ", "") == cand.replace(" ", ""):
                return df[c]
    return None


def _text(s: pd.Series | None, n: int) -> pd.Series:
    if s is None:
        return pd.Series([None] * n, dtype=object)
    t = s.astype(str).str.strip()
    return t.where(~t.isin(["", "nan", "None"]), None)


def _dates(s: pd.Series | None) -> pd.Series:
    if s is None:
        return pd.Series(dtype=object)
    from .events import parse_date
    return s.astype(str).str.strip().map(lambda v: parse_date(v) if v and v != "nan" else None)


def parse_listing(df: pd.DataFrame, segment: str) -> pd.DataFrame:
    """Normalise one official NSE list into the nse_listings columns."""
    sym = _pick(df, "SYMBOL")
    if sym is None:
        raise ValueError(f"{segment} list has no SYMBOL column (columns: {list(df.columns)[:8]})")
    name = _pick(df, "NAME OF COMPANY", "SECURITY NAME", "SECURITYNAME", "COMPANY NAME", "UNDERLYING")
    series = _pick(df, "SERIES")
    out = pd.DataFrame({
        "symbol": sym.astype(str).str.strip().str.upper(),
        "isin": _text(_pick(df, "ISIN NUMBER", "ISIN", "ISINNUMBER"), len(df)),
        "name": (name if name is not None else sym).astype(str).str.strip(),
        "segment": segment,
        "series": (series.astype(str).str.strip() if series is not None
                   else pd.Series(["ETF" if segment == "ETF" else None] * len(df))),
        "listing_date": _dates(_pick(df, "DATE OF LISTING", "DATEOFLISTING", "LISTING DATE")),
        "face_value": pd.to_numeric(_pick(df, "FACE VALUE", "FACEVALUE"), errors="coerce")
        if _pick(df, "FACE VALUE", "FACEVALUE") is not None else None,
    })
    out = out[out["symbol"].ne("") & out["symbol"].ne("NAN")]
    return out.drop_duplicates("symbol").reset_index(drop=True)


def parse_symbol_changes(df: pd.DataFrame) -> pd.DataFrame:
    """NSE's symbolchange.csv (header names vary; positional fallback: company, old, new, date)."""
    old = _pick(df, "SM KEY SYMBOL", "OLD SYMBOL", "PREVIOUS SYMBOL", "SYMBOL")
    new = _pick(df, "SM NEW SYMBOL", "NEW SYMBOL", "CHANGED SYMBOL")
    when = _pick(df, "SM APPLICABLE FROM", "DATE OF CHANGE", "EFFECTIVE DATE", "APPLICABLE FROM", "DATE")
    comp = _pick(df, "SM NAME", "COMPANY NAME", "NAME OF COMPANY", "COMPANY")
    if old is None or new is None:
        if df.shape[1] < 4:
            raise ValueError(f"cannot read symbol-change columns: {list(df.columns)}")
        comp, old, new, when = (df.iloc[:, i] for i in range(4))
    out = pd.DataFrame({"old_symbol": old.astype(str).str.strip().str.upper(),
                        "new_symbol": new.astype(str).str.strip().str.upper(),
                        "effective_date": _dates(when) if when is not None else None,
                        "company": comp.astype(str).str.strip() if comp is not None else None})
    out["effective_date"] = out["effective_date"].fillna("unknown")
    return out[(out["old_symbol"] != out["new_symbol"]) & out["new_symbol"].ne("")].drop_duplicates(
        ["old_symbol", "new_symbol", "effective_date"])


def _download(url: str, get=requests.get) -> pd.DataFrame:
    r = get(url, headers=UA, timeout=30)
    r.raise_for_status()
    text = r.text if hasattr(r, "text") else r
    if "<html" in text[:500].lower():
        raise ValueError("received an HTML page instead of a CSV (probably blocked)")
    return pd.read_csv(io.StringIO(text))


def _load(kind: str, cfg: dict, con, get, manual_dir: Path) -> tuple[pd.DataFrame | None, str | None]:
    url = cfg["research"][URL_KEYS[kind]]
    try:
        df = _download(url, get)
        log_fetch(con, kind, "nse_listing", "ok", f"{len(df)} rows", url)
        return df, f"NSE official list ({url})"
    except Exception as exc:  # noqa: BLE001
        manual = manual_dir / MANUAL_FILES[kind]
        if manual.exists():
            log_fetch(con, kind, "nse_listing", "fallback", f"download failed: {exc}", str(manual))
            return pd.read_csv(manual), f"manual copy of NSE file: data/manual/{manual.name}"
        log_fetch(con, kind, "nse_listing", "error", str(exc), url)
        return None, None


def refresh_listings(con, cfg: dict | None = None, get=requests.get,
                     manual_dir: Path | None = None) -> dict:
    """Download every official list and update nse_listings. Returns per-segment summary."""
    cfg = cfg or load_settings()
    manual_dir = manual_dir or MANUAL_DIR
    now = now_utc()
    summary = {}
    for seg in ("MAINBOARD", "SME", "ETF"):
        raw, source = _load(seg, cfg, con, get, manual_dir)
        if raw is None:
            summary[seg] = {"status": "unavailable - previous list kept"}
            continue
        try:
            new = parse_listing(raw, seg)
        except ValueError as exc:
            log_fetch(con, seg, "nse_listing", "error", str(exc), source)
            summary[seg] = {"status": f"unreadable - previous list kept ({exc})"}
            continue
        summary[seg] = apply_listing(con, new, seg, source, now)
    raw, source = _load("SYMBOL_CHANGES", cfg, con, get, manual_dir)
    if raw is not None:
        try:
            ch = parse_symbol_changes(raw)
            ch["source"], ch["retrieved_at"] = source, now
            summary["SYMBOL_CHANGES"] = {"rows": upsert_df(con, "symbol_changes", ch)}
        except ValueError as exc:
            log_fetch(con, "SYMBOL_CHANGES", "nse_listing", "error", str(exc), source)
    return summary


def apply_listing(con, new: pd.DataFrame, segment: str, source: str, now: str | None = None) -> dict:
    """Merge one freshly parsed segment list into nse_listings (keeps first_seen)."""
    now = now or now_utc()
    old = pd.read_sql_query("SELECT symbol, first_seen, status FROM nse_listings WHERE segment=?",
                            con, params=[segment])
    first = dict(zip(old["symbol"], old["first_seen"]))
    new = new.copy()
    new["first_seen"] = new["symbol"].map(first).fillna(now)
    new["last_seen"], new["status"], new["source"], new["retrieved_at"] = now, ACTIVE, source, now
    added = sorted(set(new["symbol"]) - set(old["symbol"])) if not old.empty else []
    gone = sorted(set(old.loc[old["status"] == ACTIVE, "symbol"]) - set(new["symbol"]))
    upsert_df(con, "nse_listings", new)
    if gone:
        con.executemany("UPDATE nse_listings SET status=? WHERE symbol=? AND segment=?",
                        [(NOT_IN_LATEST, s, segment) for s in gone])
        con.commit()
    return {"listed": len(new), "new_since_last_refresh": len(added) if not old.empty else 0,
            "dropped_from_list": len(gone), "source": source,
            "new_symbols": added[:50], "dropped_symbols": gone[:50]}


# ---------------------------------------------------------------- lookups
def read_listings(con, segment: str | None = None, include_inactive: bool = True) -> pd.DataFrame:
    q, p = "SELECT * FROM nse_listings WHERE 1=1", []
    if segment:
        q += " AND segment=?"
        p.append(segment)
    if not include_inactive:
        q += " AND status=?"
        p.append(ACTIVE)
    return pd.read_sql_query(q + " ORDER BY symbol", con, params=p)


def is_eligible(row: dict | pd.Series, cfg: dict | None = None) -> tuple[bool, str]:
    cfg = cfg or load_settings()
    r = cfg["research"]
    if row.get("status") not in (None, ACTIVE):
        return False, "not in latest NSE list"
    if row.get("segment") not in r["eligible_segments"]:
        return False, f"{row.get('segment')} securities are listed but not scored (settings: eligible_segments)"
    if row.get("series") and row.get("series") not in r["eligible_series"]:
        return False, f"series {row.get('series')} not in eligible_series"
    return True, "eligible for 27-metric analysis"


def eligible_symbols(con, cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_settings()
    lst = read_listings(con, include_inactive=False)
    return [r["symbol"] for _, r in lst.iterrows() if is_eligible(r, cfg)[0]]


def resolve_symbol(con, symbol: str) -> str:
    """Follow NSE symbol changes to today's symbol (loop-safe)."""
    s, seen = symbol.strip().upper(), set()
    while s not in seen:
        seen.add(s)
        row = con.execute("SELECT new_symbol FROM symbol_changes WHERE old_symbol=? "
                          "ORDER BY effective_date DESC LIMIT 1", (s,)).fetchone()
        if not row:
            break
        s = row[0]
    return s


def symbol_aliases(con, symbol: str) -> list[str]:
    """Current symbol plus every former symbol that maps to it."""
    out, todo = {symbol}, [symbol]
    while todo:
        cur = todo.pop()
        for (old,) in con.execute("SELECT old_symbol FROM symbol_changes WHERE new_symbol=?", (cur,)):
            if old not in out:
                out.add(old)
                todo.append(old)
    return sorted(out)


def company_identity(con, symbol: str) -> dict:
    """Best-known identity for a symbol: official listing first, then Nifty universe, then Yahoo info."""
    row = con.execute("SELECT symbol,isin,name,segment,series,listing_date,status,source FROM nse_listings "
                      "WHERE symbol=?", (symbol,)).fetchone()
    if row:
        return dict(zip(["symbol", "isin", "name", "segment", "series", "listing_date", "status", "source"], row))
    row = con.execute("SELECT symbol, company, source FROM universe WHERE symbol=?", (symbol,)).fetchone()
    if row:
        return {"symbol": row[0], "name": row[1], "segment": "MAINBOARD", "series": None, "isin": None,
                "status": ACTIVE, "source": row[2] + " (official NSE list not loaded yet)"}
    row = con.execute("SELECT name FROM company_info WHERE symbol=?", (symbol,)).fetchone()
    if row:
        return {"symbol": symbol, "name": row[0], "segment": None, "series": None, "isin": None,
                "status": None, "source": "Yahoo Finance company info"}
    return {}


def listing_summary(con) -> pd.DataFrame:
    return pd.read_sql_query("SELECT segment, status, COUNT(*) AS companies, MAX(retrieved_at) AS retrieved_at, "
                             "MAX(source) AS source FROM nse_listings GROUP BY segment, status "
                             "ORDER BY segment, status", con)
