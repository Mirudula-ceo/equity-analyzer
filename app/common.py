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
