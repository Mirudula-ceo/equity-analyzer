"""Shared fixtures. All numbers here are SYNTHETIC test inputs, not market data."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from equity.config import load_settings  # noqa: E402


@pytest.fixture
def cfg():
    return load_settings()


def make_fin(symbol, years: dict, lag_days=75):
    """years: {'2022-03-31': {'Total Revenue': 100, ...}, ...} -> long financials frame."""
    rows = []
    stmt_of = {}
    for pe, items in years.items():
        for k, v in items.items():
            rows.append({"symbol": symbol, "statement": stmt_of.get(k, "x"), "freq": "annual",
                         "period_end": pd.Timestamp(pe), "line_item": k, "value": v, "currency": "INR",
                         "source": "TEST", "publication_date": pd.Timestamp(pe) + pd.Timedelta(days=lag_days),
                         "publication_date_estimated": 1, "retrieved_at": "2026-01-01T00:00:00Z"})
    return pd.DataFrame(rows)


def good_company_year(scale=1.0, shares=1e8):
    return {"Total Revenue": 1000e7 * scale, "Cost Of Revenue": 600e7 * scale, "Gross Profit": 400e7 * scale,
            "Operating Income": 200e7 * scale, "EBIT": 200e7 * scale, "EBITDA": 240e7 * scale,
            "Net Income Common Stockholders": 140e7 * scale, "Pretax Income": 190e7 * scale,
            "Tax Provision": 50e7 * scale, "Interest Expense": 10e7 * scale,
            "Total Assets": 1500e7 * scale, "Stockholders Equity": 900e7 * scale,
            "Total Debt": 100e7, "Long Term Debt": 80e7, "Cash And Cash Equivalents": 150e7 * scale,
            "Current Assets": 700e7 * scale, "Current Liabilities": 300e7, "Accounts Receivable": 120e7 * scale,
            "Inventory": 80e7 * scale, "Accounts Payable": 90e7 * scale, "Ordinary Shares Number": shares,
            "Operating Cash Flow": 150e7 * scale, "Capital Expenditure": -50e7 * scale}


@pytest.fixture
def good_fin():
    yrs = {f"{y}-03-31": good_company_year(1.12 ** (y - 2021)) for y in range(2021, 2026)}
    return make_fin("GOOD", yrs)


def flat_prices(start="2020-01-01", end="2026-06-30", price=500.0):
    d = pd.bdate_range(start, end)
    return pd.DataFrame({"date": d, "close": price})
