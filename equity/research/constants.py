"""Controlled vocabularies for the research module (requirements sections 2-5)."""
from __future__ import annotations

# ---------------------------------------------------------------- evidence (section 3)
CONFIRMED = "Confirmed"
GUIDANCE = "Management guidance"
REPORTED = "Reported / unconfirmed"
NOT_FOUND = "Not found"
NOT_APPLICABLE = "Not applicable"
CONFLICTING = "Conflicting"
EVIDENCE_STATUSES = [CONFIRMED, GUIDANCE, REPORTED, NOT_FOUND, NOT_APPLICABLE, CONFLICTING]
# An event on the timeline must have something behind it; "Not found" / "Not applicable"
# describe a SEARCH, so they live in research_coverage, not in events.
EVENT_EVIDENCE = [CONFIRMED, GUIDANCE, REPORTED, CONFLICTING]
EVIDENCE_HELP = {
    CONFIRMED: "Backed by an official filing, report or company document.",
    GUIDANCE: "A future plan the company itself announced. Not yet a running business.",
    REPORTED: "Appeared in news/secondary sources; needs confirmation.",
    NOT_FOUND: "Not found in the sources searched. This does NOT mean the company did not do it.",
    NOT_APPLICABLE: "Does not apply to this company.",
    CONFLICTING: "Sources disagree.",
}

# ---------------------------------------------------------------- event lifecycle (section 5)
ANNOUNCED, UNDERWAY, COMPLETED, DELAYED, CANCELLED = (
    "Announced", "Underway", "Completed", "Delayed", "Cancelled")
EVENT_STATUSES = [ANNOUNCED, UNDERWAY, COMPLETED, DELAYED, CANCELLED]

# ---------------------------------------------------------------- business developments (section 4)
CATEGORIES = [
    "New sector / business entry",
    "Product / brand launch",
    "Emerging business plan",          # e.g. EV
    "Acquisition / merger",
    "Divestment",
    "Subsidiary / joint venture",
    "Capacity / factory expansion",
    "Capex / funding plan",
    "Order / contract win",
    "Regulatory approval",
    "Management change",
    "Debt / default",
    "Litigation / regulatory action",
    "Project delay / cancellation",
    "Other material event",
]
# Research checklist shown per company (each topic: found / not found / not yet searched)
RESEARCH_TOPICS = CATEGORIES[:-1]

BUSINESS_STAGES = ["existing", "proposed"]
REFLECTED = ["yes", "partly", "no", "unknown"]
FINANCIAL_LINKS = ["revenue", "margin", "debt", "capex", "risk", "none"]

# ---------------------------------------------------------------- sources (section 2), ordered by trust
SOURCE_TYPES = {
    "exchange_filing": "NSE/BSE filing",
    "investor_relations": "Investor relations (annual report, presentation, call transcript, results)",
    "official_website": "Company official website",
    "subsidiary_brand_site": "Subsidiary / brand website",
    "financial_publication": "Financial publication (Bloomberg, Mint, Moneycontrol ...)",
    "data_platform": "Financial-data platform (Tickertape, Screener ...)",
}
OFFICIAL_SOURCE_TYPES = {"exchange_filing", "investor_relations", "official_website", "subsidiary_brand_site"}

# ---------------------------------------------------------------- listings (section 1)
SEGMENTS = ["MAINBOARD", "SME", "ETF"]
ACTIVE, NOT_IN_LATEST = "active", "not in latest NSE list"
