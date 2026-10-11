"""Period-coherent extraction of provider statements. Missing is never zero."""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd

from .policy import get as policy

ROWS = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "gross": ["Gross Profit"],
    # Standardized Operating Income may exclude recurring operating charges
    # (e.g. merchant transaction/loan losses). Prefer the reported statement.
    "ebit": ["Total Operating Income As Reported", "Operating Income", "EBIT"],
    "net_income": ["Net Income Common Stockholders", "Diluted NI Availto Com Stockholders", "Net Income"],
    "pretax": ["Pretax Income"],
    "tax": ["Tax Provision"],
    "da": ["Reconciled Depreciation", "Depreciation And Amortization", "Depreciation Amortization Depletion"],
    "ocf": ["Operating Cash Flow"],
    "capex": ["Capital Expenditure"],
    "sbc": ["Stock Based Compensation"],
    "shares_avg": ["Diluted Average Shares", "Basic Average Shares"],
    "shares_out": ["Ordinary Shares Number", "Share Issued"],
    "debt": ["Total Debt"],
    "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
    "equity": ["Stockholders Equity"],
    "common_equity": ["Common Stock Equity"],
    "minority": ["Minority Interest"],
    "assets": ["Total Assets"],
    "goodwill": ["Goodwill"],
}
DEBT_LIKE_ROWS = ("Total Debt", "Long Term Debt", "Current Debt", "Long Term Debt And Capital Lease Obligation",
                  "Current Debt And Capital Lease Obligation", "Net Debt")

ROWS.update({
    "interest": ["Interest Expense", "Interest Expense Non Operating"],
    "working_capital": ["Change In Working Capital"],
    "rd": ["Research And Development"],
    "acquisitions": ["Purchase Of Business", "Net Business Purchase And Sale"],
    "consolidated_income": ["Net Income Including Noncontrolling Interests", "Net Income Continuous Operations"],
    "minority_income": ["Minority Interests"],
    "preferred": ["Preferred Stock Equity", "Preferred Stock"],
    "lease_liabilities": ["Capital Lease Obligations"],
    "investing_cashflow": ["Investing Cash Flow", "Cash Flow From Continuing Investing Activities"],
    "total_equity": ["Total Equity Gross Minority Interest"],
    "preferred_dividends": ["Preferred Stock Dividends"],
    "common_dividends": ["Common Stock Dividend Paid"],
    "net_common_issuance": ["Net Common Stock Issuance"],
    "common_issuance": ["Common Stock Issuance"],
    "common_repurchases": ["Common Stock Payments"],
    "discontinued_income": ["Net Income Discontinuous Operations"],
    "unusual": ["Total Unusual Items"],
    "unusual_tax": ["Tax Effect Of Unusual Items"],
    "eps_diluted": ["Diluted EPS"],
})


def period_dates(values) -> pd.DatetimeIndex:
    """Fiscal periods are calendar dates, not instants converted between zones."""
    dates=[]
    for value in values:
        if isinstance(value,(int,float,bool)):
            raise ValueError("statement period must have an explicit calendar date")
        stamp=pd.Timestamp(value)
        if pd.isna(stamp):raise ValueError("statement period is missing")
        dates.append(stamp.date())
    return pd.DatetimeIndex(dates)


