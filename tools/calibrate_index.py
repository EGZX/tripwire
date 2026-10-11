"""Calibration: the market return implied by the automatic models for a whole index.

Runs the automatic models (no class overrides, no issuer inputs) over an index's constituents and solves the one
rate at which the summed model values equal the summed market capitalisations. Compare it with the market's
expected return from an independent source (10y yield + an implied equity risk premium such as Damodaran's):
if the model's index return is materially lower, its cash-flow forecasts are biased low; if similar, the model is
calibrated and a low return on a watchlist reflects how that watchlist is priced.

    python tools/calibrate_index.py                      # S&P 500 constituents from Wikipedia
    python tools/calibrate_index.py --symbols list.csv   # any list with a 'symbol' column (Yahoo symbols)

Output: data/calibration/calibration_<date>.csv and a printed summary. Personal inputs are not read.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
import warnings
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from screener import fetch, metrics, policy, valuation as V  # noqa: E402
from screener.__main__ import load_config  # noqa: E402

SP500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def sp500(out_dir: Path) -> pd.DataFrame:
    import requests
    html = requests.get(SP500, headers={"User-Agent": "tripwire-calibration/1.0"}, timeout=30).text
    table = pd.read_html(io.StringIO(html), flavor="lxml")[0]
    symbols = pd.DataFrame({"symbol": table["Symbol"].str.replace(".", "-", regex=False), "sector": table["GICS Sector"]})
    symbols.to_csv(out_dir / f"sp500_{date.today().isoformat()}.csv", index=False)
    return symbols


def present_value(row: dict, rate: float) -> float:
    """Model value at `rate` per unit of market cap (dimensionless, so companies in any currency can be summed)."""
    flows = json.loads(row["base_cashflows"])
    value = row["base_opening_distribution"] + V.discount(flows, row["base_terminal_cashflow"], rate, row["base_terminal_growth"])
    return value / row["mcap_fc"]


def aggregate_return(rows: list[dict]) -> float:
    """Rate at which sum(cap_i * value_i(r)/cap_i) = sum(cap_i): the index's model-implied return."""
    weight = sum(r["mcap_eur_bn"] for r in rows)
    lo = max(r["base_terminal_growth"] for r in rows) + 1e-4
    gap = lambda rate: sum(r["mcap_eur_bn"] * present_value(r, rate) for r in rows) - weight
    hi = 1.0
    if gap(lo) < 0 or gap(hi) > 0:
        return math.nan
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if gap(mid) > 0 else (lo, mid)
    return (lo + hi) / 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="CSV with a 'symbol' column (and optional 'sector')")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    warnings.filterwarnings("ignore")
    cfg = load_config(Path(args.config))
    policy.configure(cfg)
    cfg["issuer_inputs"] = {}
    out_dir = ROOT / "data" / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(args.symbols) if args.symbols else sp500(out_dir)
    if args.limit:
        universe = universe.head(args.limit)
    fc = cfg["fetch"]
    raw = {}
    t0 = time.time()
    for i, s in enumerate(universe["symbol"], 1):
        d, fresh = fetch.get(s, ROOT / "data" / "cache", fc["fundamentals_max_age_days"], fc["max_retries"], log=print)
        if d is not None:
            raw[s] = d
        if fresh:
            print(f"  [{i}/{len(universe)}] fetched {s} ({(time.time() - t0) / 60:.1f} min)", flush=True)
            time.sleep(fc["pause_seconds"])
    prices = fetch.latest_prices(list(raw))
    fx = fetch.FX()
    sectors = dict(zip(universe["symbol"], universe.get("sector", pd.Series([""] * len(universe)))))
    rows = []
    for s, d in raw.items():
        try:
            m = metrics.compute(d, prices.get(s), fx, cfg, None, valuation_date=date.today())
        except Exception as exc:
            m = {"class": "error", "flags": f"{type(exc).__name__}: {exc}"}
        rows.append({"symbol": s, "sector": sectors.get(s, ""), **{k: m.get(k) for k in (
            "class", "model", "mcap_eur_bn", "mcap_fc", "implied_return", "hurdle", "entry_score", "not_rankable",
            "flags", "base_cashflows", "base_terminal_cashflow", "base_opening_distribution", "base_terminal_growth",
            "est_growth", "terminal_value_fraction")}})
    df = pd.DataFrame(rows)
    df.to_csv(out_dir / f"calibration_{date.today().isoformat()}.csv", index=False)

    ok = lambda r: isinstance(r.get("base_cashflows"), str) and all(
        isinstance(r.get(k), (int, float)) and math.isfinite(r.get(k)) for k in
        ("mcap_eur_bn", "mcap_fc", "base_terminal_cashflow", "base_opening_distribution", "base_terminal_growth")) and r["mcap_fc"] > 0
    valued = [r for r in rows if ok(r)]
    total = df["mcap_eur_bn"].sum(skipna=True)
    print(f"\n{len(universe)} symbols, {len(raw)} fetched, {len(valued)} valued "
          f"({sum(r['mcap_eur_bn'] for r in valued) / total:.0%} of market cap)")
    print(f"Aggregate model-implied return (cap-weighted, all valued): {aggregate_return(valued):.2%}")
    rankable = [r for r in valued if not r.get("not_rankable")]
    print(f"  rankable only ({len(rankable)}): {aggregate_return(rankable):.2%}")
    ir = df.dropna(subset=["implied_return"])
    w = ir["mcap_eur_bn"]
    print(f"Per-company implied return: median {ir['implied_return'].median():.2%}, "
          f"cap-weighted mean {(ir['implied_return'] * w).sum() / w.sum():.2%} (n={len(ir)})")
    print(f"Hurdle median {df['hurdle'].median():.2%}")
    by = {}
    for sector in sorted({r["sector"] for r in valued if r["sector"]}):
        group = [r for r in valued if r["sector"] == sector]
        by[sector] = (len(group), aggregate_return(group))
    for sector, (n, rate) in by.items():
        print(f"  {sector:<28} n={n:<4} {rate:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
