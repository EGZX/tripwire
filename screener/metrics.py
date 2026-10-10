"""Evidence-aware stock screening: explicit sector models or a labeled accounting proxy.

All amounts use one reporting currency and ownership scope. Missing evidence
blocks unsupported inference; numerical success is distinct from applicability.
"""
from __future__ import annotations

import math
import json
from datetime import date

import numpy as np
import pandas as pd

from . import valuation as V
from .fetch import FX, major_currency
from . import statements as S, market, models
from .inputs import validate_issuer
from .checks import policy_rates

FINANCIAL_INDUSTRIES = ("Banks", "Insurance - ", "Asset Management", "Capital Markets", "Mortgage Finance",
                        "Credit Services", "Financial Conglomerates", "Shell Companies")
CYCLICAL_SECTORS = {"Basic Materials", "Energy"}
CYCLICAL_INDUSTRIES = {"Semiconductors", "Semiconductor Equipment & Materials", "Auto Manufacturers",
                       "Farm & Heavy Construction Machinery", "Airlines", "Marine Shipping", "Steel",
                       "Auto Parts", "Trucking", "Railroads", "Building Materials", "Residential Construction",
                       "Lumber & Wood Production", "Agricultural Inputs", "Recreational Vehicles"}

from .statements import ROWS, row as _row, latest as _latest, annual as _annual, finite_series as _finite, statement_date as _date


def _isnum(x) -> bool:
    try:
        return x is not None and not isinstance(x,bool) and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _cagr(series: pd.Series, max_years: int = 3) -> float:
    s = series.dropna()
    if len(s) < 2:
        return math.nan
    s = s.iloc[-(max_years + 1):]
    first, last = float(s.iloc[0]), float(s.iloc[-1])
    years = (s.index[-1] - s.index[0]).days / 365.25
    if first <= 0 or last <= 0 or years < 0.9:
        return math.nan
    return (last / first) ** (1 / years) - 1


UNIT_TOLERANCE = 0.15  # holdings-file euro price vs Yahoo's price at today's FX; beyond this the units differ (GDR ratio)


def position_value(price: float, quote_ccy: str, shares: float, fx) -> float:
    """Euro value of a holding at today's price and FX (used when the holdings file gives no value_eur)."""
    if not (_isnum(price) and price > 0 and _isnum(shares) and shares > 0 and quote_ccy):
        return math.nan
    rate = fx.rate(quote_ccy, "EUR")
    return shares * price * rate if rate else math.nan


def cost_basis(price: float, ceiling_px: float, quote_ccy: str, shares: float, cost_eur: float,
               value_eur: float, fx) -> dict:
    """Your average buy price against today's max entry price, in the quote currency at today's FX.

    cost_px = euro cost per share converted at today's rate, so the comparison is "is the business worth more today,
    in euro, than the euro you paid" (currency moves since purchase included). cost_score uses the entry-score formula.
    Dropped with a flag when the holdings file's euro price per share and Yahoo's price disagree beyond UNIT_TOLERANCE."""
    if not (_isnum(cost_eur) and cost_eur > 0 and _isnum(shares) and shares > 0 and _isnum(price) and price > 0):
        return {}
    rate = fx.rate(quote_ccy, "EUR") if quote_ccy else None
    if not rate:
        return {"cost_flag": f"no FX rate {quote_ccy}->EUR"}
    if _isnum(value_eur) and value_eur > 0 and abs((value_eur / shares) / (price * rate) - 1) > UNIT_TOLERANCE:
        return {"cost_flag": "holdings-file and Yahoo prices disagree (units or listing); cost score skipped"}
    cost_px = cost_eur / shares / rate
    return {"cost_px": cost_px, "cost_score": entry_score(cost_px, ceiling_px) if _isnum(ceiling_px) else math.nan}


def entry_score(mcap: float, ceiling: float) -> float:
    """100 x (1 - price / ceiling). NaN when there is no positive ceiling."""
    if not _isnum(ceiling) or ceiling <= 0 or not _isnum(mcap) or mcap <= 0 or math.isinf(ceiling):
        return math.nan
    return max(-100.0, 100.0 * (1.0 - mcap / ceiling))  # floored: beyond 2x the ceiling the exact gap says nothing


def classify(info: dict, override: str | None) -> str:
    if override:
        return override
    if (info.get("quoteType") or "EQUITY") != "EQUITY":
        return "fund"
    industry, sector = info.get("industry") or "", info.get("sector") or ""
    if industry.startswith(FINANCIAL_INDUSTRIES) and industry != "Insurance Brokers":
        return "financial"
    if sector in CYCLICAL_SECTORS or industry in CYCLICAL_INDUSTRIES:
        return "cyclical"
    return "operating"


def model_family(info:dict, cls:str, override:str|None) -> str:
    industry=info.get("industry") or ""
    if cls in ("holdco","fund"):return cls
    if industry=="REIT - Mortgage":return "mortgage_reit"
    if industry.startswith("REIT"):return "reit"
    if (info.get("sector") or "")=="Utilities":return "regulated"
    if cls=="financial":
        if industry.startswith("Banks"):return "bank"
        if industry.startswith("Insurance"):return "insurer"
        if override=="financial":return "hybrid"
        if industry in ("Credit Services","Mortgage Finance"):return "bank"
        return "financial_other"
    if cls=="cyclical":return "cyclical"
    return "operating"


