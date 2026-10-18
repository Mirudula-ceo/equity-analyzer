import numpy as np
import pandas as pd
import pytest

from equity.backtest.costs import charges, exec_price, max_affordable_shares
from equity.backtest.engine import Backtester, BacktestParams, rebalance_dates
from equity.backtest.performance import full_report, period_returns, stats
from equity.backtest.strategies import MomentumStrategy
from equity.data.collect import unadjust_for_splits


def costs(cfg, **kw):
    c = dict(cfg["costs"])
    c.update(kw)
    return c


def px_frame(dates, open_, close=None):
    close = open_ if close is None else close
    return pd.DataFrame({"open": open_, "close": close}, index=dates)


def static(picks):
    return lambda d: list(picks)


DATES = pd.bdate_range("2023-01-02", "2023-12-29")


# ---------------------------------------------------------------- costs
def test_charges_buy_and_sell(cfg):
    c = cfg["costs"]
    b = charges("buy", 100_000, c)
    s = charges("sell", 100_000, c)
    assert b["stt"] == pytest.approx(100.0)
    assert b["stamp"] == pytest.approx(15.0)
    assert s["stamp"] == 0
    assert s["dp"] == pytest.approx(c["dp_charge_per_sell"])
    assert b["exchange"] == pytest.approx(2.97)
    assert b["gst"] == pytest.approx((b["brokerage"] + b["exchange"] + b["sebi"]) * 0.18)
    assert charges("buy", 0, c)["total"] == 0


def test_brokerage_cap(cfg):
    c = costs(cfg, brokerage_pct=0.5, brokerage_max_per_order=20)
    assert charges("buy", 1_000_000, c)["brokerage"] == 20
    assert charges("buy", 1_000, c)["brokerage"] == pytest.approx(5)


def test_slippage_direction(cfg):
    c = cfg["costs"]
    assert exec_price("buy", 100, c) > 100 > exec_price("sell", 100, c)


def test_max_affordable_shares_fits_cash(cfg):
    c = cfg["costs"]
    for cash, price in [(50_000, 123.45), (10_000, 9_999), (999, 1_000), (0, 10)]:
        n = max_affordable_shares(cash, price, c)
        p = exec_price("buy", price, c)
        assert n * p + charges("buy", n * p, c)["total"] <= cash + 1e-9
        p1 = (n + 1) * p
        assert p1 + charges("buy", p1, c)["total"] > cash


# ---------------------------------------------------------------- engine
def simple_bt(cfg, prices, picks, splits=None, divs=None, **kw):
    p = BacktestParams(500_000, DATES[0], DATES[-1], kw.pop("freq", "Q"), kw.pop("n", 10),
                       kw.pop("tol", 0.02), kw.pop("c", cfg["costs"]))
    strat = picks if callable(picks) else static(picks)
    return Backtester(prices, splits, divs, strat, p, DATES).run()


def test_rebalance_dates_quarter_end():
    r = rebalance_dates(DATES, "Q")
    assert [d.strftime("%m-%d") for d in r] == ["03-31", "06-30", "09-29", "12-29"]


def test_trades_next_open_not_signal_close(cfg):
    o = pd.Series(100.0, index=DATES)
    c = pd.Series(100.0, index=DATES)
    o.loc["2023-04-03"] = 110.0               # first trading day after Q1 signal
    c.loc["2023-03-31"] = 90.0                # signal-day close must NOT be used
    res = simple_bt(cfg, {"A": px_frame(DATES, o, c)}, ["A"])
    first = res.transactions.iloc[0]
    assert first["signal_date"] == pd.Timestamp("2023-03-31")
    assert first["date"] == pd.Timestamp("2023-04-03")
    assert first["open_price"] == 110.0


def test_cash_never_negative_and_accounting_identity(cfg):
    rng = np.random.default_rng(0)
    prices, splits, divs = {}, [], []
    for s in "ABCDEFGHIJKL":
        path = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, len(DATES))))
        prices[s] = px_frame(DATES, path * (1 + rng.normal(0, 0.003, len(DATES))), path)
    divs = pd.DataFrame([{"symbol": "A", "date": pd.Timestamp("2023-08-01"), "amount": 2.0}])
    order = list("ABCDEFGHIJKL")

    def rotating(d):
        k = d.month // 3
        return order[k:] + order[:k]
    res = simple_bt(cfg, prices, rotating, divs=divs)
    assert (res.equity["cash"] >= -1e-6).all()
    rep = full_report(res, None, 0.0)
    pnl = rep["pnl"]
    cil = res.corporate_actions["cash_in_lieu"].sum() if not res.corporate_actions.empty else 0
    assert pnl["realized"] + pnl["unrealized"] + pnl["dividends"] + cil == pytest.approx(pnl["total_change"], rel=1e-9, abs=1e-4)


