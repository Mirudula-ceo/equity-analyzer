"""Rank history, watchlist alerts and data freshness (requirements sections 6 and 10)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

import pandas as pd

from ..config import load_settings
from ..db import now_utc
from .constants import CANCELLED, DELAYED


def settings_hash(cfg: dict | None = None) -> str:
    cfg = cfg or load_settings()
    key = json.dumps({"t": cfg["thresholds"], "s": cfg["scoring"], "d": cfg.get("dcf")}, sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- rank snapshots
def save_snapshot(con, table: pd.DataFrame, details: pd.DataFrame, universe_label: str,
                  snapshot_date: str | None = None, cfg: dict | None = None) -> str:
    """Store one ranking run so later runs can explain what changed."""
    snapshot_date = snapshot_date or date.today().isoformat()
    sid = f"{snapshot_date}_{universe_label}_{datetime.now(timezone.utc):%H%M%S%f}"
    statuses = details.groupby("symbol").apply(lambda g: json.dumps(dict(zip(g["key"], g["status"]))),
                                               include_groups=False)
    h = settings_hash(cfg)
    rows = [(sid, snapshot_date, universe_label, r["symbol"],
             None if pd.isna(r["rank"]) else int(r["rank"]), int(bool(r["eligible"])), float(r["points"]),
             int(r["applicable"]), float(r["conservative_score"]), float(r["coverage"]),
             statuses.get(r["symbol"], "{}"), h) for _, r in table.iterrows()]
    con.executemany("INSERT OR REPLACE INTO rank_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return sid


def rank_history(con, symbol: str, universe_label: str | None = None) -> pd.DataFrame:
    q, p = "SELECT * FROM rank_history WHERE symbol=?", [symbol]
    if universe_label:
        q += " AND universe_label=?"
        p.append(universe_label)
    return pd.read_sql_query(q + " ORDER BY snapshot_id", con, params=p)


def previous_snapshot(con, symbol: str, universe_label: str, before_id: str | None = None) -> dict | None:
    """Most recent saved snapshot for this symbol (strictly before `before_id` if given)."""
    q = "SELECT * FROM rank_history WHERE symbol=? AND universe_label=?"
    p: list = [symbol, universe_label]
    if before_id:
        q += " AND snapshot_id < ?"
        p.append(before_id)
    row = pd.read_sql_query(q + " ORDER BY snapshot_id DESC LIMIT 1", con, params=p)
    return None if row.empty else row.iloc[0].to_dict()


# ---------------------------------------------------------------- watchlist & alerts
def watch(con, symbol: str, note: str = "") -> None:
    con.execute("INSERT OR REPLACE INTO watchlist VALUES (?,?,?)", (symbol, now_utc(), note))
    con.commit()


def unwatch(con, symbol: str) -> None:
    con.execute("DELETE FROM watchlist WHERE symbol=?", (symbol,))
    con.commit()


def watchlist(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT symbol FROM watchlist ORDER BY symbol")]


def _alert(con, symbol, kind, message, ref) -> int:
    cur = con.execute("INSERT OR IGNORE INTO alerts (created_at, symbol, alert_type, message, ref) "
                      "VALUES (?,?,?,?,?)", (now_utc(), symbol, kind, message, ref))
    return cur.rowcount


def generate_alerts(con, snapshot_id: str | None = None, cfg: dict | None = None) -> int:
    """Alerts for watch-listed companies: big rank moves, alert-category events, delays/cancellations,
    and exchange filings waiting for review. Each alert is created once (unique on symbol/type/ref)."""
    cfg = cfg or load_settings()
    a = cfg["research"]["alerts"]
    syms = watchlist(con)
    if not syms:
        return 0
    n = 0
    ph = ",".join("?" * len(syms))
    if snapshot_id:
        cur = pd.read_sql_query(f"SELECT * FROM rank_history WHERE snapshot_id=? AND symbol IN ({ph})",
                                con, params=[snapshot_id, *syms])
        for _, r in cur.iterrows():
            prev = previous_snapshot(con, r["symbol"], r["universe_label"], snapshot_id)
            if not prev:
                continue
            ro, rn = prev.get("rank"), r["rank"]
            if pd.isna(ro) != pd.isna(rn):
                n += _alert(con, r["symbol"], "rank change",
                            f"{r['symbol']} {'entered' if pd.isna(ro) else 'dropped out of'} the ranking "
                            f"({r['universe_label']})", snapshot_id)
            elif not pd.isna(ro) and abs(int(rn) - int(ro)) >= a["rank_change_min"]:
                n += _alert(con, r["symbol"], "rank change",
                            f"{r['symbol']} rank {int(ro)} → {int(rn)} ({r['universe_label']})", snapshot_id)
    ev = pd.read_sql_query(f"SELECT event_id, symbol, title, category, status FROM events WHERE symbol IN ({ph})",
                           con, params=syms)
    for _, e in ev.iterrows():
        if e["category"] in a["alert_categories"]:
            n += _alert(con, e["symbol"], e["category"], f"{e['symbol']}: {e['title']} ({e['status']})",
                        f"{e['event_id']}:{e['status']}")
        if e["status"] in (DELAYED, CANCELLED):
            n += _alert(con, e["symbol"], f"project {e['status'].lower()}",
                        f"{e['symbol']}: {e['title']} is now {e['status']}", f"{e['event_id']}:{e['status']}")
    inbox = pd.read_sql_query(f"SELECT guid, symbol, title, suggested_category FROM filings_inbox WHERE "
                              f"review_state='to review' AND symbol IN ({ph})", con, params=syms)
    for _, f in inbox.iterrows():
        if f["suggested_category"] in a["alert_categories"]:
            n += _alert(con, f["symbol"], "filing to review", f"{f['symbol']}: {f['title']}", f["guid"])
    con.commit()
    return n


def read_alerts(con, unseen_only: bool = False) -> pd.DataFrame:
    q = "SELECT * FROM alerts" + (" WHERE seen=0" if unseen_only else "")
    return pd.read_sql_query(q + " ORDER BY id DESC", con)


def mark_alerts_seen(con, ids: list[int] | None = None) -> None:
    if ids:
        con.executemany("UPDATE alerts SET seen=1 WHERE id=?", [(i,) for i in ids])
    else:
        con.execute("UPDATE alerts SET seen=1")
    con.commit()


# ---------------------------------------------------------------- freshness
def _age_days(ts: str | None, today: pd.Timestamp) -> float | None:
    if not ts:
        return None
    t = pd.to_datetime(ts, errors="coerce", utc=True)
    return None if pd.isna(t) else round((today - t).total_seconds() / 86400, 1)


def _latest(*ts):
    v = [t for t in ts if t]
    return max(v) if v else None


def _snapshot_ann_time() -> str | None:
    """Announcements stored by the GitHub Action (data/snapshot/) count as a refresh too."""
    from . import snapshot
    a = snapshot.load_announcements()
    if a.empty or "retrieved_at" not in a:
        return None
    v = a["retrieved_at"].dropna()
    return str(v.max()) if len(v) else None


def freshness(con, symbol: str | None = None, cfg: dict | None = None, today=None) -> pd.DataFrame:
    """Each dataset's last successful update, its refresh limit and a visible OK / Stale / Missing state.
    The last failed attempt (if any) is shown too, so a broken source is never silent."""
    cfg = cfg or load_settings()
    lim = cfg["research"]["refresh_days"]
    today = pd.Timestamp(today or datetime.now(timezone.utc))
    today = today.tz_localize("UTC") if today.tzinfo is None else today
    w, p = ("WHERE symbol=?", [symbol]) if symbol else ("", [])
    q = lambda sql, params=(): con.execute(sql, params).fetchone()[0]  # noqa: E731
    rows = [
        ("prices", q(f"SELECT MAX(retrieved_at) FROM prices {w}", p), lim["prices"], "prices"),
        ("financial results", q(f"SELECT MAX(retrieved_at) FROM financials {w}", p), lim["financials"], "%annual"),
        ("NSE company list", q("SELECT MAX(retrieved_at) FROM nse_listings"), lim["listings"], "nse_listing"),
        ("exchange announcements", _latest(q("SELECT MAX(retrieved_at) FROM filings_inbox"), _snapshot_ann_time()),
         lim["announcements"], "announcements"),
    ]
    out = []
    for name, ts, limit, dataset in rows:
        age = _age_days(ts, today)
        per_symbol = symbol and dataset in ("prices", "%annual")
        err = con.execute("SELECT retrieved_at, message FROM fetch_log WHERE dataset LIKE ? AND status='error' "
                          + ("AND symbol=? " if per_symbol else "") + "ORDER BY id DESC LIMIT 1",
                          [dataset] + ([symbol] if per_symbol else [])).fetchone()
        failed_after = bool(err and (ts is None or err[0] > ts))
        state = "Missing" if ts is None else ("Stale" if age is not None and age > limit else "OK")
        if failed_after:
            state += " · last refresh failed"
        out.append({"dataset": name, "last_updated": ts, "age_days": age, "refresh_limit_days": limit,
                    "state": state, "last_error": err[1] if failed_after else ""})
    return pd.DataFrame(out)
