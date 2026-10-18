"""Load settings.toml and resolve project paths."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import tomllib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
REPORTS_DIR = Path(os.environ.get("EQUITY_REPORTS", PROJECT_ROOT / "reports"))

_cache: dict | None = None


def load_settings(path: Path | None = None, reload: bool = False) -> dict:
    global _cache
    if _cache is not None and path is None and not reload:
        return copy.deepcopy(_cache)
    p = path or (CONFIG_DIR / "settings.toml")
    with open(p, "rb") as fh:
        cfg = tomllib.load(fh)
    if path is None:
        _cache = cfg
    return copy.deepcopy(cfg)


def db_path(cfg: dict | None = None) -> Path:
    if os.environ.get("EQUITY_DB"):
        return Path(os.environ["EQUITY_DB"])
    cfg = cfg or load_settings()
    p = Path(cfg["database_path"])
    return p if p.is_absolute() else PROJECT_ROOT / p
