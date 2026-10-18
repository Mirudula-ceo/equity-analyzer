import numpy as np
import pandas as pd
import pytest

from conftest import flat_prices, good_company_year, make_fin
from equity.data.validate import check_publication_dates
from equity.metrics.engine import FAIL, INSUFFICIENT, NA, PASS, WATCH, MetricEngine, grade
from equity.metrics.pit import PITData, cagr, safe_div
from equity.metrics.scoring import rank, score


def run(fin, as_of="2026-06-30", info=None, splits=None, qual=None, sh=None, cfg=None, price=500.0):
    pit = PITData("GOOD", fin, flat_prices(price=price), splits if splits is not None else pd.Series(dtype=float),
                  as_of, info or {"sector": "Technology", "industry": "Software"})
    res = MetricEngine(pit, cfg, qualitative=qual or {}, shareholding=sh).run()
    return {r.key: r for r in res}, res


# ---------------------------------------------------------------- basics
def test_safe_div_zero_and_missing():
    assert safe_div(1, 0) is None
    assert safe_div(None, 2) is None
    assert safe_div(np.nan, 2) is None
    assert safe_div(4, 2) == 2


def test_cagr_undefined_for_negative_or_zero():
    assert cagr(-10, 20, 3) is None
    assert cagr(0, 20, 3) is None
    assert cagr(100, 121, 2) == pytest.approx(0.10)
    assert cagr(100, 120, 0) is None


def test_grade_directions():
    assert grade(0.2, {"pass": 0.15, "watch": 0.1}) == PASS
    assert grade(0.12, {"pass": 0.15, "watch": 0.1}) == WATCH
    assert grade(0.05, {"pass": 0.15, "watch": 0.1}) == FAIL
    assert grade(0.3, {"pass": 0.5, "watch": 1.0, "lower_is_better": True}) == PASS
    assert grade(1.5, {"pass": 0.5, "watch": 1.0, "lower_is_better": True}) == FAIL
    assert grade(None, {"pass": 1, "watch": 0}) == INSUFFICIENT


# ---------------------------------------------------------------- engine
def test_all_27_metrics_returned(good_fin, cfg):
    m, res = run(good_fin, cfg=cfg)
    assert len(res) == 27
    assert sorted(r.id for r in res) == list(range(1, 28))
    for r in res:
        assert r.formula and r.criteria and r.source is not None


def test_healthy_company_values(good_fin, cfg):
    m, _ = run(good_fin, cfg=cfg)
    assert m["revenue_growth"].value == pytest.approx(0.12, abs=1e-6)
    assert m["revenue_growth"].status == PASS
    assert m["operating_margin"].value == pytest.approx(0.20)
    assert m["cfo_to_pat"].value == pytest.approx(150 / 140)
    assert m["interest_coverage"].status == PASS
    assert m["fcf"].status == PASS
    assert m["piotroski"].status in (PASS, WATCH, FAIL)


def test_qualitative_metrics_never_auto_pass(good_fin, cfg):
    m, _ = run(good_fin, cfg=cfg)
    for k in ("business_quality", "industry_growth_tam", "moat", "management_capital_allocation",
              "order_book_visibility", "customer_concentration"):
        assert m[k].status == INSUFFICIENT


def test_qualitative_requires_evidence_and_source(good_fin, cfg):
    qual = {"GOOD": {"moat": {"status": "Pass", "evidence": "brand", "source": "AR 2025 p.3"},
                     "business_quality": {"status": "Pass"}}}   # no evidence -> ignored
    m, _ = run(good_fin, cfg=cfg, qual=qual)
    assert m["moat"].status == PASS
    assert m["business_quality"].status == INSUFFICIENT


def test_missing_values_give_insufficient(cfg):
    fin = make_fin("GOOD", {"2025-03-31": {"Total Revenue": 100e7}})
    m, res = run(fin, cfg=cfg)
    assert m["revenue_growth"].status == INSUFFICIENT
    assert m["roe"].status == INSUFFICIENT
    assert m["debt_to_equity"].status == INSUFFICIENT
    assert m["pe"].status == INSUFFICIENT
    assert all(r.status != PASS for r in res)


def test_empty_data_does_not_crash(cfg):
    m, res = run(make_fin("GOOD", {}), cfg=cfg)
    assert all(r.status in (INSUFFICIENT, NA) for r in res)


