"""Fundamentals backtest: were the operating model's forecasts biased? No prices are used.

For each cohort date the universe is the N largest US non-financial listed filers by revenue, chosen only from
what SEC data showed at that date (no current index membership, so later failures and takeovers stay in). Each
company's inputs are rebuilt from 10-K facts filed on or before the cohort date, the screener's own operating model
builds and projects its base case, and the projection is compared with what the company later reported:
revenue, clean common earnings, equity reinvested, owner distributions, and the return earned on new equity.

    python tools/backtest_fundamentals.py                         # cohorts 2012-2016, 500 companies each
    python tools/backtest_fundamentals.py --cohorts 2014 --size 50

Data: SEC XBRL company facts and frames (data.sec.gov), cached under data/backtest/sec/. Set SEC_USER_AGENT to
identify yourself to the SEC (their fair-access policy asks for a contact); a tool name is used otherwise.
Outputs: data/backtest/firm_years.csv, firms.csv and summary.md.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import time
import warnings
from datetime import date
from pathlib import Path
from statistics import median

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from screener import operating_models as O, policy, statements as S, valuation as V  # noqa: E402
from screener.__main__ import load_config  # noqa: E402

OUT = ROOT / "data" / "backtest"
CACHE = OUT / "sec"
AGENT = os.environ.get("SEC_USER_AGENT") or "Tripwire fundamentals backtest (research tool)"
REVENUE = ["Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet", "SalesRevenueServicesNet"]
INCOME = ["NetIncomeLossAvailableToCommonStockholdersBasic", "NetIncomeLoss"]
DISCONTINUED = ["IncomeLossFromDiscontinuedOperationsNetOfTax",
                "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity"]
EQUITY = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
DIVIDENDS = ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]
BUYBACKS = ["PaymentsForRepurchaseOfCommonStock"]
ISSUANCE = ["ProceedsFromIssuanceOfCommonStock", "ProceedsFromStockOptionsExercised"]
TOTAL_DEBT = ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"]
DEBT_PARTS = ["LongTermDebtNoncurrent", "LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "CommercialPaper"]
CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
KEEP = set(REVENUE + INCOME + DISCONTINUED + EQUITY + DIVIDENDS + BUYBACKS + ISSUANCE + TOTAL_DEBT + DEBT_PARTS + CASH)
FACTS_VERSION = hashlib.sha1(",".join(sorted(KEEP)).encode()).hexdigest()[:8]  # cache follows the concept list
# SIC ranges: finance, insurance and real estate use other models; these use the cyclical family.
FINANCIAL = range(6000, 6800)
CYCLICAL = [range(1000, 1500), range(2900, 3000), range(3310, 3400)]
_last_call = [0.0]


def sec(url: str, path: Path, extract=None):
    """Cached GET; `extract` reduces a large payload before it is stored."""
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    for attempt in range(5):
        wait = 0.12 - (time.time() - _last_call[0])  # SEC fair access: at most 10 requests per second
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        r = requests.get(url, headers={"User-Agent": AGENT, "Accept-Encoding": "gzip"}, timeout=60)
        if r.status_code == 404:
            payload = None
            break
        if r.status_code == 200:
            payload = r.json()
            break
        time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"SEC request failed: {url}")
    payload = extract(payload) if extract and payload is not None else payload
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return payload


def facts(cik: int) -> dict:
    def keep(p):
        gaap = p.get("facts", {}).get("us-gaap", {})
        return {"name": p.get("entityName"),
                "facts": {k: v.get("units", {}).get("USD", []) for k, v in gaap.items() if k in KEEP}}
    return sec(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", CACHE / f"facts_{FACTS_VERSION}" / f"{cik}.json.gz", keep) or {}


def sic(cik: int) -> int | None:
    p = sec(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", CACHE / "submissions" / f"{cik}.json.gz",
            lambda p: {"sic": p.get("sic"), "name": p.get("name")})
    try:
        return int(p["sic"])
    except (TypeError, ValueError, KeyError):
        return None


def frame(taxonomy: str, concept: str, period: str) -> list[dict]:
    p = sec(f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/USD/{period}.json",
            CACHE / "frames" / f"{concept}_{period}.json.gz", lambda p: p.get("data", []))
    return p or []


def universe(year: int, size: int) -> list[int]:
    """Largest listed US non-financial filers by revenue in calendar year-1, as reported (frames)."""
    revenue = {}
    for concept in REVENUE:
        for x in frame("us-gaap", concept, f"CY{year - 1}"):
            revenue[x["cik"]] = max(revenue.get(x["cik"], 0), x["val"])
    listed = {x["cik"] for x in frame("dei", "EntityPublicFloat", f"CY{year - 1}Q2I") if x["val"] > 0}
    chosen = []
    for cik in sorted((c for c in revenue if c in listed), key=lambda c: -revenue[c]):
        code = sic(cik)
        if code is None or code in FINANCIAL:
            continue
        chosen.append(cik)
        if len(chosen) == size:
            break
    return chosen


def _duration(f) -> float:
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days if f.get("start") else 0


def annual(payload: dict, concepts: list[str], *, instant=False, known: date | None = None, first=False) -> dict:
    """{fiscal-year end: value} from 10-K facts. known: only facts filed by then, latest filing wins (point in time).
    first: the value as first reported (the outcome as it was published, not a later restatement)."""
    out = {}
    for concept in concepts:
        chosen = {}
        for f in payload.get("facts", {}).get(concept, []):
            if not f.get("form", "").startswith("10-K") or not f.get("filed"):
                continue
            if instant == bool(f.get("start")) or (not instant and not 330 <= _duration(f) <= 400):
                continue
            filed = date.fromisoformat(f["filed"])
            if known and filed > known:
                continue
            end = pd.Timestamp(f["end"])
            if end in chosen and ((filed >= chosen[end][0]) if first else (filed <= chosen[end][0])):
                continue
            chosen[end] = (filed, float(f["val"]))
        for end, (_, value) in chosen.items():
            out.setdefault(end, value)  # earlier concept in the list wins for the same year
    return out


def preferred(payload: dict, concepts: list[str], *, instant=False, known: date) -> list[str]:
    """The concept the company used for its latest known year first, so later years keep one definition."""
    latest = {}
    for c in concepts:
        s = annual(payload, [c], instant=instant, known=known)
        if s:
            latest[c] = max(s)
    if not latest:
        return concepts
    newest = max(latest.values())
    first = next(c for c in concepts if latest.get(c) == newest)
    return [first] + [c for c in concepts if c != first]


def fiscal_years(series: dict) -> dict:
    """Keep annual period ends (drop same-year duplicates, e.g. a 52/53-week shift, by keeping the latest)."""
    by_year = {}
    for end in sorted(series):
        by_year[(end - pd.Timedelta(days=45)).year] = end
    return {by_year[y]: series[by_year[y]] for y in sorted(by_year)}


def concepts_at(payload: dict, known: date) -> dict:
    return {"revenue": preferred(payload, REVENUE, known=known), "income": preferred(payload, INCOME, known=known),
            "equity": preferred(payload, EQUITY, instant=True, known=known)}


def inputs(payload: dict, known: date, used: dict) -> dict | None:
    revenue = fiscal_years(annual(payload, used["revenue"], known=known))
    if len(revenue) < 2:
        return None
    ends = sorted(revenue)[-4:]
    income = annual(payload, used["income"], known=known)
    disc = annual(payload, DISCONTINUED, known=known)
    equity = annual(payload, used["equity"], instant=True, known=known)
    rows = {"Total Revenue": {e: revenue[e] for e in ends},
            "Net Income Common Stockholders": {e: income[e] for e in ends if e in income},
            "Net Income Discontinuous Operations": {e: disc[e] for e in ends if e in disc}}
    inc = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=sorted(ends, reverse=True))
    bal = pd.DataFrame.from_dict({"Common Stock Equity": {e: equity[e] for e in ends if e in equity}},
                                 orient="index").reindex(columns=sorted(ends, reverse=True))
    if ends[-1] not in income or ends[-1] not in equity:
        return None
    return {"income_stmt": inc, "balance_sheet": bal, "info": {"financialCurrency": "USD", "industry": ""}}


def net_debt(payload: dict, end: pd.Timestamp, known: date) -> float:
    """Book net debt at a fiscal year end as known then: total debt (or its parts) less cash. NaN if unknown."""
    at = lambda concepts: annual(payload, concepts, instant=True, known=known).get(end, math.nan)
    debt = at(TOTAL_DEBT)
    if not math.isfinite(debt):
        parts = [at([c]) for c in ("LongTermDebtNoncurrent", "LongTermDebtCurrent")]
        if not any(math.isfinite(x) for x in parts):
            parts = [at(["LongTermDebtNoncurrent"]), at(["DebtCurrent"])]
        debt = sum(x for x in parts if math.isfinite(x)) if any(math.isfinite(x) for x in parts) else math.nan
    else:
        debt += sum(x for x in (at(["ShortTermBorrowings"]), at(["CommercialPaper"])) if math.isfinite(x))
    cash = at(CASH)
    return debt - (cash if math.isfinite(cash) else 0.0)


def outcomes(payload: dict, start: pd.Timestamp, years: int, used: dict) -> dict[int, dict]:
    """Reported results for forecast years 1..years: fiscal years ending ~t years after the last known one."""
    series = {name: annual(payload, concepts, instant=name == "equity", first=True) for name, concepts in
              (("revenue", used["revenue"]), ("income", used["income"]), ("discontinued", DISCONTINUED), ("equity", used["equity"]),
               ("dividends", DIVIDENDS), ("buybacks", BUYBACKS), ("issuance", ISSUANCE))}
    out = {}
    for t in range(0, years + 1):
        target = start + pd.DateOffset(years=t)
        row = {}
        for name, s in series.items():
            near = [e for e in s if abs((e - target).days) <= 50]
            if near:
                row[name] = s[min(near, key=lambda e: abs((e - target).days))]
        if "revenue" in row and "income" in row:
            row["clean_income"] = row["income"] - row.get("discontinued", 0.0)
            out[t] = row
    return out


def hurdle_at(day: date, cfg: dict) -> float:
    """The screener's hurdle rule with the 10-year Treasury yield of that day (FRED DGS10)."""
    path = CACHE / "DGS10.csv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", timeout=60).text)
    y = pd.read_csv(path)
    y.columns = ["date", "rate"]
    y = y[pd.to_numeric(y["rate"], errors="coerce").notna()]
    rf = float(y[pd.to_datetime(y["date"]) <= pd.Timestamp(day)].iloc[-1]["rate"]) / 100
    v = cfg["valuation"]
    g = cfg["rates"]["USD"]["g"]
    return max(rf + v["equity_risk_premium"], v["min_hurdle"], g + v["min_rate_over_terminal_growth"]), rf


