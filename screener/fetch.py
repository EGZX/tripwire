"""Fetch Yahoo Finance data per symbol with an on-disk cache and rate-limit backoff."""
from __future__ import annotations

import pickle
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

STATEMENTS = ["ttm_income_stmt", "ttm_cashflow", "income_stmt", "cashflow", "balance_sheet",
              "quarterly_balance_sheet"]


def _cache_file(cache_dir: Path, symbol: str) -> Path:
    return Path(cache_dir) / (re.sub(r"[^A-Za-z0-9._-]", "_", symbol) + ".pkl")


def load_cached(cache_dir: Path, symbol: str) -> dict | None:
    f = _cache_file(cache_dir, symbol)
    if not f.exists():
        return None
    with open(f, "rb") as fh:  # our own cache files only
        return pickle.load(fh)


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in text or "too many requests" in text or "429" in text


def fetch_symbol(symbol: str) -> dict:
    t = yf.Ticker(symbol)
    data: dict = {"symbol": symbol, "fetched_at": datetime.now(timezone.utc), "errors": []}
    data["info"] = t.info or {}
    for name in STATEMENTS:
        try:
            data[name] = getattr(t, name)
        except Exception as exc:  # statement missing for this issuer
            if _is_rate_limit(exc):
                raise
            data[name] = pd.DataFrame()
            data["errors"].append(f"{name}: {exc}")
    try:
        data["revenue_estimate"] = t.revenue_estimate
    except Exception as exc:
        if _is_rate_limit(exc):
            raise
        data["revenue_estimate"] = None
    hist = t.history(period="6y", interval="1mo", auto_adjust=False)
    data["history"] = hist[["Close"]] if "Close" in hist else pd.DataFrame()
    return data


def get(symbol: str, cache_dir: Path, max_age_days: float, max_retries: int, force: bool = False,
        log=print) -> tuple[dict | None, bool]:
    """Return (data, fetched_now). Cached data younger than max_age_days is reused."""
    cached = load_cached(cache_dir, symbol)
    if cached and not force:
        age = datetime.now(timezone.utc) - cached["fetched_at"]
        if age < timedelta(days=max_age_days):
            return cached, False
    delay = 30
    for attempt in range(max_retries + 1):
        try:
            data = fetch_symbol(symbol)
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            with open(_cache_file(cache_dir, symbol), "wb") as fh:
                pickle.dump(data, fh)
            return data, True
        except Exception as exc:
            if _is_rate_limit(exc) and attempt < max_retries:
                log(f"    rate limited on {symbol}; sleeping {delay}s")
                time.sleep(delay)
                delay *= 2
                continue
            log(f"    FAILED {symbol}: {type(exc).__name__}: {exc}")
            return cached, False  # fall back to stale cache if any
    return cached, False


def latest_prices(symbols: list[str]) -> dict[str, float]:
    """One batched download for current prices (keeps cached fundamentals usable with fresh prices)."""
    if not symbols:
        return {}
    df = yf.download(symbols, period="7d", interval="1d", auto_adjust=False, progress=False, threads=True,
                     group_by="column")
    if df is None or df.empty:
        return {}
    close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": symbols[0]})
    out = {}
    for s in symbols:
        if s in close:
            col = close[s].dropna()
            if len(col):
                out[s] = float(col.iloc[-1])
    return out


_MINOR = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ILA": ("ILS", 100.0), "ZAc": ("ZAR", 100.0)}


def major_currency(code: str | None) -> tuple[str, float]:
    """Yahoo quotes some markets in minor units (GBp = pence). Returns (major code, divisor)."""
    if not code:
        return "", 1.0
    return _MINOR.get(code, (code.upper(), 1.0))


class FX:
    """Spot FX via Yahoo '<FROM><TO>=X' pairs, cached for the run."""

    def __init__(self):
        self._rates: dict[tuple[str, str], float] = {}

    def rate(self, frm: str, to: str) -> float | None:
        frm, to = major_currency(frm)[0], major_currency(to)[0]
        if not frm or not to:
            return None
        if frm == to:
            return 1.0
        key = (frm, to)
        if key not in self._rates:
            try:
                h = yf.Ticker(f"{frm}{to}=X").history(period="7d", interval="1d")
                self._rates[key] = float(h["Close"].dropna().iloc[-1]) if len(h) else None
            except Exception:
                self._rates[key] = None
            if self._rates[key] is None:
                inv = None
                try:
                    h = yf.Ticker(f"{to}{frm}=X").history(period="7d", interval="1d")
                    inv = float(h["Close"].dropna().iloc[-1]) if len(h) else None
                except Exception:
                    pass
                self._rates[key] = 1.0 / inv if inv else None
        return self._rates[key]
