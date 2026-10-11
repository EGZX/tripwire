"""Common-equity valuation with an auditable clean-surplus identity.

The automatic builder estimates accounting earning power and capital allocation;
it does not claim that book equity is regulatory capital. Missing regulatory
forecasts lower confidence, rather than making a common-equity DCF impossible.
All projected capital calls belong to the existing owners' investment strategy.
They are neither free funding nor an estimate of dilution to nonparticipants.
"""
from __future__ import annotations

from datetime import date
import math
from numbers import Real
from statistics import median

import pandas as pd

from . import statements as S, valuation as V
from .policy import get as policy

MODEL = "equity_residual_income"


def project(p: dict, years: int, rate: float, gt: float | None = None) -> dict:
    """Project aggregate common-owner distributions, including capital calls.

    E[t] = ROE[t] B[t-1]; D[t] = E[t] - (B[t] - B[t-1]).
    Common-owner distributions include repurchases net of common issuance.
    ``roe_terminal`` is a fixed economic assumption, never the discount rate
    silently substituted when the same path is valued under a different policy.
    Optional explicit ``roe_path``/``book_growth_path`` arrays have ``years`` items.
    """
    V.horizon(years)
    required = ("common_equity", "roe_start", "roe_terminal", "book_growth_start")
    if any(k not in p for k in required):
        raise V.InvalidModel("missing common-equity projection inputs")
    book, roe0, roe_t, growth0 = (p[k] for k in required)
    terminal_growth = p.get("terminal_growth", gt)
    V.finite(book, roe0, roe_t, growth0, terminal_growth, rate)
    if book <= 0:
        raise V.InvalidModel("nonpositive common book equity requires an explicit recapitalization or asset model")
    if rate <= -1 or min(growth0, terminal_growth) <= -1:
        raise V.InvalidModel("book growth and discount rate must exceed -100%")
    if roe_t < terminal_growth:
        raise V.InvalidModel("terminal common earnings cannot fund permanent book growth")

    def path(key, start, end):
        supplied = p.get(key)
        if supplied is None:
            return V.fade(start, end, years)
        if not isinstance(supplied, (list, tuple)) or len(supplied) != years:
            raise V.InvalidModel(f"{key} must contain one observation per forecast year")
        V.finite(*supplied)
        return list(supplied)

    roes = path("roe_path", roe0, roe_t)
    growths = path("book_growth_path", growth0, terminal_growth)
    if p.get("self_funded"):
        # Book can only grow as fast as earnings allow: no automatic capital calls from owners, so owner-funded
        # expansion at above-hurdle returns cannot create value by assumption.
        growths = [min(g, r) for g, r in zip(growths, roes)]
    if min(growths) <= -1:
        raise V.InvalidModel("projected common book equity cannot be exhausted or negative")
    opening = book
    ledger, flows, residual_income = [], [], []
    for year, (roe, growth) in enumerate(zip(roes, growths), 1):
        earnings = roe * book
        closing = book * (1 + growth)
        investment = closing - book
        distribution = earnings - investment
        residual = earnings - rate * book
        V.finite(earnings, investment, distribution, closing, residual)
        if closing <= 0:
            raise V.InvalidModel("common book underflows or is exhausted")
        ledger.append(dict(year=year, opening_common_equity=book, roe=roe,
                           common_earnings=earnings, book_growth=growth,
                           equity_reinvestment=investment, net_distribution=distribution,
                           capital_call=max(-distribution, 0.0), closing_common_equity=closing,
                           residual_income=residual))
        flows.append(distribution)
        residual_income.append(residual)
        book = closing
    # Mature phase: the existing book keeps earning roe_t; capital added for terminal growth earns
    # terminal_incremental_return. Default roe_t (all book earns roe_t, the declared-model convention); the
    # automatic builder sets the hurdle, so growth adds no value at maturity as in the operating model.
    incremental = p.get("terminal_incremental_return", roe_t)
    if terminal_growth > 0 and incremental < terminal_growth:
        raise V.InvalidModel("terminal incremental return cannot fund permanent book growth")
    terminal = roe_t * book * (1 - terminal_growth / incremental) if incremental else 0.0
    value = V.discount(flows, terminal, rate, terminal_growth)
    terminal_value = terminal / (rate - terminal_growth) if terminal else 0.0
    residual_value = opening + math.fsum(x / (1 + rate) ** t for t, x in enumerate(residual_income, 1))
    if math.isclose(incremental, rate) and rate > terminal_growth + 1e-12 and terminal:
        # Independent closed form: with new capital earning the hurdle, residual income is a level perpetuity
        # (roe_t - r) * B_N from year N+1 on. Algebraically equal to the dividend path, computed separately.
        residual_value += (roe_t - rate) * book / rate / (1 + rate) ** years
    else:
        residual_value += (terminal_value - book) / (1 + rate) ** years
    scale = max(abs(opening), abs(value), abs(residual_value))
    if abs(value - residual_value) > 1e-10 * scale:
        raise V.InvalidModel("common-equity distributions do not reconcile to residual income")
    calls = math.fsum(max(-x, 0.0) for x in flows)
    return dict(model=MODEL, claim="equity", cashflows=tuple(flows), terminal_cashflow=terminal,
                terminal_growth=terminal_growth, rate=rate, opening_distribution=0.0,
                terminal_value=terminal_value, value=value, distribution_value=value,
                residual_income_value=residual_value, reconciliation_error=value - residual_value,
                funding_required=calls, conditional_on_capital_calls=calls > 0,
                funding_basis="gross nominal future contributions by existing common owners",
                ledger=ledger)


