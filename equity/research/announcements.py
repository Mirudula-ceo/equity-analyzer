"""Exchange-announcement inbox (requirements sections 2 and 10).

Reads NSE's public corporate-announcement RSS file and stores each item in
`filings_inbox` with a SUGGESTED category. Nothing here is scored or shown on
the timeline automatically: a reviewer reads the filing, writes a summary in
their own words and adds it to data/manual/company_events.csv. This keeps
wrong-company links and misread filings out of the research pages.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from ..config import load_settings
from ..db import log_fetch, now_utc, upsert_df
from .listings import UA, resolve_symbol

# First match wins. Words are matched case-insensitively on the announcement title/description.
RULES: list[tuple[str, str]] = [
    ("Acquisition / merger", r"acquisition|acquire|amalgamation|merger|scheme of arrangement|takeover"),
    ("Divestment", r"divest|disinvest|sale of (?:stake|business|undertaking)|slump sale"),
    ("Subsidiary / joint venture", r"joint venture|\bjv\b|incorporation of (?:a )?(?:wholly owned )?subsidiary"),
    ("Order / contract win", r"\b(?:wins?|won|bags?|bagged|secures?|secured|receiv\w*|bags)\b.{0,60}\b(?:order|contract)s?\b|"
                             r"\border(?:s)?\b.{0,40}(?:receiv|win|bag|secur)|letter of award|contract (?:award|win)"),
    ("Project delay / cancellation", r"(?:project|plant|launch|commission\w*|expansion|order|contract|acquisition|"
                                     r"deal|agreement)\b.{0,60}\b(?:delay\w*|postpone\w*|cancel\w*|terminat\w*|"
                                     r"withdr\w*)|(?:delay\w*|postpone\w*|cancel\w*|terminat\w*)\b.{0,60}\b"
                                     r"(?:project|plant|launch|commission\w*|expansion|order|contract|acquisition|deal)"),
    ("Capacity / factory expansion", r"capacity|new plant|commission|greenfield|brownfield|expansion"),
    ("Capex / funding plan", r"fund rais|qip|preferential (?:issue|allotment)|rights issue|\bncds?\b|debenture|capex|"
                             r"issue of securities|allotment of (?:equity|securities|shares)"),
    ("Product / brand launch", r"\blaunch"),
    ("Regulatory approval", r"approval|usfda|licen[cs]e granted|clearance|eir|establishment inspection"),
    ("Management change", r"resign|appointment|cessation|change in (?:director|kmp|management)|\bceo\b|\bcfo\b"),
    ("Debt / default", r"default|credit rating|downgrade|insolvency|nclt|ibc"),
    ("Litigation / regulatory action", r"litigation|penalty|show cause|sebi order|search|investigation|fraud"),
]
ROUTINE = re.compile(r"trading window|loss of share certificate|newspaper (?:publication|advertisement)|"
                     r"compliance certificate|regulation 74\(5\)|duplicate share|depositories and participants|"
                     r"investor grievance|scores complaints|payment of interest|interest and principal|"
                     r"regulation 27\(2\)|record date|book closure|copy of (?:newspaper|advertisement)|"
                     r"transfer of shares to iepf|issue of duplicate", re.I)
# Labels that help scan the feed but are not timeline categories (an event row needs a real category).
EXTRA_HINTS = [("Financial results", r"financial results|outcome of board meeting.*result|un-?audited|audited results"),
               ("Investor meet / presentation", r"investor presentation|analysts?/institutional investor meet|"
                                                r"con\.? call|earnings call|investor meet")]
HINT_LABELS = [c for c, _ in RULES] + [c for c, _ in EXTRA_HINTS]


def suggest_category(text: str) -> str | None:
    t = text or ""
    if ROUTINE.search(t):
        return None
    for cat, pat in RULES:
        if re.search(pat, t, re.I):
            return cat
    for cat, pat in EXTRA_HINTS:
        if re.search(pat, t, re.I):
            return cat
    return None


def subject_of(description: str) -> str:
    """NSE descriptions end with '|SUBJECT: <subject>'; fall back to the description itself."""
    d = description or ""
    m = re.search(r"\|\s*SUBJECT:\s*(.+)$", d)
    return (m.group(1) if m else d).strip()[:200]


def _norm_name(n: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", " ", (n or "").lower())
    n = re.sub(r"\b(limited|ltd|the)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def enrich(items: pd.DataFrame, listing: pd.DataFrame) -> pd.DataFrame:
    """Add subject, match symbols by link hint OR exact official company name, and re-label categories."""
    d = items.copy()
    if d.empty:
        return d
    d["description"] = d.get("description", pd.Series([""] * len(d))).fillna("")
    d["subject"] = d["description"].map(subject_of)
    known = set(listing["symbol"]) if not listing.empty else set()
    by_name = ({}
               if listing.empty else
               listing.assign(k=listing["name"].map(_norm_name)).drop_duplicates("k").set_index("k")["symbol"].to_dict())
    hint = d["symbol_hint"] if "symbol_hint" in d else d.get("symbol", pd.Series([None] * len(d)))
    sym = hint.where(hint.isin(known))
    by_title = d["title"].map(lambda t: by_name.get(_norm_name(t)))
    d["symbol"] = sym.fillna(by_title)
    d["category_hint"] = (d["subject"] + " " + d["description"]).map(suggest_category)
    return d


def parse_rss(xml_text: str) -> pd.DataFrame:
    root = ET.fromstring(xml_text)
    rows = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = pd.to_datetime(it.findtext("pubDate"), errors="coerce", utc=True)
        # NSE titles usually read "<Company name> - <subject>"; the symbol is often in the link
        m = re.search(r"[?&]symbol=([A-Z0-9&\-]+)", link) or re.search(r"/([A-Z0-9&\-]{2,20})_\d", link)
        rows.append({"title": title, "description": desc, "link": link,
                     "published": None if pd.isna(pub) else pub.strftime("%Y-%m-%d %H:%M"),
                     "symbol_hint": m.group(1) if m else None})
    return pd.DataFrame(rows)


def refresh_inbox(con, cfg: dict | None = None, get=requests.get, xml_text: str | None = None) -> dict:
    cfg = cfg or load_settings()
    url = cfg["research"]["announcements_rss_url"]
    if xml_text is None:
        try:
            r = get(url, headers=UA, timeout=30)
            r.raise_for_status()
            xml_text = r.text
        except Exception as exc:  # noqa: BLE001
            log_fetch(con, "ALL", "announcements", "error", str(exc), url)
            return {"status": f"unavailable: {exc}"}
    items = parse_rss(xml_text)
    if items.empty:
        log_fetch(con, "ALL", "announcements", "empty", "no items", url)
        return {"items": 0}
    lst = pd.read_sql_query("SELECT symbol, name FROM nse_listings UNION SELECT symbol, company AS name "
                            "FROM universe", con)
    items["symbol_hint"] = items["symbol_hint"].map(lambda s: resolve_symbol(con, s) if s else None)
    items = enrich(items, lst)
    items["symbol"] = items["symbol"].where(items["symbol"].notna(), None)
    items["suggested_category"] = items["category_hint"]
    items["title"] = items["title"] + " - " + items["subject"]
    existing = {r[0]: r[1] for r in con.execute("SELECT guid, review_state FROM filings_inbox")}
    out = pd.DataFrame({
        "guid": (items["link"] + "|" + items["title"]).map(lambda s: hashlib.sha1(s.encode()).hexdigest()[:16]),
        "symbol": items["symbol"], "title": items["title"], "published": items["published"],
        "link": items["link"], "suggested_category": items["suggested_category"],
        "source": f"NSE corporate announcements RSS ({url})", "retrieved_at": now_utc()})
    out["review_state"] = out["guid"].map(existing).fillna(
        out.apply(lambda r: "unmatched company" if pd.isna(r["symbol"]) else
                  ("to review" if pd.notna(r["suggested_category"]) else "routine"), axis=1))
    upsert_df(con, "filings_inbox", out)
    log_fetch(con, "ALL", "announcements", "ok", f"{len(out)} items", url)
    return {"items": len(out), "to_review": int((out["review_state"] == "to review").sum()),
            "unmatched": int((out["review_state"] == "unmatched company").sum())}


def read_inbox(con, symbol: str | None = None, state: str | None = None) -> pd.DataFrame:
    q, p = "SELECT * FROM filings_inbox WHERE 1=1", []
    if symbol:
        q += " AND symbol=?"
        p.append(symbol)
    if state:
        q += " AND review_state=?"
        p.append(state)
    return pd.read_sql_query(q + " ORDER BY published DESC", con, params=p)


def mark_reviewed(con, guid: str, state: str = "reviewed") -> None:
    con.execute("UPDATE filings_inbox SET review_state=? WHERE guid=?", (state, guid))
    con.commit()
