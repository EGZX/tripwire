"""Economics-based automatic model selection and transparent scenario evaluation."""
from __future__ import annotations
import json
import math
import pandas as pd
from . import asset_models, financial_models, operating_models, models, valuation as V, statements as S


def build(data,selections,context):
    family=context['family']
    revenue=S.latest(selections['income'].frame,'revenue')
    if family in ('operating','cyclical') and (not math.isfinite(revenue) or revenue<=0):
        result=asset_models.build_recovery(data,selections,context)
        reason='No positive operating revenue supports an earnings forecast; disclosed asset-recovery hypotheses are quantified and unvalued project/franchise assets identified.'
    elif family in ('bank','insurer','financial_other','hybrid','holdco','mortgage_reit'):
        result=financial_models.build(data,selections,context)
        reason='Common equity is valued after financing costs; consolidated debt is not deducted from equity earnings again.'
    elif family=='reit':
        result=asset_models.build_property(data,selections,context)
        reason='Property cash/earnings and equity reinvestment; reported common AFFO is used when a compatible bridge exists.'
    elif family=='regulated':
        result=asset_models.build_utility(data,selections,context)
        reason='Consolidated common-equity model includes regulated and competitive operations without assuming all assets are rate base.'
    else:
        result=operating_models.build(data,selections,context)
        reason='Revenue, after-financing common margins and equity-investment scenarios support operating, cyclical and recovery businesses.'
    result['selection_reason']=reason
    if 'dependencies' not in result:
        result['dependencies']={'income':['net_income'],'balance':['common_equity','equity']}
    return result


