"""Market observations retain units, timestamps and ownership diagnostics."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import math

# Identity tolerance for serialized/rounded provider arithmetic (one part/million).
IDENTITY_RTOL = 1e-6

@dataclass(frozen=True)
class Observation:
    value: float
    as_of: str | None
    source: str
    info: dict | None = None

def number(value) -> bool:
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def quote_time(info: dict) -> str | None:
    stamp=info.get("regularMarketTime")
    if number(stamp):
        try:
            return datetime.fromtimestamp(stamp,timezone.utc).isoformat()
        except (ValueError,OverflowError,OSError):
            return None
    return None

def capitalization(info: dict, divisor: float, supplied: dict) -> tuple[float,list[str],list[str]]:
    """Market cap in major quote units; no silent guess of an ADR or share class.

    Yahoo marketCap is normally in major currency. A minor-unit correction is
    permitted only when the raw price/share identity demonstrates that convention.
    Explicit researched overrides must carry provenance (validated by inputs.py).
    """
    flags,errors=[],[]
    price=info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
    cap=supplied.get("market_cap_quote",info.get("marketCap"))
    if supplied.get("market_cap_basis")=="listed_shares":
        shares=info.get("sharesOutstanding")
        if supplied.get("ownership_reconciled") is not True or not number(shares) or shares<=0:
            return math.nan,flags,["listed-share capitalization requires reconciled ownership and current shares"]
        cap=price/divisor*shares if number(price) else math.nan
        flags.append("capitalization uses source-reconciled listed common shares")
    if "market_cap_quote" in supplied:
        stamp=quote_time(info)
        if stamp is None or str(supplied.get("market_cap_as_of")) != stamp[:10]:
            errors.append("market-cap override does not match the quote observation date")
    if not number(price) or price <= 0 or not number(cap) or cap <= 0:
        return math.nan,flags,["no positive market cap and quote"]
    shares=[info.get(k) for k in ("impliedSharesOutstanding","sharesOutstanding")]
    shares=[s for s in shares if number(s) and s>0]
    if divisor != 1 and "market_cap_quote" not in supplied and supplied.get("market_cap_basis")!="listed_shares":
        minor=any(math.isclose(cap,price*s,rel_tol=IDENTITY_RTOL) for s in shares)
        major=any(math.isclose(cap,price/divisor*s,rel_tol=IDENTITY_RTOL) for s in shares)
        if minor and not major:
            cap/=divisor
            flags.append("market cap minor units reconciled to price x shares")
        elif minor == major:
            errors.append("unresolved minor-unit market capitalization: dated share reconciliation required")
    return float(cap),flags,errors

def ownership_check(info: dict, common_income: float, consolidated_income: float,
                    supplied: dict) -> str | None:
    """Detect an aggregate capitalization coupled to parent-only earnings.

    Average statement shares are not substituted for current shares. A mismatch
    triggers reconciliation, not an automatic capitalization rewrite.
    """
    if supplied.get("ownership_reconciled") is True:
        return None
    common,aggregate=info.get("sharesOutstanding"),info.get("impliedSharesOutstanding")
    if all(number(x) and x>0 for x in (common,aggregate)) and aggregate>common and not math.isclose(common,aggregate,rel_tol=IDENTITY_RTOL):
        if all(number(x) for x in (common_income,consolidated_income)) and not math.isclose(common_income,consolidated_income,rel_tol=IDENTITY_RTOL):
            return "unresolved ownership scope: aggregate share count with parent-only earnings"
    return None
