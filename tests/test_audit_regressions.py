"""Counterexamples from the independent valuation audit, not output-parity tests."""
import math
import json
import copy
import pandas as pd
import pytest
from scipy.optimize import minimize_scalar
from screener import valuation as V, statements as S, market, scoring
from test_metrics import operating,run,CFG,YEARS,TTM,frame

def test_perpetual_capital_calls_are_rejected_and_new_terminal_is_feasible():
    with pytest.raises(V.InvalidModel,match="funding"):
        V.present_value(10,-.05,.1,.03,10,.02,.02)
    d=operating()
    for key in ('balance_sheet','quarterly_balance_sheet'):
        d[key].loc['Stockholders Equity']=[8400.0]*len(d[key].columns)
        d[key].loc['Total Debt']=[50.0]*len(d[key].columns)
        d[key].loc['Cash Cash Equivalents And Short Term Investments']=[50.0]*len(d[key].columns)
    d['revenue_estimate']=pd.DataFrame({'growth':[-.9]},index=['+1y'])
    m=run(d,{**CFG,'valuation':{**CFG['valuation'],'growth_floor':-.9}})
    # The new operating model estimates a distinct mature capital productivity;
    # it need not perpetuate the low current book return from the old model.
    parameters=json.loads(m['scenario_parameters'])['base']
    result=json.loads(m['scenario_results'])['base']
    assert parameters['terminal_equity_return']>=parameters['terminal_growth']
    assert result['terminal_cashflow']>=0
    from screener import operating_models
    with pytest.raises(V.InvalidModel):
        operating_models.project({**parameters,'terminal_equity_return':.02},10,.095,.03)

def test_large_decline_is_not_clipped_into_a_favorable_growth_assumption():
    d=operating();d['revenue_estimate']=pd.DataFrame({'growth':[-.9]},index=['+1y'])
    m=run(d)
    parameters=json.loads(m['scenario_parameters'])['base']
    result=json.loads(m['scenario_results'])['base']
    assert parameters['growth']==-.9
    assert result['ledger'][0]['revenue']==pytest.approx(1050*.1)
    assert result['opening_distribution']==0  # contraction is not cash liquidation
    assert m['entry_ceiling']<run(operating())['entry_ceiling']

def test_nonfinite_growth_does_not_become_a_valid_forecast_after_clipping():
    d=operating();d['revenue_estimate']=pd.DataFrame({'growth':[float('inf')]},index=['+1y'])
    m=run(d)
    reference=run(operating(revenue_estimate=pd.DataFrame()))
    parameters=json.loads(m['scenario_parameters'])
    for case in ('base','stress','upside'):
        assert math.isfinite(parameters[case]['growth'])
        assert parameters[case]['growth']==pytest.approx(json.loads(reference['scenario_parameters'])[case]['growth'])
    assert any('nonfinite' in warning.lower() for warning in json.loads(m['model_uncertainties']))

def test_contraction_does_not_create_unverified_liquidation_cash():
    ds,term=V.distributions(10,-.05,0,10,.02,.02)
    assert ds[0]==pytest.approx(9.5)
    assert all(d>=0 for d in ds) and term>=0

@pytest.mark.parametrize('scale',[1e-12,1e-9,1,1e9,1e12])
def test_two_growth_roots_and_currency_unit_invariance(scale):
    result=V.solve_growth(10*scale,95.2*scale,.1,.03,10,.085,.085)
    assert result.status=='nonunique'
    assert result.value is None
    assert result.roots==pytest.approx([.02908008135281423,.0816578240004915],abs=1e-9)
    assert all(abs(x)<1e-7*scale for x in result.residuals)

def test_first_year_opening_capital_domain_matches_inverse():
    price=V.present_value(10,.105,.1,.03,10,.1,.1)
    result=V.solve_growth(10,price,.1,.03,10,.1,.1)
    assert result.status=='solved' and result.value==pytest.approx(.105)

def test_near_turning_point_is_not_reported_as_three_solutions():
    opt=minimize_scalar(lambda g:-V.present_value(10,g,.1,.03,10,.085,.085),bounds=(0,.085),method='bounded')
    for delta in (-1e-10,0,1e-10):
        r=V.solve_growth(10,-opt.fun+delta,.1,.03,10,.085,.085)
        assert r.status=='weakly_identified'
        assert r.value is None

@pytest.mark.parametrize('args',[(10,.1,.1,.03,0,.2,.2),(10,.1,.1,.03,10,0,0),
                                (10,.1,.1,.03,10,-.2,-.2),(10,.1,.03,.03,10,.2,.2),
                                (10,float('nan'),.1,.03,10,.2,.2)])
