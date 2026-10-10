"""Source-labeled issuer assumptions; no web extraction silently becomes fact."""
from __future__ import annotations
from datetime import date
from pathlib import Path
import math
import tomllib

MODELS={"operating_forecast","bank_capital","insurer_capital","financial_capital","reit_affo","regulated_rate_base","asset_nav","finite_project"}
FAMILIES={"operating","cyclical","bank","insurer","reit","mortgage_reit","regulated","holdco","hybrid","financial_other"}
COMMON={"source","as_of","period_end","currency","model","model_family","base","stress","country_risk_premium",
        "market_cap_quote","market_cap_as_of","market_cap_basis","ownership_reconciled","debt","cash","equity","common_equity","minority","goodwill","preferred","other_claims",
        "tax_rate","marginal_return","terminal_return","discount_rate","discount_rate_basis",
        "geographic_exposures","capital_scope","forecast_source","sec_cik"}
PARAMETERS={"source","revenue","growth","margin_start","margin_terminal","tax_rate","sales_to_capital",
            "maintenance_net_capex","funding_capacity","net_income","available_capital","capital_base",
            "required_capital_ratio","capital_growth","payout_limit","releasable_excess","release_terminal_excess","excess_income_excluded",
            "ffo_common","recurring_capex","leasing_costs","return_on_new_equity","terminal_return",
            "rate_base","rate_base_history","earned_after_tax_return","regulatory_lag_years","attributable_assets","parent_debt",
            "other_claims","tax_leakage","holding_cost_pv","cashflows","salvage_value","closure_cost",
            "discount_rate","terminal_growth"}

def validate_issuer(symbol:str,p:dict, evaluation_date:date|None=None) -> list[str]:
    errors=[]
    if not isinstance(p,dict):return [f"{symbol}: issuer input must be a table"]
    for k in p.keys()-COMMON:errors.append(f"{symbol}: unknown input {k}")
    if not isinstance(p.get("source"),str) or not p["source"].strip():errors.append(f"{symbol}: source is required")
    if not isinstance(p.get("currency"),str) or len(p["currency"])!=3:errors.append(f"{symbol}: reporting currency is required")
    for k in ("as_of","period_end"):
        try:date.fromisoformat(str(p[k]))
        except (KeyError,ValueError):errors.append(f"{symbol}: {k} must be YYYY-MM-DD")
    try:
        observed=date.fromisoformat(str(p["as_of"]));period=date.fromisoformat(str(p["period_end"]))
        if observed>(evaluation_date or date.today()) or period>observed:errors.append(f"{symbol}: observation cannot be future or precede its accounting period")
    except (KeyError,ValueError):pass
    if "model" in p and (not isinstance(p["model"],str) or p["model"] not in MODELS):errors.append(f"{symbol}: unsupported model")
    if "model_family" in p and (not isinstance(p["model_family"],str) or p["model_family"] not in FAMILIES):errors.append(f"{symbol}: unsupported model_family")
    if "ownership_reconciled" in p and not isinstance(p["ownership_reconciled"],bool):errors.append(f"{symbol}: ownership_reconciled must be boolean")
    if p.get("market_cap_basis","provider") not in ("provider","listed_shares"):errors.append(f"{symbol}: unsupported market_cap_basis")
    if p.get("market_cap_basis")=="listed_shares" and p.get("ownership_reconciled") is not True:
        errors.append(f"{symbol}: listed_shares requires a sourced ownership reconciliation")
    if "market_cap_quote" in p and p.get("market_cap_basis")=="listed_shares":
        errors.append(f"{symbol}: choose dated capitalization or listed-share basis, not both")
    if "market_cap_quote" in p:
        try:date.fromisoformat(str(p["market_cap_as_of"]))
        except (KeyError,ValueError):errors.append(f"{symbol}: dated market_cap_as_of is required for a cap override")
    if ("discount_rate" in p or any(isinstance(p.get(c),dict) and "discount_rate" in p[c] for c in ("base","stress"))) and p.get("discount_rate_basis") not in ("wacc","cost_of_equity","unlevered","policy"):
        errors.append(f"{symbol}: discount_rate_basis is required")
    if p.get("model")!="asset_nav" and p.get("discount_rate_basis") in ("wacc","cost_of_equity","unlevered") and "discount_rate" not in p:
        for case in ("base","stress"):
            if not isinstance(p.get(case),dict) or "discount_rate" not in p[case]:
                errors.append(f"{symbol}.{case}: an intrinsic rate must be supplied, not replaced by the policy hurdle")
    for case in ("base","stress"):
        if case not in p:continue
        if not isinstance(p[case],dict):errors.append(f"{symbol}.{case}: must be a table");continue
        for k,v in p[case].items():
            if k not in PARAMETERS:errors.append(f"{symbol}.{case}: unknown parameter {k}")
            if k not in ("source","release_terminal_excess","excess_income_excluded","cashflows","rate_base_history") and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)):
                errors.append(f"{symbol}.{case}.{k}: must be finite numeric")
            if k in ("release_terminal_excess","excess_income_excluded") and not isinstance(v,bool):
                errors.append(f"{symbol}.{case}.{k}: must be boolean")
    if p.get("model") and ("base" not in p or "stress" not in p):
        errors.append(f"{symbol}: explicit base and stress model inputs are required")
    for k in ("debt","cash","equity","common_equity","minority","preferred","other_claims","goodwill","tax_rate","marginal_return","terminal_return","country_risk_premium","discount_rate","market_cap_quote"):
        if k in p and (isinstance(p[k],bool) or not isinstance(p[k],(int,float)) or not math.isfinite(p[k])):
            errors.append(f"{symbol}.{k}: must be finite numeric")
    if "geographic_exposures" in p:
        weights=p["geographic_exposures"]
        if "country_risk_premium" in p:errors.append(f"{symbol}: choose exposures or an explicit country premium, not both")
        if not isinstance(weights,dict) or not weights or not all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and 0<=x<=1 for x in weights.values()):
            errors.append(f"{symbol}: geographic weights must be finite fractions")
        elif not math.isclose(sum(weights.values()),1,rel_tol=1e-9,abs_tol=0):
            errors.append(f"{symbol}: geographic weights must sum to one")
    return errors

def load(path:Path) -> dict[str,dict]:
    """Shared assumptions plus local overlay; each complete entry is validated."""
    out={}
    for file in (path,path.with_suffix('.local.toml')):
        if not file.exists():continue
        data=tomllib.loads(file.read_text(encoding='utf-8'))
        if data.keys()-{"issuers"}:raise ValueError(f"{file}: only [issuers] is supported")
        out.update(data.get("issuers",{}))
    errors=[error for symbol,p in out.items() for error in validate_issuer(symbol,p)]
    if errors:raise ValueError("; ".join(errors))
    return out
