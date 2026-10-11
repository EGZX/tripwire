"""Candidate status from the absolute entry score, plus descriptive peer-group percentiles for context.

Status depends only on a name's own price, ceiling and data; adding or removing other names cannot change it.
The pillar percentiles (value / quality / growth / balance) are shown as context and are not gates.
"""
from __future__ import annotations

import pandas as pd

# metric -> (pillar, higher_is_better)
OPERATING = {
    "nopat_yield": ("value", True), "fcf_yield": ("value", True), "ebit_yield": ("value", True),
    "roic": ("quality", True), "gross_profitability": ("quality", True),
    "margin_stability": ("quality", False), "cash_conversion": ("quality", True),
    "rev_cagr_3y": ("growth", True), "analyst_growth": ("growth", True),
    "net_debt_ebitda": ("balance", False), "share_cagr": ("balance", False),
}
FINANCIAL = {
    "earnings_yield": ("value", True), "book_yield": ("value", True),
    "roe": ("quality", True), "roe_stability": ("quality", False),
    "rev_cagr_3y": ("growth", True), "analyst_growth": ("growth", True),
    "equity_to_assets": ("balance", True), "share_cagr": ("balance", False),
}
PILLARS = ["value", "quality", "growth", "balance"]
SCORED = ("operating", "cyclical", "financial", "asset")
# Flags that block candidate status: the price, the units or the valuation inputs cannot be trusted. Purely
# informational flags (country proxy, priced capital calls, limited-liability endpoint, FX used) do not block.
CRITICAL_FLAGS = ("stale financials", "no market cap",
                  "quote date unavailable", "quote refresh failed", "stale cache fallback",
                  "stale or future quote",
                  "stale or future rates", "rate date unavailable", "filing verification unavailable")
CRITICAL_FLAGS += ("stale or future FX","FX date unavailable")
CRITICAL_FLAGS += ("TTM/quarterly inputs unreconciled", "lease/cash-flow basis unreconciled")
CRITICAL_FLAGS += ("unresolved minor-unit market capitalization", "market-cap override does not match", "unresolved ownership scope")
CRITICAL_FLAGS += ('unvalued operating or project assets','capitalization estimated from quote and shares')
CRITICAL_FLAGS += ('holding company valued on consolidated book',)


def percentiles(df: pd.DataFrame, spec: dict, weights: dict) -> pd.DataFrame:
    df = df.copy()
    for pillar in PILLARS:
        cols = []
        for metric, (p, higher) in spec.items():
            if p != pillar or metric not in df:
                continue
            vals = pd.to_numeric(df[metric], errors="coerce").replace([float("inf"),float("-inf")],float("nan"))
            df[f"pct_{metric}"] = (vals if higher else -vals).rank(pct=True) * 100
            cols.append(f"pct_{metric}")
        df[f"score_{pillar}"] = df[cols].mean(axis=1, skipna=True) if cols else float("nan")
    w = pd.Series({p: weights[f"w_{p}"] for p in PILLARS})
    scores = df[[f"score_{p}" for p in PILLARS]]
    scores.columns = PILLARS
    weight_used = scores.notna().mul(w, axis=1).sum(axis=1)
    df["composite"] = scores.mul(w, axis=1).sum(axis=1, skipna=True) / weight_used.where(weight_used > 0)
    return df


def score(results: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    weights = cfg["scoring"]
    parts = []
    for classes, spec in ((("operating", "cyclical"), OPERATING), (("financial",), FINANCIAL)):
        grp = results[results["class"].isin(classes)]
        if len(grp):
            parts.append(percentiles(grp, spec, weights))
    rest = results[~results["class"].isin(("operating","cyclical","financial"))]
    out = pd.concat(parts + [rest], ignore_index=True, sort=False)

    flags = out.get("flags", pd.Series("", index=out.index)).fillna("")
    critical = flags.str.contains("|".join(CRITICAL_FLAGS), regex=True)
    blocked = out.get("not_rankable", pd.Series("", index=out.index)).fillna("").astype(str).str.len() > 0
    es = pd.to_numeric(out.get("entry_score",pd.Series(float("nan"),index=out.index)), errors="coerce")
    out["entry_score"]=es
    status = pd.Series("not rankable", index=out.index)
    status[es < 0] = "price above ceiling"
    status[es >= 0] = "eligible"
    status[critical & es.notna()] = "data flag"
    status[blocked] = "not rankable"
    status[~out["class"].isin(SCORED)] = "not screened"
    out["status"] = status
    out["candidate"] = status == "eligible"
    return out