def test_invalid_kernel_inputs_fail_explicitly(args):
    with pytest.raises(V.InvalidModel):V.present_value(*args)

def test_inverse_invalid_domains_and_one_year_terminal_return():
    assert V.solve_growth(10,100,.1,-1.1,10,.1,.1).status=='invalid'
    assert V.solve_return(None,100,.1,.03,10,.2,.2).status=='invalid'
    ds,terminal=V.distributions(10,.03,.03,1,.1,.03)
    assert terminal==0 and ds[0]==pytest.approx(7.21)

def test_finite_payout_inverse_does_not_apply_an_irrelevant_gordon_bound():
    price=V.present_value(10,.03,.01,.03,1,.1,.03)
    assert V.solve_return(10,price,.03,.03,1,.1,.03).value==pytest.approx(.01)
    assert V.solve_growth(10,price,.01,.03,1,.1,.03).value==pytest.approx(.03)

@pytest.mark.parametrize('years',[35,50])
@pytest.mark.parametrize('growth',[-.8,-.3,0,.3])
def test_long_horizon_monotone_inverse_uses_a_direct_solve(years,growth):
    price=V.present_value(10,growth,.1,.03,years)
    solution=V.solve_growth(10,price,.1,.03,years)
    assert solution.status=='solved' and solution.value==pytest.approx(growth,abs=1e-9)

def test_financial_ttm_does_not_require_ebit():
    d=operating();d['info']['industry']='Banks - Regional'
    d['ttm_income_stmt']=d['ttm_income_stmt'].drop(index='Operating Income')
    m=run(d)
    assert m['model']=='equity_residual_income'
    assert m['net_income']==pytest.approx(159.6)
    evidence=json.loads(m['model_evidence'])
    assert next(x['value'] for x in evidence if x.get('input')=='current_common_income')==pytest.approx(159.6)
    assert 'income: annual' not in m['flags']
    assert json.loads(m['statement_periods'])['income']['period_end']=='2026-06-30'

def test_same_period_selection_and_truthful_backdating():
    d=operating();q=d['quarterly_balance_sheet'].copy()
    q[pd.Timestamp('2026-03-31')]=q.iloc[:,0]
    q.loc['Total Debt',pd.Timestamp('2026-06-30')]=math.nan
    q.loc['Cash Cash Equivalents And Short Term Investments',pd.Timestamp('2026-06-30')]=900
    d['quarterly_balance_sheet']=q
    m=run(d)
    # The common-equity model does not need debt and keeps the current book.
    assert math.isnan(m['net_debt_fc'])
    assert json.loads(m['statement_periods'])['balance']['period_end']=='2026-06-30'
    # A firm-value caller requiring a complete bridge must select one older
    # coherent period, never combine old debt with the new900 cash balance.
    firm=S.select([('quarterly_balance_sheet',q)],('equity','debt','cash'),instant=True)
    assert firm.date==pd.Timestamp('2026-03-31') and firm.backdated
    assert S.latest(firm.frame,'debt')-S.latest(firm.frame,'cash')==50
    assert math.isnan(S.latest(q,'debt'))  # no per-field backfill

def test_missing_equity_and_historical_debt_are_not_zero():
    d=operating()
    for key in ('balance_sheet','quarterly_balance_sheet'):
        d[key]=d[key].drop(index='Stockholders Equity')
    m=run(d)
    assert math.isnan(S.latest(d['quarterly_balance_sheet'],'common_equity'))
    assumptions=json.loads(m['model_assumptions'])
    intensity=next(x for x in assumptions if isinstance(x,dict) and x.get('parameter')=='equity_to_sales')
    assert intensity['value']>0 and 'industry' in intensity['basis']
    assert 'unavailable' in intensity['basis'] or 'nonpositive' in intensity['basis']
    d=operating();d['balance_sheet'].loc['Total Debt']=math.nan
    m=run(d)
    assert S.row(d['balance_sheet'],'debt') is None
    assert S.invested_capital(d['balance_sheet']).empty
    # Debt is not subtracted a second time from common earnings. Its absence
    # must not increase value by silently reducing invested capital to zero.
    assert m['entry_ceiling']==pytest.approx(run(operating())['entry_ceiling'])

