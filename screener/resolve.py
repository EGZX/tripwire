"""Map watchlist entries to Yahoo Finance symbols: overrides -> cache -> Yahoo search."""
from __future__ import annotations

import csv
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import yfinance as yf

from .universe import Entry, name_tokens, read_text

OTC = {"PNK", "OQB", "OQX", "OTC", "OBB", "OEM"}
CACHE_FIELDS = ["key", "name", "yahoo", "method", "yahoo_name"]


def load_overrides(path: Path) -> dict[str, str]:
    """The versioned overrides plus an optional git-ignored <name>.local.csv beside them (rows keyed by the ISINs of
    personal holdings belong there, not in the shared file). Local rows win."""
    out: dict[str, str] = {}
    for p in (Path(path), Path(path).with_suffix(".local.csv")):
        if p.exists():
            out.update({r["source_key"].strip(): r["yahoo_symbol"].strip()
                        for r in csv.DictReader(read_text(p).splitlines()) if r.get("source_key")})
    return out


def load_cache(path: Path) -> dict[str, dict]:
    if not Path(path).exists():
        return {}
    return {r["key"]: r for r in csv.DictReader(read_text(Path(path)).splitlines())}


def save_cache(path: Path, rows: dict[str, dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_FIELDS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: r["key"]):
            w.writerow({k: r.get(k, "") for k in CACHE_FIELDS})


def similarity(a: str, b: str) -> float:
    ta, tb = " ".join(sorted(name_tokens(a))), " ".join(sorted(name_tokens(b)))
    return SequenceMatcher(None, ta, tb).ratio() if ta and tb else 0.0


def base_symbol(symbol: str) -> str:
    base = symbol.split(".")[0].upper()
    return base.lstrip("0") if base.isdigit() else base


def _search(query: str) -> list[dict]:
    try:
        return [q for q in yf.Search(query, max_results=10, news_count=0).quotes
                if q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND")]
    except Exception:
        return []


def _pick(quotes: list[dict], ticker: str, name: str) -> dict | None:
    """Prefer listings whose base symbol equals the Koyfin ticker; among them prefer non-OTC, Yahoo's order."""
    want = base_symbol(ticker) if ticker else ""
    same = [q for q in quotes if want and base_symbol(q["symbol"]) == want]
    for pool in ([q for q in same if q.get("exchange") not in OTC], same):
        if pool:
            return pool[0]
    if not ticker:  # ISIN search: take the first listing, non-OTC first
        for pool in ([q for q in quotes if q.get("exchange") not in OTC], quotes):
            if pool:
                return pool[0]
    return None


def clean_query(name: str) -> str:
    text = name.replace("�", " ")
    text = re.sub(r"\b(Inc|Corp|Corporation|Ltd|Limited|plc|S\.?A|N\.?V|AG|SE|A/S)\b\.?", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def resolve_one(e: Entry) -> tuple[str, str, str]:
    """Return (symbol, method, yahoo_name); symbol '' if unresolved."""
    if e.isin and not e.ticker:
        q = _pick(_search(e.isin), "", e.name)
        if q:
            return q["symbol"], f"isin:{q.get('quoteType')}", q.get("shortname") or q.get("longname") or ""
    if e.ticker:
        for query in (clean_query(e.name), e.name, e.ticker):
            q = _pick(_search(query), e.ticker, e.name)
            if q:
                return q["symbol"], f"search:{query == e.ticker and 'ticker' or 'name'}", q.get("shortname") or ""
            time.sleep(0.3)
    return "", "unresolved", ""


def resolve(entries: list[Entry], overrides_path: Path, cache_path: Path, log=print) -> list[dict]:
    overrides = load_overrides(overrides_path)
    cache = load_cache(cache_path)
    report = []
    for e in entries:
        if e.yahoo:  # extra tickers are already Yahoo symbols
            report.append({"key": e.key, "name": e.name, "yahoo": e.yahoo, "method": "extra"})
            continue
        ov = overrides.get(e.ticker) or overrides.get(e.isin) or overrides.get(e.key)
        if ov:
            e.skipped = ov.upper() == "SKIP"
            e.yahoo = "" if e.skipped else ov
            report.append({"key": e.key, "name": e.name, "yahoo": e.yahoo, "method": "override"})
            continue
        hit = cache.get(e.key)
        if hit and hit.get("yahoo"):
            e.yahoo = hit["yahoo"]
            report.append(hit)
            continue
        sym, method, yname = resolve_one(e)
        e.yahoo = sym
        row = {"key": e.key, "name": e.name, "yahoo": sym, "method": method, "yahoo_name": yname}
        if sym:
            cache[e.key] = row
        report.append(row)
        log(f"  resolved {e.key:<14} -> {sym or '??':<12} ({method}) {yname}")
        time.sleep(0.3)
    save_cache(cache_path, cache)
    return report
