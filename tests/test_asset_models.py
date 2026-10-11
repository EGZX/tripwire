"""Independent equity identities and unfamiliar property/utility business cases."""
from copy import deepcopy

import pandas as pd
import pytest

from screener import asset_models as A, financial_models as F, statements as S, valuation as V


CONTEXT = {'family':'reit','years':8,'hurdle':.10,'terminal_growth':0.0,'cfg':{}}
DATES = pd.to_datetime(['2025-12-31','2024-12-31','2023-12-31','2022-12-31'])


def frame(rows,dates=DATES):
    return pd.DataFrame(rows,index=dates).T.astype(float)


def company(symbol='UNSEEN-OFFICE', income=10.0, book=100.0):
    inc=frame({'Net Income Common Stockholders':[income]*4,'Net Income':[income]*4,
               'Net Income Including Noncontrolling Interests':[income]*4,'Total Revenue':[100.]*4})
    bal=frame({'Common Stock Equity':[book]*4,'Stockholders Equity':[book]*4,
               'Total Equity Gross Minority Interest':[book]*4,'Total Debt':[60.]*4,
               'Cash And Cash Equivalents':[5.]*4})
    cf=frame({'Operating Cash Flow':[15.]*4,'Depreciation And Amortization':[5.]*4,
              'Capital Expenditure':[-8.]*4,'Investing Cash Flow':[-8.]*4,
              'Stock Based Compensation':[0.]*4})
    return {'symbol':symbol,'info':{'financialCurrency':'USD','industry':'REIT - Office'},
            'income_stmt':inc,'balance_sheet':bal,'cashflow':cf}


def selections(data):
    return {
        'income':S.select([('income_stmt',data['income_stmt'])],('net_income',)),
        'balance':S.select([('balance_sheet',data['balance_sheet'])],('common_equity',),instant=True),
        'cashflow':S.select([('cashflow',data['cashflow'])],('ocf',)),
    }


def project(plan,case='base',rate=.10):
    return F.project(plan[case],CONTEXT['years'],rate)


@pytest.mark.parametrize('builder',[A.build_property,A.build_utility])
def test_constant_earning_power_at_cost_of_equity_equals_common_book(builder):
    data=company()
    plan=builder(data,selections(data),CONTEXT)
    result=project(plan)
    # Independent perpetuity: common earnings10 / equity discount10% =100.
    assert plan['claim']=='equity'
    assert result['value']==pytest.approx(100.)
    assert result['residual_income_value']==pytest.approx(100.)
    assert result['funding_required']==0


@pytest.mark.parametrize('builder',[A.build_property,A.build_utility])
@pytest.mark.parametrize('scale',[1e-9,1e-3,1e6,1e12])
def test_monetary_units_scale_values_and_funding_not_returns(builder,scale):
    data=company(income=-5.)
    base=builder(data,selections(data),CONTEXT)
    scaled=deepcopy(data)
    for table in ('income_stmt','balance_sheet','cashflow'):
        scaled[table]*=scale
    other=builder(scaled,selections(scaled),CONTEXT)
    for case in ('base','stress','upside'):
        assert other[case]['roe_start']==pytest.approx(base[case]['roe_start'])
        assert other[case]['book_growth_start']==pytest.approx(base[case]['book_growth_start'])
        left,right=project(base,case),project(other,case)
        assert right['value']==pytest.approx(left['value']*scale)
        assert right['funding_required']==pytest.approx(left['funding_required']*scale)


@pytest.mark.parametrize('builder',[A.build_property,A.build_utility])
def test_after_interest_common_equity_is_not_reduced_by_debt_again(builder):
    data=company()
    plan=builder(data,selections(data),CONTEXT)
    data['balance_sheet'].loc['Total Debt']=60000.
    # Holding already-after-interest income and common capital constant is a
    # claim-scope invariant, not an assertion borrowing has no economic cost.
    more_debt=builder(data,selections(data),CONTEXT)
    assert project(more_debt)['value']==pytest.approx(project(plan)['value'])
    assert any('Do not subtract debt' in x for x in plan['assumptions'])