def _finish_values(m, reasonable, stress, mcap, flags, policy_ceiling=None):
    ceiling=reasonable if policy_ceiling is None else policy_ceiling
    m.update(value_reasonable=reasonable,value_adverse=stress,entry_ceiling=ceiling)
    if not _isnum(ceiling) or ceiling<=0:
        m["not_rankable"]="nonpositive or invalid equity ceiling"
    else:
        m["entry_score_raw"]=100*(1-mcap/ceiling)
        m["entry_score"]=entry_score(mcap,ceiling)
        m["adverse_score"]=-100.0 if _isnum(stress) and stress<=0 else entry_score(mcap,stress)
        m["ceiling_px"]=m["price"]*ceiling/mcap
        m["reasonable_px"]=m["price"]*reasonable/mcap
        m["adverse_px"]=m["price"]*max(0.0,stress)/mcap if _isnum(stress) else math.nan
        if _isnum(stress) and stress>reasonable:
            flags.append("stress value exceeds base: scenario is not a downside bound")
    m["flags"]="; ".join(dict.fromkeys(flags))
    return m


def _explicit_model(m,supplied,vcfg,bridge,mcap,flags,blockers):
    name=supplied["model"]
    permitted={"bank":{"bank_capital"},"insurer":{"insurer_capital"},"reit":{"reit_affo","asset_nav"},"mortgage_reit":{"asset_nav"},
               "regulated":{"regulated_rate_base","operating_forecast"},"holdco":{"asset_nav"},
               "hybrid":{"asset_nav"},"financial_other":{"financial_capital","operating_forecast","asset_nav"},
               "operating":{"operating_forecast","finite_project","asset_nav"},
               "cyclical":{"operating_forecast","finite_project","asset_nav"}}
    if m["model_family"] in permitted and name not in permitted[m["model_family"]]:
        blockers.append("model does not match issuer economics: "+m["model_family"])
    outputs=[];policy_ceiling=None;default_gt=m["terminal_growth"]
    for case in ("base","stress"):
        try:
            params=supplied[case]
            rate=params.get("discount_rate",supplied.get("discount_rate",m["hurdle"]))
            gt=params.get("terminal_growth",default_gt)
            projection=models.evaluate(name,params,vcfg["years"],rate,gt)
            if isinstance(projection,models.Projection):
                basis=supplied.get("discount_rate_basis","policy")
                if (projection.claim=="firm" and basis=="cost_of_equity") or (projection.claim=="equity" and basis in ("wacc","unlevered")):
                    raise V.InvalidModel("discount-rate basis does not match cash-flow claim")
                if projection.claim=="firm" and not _isnum(bridge):
                    raise V.InvalidModel("firm model needs a complete equity bridge")
                value=projection.value-(bridge if projection.claim=="firm" else 0)
                m[f"{case}_cashflows"]=json.dumps(projection.cashflows)
                if case=="stress":
                    m["floor_rate"]=rate
                    m["stress_terminal_growth"]=gt
                    m["adverse_growth"]=params.get("growth",math.nan)
                if case=="base":
                    m["terminal_value_fraction"]=projection.terminal_pv/projection.value if projection.value else math.nan
                    m["implied_return_claim"]=projection.claim
                    m["discount_rate"]=rate
                    m["terminal_growth"]=gt
                    m["est_growth"]=params.get("growth",math.nan)
                    policy_value=projection.opening_distribution+V.discount(list(projection.cashflows),projection.terminal_cashflow,m["hurdle"],gt)-(bridge if projection.claim=="firm" else 0)
                    m["value_at_policy_hurdle"]=policy_value
                    policy_ceiling=min(value,policy_value) # same base flows must satisfy BOTH value and purchase constraints
                    m["decision_rate"]=max(rate,m["hurdle"]) if min(projection.cashflows)>=0 and projection.terminal_cashflow>=0 else math.nan
                    m["valuation_basis"]=("source-assumption intrinsic value; separate purchase-policy ceiling" if basis!="policy" else "purchase policy; not fair value")
            else:
                value=projection
                m["floor_rate"]=math.nan
                m["valuation_basis"]="attributable asset value; entry ceiling at NAV, no return forecast"
            outputs.append(value)
        except (KeyError,TypeError,ValueError,OverflowError) as exc:
            blockers.append(f"{case} {name}: {exc}")
    m.update(model=name,model_label=models.LABELS[name],model_quality="declared issuer assumptions",assumptions_source=supplied.get("source",""),
             assumptions_as_of=str(supplied.get("as_of","")),implied_growth_status="not applicable to driver model",
             implied_return_status="not computed for explicit driver model")
    if m["class"] in ("holdco","preprofit"):m["class"]="asset"
    if blockers:
        m["not_rankable"]="; ".join(dict.fromkeys(blockers));m["flags"]="; ".join(flags);return m
    return _finish_values(m,*outputs,mcap,flags,policy_ceiling=policy_ceiling)