def test_insufficient_history_two_years(cfg):
    fin = make_fin("GOOD", {"2024-03-31": good_company_year(), "2025-03-31": good_company_year(1.1)})
    m, _ = run(fin, cfg=cfg)
    assert m["revenue_growth"].status == INSUFFICIENT
    assert m["profit_eps_growth"].status == INSUFFICIENT
    assert m["fcf"].status == INSUFFICIENT


def test_negative_earnings(cfg):
    yrs = {f"{y}-03-31": good_company_year() for y in range(2021, 2026)}
    yrs["2025-03-31"]["Net Income Common Stockholders"] = -50e7
    m, _ = run(make_fin("GOOD", yrs), cfg=cfg)
    assert m["profit_eps_growth"].status == FAIL
    assert m["pe"].status == FAIL
    assert m["peg"].status == INSUFFICIENT
    assert m["piotroski"].trend["Net income > 0"] == 0


def test_turnaround_from_losses_is_watch_not_pass(cfg):
    yrs = {f"{y}-03-31": good_company_year() for y in range(2021, 2026)}
    yrs["2021-03-31"]["Net Income Common Stockholders"] = -20e7
    m, _ = run(make_fin("GOOD", yrs), cfg=cfg)
    assert m["profit_eps_growth"].status == WATCH


def test_cumulative_losses_cfo_to_pat_insufficient(cfg):
    yrs = {f"{y}-03-31": good_company_year() for y in range(2022, 2026)}
    for y in yrs.values():
        y["Net Income Common Stockholders"] = -10e7
    m, _ = run(make_fin("GOOD", yrs), cfg=cfg)
    assert m["cfo_to_pat"].status == INSUFFICIENT


def test_zero_interest_debt_free_is_pass(cfg):
    yrs = {f"{y}-03-31": good_company_year() for y in range(2022, 2026)}
    for y in yrs.values():
        y["Interest Expense"] = 0.0
        y["Total Debt"] = 0.0
    m, _ = run(make_fin("GOOD", yrs), cfg=cfg)
    assert m["interest_coverage"].status == PASS
    assert m["debt_to_equity"].value == 0


def test_zero_interest_with_debt_is_insufficient(cfg):
    yrs = {f"{y}-03-31": good_company_year() for y in range(2022, 2026)}
    for y in yrs.values():
        y["Interest Expense"] = 0.0
    m, _ = run(make_fin("GOOD", yrs), cfg=cfg)
    assert m["interest_coverage"].status == INSUFFICIENT


def test_zero_equity_and_revenue_denominators(cfg):
    yrs = {f"{y}-03-31": good_company_year() for y in range(2022, 2026)}
    for y in yrs.values():
        y["Stockholders Equity"] = 0.0
        y["Total Revenue"] = 0.0
    m, _ = run(make_fin("GOOD", yrs), cfg=cfg)
    assert m["roe"].status == INSUFFICIENT
    assert m["debt_to_equity"].status == INSUFFICIENT
    assert m["operating_margin"].status == INSUFFICIENT
    assert m["pb"].status == FAIL         # zero/negative book value is a genuine red flag


def test_bank_gets_not_applicable(good_fin, cfg):
    m, res = run(good_fin, cfg=cfg, info={"sector": "Financial Services", "industry": "Banks - Regional"})
    for k in ("debt_to_equity", "roce", "ev_ebitda", "interest_coverage", "working_capital"):
        assert m[k].status == NA
    assert m["roe"].status in (PASS, WATCH, FAIL)


# ---------------------------------------------------------------- point in time
def test_statement_not_visible_before_publication(good_fin, cfg):
    # FY2025 (Mar-25) is published 75 days later = 2025-06-14
    before = PITData("GOOD", good_fin, flat_prices(), pd.Series(dtype=float), "2025-06-13", {})
    after = PITData("GOOD", good_fin, flat_prices(), pd.Series(dtype=float), "2025-06-14", {})
    assert before.periods()[-1] == pd.Timestamp("2024-03-31")
    assert after.periods()[-1] == pd.Timestamp("2025-03-31")


def test_price_is_point_in_time(good_fin):
    p = flat_prices()
    p.loc[p["date"] > "2024-01-01", "close"] = 9999.0
    pit = PITData("GOOD", good_fin, p, pd.Series(dtype=float), "2023-12-29", {})
    assert pit.price() == 500.0


