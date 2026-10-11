"""Build the screening universe from two CSV files: the watchlist and the current holdings.

input/watchlist.csv  symbol,name,isin,yahoo_symbol,lists
    One company per row; give at least one of symbol, isin or yahoo_symbol. A yahoo_symbol is used as is; otherwise
    the symbol (or ISIN) is resolved to a Yahoo listing, helped by the name. `lists` takes free labels separated
    by ';' (for example "Core; Semis"); they become filters in the report and dashboard.
input/holdings.csv   isin,symbol,name,yahoo_symbol,shares,value_eur,cost_eur
    Your positions. cost_eur is the euro cost basis of the shares held (optional; it enables the score at your cost).
    value_eur is optional too: left empty, the run values the position at shares x today's price. A holding attaches to the watchlist row with the same ISIN or
    symbol, else to a unique company-name match, else it is screened on its own.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

# Words that carry no identity when matching company names across sources.
_LEGAL = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "sa", "se", "ag",
    "nv", "n", "v", "spa", "s", "p", "a", "ab", "asa", "as", "oyj", "kgaa", "jsc", "holding", "holdings",
    "group", "the", "class", "adr", "gdr", "ads", "and", "of", "de", "reg", "shs",
}
# Distinctive tokens must be at least this long and not generic.
_GENERIC = {
    "international", "technologies", "technology", "systems", "global", "health", "energy", "capital",
    "financial", "services", "industries", "electric", "group", "holdings", "resources", "brands", "solutions",
    "partners", "enterprise", "networks", "therapeutics", "scientific", "platforms", "markets", "exchange",
}


@dataclass
class Entry:
    key: str                      # stable identity: watchlist symbol, else ISIN, else Yahoo symbol
    name: str
    ticker: str = ""              # symbol as written in the input (exchange suffix optional)
    isin: str = ""
    yahoo: str = ""               # resolved Yahoo symbol
    skipped: bool = False         # excluded via overrides (ETFs, funds, ...)
    lists: set[str] = field(default_factory=set)
    owned: bool = False
    shares: float = 0.0
    cost_eur: float = 0.0         # euro cost basis of the current shares; 0 = unknown
    position_eur: float = 0.0

    @property
    def sources(self) -> str:
        return ", ".join(sorted(self.lists))


def read_text(path: Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def name_tokens(name: str) -> set[str]:
    text = name.lower().replace("�", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return {t for t in text.split() if t not in _LEGAL}


def distinctive(tokens: set[str]) -> set[str]:
    return {t for t in tokens if len(t) >= 4 and t not in _GENERIC}


def names_match(a: str, b: str) -> bool:
    """True when the distinctive tokens of the shorter name are all contained in the other name."""
    ta, tb = distinctive(name_tokens(a)), distinctive(name_tokens(b))
    if not ta or not tb:
        return False
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small <= large


def _rows(path: Path) -> list[dict]:
    return [{k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
            for r in csv.DictReader(read_text(Path(path)).splitlines())]


def _num(v: str) -> float:
    try:
        return float(v.replace(",", "")) if v else 0.0
    except ValueError:
        return 0.0


def load_watchlist(path: Path) -> list[Entry]:
    """Watchlist rows; duplicates of the same company merge their list labels."""
    if not Path(path).exists():
        return []
    entries: dict[str, Entry] = {}
    for r in _rows(path):
        sym, isin, ysym = r.get("symbol", ""), r.get("isin", "").upper(), r.get("yahoo_symbol", "")
        key = sym or isin or ysym
        if not key:
            continue
        e = entries.setdefault(key, Entry(key=key, name=r.get("name") or key, ticker=sym, isin=isin, yahoo=ysym))
        e.lists |= {x.strip() for x in r.get("lists", "").split(";") if x.strip()} or {"Watchlist"}
    return list(entries.values())


def load_index_list(path: Path, label: str) -> list[Entry]:
    """An index or other reference list (yahoo_symbol,name): every row is labeled with the list name. Symbols are
    taken as given Yahoo listings, so no name search runs for hundreds of rows."""
    if not Path(path).exists():
        return []
    out = []
    for r in _rows(path):
        ysym = (r.get("yahoo_symbol") or r.get("symbol") or "").strip()
        if ysym:
            out.append(Entry(key=ysym, name=r.get("name") or ysym, ticker=ysym, yahoo=ysym, lists={label}))
    return out


def load_holdings(path: Path) -> list[Entry]:
    if not Path(path).exists():
        return []
    out = []
    for r in _rows(path):
        isin, sym, ysym = r.get("isin", "").upper(), r.get("symbol", ""), r.get("yahoo_symbol", "")
        if not (isin or sym or ysym):
            continue
        out.append(Entry(key=isin or ysym or sym, name=r.get("name") or isin or ysym or sym, ticker=sym, isin=isin,
                         yahoo=ysym, owned=True,
                         shares=_num(r.get("shares", "")), position_eur=_num(r.get("value_eur", "")),
                         cost_eur=_num(r.get("cost_eur", "")), lists={"Holdings"}))
    return out


def merge(watchlist: list[Entry], holdings: list[Entry]) -> list[Entry]:
    """Attach each holding to its watchlist row (same ISIN, else same symbol, else exactly one company-name match);
    unmatched holdings stay as their own entries and are de-duplicated after symbol resolution."""
    merged = list(watchlist)
    for p in holdings:
        hits = [w for w in watchlist if p.isin and w.isin == p.isin]
        hits = hits or [w for w in watchlist if p.yahoo and p.yahoo in (w.yahoo, w.ticker)]
        hits = hits or [w for w in watchlist if p.ticker and p.ticker in (w.ticker, w.yahoo)]
        hits = hits or [w for w in watchlist if names_match(p.name, w.name)]
        if len(hits) == 1:
            w = hits[0]
            w.owned, w.shares, w.position_eur, w.cost_eur = True, p.shares, p.position_eur, p.cost_eur
            w.isin = w.isin or p.isin
            w.lists |= p.lists
        else:
            merged.append(p)
    return merged


def dedupe_by_symbol(entries: list[Entry]) -> list[Entry]:
    out: dict[str, Entry] = {}
    for e in entries:
        if not e.yahoo:
            out[f"unresolved:{e.key}"] = e
            continue
        if e.yahoo in out:
            t = out[e.yahoo]
            t.lists |= e.lists
            if e.owned:
                t.owned, t.shares, t.position_eur = True, t.shares + e.shares, t.position_eur + e.position_eur
                t.cost_eur += e.cost_eur
            t.isin = t.isin or e.isin
        else:
            out[e.yahoo] = e
    return list(out.values())
