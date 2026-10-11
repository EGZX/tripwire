"""Write an index constituent list for the screener (yahoo_symbol,name), e.g. input/sp500.csv.

    python tools/refresh_index_list.py                    # S&P 500 from Wikipedia -> input/sp500.csv

The list is labeled in config.toml under [inputs] index_lists. Constituents change; refresh before a run that
should reflect the current index. The previous file is replaced only after the new one parsed completely.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {"sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol", "Security", 480)}


def fetch(index: str) -> pd.DataFrame:
    url, symbol, name, minimum = SOURCES[index]
    html = requests.get(url, headers={"User-Agent": "Tripwire index list refresh"}, timeout=30).text
    table = next(t for t in pd.read_html(io.StringIO(html), flavor="lxml") if symbol in t.columns and name in t.columns)
    out = pd.DataFrame({"yahoo_symbol": table[symbol].astype(str).str.strip().str.replace(".", "-", regex=False),
                        "name": table[name].astype(str).str.strip()}).drop_duplicates("yahoo_symbol")
    if len(out) < minimum:
        raise ValueError(f"{index}: only {len(out)} constituents parsed; source layout may have changed")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="sp500", choices=sorted(SOURCES))
    ap.add_argument("--out", default=str(ROOT / "input" / "sp500.csv"))
    args = ap.parse_args(argv)
    rows = fetch(args.index)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    rows.to_csv(tmp, index=False)
    tmp.replace(out)
    print(f"{len(rows)} constituents -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