def row(df: pd.DataFrame | None, key: str) -> pd.Series | None:
    """Resolve aliases per period, retaining the dates and unknown observations."""
    if df is None or df.empty:
        return None
    result = pd.Series(np.nan, index=period_dates(df.columns), dtype=float)
    for name in ROWS[key]:
        if name not in df.index:
            continue
        values = df.loc[name]
        if isinstance(values, pd.DataFrame):
            # Duplicate provider rows are ambiguous, not an invitation to pick one.
            if not all(values.iloc[0].equals(values.iloc[i]) for i in range(1,len(values))):
                continue
            values = values.iloc[0]
        values = pd.to_numeric(values, errors="coerce").replace([np.inf,-np.inf],np.nan)
        values.index = period_dates(values.index)
        result = result.fillna(values)
    if key == "debt":
        # Sum only a complete, nonoverlapping pair. Lease-inclusive aliases must
        # be paired with the same convention, never mixed with bare debt.
        for short,long in [("Current Debt And Capital Lease Obligation", "Long Term Debt And Capital Lease Obligation"),
                           ("Current Debt", "Long Term Debt")]:
            if short in df.index and long in df.index:
                summed = pd.to_numeric(df.loc[short],errors="coerce") + pd.to_numeric(df.loc[long],errors="coerce")
                if isinstance(summed,pd.Series):
                    summed.index=period_dates(summed.index)
                    result=result.fillna(summed)
    if key == "lease_liabilities":
        short,long="Current Capital Lease Obligation","Long Term Capital Lease Obligation"
        if short in df.index and long in df.index:
            summed=pd.to_numeric(df.loc[short],errors="coerce")+pd.to_numeric(df.loc[long],errors="coerce")
            if isinstance(summed,pd.Series):
                summed.index=period_dates(summed.index)
                result=result.fillna(summed)
    if key == "minority" and "Total Equity Gross Minority Interest" in df.index:
        common=row(df,"equity")
        if common is not None:
            total=pd.to_numeric(df.loc["Total Equity Gross Minority Interest"],errors="coerce")
            if isinstance(total,pd.Series):
                total.index=period_dates(total.index)
                result=result.fillna(total-common)
    if key == "preferred" and "Stockholders Equity" in df.index and "Common Stock Equity" in df.index:
        difference=pd.to_numeric(df.loc["Stockholders Equity"],errors="coerce")-pd.to_numeric(df.loc["Common Stock Equity"],errors="coerce")
        if isinstance(difference,pd.Series):
            difference.index=period_dates(difference.index)
            result=result.fillna(difference)
    if key in ("equity","common_equity"):
        other="Common Stock Equity" if key=="equity" else "Stockholders Equity"
        preferred=row(df,"preferred")
        if other in df.index and preferred is not None:
            other_values=pd.to_numeric(df.loc[other],errors="coerce")
            if isinstance(other_values,pd.Series):
                other_values.index=period_dates(other_values.index)
                result=result.fillna(other_values+preferred if key=="equity" else other_values-preferred)
    return result.sort_index(ascending=False) if result.notna().any() else None


def latest(df, key) -> float:
    """Value at this statement's selected date. Never backfill individual fields."""
    s = row(df,key)
    d = statement_date(df)
    return float(s.get(d,math.nan)) if s is not None and d is not None else math.nan


def latest_label(df, key) -> str | None:
    """Raw alias supporting a directly reported selected field."""
    if df is None or df.empty:
        return None
    for label in ROWS[key]:
        if label in df.index:
            single = df.loc[[label]].rename(index={label: ROWS[key][0]})
            if math.isfinite(latest(single, key)):
                return label
    return None


def trailing_checks(ttm, quarters, keys, rounding_unit=0.0) -> list[dict]:
    """Reconcile four consecutive quarters, never sum half-year observations.

    A fiscal quarter spans 12--14 weeks, including 52/53-week calendars. The
    reporting-rounding allowance must come from a sourced input: five rounded
    observations (four quarters plus TTM) permit at most 5 * unit / 2 error.
    Without that evidence, retain even small unexplained differences.
    """
    if quarters is None or quarters.empty or statement_date(ttm) is None:
        return []
    dates = sorted(set(period_dates(quarters.columns)), reverse=True)[:4]
    if len(dates) != 4 or dates[0] != statement_date(ttm):
        return []
    if any(not 84 <= (a-b).days <= 98 for a,b in zip(dates,dates[1:])):
        return []
    checks = []
    for key in keys:
        values = row(quarters, key)
        total = latest(ttm, key)
        if values is None or not math.isfinite(total):
            continue
        values = values.reindex(dates)
        if values.isna().any():
            continue
        summed = math.fsum(values)
        arithmetic_error = 5 * math.ulp(max(abs(total), *(abs(x) for x in values)))
        allowance = 2.5 * rounding_unit + arithmetic_error
        checks.append({"field": key, "period_end": dates[0].date().isoformat(),
                       "ttm": total, "quarter_sum": summed, "difference": total-summed,
                       "rounding_unit": rounding_unit, "allowance": allowance,
                       "status": "ties" if abs(total-summed) <= allowance else "unreconciled"})
    return checks