def test_minor_unit_market_cap_and_identity_ambiguity():
    base={'regularMarketPrice':1000,'marketCap':100000,'sharesOutstanding':100}
    cap,flags,errors=market.capitalization(base,100,{})
    assert cap==1000 and not errors and flags
    base['marketCap']=1000
    assert market.capitalization(base,100,{})[0]==1000
    base['marketCap']=12345
    assert market.capitalization(base,100,{})[2]

def test_parent_income_cannot_be_combined_with_aggregate_ownership():
    info={'sharesOutstanding':450e6,'impliedSharesOutstanding':1700e6}
    assert 'ownership scope' in market.ownership_check(info,1.126e9,4.896e9,{})
    assert market.ownership_check(info,1e9,1e9,{}) is None # multiclass alone does not prove a contradiction

def test_country_risk_is_independent_of_reporting_currency():
    d=operating();d['info']['country']='Brazil'
    cfg={**CFG,'country_risk':{'Brazil':{'premium':.035,'source':'controlled test'}}}
    m=run(d,cfg)
    assert m['country_risk_premium']==.035
    assert m['hurdle']==pytest.approx(.13)
    assert m['floor_rate']==m['hurdle']

def test_sourced_geographic_weights_override_domicile_and_currency():
    p={'source':'controlled exposure table','as_of':'2026-06-30','period_end':'2026-06-30','currency':'USD',
       'geographic_exposures':{'United States':.6,'Brazil':.4}}
    cfg={**CFG,'issuer_inputs':{'TEST':p},'country_risk':{'United States':{'premium':0},'Brazil':{'premium':.035}}}
    m=run(operating(),cfg)
    assert m['country_risk_premium']==pytest.approx(.014)
    assert m['hurdle']==pytest.approx(.109)

def test_reconciled_listed_share_basis_repairs_aggregate_cap_scope():
    info={'regularMarketPrice':100,'marketCap':170000,'sharesOutstanding':450,'impliedSharesOutstanding':1700}
    cap,flags,errors=market.capitalization(info,1,{'market_cap_basis':'listed_shares','ownership_reconciled':True})
    assert cap==45000 and not errors
    assert 'source-reconciled' in flags[0]

def test_currency_rescaling_preserves_quote_ceiling_and_owner_cashflows():
    from screener import metrics
    d=operating();original=run(d)
    for name in ('income_stmt','ttm_income_stmt','cashflow','ttm_cashflow','balance_sheet','quarterly_balance_sheet'):
        for label in d[name].index:
            if 'Shares' not in label:d[name].loc[label]*=1.5
    d['info']['financialCurrency']='CAD'
    cfg={**CFG,'rates':{**CFG['rates'],'CAD':CFG['rates']['USD']}}
    class ConvertedFX:
        def rate(self,a,b):
            return 1 if a==b else 1.5 if (a,b)==('USD','CAD') else 2/3 if (a,b)==('CAD','EUR') else 1
    changed=metrics.compute(d,20,ConvertedFX(),cfg,None)
    assert changed['ceiling_px']==pytest.approx(original['ceiling_px'])
    assert changed['entry_score']==pytest.approx(original['entry_score'])
    before,after=json.loads(original['scenario_results']),json.loads(changed['scenario_results'])
    for case in ('base','stress','upside'):
        assert after[case]['cashflows']==pytest.approx([1.5*x for x in before[case]['cashflows']])
        for field in ('opening_distribution','terminal_cashflow','funding_required','raw_value'):
            assert after[case][field]==pytest.approx(1.5*before[case][field])

def test_same_period_sourced_debt_can_complete_a_missing_provider_row():
    d=operating();d['quarterly_balance_sheet']=d['quarterly_balance_sheet'].drop(index='Total Debt')
    p={'source':'controlled same-period filing','as_of':'2026-06-30','period_end':'2026-06-30','currency':'USD','debt':0}
    m=run(d,{**CFG,'issuer_inputs':{'TEST':p}})
    assert m['net_debt_fc']==-50
    assert json.loads(m['statement_periods'])['balance']['period_end']=='2026-06-30'
    assert 'same period' in json.loads(m['statement_periods'])['balance']['source']

def test_new_provenance_flags_prevent_candidate_status():
    cfg={'scoring':{'w_value':.4,'w_quality':.3,'w_growth':.2,'w_balance':.1}}
    for flag in ('quote refresh failed','quote date unavailable','stale cache fallback','short-window cycle proxy'):
        result=scoring.score(pd.DataFrame([{'class':'operating','entry_score':20,'flags':flag,'not_rankable':''}]),cfg)
        assert result.iloc[0]['status']=='data flag' and not result.iloc[0]['candidate']
