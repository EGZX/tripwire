"""Assemble dated accounting evidence without changing provider observations.

This module resolves what can be resolved arithmetically. It does not turn a
provider's debt/lease labels or share counts into verified economic rights.
Alternatives are conditional interpretations, not probabilities or valuation
bounds. All amounts remain in their original statement/quote currency.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real

import numpy as np
import pandas as pd

from . import market, statements as S
from .fetch import major_currency


INCOME_FLOWS = ("revenue", "gross", "ebit", "net_income", "pretax", "tax",
                "interest", "rd", "consolidated_income", "minority_income", "preferred_dividends",
                "discontinued_income", "unusual", "unusual_tax")
CASH_FLOWS = ("ocf", "capex", "da", "sbc", "working_capital", "acquisitions", "investing_cashflow",
              "common_dividends", "net_common_issuance", "common_issuance", "common_repurchases")


@dataclass(frozen=True)
class AssembledEvidence:
    income: S.Selection
    cashflow: S.Selection
    balance: S.Selection
    claims: dict
    capitalizations: tuple[dict, ...]
    ledger: dict
    uncertainties: tuple[dict, ...]


def _number(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, (bool, np.bool_)) and math.isfinite(value)


def _date(value) -> str | None:
    return value.date().isoformat() if value is not None else None


def _prepared(frame, source, uncertainties) -> pd.DataFrame:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    result = frame.copy(deep=True)
    try:
        result.columns = S.period_dates(result.columns)
    except (TypeError, ValueError, OverflowError) as exc:
        uncertainties.append({"kind": "invalid_period", "source": source, "detail": str(exc)})
        return pd.DataFrame()
    if result.columns.duplicated().any():
        uncertainties.append({"kind": "duplicate_period", "source": source,
                              "detail": "Duplicate fiscal endpoints require source reconciliation."})
        return pd.DataFrame()
    return result.sort_index(axis=1, ascending=False)


def _raw_row(frame, label):
    if label not in frame.index:
        return None
    values = frame.loc[label]
    if isinstance(values, pd.DataFrame):
        if not all(values.iloc[0].equals(values.iloc[i]) for i in range(1, len(values))):
            return None
        values = values.iloc[0]
    return pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _uniform_row(frame, field, dates):
    """One definition across all quarters; never splice reported/adjusted aliases."""
    for label in S.ROWS[field]:
        values = _raw_row(frame, label)
        if values is not None:
            values = values.reindex(dates)
            if values.notna().all():
                return label, values
    return None, None


def _period_metadata(frame, key, dates):
    """Optional source metadata must not be ignored when it disproves coherence."""
    metadata = frame.attrs.get(key)
    if not isinstance(metadata, dict):
        return set()
    selected = set()
    for date, value in metadata.items():
        try:
            matches = S.period_dates([date])[0] in dates
        except (TypeError, ValueError):
            continue
        if matches and value:
            selected.add(str(value))
    return selected


def _trailing(data, kind, required, rounding_unit, uncertainties):
    quarterly_name = "quarterly_income_stmt" if kind == "income" else "quarterly_cashflow"
    ttm_name = "ttm_income_stmt" if kind == "income" else "ttm_cashflow"
    annual_name = "income_stmt" if kind == "income" else "cashflow"
    keys = INCOME_FLOWS if kind == "income" else CASH_FLOWS
    q = _prepared(data.get(quarterly_name), quarterly_name, uncertainties)
    ttm = _prepared(data.get(ttm_name), ttm_name, uncertainties)
    annual = _prepared(data.get(annual_name), annual_name, uncertainties)
    dates = list(q.columns[:4])
    detail = {"method": "provider_statement", "quarter_source": quarterly_name,
              "quarter_periods": [_date(d) for d in dates], "fields": {}, "comparisons": []}
    # Endpoints alone cannot establish duration when the provider explicitly
    # declares an annual/YTD/half-year table. Otherwise yfinance's quarterly
    # endpoint contract is used and made visible in the evidence ledger.
    duration = q.attrs.get("period_type", "quarterly")
    consecutive = len(dates) == 4 and all(84 <= (a-b).days <= 98 for a, b in zip(dates, dates[1:]))
    known_currency = q.attrs.get("currency")
    currency = (data.get("info") or {}).get("financialCurrency")
    currencies = _period_metadata(q, "currencies", dates)
    bases = _period_metadata(q, "accounting_bases", dates)
    currency_ok = (not known_currency or not currency or known_currency == currency) and len(currencies) <= 1
    if currency and currencies and currencies != {currency}:
        currency_ok = False
    if not currency_ok:
        uncertainties.append({"kind": "statement_currency_conflict", "source": quarterly_name,
                              "statement_currency": known_currency, "reporting_currency": currency})
    if len(bases) > 1:
        uncertainties.append({"kind": "quarter_accounting_basis_conflict", "source": quarterly_name,
                              "accounting_bases": sorted(bases),
                              "detail": "Different disclosed accounting bases cannot form one trailing statement."})
    constructed = pd.DataFrame()
    if consecutive and duration in ("quarterly", "quarter", "3M") and currency_ok and len(bases) <= 1:
        sums, comparable = {}, {}
        for field in keys:
            label, values = _uniform_row(q, field, dates)
            if label is None:
                continue
            sums[label] = math.fsum(float(x) for x in values)
            comparable[label] = values
            detail["fields"][field] = {"row": label, "operation": "sum",
                                       "inputs": [float(x) for x in values], "periods": [_date(d) for d in dates]}
            if label != S.ROWS[field][0]:
                uncertainties.append({"kind": "alias_fallback", "statement": kind, "field": field,
                                      "row": label, "detail": "Preferred definition unavailable in all four quarters."})
        constructed = pd.DataFrame({dates[0]: sums}, dtype=float)
        constructed.attrs = {**q.attrs, "assembled_from": quarterly_name, "period_type": "ttm"}
        canonical_quarters = pd.DataFrame(comparable).T.reindex(columns=dates)
        detail["comparisons"] = S.trailing_checks(ttm, canonical_quarters, keys, rounding_unit)
        for check in detail["comparisons"]:
            if check["status"] != "ties":
                uncertainties.append({"kind": "provider_ttm_mismatch", "statement": kind, **check,
                                      "resolution": "Use coherent four-quarter observations; retain provider TTM as an alternative."})
        detail["quarterly_duration_basis"] = "provider quarterly table plus consecutive 12-to-14-week endpoints"
        detail["currency"] = known_currency or currency
        detail["accounting_basis"] = q.attrs.get("accounting_basis", next(iter(bases)) if bases else "provider not explicitly labeled")
    elif not q.empty:
        uncertainties.append({"kind": "quarter_window_unavailable", "statement": kind,
                              "periods": [_date(d) for d in dates], "duration": duration,
                              "detail": "Four consecutive quarterly observations not established; annual/semiannual periods were not summed."})
    complete = not constructed.empty and all(math.isfinite(S.latest(constructed, k)) for k in required)
    # A newer valid provider TTM is not replaced by an older quarter window.
    newer_ttm = not ttm.empty and not constructed.empty and S.statement_date(ttm) > dates[0]
    if complete and not newer_ttm:
        selection = S.Selection(constructed, "ttm_" + kind + "_from_quarters", dates[0], dates[0], ())
        detail["method"] = "four_quarter_sum"
    else:
        selection = S.select([(ttm_name, ttm), (annual_name, annual)], required)
        if not constructed.empty:
            detail["partial_quarter_sum_missing"] = [k for k in required if not math.isfinite(S.latest(constructed, k))]
        if selection.source == ttm_name:
            uncertainties.append({"kind": "unverified_provider_ttm", "statement": kind,
                                  "detail": "No complete equally current four-quarter reconstruction; provider trailing statement retained."})
    detail.update(selected_source=selection.source, period_end=_date(selection.date), missing=list(selection.missing))
    return selection, detail


def _at(frame, label):
    values = _raw_row(frame, label)
    value = values.get(S.statement_date(frame), math.nan) if values is not None else math.nan
    return float(value) if _number(value) else None


def _candidate(value, scope, rows, source, *, conditional=False, basis=None):
    return {"value": float(value), "scope": scope, "rows": list(rows), "source": source,
            "conditional": conditional, "accounting_basis": basis}


def balance_claims(selection: S.Selection, sourced_facts=(), currency=None) -> tuple[dict, list[dict]]:
    """Expose same-period claim definitions and genuinely known arithmetic bounds.

    The maximum remains unknown when components are missing. A differently
    sourced accounting basis is a scenario, never a same-basis debt bound.
    Lease-inclusive labels do not prove leases were removed from earnings.
    """
    frame, source = selection.frame, selection.source
    claims, uncertain = {}, []
    for field in ("cash", "equity", "common_equity", "minority", "preferred", "assets", "goodwill"):
        value = S.latest(frame, field)
        value = float(value) if _number(value) else None
        label = S.latest_label(frame, field)
        rows = [label] if label else []
        if value is not None and not rows:
            rows = ({"minority": ["Total Equity Gross Minority Interest", "Stockholders Equity"],
                     "preferred": ["Stockholders Equity", "Common Stock Equity"],
                     "common_equity": ["Stockholders Equity", "Preferred Stock Equity"],
                     "equity": ["Common Stock Equity", "Preferred Stock Equity"]}).get(field, [])
        claims[field] = {"value": value, "status": "missing" if value is None else "reported" if label else "derived",
                         "source": source, "period_end": _date(selection.date), "rows": rows, "alternatives": []}
    cash_only = _at(frame, "Cash And Cash Equivalents")
    if cash_only is not None:
        claims["cash"]["alternatives"].append(_candidate(cash_only, "cash_and_cash_equivalents", ["Cash And Cash Equivalents"], source))
    if claims["cash"]["value"] is not None and cash_only is not None and claims["cash"]["value"] != cash_only:
        uncertain.append({"kind": "cash_investment_scope", "detail": "Broad cash includes investments; operating cash needs and investment liquidity remain unverified."})

    alternatives = []
    reported = _at(frame, "Total Debt")
    if reported is not None:
        alternatives.append(_candidate(reported, "provider_total_debt_unclassified", ["Total Debt"], source))
    components = {}
    for scope, labels in (("borrowings_excluding_reported_leases", ("Current Debt", "Long Term Debt")),
                          ("provider_lease_inclusive_debt", ("Current Debt And Capital Lease Obligation", "Long Term Debt And Capital Lease Obligation"))):
        values = [_at(frame, label) for label in labels]
        components[scope] = dict(zip(labels, values))
        if all(v is not None for v in values):
            alternatives.append(_candidate(math.fsum(values), scope, labels, source))
    leases = S.latest(frame, "lease_liabilities")
    leases = float(leases) if _number(leases) else None
    claims["leases"] = {"value": leases, "scope": "provider_lease_liabilities_unclassified",
                        "source": source, "period_end": _date(selection.date),
                        "detail": "Provider capital-lease labels can include operating leases; no automatic cashflow adjustment."}
    if leases is not None and leases > 0:
        uncertain.append({"kind": "lease_convention", "value": leases,
                          "detail": "Keep debt including leases and the conditional lease-excluded interpretation; match the chosen interpretation to cashflow treatment."})
        if reported is not None and reported >= leases:
            alternatives.append(_candidate(reported-leases, "debt_less_reported_leases_if_fully_included",
                                           ["Total Debt", "Capital Lease Obligations"], source, conditional=True))
    primary = alternatives[0]["value"] if alternatives else None
    # A disclosed partial debt component is a lower bound on debt in its own
    # reported convention, not evidence that the other component equals zero.
    observed = [v for pair in components.values() for v in pair.values() if v is not None and v >= 0]
    lower = max(observed + ([reported] if reported is not None and reported >= 0 else []), default=None)
    complete_values = [a["value"] for a in alternatives if not a["conditional"]]
    if complete_values:
        lower = max(([lower] if lower is not None else []) + complete_values)
    status = "missing" if primary is None else "reported" if reported is not None else "derived"
    bare = components["borrowings_excluding_reported_leases"]
    inclusive = components["provider_lease_inclusive_debt"]
    has_complete_pair = all(v is not None for v in bare.values()) or all(v is not None for v in inclusive.values())
    if primary is not None and not has_complete_pair:
        status = "scope_uncertain"
        uncertain.append({"kind": "debt_components_incomplete", "known_lower_bound": lower,
                          "detail": "A provider total is available, but missing components prevent verification of current debt and lease scope."})
    if len(set(complete_values)) > 1:
        status = "scope_uncertain"
        uncertain.append({"kind": "debt_definition_alternatives", "detail": "Reported total and complete component sums differ; definitions are retained separately."})
    claims["debt"] = {"value": primary, "status": status, "period_end": _date(selection.date),
                      "source": source, "components": components, "alternatives": alternatives,
                      "bounds": {"minimum_disclosed_claims": lower, "maximum": None,
                                 "scope": "observed provider debt conventions; not a verified common-equity bridge"}}
    for fact in sourced_facts:
        if fact.get("statement") != "balance" or fact.get("period_type", "instant") != "instant":
            continue
        field = fact.get("field")
        if field not in claims or field == "leases" or not _number(fact.get("value")) or not fact.get("source"):
            continue
        if str(fact.get("period_end")) != _date(selection.date):
            uncertain.append({"kind": "sourced_period_mismatch", "field": field, "source": fact["source"]})
            continue
        if fact.get("currency") and currency and fact["currency"] != currency:
            uncertain.append({"kind": "sourced_currency_mismatch", "field": field, "source": fact["source"]})
            continue
        cross_basis = not (fact.get("source_basis") and fact.get("provider_basis") and
                           fact["source_basis"] == fact["provider_basis"])
        alternative = _candidate(fact["value"], "sourced_" + fact.get("relation", "equal"), [], fact["source"],
                                 conditional=cross_basis, basis=fact.get("source_basis"))
        alternative["relation"] = fact.get("relation", "equal")
        claims[field]["alternatives"].append(alternative)
        if cross_basis:
            uncertain.append({"kind": "accounting_basis_alternative", "field": field,
                              "source_basis": fact.get("source_basis"), "provider_basis": fact.get("provider_basis"),
                              "detail": "Different or unspecified accounting bases remain a reconciliation scenario, not a bound or replacement."})
        elif field == "debt" and fact.get("relation") == "minimum":
            old = claims[field]["bounds"]["minimum_disclosed_claims"]
            claims[field]["bounds"]["minimum_disclosed_claims"] = max(old, fact["value"]) if old is not None else fact["value"]
        if not cross_basis and claims[field]["value"] is not None:
            actual, expected = claims[field]["value"], fact["value"]
            relation = fact.get("relation", "equal")
            rounding = fact.get("rounding_unit", 0.0)
            allowance = rounding/2 + 2*math.ulp(abs(expected)) if _number(rounding) and rounding >= 0 else 0.0
            ties = (abs(actual-expected) <= allowance if relation == "equal" else
                    actual >= expected-allowance if relation == "minimum" else actual <= expected+allowance)
            if not ties:
                claims[field]["status"] = "sourced_constraint_unreconciled"
                uncertain.append({"kind": "sourced_claim_mismatch", "field": field,
                                  "provider_value": actual, "source_value": expected,
                                  "relation": relation, "source": fact["source"],
                                  "detail": "Retain both observations; provider claim does not satisfy same-basis sourced evidence."})
    for field in ("debt", "cash", "equity", "common_equity", "minority", "preferred"):
        if claims[field]["value"] is None:
            uncertain.append({"kind": "missing_claim", "field": field,
                              "detail": "Unknown is not zero; necessity depends on the valuation claim."})
    return claims, uncertain


def capitalization_scenarios(data) -> tuple[tuple[dict, ...], list[dict]]:
    """Keep listed and aggregate capitalizations without asserting economic rights.

    Unequal counts can mean share classes, ADRs, dual listings or partnerships.
    NCI is independent evidence and does not identify which explanation applies.
    Price-times-shares alternatives require the named counts to be quote units.
    """
    info = data.get("info") or {}
    currency, divisor = major_currency(info.get("currency"))
    price = next((info.get(k) for k in ("regularMarketPrice", "currentPrice", "previousClose")
                  if _number(info.get(k)) and info[k] > 0), None)
    candidates, uncertain = [], []
    cap, flags, errors = market.capitalization(info, divisor, {})
    if _number(cap) and cap > 0:
        candidates.append({"id": "provider_market_cap", "value_quote": cap,
                           "scope": "listing_based_aggregate_equity_proxy", "conditional": bool(errors),
                           "source": "info.marketCap", "unit_reconciliation": flags})
        if errors and divisor != 1:
            candidates.append({"id": "provider_cap_if_minor_units", "value_quote": cap/divisor,
                               "scope": "unresolved_provider_currency_units", "conditional": True,
                               "source": "info.marketCap / quote_divisor"})
    if price is not None:
        for key, identity, scope in (("sharesOutstanding", "listed_share_proxy", "listed_count_in_quote_units"),
                                     ("impliedSharesOutstanding", "aggregate_share_proxy", "aggregate_count_in_quote_units")):
            shares = info.get(key)
            if _number(shares) and shares > 0:
                value = price/divisor*shares
                candidates.append({"id": identity, "value_quote": value, "scope": scope,
                                   "conditional": True, "source": "quote x info." + key,
                                   "shares": float(shares), "assumption": "Share count represents the same economic units as the listing quote."})
    for candidate in candidates:
        candidate.update(quote_currency=currency, quote_divisor=divisor, as_of=market.quote_time(info))
    distinct = []
    for candidate in candidates:
        if not any(math.isclose(candidate["value_quote"], x, rel_tol=market.IDENTITY_RTOL) for x in distinct):
            distinct.append(candidate["value_quote"])
    if len(distinct) > 1:
        uncertain.append({"kind": "capitalization_scope_alternatives",
                          "detail": "Multiple quote/count capitalizations are retained. Economic share-class, ADR or partnership rights are not established by aggregate counts or NCI alone.",
                          "values_quote": distinct})
    if not candidates:
        uncertain.append({"kind": "missing_capitalization", "detail": "No positive capitalization or quote/count scenario can be formed."})
    if errors:
        uncertain.append({"kind": "provider_capitalization_unverified", "detail": "; ".join(errors)})
    return tuple(candidates), uncertain


def assemble(data: dict, *, income_required=("revenue", "ebit"), cashflow_required=("ocf", "capex"),
             balance_required=("equity", "assets"), sourced_facts=(), rounding_unit=0.0) -> AssembledEvidence:
    """Return coherent selections, dated claims, conditional scenarios and diagnostics.

    ``rounding_unit`` is a documented statement display unit, not a fitted
    materiality threshold. Raw frames are never mutated or overwritten.
    ``sourced_facts`` uses the existing filing-check schema; different accounting
    bases remain alternatives. No ticker-specific knowledge is embedded here.
    """
    if not _number(rounding_unit) or rounding_unit < 0:
        raise ValueError("rounding_unit must be a finite nonnegative sourced reporting unit")
    uncertain = []
    income, income_ledger = _trailing(data, "income", income_required, rounding_unit, uncertain)
    cashflow, cashflow_ledger = _trailing(data, "cashflow", cashflow_required, rounding_unit, uncertain)
    balance_sources = [(name, _prepared(data.get(key), name, uncertain)) for name, key in
                       (("filing_balance_sheet", "filing_balance_sheet"),
                        ("quarterly_balance_sheet", "quarterly_balance_sheet"), ("balance_sheet", "balance_sheet"))]
    balance = S.select(balance_sources, tuple(balance_required), instant=True)
    currency = (data.get("info") or {}).get("financialCurrency")
    claims, claim_uncertainties = balance_claims(balance, sourced_facts, currency)
    caps, cap_uncertainties = capitalization_scenarios(data)
    uncertain.extend(claim_uncertainties + cap_uncertainties)
    ledger = {"income": income_ledger, "cashflow": cashflow_ledger,
              "balance": S.period_ledger({"balance": balance})["balance"],
              "source_snapshot": data.get("source_snapshot"), "source_sha256": data.get("source_sha256"),
              "reporting_currency": currency,
              "limitations": ["Coherent provider periods are not independent filing verification.",
                              "Missing claims remain unknown; scenario alternatives are not probability-weighted or certified valuation bounds."]}
    return AssembledEvidence(income, cashflow, balance, claims, caps, ledger, tuple(uncertain))
