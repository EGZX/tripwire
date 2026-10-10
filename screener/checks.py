"""Start-up validation of config.toml against the universe. Errors stop the run instead of producing silent gaps."""
from __future__ import annotations

from datetime import date
from numbers import Real


def validate(cfg: dict, currencies: set[str]) -> list[str]:
    errors: list[str] = []
    v = cfg.get("valuation") or {}
    rates = cfg.get("rates") or {}
    erp = v.get("equity_risk_premium", 0.0)
    for ccy, r in rates.items():
        if not isinstance(r, dict) or not all(isinstance(r.get(k), Real) for k in ("rf", "g")):
            errors.append(f"[rates] {ccy}: needs numeric rf and g")
            continue
        if "crp" in r and not isinstance(r["crp"], Real):
            errors.append(f"[rates] {ccy}: crp must be numeric")
            continue
        if r.get("as_of") is not None:
            try:
                date.fromisoformat(str(r["as_of"]))
            except ValueError:
                errors.append(f"[rates] {ccy}: as_of must be a date YYYY-MM-DD")
        hurdle = r["rf"] + erp + r.get("crp", 0.0)
        if r["g"] >= hurdle:
            errors.append(f"[rates] {ccy}: terminal growth {r['g']:.1%} is not below the hurdle {hurdle:.1%}")
    base = v.get("base_currency")
    if base and base not in rates:
        errors.append(f"[valuation] base_currency {base} has no entry in [rates]")
    for ccy in sorted(c for c in currencies if c):
        if ccy not in rates:
            errors.append(f"[rates] missing entry for reporting currency {ccy}: add rf and g (and crp) to config.toml")
    return errors