def reported(**values):
    return {'source':'issuer annual supplement, common-holder reconciliation',
            'period_end':'2025-12-31','period_type':'annual','currency':'USD',**values}


def test_reported_common_affo_is_used_without_second_maintenance_deduction():
    data=company()
    data['reported_property']=reported(affo_common=20.,recurring_capex=3.,leasing_costs=2.)
    plan=A.build_property(data,selections(data),CONTEXT)
    assert plan['base']['roe_start']==pytest.approx(.20)
    assert 'reported AFFO' in plan['base']['earnings_basis']
    # D&A never becomes an additional FFO addback or a second AFFO deduction.
    data['cashflow'].loc['Depreciation And Amortization']=500.
    other=A.build_property(data,selections(data),CONTEXT)
    assert other['base']['roe_start']==pytest.approx(.20)


def test_ffo_requires_complete_common_holder_maintenance_and_rent_bridge():
    data=company()
    data['reported_property']=reported(ffo_common=25.,recurring_capex=3.,leasing_costs=1.,
                                       straight_line_rent_adjustment=1.)
    plan=A.build_property(data,selections(data),CONTEXT)
    assert plan['base']['roe_start']==pytest.approx(.20)
    del data['reported_property']['straight_line_rent_adjustment']
    incomplete=A.build_property(data,selections(data),CONTEXT)
    assert incomplete['base']['roe_start']==pytest.approx(.10)
    assert incomplete['upside']['roe_start']==pytest.approx(.25)
    assert any('lacks a complete' in x for x in incomplete['warnings'])


def test_consolidated_depreciation_is_not_relabeled_reported_ffo():
    data=company()
    plan=A.build_property(data,selections(data),CONTEXT)
    assert all(x['field']!='ffo_common' for x in plan['evidence'])
    assert 'not reported FFO/AFFO' in plan['base']['earnings_basis']
    cash=[x for x in plan['evidence'] if x['field']=='cash_earning_power_estimate']
    assert cash and all(x['kind']=='estimated' for x in cash)
    assert cash[0]['value']==pytest.approx(15.-5.)
    assert plan['upside']['roe_start']==pytest.approx(.15)
    assert any('zero-additional-upkeep boundary' in x for x in plan['assumptions'])
    assert set(plan['dependencies']['cashflow'])=={'ocf','da','sbc','capex','investing_cashflow'}
    assert 'total_equity' in plan['dependencies']['balance']
    assert 'debt' not in plan['dependencies']['balance']


def test_missing_sector_details_use_labeled_income_estimate_not_blanket_rejection():
    data=company()
    data['cashflow']=pd.DataFrame()
    property_plan=A.build_property(data,selections(data),CONTEXT)
    utility_plan=A.build_utility(data,selections(data),CONTEXT)
    assert property_plan['base']['roe_start']==pytest.approx(.1)
    assert utility_plan['base']['roe_start']==pytest.approx(.1)
    assert any('No complete reported' in x for x in property_plan['warnings'])
    assert any('regulatory lag' in x for x in utility_plan['warnings'])


def test_negative_common_earnings_are_priced_as_funding_not_clipped_to_zero():
    data=company(income=-20.)
    plan=A.build_utility(data,selections(data),CONTEXT)
    result=project(plan)
    assert plan['base']['roe_start']==pytest.approx(-.2)
    assert result['cashflows'][0]==pytest.approx(-20.)
    assert result['funding_required']>20
    assert result['conditional_on_capital_calls']
    assert any('liquidity or market access is not guaranteed' in x for x in plan['warnings'])