def test_transaction_costs_reduce_value_exactly(cfg):
    flat = {s: px_frame(DATES, pd.Series(100.0, index=DATES)) for s in "AB"}
    res = simple_bt(cfg, flat, ["A", "B"])
    tx = res.transactions
    assert (tx["side"] == "buy").all()            # flat prices -> no rebalancing after first
    loss = 500_000 - res.equity["value"].iloc[-1]
    assert loss == pytest.approx(tx["charges"].sum() + tx["slippage"].sum(), rel=1e-9)
    zero = costs(cfg, stt_pct=0, exchange_txn_pct=0, sebi_fee_per_crore=0, stamp_duty_buy_pct=0,
                 dp_charge_per_sell=0, brokerage_pct=0, slippage_bps=0)
    res0 = simple_bt(cfg, flat, ["A", "B"], c=zero)
    assert res0.equity["value"].iloc[-1] == pytest.approx(500_000)


def test_equal_weight_rebalancing(cfg):
    zero = costs(cfg, stt_pct=0, exchange_txn_pct=0, sebi_fee_per_crore=0, stamp_duty_buy_pct=0,
                 dp_charge_per_sell=0, slippage_bps=0)
    a = pd.Series(100.0, index=DATES)
    a[a.index >= "2023-05-01"] = 200.0            # A doubles in Q2
    prices = {"A": px_frame(DATES, a), "B": px_frame(DATES, pd.Series(100.0, index=DATES))}
    res = simple_bt(cfg, prices, ["A", "B"], c=zero)
    h = res.holdings
    q2 = h[(h["rebalance_date"] == "2023-06-30") & (h["symbol"] != "CASH")]
    w = q2.set_index("symbol")["weight"]
    assert w["A"] == pytest.approx(0.5, abs=0.01) and w["B"] == pytest.approx(0.5, abs=0.01)
    trims = res.transactions[(res.transactions["side"] == "sell") & (res.transactions["date"] == "2023-07-03")]
    assert trims.iloc[0]["symbol"] == "A" and trims.iloc[0]["exit_type"] == "trim"


def test_exit_when_dropped(cfg):
    flat = {s: px_frame(DATES, pd.Series(100.0, index=DATES)) for s in "ABC"}
    res = simple_bt(cfg, flat, lambda d: ["A", "B"] if d.month < 6 else ["C"])
    final = set(res.final_positions["symbol"])
    assert final == {"C"}
    full = res.transactions[res.transactions["exit_type"] == "full"]
    assert set(full["symbol"]) == {"A", "B"}


def test_split_and_dividend_handling(cfg):
    zero = costs(cfg, stt_pct=0, exchange_txn_pct=0, sebi_fee_per_crore=0, stamp_duty_buy_pct=0,
                 dp_charge_per_sell=0, slippage_bps=0)
    p = pd.Series(1000.0, index=DATES)
    p[p.index >= "2023-06-01"] = 500.0            # 2:1 split ex-date 2023-06-01 (raw prices)
    splits = pd.DataFrame([{"symbol": "A", "date": pd.Timestamp("2023-06-01"), "ratio": 2.0}])
    divs = pd.DataFrame([{"symbol": "A", "date": pd.Timestamp("2023-08-01"), "amount": 5.0}])
    res = simple_bt(cfg, {"A": px_frame(DATES, p)}, ["A"], splits=splits, divs=divs, c=zero)
    ca = res.corporate_actions.iloc[0]
    assert ca["shares_after"] == ca["shares_before"] * 2
    v = res.equity["value"]
    assert v.loc["2023-05-31"] == pytest.approx(v.loc["2023-06-01"])   # split is value-neutral
    d = res.dividends.iloc[0]
    assert d["amount"] == pytest.approx(d["shares"] * 5.0)
    assert v.loc["2023-08-01"] - v.loc["2023-07-31"] == pytest.approx(d["amount"])


