import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from equity import db  # noqa: E402

STATUS_COLORS = {"Pass": "#1a7f37", "Watch": "#b7791f", "Fail": "#c62828",
                 "Insufficient Data": "#6b7280", "Not Applicable": "#9ca3af"}
STATUS_ICON = {"Pass": "🟢", "Watch": "🟡", "Fail": "🔴", "Insufficient Data": "⚪", "Not Applicable": "➖"}


def con():
    return db.connect()


def setup(title):
    st.set_page_config(page_title=f"Equity Analyzer · {title}", page_icon="📈", layout="wide")
    st.title(title)


def universe_symbols():
    c = con()
    u = db.read_universe(c, "NIFTY50")
    have = db.symbols_with_prices(c)
    c.close()
    syms = sorted(set(u["symbol"]) | {s for s in have if not s.startswith("^") and not s.endswith(".NS")})
    return syms, u


def pick_symbol(label="NSE symbol"):
    syms, _ = universe_symbols()
    default = st.session_state.get("symbol", syms[0] if syms else "")
    idx = syms.index(default) if default in syms else 0
    if not syms:
        st.warning("No data yet. Go to **Company Search** and download data, or run `run_full_pipeline.bat`.")
        return None
    s = st.selectbox(label, syms, index=idx)
    st.session_state["symbol"] = s
    return s


# ---------------------------------------------------------------- research module helpers
@st.cache_data(show_spinner="Scoring companies (27 metrics)...", ttl=3600)
def ranked_universe(symbols: tuple[str, ...]):
    """Ranking of the scorable universe; cached so every page explains the SAME calculation."""
    from equity.research import service
    c = con()
    t, d = service.rank_universe(c, list(symbols))
    c.close()
    return t, d


def scorable():
    from equity.research import service
    c = con()
    s = tuple(service.scorable_symbols(c))
    label = service.universe_label(c)
    c.close()
    return s, label


def fmt_cr(x):
    return "n/a" if x is None or x != x else f"₹{x / 1e7:,.0f} cr"


@st.cache_data(show_spinner="Loading the ranking...", ttl=3600)
def ranking():
    """The single ranking used everywhere: all-NSE snapshot if present, else live scoring."""
    from equity.research import service
    c = con()
    src = service.ranking_source(c)
    c.close()
    return src


@st.cache_data(show_spinner="Fetching latest news...", ttl=1800)
def cached_news(name: str, symbol: str, days: int = 30):
    from equity.research import news
    return news.google_news(name, symbol, days)


@st.cache_data(show_spinner=False, ttl=3600)
def cached_announcements():
    """Stored NSE announcements, with subject, company symbol (by link or official name) and labels."""
    from equity.research import listings, news, service, snapshot
    c = con()
    service.ensure_lists(c)
    lst = listings.read_listings(c)
    c.close()
    return news.exchange_announcements(snapshot.load_announcements(), listing=lst)


@st.cache_data(show_spinner="Reading the company website...", ttl=6 * 3600)
def cached_scan(url: str, ir_url: str | None = None):
    from equity.research import website
    return website.scan_website(url, ir_url)
