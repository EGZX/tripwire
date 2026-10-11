"""End-to-end economic contracts for automatic model routing and source use."""
from copy import deepcopy
from datetime import date
import json
import math

import pandas as pd
import pytest

from screener import metrics, scoring
from test_metrics import CFG, operating


class DatedFX:
    def rate(self,a,b):
        return 1.0

    def observations(self):
        return {a+'/'+b:{'rate':1.,'as_of':'2026-10-09T15:00:00+00:00','source':'controlled FX'}
                for a in ('USD','GBP','EUR','TWD') for b in ('USD','GBP','EUR','TWD') if a!=b}


def config():
    cfg=deepcopy(CFG)
    for rate in cfg['rates'].values():
        rate['as_of']='2026-10-09'
    cfg['country_risk']={'United States':{'premium':0.,'as_of':'2026-10-09'}}
    cfg['scoring']={'w_value':.25,'w_quality':.25,'w_growth':.25,'w_balance':.25}
    return cfg


def issuer(industry='Software - Application',sector='Technology'):
    data=operating()
    data['info'].update(industry=industry,sector=sector,country='United States',regularMarketTime=1791558000)
    return data


def compute(data,cfg=None):
    return metrics.compute(data,None,DatedFX(),cfg or config(),None,valuation_date=date(2026,10,11))


def candidate(result,cfg=None):
    return bool(scoring.score(pd.DataFrame([result]),cfg or config()).iloc[0]['candidate'])


def test_financial_data_fee_business_with_negative_book_uses_operating_economics():
    data=issuer('Financial Data & Stock Exchanges','Financial Services')
    for key in ('balance_sheet','quarterly_balance_sheet'):
        data[key].loc['Common Stock Equity']=-10.
        data[key].loc['Stockholders Equity']=-10.
    result=compute(data)
    assert result['model_family']=='operating' and result['model']=='operating_equity'
    assert result['not_rankable']==''
    assert 'fully equity-funded' in result['model_assumptions']


def test_prerevenue_assets_are_partial_recovery_scenarios_not_invented_project_profit():
    data=issuer('Copper','Basic Materials')
    for key in ('income_stmt','ttm_income_stmt'):
        data[key].loc['Total Revenue']=0.
        data[key].loc['Net Income Common Stockholders']=-10.
    result=compute(data)
    assert result['model']=='asset_recovery' and result['not_rankable']==''
    assert result['value_reasonable']==pytest.approx(600.)
    assert result['value_adverse']==0.
    assert 'unvalued operating or project assets' in result['flags']
    assert not candidate(result)
    assert result['funding_required']==0. # liquidation deficits are not personal common-owner obligations


def test_missing_provider_cap_uses_labeled_quote_count_scenario_without_becoming_verified():
    data=issuer();data['info']['marketCap']=None
    result=compute(data)
    assert result['not_rankable']=='' and result['mcap_fc']==pytest.approx(2000.)
    assert 'capitalization estimated from quote and shares' in result['flags']
    assert not candidate(result)
    assert json.loads(result['quote_scope_scenarios'])


def test_sum_of_parts_through_issuer_schema_preserves_common_claim_and_parent_bridge():
    from test_business_models import parts
    p=parts();cfg=config()
    cfg['issuer_inputs']={'TEST':evidence(model='sum_of_parts',base=p,stress=deepcopy(p))}
    result=compute(issuer('Conglomerates','Industrials'),cfg)
    assert result['not_rankable']=='' and result['value_reasonable']==pytest.approx(233.)
    assert result['implied_return_claim']=='equity'
    assert json.loads(result['base_parent_bridge'])['application_count']==1
    assert len(json.loads(result['base_components']))==2


def test_configured_common_property_facts_feed_the_automatic_model():
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(reported_property={
        'source':'common-holder AFFO filing bridge','period_end':'2026-06-30','period_type':'ttm',
        'currency':'USD','affo_common':300.})}
    result=compute(issuer('REIT - Industrial','Real Estate'),cfg)
    assert result['not_rankable']==''
    assert json.loads(result['scenario_parameters'])['base']['roe_start']==pytest.approx(.5)
    assert 'reported AFFO' in result['model_evidence']


