"""Validated edits of the two input files (watchlist.csv, holdings.csv) for the dashboard's Lists view.

Rows arrive as JSON from the browser. Every value is checked (shape, length, no control characters, no leading
=, + or @ that a spreadsheet would run as a formula), duplicates are refused, and the file is replaced atomically
after a dated copy of the previous version is kept under data/input-history/.
"""
from __future__ import annotations

import csv
import io
import os
import re
import tempfile
from datetime import datetime
from numbers import Real
from pathlib import Path

FIELDS = {
    "watchlist": ["symbol", "name", "isin", "yahoo_symbol", "lists"],
    "holdings": ["isin", "symbol", "name", "yahoo_symbol", "shares", "value_eur", "cost_eur"],
}
MAX_ROWS = 2000
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .\-=^&]{0,23}$")  # Yahoo and exchange tickers: BRK-B, 0700.HK, ^GSPC, EURUSD=X
FORMULA_START = ("=", "+", "@")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def isin_ok(isin: str) -> bool:
    """ISO 6166 shape and Luhn check digit (letters expand to 10..35)."""
    if not ISIN_RE.match(isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return (10 - total % 10) % 10 == int(isin[-1])


def _text(row: dict, key: str, limit: int, where: str) -> str:
    v = row.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(f"{where}: {key} must be text")
    v = v.strip()
    if len(v) > limit:
        raise ValueError(f"{where}: {key} is longer than {limit} characters")
    if CONTROL_RE.search(v):
        raise ValueError(f"{where}: {key} contains control characters")
    if v.startswith(FORMULA_START):
        raise ValueError(f"{where}: {key} may not start with = + or @ (spreadsheet formula)")
    return v


def _amount(row: dict, key: str, where: str) -> str:
    v = row.get(key)
    if v is None or v == "":
        return ""
    if isinstance(v, bool) or not isinstance(v, Real) or v != v or v < 0 or v > 1e12:
        raise ValueError(f"{where}: {key} must be a number between 0 and 10^12")
    return f"{float(v):.6f}".rstrip("0").rstrip(".")


def clean_rows(kind: str, rows) -> list[dict]:
    """Validated rows in file order, as strings ready for csv. Raises ValueError listing the problems."""
    if kind not in FIELDS:
        raise ValueError(f"unknown list {kind!r}")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError(f"rows must be a list of at most {MAX_ROWS} objects")
    out, errors, seen = [], [], {}
    for i, row in enumerate(rows, start=1):
        where = f"row {i}"
        try:
            if not isinstance(row, dict):
                raise ValueError(f"{where}: expected an object")
            r = {k: "" for k in FIELDS[kind]}
            r["symbol"] = _text(row, "symbol", 24, where)
            r["name"] = _text(row, "name", 120, where)
            r["isin"] = _text(row, "isin", 12, where).upper()
            if r["symbol"] and not SYMBOL_RE.match(r["symbol"]):
                raise ValueError(f"{where}: symbol {r['symbol']!r} is not a ticker")
            if r["isin"] and not isin_ok(r["isin"]):
                raise ValueError(f"{where}: {r['isin']!r} is not a valid ISIN")
            r["yahoo_symbol"] = _text(row, "yahoo_symbol", 24, where)
            if r["yahoo_symbol"] and not SYMBOL_RE.match(r["yahoo_symbol"]):
                raise ValueError(f"{where}: yahoo_symbol {r['yahoo_symbol']!r} is not a ticker")
            if kind == "watchlist":
                labels = row.get("lists") or []
                if isinstance(labels, str):
                    labels = labels.split(";")
                if not isinstance(labels, list) or len(labels) > 20:
                    raise ValueError(f"{where}: lists must be up to 20 labels")
                clean = []
                for lab in labels:
                    lab = _text({"l": lab}, "l", 40, where + " list label") if isinstance(lab, str) else None
                    if lab is None:
                        raise ValueError(f"{where}: list labels must be text")
                    if lab and lab not in clean:
                        clean.append(lab)
                r["lists"] = "; ".join(clean)
                key = r["yahoo_symbol"] or r["symbol"] or r["isin"]
                if not key:
                    raise ValueError(f"{where}: give a symbol, an ISIN or a Yahoo symbol")
            else:
                for k in ("shares", "value_eur", "cost_eur"):
                    r[k] = _amount(row, k, where)
                key = r["isin"] or r["yahoo_symbol"] or r["symbol"]
                if not key:
                    raise ValueError(f"{where}: give an ISIN, a symbol or a Yahoo symbol")
            k = key.upper()
            if k in seen:
                raise ValueError(f"{where}: {key} is already in row {seen[k]}")
            seen[k] = i
            out.append(r)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("; ".join(errors[:20]) + (f"; and {len(errors) - 20} more" if len(errors) > 20 else ""))
    return out


def row_key(kind: str, r: dict) -> str:
    if kind == "watchlist":
        return r.get("yahoo_symbol") or r.get("symbol") or r.get("isin")
    return r.get("isin") or r.get("yahoo_symbol") or r.get("symbol")


def read_rows(path: Path) -> list[dict]:
    if not Path(path).is_file():
        return []
    raw = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(io.StringIO(raw))]


def diff(kind: str, old: list[dict], new: list[dict]) -> dict:
    """Added / removed / changed row keys, for the review step."""
    o = {row_key(kind, r).upper(): r for r in old if row_key(kind, r)}
    n = {row_key(kind, r).upper(): r for r in new}
    fields = FIELDS[kind]

    def eq(f, x, y):
        x, y = str(x or "").strip(), str(y or "").strip()
        if f in ("shares", "value_eur", "cost_eur") and x and y:
            try:  # "7317.90" in the file and 7317.9 from the browser are the same amount
                return abs(float(x.replace(",", "")) - float(y.replace(",", ""))) <= 5e-7  # amounts are stored to 6 decimals
            except ValueError:
                pass
        return x == y

    def same(a, b):
        return all(eq(f, a.get(f), b.get(f)) for f in fields)
    return {"added": sorted(k for k in n if k not in o), "removed": sorted(k for k in o if k not in n),
            "changed": sorted(k for k in n if k in o and not same(o[k], n[k]))}


def render(kind: str, rows: list[dict], newline: str = "\n") -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS[kind], lineterminator=newline, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def save(path: Path, kind: str, rows: list[dict], history_dir: Path) -> dict:
    """Dated copy of the current file (if any), then an atomic replace. Keeps the file's line endings."""
    path, history_dir = Path(path), Path(history_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_bytes() if path.is_file() else b""
    newline = "\r\n" if b"\r\n" in original else "\n"
    copy = None
    if original:
        history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        copy = history_dir / f"{kind}_{stamp}.csv"
        n = 1
        while copy.exists():
            copy = history_dir / f"{kind}_{stamp}_{n}.csv"
            n += 1
        copy.write_bytes(original)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(render(kind, rows, newline))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"path": str(path), "history": str(copy) if copy else None}
