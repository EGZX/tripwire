"""Filing-backed counterexamples and evidence boundaries from the live audit."""
from copy import deepcopy
from datetime import date
import json
import pandas as pd
import pytest
from screener import inputs, market, scoring, statements as S
from test_metrics import operating, run, CFG, frame, TTM

QUARTERS=pd.to_datetime(['2026-06-30','2026-03-31','2025-12-31','2025-09-30'])


def evidence(**extra):
    return {'source':'controlled filing','as_of':'2026-10-10','period_end':'2026-06-30','currency':'USD',**extra}


def firm_evidence(**extra):
    p=dict(revenue=1000,growth=0,margin_start=.2,margin_terminal=.2,tax_rate=.25,
           sales_to_capital=2,maintenance_net_capex=30,funding_capacity=0,terminal_growth=0)
    return evidence(model='operating_forecast',base=p,stress=deepcopy(p),discount_rate=.1,discount_rate_basis='wacc',**extra)


def test_reported_operating_income_keeps_recurring_merchant_losses():
    d=operating()
    d['ttm_income_stmt'].loc['Operating Income']=2363e6
    d['ttm_income_stmt'].loc['Total Operating Income As Reported']=1844e6
    m=run(d)
    assert m['ebit']==1844e6
    assert m['operating_income_source']=='Total Operating Income As Reported'
    assert S.row(d['ttm_income_stmt'],'ebit').iloc[0]==1844e6
    # A missing reported period uses the named fallback, never a different date.
    d['ttm_income_stmt'].loc['Total Operating Income As Reported']=float('nan')
    assert S.latest(d['ttm_income_stmt'],'ebit')==2363e6
    assert S.latest_label(d['ttm_income_stmt'],'ebit')=='Operating Income'


def test_mixed_basis_asml_ttm_is_detected_without_replacing_it():
    ttm=frame({'Total Operating Income As Reported':[11550.1e6]},TTM)
    quarters=frame({'Total Operating Income As Reported':[3456.1e6,3157.8e6,3431.1e6,2468.4e6]},QUARTERS)
    c=S.trailing_checks(ttm,quarters,['ebit'],100000)[0]
    assert c['status']=='unreconciled'
    assert c['difference']==pytest.approx(-963.3e6)
    assert S.latest(ttm,'ebit')==11550.1e6


def test_rounding_is_bounded_by_source_precision_not_fitted_to_a_difference():
    ttm=frame({'Tax Provision':[36863e6]},TTM)
    quarters=frame({'Tax Provision':[10000e6,9000e6,9000e6,8865e6]},QUARTERS)
    assert S.trailing_checks(ttm,quarters,['tax'])[0]['status']=='unreconciled'
    c=S.trailing_checks(ttm,quarters,['tax'],1e6)[0]
    assert c['status']=='ties' and c['allowance']==pytest.approx(2.5e6)
    quarters.iloc[0,0]+=1e6
    assert S.trailing_checks(ttm,quarters,['tax'],1e6)[0]['status']=='unreconciled'


def test_quarter_crosscheck_does_not_sum_semiannual_or_incomplete_periods():
    ttm=frame({'Total Revenue':[100]},TTM)
    halfyears=frame({'Total Revenue':[25]*4},pd.to_datetime(['2026-06-30','2025-12-31','2025-06-30','2024-12-31']))
    assert S.trailing_checks(ttm,halfyears,['revenue'])==[]
    quarters=frame({'Total Revenue':[25,float('nan'),25,25]},QUARTERS)
    assert S.trailing_checks(ttm,quarters,['revenue'])==[]


def test_source_minimum_debt_abstains_but_does_not_invent_lease_amounts():
    check={'statement':'balance','field':'debt','period_type':'instant','period_end':'2026-06-30',
           'value':3719.7e6,'relation':'minimum','rounding_unit':200000,'source':'two reported borrowing components'}
    cfg=deepcopy(CFG);cfg['issuer_inputs']={'TEST':firm_evidence(filing_checks=[check])}
    d=operating();d['quarterly_balance_sheet'].loc['Total Debt']=1984.4e6
    m=run(d,cfg)
    assert 'filing contradiction: balance debt' in m['not_rankable']
    assert 'entry_score' not in m
    assert json.loads(m['filing_reconciliation'])[0]['provider_value']==1984.4e6
    # Only the checked date is constrained; later statements need new evidence.
    cfg['issuer_inputs']['TEST']['filing_checks'][0]['period_end']='2025-12-31'
    assert json.loads(run(d,cfg)['filing_reconciliation'])[0]['status']=='not_applicable'