def evidence(**extra):
    return {'source':'controlled independent filing','as_of':'2026-10-10','period_end':'2026-06-30','currency':'USD',**extra}


def fact(statement,field,value,period='2026-06-30',duration='ttm'):
    return {'statement':statement,'field':field,'value':value,'period_end':period,
            'period_type':duration,'source':'controlled independent filing',
            'source_basis':'US GAAP','provider_basis':'US GAAP'}


def test_new_ticker_names_do_not_change_economic_inputs_or_model():
    data=issuer();first=compute(data)
    data['symbol']='NEVER-SEEN-BEFORE.L'
    second=compute(data)
    for key in ('model','scenario_parameters','scenario_results','model_family','entry_ceiling'):
        assert second[key]==first[key]


def test_candidate_and_absolute_value_do_not_depend_on_watchlist_members():
    left=compute(issuer())
    other=issuer();other['symbol']='UNRELATED';other['info']['marketCap']*=10
    right=compute(other)
    cfg=config()
    alone=scoring.score(pd.DataFrame([left]),cfg).iloc[0]
    together=scoring.score(pd.DataFrame([right,left]),cfg).set_index('symbol').loc[left['symbol']]
    for key in ('entry_ceiling','entry_score','status','candidate'):
        assert together[key]==alone[key]


def test_reconciled_pence_and_major_quote_are_equivalent():
    data=issuer();major=compute(data)
    data['info']['currency']='GBp'
    data['info']['regularMarketPrice']*=100
    minor=compute(data)
    assert minor['not_rankable']==''
    assert minor['price']==major['price']
    assert minor['mcap_fc']==major['mcap_fc']
    assert minor['ceiling_px']==pytest.approx(major['ceiling_px'])
    assert minor['entry_score']==pytest.approx(major['entry_score'])


def test_adr_unit_conversion_changes_per_unit_value_not_common_business_value():
    data=issuer();ordinary=compute(data)
    data['info']['regularMarketPrice']*=5
    data['info']['sharesOutstanding']/=5
    receipt=compute(data)
    assert receipt['entry_ceiling']==pytest.approx(ordinary['entry_ceiling'])
    assert receipt['ceiling_px']==pytest.approx(ordinary['ceiling_px']*5)
    assert receipt['entry_score']==pytest.approx(ordinary['entry_score'])


def test_ambiguous_minor_currency_identity_cannot_become_eligible_point_estimate():
    data=issuer();data['info'].update(currency='GBp',regularMarketPrice=20.,marketCap=2.,sharesOutstanding=100.)
    result=compute(data)
    assert 'minor-unit' in result['flags'] or result['not_rankable']
    assert not candidate(result)


def test_missing_quote_or_statement_currency_is_not_fabricated_as_zero_or_verified():
    data=issuer();data['info'].pop('financialCurrency')
    result=compute(data)
    assert 'reporting currency' in result['not_rankable']
    assert not candidate(result)
    data=issuer();data['info']['marketCap']=None
    result=compute(data)
    assert not candidate(result)
    assert result['not_rankable'] or 'conditional' in result.get('model_uncertainties','').lower()


def test_unused_firm_debt_constraint_does_not_block_after_interest_equity_model():
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('balance','debt',9999.,duration='instant')])}
    result=compute(issuer(),cfg)
    assert result['not_rankable']==''
    checks=json.loads(result['filing_reconciliation'])
    assert checks[0]['status']=='contradiction'
    assert checks[0]['used_by_model'] is False
    assert result['implied_return_claim']=='equity'


def test_declared_equity_rate_and_purchase_hurdle_discount_the_same_cashflow_path():
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(discount_rate=.12,discount_rate_basis='cost_of_equity')}
    result=compute(issuer(),cfg)
    assert result['not_rankable']==''
    path=json.loads(result['scenario_results'])['base']
    def pv(rate):
        return (path['opening_distribution']
            +sum(c/(1+rate)**i for i,c in enumerate(path['cashflows'],1))
            +path['terminal_cashflow']/(rate-path['terminal_growth'])/(1+rate)**len(path['cashflows']))
    assert result['discount_rate']==.12 and result['hurdle']==pytest.approx(.095)
    assert result['value_reasonable']==pytest.approx(max(0.,pv(.12)))
    assert result['value_at_policy_hurdle']==pytest.approx(pv(.095))
    assert result['entry_ceiling']==pytest.approx(max(0.,min(pv(.12),pv(.095))))