def _number(x) -> bool:
    return isinstance(x, Real) and not isinstance(x, bool) and math.isfinite(x)


def _raw_series(frame, labels) -> pd.Series:
    """Resolve only explicitly named common-owner cash-flow rows."""
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    out = pd.Series(float("nan"), index=S.period_dates(frame.columns), dtype=float)
    for label in labels:
        if label not in frame.index:
            continue
        values = frame.loc[label]
        if not isinstance(values, pd.Series):
            continue
        values = pd.to_numeric(values, errors="coerce")
        values.index = S.period_dates(values.index)
        out = out.fillna(values)
    return S.finite_series(out).sort_index()


def _common_income(frame) -> pd.Series:
    # Generic Net Income can precede preferred/participating-security claims.
    # Do not silently use it as common-stockholder earnings.
    return _raw_series(frame, ["Net Income Common Stockholders", "Diluted NI Availto Com Stockholders"])


def add_dependencies(state, statement, fields, period, source):
    """Record semantic inputs and the exact accounting observations consumed."""
    selected = state.setdefault("dependencies", {}).setdefault(statement, set())
    if not isinstance(selected, set):
        selected = state["dependencies"][statement] = set(selected)
    selected.update(fields)
    if period is None:
        return
    kind = ("instant" if statement == "balance" else "ttm" if source.startswith("ttm_")
            else "quarter" if source.startswith("quarterly_") else "annual")
    records = state.setdefault("dependency_evidence", [])
    for field in fields:
        item = dict(statement=statement, field=field, period_end=pd.Timestamp(period).date().isoformat(),
                    period_type=kind, source=source)
        if item not in records:
            records.append(item)


def merge_dependencies(state, other):
    for statement, fields in other.get("dependencies", {}).items():
        state.setdefault("dependencies", {}).setdefault(statement, set()).update(fields)
    for item in other.get("dependency_evidence", []):
        if item not in state.setdefault("dependency_evidence", []):
            state["dependency_evidence"].append(item)


def dependency_contract(state):
    return dict(dependencies={key: sorted(value) for key, value in state.get("dependencies", {}).items()},
                dependency_evidence=state.get("dependency_evidence", []))


def book_dependencies(label):
    if label == "Common Stock Equity":
        return {"common_equity"}
    if label and " - " in label:
        return {"common_equity", "equity", "preferred"}
    return {"common_equity", "equity"} if label else set()


def common_book_label(frame, period):
    direct = _raw_series(frame, ["Common Stock Equity"]).get(period)
    if _number(direct):
        return "Common Stock Equity"
    values = S.row(frame, "common_equity")
    if values is not None and _number(values.get(period)):
        return "Stockholders Equity - Preferred Stock Equity"
    return None


def _utc_day(value) -> date | None:
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            return None
        return (stamp.tz_convert("UTC") if stamp.tzinfo is not None else stamp).date()
    except (TypeError, ValueError, OverflowError):
        return None