def test_equal_values_do_not_resolve_different_accounting_bases():
    check={'statement':'balance','field':'debt','period_type':'instant','period_end':'2026-06-30',
           'value':100,'source':'IFRS reference','source_basis':'IFRS','provider_basis':'US GAAP'}
    cfg=deepcopy(CFG);cfg['issuer_inputs']={'TEST':firm_evidence(filing_checks=[check])}
    m=run(operating(),cfg)
    assert 'filing basis unresolved' in m['not_rankable']
    assert 'filing contradiction' not in m['not_rankable']


def test_disclosed_operating_leases_cannot_silently_create_an_eligible_candidate():
    d=operating();d['quarterly_balance_sheet'].loc['Capital Lease Obligations']=10
    cfg={**CFG,'scoring':{'w_value':1,'w_quality':1,'w_growth':1,'w_balance':1},'issuer_inputs':{'TEST':firm_evidence()}}
    m=run(d,cfg)
    assert 'lease/cash-flow basis unreconciled' in m['flags']
    assert not scoring.score(pd.DataFrame([{**m,'entry_score':20}]),cfg).iloc[0]['candidate']
    cfg['issuer_inputs']={'TEST':firm_evidence(lease_cashflow_reconciled=True)}
    assert 'lease/cash-flow basis unreconciled' not in run(d,cfg)['flags']
    d['quarterly_balance_sheet'].columns=pd.to_datetime(['2026-09-30'])
    assert 'lease/cash-flow basis unreconciled' in run(d,cfg)['flags']


def test_sourced_balance_cannot_hide_known_same_period_provider_leases():
    d=operating();d['quarterly_balance_sheet'].loc['Capital Lease Obligations']=20
    cfg=deepcopy(CFG)
    cfg['issuer_inputs']={'TEST':firm_evidence(equity=600,debt=100,cash=50,minority=0,preferred=0)}
    m=run(d,cfg)
    assert json.loads(m['statement_periods'])['balance']['source']=='issuer facts'
    assert m['lease_liabilities_fc']==20
    assert 'lease/cash-flow basis unreconciled' in m['flags']


def test_common_classes_are_not_inferred_to_be_noncontrolling_partnership_units():
    info={'sharesOutstanding':3808087961,'impliedSharesOutstanding':4830688659}
    supplied=evidence(ownership_reconciled=True,ownership_common_shares=4830688659)
    assert market.ownership_check(info,1060.199e6,1061.089e6,supplied) is None
    info['impliedSharesOutstanding']+=100000
    assert 'no longer match' in market.ownership_check(info,1,2,supplied)
    assert 'economic-rights reconciliation' in market.ownership_check(info,1,2,{})


@pytest.mark.parametrize('change',[{'statement_rounding_unit':-1},{'lease_cashflow_reconciled':'yes'},
                                 {'ownership_common_shares':5}, {'filing_checks':{}},
                                 {'filing_checks':[{'statement':'balance','field':'debt','period_type':'ttm','value':float('inf')}] }])
def test_invalid_reconciliation_declarations_fail_validation(change):
    assert inputs.validate_issuer('TEST',evidence(**change),date(2026,10,11))


def test_live_replay_reads_hash_verified_snapshot_not_replaceable_cache(tmp_path):
    import hashlib,pickle
    from tools.validate_live import replay_record
    snapshot=tmp_path/'immutable.pkl'
    raw=pickle.dumps({'symbol':'TEST','info':{'regularMarketPrice':20}})
    snapshot.write_bytes(raw)
    (tmp_path/'TEST.pkl').write_bytes(pickle.dumps({'symbol':'TEST','info':{'regularMarketPrice':2000}}))
    record={'source_snapshot':str(snapshot),'source_sha256':hashlib.sha256(raw).hexdigest()}
    assert replay_record(record)['info']['regularMarketPrice']==20
    snapshot.write_bytes(raw+b'changed')
    with pytest.raises(ValueError,match='SHA-256'):
        replay_record(record)
