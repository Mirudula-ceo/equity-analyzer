"""The 27-metric engine.

Each metric returns a MetricResult with value, status, 5-year trend, the
formula used, the evaluation rule, its data source and a plain-English
explanation. Statuses:

    Pass / Watch / Fail        - evaluated against configurable thresholds
    Insufficient Data          - inputs missing; NEVER counted as a success
    Not Applicable             - metric does not make sense for this industry
                                 (e.g. Debt-to-Equity for a bank)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import tomllib

from ..config import CONFIG_DIR, load_settings
from .pit import PITData, cagr, safe_div

PASS, WATCH, FAIL = "Pass", "Watch", "Fail"
INSUFFICIENT, NA = "Insufficient Data", "Not Applicable"
STATUSES = [PASS, WATCH, FAIL, INSUFFICIENT, NA]


@dataclass
class MetricDef:
    id: int
    key: str
    name: str
    category: str
    kind: str              # quantitative | qualitative | external
    inputs: str
    formula: str
    method: str
    criteria: str


@dataclass
class MetricResult:
    id: int
    key: str
    name: str
    category: str
    value: float | None
    display: str
    status: str
    trend: dict = field(default_factory=dict)
    explanation: str = ""
    source: str = ""
    formula: str = ""
    criteria: str = ""
    inputs: str = ""
    method: str = ""

    def to_dict(self):
        return asdict(self)


Q = "quantitative"
DEFS: list[MetricDef] = [
    MetricDef(1, "business_quality", "Business Quality", "Business quality", "qualitative",
              "Annual reports, business description", "Manual reviewed rating",
              "Analyst review recorded in config/qualitative_evidence.toml",
              "Rubric in qualitative_evidence.toml"),
    MetricDef(2, "industry_growth_tam", "Industry Growth and Total Addressable Market", "Business quality",
              "qualitative", "Industry reports", "Manual reviewed rating",
              "Analyst review with cited industry source", "Rubric in qualitative_evidence.toml"),
    MetricDef(3, "moat", "Competitive Advantage / Economic Moat", "Business quality", "qualitative",
              "Annual reports + margin/ROCE stability", "Manual reviewed rating",
              "Analyst review; ROCE & margin stability shown as supporting indicators",
              "Rubric in qualitative_evidence.toml"),
    MetricDef(4, "revenue_growth", "Revenue Growth", "Growth and profitability", Q,
              "Total Revenue (annual)", "CAGR = (Rev_last / Rev_first)^(1/years) - 1",
              "Up to 5 published fiscal years", "Pass >= 12% CAGR, Watch >= 7%"),
    MetricDef(5, "profit_eps_growth", "Profit and EPS Growth", "Growth and profitability", Q,
              "Net income, shares outstanding (Yahoo restates to one split basis)",
              "EPS = Net income / split-adjusted shares; CAGR of EPS",
              "Up to 5 published fiscal years", "Pass >= 12% CAGR, Watch >= 7%; latest loss = Fail"),
    MetricDef(6, "roce", "ROCE", "Growth and profitability", Q,
              "EBIT, Total assets, Current liabilities",
              "EBIT / (Total assets - Current liabilities)",
              "Each year; status on average of latest 3 years", "Pass >= 15%, Watch >= 10%"),
    MetricDef(7, "roe", "ROE", "Growth and profitability", Q, "Net income, Shareholders' equity",
              "Net income / average equity (year-start and year-end)",
              "Each year; status on average of latest 3 years",
              "Pass >= 15%, Watch >= 10% (banks: 14% / 10%)"),
    MetricDef(8, "roic", "ROIC", "Growth and profitability", Q,
              "EBIT, tax, equity, debt, cash",
              "EBIT x (1 - tax rate) / (Equity + Debt - Cash)",
              "Each year; status on average of latest 3 years", "Pass >= 12%, Watch >= 8%"),
    MetricDef(9, "operating_margin", "Operating Profit Margin", "Cash flow and earnings", Q,
              "Operating income, Revenue", "Operating income / Revenue",
              "Each year; status on average of latest 3 years", "Pass >= 15%, Watch >= 8%"),
    MetricDef(10, "cfo_to_pat", "CFO-to-PAT Conversion", "Cash flow and earnings", Q,
              "Operating cash flow, Net income", "Sum(CFO) / Sum(PAT) over available years",
              "Cumulative over up to 5 years (smooths timing)", "Pass >= 0.9, Watch >= 0.7"),
    MetricDef(11, "fcf", "Free Cash Flow", "Cash flow and earnings", Q,
              "Operating cash flow, Capital expenditure", "FCF = CFO - Capex",
              "Each year; count of positive-FCF years", "Pass: positive in >= 80% of years; Watch >= 60%"),
    MetricDef(12, "fcf_yield", "Free Cash Flow Yield", "Cash flow and earnings", Q,
              "Latest FCF, market cap (price x shares)", "FCF / Market cap",
              "Latest published year vs price on the analysis date", "Pass >= 4%, Watch >= 2%"),
    MetricDef(13, "working_capital", "Working Capital Management", "Cash flow and earnings", Q,
              "Receivables, Inventory, Payables, Revenue, Cost of revenue",
              "Cash conversion cycle = DSO + DIO - DPO (days)",
              "Each year; status on latest", "Pass <= 60 days, Watch <= 120 days"),
    MetricDef(14, "earnings_quality", "Earnings Quality and Non-Core Income", "Cash flow and earnings", Q,
              "Pretax income, Operating income, Interest expense",
              "Non-core share = max(0, Pretax + Interest - Operating income) / Pretax",
              "Average of available years", "Pass <= 10%, Watch <= 25% of pretax profit"),
    MetricDef(15, "debt_to_equity", "Debt-to-Equity", "Financial strength", Q,
              "Total debt, Shareholders' equity", "Total debt / Equity", "Latest published year",
              "Pass <= 0.5, Watch <= 1.0"),
    MetricDef(16, "interest_coverage", "Interest Coverage", "Financial strength", Q,
              "EBIT, Interest expense", "EBIT / Interest expense", "Latest published year",
              "Pass >= 5x, Watch >= 2.5x; debt-free = Pass"),
    MetricDef(17, "piotroski", "Piotroski F-Score", "Financial strength", Q,
              "Two consecutive years of income, balance sheet, cash flow",
              "Sum of 9 binary tests (profitability, leverage/liquidity, efficiency)",
              "Latest year vs prior year; untestable signals score 0",
              "Pass >= 7, Watch >= 5; needs >= 7 testable signals"),
    MetricDef(18, "promoter_holding", "Promoter Shareholding", "Management and future growth", "external",
              "Quarterly shareholding pattern (BSE/NSE filing)", "Promoter shares / Total shares",
              "Latest filing available", "Pass >= 50%, Watch >= 35%"),
    MetricDef(19, "promoter_pledge", "Promoter Pledge", "Management and future growth", "external",
              "Quarterly shareholding pattern (pledge disclosure)", "Pledged / Promoter shares",
              "Latest filing available", "Pass = 0%, Watch <= 10%"),
    MetricDef(20, "management_capital_allocation", "Management Quality and Capital Allocation",
              "Management and future growth", "qualitative",
              "Annual reports, governance record; ROIC trend & dilution shown as indicators",
              "Manual reviewed rating", "Analyst review", "Rubric in qualitative_evidence.toml"),
    MetricDef(21, "order_book_visibility", "Order Book and Revenue Visibility",
              "Management and future growth", "qualitative", "Investor presentations",
              "Order book / annual revenue", "Analyst review", "Rubric in qualitative_evidence.toml"),
    MetricDef(22, "customer_concentration", "Customer and Revenue Concentration",
              "Management and future growth", "qualitative", "Annual report disclosures",
              "Top customer share of revenue", "Analyst review", "Rubric in qualitative_evidence.toml"),
    MetricDef(23, "pe", "P/E and Forward P/E", "Valuation", Q,
              "Price, shares, latest annual net income; Yahoo forward P/E snapshot",
              "P/E = Market cap / Net income", "Price on analysis date vs latest published year",
              "Pass <= 25, Watch <= 40; loss-making = Fail"),
    MetricDef(24, "pb", "P/B", "Valuation", Q, "Market cap, Shareholders' equity",
              "Market cap / Equity", "Latest published year",
              "Pass <= 4, Watch <= 8 (banks: 2.5 / 4)"),
    MetricDef(25, "ev_ebitda", "EV/EBITDA", "Valuation", Q, "Market cap, Debt, Cash, EBITDA",
              "(Market cap + Debt - Cash) / EBITDA", "Latest published year", "Pass <= 15, Watch <= 25"),
    MetricDef(26, "peg", "PEG", "Valuation", Q, "P/E, EPS CAGR",
              "P/E / (EPS CAGR x 100)", "Uses metrics 5 and 23", "Pass <= 1.0, Watch <= 2.0; no growth = Fail"),
    MetricDef(27, "margin_of_safety", "Margin of Safety", "Valuation", Q,
              "FCF history, debt, cash, shares, price",
              "MoS = (DCF value per share - Price) / DCF value per share",
              "10-yr two-stage DCF on average FCF (assumptions in settings.toml)",
              "Pass >= 25%, Watch >= 0%"),
]
DEF_BY_KEY = {d.key: d for d in DEFS}


# ------------------------------------------------------------------ helpers
def grade(value, rule: dict) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return INSUFFICIENT
    if rule.get("lower_is_better"):
        return PASS if value <= rule["pass"] else WATCH if value <= rule["watch"] else FAIL
    return PASS if value >= rule["pass"] else WATCH if value >= rule["watch"] else FAIL


def pct(x, d=1):
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def num(x, d=2, suffix=""):
    return "n/a" if x is None else f"{x:,.{d}f}{suffix}"


def crore(x):
    return "n/a" if x is None else f"₹{x / 1e7:,.0f} Cr"


def to_trend(s: pd.Series) -> dict:
    return {pd.Timestamp(k).strftime("%Y-%m"): (None if pd.isna(v) else float(v)) for k, v in s.items()}


def is_financial(info: dict) -> bool:
    sector = (info.get("sector") or "").lower()
    industry = (info.get("industry") or "").lower()
    return sector == "financial services" or any(
        w in industry for w in ("bank", "insurance", "credit services", "capital markets",
                                "financial", "mortgage"))


def is_bank(info: dict) -> bool:
    return "bank" in (info.get("industry") or "").lower()


def load_qualitative(path: Path | None = None) -> dict:
    p = path or CONFIG_DIR / "qualitative_evidence.toml"
    if not p.exists():
        return {}
    with open(p, "rb") as fh:
        return tomllib.load(fh)


def last_n_avg(s: pd.Series, n=3):
    s = s.dropna()
    return float(s.iloc[-n:].mean()) if len(s) else None


# ------------------------------------------------------------------ engine
class MetricEngine:
    def __init__(self, data: PITData, cfg: dict | None = None,
                 qualitative: dict | None = None, shareholding: pd.DataFrame | None = None):
        self.d = data
        self.cfg = cfg or load_settings()
        self.t = self.cfg["thresholds"]
        self.qual = (qualitative if qualitative is not None else load_qualitative()).get(data.symbol, {}) or {}
        self.sh = shareholding if shareholding is not None else pd.DataFrame()
        self.fin = is_financial(data.info)
        self.bank = is_bank(data.info)
        self.src = data.source_note()
        self._cache: dict = {}

    def _r(self, key, value, display, status, trend=None, explanation="", source=None):
        d = DEF_BY_KEY[key]
        return MetricResult(d.id, key, d.name, d.category, value, display, status, trend or {},
                            explanation, source if source is not None else self.src,
                            d.formula, d.criteria, d.inputs, d.method)

    def _na(self, key, why):
        return self._r(key, None, "—", NA, explanation=why)

    def _insuff(self, key, why, source=None):
        return self._r(key, None, "n/a", INSUFFICIENT, explanation=why, source=source)

    # ---------- derived series
    def eps_series(self) -> pd.Series:
        if "eps" in self._cache:
            return self._cache["eps"]
        ni, sh = self.d.series("net_income"), self.d.series("shares")
        idx = ni.index.intersection(sh.index)
        vals = {}
        rf = self.d.restatement_factor()
        for p in idx:
            # Yahoo share counts are all on one (download-day) basis, so EPS is comparable
            # across years; convert to the as_of basis for display against the price.
            vals[p] = safe_div(ni[p], sh[p] / rf)
        s = pd.Series(vals, dtype=float).sort_index()
        self._cache["eps"] = s
        return s

    def eps_cagr(self):
        s = self.eps_series().dropna()
        if len(s) < 3:
            return None, s
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        return cagr(s.iloc[0], s.iloc[-1], round(yrs)), s

    # ---------- metrics
    def qualitative(self, key) -> MetricResult:
        e = self.qual.get(key)
        if isinstance(e, dict) and e.get("status") in (PASS, WATCH, FAIL) and e.get("evidence") and e.get("source"):
            return self._r(key, None, e["status"] + " (manual)", e["status"],
                           explanation=f"Manual review on {e.get('reviewed_on', '?')} by "
                                       f"{e.get('reviewer', '?')}: {e['evidence']}",
                           source=e["source"])
        hint = ""
        if key == "moat":
            m = self.d.series("operating_income") / self.d.series("revenue")
            if m.dropna().size >= 3:
                hint = f" Supporting indicator: operating margin range {pct(m.min())}–{pct(m.max())} over {m.dropna().size} yrs."
        if key == "management_capital_allocation":
            sh = self.d.series("shares").dropna()
            if len(sh) >= 2:
                hint = (f" Supporting indicator: share count change {pct(sh.iloc[-1] / sh.iloc[0] - 1)} "
                        f"over {len(sh)} yrs (before splits).")
        return self._insuff(key, "Qualitative metric - needs a reviewed entry with evidence and source in "
                                 "config/qualitative_evidence.toml. Not scored automatically." + hint,
                            source="Manual review required")

    def revenue_growth(self):
        s = self.d.series("revenue").dropna()
        if len(s) < 3:
            return self._insuff("revenue_growth", f"Need >= 3 years of revenue; have {len(s)}.")
        yrs = round((s.index[-1] - s.index[0]).days / 365.25)
        g = cagr(s.iloc[0], s.iloc[-1], yrs)
        if g is None:
            return self._insuff("revenue_growth", "Revenue <= 0 at start or end; CAGR undefined.")
        return self._r("revenue_growth", g, pct(g), grade(g, self.t["revenue_growth_cagr"]), to_trend(s),
                       f"Revenue grew from {crore(s.iloc[0])} to {crore(s.iloc[-1])} over {yrs} years "
                       f"({len(s)} annual reports available; 5 preferred).")

    def profit_eps_growth(self):
        g, s = self.eps_cagr()
        if len(s.dropna()) < 3:
            return self._insuff("profit_eps_growth", f"Need >= 3 years of profit and share data; have {len(s.dropna())}.")
        if s.iloc[-1] is None or s.iloc[-1] <= 0:
            return self._r("profit_eps_growth", None, "Loss", FAIL, to_trend(s),
                           "Latest year EPS is zero or negative (loss-making).")
        if g is None:
            return self._r("profit_eps_growth", None, "Turnaround", WATCH, to_trend(s),
                           "Starting EPS was negative; growth rate undefined. Now profitable - review manually.")
        return self._r("profit_eps_growth", g, pct(g), grade(g, self.t["eps_growth_cagr"]), to_trend(s),
                       f"EPS (on today's share basis) went from ₹{s.iloc[0]:.2f} to ₹{s.iloc[-1]:.2f} ({len(s)} years).")

    def roce(self):
        if self.fin:
            return self._na("roce", "ROCE is not meaningful for banks/financial companies (debt is raw material).")
        ce = self.d.series("total_assets") - self.d.series("current_liabilities")
        s = (self.d.series("ebit") / ce.where(ce > 0)).dropna()
        if s.empty:
            return self._insuff("roce", "EBIT or capital employed missing.")
        v = last_n_avg(s)
        return self._r("roce", v, pct(v), grade(v, self.t["roce"]), to_trend(s),
                       f"Average of latest {min(3, len(s))} years; latest year {pct(s.iloc[-1])}.")

    def roe(self):
        ni, eq = self.d.series("net_income"), self.d.series("equity")
        if eq.empty or ni.empty:
            return self._insuff("roe", "Net income or equity missing.")
        avg_eq = (eq + eq.shift(1)) / 2
        avg_eq = avg_eq.fillna(eq)          # first year: closing equity only
        s = (ni / avg_eq.where(avg_eq > 0)).dropna()
        if s.empty:
            return self._insuff("roe", "Equity is zero/negative or data missing.")
        v = last_n_avg(s)
        rule = self.t["roe_bank"] if self.fin else self.t["roe"]
        return self._r("roe", v, pct(v), grade(v, rule), to_trend(s),
                       f"Average of latest {min(3, len(s))} years; latest {pct(s.iloc[-1])}."
                       + (" Bank/financial thresholds applied." if self.fin else ""))

    def roic(self):
        if self.fin:
            return self._na("roic", "ROIC not meaningful for banks/financial companies.")
        ebit, pre, tax = self.d.series("ebit"), self.d.series("pretax"), self.d.series("tax")
        rate = (tax / pre.where(pre > 0)).clip(0, 0.40).reindex(ebit.index).fillna(0.25)
        ic = self.d.series("equity") + self.d.series("total_debt").reindex(ebit.index).fillna(0) \
            - self.d.series("cash").reindex(ebit.index).fillna(0)
        s = (ebit * (1 - rate) / ic.where(ic > 0)).dropna()
        if s.empty:
            return self._insuff("roic", "EBIT, equity or invested capital missing / non-positive.")
        v = last_n_avg(s)
        return self._r("roic", v, pct(v), grade(v, self.t["roic"]), to_trend(s),
                       f"After-tax operating return on (equity + debt - cash); latest {pct(s.iloc[-1])}.")

    def operating_margin(self):
        if self.fin:
            return self._na("operating_margin", "Operating margin is not comparable for financial companies.")
        rev = self.d.series("revenue")
        s = (self.d.series("operating_income") / rev.where(rev > 0)).dropna()
        if s.empty:
            return self._insuff("operating_margin", "Operating income or revenue missing.")
        v = last_n_avg(s)
        return self._r("operating_margin", v, pct(v), grade(v, self.t["operating_margin"]), to_trend(s),
                       f"Average of latest {min(3, len(s))} years; latest {pct(s.iloc[-1])}.")

    def cfo_to_pat(self):
        if self.fin:
            return self._na("cfo_to_pat", "Operating cash flow of lenders mixes in loan growth; not meaningful.")
        cfo, ni = self.d.series("cfo"), self.d.series("net_income")
        idx = cfo.dropna().index.intersection(ni.dropna().index)
        if len(idx) < 2:
            return self._insuff("cfo_to_pat", f"Need >= 2 years with both CFO and PAT; have {len(idx)}.")
        trend = to_trend((cfo[idx] / ni[idx].where(ni[idx] != 0)))
        tot_ni = ni[idx].sum()
        if tot_ni <= 0:
            return self._insuff("cfo_to_pat", "Cumulative profit is zero or negative; conversion ratio not meaningful.")
        v = cfo[idx].sum() / tot_ni
        return self._r("cfo_to_pat", v, num(v, 2, "x"), grade(v, self.t["cfo_to_pat"]), trend,
                       f"Over {len(idx)} years, operating cash flow was {v:.2f}x reported profit.")

    def fcf_series(self):
        cfo, capex = self.d.series("cfo"), self.d.series("capex")
        idx = cfo.dropna().index.intersection(capex.dropna().index)
        # Yahoo reports capex as a negative number
        return (cfo[idx] - capex[idx].abs()).sort_index()

    def fcf(self):
        if self.fin:
            return self._na("fcf", "Free cash flow not meaningful for banks/financial companies.")
        s = self.fcf_series()
        if len(s) < 3:
            return self._insuff("fcf", f"Need >= 3 years of CFO and capex; have {len(s)}.")
        pos = int((s > 0).sum())
        frac = pos / len(s)
        rule = {"pass": self.t["fcf_positive_years"]["pass"] / 5, "watch": self.t["fcf_positive_years"]["watch"] / 5}
        return self._r("fcf", float(s.iloc[-1]), crore(s.iloc[-1]), grade(frac, rule), to_trend(s),
                       f"FCF positive in {pos} of {len(s)} years. Latest {crore(s.iloc[-1])}.")

    def fcf_yield(self):
        if self.fin:
            return self._na("fcf_yield", "Not meaningful for financial companies.")
        s, mc = self.fcf_series(), self.d.market_cap()
        if s.empty or mc is None:
            return self._insuff("fcf_yield", "FCF or market cap (price x shares) unavailable.")
        v = s.iloc[-1] / mc
        return self._r("fcf_yield", v, pct(v, 2), grade(v, self.t["fcf_yield"]), {},
                       f"Latest FCF {crore(s.iloc[-1])} vs market cap {crore(mc)} on "
                       f"{pd.Timestamp(self.d.price_date()).date()}.")

    def working_capital(self):
        if self.fin:
            return self._na("working_capital", "Working-capital cycle not applicable to financial companies.")
        rev = self.d.series("revenue")
        recv = self.d.series("receivables").reindex(rev.index)
        if rev.dropna().empty or recv.dropna().empty:
            return self._insuff("working_capital", "Revenue or receivables missing.")
        cogs = self.d.series("cogs").reindex(rev.index)
        base = cogs.where(cogs > 0).fillna(rev)
        inv = self.d.series("inventory").reindex(rev.index).fillna(0)
        pay = self.d.series("payables").reindex(rev.index).fillna(0)
        s = (recv / rev * 365 + inv / base * 365 - pay / base * 365).dropna()
        if s.empty:
            return self._insuff("working_capital", "Insufficient working-capital items.")
        v = float(s.iloc[-1])
        note = "" if self.d.series("cogs").size else " Cost of revenue unavailable; revenue used as base for DIO/DPO."
        trend_note = ""
        if len(s) >= 2:
            trend_note = " Improving." if s.iloc[-1] < s.iloc[0] else " Deteriorating vs earliest year."
        return self._r("working_capital", v, num(v, 0, " days"),
                       grade(v, self.t["cash_conversion_cycle_days"]), to_trend(s),
                       f"Cash conversion cycle {v:.0f} days in latest year.{trend_note}{note}")

    def earnings_quality(self):
        pre, op = self.d.series("pretax"), self.d.series("operating_income")
        intr = self.d.series("interest_expense").reindex(pre.index).fillna(0).abs()
        if pre.dropna().empty or op.dropna().empty:
            return self._insuff("earnings_quality", "Pretax or operating income missing.")
        noncore = (pre + intr - op).clip(lower=0)
        s = (noncore / pre.where(pre > 0)).dropna()
        if s.empty:
            return self._insuff("earnings_quality", "Pretax profit is zero/negative; share undefined.")
        v = float(s.mean())
        return self._r("earnings_quality", v, pct(v), grade(v, self.t["non_core_income_share"]), to_trend(s),
                       f"On average {pct(v)} of pretax profit came from outside core operations "
                       f"(other income, one-offs). See also CFO-to-PAT.")

    def debt_to_equity(self):
        if self.fin:
            return self._na("debt_to_equity", "Banks/NBFCs are leveraged by design; use capital adequacy instead (not available free).")
        debt, eq = self.d.series("total_debt"), self.d.series("equity")
        if eq.empty:
            return self._insuff("debt_to_equity", "Equity missing.")
        debt = debt.reindex(eq.index)
        if debt.dropna().empty:
            return self._insuff("debt_to_equity", "Total debt not reported by source.")
        s = (debt / eq.where(eq > 0)).dropna()
        if s.empty:
            return self._insuff("debt_to_equity", "Equity non-positive.")
        v = float(s.iloc[-1])
        return self._r("debt_to_equity", v, num(v, 2, "x"), grade(v, self.t["debt_to_equity"]), to_trend(s),
                       f"Debt is {v:.2f}x shareholders' equity in the latest year.")

    def interest_coverage(self):
        if self.fin:
            return self._na("interest_coverage", "Interest is the main cost for lenders; not meaningful.")
        ebit, ie = self.d.latest("ebit"), self.d.latest("interest_expense")
        debt = self.d.latest("total_debt")
        if ebit is None:
            return self._insuff("interest_coverage", "EBIT missing.")
        if (ie is None or ie == 0):
            if debt is not None and debt <= 0:
                return self._r("interest_coverage", None, "Debt-free", PASS, {}, "No debt and no interest expense.")
            return self._insuff("interest_coverage", "Interest expense not reported while debt is non-zero or unknown.")
        s = (self.d.series("ebit") / self.d.series("interest_expense").abs().replace(0, np.nan)).dropna()
        v = ebit / abs(ie)
        return self._r("interest_coverage", v, num(v, 1, "x"), grade(v, self.t["interest_coverage"]), to_trend(s),
                       f"EBIT covers interest {v:.1f} times.")

    def piotroski(self):
        if self.fin:
            return self._na("piotroski", "F-Score tests (current ratio, gross margin) don't fit financial companies.")
        g = lambda k: self.d.series(k, 2)  # noqa: E731
        ni, ta, cfo = g("net_income"), g("total_assets"), g("cfo")
        tests, avail = {}, 0

        def two(s):
            return s.dropna().size == 2

        def add(name, cond):
            nonlocal avail
            if cond is None:
                tests[name] = None
            else:
                tests[name] = int(bool(cond))
                avail += 1
        add("Net income > 0", (ni.iloc[-1] > 0) if ni.size else None)
        add("CFO > 0", (cfo.iloc[-1] > 0) if cfo.size else None)
        roa = ni / ta if two(ni) and two(ta) else None
        add("ROA improved", (roa.iloc[-1] > roa.iloc[0]) if roa is not None and roa.dropna().size == 2 else None)
        add("CFO > Net income", (cfo.iloc[-1] > ni.iloc[-1]) if cfo.size and ni.size else None)
        ltd = g("long_term_debt")
        if two(ltd) and two(ta):
            add("Leverage fell", (ltd / ta).iloc[-1] <= (ltd / ta).iloc[0])
        else:
            add("Leverage fell", None)
        ca, cl = g("current_assets"), g("current_liabilities")
        add("Current ratio improved", ((ca / cl).iloc[-1] > (ca / cl).iloc[0]) if two(ca) and two(cl) else None)
        sh = g("shares")
        add("No new shares issued", (sh.iloc[-1] <= sh.iloc[0] * 1.001) if two(sh) else None)
        gp, rev = g("gross_profit"), g("revenue")
        add("Gross margin improved", ((gp / rev).iloc[-1] > (gp / rev).iloc[0]) if two(gp) and two(rev) else None)
        add("Asset turnover improved", ((rev / ta).iloc[-1] > (rev / ta).iloc[0]) if two(rev) and two(ta) else None)
        score = sum(v for v in tests.values() if v)
        detail = "; ".join(f"{k}: {'✓' if v else ('✗' if v == 0 else '?')}" for k, v in tests.items())
        if avail < 7:
            return self._insuff("piotroski", f"Only {avail}/9 signals testable. {detail}")
        return self._r("piotroski", float(score), f"{score}/9", grade(score, self.t["piotroski"]), tests,
                       f"{avail}/9 signals testable (untestable count as 0). {detail}")

    def _shareholding(self, key, col, rule):
        sh = self.sh
        if sh is not None and not sh.empty:
            sh = sh[sh["publication_date"] <= self.d.as_of].dropna(subset=[col])
        if sh is None or sh.empty:
            extra = ""
            ins = self.d.info.get("heldPercentInsiders")
            if key == "promoter_holding" and ins is not None:
                extra = (f" For reference only (not scored): Yahoo's 'insiders' holding is {pct(ins)}, "
                         "which is not the same as SEBI-defined promoter holding.")
            return self._insuff(key, "No free, automatable source for SEBI shareholding patterns was "
                                     "found that permits scripted access (NSE/BSE pages use anti-bot "
                                     "protection). Add figures from the official filing to "
                                     "data/manual/shareholding.csv." + extra,
                                source="Unavailable")
        last = sh.iloc[-1]
        v = float(last[col])
        trend = to_trend(sh.set_index("period_end")[col])
        return self._r(key, v, pct(v), grade(v, rule), trend,
                       f"As of shareholding period {pd.Timestamp(last['period_end']).date()}.",
                       source=f"{last['source']} (manual import)")

    def pe(self):
        mc, ni = self.d.market_cap(), self.d.latest("net_income")
        fwd = self.d.info.get("forwardPE")
        fwd_txt = (f" Forward P/E (Yahoo analyst-consensus snapshot retrieved "
                   f"{self.d.info.get('_retrieved_at', '?')}, not point-in-time): {fwd:.1f}.") if fwd else \
            " Forward P/E: not available."
        if mc is None or ni is None:
            return self._insuff("pe", "Market cap or net income missing." + fwd_txt)
        if ni <= 0:
            return self._r("pe", None, "Loss-making", FAIL, {}, "Latest annual net income is negative; P/E not meaningful." + fwd_txt)
        v = mc / ni
        self._cache["pe"] = v
        return self._r("pe", v, num(v, 1, "x"), grade(v, self.t["pe"]), {},
                       f"Price {num(self.d.price())} on {pd.Timestamp(self.d.price_date()).date()} vs latest "
                       f"annual profit {crore(ni)}." + fwd_txt)

    def pb(self):
        mc, eq = self.d.market_cap(), self.d.latest("equity")
        if mc is None or eq is None:
            return self._insuff("pb", "Market cap or equity missing.")
        if eq <= 0:
            return self._r("pb", None, "Negative equity", FAIL, {}, "Book value is negative.")
        v = mc / eq
        rule = self.t["pb_bank"] if self.fin else self.t["pb"]
        return self._r("pb", v, num(v, 2, "x"), grade(v, rule), {},
                       f"Market cap {crore(mc)} vs book value {crore(eq)}."
                       + (" Bank thresholds applied." if self.fin else ""))

    def ev_ebitda(self):
        if self.fin:
            return self._na("ev_ebitda", "EV/EBITDA not meaningful for financial companies.")
        mc, ebitda = self.d.market_cap(), self.d.latest("ebitda")
        if mc is None or ebitda is None:
            return self._insuff("ev_ebitda", "Market cap or EBITDA missing.")
        if ebitda <= 0:
            return self._r("ev_ebitda", None, "Negative EBITDA", FAIL, {}, "EBITDA is negative.")
        ev = mc + (self.d.latest("total_debt") or 0) - (self.d.latest("cash") or 0)
        v = ev / ebitda
        return self._r("ev_ebitda", v, num(v, 1, "x"), grade(v, self.t["ev_ebitda"]), {},
                       f"Enterprise value {crore(ev)} vs EBITDA {crore(ebitda)}.")

    def peg(self):
        pe = self._cache.get("pe")
        if pe is None:
            self.pe()
            pe = self._cache.get("pe")
        g, s = self.eps_cagr()
        if pe is None:
            return self._insuff("peg", "P/E unavailable or company loss-making.")
        if g is None:
            return self._insuff("peg", "EPS growth rate unavailable (needs >= 3 positive years).")
        if g <= 0:
            return self._r("peg", None, "No growth", FAIL, {}, f"EPS CAGR is {pct(g)}; PEG undefined.")
        v = pe / (g * 100)
        return self._r("peg", v, num(v, 2), grade(v, self.t["peg"]), {},
                       f"P/E {pe:.1f} divided by EPS growth {pct(g)}.")

    def margin_of_safety(self):
        if self.fin:
            return self._na("margin_of_safety", "FCF-based DCF not suitable for financial companies.")
        dcf = self.cfg["dcf"]
        s = self.fcf_series()
        price, shares = self.d.price(), self.d.shares_now()
        if len(s) < 3 or price is None or not shares:
            return self._insuff("margin_of_safety", "Needs >= 3 years FCF, price and share count.")
        base = float(s.iloc[-3:].mean())
        if base <= 0:
            return self._insuff("margin_of_safety", "Average FCF is not positive; DCF not meaningful.")
        rev = self.d.series("revenue").dropna()
        g = cagr(rev.iloc[0], rev.iloc[-1], round((rev.index[-1] - rev.index[0]).days / 365.25)) if len(rev) >= 3 else None
        g = min(max(g if g is not None else dcf["min_growth"], dcf["min_growth"]), dcf["max_growth"])
        r, tg, n = dcf["discount_rate"], dcf["terminal_growth"], dcf["years"]
        pv, f = 0.0, base
        for yr in range(1, n + 1):
            gy = g + (tg - g) * (yr - 1) / max(n - 1, 1)   # fade growth to terminal
            f *= 1 + gy
            pv += f / (1 + r) ** yr
        tv = f * (1 + tg) / (r - tg) / (1 + r) ** n
        eq_val = pv + tv - (self.d.latest("total_debt") or 0) + (self.d.latest("cash") or 0)
        iv = eq_val / shares
        if iv <= 0:
            return self._r("margin_of_safety", None, "Negative value", FAIL, {}, "DCF equity value negative after debt.")
        v = (iv - price) / iv
        return self._r("margin_of_safety", v, pct(v), grade(v, self.t["margin_of_safety"]),
                       {"DCF value/share": iv, "Price": price},
                       f"Estimated value ₹{iv:,.0f}/share vs price ₹{price:,.0f}. Assumes avg FCF {crore(base)}, "
                       f"growth {pct(g)} fading to {pct(tg)}, discount {pct(r)}. Highly assumption-sensitive.")

    # ---------- run all
    def run(self) -> list[MetricResult]:
        out = []
        for d in DEFS:
            try:
                if d.kind == "qualitative":
                    out.append(self.qualitative(d.key))
                elif d.key == "promoter_holding":
                    out.append(self._shareholding(d.key, "promoter_pct", self.t["promoter_holding"]))
                elif d.key == "promoter_pledge":
                    out.append(self._shareholding(d.key, "promoter_pledged_pct", self.t["promoter_pledge"]))
                else:
                    out.append(getattr(self, d.key)())
            except Exception as exc:  # noqa: BLE001 - one bad metric must not break the report
                out.append(self._insuff(d.key, f"Calculation error: {exc}"))
        return out


def results_frame(results: list[MetricResult]) -> pd.DataFrame:
    return pd.DataFrame([r.to_dict() for r in results])
