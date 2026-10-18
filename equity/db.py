"""SQLite storage. Every observation keeps its source and retrieval date."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
    adj_close REAL, volume REAL, source TEXT, retrieved_at TEXT,
    PRIMARY KEY (symbol, date));
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol TEXT, date TEXT, action_type TEXT, value REAL,
    source TEXT, retrieved_at TEXT,
    PRIMARY KEY (symbol, date, action_type));
CREATE TABLE IF NOT EXISTS financials (
    symbol TEXT, statement TEXT, freq TEXT, period_end TEXT, line_item TEXT,
    value REAL, currency TEXT, source TEXT,
    publication_date TEXT, publication_date_estimated INTEGER,
    retrieved_at TEXT,
    PRIMARY KEY (symbol, statement, freq, period_end, line_item));
CREATE TABLE IF NOT EXISTS company_info (
    symbol TEXT PRIMARY KEY, name TEXT, sector TEXT, industry TEXT,
    currency TEXT, info_json TEXT, source TEXT, retrieved_at TEXT);
CREATE TABLE IF NOT EXISTS universe (
    index_name TEXT, symbol TEXT, company TEXT, industry TEXT,
    source TEXT, as_of TEXT, retrieved_at TEXT,
    PRIMARY KEY (index_name, symbol));
CREATE TABLE IF NOT EXISTS shareholding (
    symbol TEXT, period_end TEXT, promoter_pct REAL,
    promoter_pledged_pct REAL, source TEXT, publication_date TEXT,
    retrieved_at TEXT, PRIMARY KEY (symbol, period_end));
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, dataset TEXT,
    status TEXT, message TEXT, source TEXT, retrieved_at TEXT);
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY, created_at TEXT, label TEXT,
    params_json TEXT, summary_json TEXT);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.executescript(SCHEMA)
    return con


def upsert_df(con: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    cols = list(df.columns)
    sql = (f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
           f"VALUES ({','.join('?' * len(cols))})")
    rows = [tuple(None if (isinstance(v, float) and pd.isna(v)) else v for v in r)
            for r in df.itertuples(index=False, name=None)]
    con.executemany(sql, rows)
    con.commit()
    return len(rows)


def log_fetch(con, symbol, dataset, status, message, source):
    con.execute("INSERT INTO fetch_log (symbol,dataset,status,message,source,retrieved_at)"
                " VALUES (?,?,?,?,?,?)", (symbol, dataset, status, message[:500], source, now_utc()))
    con.commit()


# ---------------------------------------------------------------- readers
def read_prices(con, symbols=None) -> pd.DataFrame:
    q = "SELECT * FROM prices"
    params: list = []
    if symbols:
        q += f" WHERE symbol IN ({','.join('?' * len(symbols))})"
        params = list(symbols)
    df = pd.read_sql_query(q, con, params=params)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def read_actions(con, symbols=None) -> pd.DataFrame:
    q = "SELECT * FROM corporate_actions"
    params: list = []
    if symbols:
        q += f" WHERE symbol IN ({','.join('?' * len(symbols))})"
        params = list(symbols)
    df = pd.read_sql_query(q, con, params=params)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def read_financials(con, symbol=None) -> pd.DataFrame:
    q = "SELECT * FROM financials"
    params: list = []
    if symbol:
        q += " WHERE symbol = ?"
        params = [symbol]
    df = pd.read_sql_query(q, con, params=params)
    if not df.empty:
        df["period_end"] = pd.to_datetime(df["period_end"])
        df["publication_date"] = pd.to_datetime(df["publication_date"])
    return df


def read_info(con, symbol) -> dict:
    row = con.execute("SELECT info_json, retrieved_at, source FROM company_info WHERE symbol=?",
                      (symbol,)).fetchone()
    if not row:
        return {}
    info = json.loads(row[0]) if row[0] else {}
    info["_retrieved_at"] = row[1]
    info["_source"] = row[2]
    return info


def read_shareholding(con, symbol) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM shareholding WHERE symbol=? ORDER BY period_end",
                           con, params=[symbol])
    if not df.empty:
        df["period_end"] = pd.to_datetime(df["period_end"])
        df["publication_date"] = pd.to_datetime(df["publication_date"])
    return df


def read_universe(con, index_name="NIFTY50") -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM universe WHERE index_name=? ORDER BY symbol",
                             con, params=[index_name])


def symbols_with_prices(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT symbol FROM prices ORDER BY symbol")]