def _same_arithmetic(a, b) -> bool:
    """Floating arithmetic allowance only, not an assumed reporting tolerance."""
    return _number(a) and _number(b) and abs(a-b) <= 4 * math.ulp(max(abs(a), abs(b)))


def _same_eps(a, b) -> bool:
    """Reported EPS figures agree within cent rounding (half a cent) or 0.5%.

    Tight enough to separate GAAP from adjusted consensus bases, which typically differ by several percent."""
    return _number(a) and _number(b) and abs(a - b) <= max(0.006, 0.005 * max(abs(a), abs(b)))


def _eps_ties(eps, shares, income) -> bool:
    """EPS x weighted shares reproduces common income up to EPS rounding (half a cent per share) plus 0.5%."""
    return (_number(eps) and _number(shares) and _number(income)
            and abs(eps * shares - income) <= 0.006 * abs(shares) + 0.005 * abs(income))


def _eps_unit_ratio(data, selections, estimates, annual_date, shares, share_selection,
                    share_label, periods, evidence, warnings):
    """Prove estimate EPS units before multiplying by statement share counts.

    Optional ``data['earnings_estimate_units']`` metadata has these exact keys:
      source: source reference for the economic share-unit relationship;
      as_of: YYYY-MM-DD, matching the immutable fundamentals/estimate snapshot's
             fetched_at UTC date (not the current quote or the client's date);
      statement_period_end: selected denominator statement's YYYY-MM-DD;
      statement_share_field: exact denominator row, e.g. Diluted Average Shares;
      statement_shares_per_estimate_unit: positive factor (ordinary shares per
             ADS when the statement denominator is ordinary shares; may be a
             fraction for equivalent share classes or a split-adjusted unit);
      currency: both estimate EPS and reporting currency.

    Earnings = EPS * statement shares / factor. Neither quote prices nor the
    ratio of outstanding/weighted shares establishes economic rights. Without
    metadata, only exact matched-period common income/weighted shares/EPS and
    estimate yearAgoEps identities establish factor1. A known intervening split
    requires explicit normalization; no rounding or adjustment is invented.
    """
    record = dict(input="consensus_unit_reconciliation", source="Yahoo statement/estimate fields",
                  statement_share_field=share_label, statement_period_end=share_selection.date.date().isoformat(),
                  statement_shares=float(shares), status="rejected", estimate_rows=[])
    for period in periods:
        if period in estimates.index:
            row = {"period": period}
            for field in ("avg", "low", "high", "yearAgoEps", "currency"):
                if field in estimates.columns:
                    value = estimates.loc[period, field]
                    row[field] = float(value) if _number(value) else value if isinstance(value, str) else None
            record["estimate_rows"].append(row)

    def reject(reason):
        record["reason"] = reason
        evidence.append(record)
        warnings.append("EPS consensus unit evidence rejected: " + reason + "; reported common income remains the base.")
        return None

    currency = (data.get("info") or {}).get("financialCurrency")
    for period in periods:
        if period not in estimates.index or "currency" not in estimates.columns:
            return reject("annual estimate currency/window is unavailable")
        value = estimates.loc[period, "currency"]
        if not currency or not isinstance(value, str) or value != currency:
            return reject("estimate currency is not reconciled to reporting-currency earnings")
    proof = data.get("earnings_estimate_units")
    if proof is not None:
        if not isinstance(proof, dict):
            return reject("earnings_estimate_units must be a source-backed table")
        record["supplied_unit_evidence"] = {
            str(k): float(v) if _number(v) else v if isinstance(v, (str, bool)) or v is None else str(v)
            for k, v in proof.items()
        }
        if not isinstance(proof.get("source"), str) or not proof["source"].strip():
            return reject("share-unit normalization has no source")
        try:
            as_of = date.fromisoformat(str(proof["as_of"]))
            period_end = date.fromisoformat(str(proof["statement_period_end"]))
        except (KeyError, ValueError):
            return reject("share-unit normalization needs explicit observation and statement dates")
        if as_of != _utc_day(data.get("fetched_at")) or period_end != share_selection.date.date() or period_end > as_of:
            return reject("share-unit normalization does not match the immutable estimate snapshot and denominator period")
        if proof.get("currency") != currency or proof.get("statement_share_field") != share_label:
            return reject("share-unit normalization does not match the estimate currency and exact denominator field")
        factor = proof.get("statement_shares_per_estimate_unit")
        if not _number(factor) or factor <= 0:
            return reject("statement shares per estimate unit must be finite and positive")
        record.update(status="verified", basis="explicit dated source-backed unit normalization",
                      statement_shares_per_estimate_unit=float(factor))
        evidence.append(record)
        return float(factor)

    # An exact factor1 identity is intentionally narrower than a guessed ADR,
    # class or split ratio. Adjusted EPS versus GAAP income may not tie.
    eps_label = {"Diluted Average Shares": "Diluted EPS", "Basic Average Shares": "Basic EPS"}.get(share_label)
    if eps_label is None:
        return reject("ordinary outstanding shares do not establish the EPS weighted-share unit")
    fetched_day = _utc_day(data.get("fetched_at"))
    if fetched_day is None:
        return reject("estimate snapshot date unavailable for share-unit reconciliation")
    split = (data.get("info") or {}).get("lastSplitDate")
    if _number(split) and split > 0:
        try:
            split_day = pd.Timestamp(split, unit="s", tz="UTC").date()
        except (ValueError, OverflowError):
            return reject("split observation is invalid")
        if split_day > annual_date.date():
            return reject("a split after the annual EPS reference requires explicit denominator normalization")
    annual = data.get("income_stmt")
    annual_eps = _raw_series(annual, [eps_label]).get(annual_date)
    annual_shares = _raw_series(annual, [share_label]).get(annual_date)
    annual_income = _common_income(annual).get(annual_date)
    current_eps = _raw_series(share_selection.frame, [eps_label]).get(share_selection.date)
    current_income = _common_income(share_selection.frame).get(share_selection.date)
    if "0y" not in estimates.index or "yearAgoEps" not in estimates.columns:
        return reject("no matched annual estimate EPS reference; equal currency alone does not identify share units")
    reference_eps = estimates.loc["0y", "yearAgoEps"]
    observed = (annual_eps, annual_shares, annual_income, current_eps, current_income, reference_eps)
    if not all(_number(x) for x in observed) or annual_shares <= 0 or annual_eps == 0 or current_eps == 0:
        return reject("reported annual/current common EPS and weighted-share evidence is incomplete")
    if not _same_eps(annual_eps, reference_eps):
        return reject("estimate year-ago EPS differs from reported EPS: the consensus is on another (e.g. adjusted) basis")
    if not (_eps_ties(annual_eps, annual_shares, annual_income) and _eps_ties(current_eps, shares, current_income)):
        return reject("reported common earnings, EPS and weighted shares do not tie within EPS rounding; no share ratio is inferred")
    if "+1y" in periods:
        if "avg" not in estimates.columns or not _same_eps(estimates.loc["+1y", "yearAgoEps"], estimates.loc["0y", "avg"]):
            return reject("next-year EPS reference is not on the same current-year estimate basis")
    record.update(status="verified", basis="matched-period common-income/weighted-share/EPS identities within EPS rounding",
                  annual_period_end=annual_date.date().isoformat(), annual_common_income=float(annual_income),
                  annual_weighted_shares=float(annual_shares), annual_eps=float(annual_eps),
                  estimate_year_ago_eps=float(reference_eps), current_common_income=float(current_income),
                  current_eps=float(current_eps), statement_shares_per_estimate_unit=1.0)
    evidence.append(record)
    return 1.0