def test_future_data_does_not_change_past_metrics(good_fin, cfg):
    m1, _ = run(good_fin, as_of="2024-07-01", cfg=cfg)
    future = make_fin("GOOD", {"2025-03-31": {**good_company_year(5.0)}})
    m2, _ = run(pd.concat([good_fin[good_fin["period_end"] < "2025-01-01"], future]), as_of="2024-07-01", cfg=cfg)
    for k in ("revenue_growth", "roe", "roce", "pe", "piotroski"):
        assert m1[k].value == m2[k].value


def test_incorrect_publication_dates_flagged(good_fin):
    bad = good_fin.copy()
    bad.loc[bad["period_end"] == "2024-03-31", "publication_date"] = pd.Timestamp("2024-01-01")
    issues = check_publication_dates(bad, today="2026-01-01")
    assert (issues["problem"].str.contains("before period end")).any()


# ---------------------------------------------------------------- splits
def test_split_handling_with_yahoo_restated_share_counts(cfg):
    """Yahoo restates every year's share count to the latest basis. A 1:2 split in
    Jan 2024 therefore shows 2e8 shares in ALL years, while traded prices halve."""
    yrs = {f"{y}-03-31": good_company_year(1.12 ** (y - 2021)) for y in range(2021, 2026)}
    base, _ = run(make_fin("GOOD", yrs), cfg=cfg, price=500.0)          # no split ever
    yrs2 = {k: dict(v) for k, v in yrs.items()}
    for v in yrs2.values():
        v["Ordinary Shares Number"] *= 2
    splits = pd.Series([2.0], index=[pd.Timestamp("2024-01-15")])
    after, _ = run(make_fin("GOOD", yrs2), cfg=cfg, splits=splits, price=250.0)
    assert after["profit_eps_growth"].value == pytest.approx(base["profit_eps_growth"].value)
    assert after["pe"].value == pytest.approx(base["pe"].value)
    assert after["pb"].value == pytest.approx(base["pb"].value)
    # Point in time BEFORE the split: traded price 500, restated shares must be halved
    fin2 = make_fin("GOOD", yrs2)
    b1, _ = run(make_fin("GOOD", yrs), as_of="2023-12-29", cfg=cfg, price=500.0)
    b2, _ = run(fin2, as_of="2023-12-29", cfg=cfg, splits=splits, price=500.0)
    assert b2["pe"].value == pytest.approx(b1["pe"].value)
    assert b2["margin_of_safety"].value == pytest.approx(b1["margin_of_safety"].value)


# ---------------------------------------------------------------- scoring
def test_scoring_never_rewards_missing(good_fin, cfg):
    _, res = run(good_fin, cfg=cfg)
    sc = score(res, cfg)
    assert sc["applicable"] == 27 - sc["not_applicable"]
    assert sc["conservative_score"] <= sc["score_on_evaluated"]
    # 6 qualitative + 2 shareholding at least are insufficient
    assert sc["insufficient"] >= 8
    _, empty = run(make_fin("GOOD", {}), cfg=cfg)
    assert score(empty, cfg)["conservative_score"] == 0


def test_rank_requires_coverage(cfg):
    t = pd.DataFrame([{"symbol": "A", "conservative_score": 0.9, "coverage": 0.1},
                      {"symbol": "B", "conservative_score": 0.5, "coverage": 0.8}])
    r = rank(t, cfg)
    assert r.iloc[0]["symbol"] == "B"
    assert pd.isna(r[r["symbol"] == "A"]["rank"].iloc[0])


def test_statements_to_long_from_yfinance_shape():
    from equity.data.collect import statements_to_long
    wide = pd.DataFrame({pd.Timestamp("2025-03-31"): [100.0, np.nan], pd.Timestamp("2024-03-31"): [90.0, 5.0]},
                        index=pd.Index(["Total Revenue", "EBIT"]))
    long = statements_to_long(wide, "X", "income", "annual", "INR", 75, "2026-01-01T00:00:00Z")
    assert len(long) == 3                                   # NaN dropped, never fabricated
    r = long[(long["period_end"] == "2025-03-31")].iloc[0]
    assert r["publication_date"] == "2025-06-14" and r["publication_date_estimated"] == 1
    assert r["source"].startswith("Yahoo")