def run_firm(cik: int, cohort: int, cfg: dict, years: int):
    known = date(cohort, 6, 30)
    payload = facts(cik)
    used = concepts_at(payload, known)
    data = inputs(payload, known, used)
    if data is None:
        return None, "insufficient point-in-time 10-K data"
    code = sic(cik)
    family = "cyclical" if any(code in r for r in CYCLICAL) else "operating"
    hurdle, rf = hurdle_at(known, cfg)
    gt = cfg["rates"]["USD"]["g"]
    last = S.statement_date(data["income_stmt"])
    select = lambda f, name: S.Selection(frame=f, source=name, date=last, newest=last, missing=())
    selections = {"income": select(data["income_stmt"], "income_stmt"), "balance": select(data["balance_sheet"], "balance_sheet")}
    try:
        plan = O.build(data, selections, {"terminal_growth": gt, "hurdle": hurdle, "years": years, "family": family})
        base = O.project(plan["base"], years, hurdle, gt)
        stress = O.project(plan["stress"], years, hurdle, gt)
    except V.InvalidModel as exc:
        return None, f"model abstains: {exc}"
    actual = outcomes(payload, last, years, used)
    if 0 not in actual:
        return None, "no starting year in outcomes"
    meta = dict(cik=cik, name=payload.get("name"), cohort=cohort, sic=code, family=family, last_fy=last.date().isoformat(),
                hurdle=hurdle, rf=rf, value=base["value"], stress_value=stress["value"],
                terminal=base["terminal_cashflow"], opening=base["opening_distribution"],
                g0=plan["base"]["growth"], margin0=plan["base"]["margin_start"], margin_t=plan["base"]["margin_terminal"],
                intensity=plan["base"]["equity_to_sales"], revenue0=actual[0]["revenue"], income0=actual[0]["clean_income"],
                equity0=actual[0].get("equity"), years_observed=max(actual),
                net_debt0=net_debt(payload, last, known))
    rows = []
    equity = actual[0].get("equity")
    for f in base["ledger"]:
        t = f["year"]
        a = actual.get(t)
        row = dict(cik=cik, cohort=cohort, year=t, f_revenue=f["revenue"], f_income=f["common_earnings"],
                   f_investment=f["new_equity_investment"], f_distribution=f["net_distribution"])
        if a:
            row.update(a_revenue=a["revenue"], a_income=a["clean_income"], a_equity=a.get("equity"),
                       a_cash_distribution=a.get("dividends", 0.0) + a.get("buybacks", 0.0) - a.get("issuance", 0.0))
            if a.get("equity") is not None and equity is not None:
                row["a_distribution"] = a["income"] - (a["equity"] - equity)  # clean surplus: earnings less equity retained
            equity = a.get("equity", equity)
        rows.append(row)
    return (meta, rows), None