def test_bonus_with_fractional_entitlement_pays_cash(cfg):
    p = pd.Series(150.0, index=DATES)
    p[p.index >= "2023-06-01"] = 100.0            # 3:2 bonus => ratio 1.5
    splits = pd.DataFrame([{"symbol": "A", "date": pd.Timestamp("2023-06-01"), "ratio": 1.5}])
    res = simple_bt(cfg, {"A": px_frame(DATES, p)}, ["A"], splits=splits)
    ca = res.corporate_actions.iloc[0]
    assert ca["shares_after"] == int(ca["shares_before"] * 1.5)
    assert ca["cash_in_lieu"] >= 0


def test_unadjust_for_splits_restores_traded_prices():
    d = pd.bdate_range("2024-01-01", periods=6)
    adj = pd.DataFrame({"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0, "dividend": 0.0,
                        "volume": 200.0}, index=d)
    adj.loc[d[1], "dividend"] = 1.0
    raw = unadjust_for_splits(adj, pd.Series([2.0], index=[d[3]]))
    assert list(raw["close"]) == [100, 100, 100, 50, 50, 50]
    assert raw.loc[d[1], "dividend"] == 2.0
    assert raw.loc[d[0], "volume"] == 100


def test_untradable_stock_is_skipped_with_warning(cfg):
    a = px_frame(DATES, pd.Series(100.0, index=DATES))
    b = px_frame(DATES, pd.Series(100.0, index=DATES))
    b.loc["2023-04-03", "open"] = np.nan          # suspended on execution day
    res = simple_bt(cfg, {"A": a, "B": b}, ["A", "B"])
    assert any("could not buy B" in w for w in res.warnings)
    assert (res.equity["cash"] >= 0).all()


# ---------------------------------------------------------------- look-ahead
def test_momentum_strategy_ignores_future_prices():
    d = pd.bdate_range("2020-01-01", "2023-12-29")
    rng = np.random.default_rng(1)
    ac = pd.DataFrame({s: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(d)))) for s in "ABCDE"}, index=d)
    signal_day = pd.Timestamp("2022-06-30")
    p1 = MomentumStrategy(ac)(signal_day)
    ac2 = ac.copy()
    ac2.loc[ac2.index > signal_day, "E"] *= 50     # huge future move
    p2 = MomentumStrategy(ac2)(signal_day)
    assert p1 == p2


def test_engine_signal_sees_only_past(cfg):
    seen = []

    def spy(d):
        seen.append(d)
        return ["A"]
    simple_bt(cfg, {"A": px_frame(DATES, pd.Series(100.0, index=DATES))}, spy)
    assert all(s in DATES for s in seen)
    assert max(seen) < DATES[-1]                   # no signal on the final day


# ---------------------------------------------------------------- performance
def test_stats_known_values():
    idx = pd.date_range("2020-01-01", "2022-01-01", freq="D")
    v = pd.Series(np.linspace(100, 121, len(idx)), index=idx)
    s = stats(v)
    assert s["total_return"] == pytest.approx(0.21)
    assert s["cagr"] == pytest.approx(0.10, abs=0.002)
    dd = pd.Series([100, 120, 90, 130], index=pd.date_range("2020-01-01", periods=4))
    assert stats(dd)["max_drawdown"] == pytest.approx(-0.25)


def test_period_returns_chain():
    idx = pd.date_range("2021-01-01", "2022-12-31", freq="D")
    v = pd.Series(np.linspace(100, 150, len(idx)), index=idx)
    yr = period_returns(v, "Y")
    assert (1 + yr).prod() - 1 == pytest.approx(0.5)


def test_repair_price_spike_decimal_error():
    from equity.data.validate import repair_price_spikes
    d = pd.bdate_range("2019-12-16", periods=8)
    c = [130, 131, 13.02, 13.1, 130.5, 131, 132, 40]     # 2-day bad tick, then a REAL fall
    px = pd.DataFrame({"open": c, "close": c, "adj_close": c}, index=d)
    fixed, bad = repair_price_spikes(px)
    assert list(bad) == [d[2], d[3]]
    assert fixed["close"].iloc[2] == 131 and fixed["close"].iloc[3] == 131
    assert fixed["close"].iloc[-1] == 40                  # genuine move not reverted is kept


def test_repair_ignores_real_split():
    from equity.data.validate import repair_price_spikes
    d = pd.bdate_range("2020-01-01", periods=5)
    c = [100, 100, 50, 50, 50]
    fixed, bad = repair_price_spikes(pd.DataFrame({"close": c}, index=d), split_dates=[d[2]])
    assert bad == []
