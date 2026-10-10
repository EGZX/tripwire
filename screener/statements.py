"""Period-coherent extraction of provider statements. Missing is never zero."""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd

ROWS = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "gross": ["Gross Profit"],
    "ebit": ["Operating Income", "EBIT"],
    "net_income": ["Net Income Common Stockholders", "Net Income"],
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

