"""Start-up validation of config.toml against the universe. Errors stop the run instead of producing silent gaps."""
from __future__ import annotations

from datetime import date
from numbers import Real
import math


def policy_rates(r:dict,v:dict,country_premium:float=0.0) -> dict:
    """One canonical purchase-policy rate calculation; currency CRP is not used."""
    least=r["g"]+v.get("min_rate_over_terminal_growth",0.02)
    before_min=max(r["rf"]+v["equity_risk_premium"]+country_premium,least)
    hurdle=max(before_min,v.get("min_hurdle",0.0))
    return {"rf":r["rf"],"g":r["g"],"crp":country_premium,"hurdle":hurdle,"floor":hurdle,
            "min_hurdle_binds":hurdle>before_min}


def numeric(x):
    return isinstance(x,Real) and not isinstance(x,bool) and math.isfinite(x)


def validate(cfg: dict, currencies: set[str]) -> list[str]:
    errors: list[str] = []
    v = cfg.get("valuation") or {}
    rates = cfg.get("rates") or {}
    erp = v.get("equity_risk_premium", 0.0)
    if not numeric(erp) or erp<0:
        errors.append("[valuation] equity_risk_premium must be finite and nonnegative")
        erp=0.0
    for key in ("growth_floor","growth_cap","return_cap","min_hurdle","min_rate_over_terminal_growth",
                "adverse_growth_haircut","stale_after_days","quote_max_age_days","rates_max_age_days"):
        if key in v and not numeric(v[key]):errors.append(f"[valuation] {key} must be finite numeric")
    if "years" in v and (not isinstance(v["years"],int) or isinstance(v["years"],bool) or not 1<=v["years"]<=50):
        errors.append("[valuation] years must be an integer in 1..50")
    if all(numeric(v.get(k)) for k in ("growth_floor","growth_cap")) and not -1<v["growth_floor"]<=v["growth_cap"]:
        errors.append("[valuation] growth bounds must be ordered and greater than -100%")
    for key in ("return_cap","stale_after_days","min_rate_over_terminal_growth","quote_max_age_days","rates_max_age_days"):
        if key in v and numeric(v[key]) and v[key]<=0:errors.append(f"[valuation] {key} must be positive")
    if v.get("adverse_growth_mode","half") not in ("half","haircut"):
        errors.append("[valuation] adverse_growth_mode must be half or haircut")
    if numeric(v.get("adverse_growth_haircut")) and v["adverse_growth_haircut"]<0:
        errors.append("[valuation] adverse_growth_haircut must be nonnegative")
    for ccy, r in rates.items():
        if not isinstance(r, dict) or not all(numeric(r.get(k)) for k in ("rf", "g")):
            errors.append(f"[rates] {ccy}: needs numeric rf and g")
            continue
        if "crp" in r and not numeric(r["crp"]):
            errors.append(f"[rates] {ccy}: crp must be numeric")
            continue
        if r.get("crp",0.0)!=0:
            errors.append(f"[rates] {ccy}: nonzero currency crp is no longer supported; use country_risk or sourced issuer exposures")
        if r.get("as_of") is not None:
            try:
                date.fromisoformat(str(r["as_of"]))
            except ValueError:
                errors.append(f"[rates] {ccy}: as_of must be a date YYYY-MM-DD")
        if min(r["rf"],r["g"])<=-1:errors.append(f"[rates] {ccy}: rf/g must be above -100%")
        hurdle = r["rf"] + erp
        if r["g"] >= hurdle:
            errors.append(f"[rates] {ccy}: terminal growth {r['g']:.1%} is not below the hurdle {hurdle:.1%}")
    base = v.get("base_currency")
    for country,entry in (cfg.get("country_risk") or {}).items():
        if not isinstance(entry,dict) or not numeric(entry.get("premium")) or entry["premium"]<0:
            errors.append(f"country risk {country}: finite nonnegative premium required")
        elif not entry.get("source"):
            errors.append(f"country risk {country}: source/assumption basis required")
    if base and base not in rates:
        errors.append(f"[valuation] base_currency {base} has no entry in [rates]")
    for ccy in sorted(c for c in currencies if c):
        if ccy not in rates:
            errors.append(f"[rates] missing entry for reporting currency {ccy}: add rf and g (and crp) to config.toml")
    return errors