def _estimate(data, selections, current, evidence, assumptions, warnings) -> dict:
    """Annual EPS windows overlap the next twelve months; no FY/TTM confusion."""
    estimates = data.get("earnings_estimate")
    income, balance = selections["income"], selections["balance"]
    result = {"avg": current, "basis": "current common income run rate"}
    if not isinstance(estimates, pd.DataFrame) or estimates.empty:
        assumptions.append("No usable annual EPS consensus: current common earnings continue into forecast year1.")
        return result
    shares = S.latest(income.frame, "shares_avg")
    share_basis = "selected income statement diluted/basic weighted-average common shares"
    share_selection, share_label = income, S.latest_label(income.frame, "shares_avg")
    if not _number(shares) or shares <= 0:
        # A four-quarter TTM carries no weighted shares (they are not additive): use the latest quarter, whose own
        # EPS and income then form the current identity.
        q = data.get("quarterly_income_stmt")
        if isinstance(q, pd.DataFrame) and not q.empty:
            try:
                q_date = S.statement_date(q)
            except (TypeError, ValueError):
                q_date = None
            if q_date is not None and income.date is not None and abs((q_date - income.date).days) <= 45:
                q_sel = S.Selection(q, "quarterly_income_stmt", q_date, q_date, ())
                q_shares = S.latest(q, "shares_avg")
                if _number(q_shares) and q_shares > 0:
                    shares, share_selection, share_label = q_shares, q_sel, S.latest_label(q, "shares_avg")
                    share_basis = "latest-quarter diluted/basic weighted-average common shares"
    if not _number(shares) or shares <= 0:
        shares = S.latest(balance.frame, "shares_out")
        share_basis = "selected balance-sheet ordinary shares"
        share_selection, share_label = balance, S.latest_label(balance.frame, "shares_out")
    annual_date = S.statement_date(data.get("income_stmt"))
    if annual_date is None or balance.date is None or not _number(shares) or shares <= 0:
        warnings.append("EPS consensus retained as context: fiscal window or common-share denominator is unavailable.")
        return result
    fy_end = annual_date + pd.DateOffset(years=1)
    if fy_end <= balance.date or fy_end > balance.date + pd.DateOffset(years=1):
        warnings.append("EPS consensus not used: annual fiscal anchor does not identify the provider's current fiscal year.")
        return result
    target_end = balance.date + pd.DateOffset(years=1)
    current_weight = (fy_end - balance.date).days / (target_end - balance.date).days
    weights = {"0y": current_weight, "+1y": 1.0 - current_weight}
    factor = _eps_unit_ratio(data, selections, estimates, annual_date, shares, share_selection,
                             share_label, [period for period, weight in weights.items() if weight], evidence, warnings)
    if factor is None:
        assumptions.append("Unverified listing EPS is context only; next-year common earning power uses reported common income.")
        return result
    for field in ("avg", "low", "high"):
        parts = []
        for period, weight in weights.items():
            if not weight:
                continue
            if period not in estimates.index or field not in estimates.columns:
                break
            reporting_currency = (data.get("info") or {}).get("financialCurrency")
            estimate_currency = estimates.loc[period, "currency"] if "currency" in estimates.columns else None
            if not reporting_currency or not isinstance(estimate_currency, str) or estimate_currency != reporting_currency:
                warnings.append("EPS consensus not used: its currency/quoted-share basis is not reconciled to reporting-currency common earnings.")
                break
            if "numberOfAnalysts" in estimates.columns:
                count = estimates.loc[period, "numberOfAnalysts"]
                if _number(count) and count <= 0:
                    break
            value = estimates.loc[period, field]
            if not _number(value):
                break
            parts.append(dict(period=period, eps=float(value), fraction=weight))
        else:
            earnings = math.fsum(x["eps"] * x["fraction"] for x in parts) * (shares / factor)
            result[field] = earnings
            evidence.append(dict(input=f"consensus_{field}_common_earnings", value=earnings,
                                 source="Yahoo earnings_estimate", fiscal_year_end=fy_end.date().isoformat(),
                                 forecast_end=target_end.date().isoformat(), eps_components=parts,
                                 assumed_shares=shares, share_basis=share_basis,
                                 denominator_statement="income" if share_selection is income else "balance",
                                 denominator_field="shares_avg" if share_selection is income else "shares_out",
                                 denominator_period_end=share_selection.date.date().isoformat(),
                                 denominator_source=share_selection.source,
                                 statement_shares_per_estimate_unit=factor,
                                 equivalent_estimate_units=shares / factor))
    if any(x["input"] == "consensus_avg_common_earnings" for x in evidence):
        result["basis"] = "annual EPS consensus interpolated to next twelve months"
        assumptions.append("EPS consensus uses constant current diluted/common shares and uniform earnings within each fiscal year; forecast EPS accounting adjustments are not independently reconciled.")
        warnings.append("Analyst EPS is an estimate with an unverified GAAP/IFRS adjustment basis, not reported common income.")
    else:
        warnings.append("Incomplete annual EPS windows: year1 uses current common earnings, not a partial-year estimate.")
    return result


