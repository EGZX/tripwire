"""Build the screening universe from Koyfin watchlist exports, the Parqet holdings snapshot and extra tickers."""
from __future__ import annotations

import csv
import glob
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
    key: str                      # stable identity: Koyfin ticker, ISIN, or Yahoo symbol for extras
    name: str
    ticker: str = ""              # Koyfin ticker (no exchange suffix)
    isin: str = ""
    yahoo: str = ""               # resolved Yahoo symbol
    skipped: bool = False         # excluded via overrides (ETFs, funds, ...)
    lists: set[str] = field(default_factory=set)
    owned: bool = False
    shares: float = 0.0
    cost_eur: float = 0.0         # euro cost basis of the current shares (Parqet); 0 = unknown
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


def load_koyfin(pattern: str) -> list[Entry]:
    """Parse Koyfin watchlist CSV exports. Group header rows (single cell) become list labels."""
    entries: dict[str, Entry] = {}
    for path in sorted(glob.glob(pattern)):
        group = Path(path).stem
        for row in csv.reader(read_text(Path(path)).splitlines()):
            cells = [c.strip() for c in row]
            if not cells or not cells[0]:
                continue
            if cells[0] == "Ticker" or cells[0].startswith(("Weighted Average", "Median")):
                continue
            if len(cells) == 1 or not any(cells[1:]):
                group = cells[0]
                continue
            ticker, name = cells[0], cells[1]
            e = entries.setdefault(ticker, Entry(key=ticker, name=name, ticker=ticker))
            e.lists.add(f"Koyfin: {group}")
    return list(entries.values())


def load_parqet(path: Path) -> list[Entry]:
    if not Path(path).exists():
        return []
    out = []
    for row in csv.DictReader(read_text(Path(path)).splitlines()):
        isin = row["isin"].strip()
        out.append(Entry(key=isin, name=row["name"].strip(), isin=isin, owned=True,
                         shares=float(row.get("shares") or 0), position_eur=float(row.get("value_eur") or 0),
                         cost_eur=float(row.get("cost_eur") or 0),
                         lists={"Parqet"}))
    return out


def load_extra(path: Path) -> list[Entry]:
    if not Path(path).exists():
        return []
    out = []
    for row in csv.DictReader(read_text(Path(path)).splitlines()):
        sym = (row.get("yahoo_symbol") or "").strip()
        if sym:
            out.append(Entry(key=sym, name=(row.get("name") or sym).strip(), yahoo=sym, lists={"Extra"}))
    return out


def merge(koyfin: list[Entry], parqet: list[Entry], extra: list[Entry]) -> list[Entry]:
    """Attach Parqet positions to the Koyfin entry with the same company name (exactly one match);
    unmatched holdings stay as separate ISIN entries and are de-duplicated after symbol resolution."""
    merged = list(koyfin)
    for p in parqet:
        hits = [k for k in koyfin if names_match(p.name, k.name)]
        if len(hits) == 1:
            k = hits[0]
            k.owned, k.shares, k.position_eur, k.isin = True, p.shares, p.position_eur, p.isin
            k.cost_eur = p.cost_eur
            k.lists |= p.lists
        else:
            merged.append(p)
    return merged + extra


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