def test_common_owner_cashflows_cannot_accept_a_wacc_discount_rate():
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(discount_rate=.08,discount_rate_basis='wacc')}
    result=compute(issuer(),cfg)
    assert 'common-equity model cannot use a firm discount rate' in result['not_rankable']
    assert not candidate(result,cfg)


def test_used_common_income_contradiction_blocks_an_unsupported_point_result():
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('income','net_income',0.)])}
    result=compute(issuer(),cfg)
    assert result['not_rankable']
    assert not candidate(result,cfg)
    check=json.loads(result['filing_reconciliation'])[0]
    assert check['used_by_model'] and check['status']=='contradiction'


def test_used_property_cashflow_source_contradiction_is_not_ignored():
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('cashflow','ocf',0.)])}
    result=compute(issuer('REIT - Office','Real Estate'),cfg)
    check=json.loads(result['filing_reconciliation'])[0]
    assert check['used_by_model'] is True
    assert result['not_rankable']
    assert not candidate(result,cfg)


def test_reconstructed_property_cashflow_keeps_investing_flows_used_by_scenarios():
    data=issuer('REIT - Office','Real Estate')
    periods=pd.to_datetime(['2026-06-30','2026-03-31','2025-12-31','2025-09-30'])
    data['quarterly_cashflow']=pd.DataFrame({d:[55.,-10.,7.5,2.5,-1000.] for d in periods},
        index=['Operating Cash Flow','Capital Expenditure','Depreciation And Amortization',
               'Stock Based Compensation','Investing Cash Flow'])
    data['ttm_cashflow'].loc['Investing Cash Flow']=-4000.
    result=compute(data)
    evidence_rows=json.loads(result['model_evidence'])
    newest=[x for x in evidence_rows if x.get('field')=='high_maintenance_cash_return'
            and x.get('period_end')=='2026-06-30']
    assert newest and newest[0]['maintenance_proxy']==4000.
    assert newest[0]['value']==pytest.approx((220.-10.-4000.)/600.)


def test_historical_income_used_by_scenarios_is_still_subject_to_filing_evidence():
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('income','net_income',0.,'2025-12-31','annual')])}
    result=compute(issuer(),cfg)
    checks=json.loads(result['filing_reconciliation'])
    assert any(c.get('used_by_model') and c['status']=='contradiction' for c in checks)
    assert result['not_rankable']


def test_annual_filing_fact_is_not_compared_to_same_endpoint_single_quarter():
    data=issuer()
    periods=pd.to_datetime(['2026-06-30','2026-03-31','2025-12-31','2025-09-30'])
    data['quarterly_income_stmt']=pd.DataFrame({d:[250.,38.,50.,45.,9.] for d in periods},
        index=['Total Revenue','Net Income Common Stockholders','Operating Income','Pretax Income','Tax Provision'])
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('income','net_income',152.,'2025-12-31','annual')])}
    result=compute(data,cfg)
    checks=json.loads(result['filing_reconciliation'])
    assert any(c['status']=='ties' and c.get('historical_source')=='income_stmt' for c in checks)
    assert not any(c['status']=='contradiction' for c in checks)
    assert result['not_rankable']==''


def test_hybrid_and_utility_profiles_do_not_claim_pure_sector_asset_bases():
    data=issuer('Utilities - Diversified','Utilities')
    data['info']['segments']=[{'name':'Regulated networks'},{'name':'Merchant generation'}]
    result=compute(data)
    profile=json.loads(result['business_profile'])
    assert profile['diversified']
    assert result['model']=='equity_residual_income'
    assert 'consolidated' in result['model_selection_reason'].lower()
    assert 'not assumed to be regulated rate base' in result['model_assumptions']
    # Alter debt only, holding common capital/after-interest income constant.
    changed=deepcopy(data)
    for name in ('quarterly_balance_sheet','balance_sheet'):
        changed[name].loc['Total Debt']*=50
    assert compute(changed)['entry_ceiling']==pytest.approx(result['entry_ceiling'])


