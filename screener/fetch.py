"""Fetch Yahoo Finance data per symbol with an on-disk cache and rate-limit backoff."""
from __future__ import annotations

import pickle
import hashlib
import os
import tempfile
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from .market import Observation, quote_time

STATEMENTS = ["ttm_income_stmt", "ttm_cashflow", "income_stmt", "cashflow", "balance_sheet",
              "quarterly_balance_sheet", "quarterly_income_stmt", "quarterly_cashflow"]
CACHE_SCHEMA = 2


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
    data: dict = {"schema": CACHE_SCHEMA, "symbol": symbol, "fetched_at": datetime.now(timezone.utc), "errors": []}
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
        data["errors"].append(f"revenue_estimate: {exc}")
    for name in ("earnings_estimate", "eps_trend", "eps_revisions"):
        try:
            data[name] = getattr(t,name)
        except Exception as exc:
            if _is_rate_limit(exc):raise
            data[name] = None
            data["errors"].append(f"{name}: {exc}")
    hist = t.history(period="6y", interval="1mo", auto_adjust=False)
    data["history"] = hist[["Close"]] if "Close" in hist else pd.DataFrame()
    return data


def get(symbol: str, cache_dir: Path, max_age_days: float, max_retries: int, force: bool = False,
        log=print) -> tuple[dict | None, bool]:
    """Return (data, fetched_now). Cached data younger than max_age_days is reused."""
    cached = load_cached(cache_dir, symbol)
    if cached and not force and cached.get("schema") == CACHE_SCHEMA:
        age = datetime.now(timezone.utc) - cached["fetched_at"]
        if age < timedelta(days=max_age_days):
            return {**cached,"cache_state":"fresh_cache"}, False
    delay = 30
    for attempt in range(max_retries + 1):
        try:
            data = fetch_symbol(symbol)
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            # Immutable raw record precedes the replaceable convenience cache.
            payload=pickle.dumps(data,protocol=pickle.HIGHEST_PROTOCOL)
            digest=hashlib.sha256(payload).hexdigest()
            snapshots=Path(cache_dir)/"snapshots"/_cache_file(Path(),symbol).stem
            snapshots.mkdir(parents=True,exist_ok=True)
            snapshot=snapshots/f"{data['fetched_at'].strftime('%Y%m%dT%H%M%S%f')}-{digest[:12]}.pkl"
            with snapshot.open("xb") as fh:fh.write(payload)
            data.update(source_snapshot=str(snapshot),source_sha256=digest)
            fd,tmp=tempfile.mkstemp(dir=cache_dir,prefix="fetch-",suffix=".tmp")
            try:
                with os.fdopen(fd,"wb") as fh:pickle.dump(data,fh)
                os.replace(tmp,_cache_file(cache_dir,symbol))
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
            return {**data,"cache_state":"fetched"}, True
        except Exception as exc:
            if _is_rate_limit(exc) and attempt < max_retries:
                log(f"    rate limited on {symbol}; sleeping {delay}s")
                time.sleep(delay)
                delay *= 2
                continue
            log(f"    FAILED {symbol}: {type(exc).__name__}: {exc}")
            return ({**cached,"cache_state":"stale_fallback","refresh_error":f"{type(exc).__name__}: {exc}"}
                    if cached else None), False
    return cached, False


def latest_prices(symbols: list[str]) -> dict[str, Observation]:
    """Dated quotes with contemporaneous market cap/share metadata.

    Refreshing just price would preserve a stale share basis across corporate
    actions. A failed quote refresh is explicit; caller may display cached data
    but must not silently turn it into a current candidate.
    """
    if not symbols:
        return {}
    out = {}
    for s in symbols:
        try:
            info=yf.Ticker(s).info or {}
            price=info.get("regularMarketPrice") or info.get("currentPrice")
            if price and quote_time(info):out[s]=Observation(float(price),quote_time(info),"Yahoo quote",info)
        except Exception:
            continue
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
        self._dates: dict[tuple[str,str],str|None] = {}

    def observations(self) -> dict:
        return {f"{a}/{b}":{"rate":v,"as_of":self._dates.get((a,b)),"source":"Yahoo FX"}
                for (a,b),v in self._rates.items()}

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
                clean=h["Close"].dropna() if len(h) else pd.Series(dtype=float)
                self._rates[key] = float(clean.iloc[-1]) if len(clean) else None
                self._dates[key] = clean.index[-1].isoformat() if len(clean) else None
            except Exception:
                self._rates[key] = None
            if self._rates[key] is None:
                inv = None
                try:
                    h = yf.Ticker(f"{to}{frm}=X").history(period="7d", interval="1d")
                    clean=h["Close"].dropna() if len(h) else pd.Series(dtype=float)
                    inv = float(clean.iloc[-1]) if len(clean) else None
                    self._dates[key] = clean.index[-1].isoformat() if len(clean) else None
                except Exception:
                    pass
                self._rates[key] = 1.0 / inv if inv else None
        return self._rates[key]