def annual_tieout(annual_df, quarters, keys) -> list[dict]:
    """Four consecutive quarters ending at the latest fiscal year end must add up to the audited annual figure.

    A material gap means restated and unrestated quarters (or different definitions) are mixed, so a TTM built
    from those quarters cannot be trusted. Only checked when such a quarter window exists."""
    a_date = statement_date(annual_df) if annual_df is not None and not annual_df.empty else None
    if a_date is None or quarters is None or quarters.empty:
        return []
    dates = [d for d in sorted(set(period_dates(quarters.columns)), reverse=True) if d <= a_date][:4]
    if len(dates) != 4 or abs((dates[0] - a_date).days) > 7:
        return []
    if any(not 84 <= (x - y).days <= 98 for x, y in zip(dates, dates[1:])):
        return []
    out = []
    for key in keys:
        values, total = row(quarters, key), row(annual_df, key)
        if values is None or total is None:
            continue
        values, annual_value = values.reindex(dates), total.get(a_date, math.nan)
        if values.isna().any() or not math.isfinite(annual_value):
            continue
        summed = math.fsum(values)
        material = abs(summed - annual_value) > policy().annual_tie_tolerance * max(abs(annual_value), abs(summed))
        out.append({"field": key, "fiscal_year_end": a_date.date().isoformat(), "annual": float(annual_value),
                    "quarter_sum": summed, "status": "unreconciled" if material else "ties"})
    return out


def filing_checks(selections: dict[str, "Selection"], evidence: list[dict]) -> list[dict]:
    """Compare sourced facts only with the same period, duration and currency.

    Facts never overwrite provider values. Minimum/maximum claims support facts
    such as disclosed borrowings without asserting that omitted leases are zero.
    Currency is checked on the enclosing issuer input by metrics.compute.
    """
    results = []
    for fact in evidence:
        selection = selections[fact["statement"]]
        period_type = ("instant" if fact["statement"] == "balance" else
                       "ttm" if selection.source.startswith("ttm_") else "quarter" if selection.source.startswith('quarterly_') else "annual")
        check = {**fact, "status": "not_applicable"}
        if selection.date is not None and str(fact["period_end"]) == selection.date.date().isoformat() and fact["period_type"] == period_type:
            actual = latest(selection.frame, fact["field"])
            expected = fact["value"]
            allowance = fact.get("rounding_unit", 0.0) / 2 + 2 * math.ulp(abs(expected))
            relation = fact.get("relation", "equal")
            ties = math.isfinite(actual) and (abs(actual-expected) <= allowance if relation == "equal" else
                    actual >= expected-allowance if relation == "minimum" else actual <= expected+allowance)
            check.update(provider_value=actual if math.isfinite(actual) else None, allowance=allowance,
                         status="ties" if ties else "contradiction")
            if fact.get("source_basis") != fact.get("provider_basis"):
                check["status"]="basis_unreconciled"
        results.append(check)
    return results


def clean_common_income(df) -> pd.Series:
    """Common income from continuing operations before after-tax unusual items, per period.

    "Net Income Common Stockholders" includes discontinued operations and one-off items (divestiture gains,
    warrant revaluations, securities sales). Those are real but cannot be extrapolated as earning power, so
    margin evidence uses this series. Missing adjustment rows count as zero; a missing income row stays missing.
    """
    common = row(df, "net_income")
    if common is None:
        return pd.Series(dtype=float)
    def part(key):
        s = row(df, key)
        return s.reindex(common.index).fillna(0.0) if s is not None else 0.0
    return (common - part("discontinued_income") - (part("unusual") - part("unusual_tax"))).sort_index()


