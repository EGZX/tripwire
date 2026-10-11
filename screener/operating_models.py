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

import numpy as np
import pandas as pd
from . import statements as S, valuation as V
from .policy import get as policy
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
    'Credit Services':'Financial Svcs. (Non-bank & Insurance)','Electronic Components':'Electronics (General)',
    'Electrical Equipment & Parts':'Electrical Equipment','Communication Equipment':'Telecom. Equipment',
}


# Judgement thresholds (margin limit, growth half-life, cyclical normalisation, reference sample size, estimate
# tolerance) live in screener/policy.py and are read at call time, so [model_policy] in config.toml applies.


@lru_cache(maxsize=1)
def references():
    try:return json.loads((Path(__file__).parent/'resources/industry_benchmarks.json').read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:raise V.InvalidModel('industry-reference evidence unavailable: '+str(exc)) from exc


def benchmark(info):
    dataset=references();industry=info.get('industry','')
    name=INDUSTRY_REFERENCE.get(industry,industry)
    mapped=name in dataset['industries']
    if mapped and (dataset['industries'][name].get('firms') or 0)<policy().min_reference_firms:
        mapped=False  # an 8-firm average is not a reference for anyone
    if not mapped:name='Total Market (without financials)'
    return {**dataset['industries'][name],'industry':name,'mapped':mapped,
            'as_of':dataset['as_of'],'geography':dataset['geography'],'sources':dataset['sources']}


def finite(x):
    # numpy scalars count: provider tables often hold int64/float64 values (e.g. revenue-estimate 'avg').
    return isinstance(x,(int,float,np.integer,np.floating)) and not isinstance(x,(bool,np.bool_)) and math.isfinite(x)


def _ratio(n,d):
    return n/d if finite(n) and finite(d) and d>0 else math.nan


def _observed_ratios(frame,numerator,denominator):
    n,d=S.annual(frame,numerator),S.annual(frame,denominator)
    if n.empty or d.empty:return []
    return [float(n[x]/d[x]) for x in n.index.intersection(d.index) if finite(n[x]) and finite(d[x]) and d[x]>0]




def _close(a,b,tol=None):
    tol=policy().estimate_base_tolerance if tol is None else tol
    return finite(a) and finite(b) and b>0 and abs(a/b-1)<=tol


def estimate_growths(estimates,last_fy_revenue,currency,ttm_revenue=math.nan):
    """Provider fiscal-year growth estimates, used only when their base year reconciles to reported revenue.

    The provider's growth divides its estimate by its own yearAgoRevenue, which is internally consistent but not
    always the company's reported year: a quarter or nine months for some Japanese issuers (6301.T: +297%), a
    fiscal year newer than the statements (MU), or another revenue definition (net vs gross). Rather than mix
    periods or definitions, the estimates are used only when '0y' yearAgoRevenue matches the last annual or the
    TTM revenue within policy().estimate_base_tolerance; otherwise none is used and observed growth applies.
    Returns (growths, notes, base) with base 'last_fy' or 'ttm'."""
    if not isinstance(estimates,pd.DataFrame) or 'growth' not in estimates:return {},[],'last_fy'
    column=lambda p,c:estimates.loc[p,c] if p in estimates.index and c in estimates else math.nan
    ya0=column('0y','yearAgoRevenue');base='last_fy';notes=[]
    est_ccy=column('0y','currency') if '0y' in estimates.index else column('+1y','currency')
    if isinstance(est_ccy,str) and currency and est_ccy!=currency:
        # Revenues in different currencies cannot be compared; a growth RATE is currency-neutral.
        notes.append(f"Revenue estimates are in {est_ccy}, statements in {currency}: base year not reconciled; provider growth rates used.")
    elif finite(ya0):
        if _close(ya0,last_fy_revenue):base='last_fy'
        elif _close(ya0,ttm_revenue):base='ttm'
        else:
            ref=max([x for x in (last_fy_revenue,ttm_revenue) if finite(x)] or [math.nan])
            return {},[f"Revenue-growth estimates not used: their base-year revenue ({ya0:.4g}) does not reconcile to reported revenue ({ref:.4g}) within {policy().estimate_base_tolerance:.0%} (period or definition differs)."],base
    out={p:float(column(p,'growth')) for p in ('0y','+1y') if finite(column(p,'growth')) and column(p,'growth')>-1}
    return out,notes,base


def ntm_growth(estimates,income_date,last_fy_end,last_fy_revenue=math.nan,currency=None,ttm_revenue=math.nan):
    """Revenue growth over the twelve months after the TTM date, from the fiscal-year estimates it overlaps.

    '0y' is the fiscal year after its base year (see estimate_growths). The share of the next twelve months that
    falls into '0y' weights its growth against '+1y'; a TTM ending at the base year end gives exactly '0y'.
    Returns (ntm_growth, next_year_growth, basis, notes) or (None, None, reason, notes)."""
    g,notes,base=estimate_growths(estimates,last_fy_revenue,currency,ttm_revenue)
    g={k:v for k,v in g.items() if finite(v) and v>-1}
    if not g:return None,None,'no finite revenue-growth estimate',notes
    base_end=income_date if base=='ttm' else last_fy_end
    if '0y' in g and '+1y' in g and income_date is not None and base_end is not None:
        fy0_end=base_end+pd.DateOffset(years=1)
        w=min(1.0,max(0.0,(fy0_end-income_date).days/(fy0_end-base_end).days))  # share of the fiscal year still ahead
        return w*g['0y']+(1-w)*g['+1y'],g['+1y'],f'provider fiscal revenue-growth estimates time-weighted to the next twelve months ({w:.0%} current year)',notes
    if '0y' in g:return g['0y'],g.get('+1y',g['0y']),'provider current fiscal-year revenue-growth estimate',notes
    return g['+1y'],g['+1y'],'provider next fiscal-year revenue-growth estimate (current year unavailable)',notes


def growth_path(g1,g2,gt,years):
    """Year 1 and 2 follow the estimates; afterwards the excess over terminal growth decays geometrically
    (half-life policy().growth_half_life). A one-year estimate is not compounded for a decade."""
    path=[g1,g2][:years]
    for t in range(3,years+1):
        path.append(gt+(g2-gt)*0.5**((t-2)/policy().growth_half_life))
    return path


def clean_margins(data,historical):
    """Annual clean common margins (continuing operations, before after-tax unusual items) and exclusions."""
    clean=S.clean_common_income(data.get('income_stmt'));out=[];excluded=[]
    for d in clean.index.intersection(historical.index):
        r,c=historical[d],clean[d]
        if not(finite(r) and finite(c) and r>0):continue
        m=c/r
        if m>policy().margin_limit:
            excluded.append({'period':d.date().isoformat(),'margin':m,'reason':'clean margin above 100% of revenue'})
            continue
        out.append((d,m))
    return out,excluded


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
    last_fy_end=historical.index.max() if len(historical) else None
    last_fy_revenue=float(historical[last_fy_end]) if last_fy_end is not None and finite(historical[last_fy_end]) else math.nan
    forecast_growth,next_growth,growth_source,growth_notes=ntm_growth(data.get('revenue_estimate'),selections['income'].date,last_fy_end,
                                                                      last_fy_revenue,info.get('financialCurrency'),revenue)
    rejected_growth=[] if forecast_growth is not None else ['No usable revenue-growth estimate ('+growth_source+'); observed growth evidence used instead.']
    rejected_growth.extend(growth_notes)
    estimates=data.get('revenue_estimate')
    if isinstance(estimates,pd.DataFrame) and 'growth' in estimates:
        for period in ('0y','+1y'):
            if period in estimates.index and not(finite(estimates.loc[period,'growth']) and estimates.loc[period,'growth']>-1):
                rejected_growth.append('Nonfinite or inadmissible '+period+' revenue-growth estimate excluded.')
    gt=context['terminal_growth'];rate=context['hurdle'];years=context.get('years',10)
    if forecast_growth is None:
        g0=median(growth_history) if growth_history else gt;next_growth=g0
        growth_source='median observed annual revenue growth' if growth_history else 'terminal-growth policy (no growth evidence)'
    else:g0=forecast_growth
    growth_cases=growth_history+[g0,gt]
    dated_margins,excluded_margins=clean_margins(data,historical)
    margins=[m for _,m in dated_margins]
    clean_current=S.clean_latest(inc)
    current_margin=_ratio(clean_current if finite(clean_current) else observed_income,revenue)
    if finite(current_margin) and current_margin>policy().margin_limit:
        excluded_margins.append({'period':'TTM','margin':current_margin,'reason':'clean margin above 100% of revenue'})
        current_margin=math.nan
    ttm_is_annual=last_fy_end is not None and selections['income'].date==last_fy_end
    dependencies={}
    F.add_dependencies(dependencies,'income',{'revenue','net_income'},selections['income'].date,selections['income'].source)
    annual_income=S.annual(data.get('income_stmt'),'net_income')
    for period in historical.index:
        if finite(historical[period]) and historical[period]>0:
            F.add_dependencies(dependencies,'income',{'revenue'},period,'income_stmt')
            if finite(annual_income.get(period,math.nan)):
                F.add_dependencies(dependencies,'income',{'net_income'},period,'income_stmt')
    history_margins=list(margins)
    if finite(current_margin) and not ttm_is_annual:margins.append(current_margin)  # TTM = last FY: count it once
    # Listing-specific EPS panels must not create a different issuer base model
    # for an ADR and its ordinary shares. Compatible EPS estimates enrich the
    # scenario envelope; issuer-level reported margins anchor the base.
    if not finite(current_margin):current_margin=median(margins) if margins else math.nan
    if context.get('family')=='cyclical' and history_margins:
        first_margin=current_margin+policy().cyclical_normalisation*(median(history_margins)-current_margin)
    else:first_margin=current_margin
    if not finite(first_margin):raise V.InvalidModel('reported common margin is required')
    positive=[m for m in margins if m>0]
    # A mature margin needs a mostly profitable record; one good year among losses (often non-operating) is not one.
    profitable_record=len(positive)>=2 and len(positive)>=len(margins)/2
    terminal=median(positive) if profitable_record else ref['net_margin']
    terminal_basis=('median clean positive margin over a mostly profitable record' if profitable_record else
                    'industry reference: own record is not mostly profitable ('+str(len(positive))+' of '+str(len(margins))+' periods)')
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
    # Own history sets the stress floor when it is long enough; a small-sample industry average does not.
    terminals=[terminal,*positive] if len(positive)>=3 else [terminal,max(0.0,ref['net_margin']),*positive]
    starts=margins+[first_margin]
    if forecast.get('basis')=='annual EPS consensus interpolated to next twelve months':
        starts.extend(forecast[k]/(revenue*(1+g0)) for k in ('avg','low','high') if k in forecast)
        F.merge_dependencies(dependencies,forecast)
    typical=median(intensity_observations)
    upside_intensities=[x for x in intensity_observations if x>=0.25*typical] or [intensity]  # drop near-zero-book years
    def params(g,start,end,capital,g2=None):
        return dict(revenue=revenue,growth=g,growth_path=growth_path(g,g if g2 is None else g2,gt,years),margin_start=start,
                    margin_terminal=end,equity_to_sales=capital,terminal_equity_return=rate,terminal_growth=gt)
    base=params(g0,first_margin,terminal,intensity,next_growth)
    # Stress must lower value. Lower growth lowers value only when new equity earns more than the hurdle, so both
    # growth directions are priced and the lower one is kept.
    stress_options=[params(g,min(starts),min(terminals),max(intensity_observations)) for g in (min(growth_cases),g0)]
    def priced(p):
        try:return project(p,years,rate,gt)['value']
        except V.InvalidModel:return math.inf
    stress=min(stress_options,key=priced)
    upside=params(max(growth_cases),max(starts),max(terminals),min(upside_intensities))
    warnings=list(forecast.get('warnings',[]))+rejected_growth
    if current_margin<=0:warnings.append('Recovery scenario: future profitability and access to owner funding are assumptions, not established facts.')
    if context.get('family')=='cyclical':warnings.append('Cycle scenarios use observed annual ranges and an external industry reference; they are not a certified full-cycle forecast.')
    if not ref['mapped']:warnings.append('No exact industry-reference crosswalk: broad nonfinancial reference is explicitly used for missing mature-margin/capital evidence.')
    if excluded_margins:warnings.append('Margin observations above 100% of revenue excluded: '+', '.join(x['period'] for x in excluded_margins))
    return {'model':'operating_equity','claim':'equity','family':context.get('family','operating'),
            'quality':'estimated consolidated common-equity scenarios','base':base,'stress':stress,'upside':upside,
            **F.dependency_contract(dependencies),
            'evidence':list(forecast.get('evidence',[]))+[{'field':'revenue','value':revenue,'source':selections['income'].source},
                {'field':'historical_net_margins','value':margins,'source':'clean common income (continuing operations, before after-tax unusual items) / revenue'},
                {'field':'excluded_margins','value':excluded_margins,'source':'margins above 100% of revenue are not operating evidence'},
                {'field':'industry_reference','value':ref,'source':'dated independent industry data, not this watchlist'}],
            'assumptions':list(forecast.get('assumptions',[]))+[
                {'parameter':'initial_growth','value':g0,'basis':growth_source},
                {'parameter':'growth_path','value':base['growth_path'],'basis':'years 1-2 from the estimates, then excess growth halves every '+f'{policy().growth_half_life:g}'+' years'},
                {'parameter':'first_margin','value':first_margin,'basis':'clean common margin (TTM); cyclicals halfway to their historical median; listing-specific EPS forecasts are scenario overlays'},
                {'parameter':'terminal_margin','value':terminal,'basis':terminal_basis},
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
    supplied=p.get('growth_path')
    if supplied is not None:
        if not isinstance(supplied,(list,tuple)) or len(supplied)!=years:raise V.InvalidModel('growth_path must contain one rate per forecast year')
        V.finite(*supplied)
        if min(supplied)<=-1:raise V.InvalidModel('growth_path rates must exceed -100%')
        growths=list(supplied)+[gt];growth=growths[0]
    else:growths=V.fade(growth,gt,years)+[gt]
    opening=-revenue*max(growth,0)*intensity
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