def compute(data: dict, price_now: float | None, fx: FX, cfg: dict, class_override: str | None,
            *, valuation_date:date|None=None) -> dict:
    vcfg = cfg["valuation"]
    effective_date=valuation_date or date.today()
    info = data.get("info") or {}
    if isinstance(price_now,market.Observation) and price_now.info is not None:
        # Only market/security metadata is refreshed; accounting periods remain
        # those of the immutable fundamental snapshot.
        quote_fields=("regularMarketPrice","currentPrice","previousClose","regularMarketTime","marketCap",
                      "sharesOutstanding","impliedSharesOutstanding","currency")
        info={**info,**{k:price_now.info.get(k) for k in quote_fields}}
    flags: list[str] = []
    blockers: list[str] = []  # reasons the name cannot get an entry ceiling
    supplied = cfg.get("issuer_inputs", {}).get(data["symbol"], {})
    if supplied:
        blockers.extend(validate_issuer(data["symbol"], supplied,effective_date))
    m: dict = {"symbol": data["symbol"], "yahoo_name": info.get("longName") or info.get("shortName") or "",
               "sector": info.get("sector") or "", "industry": info.get("industry") or "",
               "country": info.get("country") or "", "not_rankable": ""}
    if blockers:
        return {**m,"class":classify(info,class_override),"not_rankable":"; ".join(blockers),"flags":"invalid issuer inputs"}
    cls = classify(info, class_override)
    family = supplied["model_family"] if "model_family" in supplied else model_family(info, cls, class_override)
    if "model_family" in supplied:
        cls="financial" if family in ("bank","insurer","hybrid","financial_other") else "holdco" if family=="holdco" else "cyclical" if family=="cyclical" else "operating"
    m["class"] = cls
    m.update(model_family=family, model="funded_earnings", model_quality="accounting proxy",
             model_label=models.LABELS["funded_earnings"],
             valuation_basis="purchase policy; not fair value", implied_return_claim="equity" if cls=="financial" else "firm")
    m["classification_basis"]="sourced family declaration" if "model_family" in supplied else "explicit override" if class_override else "provider industry"
    if cls!="fund" and not info.get("industry") and not class_override and "model_family" not in supplied:
        blockers.append("business classification unavailable: a sourced model_family is required")
    if supplied:
        m.update(assumptions_source=supplied["source"],assumptions_as_of=str(supplied["as_of"]))
    if cls == "fund":
        m["flags"] = "fund/ETF - not screened"
        return m

    # ---------- market data (quote currency -> financial currency)
    q_ccy, q_div = major_currency(info.get("currency"))
    f_ccy = major_currency(info.get("financialCurrency") or supplied.get("currency") or info.get("currency"))[0]
    if not info.get("financialCurrency") and not supplied.get("currency"):
        blockers.append("reporting currency unavailable; quote currency does not identify statement units")
    info_price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
    mcap_q, cap_flags, cap_errors = market.capitalization(info,q_div,supplied)
    flags.extend(cap_flags);blockers.extend(cap_errors)
    if isinstance(price_now, market.Observation):
        m["price_as_of"] = price_now.as_of
        price_now = price_now.value
    else:
        m["price_as_of"] = market.quote_time(info) if price_now is None else None
    if price_now is not None and (not _isnum(price_now) or price_now <= 0):
        blockers.append("invalid current quote")
    if price_now and info_price and _isnum(mcap_q):
        # Fresh fetch provides a common corporate-action basis. A stale fallback
        # is never silently mixed with a new quote after a potential split.
        if data.get("cache_state") == "stale_fallback":
            blockers.append("stale cache: share/corporate-action basis unverified")
        mcap_q = mcap_q * price_now / info_price
    fx_q_to_f = fx.rate(q_ccy, f_ccy) if q_ccy and f_ccy else None
    if q_ccy != f_ccy:
        flags.append(f"fx {q_ccy}->{f_ccy}")
    if not _isnum(mcap_q) or mcap_q <= 0 or not _isnum(fx_q_to_f) or fx_q_to_f <= 0:
        m["flags"] = "no market cap or FX"
        m["not_rankable"] = "no market cap or FX"
        return m
    mcap = mcap_q * fx_q_to_f
    m["currency"] = f_ccy
    m["mcap_fc"] = mcap
    eur = fx.rate(f_ccy, "EUR")
    m["mcap_eur_bn"] = mcap * eur / 1e9 if eur else math.nan
    m["price"] = (price_now or info_price or math.nan) / q_div
    m["quote_ccy"] = q_ccy
    if supplied and supplied.get("currency") != f_ccy:
        blockers.append("issuer inputs do not match reporting currency")
    if not m["price_as_of"]:
        flags.append("quote date unavailable")
    if data.get("cache_state") == "stale_fallback":
        flags.append("stale cache fallback")
    if data.get("errors"):
        flags.append("provider retrieval errors: " + " | ".join(data["errors"]))
    if data.get("filing_error"):
        flags.append(data["filing_error"])
    m["filing_source"]=data.get("filing_source","")
    m["filing_snapshot"]=data.get("filing_snapshot","")
    m["filing_sha256"]=data.get("filing_sha256","")
    m["fetched_at"] = str(data.get("fetched_at", ""))
    m["source_snapshot"]=data.get("source_snapshot","")
    m["source_sha256"]=data.get("source_sha256","")
    m["quote_metadata"]=json.dumps({k:info.get(k) for k in ("marketCap","sharesOutstanding","impliedSharesOutstanding","regularMarketPrice","regularMarketTime","currency")},sort_keys=True)
    if data.get("quote_refresh_failed"):
        flags.append("quote refresh failed")
    if hasattr(fx,"observations"):
        m["fx_observations"] = json.dumps(fx.observations(),sort_keys=True)

    # ---------- required return and terminal growth for the reporting currency
    caps: list[str] = []  # parameters that bound this name's result (visibility, not a gate)
    all_rates = cfg.get("rates") or {}
    rates = all_rates.get(f_ccy)
    base_rates = all_rates.get(vcfg.get("base_currency", "EUR"))
    if rates is None:
        blockers.append(f"no rates for {f_ccy} in config [rates]")
        hurdle = floor = gT = math.nan
    else:
        gT = rates["g"]
        # Reporting currency is not country exposure. Domicile is an explicitly
        # labeled screen proxy only; researched weighted exposures take priority.
        country = (cfg.get("country_risk") or {}).get(m["country"])
        if "country_risk_premium" in supplied:
            crp=supplied["country_risk_premium"]
            m["country_risk_basis"]="issuer assumption"
        elif supplied.get("geographic_exposures"):
            crp=0.0
            for location,weight in supplied["geographic_exposures"].items():
                entry=(cfg.get("country_risk") or {}).get(location)
                if entry is None:blockers.append("no country-risk assumption for exposure "+location)
                else:crp+=weight*entry["premium"]
            m["country_risk_basis"]="source-labeled geographic exposure weights"
        elif isinstance(country,dict):
            crp=country["premium"]
            m["country_risk_basis"]="provider-country proxy: " + m["country"]
            flags.append("country exposure uses provider-country proxy")
        elif m["country"]:
            crp=0.0
            blockers.append("no country-risk assumption for " + m["country"])
        else:
            crp=0.0
            flags.append("country exposure unavailable")
        m["country_risk_premium"] = crp
        calculated=policy_rates(rates,vcfg,crp)
        hurdle=calculated["hurdle"]
        if calculated["min_hurdle_binds"]:
            caps.append("min hurdle")
        floor=calculated["floor"]
    m.update(hurdle=hurdle, floor_rate=floor, terminal_growth=gT)

    # ---------- statements: one source per statement, so fields within a statement share a period
    ti, tc = data.get("ttm_income_stmt"), data.get("ttm_cashflow")
    ai, ac, ab = data.get("income_stmt"), data.get("cashflow"), data.get("balance_sheet")
    qb = data.get("quarterly_balance_sheet")
    # A provider aggregate may confirm an omitted debt row, but only for its
    # explicit statement period. It is never propagated into earlier years.
    if _isnum(info.get("totalDebt")) and info.get("mostRecentQuarter"):
        try:
            debt_date=pd.Timestamp(info["mostRecentQuarter"],unit="s").normalize()
            if qb is not None and debt_date in qb.columns and not _isnum(_latest(qb,"debt")):
                qb=qb.copy()
                if "Total Debt" not in qb.index:qb.loc["Total Debt"]=math.nan
                qb.loc["Total Debt",debt_date]=info["totalDebt"]
                flags.append("debt reconciled from provider aggregate at matching period")
        except (ValueError,TypeError,OverflowError):
            pass
    income_required = ("net_income",) if cls in ("financial", "holdco") else ("revenue", "ebit")
    inc_sel = S.select([("ttm_income_stmt",ti),("income_stmt",ai)], income_required)
    balance_required = ("common_equity",) if cls=="financial" else (("debt","cash") if "marginal_return" in supplied else ("equity","debt","cash"))
    supplied_balance=None
    facts={ROWS[k][0]:supplied[k] for k in ("equity","common_equity","debt","cash","minority","preferred","goodwill") if k in supplied}
    if facts:
        supplied_balance=pd.DataFrame({pd.Timestamp(supplied["period_end"]):facts})
    balance_sources=[("issuer facts",supplied_balance)]
    for name,frame in (("SEC companyfacts",data.get("filing_balance_sheet")),("quarterly_balance_sheet",qb),("balance_sheet",ab)):
        if facts and frame is not None and pd.Timestamp(supplied["period_end"]) in frame.columns:
            frame=frame.copy()
            for label,value in facts.items():
                if label not in frame.index:frame.loc[label]=math.nan
                frame.loc[label,pd.Timestamp(supplied["period_end"])]=value
            name+=" + sourced issuer facts at same period"
        balance_sources.append((name,frame))
    bal_sel = S.select(balance_sources,balance_required,instant=True)
    cf_sel = S.select([("ttm_cashflow",tc),("cashflow",ac)],("ocf","capex"))
    inc,bal,cfs = inc_sel.frame,bal_sel.frame,cf_sel.frame
    selections={"income":inc_sel,"balance":bal_sel,"cashflow":cf_sel}
    m["statement_periods"] = json.dumps(S.period_ledger(selections),sort_keys=True)
    m["operating_income_source"] = S.latest_label(inc,"ebit") or "unavailable"
    reconciliation=[]
    for name,selection,quarterly,keys in (
        ("income",inc_sel,data.get("quarterly_income_stmt"),("net_income",) if cls=="financial" else ("revenue","ebit","pretax","tax")),
        ("cashflow",cf_sel,data.get("quarterly_cashflow"),("ocf","capex"))):
        rounding=supplied.get("statement_rounding_unit",0.0) if selection.date is not None and str(supplied.get("period_end"))==selection.date.date().isoformat() else 0.0
        if selection.source.startswith("ttm_"):
            reconciliation.extend({"statement":name,**c} for c in S.trailing_checks(selection.frame,quarterly,keys,rounding))
    m["ttm_reconciliation"] = json.dumps(reconciliation,sort_keys=True)
    if not supplied.get("model") and any(c["status"]=="unreconciled" for c in reconciliation):
        flags.append("TTM/quarterly inputs unreconciled: rounding or accounting basis needs verification")
    checks=S.filing_checks(selections,supplied.get("filing_checks",[]))
    m["filing_reconciliation"]=json.dumps(checks,sort_keys=True)
    for check in checks:
        if check["status"]=="contradiction":
            blockers.append(f"filing contradiction: {check['statement']} {check['field']} at {check['period_end']}")
        elif check["status"]=="basis_unreconciled":
            blockers.append(f"filing basis unresolved: {check['statement']} {check['field']} at {check['period_end']}")
    for name,selection in (("income",inc_sel),("balance",bal_sel),("cashflow",cf_sel)):
        if selection.backdated:
            flags.append(f"{name}: coherent older period {selection.date.date()}")
    if inc_sel.source == "income_stmt":
        flags.append("income: annual not TTM")
    if cf_sel.source == "cashflow":
        flags.append("cash flow: annual not TTM")

    rev, ebit, ni, gross = (_latest(inc, k) for k in ("revenue", "ebit", "net_income", "gross"))
    pretax, tax = _latest(inc, "pretax"), _latest(inc, "tax")
    ocf, capex, da, sbc = (_latest(cfs, k) for k in ("ocf", "capex", "da", "sbc"))
    if math.isnan(sbc):
        if cls not in ("financial", "holdco"):
            flags.append("no SBC data (FCF not SBC-adjusted)")
        sbc = 0.0
    tax_rate = supplied.get("tax_rate", tax/pretax if _isnum(pretax) and pretax>0 and _isnum(tax) else math.nan)
    if not _isnum(tax_rate) or not 0<=tax_rate<=1:
        if cls not in ("financial","holdco") and not supplied.get("model"):
            blockers.append("no valid normalized tax rate")
        tax_rate=math.nan

    debt, cash = _latest(bal, "debt"), _latest(bal, "cash")
    equity, minority, assets = _latest(bal, "common_equity" if cls=="financial" else "equity"), _latest(bal, "minority"), _latest(bal, "assets")
    amounts={"debt":debt,"cash":cash,"equity":equity,"minority":minority,"preferred":_latest(bal,"preferred")}
    if facts and (bal_sel.date is None or str(supplied.get("period_end"))!=bal_sel.date.date().isoformat()):
        blockers.append("sourced balance facts differ from selected balance period")
    debt,cash,equity,minority,preferred=(amounts[k] for k in ("debt","cash","equity","minority","preferred"))
    # Absence of minority capital is exposed as a bridge limitation; supplied
    # sector models must provide all noncommon claims explicitly.
    if not _isnum(minority):
        if cls != "financial" and not supplied.get("model"):
            blockers.append("no minority interest reconciliation")
    if not _isnum(preferred) and cls!="financial" and not supplied.get("model"):
        blockers.append("no preferred-equity reconciliation")
    other_claims=supplied.get("other_claims",0.0)
    m["equity_bridge_scope"]="debt/cash/minority/preferred; other claims " + ("supplied" if "other_claims" in supplied else "not independently reviewed")
    if math.isnan(debt):
        if cls != "financial" and not supplied.get("model"):
            blockers.append("no debt figure")
    if math.isnan(cash) and cls != "financial" and not supplied.get("model"):
        blockers.append("no cash figure")
    if not _isnum(equity) and not supplied.get("model") and "marginal_return" not in supplied:
        blockers.append("no book equity figure")
    firm_bridge_needed=supplied.get("model") in ("operating_forecast","regulated_rate_base","finite_project") or (not supplied.get("model") and cls not in ("financial","holdco"))
    lease_evidence=[]
    for name,frame in ((bal_sel.source,bal),("quarterly_balance_sheet",qb),("balance_sheet",ab)):
        values=S.row(frame,"lease_liabilities")
        value=values.get(bal_sel.date,math.nan) if values is not None else math.nan
        if _isnum(value):lease_evidence.append({"source":name,"value":float(value)})
    lease_values={v["value"] for v in lease_evidence}
    m["lease_liabilities_fc"]=next(iter(lease_values)) if len(lease_values)==1 else math.nan
    m["lease_evidence"]=json.dumps(lease_evidence,sort_keys=True)
    lease_reconciled=(supplied.get("lease_cashflow_reconciled",False) and bal_sel.date is not None and inc_sel.date is not None
                      and str(supplied.get("period_end"))==bal_sel.date.date().isoformat()==inc_sel.date.date().isoformat())
    if firm_bridge_needed and any(v>0 for v in lease_values) and not lease_reconciled:
        flags.append("lease/cash-flow basis unreconciled: provider debt may include already-expensed operating leases")
    if firm_bridge_needed and any(_isnum(x) and x<0 for x in (debt,cash,minority,preferred,other_claims)):
        blockers.append("negative cash/debt/noncommon book claim requires reconciliation")
    scope_issue=market.ownership_check(info,ni,_latest(inc,"consolidated_income"),supplied)
    if scope_issue:
        blockers.append(scope_issue)
    net_debt = debt - cash  # NaN if either is missing
    ev = mcap + net_debt + minority + preferred + other_claims
    m.update(revenue=rev, ebit=ebit, net_income=ni, ev_fc=ev, net_debt_fc=net_debt)

    today = pd.Timestamp(effective_date)
    m["valuation_date"]=today.date().isoformat()
    if m.get("price_as_of"):
        try:
            quote_day=pd.Timestamp(m["price_as_of"])
            quote_day=(quote_day.tz_convert("UTC").tz_localize(None) if quote_day.tzinfo else quote_day).normalize()
            age=(today-quote_day).days
            if age<0 or age>vcfg.get("quote_max_age_days",7):flags.append("stale or future quote")
        except (TypeError,ValueError):flags.append("quote date unavailable")
    if hasattr(fx,"observations"):
        observations=fx.observations()
        for a,b in ((q_ccy,f_ccy),(f_ccy,"EUR")):
            if a==b:continue
            try:
                observed=pd.Timestamp(observations[f"{a}/{b}"]["as_of"])
                if pd.isna(observed):raise ValueError("missing FX date")
                observed=(observed.tz_convert("UTC").tz_localize(None) if observed.tzinfo else observed).normalize()
                age=(today-observed).days
                if age<0 or age>vcfg.get("quote_max_age_days",7):flags.append("stale or future FX")
            except (KeyError,TypeError,ValueError):flags.append("FX date unavailable")
    if rates is not None:
        try:
            age=(today-pd.Timestamp(rates["as_of"])).days
            if age<0 or age>vcfg.get("rates_max_age_days",90):flags.append("stale or future rates")
        except (KeyError,TypeError,ValueError):flags.append("rate date unavailable")
    core = [d for d in (_date(inc), _date(bal)) if d is not None]  # statements the ceiling depends on
    if any(d>today for d in core):blockers.append("financial statement period is after valuation date")
    periods = core + ([_date(cfs)] if _date(cfs) is not None else [])
    if periods:
        m["data_as_of"] = min(periods).date().isoformat()
    if core and (today - min(core)).days > vcfg["stale_after_days"]:
        flags.append("stale financials")
    elif _date(cfs) is not None and (today - _date(cfs)).days > vcfg["stale_after_days"]:
        flags.append("cash flow stale")
    ttm_date = _date(inc)
    if supplied:
        try:
            if (today-pd.Timestamp(supplied["as_of"])).days>vcfg["stale_after_days"]:
                blockers.append("stale issuer assumptions")
        except (KeyError,ValueError,TypeError):
            blockers.append("invalid issuer assumption date")

    # ---------- annual history
    a_rev, a_ebit, a_ni = _annual(ai, "revenue"), _annual(ai, "ebit"), _annual(ai, "net_income")
    a_eq = _annual(ab, "common_equity" if cls=="financial" else "equity")
    if len(a_rev) < 3:
        flags.append("short history")

    fcf = ocf + capex if not (math.isnan(ocf) or math.isnan(capex)) else math.nan
    fcf_adj = fcf - sbc if not math.isnan(fcf) else math.nan
    working_capital=_latest(cfs,"working_capital")
    acquisitions=_latest(cfs,"acquisitions")
    m["observed_net_investment"]=-capex-da-working_capital if all(_isnum(x) for x in (capex,da,working_capital)) else math.nan
    m["acquisition_cashflow"]=acquisitions
    m["acquisition_cashflow_basis"]="provider acquisition category; may include other investments, not independently normalized"
    m["rd_expense"]=_latest(inc,"rd")

    # growth inputs
    rev_cagr = _cagr(a_rev)
    if not math.isnan(rev) and len(a_rev) >= 1 and ttm_date is not None and ttm_date > a_rev.index[-1]:
        rev_cagr = _cagr(pd.concat([a_rev, pd.Series([rev], index=[ttm_date])]))
    analyst = math.nan
    re_ = data.get("revenue_estimate")
    if isinstance(re_, pd.DataFrame) and "growth" in re_.columns:
        for period in ("+1y", "0y"):
            if period in re_.index and pd.notna(re_.loc[period, "growth"]):
                analyst = float(re_.loc[period, "growth"])
                break
    if any(math.isinf(g) for g in (rev_cagr,analyst)):
        flags.append("nonfinite provider growth input")
        if not supplied.get("model"):blockers.append("nonfinite growth input cannot be capped into a forecast")
    growth_inputs = [g for g in (rev_cagr, analyst) if _isnum(g)]
    g_raw = float(np.mean(growth_inputs)) if growth_inputs else math.nan
    g_est = float(np.clip(g_raw, vcfg["growth_floor"], vcfg["growth_cap"])) if growth_inputs else math.nan
    if g_raw == g_raw and g_raw > vcfg["growth_cap"]:
        caps.append("growth cap")
    elif g_raw == g_raw and g_raw < vcfg["growth_floor"]:
        caps.append("growth outside screening domain")
        if not supplied.get("model"):
            blockers.append("growth below screening domain: explicit recovery or finite-life model required")
    m.update(rev_cagr_3y=rev_cagr, analyst_growth=analyst,growth_raw=g_raw)
    if isinstance(re_,pd.DataFrame):
        for period in ("+1y","0y"):
            if period in re_.index:
                for col in ("numberOfAnalysts","avg","low","high","yearAgoRevenue","currency"):
                    if col in re_.columns:m["analyst_"+col]=re_.loc[period,col]
                m["analyst_period"]=period
                break

    sh = _annual(ai, "shares_avg")
    m["share_cagr"] = _cagr(sh) if len(sh) >= 2 else math.nan
    m["evs"] = ev / rev if rev and rev > 0 and not math.isnan(ev) else math.nan
    m["evs_vs_hist"] = (_evs_vs_history(data, mcap, price_now or info_price, a_rev, ab, ai, m["evs"])
                        if cls in ("operating", "cyclical") else math.nan)
    if supplied.get("model"):
        return _explicit_model(m,supplied,vcfg,net_debt+minority+preferred+other_claims,mcap,flags,blockers)
    if family in ("bank","insurer","reit","mortgage_reit","regulated","hybrid","financial_other"):
        blockers.append(f"{family} requires explicit sector inputs; generic earnings model is not applicable")
        required={"bank":"bank_capital","insurer":"insurer_capital","reit":"reit_affo",
                  "mortgage_reit":"asset_nav","regulated":"regulated_rate_base","hybrid":"asset_nav","financial_other":"financial_capital"}[family]
        m["model"]=required
        m["model_label"]=models.LABELS[required]+" (inputs needed)"

    # ---------- base earnings and return on capital per model family
    years, R_cap = vcfg["years"], vcfg["return_cap"]
    base = base_adv = R0 = R_avg = math.nan
    if cls in ("financial", "holdco"):
        fin_type="platform" if family=="hybrid" else family if family in ("bank","insurer") else "other"
        m["fin_type"] = fin_type
        base = base_adv = ni
        if fin_type == "insurer" and vcfg.get("insurer_smooth_earnings", False) and len(a_ni) >= 2 and ni == ni:
            smoothed = float(a_ni.iloc[-3:].mean())  # a catastrophe-light year is not the base
            if smoothed < ni:
                base = base_adv = smoothed
                flags.append("insurer: 3y average earnings used")
        value_base, bridge = mcap, 0.0
        m["earnings_yield"] = ni / mcap if ni == ni else math.nan
        m["book_yield"] = equity / mcap if equity and equity > 0 else math.nan
        m["roe"] = ni / equity if equity and equity > 0 else math.nan
        roe_hist = _finite(a_ni / a_eq.reindex(a_ni.index))
        m["roe_stability"] = float(roe_hist.std()) if len(roe_hist) >= 3 else math.nan
        m["equity_to_assets"] = equity / assets if assets and assets > 0 else math.nan
        R0 = m["roe"]
        R_avg = float(roe_hist.iloc[-3:].mean()) if len(roe_hist) >= 2 else math.nan
        if not (equity and equity > 0):
            blockers.append("no positive book equity")
        if fin_type == "bank" and m["equity_to_assets"] == m["equity_to_assets"] and \
                m["equity_to_assets"] < vcfg.get("bank_min_equity_to_assets", 0.04):
            flags.append(f"thin capital (equity/assets < {vcfg.get('bank_min_equity_to_assets', 0.04):.0%})")
        if fin_type == "platform":
            flags.append("hybrid requires business/ownership segmentation; ROE is not a conservative valuation floor")
        loss_making = math.isnan(ni) or ni <= 0
    else:
        value_base, bridge = ev, net_debt + minority + preferred + other_claims
        ebit_used = ebit_adv = ebit
        if cls == "cyclical" and len(a_rev) >= 3 and not math.isnan(rev) and rev > 0:
            margins = list(_finite(a_ebit / a_rev.reindex(a_ebit.index)))
            if len(margins) >= 3:
                mid = float(np.median(margins))
                adv_margin = min(margins) if vcfg.get("cyclical_adverse_margin", "median") == "min" else mid
                ebit_used = mid * rev
                ebit_adv = adv_margin * min(rev, float(np.mean(list(a_rev.iloc[-3:]) + [rev])))
                flags.append("short-window cycle proxy: nonoverlapping annual margins; full cycle not established")
                m["model_quality"]="short-history cycle proxy"
        base, base_adv = ebit_used * (1 - tax_rate), ebit_adv * (1 - tax_rate)
        nopat_ttm = ebit * (1 - tax_rate) if ebit == ebit else math.nan
        # Consolidated NOPAT must use consolidated capital, including NCI.
        # Match the earnings period's opening/closing capital where available;
        # old fiscal year-ends are not silently called a TTM average.
        capital_points=pd.Series(dtype=float)
        goodwill_points=pd.Series(dtype=float)
        for _,source_frame in reversed(balance_sources):
            capital_points=S.invested_capital(source_frame).combine_first(capital_points)
            goodwill_points=_finite(_annual(source_frame,"goodwill")).combine_first(goodwill_points)
        window=S.annual_capital_window(capital_points,inc_sel.date)
        if window:
            invested=float(capital_points.loc[list(window)].mean())
            goodwill=float(goodwill_points.reindex(window).mean()) if goodwill_points.reindex(window).notna().all() else math.nan
            m["capital_period_basis"]=f"annual opening/closing: {window[0].date()} / {window[1].date()}"
        else:
            invested=equity+minority+debt-cash
            goodwill=_latest(bal,"goodwill")
            m["capital_period_basis"]="closing capital proxy: annual opening or aligned period unavailable"
            flags.append("capital-period proxy: annual opening unavailable or not aligned")
        invested_ex=invested-goodwill
        m["roic"]=nopat_ttm/invested if invested>0 and _isnum(nopat_ttm) else math.nan
        m["roic_ex_goodwill"]=nopat_ttm/invested_ex if invested_ex>0 and _isnum(nopat_ttm) else math.nan
        R0=supplied.get("marginal_return",m["roic"])
        if "marginal_return" not in supplied:
            flags.append("book ROIC is an incremental-return proxy; acquisitions included")
        annual_capital=S.invested_capital(ab)
        denominators=pd.Series({d:S.annual_average_capital(annual_capital,d) for d in annual_capital.index},dtype=float)
        roic_hist=_finite(a_ebit.reindex(denominators.index)*(1-tax_rate)/denominators)
        R_avg=float(roic_hist.iloc[-3:].mean()) if len(roic_hist)>=2 else math.nan
        m["nopat_yield"] = base / ev if ev > 0 and base == base else math.nan
        m["fcf_yield"] = fcf_adj / ev if ev > 0 and fcf_adj == fcf_adj else math.nan
        m["ebit_yield"] = ebit / ev if ev > 0 and ebit == ebit else math.nan
        m["gross_profitability"] = gross / assets if assets and assets > 0 and gross == gross else math.nan
        margins_hist = _finite(a_ebit / a_rev.reindex(a_ebit.index))
        m["margin_stability"] = float(margins_hist.std()) if len(margins_hist) >= 3 else math.nan
        m["cash_conversion"] = float(fcf / ni) if ni and ni > 0 and fcf == fcf else math.nan
        ebitda = ebit + da if _isnum(ebit) and _isnum(da) else math.nan
        m["net_debt_ebitda"] = net_debt / ebitda if ebitda > 0 and _isnum(net_debt) else math.nan
        if fcf_adj == fcf_adj and fcf_adj < 0:
            flags.append("negative FCF-SBC (TTM)")
        loss_making = math.isnan(ebit) or ebit <= 0
        if loss_making:
            m["class"] = cls = "preprofit"
            burn = -(fcf_adj if fcf_adj == fcf_adj else 0)
            m["cash_runway_years"] = cash / burn if burn > 0 and cash == cash else math.nan

    for y in ("fcf_yield", "earnings_yield", "nopat_yield"):
        if m.get(y, 0) == m.get(y, 0) and (m.get(y) or 0) > 0.25:
            flags.append(f"{y} > 25%: check one-offs")
    m["base_earnings"] = base
    if cls == "holdco":
        flags.append("holding company: value via NAV, not screened")
    if cls in ("holdco", "preprofit") or (cls == "financial" and loss_making):
        if cls == "financial":
            m["class"] = "preprofit"
        m["flags"] = "; ".join(flags)
        return m

    # ---------- scenarios
    if _isnum(R0) and R0 > 0:
        if vcfg.get("return_smoothing", False) and "marginal_return" not in supplied and R_avg == R_avg and R_avg > 0 and R_avg < R0:
            R0 = R_avg  # one strong year does not set ten years of reinvestment
            caps.append("R smoothed (3y avg)")
        if R0 > R_cap and "marginal_return" not in supplied:
            R0 = R_cap
            caps.append("R cap")
    else:
        blockers.append("no return on capital")
    if g_est == g_est:
        g0 = g_est
    else:
        g0 = math.nan
        blockers.append("no supported growth estimate")
    if R0 == R0 and g0 == g0 and g0 > R0:
        g0 = R0
        flags.append("growth capped at return on capital")
        caps.append("growth <= R")
    adverse_inputs = [g for g in (rev_cagr, analyst, g0) if g == g]
    if not adverse_inputs:
        g_adv = math.nan
    elif vcfg.get("adverse_growth_mode", "half") == "haircut":
        g_adv = min(adverse_inputs) - vcfg["adverse_growth_haircut"]
    else:
        low = min(adverse_inputs)
        g_adv = 0.5 * low if low > 0 else low  # half the lowest input; a decline is kept, not halved
    if g_adv == g_adv and cls == "cyclical" and vcfg.get("cyclical_adverse_growth_at_terminal", False):
        g_adv = min(g_adv, gT)  # a cycle reverts to trend
    g_adv = min(g_adv,R0) if g_adv == g_adv and R0 == R0 else g_adv
    m.update(est_growth=g0, adverse_growth=g_adv, return_on_capital=R0, base_earnings_adverse=base_adv,
             reinvest_rate=(g0 / R0 if R0 == R0 and R0 > 0 and g0 == g0 else math.nan), caps_hit="; ".join(caps))
    if not (base == base and base > 0):
        blockers.append("no positive base earnings")
    if blockers:
        m["not_rankable"] = "; ".join(blockers)
        m["flags"] = "; ".join(flags)
        return m

    cost_rate=supplied.get("discount_rate",hurdle)
    rate_basis=supplied.get("discount_rate_basis","policy")
    if rate_basis=="cost_of_equity":
        m["not_rankable"]="operating firm cash flows cannot use an equity discount rate"
        m["flags"]="; ".join(flags)
        return m
    terminal_R = supplied.get("terminal_return", min(R0,cost_rate))
    try:
        ds,dt=V.distributions(base,g0,gT,years,R0,terminal_R)
        if dt<=0:
            raise V.InvalidModel("no sustainable terminal distribution; use an explicit asset/finite-life model")
        m["modeled_year1_reinvestment"]=base*(1+g0)-ds[0]
        m["reinvest_rate"]=m["modeled_year1_reinvestment"]/(base*(1+g0))
        m["reinvestment_gap"]=m["observed_net_investment"]-m["modeled_year1_reinvestment"]
        firm_value=V.discount(ds,dt,cost_rate,gT)
        v_reas=firm_value-bridge
        policy_value=V.discount(ds,dt,hurdle,gT)-bridge
        ceiling=min(v_reas,policy_value)
        m["value_at_policy_hurdle"]=policy_value
        m["discount_rate"]=cost_rate
        m["decision_rate"]=max(cost_rate,hurdle)
        m["floor_rate"]=cost_rate
        if rate_basis!="policy":m["valuation_basis"]="sourced cost of capital with accounting-proxy forecast; ceiling satisfies value and policy constraints"
        m["terminal_value_fraction"]=dt/(cost_rate-gT)/(1+cost_rate)**years/firm_value if firm_value else math.nan
        m["terminal_return"]=terminal_R
    except (ValueError,OverflowError) as exc:
        m["not_rankable"]="invalid funded model: "+str(exc)
        m["flags"]="; ".join(flags)
        return m
    try:
        v_adv=V.present_value(base_adv,g_adv,cost_rate,gT,years,R0,terminal_R)-bridge
        m["stress_status"]="computed at common discount rate"
    except (ValueError,OverflowError) as exc:
        v_adv=math.nan
        m["stress_status"]="invalid: "+str(exc)
        flags.append("stress case unavailable: "+str(exc))
    ret=V.solve_return(base,value_base,g0,gT,years,R0,terminal_R)
    growth=V.solve_growth(base,value_base,max(cost_rate,hurdle),gT,years,R0,terminal_R)
    m.update(implied_return=ret.value,implied_return_status=ret.status,implied_return_reason=ret.reason,
             implied_growth=growth.value,implied_growth_status=growth.status,implied_growth_reason=growth.reason,
             implied_growth_roots=json.dumps(growth.roots),implied_growth_residuals=json.dumps(growth.residuals))
    # This is an explicitly named FX-parity scenario, not an EUR equity return.
    if ret.value is not None and base_rates is not None:
        m["fx_parity_return"]=(1+ret.value)*(1+base_rates["rf"])/(1+rates["rf"])-1
    return _finish_values(m,v_reas,v_adv,mcap,flags,policy_ceiling=ceiling)