def evaluate(data,selections,context,m,flags,blockers,assembly=None):
    """Conditional common-equity values; uncertainty is kept separate from failure.

    Economic scenarios have no probability weights. A nonpositive continuation
    value maps to the zero common-equity endpoint under limited liability; its
    underlying negative value and owner funding remain visible.
    """
    context=dict(context)
    supplied=context.get('supplied',{})
    if 'reported_property' in supplied:data={**data,'reported_property':supplied['reported_property']}
    if 'earnings_estimate_units' in supplied:data={**data,'earnings_estimate_units':supplied['earnings_estimate_units']}
    if 'regulatory' in supplied:context['regulatory']=supplied['regulatory']
    policy=context['hurdle']
    if 'discount_rate' in supplied:
        basis=supplied.get('discount_rate_basis')
        if basis not in ('cost_of_equity','policy'):
            blockers.append('common-equity model cannot use a firm discount rate; supply cost_of_equity or an explicit firm model')
        else:context['hurdle']=supplied['discount_rate']
    try:
        plan=build(data,selections,context)
    except (KeyError,ValueError,TypeError,OverflowError,OSError) as exc:
        return {**m,'not_rankable':'automatic model needs essential evidence: '+str(exc),
                'flags':'; '.join(dict.fromkeys(flags)),'model_status':'missing essential evidence'}
    # A reconstructed trailing flow consumes the actual four quarterly inputs.
    # Shares/EPS are not summed and never inherit flow dependencies by analogy.
    if assembly:
        for dependency in list(plan.get('dependency_evidence',[])):
            category=dependency['statement']
            detail=assembly.ledger.get(category,{})
            if (dependency['period_type']=='ttm' and detail.get('method')=='four_quarter_sum'
                    and dependency['source']==detail.get('selected_source')
                    and dependency['period_end']==detail.get('period_end')):
                field=detail.get('fields',{}).get(dependency['field'],{})
                if field.get('operation')=='sum':
                    for period in field.get('periods',[]):
                        financial_models.add_dependencies(plan,category,{dependency['field']},period,detail['quarter_source'])
        plan.update(financial_models.dependency_contract(plan))
    results={};years=context['years'];rate=context['hurdle'];gt=context['terminal_growth']
    projector=(operating_models.project if plan['model']=='operating_equity' else
               asset_models.project_recovery if plan['model']=='asset_recovery' else financial_models.project)
    for case in ('base','stress','upside'):
        try:
            raw=projector(plan[case],years,rate,gt)
            projection=models.evaluate(plan['model'],plan[case],years,rate,gt)
            results[case]={'raw_value':projection.value,'value':max(0.,projection.value),
                'value_at_policy_hurdle':projection.opening_distribution+V.discount(list(projection.cashflows),projection.terminal_cashflow,policy,projection.terminal_growth),
                'cashflows':list(projection.cashflows),'terminal_cashflow':projection.terminal_cashflow,
                'terminal_growth':projection.terminal_growth,'opening_distribution':projection.opening_distribution,
                'funding_required':raw.get('funding_required',0.),'ledger':raw.get('ledger',[])}
        except (KeyError,ValueError,TypeError,OverflowError) as exc:
            blockers.append(case+' automatic scenario: '+str(exc))
    checks=[]
    for fact in supplied.get('filing_checks',[]):
        latest=S.filing_checks(selections,[fact])[0]
        checks.append(latest)
        category=fact['statement']
        source_names={'income':('income_stmt','quarterly_income_stmt'),
                      'balance':('balance_sheet','quarterly_balance_sheet'),
                      'cashflow':('cashflow','quarterly_cashflow')}[category]
        for source_name in source_names:
            frame=data.get(source_name)
            if frame is None or frame.empty:continue
            normalized=frame.copy();normalized.columns=S.period_dates(normalized.columns)
            target=pd.Timestamp(fact['period_end'])
            if target not in normalized.columns:continue
            historical=S.Selection(normalized.loc[:,[target]],source_name,target,max(normalized.columns),())
            check=S.filing_checks({**selections,category:historical},[fact])[0]
            if check['status']!='not_applicable':checks.append({**check,'historical_source':source_name})
    for check in checks:
        used=check['field'] in plan['dependencies'].get(check['statement'],[])
        dated=[d for d in plan.get('dependency_evidence',[]) if d['statement']==check['statement'] and d['field']==check['field']]
        if dated:
            used=any(d['period_end']==str(check['period_end']) and d['period_type']==check['period_type'] for d in dated)
        check['used_by_model']=used
        if used and check['status'] in ('contradiction','basis_unreconciled'):
            blockers.append(f"required {check['statement']} {check['field']} has unresolved filing evidence")
    m['filing_reconciliation']=json.dumps(checks,sort_keys=True)
    warnings=list(plan.get('warnings',[]))
    if any('capitalization estimated from quote and shares' in f for f in flags):
        warnings.append('Market capitalization is conditional on the quote and share count representing the same economic units; the provider aggregate cap is unavailable.')
    if assembly:
        warnings.extend(x['detail'] for x in assembly.uncertainties if x.get('detail'))
        m['evidence_assembly']=json.dumps(assembly.ledger,sort_keys=True)
        m['capitalization_scenarios']=json.dumps(assembly.capitalizations,sort_keys=True)
    m.update(model=plan['model'],model_label=models.LABELS[plan['model']],model_quality=plan.get('quality','automatic scenario estimate'),
             model_status='estimated',model_selection_reason=plan['selection_reason'],
             model_assumptions=json.dumps(plan.get('assumptions',[]),sort_keys=True),
             model_evidence=json.dumps(plan.get('evidence',[]),sort_keys=True,default=str),
             model_uncertainties=json.dumps(list(dict.fromkeys(warnings))),
             scenario_parameters=json.dumps({k:plan[k] for k in ('base','stress','upside')},sort_keys=True),
             scenario_results=json.dumps(results,sort_keys=True),
             valuation_basis=('conditional common-equity scenarios at a sourced equity return; separate purchase policy' if 'discount_rate' in supplied else 'conditional common-equity scenarios at the declared policy return; not calibrated fair-value probabilities'),
             implied_return_claim='equity',discount_rate=rate,decision_rate=rate,floor_rate=rate,
             implied_return_status='not evaluated',
             implied_growth_status='not identified by multi-driver scenarios',
             assumptions_source='automatic evidence/assumption ledger; company observations and dated independent references')
    growth_key='growth' if plan['model']=='operating_equity' else 'book_growth_start'
    m['est_growth']=plan['base'].get(growth_key)
    m['adverse_growth']=plan['stress'].get(growth_key)
    m['growth_measure']='not modeled' if plan['model']=='asset_recovery' else 'revenue' if growth_key=='growth' else 'common book equity'
    m['horizon_years']=years
    retired={'growth_cap','growth_floor','return_cap','return_smoothing','insurer_smooth_earnings',
             'bank_min_equity_to_assets','adverse_growth_mode','adverse_growth_haircut','cyclical_adverse_margin','cyclical_adverse_growth_at_terminal'}
    present=sorted(retired.intersection(context.get('cfg',{}).get('valuation',{})))
    if present:
        warnings.append('Retired funded-proxy settings are not used by the general scenario model: '+', '.join(present))
        m['model_uncertainties']=json.dumps(list(dict.fromkeys(warnings)))
    if blockers:
        m.update(not_rankable='; '.join(dict.fromkeys(blockers)),flags='; '.join(dict.fromkeys(flags)),model_status='unresolved essential evidence')
        return m
    base,stress,upside=(results[k]['value'] for k in ('base','stress','upside'))
    ceiling=max(0.,min(base,results['base']['value_at_policy_hurdle']))
    cap=m['mcap_fc'];price=m['price']
    scope_prices=[]
    if assembly and assembly.capitalizations:
        fx=context.get('quote_to_reporting_fx')
        if isinstance(fx,(int,float)) and math.isfinite(fx) and fx>0:
            for alternative in assembly.capitalizations:
                denominator=alternative['value_quote']*fx
                if denominator>0:scope_prices.append({'basis':alternative['id'],'conditional':alternative['conditional'],
                    'base_value_per_quote_unit':price*base/denominator,'assumption':alternative.get('assumption',alternative['scope'])})
    m['quote_scope_scenarios']=json.dumps(scope_prices,sort_keys=True)
    m.update(not_rankable='',value_reasonable=base,value_adverse=stress,value_upside=upside,
             entry_ceiling=ceiling,value_at_policy_hurdle=results['base']['value_at_policy_hurdle'],ceiling_px=price*ceiling/cap,
             reasonable_px=price*base/cap,adverse_px=price*stress/cap,upside_px=price*upside/cap,
             valuation_range_low_px=price*min(base,stress,upside)/cap,
             valuation_range_high_px=price*max(base,stress,upside)/cap,
             base_cashflows=json.dumps(results['base']['cashflows']),stress_cashflows=json.dumps(results['stress']['cashflows']),
             funding_required=results['base']['funding_required'],funding_required_stress=results['stress']['funding_required'],
             entry_score_raw=100*(1-cap/ceiling) if ceiling>0 else None,
             entry_score=max(-100.,100*(1-cap/ceiling)) if ceiling>0 else -100.,
             adverse_score=max(-100.,100*(1-cap/stress)) if stress>0 else -100.,
             entry_score_basis='standard price/ceiling ratio' if ceiling>0 else 'zero-equity endpoint; displayed score is the limiting -100 floor',
             terminal_value_fraction=(results['base']['terminal_cashflow']/(rate-results['base']['terminal_growth'])/(1+rate)**years/results['base']['raw_value'])
                 if results['base']['terminal_cashflow'] and results['base']['raw_value'] else 0.)
    if stress>base or upside<base:flags.append('scenario ordering crosses: displayed envelope is not a probability bound')
    if results['base']['funding_required']>0:flags.append('owner capital contributions included in valuation; financing availability is uncertain')
    if base==0:flags.append('base continuation value nonpositive; limited-liability endpoint shown, unmodeled option value not asserted')
    if plan['model']=='asset_recovery':flags.append('unvalued operating or project assets; partial recovery estimate only')
    future=results['base']['cashflows']+[results['base']['terminal_cashflow']]
    monotone=min(future)>=0 and max(future)>0
    m['decision_rate']=max(rate,policy) if monotone else math.nan
    solution=V.solve_distribution_return(results['base']['cashflows'],results['base']['terminal_cashflow'],
        cap,results['base']['terminal_growth'],results['base']['opening_distribution'])
    m['implied_return']=solution.value
    m['implied_return_status']=solution.status+(': '+solution.reason if solution.reason else '')
    if plan['model']=='asset_recovery':
        m['valuation_basis']='partial asset recovery at disclosed carrying values; no operating or project value asserted'
    if m['class'] in ('holdco','preprofit'):m['class']='asset'
    # Data-quality failures remain gates. Historical-cycle/lease uncertainty is
    # modeled and disclosed; it is not a missing-equation exception.
    m['flags']='; '.join(dict.fromkeys(flags))
    return m
