"""CLI:  python -m screener [--refresh] [--limit N] [--only SYM,SYM] [--resolve-only]"""
from __future__ import annotations

import argparse
import warnings
import csv
import sys
import time
import tomllib
from datetime import date
from pathlib import Path

import pandas as pd

from . import fetch, metrics, report, resolve, universe, inputs, filings

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_class_overrides(path: Path) -> dict[str, str]:
    """The versioned classification fixes plus an optional git-ignored <name>.local.csv beside them; local rows win."""
    out: dict[str, str] = {}
    for p in (Path(path), Path(path).with_suffix(".local.csv")):
        if p.exists():
            rows = csv.DictReader(universe.read_text(p).splitlines())
            out.update({r["yahoo_symbol"].strip(): r["class"].strip() for r in rows if r.get("yahoo_symbol")})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="screener", description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--refresh", action="store_true", help="ignore cached fundamentals and refetch everything")
    ap.add_argument("--limit", type=int, help="screen only the first N names (testing)")
    ap.add_argument("--only", help="comma-separated Yahoo symbols to screen")
    ap.add_argument("--resolve-only", action="store_true", help="resolve symbols and stop")
    args = ap.parse_args(argv)
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    warnings.filterwarnings("ignore", category=FutureWarning)

    cfg = load_config(Path(args.config))
    ic, fc = cfg["inputs"], cfg["fetch"]
    try:
        cfg["issuer_inputs"]=inputs.load(ROOT/ic.get("valuation_overrides","overrides/valuation.toml"))
    except ValueError as exc:
        print(f"ISSUER INPUT ERROR: {exc}",flush=True)
        return 2
    data_dir = ROOT / "data"
    t0 = time.time()
    log = lambda msg: print(msg, flush=True)

    entries = universe.merge(universe.load_watchlist(ROOT / ic["watchlist"]),
                             universe.load_holdings(ROOT / ic["holdings"]))
    if not entries:
        log(f"No input: create {ic['watchlist']} (and optionally {ic['holdings']}); see examples/")
        return 1
    log(f"Universe: {len(entries)} entries before symbol resolution")
    res_rows = resolve.resolve(entries, ROOT / ic["symbol_overrides"], data_dir / "symbol_map.csv", log=log)
    entries = universe.dedupe_by_symbol(entries)
    skipped = [e for e in entries if e.skipped]
    unresolved = [e for e in entries if not e.yahoo and not e.skipped]
    entries = [e for e in entries if e.yahoo]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        entries = [e for e in entries if e.yahoo in wanted]
    if args.limit:
        entries = entries[: args.limit]
    log(f"Resolved {len(entries)} unique symbols; {len(unresolved)} unresolved; {len(skipped)} skipped by override")
    if args.resolve_only:
        for e in unresolved:
            log(f"  UNRESOLVED {e.key} {e.name}")
        return 0

    # ---------- fetch
    cache_dir = data_dir / "cache"
    raw: dict[str, dict] = {}
    for i, e in enumerate(entries, start=1):
        d, fresh = fetch.get(e.yahoo, cache_dir, fc["fundamentals_max_age_days"], fc["max_retries"],
                             force=args.refresh, log=log)
        if d is not None:
            issuer=cfg["issuer_inputs"].get(e.yahoo,{})
            if issuer.get("sec_cik"):
                d=filings.enrich(d,str(issuer["sec_cik"]),cache_dir)
            raw[e.yahoo] = d
        if fresh:
            elapsed = time.time() - t0
            log(f"  [{i}/{len(entries)}] fetched {e.yahoo:<12} ({elapsed / 60:.1f} min elapsed)")
            time.sleep(fc["pause_seconds"])
    from . import checks
    currencies = {fetch.major_currency((d.get("info") or {}).get("financialCurrency") or (d.get("info") or {}).get("currency"))[0]
                  for d in raw.values()}
    problems = checks.validate(cfg, currencies)
    if problems:
        for p in problems:
            log(f"CONFIG ERROR: {p}")
        log("Fix config.toml and rerun; nothing was written.")
        return 2
    log("Refreshing dated quotes and share/market-cap metadata...")
    prices = fetch.latest_prices(list(raw))
    valuation_date=date.today()  # one calendar date for every result and its report
    fx = fetch.FX()
    class_ov = load_class_overrides(ROOT / ic["classification_overrides"])

    # ---------- metrics
    rows = []
    for e in entries:
        d = raw.get(e.yahoo)
        base = {"symbol": e.yahoo, "name": e.name, "owned": e.owned, "position_eur": e.position_eur or None,
                "lists": e.sources, "isin": e.isin,
                "shares": e.shares or None, "cost_eur": e.cost_eur or None}
        if d is None:
            rows.append({**base, "class": "error", "flags": "fetch failed"})
            continue
        if e.yahoo not in prices:
            d={**d,"quote_refresh_failed":True}
        try:
            m = metrics.compute(d, prices.get(e.yahoo), fx, cfg, class_ov.get(e.yahoo),valuation_date=valuation_date)
        except Exception as exc:  # keep the run going; surface the problem in the report
            m = {"class": "error", "flags": f"metrics failed: {type(exc).__name__}: {exc}"}
        if e.owned and m.get("class") != "error":
            m.update(metrics.cost_basis(m.get("price"), m.get("ceiling_px"), m.get("quote_ccy"), e.shares, e.cost_eur,
                                        e.position_eur, fx))
        rows.append({**base, **m, "symbol": e.yahoo})
    for e in unresolved:
        rows.append({"symbol": "", "name": e.name, "owned": e.owned, "position_eur": e.position_eur or None,
                     "lists": e.sources, "class": "unresolved", "flags": "symbol not resolved"})
    df = pd.DataFrame(rows)

    from . import scoring
    df = scoring.score(df, cfg)

    # symbol sanity: compare source name with Yahoo's name
    res = pd.DataFrame(res_rows)
    names = dict(zip(df["symbol"], df.get("yahoo_name", pd.Series(dtype=str)).fillna("")))
    if len(res):
        if "yahoo_name" not in res:
            res["yahoo_name"] = ""
        res["yahoo_name"] = [names.get(s) or (yn if isinstance(yn, str) else "")
                             for s, yn in zip(res["yahoo"], res["yahoo_name"])]
        res["similarity"] = [resolve.similarity(a, b) for a, b in zip(res["name"], res["yahoo_name"])]

    xlsx, md = report.write(df, res, ROOT / "reports", data_dir / "history", cfg,run_date=valuation_date)
    log(f"Done in {(time.time() - t0) / 60:.1f} min -> {xlsx}")
    log(f"Summary -> {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