def forecast_income(data: dict, selections: dict[str, S.Selection]) -> dict:
    """Common-income estimate plus its fiscal/units evidence for any equity model.

    Returns ``avg`` and optional ``low``/``high`` in reporting-currency units,
    together with ``basis``, ``evidence``, ``assumptions`` and ``warnings``.
    Missing/unaligned or unverified-unit EPS consensus leaves the reported-income
    run rate explicit. See _eps_unit_ratio for the source metadata contract.
    """
    income = selections["income"]
    current = _common_income(income.frame).get(income.date, float("nan"))
    if not _number(current):
        raise V.InvalidModel("no explicitly reported common-stockholder income")
    evidence, assumptions, warnings = [], [], []
    estimate = _estimate(data, selections, current, evidence, assumptions, warnings)
    dependencies = {}
    add_dependencies(dependencies, "income", {"net_income"}, income.date, income.source)
    consumed = [e for e in evidence if e.get("input", "").startswith("consensus_") and "denominator_field" in e]
    for item in consumed:
        add_dependencies(dependencies, item["denominator_statement"], {item["denominator_field"]},
                         item["denominator_period_end"], item["denominator_source"])
    if consumed:
        for item in evidence:
            if item.get("basis") == "exact matched-period common-income/weighted-share/EPS identities":
                add_dependencies(dependencies, "income", {"net_income", "shares_avg"},
                                 item["annual_period_end"], "income_stmt")
    return dict(**estimate, **dependency_contract(dependencies), evidence=evidence, assumptions=assumptions,
                warnings=list(dict.fromkeys(warnings)))


