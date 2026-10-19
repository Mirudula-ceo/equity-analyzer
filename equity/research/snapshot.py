"""All-NSE scoring snapshot.

Full prices + statements for ~2,000 companies would be far too large to keep in
the repository, so the GitHub Action (.github/workflows/refresh-data.yml) scores
every eligible NSE company and commits only the RESULTS here:

    data/snapshot/universe_scores.csv.gz    one row per company (same columns as rank_symbols())
    data/snapshot/universe_details.csv.gz   27 metric results per company
    data/snapshot/company_profiles.csv.gz   website, business summary, officers ... (never scored)
    data/snapshot/nse_announcements.csv.gz  official exchange announcements (rolling history)
    data/snapshot/meta.json                 when it ran, how many companies, failures

The results are produced by the unchanged 27-metric engine; the explanation on the
Company Research page is rebuilt from these exact results.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from ..config import PROJECT_ROOT
from ..metrics.engine import DEF_BY_KEY, MetricResult

SNAP_DIR = Path(os.environ.get("EQUITY_SNAPSHOT", PROJECT_ROOT / "data" / "snapshot"))
FILES = {"scores": "universe_scores.csv.gz", "details": "universe_details.csv.gz",
         "profiles": "company_profiles.csv.gz", "announcements": "nse_announcements.csv.gz"}
DETAIL_COLS = ["symbol", "id", "key", "status", "value", "display", "explanation", "source"]
PROFILE_COLS = ["symbol", "name", "sector", "industry", "website", "irWebsite", "longBusinessSummary",
                "fullTimeEmployees", "heldPercentInsiders", "heldPercentInstitutions", "companyOfficers",
                "city", "fetch_status", "retrieved_at"]


def path(kind: str, d: Path | None = None) -> Path:
    return (d or SNAP_DIR) / FILES[kind]


def meta(d: Path | None = None) -> dict:
    p = (d or SNAP_DIR) / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def available(d: Path | None = None) -> bool:
    return path("scores", d).exists() and path("details", d).exists()


def _read(kind, d=None) -> pd.DataFrame:
    p = path(kind, d)
    return pd.read_csv(p, dtype={"symbol": str}, keep_default_na=True) if p.exists() else pd.DataFrame()


def load_scores(d: Path | None = None) -> pd.DataFrame:
    t = _read("scores", d)
    if not t.empty:
        t["eligible"] = t["eligible"].astype(str).str.lower().isin(["true", "1"])
        t["rank"] = pd.to_numeric(t["rank"], errors="coerce")
    return t


def load_details(d: Path | None = None) -> pd.DataFrame:
    return _read("details", d)


def load_profiles(d: Path | None = None) -> pd.DataFrame:
    return _read("profiles", d)


def load_announcements(d: Path | None = None) -> pd.DataFrame:
    return _read("announcements", d)


def profile(symbol: str, profiles: pd.DataFrame | None = None) -> dict:
    p = profiles if profiles is not None else load_profiles()
    if p.empty or symbol not in set(p["symbol"]):
        return {}
    r = p[p["symbol"] == symbol].iloc[0].to_dict()
    r = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in r.items()}
    if isinstance(r.get("companyOfficers"), str):
        try:
            r["companyOfficers"] = json.loads(r["companyOfficers"])
        except ValueError:
            r["companyOfficers"] = []
    return r


def results_for(symbol: str, details: pd.DataFrame) -> list[MetricResult]:
    """Rebuild the 27 MetricResult objects stored for a company (definitions come from the engine)."""
    rows = details[details["symbol"] == symbol].sort_values("id")
    out = []
    for r in rows.itertuples():
        d = DEF_BY_KEY[r.key]
        val = None if pd.isna(r.value) else float(r.value)
        out.append(MetricResult(d.id, d.key, d.name, d.category, val,
                                "" if pd.isna(r.display) else str(r.display), r.status, {},
                                "" if pd.isna(r.explanation) else str(r.explanation),
                                "" if pd.isna(r.source) else str(r.source),
                                d.formula, d.criteria, d.inputs, d.method))
    return out


def details_frame(symbol: str, results: list[MetricResult]) -> pd.DataFrame:
    rows = [{"symbol": symbol, "id": r.id, "key": r.key, "status": r.status, "value": r.value,
             "display": r.display, "explanation": (r.explanation or "")[:400],
             "source": (r.source or "")[:200]} for r in results]
    return pd.DataFrame(rows, columns=DETAIL_COLS)
