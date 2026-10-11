"""Automatic common-equity estimates for property and utility businesses.

These builders deliberately do not identify gross assets as regulated rate base
or turn consolidated depreciation into reported common-holder FFO. They return
transparent, data-anchored scenario assumptions for financial_models.project.
Cash-return scenarios use economic equity, not a claim that GAAP clean surplus
holds after replacing net income with a cash measure. All resulting values are
COMMON EQUITY: the caller must not deduct debt or preferred claims again.

Methods: Nareit AFFO glossary (maintenance/leasing/noncash-rent adjustments;
issuer definitions vary), https://www.reit.com/glossary/adjusted-funds-operations-affo
and the equity reinvestment identity in Damodaran's FCFE framework,
https://pages.stern.nyu.edu/~adamodar/New_Home_Page/lectures/fcfe.html.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any

import pandas as pd

from . import financial_models as F, statements as S, valuation as V


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _at(frame, labels, period):
    """Read a single period, rejecting ambiguous duplicate provider rows."""
    if frame is None or frame.empty or period is None:
        return None, None
    dates = S.period_dates(frame.columns)
    positions = [i for i, d in enumerate(dates) if d == period]
    if len(positions) != 1:
        return None, None
    for label in labels:
        if label not in frame.index:
            continue
        selected = frame.loc[label]
        if isinstance(selected, pd.DataFrame):
            continue
        value = selected.iloc[positions[0]]
        if pd.notna(value) and _number(float(value)):
            return float(value), label
    return None, None


def _selection(data, selections, name):
    selected = selections.get(name)
    if selected is not None and selected.frame is not None and not selected.frame.empty:
        return selected.frame, selected.date, selected.source
    key = {'income': 'income_stmt', 'balance': 'balance_sheet', 'cashflow': 'cashflow'}[name]
    frame = data.get(key)
    return frame, S.statement_date(frame), key


def _fact(evidence, field, value, period, source, kind='reported', **extra):
    evidence.append({'field': field, 'value': value, 'period_end': str(period.date()) if period is not None else None,
                     'source': source, 'kind': kind, **extra})


def _book(frame, period):
    common, label = _at(frame, ['Common Stock Equity'], period)
    if common is not None:
        return common, label, None
    equity, label = _at(frame, ['Stockholders Equity'], period)
    preferred, _ = _at(frame, ['Preferred Stock Equity', 'Preferred Stock'], period)
    if equity is not None:
        if preferred is not None:
            return equity-preferred, 'Stockholders Equity - Preferred Stock Equity', None
        return equity, label, 'Preferred capital unavailable: parent book equity is used as an estimated common-equity base.'
    return None, None, None


def _common_income(frame, period):
    income, label = _at(frame, ['Net Income Common Stockholders'], period)
    if income is not None:
        return income, label, None
    income, label = _at(frame, ['Net Income'], period)
    preferred, _ = _at(frame, ['Preferred Stock Dividends'], period)
    if income is not None and preferred is not None:
        return income-preferred, 'Net Income - Preferred Stock Dividends', None
    return income, label, ('Preferred dividends unavailable: parent net income is a common-income estimate.'
                           if income is not None else None)


def _book_dependencies(label):
    return F.book_dependencies(label)


def _history(data, selected, name):
    """Annual observations plus a distinct latest selection, with actual dates."""
    annual_key = {'income': 'income_stmt', 'balance': 'balance_sheet', 'cashflow': 'cashflow'}[name]
    annual = data.get(annual_key)
    observations = []
    if annual is not None and not annual.empty:
        for period in sorted(set(S.period_dates(annual.columns))):
            observations.append((annual, period, annual_key))
    frame, period, source = selected
    if period is not None:
        # Prefer the selected source at its own date; do not count it twice.
        observations = [x for x in observations if x[1] != period]
        observations.append((frame, period, source))
    return observations


def _prepare(data, selections, context):
    evidence, assumptions, warnings = [], [], []
    dependencies = {'income':set(),'balance':set(),'cashflow':set()}
    balance = _selection(data, selections, 'balance')
    income = _selection(data, selections, 'income')
    cashflow = _selection(data, selections, 'cashflow')
    book, label, warning = _book(balance[0], balance[1])
    if not _number(book) or book <= 0:
        raise V.InvalidModel('automatic equity model requires a positive common-book capital base; use an explicit cash-flow or asset model')
    if warning:
        warnings.append(warning)
    _fact(evidence, 'common_equity', book, balance[1], balance[2]+': '+str(label),
          'estimated' if warning else 'reported')
    dependency_state={'dependencies':dependencies,'dependency_evidence':[]}
    F.add_dependencies(dependency_state,'balance',_book_dependencies(label),balance[1],balance[2])
    books = {}
    for frame, period, source in _history(data, balance, 'balance'):
        b, book_label, _ = _book(frame, period)
        if _number(b) and b > 0:
            books[period] = b
            F.add_dependencies(dependency_state,'balance',_book_dependencies(book_label),period,source)
    income_returns = []
    for frame, period, source in _history(data, income, 'income'):
        ni, ni_label, ni_warning = _common_income(frame, period)
        if not _number(ni):
            continue
        if period in books:
            denominator = S.annual_average_capital(pd.Series(books), period)
            basis = 'average common equity over matching annual period'
            if not _number(denominator):
                denominator, basis = books[period], 'closing common equity; opening annual capital unavailable'
        elif period == income[1]:
            denominator, basis = book, 'latest common equity; income/capital dates may differ'
        else:
            continue
        _fact(evidence, 'common_income', ni, period, source+': '+str(ni_label),
              'estimated' if ni_warning else 'reported', capital_denominator=denominator, capital_basis=basis)
        if ni_warning:
            warnings.append(ni_warning)
        income_returns.append(ni/denominator)
        F.add_dependencies(dependency_state,'income',{'net_income'},period,source)
        if ni_label == 'Net Income - Preferred Stock Dividends':
            F.add_dependencies(dependency_state,'income',{'preferred_dividends'},period,source)
    # Book growth is an observation, not a claim that issuance/OCI is organic growth.
    annual_books = [(p,b) for p,b in books.items()
                    if data.get('balance_sheet') is not None and p in S.period_dates(data['balance_sheet'].columns)]
    annual_books.sort()
    if len(annual_books) >= 2:
        first, last = annual_books[0], annual_books[-1]
        elapsed = (last[0]-first[0]).days/365.25
        growth = math.expm1(math.log(last[1]/first[1])/elapsed) if elapsed > 0 else 0.0
        _fact(evidence, 'book_growth_cagr', growth, last[0], 'annual common-equity history', 'derived',
              start_period=str(first[0].date()), years=elapsed)
        assumptions.append('Observed annual common-book CAGR is the initial capital-growth estimate; issuance, buybacks, acquisitions and OCI are not separated.')
    else:
        growth = 0.0
        assumptions.append('No comparable annual capital history: initial capital growth is assumed zero, then fades to terminal growth.')
        warnings.append('Capital-growth estimation has no annual history.')
    if not income_returns:
        warnings.append('Common net-income return could not be measured; cash-return evidence must support the estimate.')
    return {'book': book, 'books': books, 'income_returns': income_returns, 'growth': growth,
            'balance': balance, 'income': income, 'cashflow': cashflow,
            'evidence': evidence, 'assumptions': assumptions, 'warnings': warnings,
            'dependencies':dependencies,'dependency_evidence':dependency_state['dependency_evidence']}


def _cash_returns(data, state, *, include_upper=False):
    """Economic earnings alternatives, never relabeled reported FFO or AFFO.

    CFO already includes interest/tax under the provider's accounting policy.
    Common-book share is an explicitly estimated ownership allocation. D&A is a
    maintenance estimate; all observed investment outflow is a high-maintenance
    stress, not a statement that reported development expenditure is recurring.
    Neither net borrowing nor current cash balances are added to the value.
    """
    cash_returns, stress_returns, before_maintenance_returns = [], [], []
    annual_balance = data.get('balance_sheet')
    for frame, period, source in _history(data, state['cashflow'], 'cashflow'):
        ocf, label = _at(frame, ['Operating Cash Flow'], period)
        if ocf is None:
            continue
        balance = state['balance'][0] if period == state['balance'][1] else annual_balance
        common, common_label, _ = _book(balance, period)
        total, _ = _at(balance, ['Total Equity Gross Minority Interest'], period)
        if common is None or common <= 0:
            continue
        if total is not None and total >= common and total > 0:
            allocation = common/total
        else:
            allocation = 1.0
            state['warnings'].append('Cash-flow ownership allocation unavailable: full parent ownership is an upper-scope estimate, not verified common-holder cash.')
        sbc, _ = _at(frame, ['Stock Based Compensation'], period)
        if sbc is None:
            sbc = 0.0
            state['warnings'].append('Cash-return estimate has no disclosed stock-compensation adjustment; it is not verified distributable cash.')
        da, _ = _at(frame, ['Depreciation And Amortization', 'Depreciation Amortization Depletion', 'Reconciled Depreciation'], period)
        capex, _ = _at(frame, ['Capital Expenditure'], period)
        investment, _ = _at(frame, ['Investing Cash Flow', 'Cash Flow From Continuing Investing Activities'], period)
        upkeep = [max(-v,0.0) for v in (capex,investment) if v is not None]
        if (da is not None and da >= 0) or upkeep or include_upper:
            F.add_dependencies(state,'cashflow',{'ocf'},period,source)
            balance_source=state['balance'][2] if period==state['balance'][1] else 'balance_sheet'
            F.add_dependencies(state,'balance',_book_dependencies(common_label),period,balance_source)
            if total is not None and total >= common and total > 0:
                F.add_dependencies(state,'balance',{'total_equity'},period,balance_source)
            if _at(frame,['Stock Based Compensation'],period)[0] is not None:
                F.add_dependencies(state,'cashflow',{'sbc'},period,source)
        if da is not None and da >= 0:
            F.add_dependencies(state,'cashflow',{'da'},period,source)
            cash_income = (ocf-sbc-da)*allocation
            cash_returns.append(cash_income/common)
            _fact(state['evidence'], 'cash_earning_power_estimate', cash_income, period, source,
                  'estimated', formula='(CFO - SBC - D&A maintenance proxy) x common-book / total-equity',
                  ocf=ocf, sbc=sbc, maintenance_proxy=da, common_allocation=allocation)
        else:
            state['warnings'].append('Maintenance depreciation unavailable: cash flow is diagnostic only; no zero-maintenance base case is inferred.')
        # A ceiling on cash earning power is useful when property upkeep is
        # unknown, but is never silently used as the central AFFO estimate.
        before_maintenance_returns.append((ocf-sbc)*allocation/common)
        if upkeep:
            if capex is not None:
                F.add_dependencies(state,'cashflow',{'capex'},period,source)
            if investment is not None:
                F.add_dependencies(state,'cashflow',{'investing_cashflow'},period,source)
            stressed = (ocf-sbc-max(upkeep))*allocation/common
            stress_returns.append(stressed)
            _fact(state['evidence'], 'high_maintenance_cash_return', stressed, period, source, 'estimated',
                  formula='(CFO - SBC - max(gross capex, net investing outflow)) x ownership allocation / common equity',
                  maintenance_proxy=max(upkeep), common_allocation=allocation)
        _fact(state['evidence'], 'operating_cashflow', ocf, period, source+': '+str(label))
    if cash_returns or stress_returns:
        state['assumptions'].append('Cash-flow scenarios allocate consolidated cash by common-book/total-equity share; this is a capital-allocation estimate, not an observed ownership cash distribution.')
        state['assumptions'].append('D&A approximates sustaining investment only in the cash-return alternative. Stress treats all observed investment outflow as sustaining; future growth capital is then separately funded.')
        state['warnings'].append('Cash-flow classification, maintenance/development split and minority distributions are not independently normalized.')
    return cash_returns, stress_returns, before_maintenance_returns


def _reported_property(data, state):
    """Optional filing connector contract: dated common-holder monetary facts.

    data['reported_property'] contains source, period_end, currency and one of
    affo_common/ffo_common. FFO becomes AFFO only when recurring_capex,
    leasing_costs AND straight_line_rent_adjustment are explicitly supplied.
    All values are annual/TTM base-currency amounts, never per-share figures.
    """
    facts = data.get('reported_property')
    if facts is None:
        return None
    if not isinstance(facts, dict) or not isinstance(facts.get('source'),str) or not facts['source'].strip():
        raise V.InvalidModel('reported property metrics require source and dated common-holder scope')
    if facts.get('period_type') not in ('annual','ttm'):
        raise V.InvalidModel('reported property metrics must identify annual or ttm duration')
    try:
        period = pd.Timestamp(facts['period_end'])
        if pd.isna(period) or period.normalize() != state['income'][1]:
            raise ValueError()
    except (ValueError,TypeError,KeyError):
        raise V.InvalidModel('reported property metrics must match the selected income period') from None
    if facts.get('currency') != (data.get('info') or {}).get('financialCurrency'):
        raise V.InvalidModel('reported property metrics currency differs from statements')
    for name in ('affo_common','ffo_common','recurring_capex','leasing_costs','straight_line_rent_adjustment'):
        if name in facts and not _number(facts[name]):
            raise V.InvalidModel('reported property metrics must be finite base-currency values')
    if any(facts.get(k,0)<0 for k in ('recurring_capex','leasing_costs')):
        raise V.InvalidModel('property sustaining costs cannot be negative')
    if 'affo_common' in facts:
        earning, label, kind = facts['affo_common'], 'reported AFFO available to common holders', 'reported'
        state['assumptions'].append('Reported common AFFO is already after its disclosed maintenance adjustments: maintenance is not deducted again; incremental growth still requires equity investment.')
    elif 'ffo_common' in facts:
        costs = ('recurring_capex','leasing_costs','straight_line_rent_adjustment')
        if all(k in facts for k in costs):
            earning = facts['ffo_common']-sum(facts[k] for k in costs)
            label, kind = 'FFO less disclosed maintenance, leasing and noncash-rent adjustments', 'derived'
        else:
            _fact(state['evidence'],'ffo_common',facts['ffo_common'],period,facts['source'])
            state['warnings'].append('Reported common FFO lacks a complete sustaining-capex/leasing/noncash-rent bridge; it is retained as an upper earning-power scenario, not reported AFFO.')
            return {'upper_return': facts['ffo_common']/state['book']}
    else:
        raise V.InvalidModel('reported property metrics contain neither common AFFO nor common FFO')
    _fact(state['evidence'],'common_property_earning_power',earning,period,facts['source'],kind,label=label)
    return {'central_return':earning/state['book'],'label':label}


def _plan(state, context, central, returns, low_returns, label):
    if not _number(central):
        raise V.InvalidModel('no finite common earnings or cash-return evidence for automatic equity estimate')
    years, rate, gt = context['years'], context['hurdle'], context['terminal_growth']
    V.horizon(years); V.finite(rate,gt)
    if rate <= max(gt,-1) or gt <= -1:
        raise V.InvalidModel('automatic equity terminal assumption requires hurdle above terminal growth and -100%')
    observations = [x for x in [central,*returns,*low_returns] if _number(x)]
    if state['growth'] <= -1:
        raise V.InvalidModel('common capital growth must exceed -100%')
    params = {'common_equity':state['book'],'roe_terminal':rate,
              'book_growth_start':state['growth'],'terminal_growth':gt,
              'earnings_basis':label}
    state['assumptions'].extend([
        'Initial common-equity capital growth fades over the explicit horizon to terminal growth; changes in capital are funded by retained earnings or explicit negative distributions.',
        'Terminal return on economic common equity equals the build-time hurdle; it remains fixed when discount-rate sensitivity is evaluated.',
        'Scenario endpoints are observed accounting/cash-proxy extremes, not statistical confidence intervals or probabilities.',
        'The model values common equity after financing expenses. Do not subtract debt, preferred capital or minorities again and do not add the balance-sheet cash balance again.',
    ])
    if min(observations) < state['growth']:
        state['warnings'].append('Some scenarios require additional common-equity funding. Negative distributions must remain in valuation; liquidity or market access is not guaranteed.')
    return {'model':'equity_residual_income','claim':'equity','family':context.get('family'),
            'quality':'automatic consolidated common-equity estimate; sector cash and capital assumptions explicit',
            'base':{**params,'roe_start':central},
            'stress':{**params,'roe_start':min(observations)},
            'upside':{**params,'roe_start':max(observations)},
            'evidence':state['evidence'],
            **F.dependency_contract(state),
            'assumptions':list(dict.fromkeys(state['assumptions'])),
            'warnings':list(dict.fromkeys(state['warnings']))}


def build_property(data: dict, selections: dict, context: dict) -> dict:
    """Build an equity-property estimate for any issuer, without symbol rules."""
    state = _prepare(data,selections,context)
    cash, stress, cash_upper = _cash_returns(data,state,include_upper=True)
    reported = _reported_property(data,state)
    returns = [*state['income_returns'],*cash]
    if reported and 'central_return' in reported:
        central, label = reported['central_return'], reported['label']+'; economic equity rollforward proxy'
    else:
        if not returns:
            raise V.InvalidModel('property estimate needs common income or cash-return evidence, not only unbridged FFO')
        central, label = median(returns), 'observed common-income and maintenance-adjusted cash-return estimates; not reported FFO/AFFO'
        state['warnings'].append('No complete reported common-holder AFFO bridge: property value is an economic common-equity estimate, not an FFO/AFFO valuation.')
    if reported and 'upper_return' in reported:
        returns.append(reported['upper_return'])
    returns.extend(cash_upper)
    if cash_upper:
        state['assumptions'].append('Property upside includes common-allocated CFO before additional maintenance: a zero-additional-upkeep boundary, not evidence that upkeep costs are zero. This widens the missing-maintenance range without calling it reported AFFO.')
    state['assumptions'].append('Book common equity is an investment-scale proxy, not current property NAV; accounting income, capitalized property costs, gains and common cash allocations can diverge.')
    state['assumptions'].append('The estimate covers the consolidated parent group, which can own services, development, investments and other businesses; it does not assert pure rental-property exposure.')
    return _plan(state,context,central,returns,stress,label)


def build_utility(data: dict, selections: dict, context: dict) -> dict:
    """Value regulated/merchant utility groups through consolidated common equity."""
    state = _prepare(data,selections,context)
    cash, stress, _ = _cash_returns(data,state)
    observed = state['income_returns'] or cash
    if not observed:
        raise V.InvalidModel('utility estimate needs common income or cash-return evidence')
    state['assumptions'].append('The group is modeled through parent common equity, including merchant and regulated activities; total assets and book equity are not assumed to be regulated rate base.')
    state['warnings'].append('Allowed returns, rate-base vintages, regulatory lag, tax-equity allocation and merchant exposures remain unresolved; the observed common-equity scenarios are estimates.')
    central = median(observed)
    label = ('reported common net income and book-equity reinvestment estimate' if state['income_returns']
             else 'maintenance-adjusted common cash-return estimate; economic equity rollforward proxy')
    alternatives = [*state['income_returns'],*cash]
    if all(k in selections for k in ('income','balance')):
        try:
            estimate = F.forecast_income(data,selections)
        except V.InvalidModel as exc:
            state['warnings'].append('Common EPS consensus not used: '+str(exc))
        else:
            state['evidence'].extend(estimate['evidence'])
            state['assumptions'].extend(estimate['assumptions'])
            state['warnings'].extend(estimate['warnings'])
            if estimate['basis']=='annual EPS consensus interpolated to next twelve months':
                central = estimate['avg']/state['book']
                label = estimate['basis']+'; consolidated common-equity model'
                alternatives.extend(estimate[k]/state['book'] for k in ('low','high') if k in estimate)
                F.merge_dependencies(state,estimate)
    return _plan(state,context,central,alternatives,stress,label)


def build_recovery(data, selections, context):
    """Quantify book-recovery hypotheses; do not invent project/franchise value."""
    frame=selections['balance'].frame;period=selections['balance'].date
    book,label,warning=_book(frame,period)
    assets=S.latest(frame,'assets');cash=S.latest(frame,'cash')
    if book is None or not _number(book) or not _number(assets) or assets<=0 or book>assets:
        raise V.InvalidModel('asset recovery requires compatible total assets and common/parent equity')
    recoverable=min(assets,max(0.,cash)) if _number(cash) else 0.
    params=dict(common_equity=book,total_assets=assets)
    dependencies={}
    F.add_dependencies(dependencies,'balance',_book_dependencies(label)|{'assets','cash'},period,selections['balance'].source)
    return {'model':'asset_recovery','claim':'equity','family':context.get('family'),
        'quality':'partial balance-sheet recovery scenarios; operating/project option value unquantified',
        'base':{**params,'asset_recovery_value':assets},
        'stress':{**params,'asset_recovery_value':recoverable},
        'upside':{**params,'asset_recovery_value':assets},
        **F.dependency_contract(dependencies),
        'evidence':[{'field':'book_equity','value':book,'source':label},{'field':'assets','value':assets,'source':selections['balance'].source}],
        'assumptions':['Base: reported asset carrying amounts are recoverable and reported claims settle at carrying amounts.',
            'Stress: only disclosed cash-like assets recover; zero recovery is a stress hypothesis when cash is unobserved, not a fact that cash is zero.',
            'No additional liquidation tax/cost or off-balance claim is estimated. These hypotheses need refinement before investment use.',
            'The highest quantified scenario is not an upper bound: mineral rights, development projects, recovery options and future operations are not valued.'],
        'warnings':(['Common book estimate: '+warning] if warning else [])+['Unvalued operating or project assets; partial asset-recovery model, not a full company valuation.']}


def project_recovery(p,years,rate,gt=None):
    book,assets,recovered=(p[k] for k in ('common_equity','total_assets','asset_recovery_value'))
    V.finite(book,assets,recovered,rate);V.horizon(years)
    if assets<=0 or book>assets or not 0<=recovered<=assets or rate<=-1:
        raise V.InvalidModel('invalid recovery assets, book claims or discount domain')
    residual=book-assets+recovered
    value=max(0.,residual) # Common owners do not personally repay liquidation deficits.
    return {'cashflows':tuple([0.]*years),'terminal_cashflow':0.,'terminal_growth':0.,'claim':'equity',
        'opening_distribution':value,'funding_required':0.,'ledger':[],
        'value':value,'book_claim_residual':residual,'basis':'asset and book-claim recovery hypothesis; not an observed liquidation quote'}
