"""General operating/recovery scenarios on an explicitly common-equity basis.

Net income already includes financing, tax and lease expenses. Consequently no
second debt/cash/lease bridge is deducted from these equity cash flows. Growth
uses new equity capital; negative distributions are priced owner contributions.
Observed capital intensity is a proxy, never asserted to be marginal capital.
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path
from statistics import median

import pandas as pd
from . import statements as S, valuation as V
from . import financial_models as F
from .financial_models import forecast_income

# A crosswalk between provider industry labels and a dated public reference.
# This is NOT GICS or a company-specific classification/forecast table.
INDUSTRY_REFERENCE = {
    'Software - Application':'Software (System & Application)',
    'Software - Infrastructure':'Software (System & Application)',
    'Internet Content & Information':'Software (Internet)',
    'Financial Data & Stock Exchanges':'Information Services',
    'Electronic Gaming & Multimedia':'Software (Entertainment)',
    'Semiconductors':'Semiconductor','Semiconductor Equipment & Materials':'Semiconductor Equip',
    'Auto Manufacturers':'Auto & Truck','Auto Parts':'Auto Parts',
    'Beverages - Non-Alcoholic':'Beverage (Soft)','Beverages - Brewers':'Beverage (Alcoholic)',
    'Beverages - Wineries & Distilleries':'Beverage (Alcoholic)',
    'Oil & Gas Integrated':'Oil/Gas (Integrated)','Oil & Gas E&P':'Oil/Gas (Production and Exploration)',
    'Oil & Gas Midstream':'Oil/Gas Distribution','Oil & Gas Equipment & Services':'Oilfield Svcs/Equip.',
    'Other Industrial Metals & Mining':'Metals & Mining','Copper':'Metals & Mining',
    'Uranium':'Metals & Mining',
    'Gold':'Precious Metals','Silver':'Precious Metals','Steel':'Steel',
    'Aerospace & Defense':'Aerospace/Defense','Farm & Heavy Construction Machinery':'Machinery',
    'Specialty Industrial Machinery':'Machinery','Airlines':'Air Transport',
    'Railroads':'Transportation (Railroads)','Trucking':'Trucking',
    'Drug Manufacturers - General':'Drugs (Pharmaceutical)','Biotechnology':'Drugs (Biotechnology)',
    'Medical Devices':'Healthcare Products','Medical Instruments & Supplies':'Healthcare Products',
    'Packaged Foods':'Food Processing','Household & Personal Products':'Household Products',
    'Apparel Manufacturing':'Apparel','Apparel Retail':'Apparel','Footwear & Accessories':'Shoe',
    'Consumer Electronics':'Electronics (Consumer & Office)','Computer Hardware':'Computers/Peripherals',
    'Engineering & Construction':'Engineering/Construction','Residential Construction':'Homebuilding',
    'Restaurants':'Restaurant/Dining','Lodging':'Hotel/Gaming','Gambling':'Hotel/Gaming',
    'Conglomerates':'Diversified','Specialty Chemicals':'Chemical (Specialty)',
    'Specialty Retail':'Retail (Special Lines)','Internet Retail':'Retail (General)',
}


@lru_cache(maxsize=1)
def references():
    try:return json.loads((Path(__file__).parent/'resources/industry_benchmarks.json').read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:raise V.InvalidModel('industry-reference evidence unavailable: '+str(exc)) from exc


def benchmark(info):
    dataset=references();industry=info.get('industry','')
    name=INDUSTRY_REFERENCE.get(industry,industry)
    mapped=name in dataset['industries']
    if not mapped:name='Total Market (without financials)'
    return {**dataset['industries'][name],'industry':name,'mapped':mapped,
            'as_of':dataset['as_of'],'geography':dataset['geography'],'sources':dataset['sources']}


def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def _ratio(n,d):
    return n/d if finite(n) and finite(d) and d>0 else math.nan


def _observed_ratios(frame,numerator,denominator):
    n,d=S.annual(frame,numerator),S.annual(frame,denominator)
    if n.empty or d.empty:return []
    return [float(n[x]/d[x]) for x in n.index.intersection(d.index) if finite(n[x]) and finite(d[x]) and d[x]>0]


def build(data,selections,context):
    info=data.get('info') or {};inc=selections['income'].frame;bal=selections['balance'].frame
    revenue=S.latest(inc,'revenue')
    if not finite(revenue) or revenue<=0:raise V.InvalidModel('operating revenue must be available and positive')
    forecast=forecast_income(data,selections)
    common_income=forecast['avg']
    observed_income=S.latest(inc,'net_income')
    if not finite(common_income):raise V.InvalidModel('common earnings or a compatible earnings forecast is required')
    ref=benchmark(info);historical=S.annual(data.get('income_stmt'),'revenue')
    growth_history=[]
    for (a,ra),(b,rb) in zip(historical.items(),list(historical.items())[1:]):
        years=(b-a).days/365.25
        if ra>0 and rb>0 and .9<=years<=1.1:growth_history.append((rb/ra)**(1/years)-1)
    estimates=data.get('revenue_estimate');forecast_growth=None;growth_source='observed annual revenue growth';rejected_growth=[]
    if isinstance(estimates,pd.DataFrame) and 'growth' in estimates:
        for period in ('+1y','0y'):
            if period in estimates.index:
                g=estimates.loc[period,'growth']
                if finite(g) and g>-1:
                    forecast_growth=float(g);growth_source='provider '+period+' fiscal revenue-growth estimate, applied as initial annual scenario';break
                rejected_growth.append('Nonfinite or inadmissible '+period+' revenue-growth estimate excluded; observed growth evidence used instead.')
    gt=context['terminal_growth'];rate=context['hurdle']
    g0=forecast_growth if forecast_growth is not None else median(growth_history) if growth_history else gt
    growth_cases=growth_history+[g0,gt]
    margins=_observed_ratios(data.get('income_stmt'),'net_income','revenue')
    current_margin=_ratio(observed_income,revenue)
    dependencies={}
    F.add_dependencies(dependencies,'income',{'revenue','net_income'},selections['income'].date,selections['income'].source)
    annual_income=S.annual(data.get('income_stmt'),'net_income')
    for period in historical.index:
        if finite(historical[period]) and historical[period]>0:
            F.add_dependencies(dependencies,'income',{'revenue'},period,'income_stmt')
            if finite(annual_income.get(period,math.nan)):
                F.add_dependencies(dependencies,'income',{'net_income'},period,'income_stmt')
    if finite(current_margin):margins.append(current_margin)
    # Listing-specific EPS panels must not create a different issuer base model
    # for an ADR and its ordinary shares. Compatible EPS estimates enrich the
    # scenario envelope; issuer-level reported margins anchor the base.
    first_margin=median(margins) if context.get('family')=='cyclical' and margins else current_margin
    if not finite(first_margin):raise V.InvalidModel('reported common margin is required')
    positive=[m for m in margins if m>0]
    terminal=median(positive) if positive else ref['net_margin']
    terminal=max(0.0,terminal) # zero is an explicit no-profitable-continuation endpoint
    equity=S.latest(bal,'common_equity')
    intensity=_ratio(equity,revenue)
    intensity_basis='current common book equity / revenue; marginal equity-capital proxy'
    if not finite(intensity) or intensity<=0:
        turnover=ref['sales_to_invested_capital']
        if not finite(turnover) or turnover<=0:raise V.InvalidModel('neither usable capital history nor a positive industry capital reference')
        intensity=1/turnover
        intensity_basis='industry invested-capital/sales reference; growth assumed fully equity-funded because common book capital is nonpositive/unavailable'
    else:
        F.add_dependencies(dependencies,'balance',F.book_dependencies(F.common_book_label(bal,selections['balance'].date)),selections['balance'].date,selections['balance'].source)
    intensity_observations=[]
    eq=S.annual(data.get('balance_sheet'),'common_equity')
    for d in eq.index.intersection(historical.index):
        v=_ratio(eq[d],historical[d])
        if finite(v) and v>0:
            intensity_observations.append(v)
            F.add_dependencies(dependencies,'balance',F.book_dependencies(F.common_book_label(data.get('balance_sheet'),d)),d,'balance_sheet')
    intensity_observations.append(intensity)
    # A negative perpetual margin is not silently turned into a positive one.
    # Recovery to an industry/history reference is explicitly conditional.
    terminals=[terminal,max(0.0,ref['net_margin']),*[m for m in positive]]
    starts=margins+[first_margin]
    if forecast.get('basis')=='annual EPS consensus interpolated to next twelve months':
        starts.extend(forecast[k]/(revenue*(1+g0)) for k in ('avg','low','high') if k in forecast)
        F.merge_dependencies(dependencies,forecast)
    def params(g,start,end,capital):
        return dict(revenue=revenue,growth=g,margin_start=start,margin_terminal=end,
                    equity_to_sales=capital,terminal_equity_return=rate,terminal_growth=gt)
    base=params(g0,first_margin,terminal,intensity)
    stress=params(min(growth_cases),min(starts),min(terminals),max(intensity_observations))
    upside=params(max(growth_cases),max(starts),max(terminals),min(intensity_observations))
    warnings=list(forecast.get('warnings',[]))+rejected_growth
    if current_margin<=0:warnings.append('Recovery scenario: future profitability and access to owner funding are assumptions, not established facts.')
    if context.get('family')=='cyclical':warnings.append('Cycle scenarios use observed annual ranges and an external industry reference; they are not a certified full-cycle forecast.')
    if not ref['mapped']:warnings.append('No exact industry-reference crosswalk: broad nonfinancial reference is explicitly used for missing mature-margin/capital evidence.')
    return {'model':'operating_equity','claim':'equity','family':context.get('family','operating'),
            'quality':'estimated consolidated common-equity scenarios','base':base,'stress':stress,'upside':upside,
            **F.dependency_contract(dependencies),
            'evidence':list(forecast.get('evidence',[]))+[{'field':'revenue','value':revenue,'source':selections['income'].source},
                {'field':'historical_net_margins','value':margins,'source':'reported annual/common income and revenue'},
                {'field':'industry_reference','value':ref,'source':'dated independent industry data, not this watchlist'}],
            'assumptions':list(forecast.get('assumptions',[]))+[
                {'parameter':'initial_growth','value':g0,'basis':growth_source},
                {'parameter':'first_margin','value':first_margin,'basis':'observed common net margin; historical median for cyclicals; listing-specific EPS forecasts are scenario overlays'},
                {'parameter':'terminal_margin','value':terminal,'basis':'median positive observed net margins; industry reference when no profitable history'},
                {'parameter':'equity_to_sales','value':intensity,'basis':intensity_basis},
                {'parameter':'terminal_equity_return','value':rate,'basis':'new investment earns the policy equity return at maturity; no perpetual excess return assumed'},
                {'parameter':'capital_structure','basis':'constant financing convention implicit in after-interest common earnings; no second debt/cash/lease subtraction'},
                {'parameter':'scenario_envelope','basis':'observations and reference hypotheses, not confidence bounds or scenario probabilities'}],
            'warnings':warnings}


def project(p,years,rate,gt=None):
    """Price net owner distributions, including required future equity capital."""
    keys=('revenue','growth','margin_start','margin_terminal','equity_to_sales','terminal_equity_return')
    try:revenue,growth,m0,mt,intensity,ret=(p[k] for k in keys)
    except KeyError as exc:raise V.InvalidModel('missing operating-equity input: '+str(exc)) from exc
    gt=p.get('terminal_growth',gt)
    V.finite(revenue,growth,m0,mt,intensity,ret,rate,gt);V.horizon(years)
    if revenue<=0 or min(growth,gt)<=-1 or intensity<=0 or ret<=0 or rate<=-1:
        raise V.InvalidModel('invalid revenue, growth, equity investment or discount domain')
    if mt>0 and ret<max(gt,0):raise V.InvalidModel('terminal equity earnings cannot fund permanent growth')
    if mt<0:raise V.InvalidModel('perpetually negative net margin requires a finite recovery/runoff scenario')
    mature_intensity=mt/ret
    # Fund first-year expansion at the opening date; subsequent expansion is
    # funded from the preceding year's cash flow. No free opening investment.
    opening=-revenue*max(growth,0)*intensity
    growths=V.fade(growth,gt,years)+[gt]
    capitals=V.fade(intensity,mature_intensity,years)+[mature_intensity]
    flows=[];ledger=[]
    for year,(g,margin) in enumerate(zip(growths,V.fade(m0,mt,years)),1):
        revenue*=1+g
        earnings=revenue*margin;investment=revenue*max(growths[year],0)*capitals[year]
        distribution=earnings-investment
        flows.append(distribution);ledger.append({'year':year,'revenue':revenue,'common_earnings':earnings,
            'new_equity_investment':investment,'net_distribution':distribution})
    terminal_revenue=revenue*(1+gt)
    terminal=terminal_revenue*mt*(1-max(gt,0)/ret)
    # Zero maturity profit is a finite-payout endpoint, not a fictional perpetuity.
    value=opening+V.discount(flows,terminal,rate,gt)
    return {'cashflows':tuple(flows),'terminal_cashflow':terminal,'terminal_growth':gt,'claim':'equity',
            'opening_distribution':opening,'funding_required':-opening+sum(max(-x,0) for x in flows),'ledger':ledger,'value':value}
