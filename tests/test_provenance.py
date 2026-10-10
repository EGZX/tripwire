import hashlib
import pickle
from datetime import datetime,timedelta,timezone,date
import pandas as pd
import pytest
from screener import fetch,statements as S,filings,market
from test_metrics import operating,run

def test_newest_complete_balance_wins_across_statement_sources():
    newer=pd.DataFrame({'2026-06-30':[2.]},index=['Total Assets'])
    older=pd.DataFrame({'2025-12-31':[1.]},index=['Total Assets'])
    chosen=S.select([('quarterly',older),('annual',newer)],('assets',),instant=True)
    assert chosen.date==pd.Timestamp('2026-06-30')
    assert S.latest(chosen.frame,'assets')==2

def test_fiscal_date_keeps_its_local_calendar_day_across_timezones():
    df=pd.DataFrame({pd.Timestamp('2026-06-30T00:00:00+09:00'):[10.]},index=['Total Assets'])
    selected=S.select([('quarterly',df)],('assets',))
    assert selected.date==pd.Timestamp('2026-06-30')
    assert S.latest(selected.frame,'assets')==10

def test_missing_debt_is_not_inferred_from_only_one_component():
    df=pd.DataFrame({'2026-06-30':[100.]},index=['Long Term Debt'])
    assert S.row(df,'debt') is None
    df.loc['Current Debt']=20
    assert S.latest(df,'debt')==120

def test_preferred_and_minority_can_be_reconciled_from_reported_equity():
    df=pd.DataFrame({'2026-06-30':[600.,550.,630.]},index=['Stockholders Equity','Common Stock Equity','Total Equity Gross Minority Interest'])
    assert S.latest(df,'preferred')==50 and S.latest(df,'minority')==30

def test_common_parent_and_consolidated_equity_are_distinct_claims():
    df=pd.DataFrame({'2026-06-30':[500.,50.,600.,100.,20.]},index=['Common Stock Equity','Preferred Stock Equity',
        'Total Equity Gross Minority Interest','Total Debt','Cash And Cash Equivalents'])
    assert S.latest(df,'common_equity')==500
    assert S.latest(df,'equity')==550
    assert S.latest(df,'minority')==50
    assert S.invested_capital(df).iloc[0]==680

def test_roic_matches_the_earnings_window_and_includes_minorities():
    d=operating();q=d['quarterly_balance_sheet']
    q[pd.Timestamp('2025-06-30')]=q.iloc[:,0]
    q.loc['Stockholders Equity',pd.Timestamp('2025-06-30')]=400
    q.loc['Minority Interest']=200
    m=run(d)
    # Opening650 and closing850 support consolidated TTM NOPAT168.
    assert m['roic']==pytest.approx(168/750)
    assert '2025-06-30 / 2026-06-30' in m['capital_period_basis']

def test_negative_noncommon_book_equity_is_not_added_to_common_value():
    d=operating()
    for key in ('balance_sheet','quarterly_balance_sheet'):d[key].loc['Minority Interest']=-50
    m=run(d)
    assert 'negative cash/debt/noncommon' in m['not_rankable']
    assert 'entry_score' not in m

def test_cache_snapshots_are_immutable_and_hash_verifiable(tmp_path,monkeypatch):
    raw={'schema':fetch.CACHE_SCHEMA,'symbol':'X','fetched_at':datetime.now(timezone.utc),'info':{},'errors':[]}
    monkeypatch.setattr(fetch,'fetch_symbol',lambda symbol:dict(raw))
    first,_=fetch.get('X',tmp_path,6,0,force=True)
    original=__import__('pathlib').Path(first['source_snapshot']).read_bytes()
    assert hashlib.sha256(original).hexdigest()==first['source_sha256']
    raw['info']={'new':1}
    second,_=fetch.get('X',tmp_path,6,0,force=True)
    assert first['source_snapshot']!=second['source_snapshot']
    assert __import__('pathlib').Path(first['source_snapshot']).read_bytes()==original

def test_refresh_failure_has_structured_stale_state(tmp_path,monkeypatch):
    old={'schema':fetch.CACHE_SCHEMA,'symbol':'X','fetched_at':datetime.now(timezone.utc)-timedelta(days=20),'info':{}}
    fetch._cache_file(tmp_path,'X').write_bytes(pickle.dumps(old))
    def fail(_):raise OSError('offline')
    monkeypatch.setattr(fetch,'fetch_symbol',fail)
    got,fresh=fetch.get('X',tmp_path,6,0,log=lambda x:None)
    assert not fresh and got['cache_state']=='stale_fallback'
    assert got['fetched_at']==old['fetched_at'] and 'offline' in got['refresh_error']

def test_split_refresh_uses_current_market_cap_not_old_price_ratio():
    d=operating()
    info={**d['info'],'regularMarketPrice':10,'marketCap':2000,'sharesOutstanding':200,
          'impliedSharesOutstanding':200,'regularMarketTime':1791547200}
    quote=market.Observation(10,'2026-10-09T12:00:00+00:00','controlled quote',info)
    m=run(d,price=quote)
    assert m['mcap_fc']==2000 and m['price']==10

def test_sec_facts_preserve_units_publication_time_and_conflicts():
    payload={'facts':{'us-gaap':{'Assets':{'units':{'USD':[
        {'end':'2025-12-31','filed':'2026-02-01','val':100,'accn':'old'},
        {'end':'2025-12-31','filed':'2026-11-01','val':999,'accn':'future'}],
        'EUR':[{'end':'2025-12-31','filed':'2026-02-01','val':88,'accn':'eur'}]}}}}}
    frame,ledger=filings.balance_facts(payload,'USD',date(2026,10,10))
    assert S.latest(frame,'assets')==100 and ledger[0]['accessions']==['old']
    payload['facts']['us-gaap']['Assets']['units']['USD'].append({'end':'2025-12-31','filed':'2026-02-01','val':101,'accn':'conflict'})
    frame,ledger=filings.balance_facts(payload,'USD',date(2026,10,10))
    assert frame.empty and ledger[0]['status']=='conflicting'

def test_current_portion_of_long_term_debt_is_not_total_current_debt():
    payload={'facts':{'us-gaap':{t:{'units':{'USD':[{'end':'2025-12-31','filed':'2026-02-01','val':v}]}}
                              for t,v in [('LongTermDebtCurrent',10),('LongTermDebtNoncurrent',100)]}}}
    frame,_=filings.balance_facts(payload,'USD',date(2026,10,10))
    assert S.row(frame,'debt') is None  # short-term borrowings remain unknown

def test_requested_filing_verification_failure_is_visible(monkeypatch,tmp_path):
    monkeypatch.delenv('SEC_USER_AGENT',raising=False)
    out=filings.enrich({'info':{}},'123',tmp_path)
    assert 'filing verification unavailable' in out['filing_error']
