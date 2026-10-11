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
from . import statements as S, market, models, automatic, evidence_assembly, business_models
from .inputs import validate_issuer
from .checks import policy_rates

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
    return business_models.profile(info,{},override)['class']


def model_family(info:dict, cls:str, override:str|None) -> str:
    return business_models.profile(info,{},override)['family']


def _finish_values(m, reasonable, stress, mcap, flags, policy_ceiling=None):
    raw_ceiling=reasonable if policy_ceiling is None else policy_ceiling
    m.update(raw_value_reasonable=reasonable,raw_value_adverse=stress)
    if not _isnum(raw_ceiling) or not _isnum(reasonable):
        m.update(not_rankable="nonfinite equity valuation",flags="; ".join(dict.fromkeys(flags)))
        return m
    ceiling=max(0.,raw_ceiling);reasonable=max(0.,reasonable)
    stress=max(0.,stress) if _isnum(stress) else math.nan
    m.update(value_reasonable=reasonable,value_adverse=stress,entry_ceiling=ceiling,
             entry_score_raw=100*(1-mcap/ceiling) if ceiling>0 else None,
             entry_score=entry_score(mcap,ceiling) if ceiling>0 else -100.,
             adverse_score=entry_score(mcap,stress) if stress>0 else -100. if _isnum(stress) else math.nan,
             ceiling_px=m['price']*ceiling/mcap,reasonable_px=m['price']*reasonable/mcap,
             adverse_px=m['price']*stress/mcap if _isnum(stress) else math.nan)
    if ceiling==0:flags.append('nonpositive common-equity continuation value; limited-liability endpoint shown')
    if _isnum(stress) and stress>reasonable:flags.append('stress value exceeds base: scenario is not a downside bound')
    m['flags']='; '.join(dict.fromkeys(flags))
    return m


