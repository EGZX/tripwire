"""Independent accounting/reference cases for sector-specific production paths."""
import copy
import math
from datetime import date
import pytest
from screener import models as M, valuation as V, inputs
from test_metrics import operating,run,CFG

def test_operating_zero_growth_reconciles_cash_taxes_and_maintenance():
    p=dict(revenue=1000,growth=0,margin_start=.2,margin_terminal=.2,tax_rate=.25,
           sales_to_capital=2,maintenance_net_capex=30,funding_capacity=0)
    result=M.operating(p,10,.1,0)
    assert result.cashflows==(120.0,)*10
    assert result.value==pytest.approx(1200)
    p['maintenance_net_capex']=50
    assert M.operating(p,10,.1,0).value==pytest.approx(1000)

def test_preprofit_forecast_checks_funding_and_does_not_invent_tax_refund():
    p=dict(revenue=100,growth=.1,margin_start=-.2,margin_terminal=.15,tax_rate=.25,
           sales_to_capital=2,maintenance_net_capex=0,funding_capacity=0)
    with pytest.raises(V.InvalidModel,match='funding'):M.operating(p,5,.1,.02)
    p['funding_capacity']=100
    out=M.operating(p,5,.1,.02)
    assert out.cashflows[0]==pytest.approx(-22-5)
    assert out.terminal_cashflow>0

def capital_params():
    return dict(net_income=10,growth=0,capital_growth=0,available_capital=100,capital_base=1000,
                required_capital_ratio=.1,payout_limit=1,releasable_excess=0)

@pytest.mark.parametrize('family',['bank','insurer','financial'])
def test_regulated_capital_distributions_zero_growth_and_capital_shortfall(family):
    p=capital_params()
    assert M.capital_distributions(p,10,.1,0,family).value==pytest.approx(100)
    p['available_capital']=99
    with pytest.raises(V.InvalidModel,match='shortfall'):M.capital_distributions(p,10,.1,0,family)

def test_required_bank_capital_is_funded_separately_from_earnings_growth():
    p=capital_params();p.update(growth=.05,capital_growth=.1)
    out=M.capital_distributions(p,1,.1,.02,'bank')
    # Income10.5, additional required equity10 -> distribution .5.
    assert out.cashflows[0]==pytest.approx(.5)
    assert out.terminal_cashflow==pytest.approx(10.5*1.02-110*.02)

def test_reit_maintenance_and_leasing_are_deducted_once_from_common_ffo():
    p=dict(ffo_common=100,recurring_capex=20,leasing_costs=10,growth=0,return_on_new_equity=.12)
    out=M.reit(p,10,.1,0)
    assert out.claim=='equity' and out.value==pytest.approx(700)
    del p['recurring_capex']
    with pytest.raises(V.InvalidModel,match='recurring_capex'):M.reit(p,10,.1,0)

def test_regulated_lag_pipeline_is_cleared_before_terminal_value():
    p=dict(rate_base=100,rate_base_history=[100,100,100],growth=.4,earned_after_tax_return=.1,regulatory_lag_years=2,funding_capacity=30)
    out=M.regulated(p,1,.1,.02)
    assert out.cashflows==pytest.approx([-30,7.2,7.144])
    assert out.terminal_cashflow==pytest.approx(11.08688)
    reference=-30/1.1+7.2/1.1**2+7.144/1.1**3+11.08688/.08/1.1**3
    assert out.value==pytest.approx(reference)
    p['funding_capacity']=0
    with pytest.raises(V.InvalidModel,match='funding'):M.regulated(p,1,.1,.02)

def test_finite_project_closure_cost_and_finite_negative_rate_domain():
    p=dict(cashflows=[10],salvage_value=0,closure_cost=100,funding_capacity=0)
    with pytest.raises(V.InvalidModel,match='funding'):M.project(p,.1)
    p=dict(cashflows=[10,10],salvage_value=5,closure_cost=2,funding_capacity=0)
    out=M.evaluate('finite_project',p,10,-.01,0)
    assert out.value==pytest.approx(10/.99+13/.99**2)
    assert out.terminal_pv==0

def test_nav_is_attributable_assets_less_all_documented_claims():
    p=dict(attributable_assets=1000,parent_debt=100,other_claims=50,tax_leakage=25,holding_cost_pv=75)
    assert M.asset_nav(p)==750

def issuer(model,base,stress=None):
    return dict(model=model,source='controlled synthetic accounting example',as_of='2026-06-30',period_end='2026-06-30',
                currency='USD',country_risk_premium=0,discount_rate=.1,discount_rate_basis='cost_of_equity',
                base=base,stress=stress if stress is not None else copy.deepcopy(base))

def test_bank_model_integrates_without_requiring_ebit_or_debt():
    d=operating();d['info']['industry']='Banks - Regional'
    d['ttm_income_stmt']=d['ttm_income_stmt'].drop(index='Operating Income')
    for key in ('balance_sheet','quarterly_balance_sheet'):
        d[key]=d[key].drop(index=['Total Debt','Cash Cash Equivalents And Short Term Investments'])
    p=capital_params();p['terminal_growth']=0
    cfg={**CFG,'issuer_inputs':{'TEST':issuer('bank_capital',p)}}
    m=run(d,cfg)
    assert m['not_rankable']==''
    assert m['value_reasonable']==pytest.approx(100)
    assert m['entry_ceiling']==pytest.approx(100)
    assert m['value_at_policy_hurdle']==pytest.approx(10/.095)
    assert m['model']=='bank_capital' and m['implied_return_claim']=='equity'