def test_missing_cash_and_debt_remain_unknown_in_equity_evidence():
    data=issuer()
    for name in ('balance_sheet','quarterly_balance_sheet'):
        data[name]=data[name].drop(index=['Total Debt','Cash Cash Equivalents And Short Term Investments'])
    result=compute(data)
    assert result['not_rankable']==''
    assert math.isnan(result['net_debt_fc'])
    assert 'Unknown is not zero' in result['model_uncertainties']
    assert result['implied_return_claim']=='equity'


def test_identifiable_distribution_yield_reprices_common_equity_including_opening_funding():
    from screener import valuation as V
    result=compute(issuer())
    base=json.loads(result['scenario_results'])['base']
    assert base['opening_distribution']<0
    assert result['implied_return_status']=='solved'
    assert base['opening_distribution']+V.discount(base['cashflows'],base['terminal_cashflow'],
        result['implied_return'],base['terminal_growth'])==pytest.approx(result['mcap_fc'])
    assert math.isfinite(result['decision_rate'])


def consensus_issuer(industry,sector,denominator='shares_avg'):
    data=issuer(industry,sector)
    data['fetched_at']='2026-10-10T23:40:00Z'
    data['earnings_estimate']=pd.DataFrame({'avg':[2.,2.],'low':[1.8,1.8],'high':[2.2,2.2],
        'currency':['USD','USD'],'numberOfAnalysts':[4,4]},index=['0y','+1y'])
    label='Diluted Average Shares'
    if denominator=='shares_out':
        data['ttm_income_stmt']=data['ttm_income_stmt'].drop(index='Diluted Average Shares')
        data['quarterly_balance_sheet'].loc['Ordinary Shares Number']=100.
        label='Ordinary Shares Number'
    units=dict(source='controlled economic EPS units',as_of='2026-10-10',statement_period_end='2026-06-30',
               statement_share_field=label,statement_shares_per_estimate_unit=1.,currency='USD')
    return data,units


@pytest.mark.parametrize('industry,sector',[('Banks - Regional','Financial Services'),
    ('Software - Application','Technology'),('Utilities - Regulated Electric','Utilities')])
@pytest.mark.parametrize('denominator',['shares_avg','shares_out'])
def test_consumed_consensus_share_denominator_is_subject_to_filing_evidence(industry,sector,denominator):
    data,units=consensus_issuer(industry,sector,denominator)
    cfg=config();statement='income' if denominator=='shares_avg' else 'balance'
    duration='ttm' if denominator=='shares_avg' else 'instant'
    cfg['issuer_inputs']={'TEST':evidence(earnings_estimate_units=units,
        filing_checks=[fact(statement,denominator,999.,duration=duration)])}
    result=compute(data,cfg)
    checks=json.loads(result['filing_reconciliation'])
    assert any(c['used_by_model'] and c['status']=='contradiction' for c in checks)
    assert result['not_rankable'] and not candidate(result,cfg)


@pytest.mark.parametrize('industry,sector',[('Banks - Regional','Financial Services'),
    ('Software - Application','Technology'),('Utilities - Regulated Electric','Utilities')])
def test_rejected_eps_panel_does_not_consume_unused_share_denominator(industry,sector):
    data,_=consensus_issuer(industry,sector)
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('income','shares_avg',999.)])}
    result=compute(data,cfg)
    assert result['not_rankable']==''
    assert all(not c['used_by_model'] for c in json.loads(result['filing_reconciliation']))
    assert 'unit evidence rejected' in result['model_uncertainties']


@pytest.mark.parametrize('industry,sector',[('Banks - Regional','Financial Services'),
    ('Software - Application','Technology'),('Utilities - Regulated Electric','Utilities')])