def test_terminal_economics_do_not_change_with_discount_sensitivity():
    data=company(income=15.)
    plan=A.build_utility(data,selections(data),CONTEXT)
    cheap,dear=project(plan,rate=.08),project(plan,rate=.14)
    assert cheap['cashflows']==dear['cashflows']
    assert cheap['terminal_cashflow']==dear['terminal_cashflow']
    assert cheap['value']>dear['value']
    assert plan['base']['roe_terminal']==.10


def test_stress_and_upside_bound_base_without_reordering_cashflows():
    data=company()
    data['cashflow'].loc['Investing Cash Flow']=-40.
    plan=A.build_property(data,selections(data),CONTEXT)
    values=[project(plan,c)['value'] for c in ('stress','base','upside')]
    assert values==sorted(values)
    assert project(plan,'stress')['funding_required']>0
    assert any('not statistical confidence' in x for x in plan['assumptions'])


@pytest.mark.parametrize('builder',[A.build_property,A.build_utility])
def test_held_out_business_labels_and_arbitrary_ticker_do_not_change_algorithm(builder):
    data=company('HOLDOUT-INTERNATIONAL-MIXED')
    data['info'].update(industry='Diversified property, power generation and infrastructure')
    before=builder(data,selections(data),CONTEXT)
    data['symbol']='ANOTHER-UNSEEN-LISTING'
    after=builder(data,selections(data),CONTEXT)
    assert before==after


@pytest.mark.parametrize('change',[{'currency':'EUR'},{'period_end':'2024-12-31'},
                                 {'period_type':'quarter'},{'affo_common':float('nan')},
                                 {'recurring_capex':-1.}])
def test_reported_property_evidence_rejects_unit_period_and_domain_conflicts(change):
    data=company();data['reported_property']=reported(affo_common=20.,**change) if 'affo_common' not in change else reported(**change)
    with pytest.raises(V.InvalidModel):
        A.build_property(data,selections(data),CONTEXT)


@pytest.mark.parametrize('builder',[A.build_property,A.build_utility])
def test_nonpositive_equity_or_unsustainable_terminal_cannot_become_valid(builder):
    data=company(book=-1.)
    with pytest.raises(V.InvalidModel,match='positive common-book'):
        builder(data,selections(data),CONTEXT)
    data=company()
    with pytest.raises(V.InvalidModel,match='terminal'):
        builder(data,selections(data),{**CONTEXT,'terminal_growth':.10})


@pytest.mark.parametrize('symbol,builder',[('O',A.build_property),('VICI',A.build_property),
                                         ('DUK',A.build_utility),('IBE.MC',A.build_utility)])
def test_frozen_held_out_issuer_evidence_when_locally_available(symbol,builder):
    import pickle
    from pathlib import Path
    cache=Path(__file__).resolve().parents[1]/'data/general-models-2026-10-11/heldout/cache'/f'{symbol}.pkl'
    if not cache.exists():
        pytest.skip('separate live-validation issuer snapshot is not distributed')
    with cache.open('rb') as stream:
        data=pickle.load(stream)
    chosen={role:S.select([(first,data.get(first)),(second,data.get(second))],required,instant=role=='balance')
            for role,first,second,required in (
                ('income','ttm_income_stmt','income_stmt',('net_income',)),
                ('balance','quarterly_balance_sheet','balance_sheet',('common_equity',)),
                ('cashflow','ttm_cashflow','cashflow',('ocf',)))}
    plan=builder(data,chosen,CONTEXT)
    values=[]
    for case in ('stress','base','upside'):
        out=project(plan,case)
        # Independent cash-flow sum, including every negative contribution.
        expected=sum(cf/1.1**t for t,cf in enumerate(out['cashflows'],1))
        expected+=out['terminal_cashflow']/.1/1.1**CONTEXT['years']
        assert out['value']==pytest.approx(expected)
        assert out['claim']=='equity'
        values.append(out['value'])
    assert values==sorted(values)
    assert plan['evidence'] and plan['assumptions']