def pooled(df: pd.DataFrame, a: str, f: str) -> float:
    d = df.dropna(subset=[a, f])
    return d[a].sum() / d[f].sum() if len(d) and d[f].sum() else math.nan


def summarise(firms: pd.DataFrame, fy: pd.DataFrame, skipped: dict, years: int) -> str:
    lines = ["# Fundamentals backtest of the operating model", "",
             f"Run {date.today().isoformat()}. Cohorts: {', '.join(map(str, sorted(firms.cohort.unique())))}. "
             f"{len(firms)} company-cohorts modeled; abstentions/skips: " +
             ", ".join(f"{k} {v}" for k, v in sorted(skipped.items(), key=lambda x: -x[1])) + ".", "",
             "Ratios are actual / forecast. Pooled = sum over companies (size-weighted, like an index); "
             "median = typical company. Only years a company actually reported are compared.", "",
             "| Year | Companies reporting | Revenue pooled | Revenue median | Earnings pooled | Distributions pooled (clean surplus) | Distributions pooled (cash) |",
             "|---|---|---|---|---|---|---|"]
    for t in range(1, years + 1):
        d = fy[fy.year == t]
        rep = d.dropna(subset=["a_revenue"])
        med = (rep.a_revenue / rep.f_revenue).median() if len(rep) else math.nan
        lines.append(f"| {t} | {len(rep)} | {pooled(d, 'a_revenue', 'f_revenue'):.2f} | {med:.2f} | "
                     f"{pooled(d, 'a_income', 'f_income'):.2f} | {pooled(d, 'a_distribution', 'f_distribution'):.2f} | "
                     f"{pooled(d, 'a_cash_distribution', 'f_distribution'):.2f} |")
    # Return on new equity over the horizon: change in earnings per unit of equity added (pooled).
    h = years
    end = fy[(fy.year == h)].dropna(subset=["a_income", "a_equity"]).set_index(["cik", "cohort"])
    start = firms.set_index(["cik", "cohort"])
    common = end.index.intersection(start.index)
    e, s = end.loc[common], start.loc[common]
    d_income = (e.a_income - s.income0).sum()
    d_equity = (e.a_equity - s.equity0).sum()
    f_end = fy[fy.year == h].set_index(["cik", "cohort"]).loc[common]
    f_cum_invest = fy[fy.year.between(1, h)].groupby(["cik", "cohort"]).f_investment.sum().loc[common] - s.opening
    lines += ["", f"## Return on new equity over {h} years (companies reporting year {h}: {len(common)})", "",
              f"- Actual: change in clean earnings / change in common equity = {d_income / 1e9:,.0f}bn / {d_equity / 1e9:,.0f}bn = "
              f"**{d_income / d_equity:.1%}**" if d_equity > 0 else "- Actual: equity did not grow in aggregate (buybacks); ratio not meaningful",
              f"- Model: change in forecast earnings / forecast equity invested = "
              f"**{(f_end.f_income - s.income0).sum() / f_cum_invest.sum():.1%}**",
              f"- Hurdle used: {firms.hurdle.median():.2%} (10y Treasury {firms.rf.median():.2%} at the cohort dates)"]
    # Value under realized fundamentals vs model value, discounted at the model's own hurdle.
    vals = []
    for (cik, cohort), g in fy.groupby(["cik", "cohort"]):
        m = start.loc[(cik, cohort)]
        if m.years_observed < years or g.a_distribution.isna().any():
            continue
        disc = [(1 + m.hurdle) ** -t for t in g.year]
        pv_f = sum(x * w for x, w in zip(g.f_distribution, disc))
        pv_a = sum(x * w for x, w in zip(g.a_distribution, disc))
        last = g[g.year == years].iloc[0]
        tv = m.value - m.opening - pv_f                 # model terminal PV
        scale = last.a_income / last.f_income if last.f_income > 0 else math.nan
        vals.append(dict(value=m.value, pv_f=pv_f, pv_a=pv_a, tv=tv, tv_a=tv * scale if scale == scale else math.nan, g0=m.g0,
                         stress=m.stress_value, opening=m.opening, equity0=m.equity0,
                         net_debt0=m.get("net_debt0", math.nan)))
    v = pd.DataFrame(vals).dropna(subset=["pv_f", "pv_a", "tv", "tv_a"])
    v = v[(v.pv_f + v.tv) > 0]
    if len(v):
        v["ratio"] = (v.pv_a + v.tv_a) / (v.pv_f + v.tv)
        v["bucket"] = pd.cut(v.g0, [-1, 0, 0.05, 0.10, 0.20, 10], labels=["shrinking", "0-5%", "5-10%", "10-20%", "above 20%"])
        by = ["", "| Starting growth | Companies | Actual / model, pooled | Actual / model, median |", "|---|---|---|---|"]
        for name, d in v.groupby("bucket", observed=True):
            by.append(f"| {name} | {len(d)} | {(d.pv_a.sum() + d.tv_a.sum()) / (d.pv_f.sum() + d.tv.sum()):.2f} | {d.ratio.median():.2f} |")
        # Stress case: how often did the realized value fall below the model's stress value?
        realized = v.opening + v.pv_a + v.tv_a
        by += ["", "| Starting growth | Realized below stress value | Realized below half the base value |", "|---|---|---|"]
        for name, d in v.assign(realized=realized).groupby("bucket", observed=True):
            by.append(f"| {name} | {(d.realized < d.stress).mean():.0%} | {(d.realized < 0.5 * d.value).mean():.0%} |")
        by.append(f"| all | {(realized < v.stress).mean():.0%} | {(realized < 0.5 * v.value).mean():.0%} |")
        # Leverage: do indebted companies have wider and worse outcomes relative to the model?
        lev = (v.net_debt0 / v.equity0).where(v.equity0 > 0, math.inf)
        v["lev"] = pd.cut(lev, [-math.inf, 0, 0.5, 1.0, 2.0, math.inf], labels=["net cash", "0-0.5", "0.5-1", "1-2", "above 2 or negative equity"])
        by += ["", "| Book net debt / equity | Companies | Median actual / model | 10th percentile | 25th percentile | Below half the model value |", "|---|---|---|---|---|---|"]
        for name, d in v.dropna(subset=["lev"]).groupby("lev", observed=True):
            by.append(f"| {name} | {len(d)} | {d.ratio.median():.2f} | {d.ratio.quantile(.1):.2f} | {d.ratio.quantile(.25):.2f} | {(d.ratio < 0.5).mean():.0%} |")
        lines += ["", f"## Value check (companies reporting all {years} years: {len(v)})", "",
                  f"- 10-year distributions, present value at the hurdle: actual / forecast = **{v.pv_a.sum() / v.pv_f.sum():.2f}**",
                  f"- Terminal value rescaled by actual / forecast year-{years} earnings: **{v.tv_a.sum() / v.tv.sum():.2f}** x the model's",
                  f"- Combined (actual 10-year distributions + rescaled terminal) / (forecast 10-year + model terminal): "
                  f"**{(v.pv_a.sum() + v.tv_a.sum()) / (v.pv_f.sum() + v.tv.sum()):.2f}**",
                  f"- Typical company (median): **{v.ratio.median():.2f}**",
                  "", "Above 1: companies delivered more than the model assumed (forecasts biased low). Below 1: less.", *by]
    key = lambda df: df.cik.astype(str) + "-" + df.cohort.astype(str)
    exited = set(key(firms[firms.years_observed < years]))
    exits = firms[firms.years_observed < years]
    early = fy[fy.year.between(1, 3)]
    lines += ["", "## Survivorship", "",
              f"- {len(exits)} of {len(firms)} company-cohorts stopped reporting before year {years} (takeover, merger, "
              "going private or failure). They are included for the years they reported.",
              f"- Earnings pooled, years 1-3, exits: {pooled(early[key(early).isin(exited)], 'a_income', 'f_income'):.2f}; "
              f"survivors: {pooled(early[~key(early).isin(exited)], 'a_income', 'f_income'):.2f}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohorts", default="2012,2013,2014,2015,2016")
    ap.add_argument("--size", type=int, default=500)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--policy", action="append", default=[], metavar="KEY=VALUE",
                    help="test a [model_policy] setting, e.g. growth_half_life=1.5 (repeatable)")
    ap.add_argument("--out", default="", help="suffix for the output files, e.g. _hl15")
    args = ap.parse_args(argv)
    warnings.filterwarnings("ignore")
    cfg = load_config(ROOT / "config.toml")
    overrides = {k: float(v) for k, v in (x.split("=", 1) for x in args.policy)}
    policy.configure({"model_policy": {**cfg.get("model_policy", {}), **overrides}})
    metas, rows, skipped = [], [], {}
    for cohort in (int(x) for x in args.cohorts.split(",")):
        ciks = universe(cohort, args.size)
        print(f"cohort {cohort}: {len(ciks)} companies", flush=True)
        for i, cik in enumerate(ciks, 1):
            result, reason = run_firm(cik, cohort, cfg, args.years)
            if result is None:
                key = reason.split(":")[0]
                skipped[key] = skipped.get(key, 0) + 1
                continue
            metas.append(result[0])
            rows.extend(result[1])
            if i % 50 == 0:
                print(f"  {i}/{len(ciks)}", flush=True)
    firms, fy = pd.DataFrame(metas), pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    firms.to_csv(OUT / f"firms{args.out}.csv", index=False)
    fy.to_csv(OUT / f"firm_years{args.out}.csv", index=False)
    text = summarise(firms, fy, skipped, args.years)
    if overrides:
        text = text.replace("\n\n", f"\n\nPolicy tested: {overrides}\n\n", 1)
    (OUT / f"summary{args.out}.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