def test_common_book_tracks_preferred_only_for_the_periods_it_was_derived(industry,sector):
    data=issuer(industry,sector)
    # Current book is independently reported, history is derived from parent
    # stockholders' equity less preferred. A global field union loses this scope.
    data['quarterly_balance_sheet'].loc['Common Stock Equity']=600.
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('balance','preferred',999.,duration='instant')])}
    current=compute(data,cfg)
    assert current['not_rankable']==''
    assert all(not c['used_by_model'] for c in json.loads(current['filing_reconciliation']))
    cfg['issuer_inputs']['TEST']['filing_checks']=[fact('balance','preferred',999.,'2025-12-31','instant')]
    historical=compute(data,cfg)
    assert historical['not_rankable']
    assert any(c['used_by_model'] and c['status']=='contradiction' for c in json.loads(historical['filing_reconciliation']))


def test_current_derived_common_book_does_consume_preferred():
    data=issuer('Banks - Regional','Financial Services')
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('balance','preferred',999.,duration='instant')])}
    result=compute(data,cfg)
    assert result['not_rankable']
    assert any(c['used_by_model'] for c in json.loads(result['filing_reconciliation']))


def test_derived_ttm_consumes_quarter_facts_with_exact_duration():
    data=issuer('Banks - Regional','Financial Services')
    periods=pd.to_datetime(['2026-06-30','2026-03-31','2025-12-31','2025-09-30'])
    data['quarterly_income_stmt']=pd.DataFrame({d:[250.,38.] for d in periods},
        index=['Total Revenue','Net Income Common Stockholders'])
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('income','net_income',0.,'2025-09-30','quarter')])}
    result=compute(data,cfg)
    assert json.loads(result['evidence_assembly'])['income']['method']=='four_quarter_sum'
    assert result['not_rankable']
    checks=json.loads(result['filing_reconciliation'])
    assert any(c['used_by_model'] and c['status']=='contradiction' and c.get('historical_source')=='quarterly_income_stmt' for c in checks)
    cfg['issuer_inputs']['TEST']['filing_checks']=[fact('income','net_income',0.,'2025-09-30','annual')]
    different_duration=compute(data,cfg)
    assert different_duration['not_rankable']==''
    assert not any(c['used_by_model'] for c in json.loads(different_duration['filing_reconciliation']))


def test_actual_financial_retained_book_cash_payout_is_subject_to_annual_fact_checks():
    data=issuer('Banks - Regional','Financial Services')
    data['cashflow'].loc['Common Stock Dividend Paid']=[-30.,-27.,-24.,-21.]
    data['cashflow'].loc['Net Common Stock Issuance']=[-10.,-9.,-8.,-7.]
    cfg=config();cfg['issuer_inputs']={'TEST':evidence(filing_checks=[fact('cashflow','common_dividends',-99.,'2025-12-31','annual')])}
    result=compute(data,cfg)
    assert result['not_rankable']
    assert any(c['used_by_model'] and c['status']=='contradiction' for c in json.loads(result['filing_reconciliation']))


@pytest.mark.parametrize('symbol',['O','VICI','DUK','IBE.MC','AIG','ALV.DE','DBK.DE','HDB','BN','BRK-B','SNOW','VALE'])
def test_frozen_new_issuers_route_without_manual_forecasts(symbol):
    import pickle
    from pathlib import Path
    cache=Path(__file__).resolve().parents[1]/'data/general-models-2026-10-11/heldout/cache'/f'{symbol}.pkl'
    if not cache.exists():
        pytest.skip('held-out live source snapshot not distributed')
    with cache.open('rb') as stream:
        data=pickle.load(stream)
    cfg=config()
    # Same explicit policy across units; FX unity is a controlled dimensional
    # test, not a claim about current market exchange rates or fair value.
    currency=data['info']['financialCurrency']
    cfg['rates'][currency]={**cfg['rates']['USD']}
    data['info']['country']='United States'
    result=compute(data,cfg)
    assert result['model'] in ('equity_residual_income','operating_equity')
    assert result['model_evidence'] and result['model_assumptions']
    assert result['not_rankable']==''
    outcomes=json.loads(result['scenario_results'])
    assert set(outcomes)=={'base','stress','upside'}
    assert all(math.isfinite(case['raw_value']) for case in outcomes.values())
    assert result['implied_return_claim']=='equity'
