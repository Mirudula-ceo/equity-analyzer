"""All-NSE refresh, run by GitHub Actions (.github/workflows/refresh-data.yml). Can also run on a PC.

    python scripts/refresh_universe.py lists   --out work/        # NSE lists + announcements -> data/
    python scripts/refresh_universe.py score   --shard 0 --of 12 --out work/parts
    python scripts/refresh_universe.py publish --parts work/parts  # rank everything, write data/snapshot/

Scoring uses the unchanged 27-metric engine on a temporary database; only results are kept.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from equity import db  # noqa: E402
from equity.config import load_settings  # noqa: E402
from equity.metrics.scoring import rank  # noqa: E402
from equity.research import announcements, listings, snapshot  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36", "Accept": "*/*"}
MANUAL = ROOT / "data" / "manual"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            r.raise_for_status()
            if "<html" in r.text[:300].lower():
                raise ValueError("HTML instead of data")
            return r.text
        except Exception as exc:  # noqa: BLE001
            if i == tries - 1:
                raise
            print("   retry", url, exc, flush=True)
            time.sleep(5 * (i + 1))


# ---------------------------------------------------------------- step 1: lists + announcements
def cmd_lists(a):
    cfg = load_settings()
    r = cfg["research"]
    MANUAL.mkdir(parents=True, exist_ok=True)
    status = {}
    for kind, key in listings.URL_KEYS.items():
        try:
            text = get(r[key])
            pd.read_csv(io.StringIO(text))                       # must parse
            (MANUAL / listings.MANUAL_FILES[kind]).write_text(text, encoding="utf-8")
            status[kind] = "ok"
        except Exception as exc:  # noqa: BLE001
            status[kind] = f"failed: {exc} (previous copy kept)"
        print(kind, status[kind], flush=True)
    con = db.connect(":memory:")
    listings.load_manual_lists(con, MANUAL, as_of=now()[:10])
    syms = listings.eligible_symbols(con, cfg)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "symbols.txt").write_text("\n".join(syms), encoding="utf-8")
    print(f"eligible symbols: {len(syms)}", flush=True)

    # announcements: keep a rolling history
    try:
        items = announcements.parse_rss(get(r["announcements_rss_url"]))
        known = set(listings.read_listings(con)["symbol"])
        items["symbol"] = items["symbol_hint"].where(items["symbol_hint"].isin(known))
        items["category_hint"] = (items["title"] + " " + items["description"]).map(announcements.suggest_category)
        items["retrieved_at"] = now()
        old = snapshot.load_announcements()
        allx = pd.concat([items, old], ignore_index=True).drop_duplicates(["link", "title"])
        allx["published"] = allx["published"].astype(str)
        cut = (pd.Timestamp.now("UTC") - pd.Timedelta(days=r.get("announcement_history_days", 365))).strftime("%Y-%m-%d")
        allx = allx[allx["published"] >= cut].sort_values("published", ascending=False)
        snapshot.SNAP_DIR.mkdir(parents=True, exist_ok=True)
        allx[["published", "symbol", "title", "description", "link", "category_hint", "retrieved_at"]].to_csv(
            snapshot.path("announcements"), index=False, compression="gzip")
        status["announcements"] = f"ok: {len(items)} new feed items, {len(allx)} kept"
    except Exception as exc:  # noqa: BLE001
        status["announcements"] = f"failed: {exc}"
    print("announcements", status["announcements"], flush=True)
    (out / "lists_status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- step 2: score one shard
def cmd_score(a):
    from equity.analysis import analyze_symbol
    from equity.data.collect import fetch_fundamentals, fetch_prices
    from equity.metrics.engine import load_qualitative

    cfg = load_settings()
    syms = [s for s in Path(a.symbols).read_text(encoding="utf-8").split() if s]
    mine = syms[a.shard::a.of]
    if a.limit:
        mine = mine[:a.limit]
    tmp = Path(tempfile.mkdtemp()) / "shard.db"
    os.environ["EQUITY_DB"] = str(tmp)
    con = db.connect(tmp)
    qual = load_qualitative()
    rows, details, profiles = [], [], []
    t0 = time.time()
    for i, s in enumerate(mine, 1):
        status = "ok"
        try:
            n = fetch_prices(con, s, years=a.years, pause=a.pause)
            if n:
                fetch_fundamentals(con, s, cfg, quarterly=False)
            else:
                status = "no prices from Yahoo"
            res, sc, pit = analyze_symbol(con, s, None, cfg, qual)
        except Exception as exc:  # noqa: BLE001
            print(f"   {s}: failed {exc}", flush=True)
            status = f"error: {str(exc)[:120]}"
            continue
        info = pit.info
        per = pit.periods()
        pdate = pit.price_date()
        rows.append({"symbol": s, "name": info.get("longName") or info.get("shortName") or s,
                     "sector": info.get("sector"), "industry": info.get("industry"), "price": pit.price(),
                     "price_date": None if pdate is None else pd.Timestamp(pdate).date().isoformat(),
                     "market_cap": pit.market_cap(), "latest_period": per[-1].strftime("%Y-%m") if per else None,
                     **sc})
        details.append(snapshot.details_frame(s, res))
        prof = {k: info.get(k) for k in snapshot.PROFILE_COLS if k in info}
        prof.update(symbol=s, name=info.get("longName") or info.get("shortName"), fetch_status=status,
                    retrieved_at=now(), companyOfficers=json.dumps(info.get("companyOfficers") or []))
        profiles.append(prof)
        if i % 20 == 0:
            print(f"[shard {a.shard}] {i}/{len(mine)} done, {time.time() - t0:.0f}s", flush=True)
        con.execute("DELETE FROM prices WHERE symbol=?", (s,))          # keep the temp DB small
        con.execute("DELETE FROM financials WHERE symbol=?", (s,))
        con.commit()
        time.sleep(a.pause)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / f"scores_{a.shard}.csv", index=False)
    (pd.concat(details) if details else pd.DataFrame(columns=snapshot.DETAIL_COLS)).to_csv(
        out / f"details_{a.shard}.csv", index=False)
    pd.DataFrame(profiles, columns=snapshot.PROFILE_COLS).to_csv(out / f"profiles_{a.shard}.csv", index=False)
    print(f"[shard {a.shard}] scored {len(rows)}/{len(mine)} in {time.time() - t0:.0f}s", flush=True)


# ---------------------------------------------------------------- step 3: rank and publish
def cmd_publish(a):
    cfg = load_settings()
    parts = Path(a.parts)

    def cat(prefix):
        fs = sorted(parts.rglob(f"{prefix}_*.csv"))
        dfs = [pd.read_csv(f, dtype={"symbol": str}) for f in fs if f.stat().st_size > 0]
        dfs = [d for d in dfs if not d.empty]
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    scores, details, profiles = cat("scores"), cat("details"), cat("profiles")
    if scores.empty:
        sys.exit("no shard produced any score - keeping the previous snapshot")
    expected = len(Path(a.symbols).read_text().split()) if a.symbols and Path(a.symbols).exists() else None
    prev = snapshot.load_scores()
    if expected and len(scores) < 0.5 * expected and not prev.empty:
        sys.exit(f"only {len(scores)} of {expected} companies scored (source blocked?) - keeping previous snapshot")
    table = rank(scores.drop_duplicates("symbol"), cfg)
    snapshot.SNAP_DIR.mkdir(parents=True, exist_ok=True)
    table.to_csv(snapshot.path("scores"), index=False, compression="gzip")
    details.drop_duplicates(["symbol", "key"]).to_csv(snapshot.path("details"), index=False, compression="gzip")
    profiles.drop_duplicates("symbol").to_csv(snapshot.path("profiles"), index=False, compression="gzip")
    lists_status = {}
    for f in parts.parent.rglob("lists_status.json"):
        lists_status = json.loads(f.read_text())
    m = {"generated_at": now(), "companies_expected": expected, "companies_scored": int(len(table)),
         "companies_ranked": int(table["eligible"].sum()), "lists": lists_status,
         "price_history_years": a.years, "note": "Scores from the unchanged 27-metric engine on Yahoo Finance "
         "data; annual statements only; see docs/COMPANY_RESEARCH_MODULE.md"}
    (snapshot.SNAP_DIR / "meta.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    print(json.dumps(m, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lists")
    p.add_argument("--out", default="work")
    p = sub.add_parser("score")
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--of", type=int, required=True)
    p.add_argument("--symbols", default="work/symbols.txt")
    p.add_argument("--out", default="work/parts")
    p.add_argument("--years", type=int, default=2)
    p.add_argument("--pause", type=float, default=0.4)
    p.add_argument("--limit", type=int, default=0)
    p = sub.add_parser("publish")
    p.add_argument("--parts", default="work/parts")
    p.add_argument("--symbols", default="work/symbols.txt")
    p.add_argument("--years", type=int, default=2)
    a = ap.parse_args()
    {"lists": cmd_lists, "score": cmd_score, "publish": cmd_publish}[a.cmd](a)


if __name__ == "__main__":
    main()
