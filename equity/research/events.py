"""Permanent company timeline of important events (requirements sections 3, 4, 5, 7, 10).

How events get in
-----------------
Reading annual reports, investor presentations, filings and news is human
work in this project (no scraping behind anti-bot protection). A reviewer
records each development as a row in a CSV (template:
data/manual/company_events_TEMPLATE.csv) and runs the import. The import:

* checks identity - the symbol must be a known NSE company (old symbols are
  followed to the current one) and, if the source's company name / ISIN is
  given, it must match, so news is never attached to the wrong company;
* refuses to show a future plan as a running business;
* merges the same development reported by several sources into ONE event and
  attaches later updates to it (every change is kept in event_updates);
* never deletes anything - the summary, source metadata and verification
  history survive even if the original article disappears. Only our own
  summary is stored, never the article text.

Events are research context. They do not change any of the 27 metric
results, the score or the rank (settings: research.event_scoring = false).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from ..config import PROJECT_ROOT, load_settings
from ..db import now_utc
from .constants import (BUSINESS_STAGES, CANCELLED, CATEGORIES, COMPLETED, CONFIRMED, CONFLICTING, DELAYED,
                        EVENT_EVIDENCE, EVENT_STATUSES, EVIDENCE_STATUSES, FINANCIAL_LINKS, GUIDANCE,
                        NOT_APPLICABLE, NOT_FOUND, OFFICIAL_SOURCE_TYPES, REFLECTED, REPORTED,
                        RESEARCH_TOPICS, SOURCE_TYPES)
from .listings import company_identity, resolve_symbol, symbol_aliases

EVENTS_CSV = PROJECT_ROOT / "data" / "manual" / "company_events.csv"
COVERAGE_CSV = PROJECT_ROOT / "data" / "manual" / "research_coverage.csv"

EVENT_COLUMNS = [
    "symbol", "title", "category", "event_date", "published_date", "status", "evidence_status",
    "summary", "business_relevance", "segment_affected", "business_stage", "financial_link",
    "impact_quantified", "impact_note", "reflected_in_metrics", "expected_timeline", "next_milestone",
    "source_name", "source_type", "source_url", "last_verified", "company_name_in_source", "isin_in_source",
    "event_ref",
]
REQUIRED = ["symbol", "title", "category", "published_date", "status", "evidence_status", "summary",
            "source_name", "source_type", "source_url"]
COVERAGE_COLUMNS = ["symbol", "topic", "source_type", "searched_on", "result", "note", "source_url"]
OPEN_EVENT_SIMILARITY = 0.85        # later updates to a still-open event (use event_ref to be certain)
TRACKED_FIELDS = ["status", "evidence_status", "expected_timeline", "next_milestone", "event_date",
                  "impact_note", "reflected_in_metrics", "business_stage"]
_LEGAL = re.compile(r"\b(ltd|limited|pvt|private|plc|inc|corp|corporation|company|co|the|and|india|"
                    r"industries|enterprises?)\b\.?")


# ---------------------------------------------------------------- small helpers
def _clean(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _date(v) -> str | None:
    v = _clean(v)
    if not v:
        return None
    return parse_date(v)


def parse_date(v: str) -> str | None:
    """ISO (YYYY-MM-DD) first; otherwise Indian day-first dates such as 05-03-2025 or 05-Mar-2025."""
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", v):
        d = pd.to_datetime(v[:10], errors="coerce", format="%Y-%m-%d")
    else:
        d = pd.to_datetime(v, errors="coerce", dayfirst=True)
    return None if pd.isna(d) else d.strftime("%Y-%m-%d")


def norm_title(t: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def title_similarity(a: str, b: str) -> float:
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jacc = len(ta & tb) / len(ta | tb)
    return max(jacc, SequenceMatcher(None, a, b).ratio())


def name_matches(official: str | None, given: str | None) -> bool:
    """Loose company-name check used to stop news being linked to the wrong company."""
    if not official or not given:
        return True
    a = set(_LEGAL.sub(" ", norm_title(official)).split())
    b = set(_LEGAL.sub(" ", norm_title(given)).split())
    if not a or not b:
        return True
    return len(a & b) / min(len(a), len(b)) >= 0.5


def make_event_id(symbol, category, title, event_date) -> str:
    key = f"{symbol}|{category}|{norm_title(title)}|{event_date or ''}"
    return "EV" + hashlib.sha1(key.encode()).hexdigest()[:12]


def _pick_enum(v, allowed: list[str]) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    for a in allowed:
        if v.lower() == a.lower():
            return a
    return None


# ---------------------------------------------------------------- validation
@dataclass
class RowCheck:
    row: int
    data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_event_row(con, raw: dict, row_no: int, today: str | None = None) -> RowCheck:
    today = today or date.today().isoformat()
    chk = RowCheck(row_no)
    d = {c: _clean(raw.get(c)) for c in EVENT_COLUMNS}
    for c in REQUIRED:
        if not d.get(c):
            chk.errors.append(f"missing {c}")

    # identity (section 10: never attach news to the wrong company)
    if d["symbol"]:
        given = d["symbol"].upper()
        cur = resolve_symbol(con, given)
        if cur != given:
            chk.warnings.append(f"symbol {given} was changed to {cur} on NSE; stored under {cur}")
        ident = company_identity(con, cur)
        if not ident:
            chk.errors.append(f"unknown symbol {given}: not in NSE listings / universe / company info "
                              "(refresh listings or fix the symbol)")
        else:
            if not name_matches(ident.get("name"), d["company_name_in_source"]):
                chk.errors.append(f"identity mismatch: source names '{d['company_name_in_source']}' but "
                                  f"{cur} is '{ident.get('name')}'")
            if d["isin_in_source"] and ident.get("isin") and d["isin_in_source"].upper() != ident["isin"].upper():
                chk.errors.append(f"identity mismatch: ISIN {d['isin_in_source']} != {ident['isin']}")
            d["isin"] = ident.get("isin")
        d["symbol"] = cur

    if d.get("event_ref") and not con.execute("SELECT 1 FROM events WHERE event_id=?", (d["event_ref"],)).fetchone():
        chk.errors.append(f"event_ref {d['event_ref']} does not exist (copy the id from the Company Research page)")

    # vocabularies
    for col, allowed in (("category", CATEGORIES), ("status", EVENT_STATUSES),
                         ("evidence_status", EVENT_EVIDENCE), ("business_stage", BUSINESS_STAGES),
                         ("reflected_in_metrics", REFLECTED)):
        if d.get(col):
            v = _pick_enum(d[col], allowed)
            if v is None:
                extra = (" ('Not found' / 'Not applicable' belong in research_coverage.csv)"
                         if col == "evidence_status" else "")
                chk.errors.append(f"{col} '{d[col]}' not one of: {', '.join(allowed)}{extra}")
            d[col] = v
    if d.get("source_type") and d["source_type"] not in SOURCE_TYPES:
        chk.errors.append(f"source_type '{d['source_type']}' not one of: {', '.join(SOURCE_TYPES)}")
    if d.get("financial_link"):
        parts = [p.strip().lower() for p in re.split(r"[;,|/]", d["financial_link"]) if p.strip()]
        bad = [p for p in parts if p not in FINANCIAL_LINKS]
        if bad:
            chk.errors.append(f"financial_link {bad} not in {FINANCIAL_LINKS}")
        d["financial_link"] = ";".join(parts)
    if d.get("source_url") and not re.match(r"https?://", d["source_url"]):
        chk.errors.append("source_url must start with http:// or https://")

    # dates
    for col in ("event_date", "published_date", "last_verified"):
        if d.get(col):
            v = _date(d[col])
            if v is None:
                chk.errors.append(f"{col} '{d[col]}' is not a date (use YYYY-MM-DD)")
            d[col] = v
    if d.get("published_date") and d["published_date"] > today:
        chk.errors.append("published_date is in the future")
    d["last_verified"] = d.get("last_verified") or today
    imp = (d.get("impact_quantified") or "").lower()
    d["impact_quantified"] = 1 if imp in ("1", "yes", "y", "true") else 0
    # Defaults are applied only when an event is first created (see upsert_event), so a
    # later row that leaves a field blank can never overwrite what is already known.
    if d.get("status") == COMPLETED and not d.get("business_stage") and d.get("evidence_status") != GUIDANCE:
        d["business_stage"] = "existing"

    # section 4: a future plan must never be shown as a running business
    if d.get("status") == COMPLETED:
        if d.get("business_stage") == "proposed":
            chk.errors.append("status Completed but business_stage is 'proposed' - a completed plan is an "
                              "'existing' business; otherwise use Announced/Underway/Delayed")
        if d.get("evidence_status") == GUIDANCE:
            chk.errors.append("status Completed needs evidence that it happened (Confirmed), not Management "
                              "guidance")
        if d.get("event_date") and d["event_date"] > today:
            chk.errors.append("status Completed with an event_date in the future")
    if d.get("evidence_status") == GUIDANCE and d.get("business_stage") == "existing":
        chk.warnings.append("Management guidance on an existing business - check business_stage")
    if d.get("status") in (COMPLETED, CANCELLED) and not d.get("event_date"):
        chk.warnings.append("no event_date for a completed/cancelled event")
    if d.get("source_type") and d["source_type"] not in OFFICIAL_SOURCE_TYPES and d.get("evidence_status") == CONFIRMED:
        chk.warnings.append("Confirmed on a non-official source only - consider 'Reported / unconfirmed'")
    if d.get("impact_quantified") and not d.get("impact_note"):
        chk.warnings.append("impact_quantified = yes but no impact_note with the figure")
    chk.data = d
    return chk


# ---------------------------------------------------------------- de-duplication
def find_existing(con, d: dict, cfg: dict) -> tuple[str | None, str]:
    if d.get("event_ref"):
        row = con.execute("SELECT event_id FROM events WHERE event_id=?", (d["event_ref"],)).fetchone()
        return (row[0], "event_ref") if row else (None, "event_ref not found")
    row = con.execute("SELECT s.event_id FROM event_sources s JOIN events e ON e.event_id=s.event_id "
                      "WHERE s.url=? AND e.symbol=?", (d["source_url"], d["symbol"])).fetchone()
    if row:
        return row[0], "same source URL"
    r = cfg["research"]
    aliases = symbol_aliases(con, d["symbol"])
    cands = pd.read_sql_query(
        f"SELECT event_id, title, event_date, published_date, status FROM events WHERE category=? "
        f"AND symbol IN ({','.join('?' * len(aliases))})", con, params=[d["category"], *aliases])
    best, best_sim, best_why = None, 0.0, ""
    ref = pd.Timestamp(_clean(d.get("event_date")) or d["published_date"])
    for _, c in cands.iterrows():
        other = pd.Timestamp(_clean(c["event_date"]) or c["published_date"])
        sim = title_similarity(d["title"], c["title"])
        near = abs((ref - other).days) <= r["dedupe_window_days"]
        if near and sim >= r["dedupe_title_similarity"]:
            why = f"similar title ({sim:.0%}) within {r['dedupe_window_days']} days"
        elif c["status"] not in (COMPLETED, CANCELLED) and sim >= OPEN_EVENT_SIMILARITY:
            why = f"near-identical title ({sim:.0%}) on an open event - treated as an update"
        else:
            continue
        if sim > best_sim:
            best, best_sim, best_why = c["event_id"], sim, why
    return best, best_why


def _log_update(con, event_id, fld, old, new, note, url, published, now):
    con.execute("INSERT INTO event_updates (event_id, field, old_value, new_value, note, source_url, "
                "published_date, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, fld, None if old is None else str(old), None if new is None else str(new),
                 note, url, published, now))


def _add_source(con, event_id, d, now):
    con.execute("INSERT OR IGNORE INTO event_sources (event_id,url,source_name,source_type,published_date,"
                "retrieved_at,link_status) VALUES (?,?,?,?,?,?,?)",
                (event_id, d["source_url"], d["source_name"], d["source_type"], d["published_date"], now,
                 "not checked"))


def upsert_event(con, d: dict, cfg: dict | None = None, now: str | None = None) -> tuple[str, str, str]:
    """Insert a validated row or merge it into an existing event. Returns (action, event_id, note)."""
    cfg = cfg or load_settings()
    now = now or now_utc()
    existing, why = find_existing(con, d, cfg)
    if existing is None:
        eid = make_event_id(d["symbol"], d["category"], d["title"], d.get("event_date"))
        if con.execute("SELECT 1 FROM events WHERE event_id=?", (eid,)).fetchone():
            existing, why = eid, "identical event id"
    if existing is None:
        cols = ["event_id", "symbol", "isin", "title", "category", "event_date", "published_date",
                "first_captured", "summary", "business_relevance", "segment_affected", "business_stage",
                "financial_link", "impact_quantified", "impact_note", "reflected_in_metrics",
                "expected_timeline", "next_milestone", "status", "evidence_status", "last_verified", "updated_at"]
        vals = {**d, "event_id": eid, "first_captured": now, "updated_at": now,
                "business_stage": d.get("business_stage") or ("proposed" if d.get("evidence_status") == GUIDANCE
                                                              else "existing"),
                "reflected_in_metrics": d.get("reflected_in_metrics") or "unknown"}
        con.execute(f"INSERT INTO events ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [vals.get(c) for c in cols])
        _add_source(con, eid, d, now)
        for f in ("status", "evidence_status", "expected_timeline"):
            if d.get(f):
                _log_update(con, eid, f, None, d[f], "first captured", d["source_url"], d["published_date"], now)
        con.commit()
        return "created", eid, ""

    cur = dict(zip([c[0] for c in con.execute("SELECT * FROM events LIMIT 0").description],
                   con.execute("SELECT * FROM events WHERE event_id=?", (existing,)).fetchone()))
    _add_source(con, existing, d, now)
    latest_pub = con.execute("SELECT MAX(published_date) FROM event_sources WHERE event_id=? AND url<>?",
                             (existing, d["source_url"])).fetchone()[0]
    newer = latest_pub is None or (d["published_date"] or "") >= latest_pub
    changes = []
    for f in TRACKED_FIELDS:
        nv, ov = d.get(f), cur.get(f)
        if nv in (None, "", "unknown") or nv == ov:
            continue
        if f == "evidence_status":
            nv = _merge_evidence(ov, nv, d["source_type"])
            if nv == ov:
                continue
        elif not newer:
            _log_update(con, existing, f, ov, nv, "older source disagrees - not applied, kept for history",
                        d["source_url"], d["published_date"], now)
            continue
        _log_update(con, existing, f, ov, nv, f"update from {d['source_name']}", d["source_url"],
                    d["published_date"], now)
        changes.append(f)
        con.execute(f"UPDATE events SET {f}=? WHERE event_id=?", (nv, existing))
    lv = max(filter(None, [cur.get("last_verified"), d.get("last_verified")]), default=None)
    con.execute("UPDATE events SET last_verified=?, updated_at=? WHERE event_id=?", (lv, now, existing))
    if newer and d.get("summary") and d["summary"] != cur.get("summary") and "summary" not in changes:
        _log_update(con, existing, "summary", cur.get("summary"), d["summary"], "newer summary",
                    d["source_url"], d["published_date"], now)
        con.execute("UPDATE events SET summary=? WHERE event_id=?", (d["summary"], existing))
    con.commit()
    return ("updated" if changes else "merged"), existing, why


def _merge_evidence(old: str | None, new: str, source_type: str) -> str:
    """Evidence can be upgraded by an official source; a disagreement is made visible, never hidden."""
    if old is None or old == new:
        return new
    if CONFLICTING in (old, new):
        return CONFLICTING
    if new == CONFIRMED and source_type in OFFICIAL_SOURCE_TYPES:
        return CONFIRMED
    if old == CONFIRMED and new in (REPORTED, GUIDANCE):
        return CONFIRMED                     # a weaker repeat does not downgrade confirmed evidence
    if old == REPORTED and new == GUIDANCE:
        return GUIDANCE
    return old


@dataclass
class ImportReport:
    rows: pd.DataFrame

    @property
    def counts(self) -> dict:
        return self.rows["action"].value_counts().to_dict() if not self.rows.empty else {}


def import_events(con, df: pd.DataFrame, cfg: dict | None = None, today: str | None = None) -> ImportReport:
    cfg = cfg or load_settings()
    out = []
    for i, raw in enumerate(df.to_dict("records"), start=2):          # row 1 = header in Excel terms
        chk = validate_event_row(con, raw, i, today)
        if chk.errors:
            out.append({"row": i, "symbol": chk.data.get("symbol"), "title": chk.data.get("title"),
                        "action": "rejected", "event_id": None, "message": "; ".join(chk.errors)})
            continue
        action, eid, note = upsert_event(con, chk.data, cfg)
        out.append({"row": i, "symbol": chk.data["symbol"], "title": chk.data["title"], "action": action,
                    "event_id": eid, "message": "; ".join(filter(None, [note, *chk.warnings]))})
    return ImportReport(pd.DataFrame(out, columns=["row", "symbol", "title", "action", "event_id", "message"]))


def import_events_csv(con, path: Path | None = None, cfg: dict | None = None) -> ImportReport | None:
    path = Path(path) if path else EVENTS_CSV
    if not path.exists():
        return None
    return import_events(con, pd.read_csv(path, dtype=str), cfg)


# ---------------------------------------------------------------- research coverage ("where we looked")
def import_coverage(con, df: pd.DataFrame, today: str | None = None) -> pd.DataFrame:
    today = today or date.today().isoformat()
    now, out = now_utc(), []
    for i, raw in enumerate(df.to_dict("records"), start=2):
        d = {c: _clean(raw.get(c)) for c in COVERAGE_COLUMNS}
        err = []
        if not d["symbol"]:
            err.append("missing symbol")
        else:
            d["symbol"] = resolve_symbol(con, d["symbol"].upper())
            if not company_identity(con, d["symbol"]):
                err.append(f"unknown symbol {d['symbol']}")
        if _pick_enum(d["topic"], RESEARCH_TOPICS) is None:
            err.append(f"topic must be one of: {', '.join(RESEARCH_TOPICS)}")
        if d["source_type"] not in SOURCE_TYPES:
            err.append(f"source_type must be one of: {', '.join(SOURCE_TYPES)}")
        res = _pick_enum(d["result"], EVIDENCE_STATUSES)
        if res is None:
            err.append(f"result must be one of: {', '.join(EVIDENCE_STATUSES)}")
        searched = _date(d["searched_on"]) or today
        if err:
            out.append({"row": i, "action": "rejected", "message": "; ".join(err)})
            continue
        con.execute("INSERT OR REPLACE INTO research_coverage VALUES (?,?,?,?,?,?,?,?)",
                    (d["symbol"], _pick_enum(d["topic"], RESEARCH_TOPICS), d["source_type"], searched, res,
                     d["note"], d["source_url"], now))
        out.append({"row": i, "action": "recorded", "message": ""})
    con.commit()
    return pd.DataFrame(out, columns=["row", "action", "message"])


def import_coverage_csv(con, path: Path | None = None) -> pd.DataFrame | None:
    path = Path(path) if path else COVERAGE_CSV
    return import_coverage(con, pd.read_csv(path, dtype=str)) if path.exists() else None


# ---------------------------------------------------------------- reading the timeline
def _apply_as_of(con, ev: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """Rebuild status/evidence/timeline as they were known on `as_of` (no look-ahead)."""
    if ev.empty:
        return ev
    ups = pd.read_sql_query(
        f"SELECT event_id, field, new_value, published_date, id FROM event_updates "
        f"WHERE event_id IN ({','.join('?' * len(ev))}) AND published_date <= ? ORDER BY published_date, id",
        con, params=[*ev["event_id"], as_of])
    ev = ev.copy()
    for f in ("status", "evidence_status", "expected_timeline", "next_milestone", "event_date"):
        last = ups[ups["field"] == f].groupby("event_id")["new_value"].last()
        if f in ("status", "evidence_status", "expected_timeline"):
            ev[f] = ev["event_id"].map(last)           # only what had been published by then
        else:
            ev[f] = ev["event_id"].map(last).fillna(ev[f])
    return ev


def read_events(con, symbol: str | None = None, as_of=None, since_years: int | None = None) -> pd.DataFrame:
    """Timeline for one company (including its former symbols) or for all companies.

    as_of: only events whose source was PUBLISHED by that date, with the status
           known at that date. Used by backtests and historical views.
    """
    q, p = "SELECT * FROM events WHERE 1=1", []
    if symbol:
        al = symbol_aliases(con, symbol)
        q += f" AND symbol IN ({','.join('?' * len(al))})"
        p += al
    if as_of is not None:
        q += " AND published_date <= ?"
        p.append(pd.Timestamp(as_of).strftime("%Y-%m-%d"))
    ev = pd.read_sql_query(q, con, params=p)
    if as_of is not None:
        ev = _apply_as_of(con, ev, pd.Timestamp(as_of).strftime("%Y-%m-%d"))
    if since_years and not ev.empty:
        cut = (pd.Timestamp(as_of or date.today()) - pd.DateOffset(years=since_years)).strftime("%Y-%m-%d")
        ev = ev[ev["event_date"].fillna(ev["published_date"]) >= cut]
    if ev.empty:
        return ev
    ev["sort_date"] = ev["event_date"].fillna(ev["published_date"])
    n_src = pd.read_sql_query("SELECT event_id, COUNT(*) AS sources FROM event_sources GROUP BY event_id", con)
    ev = ev.merge(n_src, on="event_id", how="left")
    ev["display"] = ev.apply(display_line, axis=1)
    return ev.sort_values(["sort_date", "published_date"], ascending=False).reset_index(drop=True)


def display_line(e) -> str:
    """One-line description that never presents a plan as a running business."""
    status = e.get("status") or "unknown"
    if e.get("business_stage") == "proposed" or e.get("evidence_status") == GUIDANCE:
        when = f" ({e['expected_timeline']})" if e.get("expected_timeline") else ""
        who = "Management announced" if e.get("evidence_status") == GUIDANCE else "Reported"
        return f"{who} a proposed plan: {e['title']}{when}; current execution status: {status}."
    if e.get("evidence_status") == REPORTED:
        return f"Reported (not yet confirmed): {e['title']} - status {status}."
    if e.get("evidence_status") == CONFLICTING:
        return f"Sources conflict: {e['title']} - status {status}; see sources."
    return f"{e['title']} - {status}."


def event_sources(con, event_id: str) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM event_sources WHERE event_id=? ORDER BY published_date", con,
                             params=[event_id])


def event_history(con, event_id: str) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM event_updates WHERE event_id=? ORDER BY published_date, id", con,
                             params=[event_id])


def research_checklist(con, symbol: str) -> pd.DataFrame:
    """Per business-development topic: what we found, where we looked, what is still unknown."""
    al = symbol_aliases(con, symbol)
    ev = pd.read_sql_query(f"SELECT category, COUNT(*) n, MAX(COALESCE(event_date, published_date)) latest "
                           f"FROM events WHERE symbol IN ({','.join('?' * len(al))}) GROUP BY category",
                           con, params=al)
    cov = pd.read_sql_query(f"SELECT * FROM research_coverage WHERE symbol IN ({','.join('?' * len(al))})",
                            con, params=al)
    rows = []
    for t in RESEARCH_TOPICS:
        e = ev[ev["category"] == t]
        c = cov[cov["topic"] == t]
        searched = ", ".join(sorted({SOURCE_TYPES[s].split(" (")[0] for s in c["source_type"]})) if not c.empty else ""
        last = c["searched_on"].max() if not c.empty else None
        if not e.empty:
            verdict = f"Found: {int(e['n'].iloc[0])} event(s), latest {e['latest'].iloc[0]}"
        elif not c.empty and (c["result"] == NOT_APPLICABLE).any():
            verdict = NOT_APPLICABLE
        elif not c.empty and (c["result"] == CONFLICTING).any():
            verdict = CONFLICTING
        elif not c.empty and (c["result"] == NOT_FOUND).all():
            verdict = f"{NOT_FOUND} in the sources searched (not proof it did not happen)"
        elif not c.empty:
            verdict = "Searched - see notes (no event recorded yet)"
        else:
            verdict = "Not yet researched"
        rows.append({"topic": t, "verdict": verdict, "sources_searched": searched or "-",
                     "last_searched": last, "notes": "; ".join(n for n in c["note"] if isinstance(n, str) and n)})
    return pd.DataFrame(rows)


def stale_verifications(con, cfg: dict | None = None, today: str | None = None) -> pd.DataFrame:
    cfg = cfg or load_settings()
    cut = (pd.Timestamp(today or date.today()) -
           pd.Timedelta(days=cfg["research"]["refresh_days"]["events_reverify"])).strftime("%Y-%m-%d")
    return pd.read_sql_query("SELECT event_id, symbol, title, status, last_verified FROM events WHERE "
                             "status NOT IN (?, ?) AND (last_verified IS NULL OR last_verified < ?) "
                             "ORDER BY last_verified", con, params=[COMPLETED, CANCELLED, cut])


def open_risk_events(ev: pd.DataFrame) -> pd.DataFrame:
    risky = {"Debt / default", "Litigation / regulatory action", "Project delay / cancellation"}
    if ev.empty:
        return ev
    return ev[ev["category"].isin(risky) | ev["status"].isin([DELAYED, CANCELLED])]