def _explicit_model(m,supplied,vcfg,bridge,mcap,flags,blockers):
    name=supplied["model"]
    permitted={"bank":{"bank_capital"},"insurer":{"insurer_capital"},"reit":{"reit_affo","asset_nav"},"mortgage_reit":{"asset_nav"},
               "regulated":{"regulated_rate_base","operating_forecast"},"holdco":{"asset_nav"},
               "hybrid":{"asset_nav"},"financial_other":{"financial_capital","operating_forecast","asset_nav"},
               "operating":{"operating_forecast","finite_project","asset_nav"},
               "cyclical":{"operating_forecast","finite_project","asset_nav"}}
    if m["model_family"] in permitted and name not in permitted[m["model_family"]] and name not in ('sum_of_parts','equity_residual_income','operating_equity','asset_recovery'):
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
                m['implied_return_claim']='equity'
                m["floor_rate"]=math.nan
                m["valuation_basis"]=("component-specific discount/FX assumptions; parent hurdle does not rerate components" if name=='sum_of_parts' else "attributable asset value; entry ceiling at NAV, no return forecast")
                if name=='sum_of_parts':
                    detail=business_models.sum_of_parts(params,vcfg['years'],rate,gt,models.evaluate)
                    m[case+'_components']=json.dumps(detail.components,sort_keys=True)
                    m[case+'_parent_bridge']=json.dumps(detail.parent_bridge,sort_keys=True)
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
    is_automatic=not supplied.get('model')
    if supplied:
        blockers.extend(validate_issuer(data["symbol"], supplied,effective_date))
    m: dict = {"symbol": data["symbol"], "yahoo_name": info.get("longName") or info.get("shortName") or "",
               "sector": info.get("sector") or "", "industry": info.get("industry") or "",
               "country": info.get("country") or "", "not_rankable": ""}
    if blockers:
        return {**m,"class":classify(info,class_override),"not_rankable":"; ".join(blockers),"flags":"invalid issuer inputs"}
    profile=business_models.profile(info,supplied,class_override)
    cls,family=profile['class'],profile['family']
    m['business_profile']=json.dumps(profile,sort_keys=True,default=str)
    m["class"] = cls
    m.update(model_family=family, model="funded_earnings", model_quality="accounting proxy",
             model_label=models.LABELS["funded_earnings"],
             valuation_basis="purchase policy; not fair value", implied_return_claim="equity" if cls=="financial" else "firm")
    m['classification_basis']=profile['classification_basis']
    if profile.get('uncertainties'):flags.extend(profile['uncertainties'])
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
    if is_automatic and (not _isnum(mcap_q) or mcap_q<=0):
        alternatives,_=evidence_assembly.capitalization_scenarios({**data,'info':info})
        proxies=[x for x in alternatives if x['id'] in ('aggregate_share_proxy','listed_share_proxy')]
        if proxies:
            chosen=next((x for x in proxies if x['id']=='aggregate_share_proxy'),proxies[0])
            mcap_q=chosen['value_quote'];cap_errors=[]
            flags.append('capitalization estimated from quote and shares; economic-unit reconciliation required')
    flags.extend(cap_flags)
    if is_automatic:flags.extend(cap_errors)
    else:blockers.extend(cap_errors)
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
        if is_automatic and base_rates is not None:
            rates=base_rates
            m['currency_assumption']=f'Constant future {f_ccy}/{vcfg.get("base_currency","EUR")} FX; cash flows valued under base-currency policy'
            flags.append('reporting-currency rate unavailable; explicit constant-FX/base-currency policy scenario')
        else:
            blockers.append(f"no rates for {f_ccy} in config [rates]")
            hurdle = floor = gT = math.nan
    if rates is not None:
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
            if is_automatic:flags.append('country premium unspecified; valuation conditional on global policy return')
            else:blockers.append("no country-risk assumption for " + m["country"])
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
    assembled=None
    if is_automatic:
        assembled=evidence_assembly.assemble(data,income_required=('net_income',) if cls in ('financial','holdco') else ('revenue','net_income'),
            balance_required=('common_equity',),sourced_facts=supplied.get('filing_checks',()),
            rounding_unit=supplied.get('statement_rounding_unit',0.0))
        inc_sel,cf_sel=assembled.income,assembled.cashflow
        # A same-period sourced balance remains preferred when it is complete.
        if not facts:bal_sel=assembled.balance
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
    if not is_automatic and any(c["status"]=="unreconciled" for c in reconciliation):
        flags.append("TTM/quarterly inputs unreconciled: rounding or accounting basis needs verification")
    checks=S.filing_checks(selections,supplied.get("filing_checks",[]))
    m["filing_reconciliation"]=json.dumps(checks,sort_keys=True)
    for check in checks:
        if not is_automatic and check["status"]=="contradiction":
            blockers.append(f"filing contradiction: {check['statement']} {check['field']} at {check['period_end']}")
        elif not is_automatic and check["status"]=="basis_unreconciled":
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
        tax_rate=math.nan

    debt, cash = _latest(bal, "debt"), _latest(bal, "cash")
    equity, minority, assets = _latest(bal, "common_equity" if cls=="financial" else "equity"), _latest(bal, "minority"), _latest(bal, "assets")
    amounts={"debt":debt,"cash":cash,"equity":equity,"minority":minority,"preferred":_latest(bal,"preferred")}
    if facts and (bal_sel.date is None or str(supplied.get("period_end"))!=bal_sel.date.date().isoformat()):
        blockers.append("sourced balance facts differ from selected balance period")
    debt,cash,equity,minority,preferred=(amounts[k] for k in ("debt","cash","equity","minority","preferred"))
    # Claim requirements are enforced by the selected projection: equity models
    # do not need a second debt/cash bridge; firm models require every bridge leg.
    other_claims=supplied.get("other_claims",0.0)
    m["equity_bridge_scope"]="debt/cash/minority/preferred; other claims " + ("supplied" if "other_claims" in supplied else "not independently reviewed")
    firm_bridge_needed=supplied.get("model") in ("operating_forecast","regulated_rate_base","finite_project")
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
        if is_automatic:flags.append(scope_issue+'; provider capitalization is a conditional common-equity proxy')
        else:blockers.append(scope_issue)
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
    growth_inputs = [g for g in (rev_cagr, analyst) if _isnum(g)]
    g_raw = float(np.mean(growth_inputs)) if growth_inputs else math.nan
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
    # Model-specific input requirements belong to the selected model, not to a
    # generic firm bridge or a sector label. Context metrics remain descriptive.
    m.update(earnings_yield=ni/mcap if _isnum(ni) else math.nan,
             book_yield=equity/mcap if _isnum(equity) else math.nan,
             roe=ni/equity if _isnum(ni) and _isnum(equity) and equity>0 else math.nan,
             equity_to_assets=equity/assets if _isnum(equity) and _isnum(assets) and assets>0 else math.nan,
             fcf_yield=fcf_adj/mcap if _isnum(fcf_adj) else math.nan)
    context={'family':family,'years':vcfg['years'],'hurdle':hurdle,'terminal_growth':gT,'cfg':cfg,'supplied':supplied,
             'quote_to_reporting_fx':fx_q_to_f,'valuation_date':effective_date}
    return automatic.evaluate(data,selections,context,m,flags,blockers,assembled)



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