def build(data: dict, selections: dict[str, S.Selection], context: dict) -> dict:
    """Build reusable financial scenarios from ordinary issuer financial fields.

    Context supplies ``family``, ``years``, ``hurdle``, ``terminal_growth`` and
    ``cfg``. Regulatory diagnostics may be supplied separately as ``regulatory``
    with available_capital, capital_base, required_capital_ratio and source.
    No ticker, country, or desired valuation outcome controls these equations.
    """
    income, balance = selections["income"], selections["balance"]
    book = S.latest(balance.frame, "common_equity")
    common_income = _common_income(income.frame).get(income.date, float("nan"))
    if not _number(common_income):
        raise V.InvalidModel("no explicitly reported common-stockholder income")
    rate, gt, years = context["hurdle"], context["terminal_growth"], context["years"]
    V.finite(book, common_income, rate, gt)
    V.horizon(years)
    if book <= 0:
        raise V.InvalidModel("no positive common book equity; automatic clean-surplus valuation needs an explicit recapitalization")
    if gt <= -1 or rate <= max(gt, -1):
        raise V.InvalidModel("financial terminal policy requires rate above terminal growth")
    evidence = [dict(input="opening_common_equity", value=book, source=balance.source,
                     period_end=balance.date.date().isoformat(), field="Common Stock Equity"),
                dict(input="current_common_income", value=common_income, source=income.source,
                     period_end=income.date.date().isoformat(), field=S.latest_label(income.frame, "net_income"))]
    assumptions = ["Forecast common equity follows clean surplus: earnings less net owner distributions equals the change in book equity.",
                   "Mature return on existing book: hurdle plus half of today's excess return (at most 5 points; stress: hurdle). Capital added for terminal growth earns the hurdle, so growth itself adds no value at maturity.",
                   "Book growth is self-funded: it cannot exceed the return on book, so no owner capital calls are assumed.",
                   "Base, stress and upside share one capital trajectory; only evidenced starting earning power varies, so lower profitability is paid for through lower distributions or capital calls.",
                   "Future OCI, noncash share issuance and accounting revaluations are zero in the projection; historical discrepancies are disclosed rather than forced into earnings."]
    warnings = ["Book common equity is not regulatory capital; regulatory buffers, transfer restrictions and credit/catastrophe stress are not identified by this accounting model."]
    dependencies = {}
    add_dependencies(dependencies, "income", {"net_income"}, income.date, income.source)
    add_dependencies(dependencies, "balance", book_dependencies(common_book_label(balance.frame,balance.date)), balance.date, balance.source)
    annual_income = _common_income(data.get("income_stmt"))
    annual_book = S.annual(data.get("balance_sheet"), "common_equity")
    dividends = _raw_series(data.get("cashflow"), ["Common Stock Dividend Paid"])
    net_issue = _raw_series(data.get("cashflow"), ["Net Common Stock Issuance"])
    issuance_fields = {"net_common_issuance"}
    if net_issue.empty:
        issued = _raw_series(data.get("cashflow"), ["Common Stock Issuance"])
        repurchased = _raw_series(data.get("cashflow"), ["Common Stock Payments"])
        net_issue = S.finite_series(issued + repurchased)
        issuance_fields = {"common_issuance", "common_repurchases"}
    returns, retained_growth, book_growth = [], [], []
    for end in annual_book.index:
        window = S.annual_capital_window(annual_book, end)
        if not window:
            continue
        start, _ = window
        beginning, closing = float(annual_book[start]), float(annual_book[end])
        for period in (start,end):
            add_dependencies(dependencies, "balance", book_dependencies(common_book_label(data.get("balance_sheet"),period)), period, "balance_sheet")
        observed_growth = closing / beginning - 1
        book_growth.append(observed_growth)
        ni = annual_income.get(end, float("nan"))
        item = dict(input="historical_common_book", source="Yahoo annual statements",
                    period_start=start.date().isoformat(), period_end=end.date().isoformat(),
                    opening_book=beginning, closing_book=closing, observed_book_growth=observed_growth)
        if _number(ni):
            add_dependencies(dependencies, "income", {"net_income"}, end, "income_stmt")
            returns.append(float(ni / beginning))
            item.update(common_income=float(ni), return_on_opening_common_book=float(ni / beginning))
            div, issuance = dividends.get(end), net_issue.get(end)
            if _number(div) and _number(issuance) and div <= 0:
                distributions = -div - issuance
                reinvestment = ni - distributions
                retained_rate = reinvestment / beginning
                item.update(net_common_distributions=float(distributions), implied_retained_book_growth=float(retained_rate),
                            clean_surplus_gap=float(closing - beginning - reinvestment))
                if retained_rate > -1:
                    retained_growth.append(float(retained_rate))
                    add_dependencies(dependencies, "cashflow", {"common_dividends", *issuance_fields}, end, "cashflow")
                else:
                    item["retained_growth_status"] = "exhausts opening common book; excluded from positive-book continuation estimate"
                    warnings.append("A historical retained-earnings path exhausts common book; a recapitalization/OCI bridge is not identified.")
            elif _number(div) and div > 0:
                warnings.append("Positive common-dividend cash-flow sign is unresolved; not interpreted as a payout.")
        evidence.append(item)
    if retained_growth:
        growth = float(median(retained_growth))
        growth_basis = "median annual earnings less net common cash distributions / opening common book"
    elif book_growth:
        growth = float(median(book_growth))
        growth_basis = "median observed annual common-book change"
        warnings.append("Incomplete common cash-payout history: book-growth estimate includes OCI, dilution and other equity movements.")
    else:
        growth = gt
        growth_basis = "terminal-growth continuation policy because common-book history is unavailable"
        warnings.append("No aligned annual common-book history; initial capital growth is the disclosed terminal-growth policy.")
    assumptions.append("Initial common-book growth uses " + growth_basis + "; it fades linearly to terminal growth.")
    evidence.append(dict(input="book_growth_start", value=growth, basis=growth_basis,
                         retained_growth_observations=retained_growth, observed_book_growth_observations=book_growth))
    estimate = forecast_income(data, selections)
    merge_dependencies(dependencies, estimate)
    evidence.extend(estimate["evidence"])
    assumptions.extend(estimate["assumptions"])
    warnings.extend(estimate["warnings"])
    # Base return on AVERAGE book over the trailing year (earnings accrue on the opening as well as the closing
    # book), from clean income (continuing operations before after-tax unusual items) unless consensus is used.
    opening_book = math.nan
    for frame_name in ("quarterly_balance_sheet", "balance_sheet"):
        series = S.row(data.get(frame_name), "common_equity")
        if series is None or balance.date is None:
            continue
        near = [d for d in series.index if abs((d - (balance.date - pd.DateOffset(years=1))).days) <= 60 and _number(series[d])]
        if near:
            opening_book = float(series[min(near, key=lambda d: abs((d - (balance.date - pd.DateOffset(years=1))).days))])
            break
    average_book = (opening_book + book) / 2 if _number(opening_book) and opening_book > 0 else book
    earnings = estimate["avg"]
    if estimate.get("basis") != "annual EPS consensus interpolated to next twelve months":
        clean = S.clean_latest(income.frame)
        if _number(clean):
            earnings = clean
    base_roe = earnings / average_book
    evidence.append(dict(input="base_return_on_average_book", value=base_roe, earnings=earnings, opening_book=opening_book,
                         closing_book=book, basis="clean common income / average common book" if earnings is not estimate["avg"] else estimate["basis"]))
    candidates = [base_roe, common_income / book, *returns]
    if "low" in estimate:
        candidates.append(estimate["low"] / book)
    if "high" in estimate:
        candidates.append(estimate["high"] / book)
    pol = policy()
    # Stress is a real downside (credit/catastrophe year): below base by the policy factor, with reduced book growth.
    stress_roe = min(min(candidates), pol.stress_return_factor * base_roe) if base_roe > 0 else min(candidates)
    starts = {"base": base_roe, "stress": stress_roe, "upside": max(candidates)}
    assumptions.append(f"Stress starts at the lower of the worst observed/estimated return and {pol.stress_return_factor:.0%} of base, with {pol.stress_growth_factor:.0%} of the base book growth; upside spans observed returns and analyst high estimates. Scenarios, not probability quantiles or guaranteed bounds.")
    excess = lambda roe: max(0.0, roe - rate)
    # A durable franchise keeps part of today's excess return, capped over the hurdle (policy franchise share/cap).
    # Stress assumes it is competed away entirely.
    terminals = {"base": rate + min(pol.franchise_share * excess(base_roe), pol.franchise_cap), "stress": rate,
                 "upside": rate + min(pol.upside_franchise_share * excess(starts["upside"]), pol.upside_franchise_cap)}
    if not retained_growth and book_growth:
        # Observed book change includes share issuance; without payout evidence retention is unknown, so start at
        # most at the policy retention share of the base return.
        if growth > pol.unknown_payout_retention * base_roe:
            growth = max(gt, pol.unknown_payout_retention * base_roe)
            warnings.append(f"Payout history unavailable: starting book growth limited to {pol.unknown_payout_retention:.0%} of the base return on book (issuance excluded).")
    growths = {"base": growth, "stress": pol.stress_growth_factor * growth if growth > gt else growth, "upside": growth}
    if not returns:
        warnings.append("No annual earnings matched to positive opening common book; empirical return scenarios have limited history.")
    plans = {}
    for name, initial_return in starts.items():
        params = dict(common_equity=book, roe_start=float(initial_return), roe_terminal=terminals[name],
                      book_growth_start=growths[name], terminal_growth=gt, self_funded=True, terminal_incremental_return=rate,
                      earnings_basis=estimate["basis"] if name == "base" else "empirical earnings scenario")
        projected = project(params, years, rate, gt)
        if projected["funding_required"] > 0:
            warnings.append(f"{name}: forecast includes {projected['funding_required']:.12g} reporting-currency units of additional common-owner capital; those calls reduce value.")
        plans[name] = params
    regulatory = context.get("regulatory")
    if isinstance(regulatory, dict) and regulatory.get("source"):
        evidence.append(dict(input="regulatory_context", **regulatory))
        keys = ("available_capital", "capital_base", "required_capital_ratio")
        if all(_number(regulatory.get(k)) for k in keys):
            excess = regulatory[keys[0]] - regulatory[keys[1]] * regulatory[keys[2]]
            evidence.append(dict(input="reported_regulatory_surplus", value=excess, source=regulatory["source"]))
            if excess < 0:
                warnings.append("Sourced regulatory capital is below the supplied requirement; common-book value is conditional on resolving that shortfall.")
    return dict(model=MODEL, family=context.get("family", "financial_other"), **plans, **dependency_contract(dependencies),
                evidence=evidence, assumptions=assumptions, warnings=list(dict.fromkeys(warnings)),
                quality="automatic common-equity accounting model; estimates and capital calls explicit")