def _evs_vs_history(data, mcap_now, price_now, a_rev, ab, ai, evs_now) -> float:
    """Current EV/Sales divided by the median EV/Sales at past fiscal year-ends (context only, not a gate).
    Historical market cap = current market cap x price ratio x share-count ratio (ADR-ratio invariant)."""
    hist = data.get("history")
    if hist is None or hist.empty or not price_now or len(a_rev) < 2 or math.isnan(evs_now):
        return math.nan
    close = hist["Close"].dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None) + pd.offsets.MonthEnd(0)
    shares = _annual(ai, "shares_avg")
    debt, cash = _annual(ab, "debt"), _annual(ab, "cash")
    if len(shares) < 2:
        return math.nan
    s_now = float(shares.iloc[-1])
    vals = []
    for d, r in a_rev.items():
        d = pd.Timestamp(d)
        p = close[close.index <= d]
        if not len(p) or r <= 0 or d not in shares.index:
            continue
        mc = mcap_now * float(p.iloc[-1]) / price_now * float(shares[d]) / s_now
        if not all(_isnum(x) for x in (debt.get(d),cash.get(d),shares[d],s_now,mc)):
            continue
        nd = debt[d] - cash[d]
        vals.append((mc + nd) / r)
    return evs_now / float(np.median(vals)) if len(vals) >= 2 else math.nan