def clean_latest(df) -> float:
    """clean_common_income at this statement's selected date."""
    s, d = clean_common_income(df), statement_date(df)
    return float(s.get(d, math.nan)) if d is not None and not s.empty else math.nan


def annual(df,key) -> pd.Series:
    s=row(df,key)
    return s.sort_index() if s is not None else pd.Series(dtype=float)


def finite_series(s: pd.Series) -> pd.Series:
    return s.replace([np.inf,-np.inf],np.nan).dropna()


def statement_date(df) -> pd.Timestamp | None:
    return max(period_dates(df.columns)) if df is not None and not df.empty else None


@dataclass(frozen=True)
class Selection:
    frame: pd.DataFrame
    source: str
    date: pd.Timestamp | None
    newest: pd.Timestamp | None
    missing: tuple[str,...]

    @property
    def backdated(self):
        return self.date is not None and self.newest is not None and self.date < self.newest


def select(sources: list[tuple[str,pd.DataFrame | None]], required: tuple[str,...], *, instant:bool=False) -> Selection:
    """Newest complete column from preferred source, otherwise an explicit gap.

    Source ordering distinguishes trailing and annual duration. A complete older
    period is allowed but retains its real date and a backdating diagnostic.
    """
    if instant:
        options=[select([(name,df)],required) for name,df in sources]
        complete=[o for o in options if o.date is not None and not o.missing]
        if complete:
            chosen=max(complete,key=lambda o:o.date)
            newest=max(o.newest for o in options if o.newest is not None)
            return Selection(chosen.frame,chosen.source,chosen.date,newest,())
    fallback=None
    for name,df in sources:
        if df is None or df.empty:
            continue
        dates=sorted(period_dates(df.columns),reverse=True)
        normalized=df.copy()
        normalized.columns=period_dates(normalized.columns)
        fields={key:row(normalized,key) for key in required}
        for date in dates:
            missing=tuple(key for key,s in fields.items() if s is None or pd.isna(s.get(date)))
            choice=Selection(normalized.loc[:,[date]],name,date,dates[0],missing)
            if fallback is None:
                fallback=choice
            if not missing:
                return choice
    return fallback or Selection(pd.DataFrame(),"missing",None,None,required)


def period_ledger(selections: dict[str,Selection]) -> dict:
    return {k:{"source":v.source,"period_end":v.date.date().isoformat() if v.date is not None else None,
               "newest_provider_period":v.newest.date().isoformat() if v.newest is not None else None,
               "missing":list(v.missing)} for k,v in selections.items()}


def invested_capital(df) -> pd.Series:
    """Capital supporting consolidated operating earnings, including NCI.

    Parent equity includes preferred capital. Missing ownership components are
    not zero. The result retains each balance-sheet observation's own date.
    """
    fields=[row(df,k) for k in ("equity","minority","debt","cash")]
    if any(s is None for s in fields):return pd.Series(dtype=float)
    return finite_series(fields[0]+fields[1]+fields[2]-fields[3]).sort_index()


def annual_average_capital(series:pd.Series,end:pd.Timestamp) -> float:
    """Match a calendar/52-/53-week annual window; never average unrelated years."""
    window=annual_capital_window(series,end)
    return float((series[window[0]]+series[window[1]])/2) if window else math.nan


def annual_capital_window(series:pd.Series,end:pd.Timestamp) -> tuple[pd.Timestamp,pd.Timestamp] | None:
    if end not in series.index or series[end]<=0:return None
    starts=[d for d in series.index if (end-d).days in (364,365,366,371) and series[d]>0]
    return (max(starts),end) if starts else None