def test_missing_provider_classification_requires_a_sourced_family():
    d=operating();d['info']['industry']=''
    assert 'business classification unavailable' in run(d)['not_rankable']
    p=capital_params();p['terminal_growth']=0
    source=issuer('bank_capital',p);source['model_family']='bank'
    m=run(d,{**CFG,'issuer_inputs':{'TEST':source}})
    assert m['class']=='financial' and m['value_reasonable']==pytest.approx(100)

def test_broker_can_use_equity_capital_distributions_without_a_firm_debt_bridge():
    d=operating();d['info']['industry']='Capital Markets'
    p=capital_params();p['terminal_growth']=0
    m=run(d,{**CFG,'issuer_inputs':{'TEST':issuer('financial_capital',p)}})
    assert m['model_family']=='financial_other' and m['implied_return_claim']=='equity'
    assert m['not_rankable']=='' and m['value_reasonable']==pytest.approx(100)

def test_mortgage_reit_cannot_inherit_property_ffo_math():
    d=operating();d['info']['industry']='REIT - Mortgage'
    m=run(d)
    assert m['model_family']=='mortgage_reit' and 'requires explicit sector inputs' in m['not_rankable']
    p=dict(ffo_common=100,recurring_capex=20,leasing_costs=10,growth=0,return_on_new_equity=.12)
    m=run(d,{**CFG,'issuer_inputs':{'TEST':issuer('reit_affo',p)}})
    assert 'model does not match issuer economics' in m['not_rankable']

def test_cashflow_claim_and_discount_rate_must_match():
    p=dict(revenue=1000,growth=0,margin_start=.2,margin_terminal=.2,tax_rate=.25,sales_to_capital=2,
           maintenance_net_capex=30,funding_capacity=0,terminal_growth=0)
    cfg={**CFG,'issuer_inputs':{'TEST':issuer('operating_forecast',p)}}
    m=run(operating(),cfg)
    assert 'does not match cash-flow claim' in m['not_rankable']
    cfg['issuer_inputs']['TEST']['discount_rate_basis']='wacc'
    m=run(operating(),cfg)
    assert m['value_reasonable']==pytest.approx(1200-50)
    assert m['entry_ceiling']==pytest.approx(1150)
    assert m['value_at_policy_hurdle']==pytest.approx(120/.095-50)

def test_ceiling_respects_both_risk_value_and_the_purchase_hurdle():
    d=operating();d['info']['industry']='Banks - Regional'
    p=capital_params();p['terminal_growth']=0
    source=issuer('bank_capital',p)
    m=run(d,{**CFG,'issuer_inputs':{'TEST':source}})
    assert m['entry_ceiling']==pytest.approx(100,rel=1e-12) and m['decision_rate']==.1
    source['discount_rate']=.05
    m=run(d,{**CFG,'issuer_inputs':{'TEST':source}})
    assert m['value_reasonable']==pytest.approx(200)
    assert m['entry_ceiling']==pytest.approx(10/.095)
    assert m['decision_rate']==.095

def test_sourced_cost_of_capital_is_not_ignored_by_the_operating_proxy():
    d=operating();baseline=run(d)
    source={'source':'controlled cost-of-capital estimate','as_of':'2026-06-30','period_end':'2026-06-30',
            'currency':'USD','discount_rate':.2,'discount_rate_basis':'wacc'}
    m=run(d,{**CFG,'issuer_inputs':{'TEST':source}})
    assert m['discount_rate']==.2 and m['decision_rate']==.2
    assert m['entry_ceiling']<baseline['entry_ceiling']
    assert m['entry_ceiling']<=m['value_reasonable']
    source['discount_rate_basis']='cost_of_equity'
    assert 'cannot use an equity discount rate' in run(d,{**CFG,'issuer_inputs':{'TEST':source}})['not_rankable']

def test_complete_sourced_facts_work_when_provider_statements_are_unavailable():
    import pandas as pd
    d=operating()
    for key in ('income_stmt','ttm_income_stmt','balance_sheet','quarterly_balance_sheet'):
        d[key]=pd.DataFrame()
    p=dict(revenue=1000,growth=0,margin_start=.2,margin_terminal=.2,tax_rate=.25,sales_to_capital=2,
           maintenance_net_capex=30,funding_capacity=0,terminal_growth=0)
    source=issuer('operating_forecast',p)
    source.update(discount_rate_basis='wacc',equity=600,debt=100,cash=50,minority=0,preferred=0,other_claims=0)
    m=run(d,{**CFG,'issuer_inputs':{'TEST':source}})
    assert m['not_rankable']=='' and m['value_reasonable']==pytest.approx(1150)

def test_preferred_claims_reduce_common_equity_value_dollar_for_dollar():
    d=operating();first=run(d)
    for key in ('balance_sheet','quarterly_balance_sheet'):
        d[key].loc['Preferred Stock Equity']=500
    second=run(d)
    assert first['value_reasonable']-second['value_reasonable']==pytest.approx(500)

def test_input_dates_and_scenario_discount_basis_are_validated():
    p=issuer('bank_capital',capital_params())
    assert not inputs.validate_issuer('TEST',p)
    p['as_of']='2099-01-01';p['period_end']='2100-01-01'
    assert any('observation' in x for x in inputs.validate_issuer('TEST',p))
    p=issuer('bank_capital',capital_params());del p['discount_rate'];del p['discount_rate_basis']
    p['base']['discount_rate']=.1
    assert any('discount_rate_basis' in x for x in inputs.validate_issuer('TEST',p))
    p=issuer('bank_capital',capital_params());del p['discount_rate']
    assert any('intrinsic rate must be supplied' in x for x in inputs.validate_issuer('TEST',p))
