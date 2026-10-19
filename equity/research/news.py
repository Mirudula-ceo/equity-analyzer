"""Latest news (requirements sections 2, 5, 7).

Two kinds, always labelled differently:
* Exchange announcements - official NSE filings (from the daily snapshot, or live if NSE is reachable).
* News headlines - Google News RSS search for the company name. Headline, publisher, date and link only;
  article text is never copied. Evidence state: "Reported / unconfirmed".

News is research context: it is never scored and never moves a rank. To keep something permanently,
add it to the timeline (data/manual/company_events.csv) in your own words.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import pandas as pd
import requests

from .announcements import suggest_category
from .constants import CATEGORIES, REPORTED

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"}
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
_SUFFIX = re.compile(r"\b(limited|ltd\.?|the|india)\b", re.I)


def short_name(name: str) -> str:
    """'Tata Consultancy Services Limited' -> 'Tata Consultancy Services' (keeps the search specific)."""
    n = _SUFFIX.sub(" ", name or "")
    return re.sub(r"\s+", " ", n).strip(" .,&") or name


def news_query(name: str, symbol: str | None = None, days: int = 30) -> str:
    q = f'"{short_name(name)}"'
    if symbol:
        q += f" OR \"NSE:{symbol}\""
    return f"{q} when:{days}d"


def parse_google_news(xml_text: str) -> pd.DataFrame:
    root = ET.fromstring(xml_text)
    rows = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        pub = pd.to_datetime(it.findtext("pubDate"), errors="coerce", utc=True)
        rows.append({"published": pub, "title": title, "publisher": source,
                     "link": (it.findtext("link") or "").strip()})
    df = pd.DataFrame(rows, columns=["published", "title", "publisher", "link"])
    if df.empty:
        return df
    df["category_hint"] = df["title"].map(suggest_category)
    df["evidence"] = REPORTED
    df["kind"] = "News headline"
    return df.sort_values("published", ascending=False).drop_duplicates("title").reset_index(drop=True)


def google_news(name: str, symbol: str | None = None, days: int = 30, limit: int = 30,
                get=requests.get) -> tuple[pd.DataFrame, str | None]:
    """Returns (headlines, error). An error is reported, never hidden."""
    url = GOOGLE_NEWS.format(q=quote_plus(news_query(name, symbol, days)))
    try:
        r = get(url, headers=UA, timeout=20)
        r.raise_for_status()
        df = parse_google_news(r.text)
        return df.head(limit), None
    except Exception as exc:  # noqa: BLE001
        reason = "network blocked" if "proxy" in str(exc).lower() or "connect" in str(exc).lower() else str(exc)[:100]
        return (pd.DataFrame(columns=["published", "title", "publisher", "link"]),
                f"Google News could not be reached from this server ({reason}). Try again later.")


def exchange_announcements(snapshot_df: pd.DataFrame, symbol: str | None = None,
                           aliases: list[str] | None = None, listing: pd.DataFrame | None = None) -> pd.DataFrame:
    if snapshot_df is None or snapshot_df.empty:
        return pd.DataFrame(columns=["published", "symbol", "company", "subject", "title", "link", "category_hint"])
    from .announcements import enrich
    d = snapshot_df.copy()
    if listing is not None and not listing.empty:
        d = enrich(d.drop(columns=["subject"], errors="ignore"), listing)
    elif "subject" not in d:
        from .announcements import subject_of
        d["subject"] = d["description"].fillna("").map(subject_of)
    d["company"] = d["title"]
    if symbol:
        d = d[d["symbol"].isin(aliases or [symbol])]
    d["published"] = pd.to_datetime(d["published"], errors="coerce")
    d["kind"] = "Exchange announcement"
    d["evidence"] = "Official filing (read before relying on it)"
    return d.sort_values("published", ascending=False).reset_index(drop=True)


def to_event_rows(news: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Pre-filled company_events.csv rows for the headlines a reviewer wants to keep.
    The summary is left EMPTY on purpose: it must be written in the reviewer's own words."""
    from .events import EVENT_COLUMNS
    rows = []
    for r in news.itertuples():
        pub = pd.Timestamp(r.published)
        hint = getattr(r, "category_hint", None)
        title = getattr(r, "subject", None) if getattr(r, "kind", "") == "Exchange announcement" else r.title
        rows.append({"symbol": symbol, "title": title if isinstance(title, str) and title else r.title,
                     "category": hint if hint in CATEGORIES else ("Other material event" if hint else ""),
                     "published_date": "" if pd.isna(pub) else pub.strftime("%Y-%m-%d"),
                     "status": "Announced", "evidence_status": REPORTED, "summary": "",
                     "source_name": getattr(r, "publisher", "") or "NSE", "source_type":
                     "exchange_filing" if getattr(r, "kind", "") == "Exchange announcement" else "financial_publication",
                     "source_url": r.link})
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)
