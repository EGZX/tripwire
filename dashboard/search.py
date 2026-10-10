"""Yahoo Finance symbol search for the Lists view (yfinance.Search), cached and rate-limited."""
from __future__ import annotations

import threading
import time

TYPES = {"EQUITY": "Stock", "ETF": "ETF", "MUTUALFUND": "Fund"}
TTL = 600.0          # seconds a query result is reused
MIN_GAP = 0.35       # seconds between calls to Yahoo, whatever the typing speed
_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()
_last = [0.0]


def _query_yahoo(q: str) -> list[dict]:
    import yfinance as yf  # deferred: the dashboard starts without touching the network
    return list(yf.Search(q, max_results=12, news_count=0).quotes or [])


def search(q: str, fetch=_query_yahoo) -> list[dict]:
    """Up to 12 listings for a name, ticker or ISIN: symbol, name, exchange, type."""
    q = " ".join((q or "").split())[:40]
    if len(q) < 1:
        return []
    key = q.lower()
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < TTL:
            return hit[1]
        wait = MIN_GAP - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    try:
        quotes = fetch(q)
    except Exception as exc:  # network trouble or a changed Yahoo endpoint: report, never crash the server
        raise RuntimeError(f"Yahoo search failed: {type(exc).__name__}") from exc
    out = []
    for x in quotes:
        kind = TYPES.get(str(x.get("quoteType", "")).upper())
        sym = str(x.get("symbol") or "")
        if not kind or not sym:
            continue
        out.append({"symbol": sym, "name": x.get("longname") or x.get("shortname") or sym,
                    "exchange": x.get("exchDisp") or x.get("exchange") or "", "type": kind,
                    "sector": x.get("sectorDisp") or x.get("sector") or ""})
    with _lock:
        _cache[key] = (time.monotonic(), out)
        if len(_cache) > 500:  # bounded: drop the oldest entries
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:100]:
                _cache.pop(k, None)
    return out
