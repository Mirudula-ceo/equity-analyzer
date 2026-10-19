"""Company-website research for what the 27 metrics cannot measure from numbers.

Eight of the 27 metrics have no free numeric source: six qualitative ones
(business quality, industry growth, moat, management, order book, customer
concentration) and promoter holding / pledge. This module visits the company's
own website (homepage + investor-relations page) and collects, per missing
metric, the official documents and facts a reviewer needs: annual reports,
investor presentations, shareholding patterns, governance pages, press releases.

It does NOT turn marketing text into Pass/Fail - a rating still needs a human
reading the documents (config/qualitative_evidence.toml, data/manual/shareholding.csv).
Proxy figures (e.g. Yahoo's insider holding) are shown as "proxy - not scored".
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-IN,en;q=0.9"}

# document type -> (keywords matched on link text + URL), most specific first
DOC_RULES: list[tuple[str, str]] = [
    ("Shareholding pattern", r"sharehold(?:ing)?[\s_-]*pattern|share[\s_-]*holding|shp\b"),
    ("Annual report", r"annual[\s_-]*report|integrated[\s_-]*report"),
    ("Investor presentation", r"investor[\s_-]*presentation|analyst[\s_-]*presentation|earnings[\s_-]*presentation|"
                              r"investor[\s_-]*deck"),
    ("Earnings call / transcript", r"transcript|earnings[\s_-]*call|conference[\s_-]*call|concall"),
    ("Financial results", r"financial[\s_-]*results|quarterly[\s_-]*results|financials?\b"),
    ("Corporate governance / board", r"governance|board[\s_-]*of[\s_-]*directors|leadership|management[\s_-]*team|"
                                     r"key[\s_-]*managerial|policies"),
    ("Subsidiaries / group companies", r"subsidiar|group[\s_-]*compan|our[\s_-]*companies|joint[\s_-]*venture"),
    ("Press releases / news", r"press[\s_-]*release|newsroom|media[\s_-]*(?:centre|center|room)|\bnews\b"),
    ("Products / brands / businesses", r"products?|brands?|solutions|businesses|our[\s_-]*business|services"),
    ("Investor relations", r"investor|shareholder"),
    ("Sustainability / ESG", r"sustainab|\besg\b|brsr|csr"),
    ("About the company", r"about|who[\s_-]*we[\s_-]*are|overview|profile"),
]
# which documents help with which missing metric
METRIC_EVIDENCE = {
    "business_quality": ["About the company", "Products / brands / businesses", "Annual report"],
    "industry_growth_tam": ["Annual report", "Investor presentation"],
    "moat": ["Annual report", "Investor presentation", "Products / brands / businesses"],
    "promoter_holding": ["Shareholding pattern"],
    "promoter_pledge": ["Shareholding pattern"],
    "management_capital_allocation": ["Corporate governance / board", "Annual report", "Earnings call / transcript"],
    "order_book_visibility": ["Investor presentation", "Earnings call / transcript", "Financial results"],
    "customer_concentration": ["Annual report", "Investor presentation"],
}
WHAT_TO_READ = {
    "business_quality": "Business description and segment results; years of profit; ROCE vs cost of capital.",
    "industry_growth_tam": "Industry structure / outlook section of the annual report (MD&A); cite the industry source.",
    "moat": "Brand, network, cost or licence advantages, and whether margins/ROCE held up over 5 years.",
    "promoter_holding": "Latest quarterly shareholding pattern: promoter & promoter group %.",
    "promoter_pledge": "Same filing: shares pledged or encumbered as % of promoter holding.",
    "management_capital_allocation": "Related-party transactions, auditor changes, acquisitions, dilution, dividends/buybacks.",
    "order_book_visibility": "Order book / backlog vs annual revenue, recurring revenue share.",
    "customer_concentration": "Top-customer / top-10 customer share of revenue (annual report notes).",
}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._txt = [], None, []
        self.title, self.meta, self._in_title = "", {}, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._href, self._txt = a["href"], [a.get("title") or a.get("aria-label") or ""]
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or a.get("property")):
            self.meta[(a.get("name") or a.get("property")).lower()] = a.get("content") or ""

    def handle_data(self, data):
        if self._href is not None:
            self._txt.append(data)
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((re.sub(r"\s+", " ", " ".join(self._txt)).strip(), self._href))
            self._href = None
        elif tag == "title":
            self._in_title = False


def normalise_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    if not re.match(r"https?://", u):
        u = "https://" + u.lstrip("/")
    return u


def classify_link(text: str, href: str) -> str | None:
    hay = f"{text} {href}".lower()
    for doc, pat in DOC_RULES:
        if re.search(pat, hay):
            return doc
    return None


def parse_page(html: str, base_url: str) -> dict:
    p = _Links()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - broken HTML still yields what was parsed
        pass
    host = urlparse(base_url).netloc.replace("www.", "")
    docs = []
    for text, href in p.links:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = urljoin(base_url, href)
        kind = classify_link(text, full)
        if not kind:
            continue
        lhost = urlparse(full).netloc.replace("www.", "")
        same_site = lhost == host or lhost.endswith("." + host) or host.endswith("." + lhost)
        docs.append({"document": kind, "text": (text or full.rsplit("/", 1)[-1])[:120], "url": full,
                     "pdf": full.lower().split("?")[0].endswith(".pdf"), "same_site": same_site,
                     "found_on": base_url})
    desc = p.meta.get("description") or p.meta.get("og:description") or ""
    return {"title": re.sub(r"\s+", " ", p.title).strip()[:200], "description": desc.strip()[:400], "docs": docs}


def fetch(url: str, get=requests.get, timeout: int = 20) -> tuple[str | None, str | None]:
    try:
        r = get(url, headers=UA, timeout=timeout, allow_redirects=True)
        if r.status_code in (401, 403, 429, 503):
            return None, f"{url}: the website refused automated access (HTTP {r.status_code}) - open it in a browser"
        r.raise_for_status()
        ctype = r.headers.get("content-type", "") if hasattr(r, "headers") else "text/html"
        if "html" not in ctype and "xml" not in ctype and ctype:
            return None, f"{url}: not an HTML page ({ctype})"
        return r.text, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{url}: {exc.__class__.__name__}: {str(exc)[:120]}"


def scan_website(url: str, ir_url: str | None = None, get=requests.get, max_pages: int = 4) -> dict:
    """Homepage + investor-relations page (+ the pages it links for annual reports / shareholding)."""
    url = normalise_url(url)
    out = {"website": url, "pages_read": [], "errors": [], "title": "", "description": "", "docs": []}
    if not url:
        out["errors"].append("no website known for this company")
        return out
    queue, seen = [url] + ([normalise_url(ir_url)] if ir_url else []), set()
    while queue and len(out["pages_read"]) < max_pages:
        u = queue.pop(0)
        if not u or u in seen:
            continue
        seen.add(u)
        html, err = fetch(u, get)
        if err:
            out["errors"].append(err)
            continue
        page = parse_page(html, u)
        out["pages_read"].append(u)
        if not out["title"]:
            out["title"], out["description"] = page["title"], page["description"]
        out["docs"] += page["docs"]
        # follow the investor-relations / shareholding / annual-report hub pages (not PDFs) once
        for d in page["docs"]:
            if (d["document"] in ("Investor relations", "Shareholding pattern", "Annual report",
                                  "Investor presentation", "Corporate governance / board")
                    and not d["pdf"] and d["same_site"] and d["url"] not in seen and d["url"] not in queue):
                queue.append(d["url"])
    seen_urls, docs = set(), []
    for d in out["docs"]:
        if d["url"] not in seen_urls:
            seen_urls.add(d["url"])
            docs.append(d)
    out["docs"] = docs
    return out


def evidence_for_missing(results, scan: dict, profile: dict | None = None) -> list[dict]:
    """One row per metric that is Insufficient Data, with the website documents to read and any proxy facts."""
    profile = profile or {}
    docs = scan.get("docs", []) if scan else []
    rows = []
    for r in results:
        if r.status != "Insufficient Data" or r.key not in METRIC_EVIDENCE:
            continue
        want = METRIC_EVIDENCE[r.key]
        found = [d for d in docs if d["document"] in want]
        found.sort(key=lambda d: (want.index(d["document"]), not d["pdf"]))
        proxy = ""
        if r.key == "promoter_holding" and profile.get("heldPercentInsiders") is not None:
            proxy = (f"Yahoo 'insiders' holding {float(profile['heldPercentInsiders']) * 100:.1f}% - close to, but not the "
                     "same as, promoter holding. Proxy only, not scored.")
        if r.key == "management_capital_allocation" and profile.get("companyOfficers"):
            offs = profile["companyOfficers"][:4]
            proxy = "Officers listed by Yahoo: " + "; ".join(f"{o.get('name')} ({o.get('title')})" for o in offs)
        if r.key == "business_quality" and (scan or {}).get("description"):
            proxy = f"Website says: {scan['description'][:200]}"
        rows.append({"metric": f"#{r.id} {r.name}", "key": r.key, "what_to_read": WHAT_TO_READ[r.key],
                     "documents": found[:6], "proxy": proxy,
                     "status": ("documents found on the company website" if found else
                                "no matching document found on the pages read (not proof it does not exist)")})
    return rows


def manual_website(symbol: str, path=None) -> dict:
    """Optional override: data/manual/company_websites.csv with columns symbol,website,ir_url."""
    import pandas as pd

    from ..config import PROJECT_ROOT
    p = path or PROJECT_ROOT / "data" / "manual" / "company_websites.csv"
    try:
        df = pd.read_csv(p, dtype=str)
    except (FileNotFoundError, ValueError):
        return {}
    r = df[df["symbol"].str.upper() == symbol.upper()]
    if r.empty:
        return {}
    return {k: (None if pd.isna(v) else v) for k, v in r.iloc[0].to_dict().items()}
