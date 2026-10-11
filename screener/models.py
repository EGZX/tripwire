"""Production sector models using explicit, source-labeled driver assumptions.

No invented regulatory capital, maintenance capex, asset values or survival
probabilities. Missing sector inputs are an applicability failure, not zero.
Experimental statistical estimation is deliberately outside this module.
"""
from __future__ import annotations
from dataclasses import dataclass
from . import valuation as V

LABELS={"funded_earnings":"Funded earnings proxy","operating_forecast":"Operating cash-flow forecast",
        "bank_capital":"Bank capital distributions","insurer_capital":"Insurance capital distributions",
        "financial_capital":"Financial capital distributions",
        "reit_affo":"Property cash distributions","regulated_rate_base":"Regulated asset-base model",
        "asset_nav":"Attributable asset value","finite_project":"Finite-life project",
        "equity_residual_income":"Common-equity return and capital scenarios",
        "operating_equity":"Operating earnings and equity-investment scenarios",
        "sum_of_parts":"Business-component sum of parts","asset_recovery":"Balance-sheet recovery scenarios (partial value)"}

@dataclass(frozen=True)
class Projection:
    cashflows: tuple[float,...]
    terminal_cashflow: float
    terminal_growth: float
    rate: float
    claim: str
    model: str
    opening_distribution: float = 0.0

    @property
    def value(self):
        return self.opening_distribution + V.discount(list(self.cashflows),self.terminal_cashflow,self.rate,self.terminal_growth)

    @property
    def terminal_pv(self):
        return self.terminal_cashflow/(self.rate-self.terminal_growth)/(1+self.rate)**len(self.cashflows) if self.terminal_cashflow else 0.0

def _require(p: dict,*keys: str) -> list[float]:
    missing=[k for k in keys if k not in p]
    if missing:
        raise V.InvalidModel("missing model inputs: "+", ".join(missing))
    values=[p[k] for k in keys]
    V.finite(*values)
    return values

def _positive(**values):
    if any(v<=0 for v in values.values()):
        raise V.InvalidModel("must be positive: "+", ".join(values))

def operating(p:dict, years:int, rate:float, gt:float) -> Projection:
    """Revenue/margin FCFF for operating, cyclical and recovering businesses.

    Sales/capital funds incremental revenue. Sustaining net capex is explicit;
    contraction does not automatically sell assets. Negative FCFF needs a
    source-backed financing capacity, not assumed free future share issuance.
    """
    revenue,growth,m0,mt,tax,turnover,maintenance,capacity=_require(p,
        "revenue","growth","margin_start","margin_terminal","tax_rate","sales_to_capital",
        "maintenance_net_capex","funding_capacity")
    _positive(revenue=revenue,sales_to_capital=turnover)
    if not 0<=tax<=1 or min(maintenance,capacity)<0 or min(growth,gt)<=-1 or not -1<=m0<=1 or not -1<=mt<=1:
        raise V.InvalidModel("invalid operating margin, tax, investment or funding domain")
    flows=[];available=capacity
    initial=revenue
    for g,margin in zip(V.fade(growth,gt,years),V.fade(m0,mt,years)):
        previous=revenue;revenue*=1+g
        earnings=revenue*margin
        # No assumed NOL asset: losses have no immediate cash tax refund.
        nopat=earnings-max(earnings,0)*tax
        investment=max(revenue-previous,0)/turnover+maintenance*revenue/initial
        cf=nopat-investment
        if cf<0:
            available+=cf
            if available<0:
                raise V.InvalidModel("operating forecast exceeds documented funding capacity")
        flows.append(cf)
    terminal_revenue=revenue*(1+gt)
    terminal_nopat=terminal_revenue*mt-max(terminal_revenue*mt,0)*tax
    terminal=terminal_nopat-max(terminal_revenue-revenue,0)/turnover-maintenance*terminal_revenue/initial
    if terminal<=0:
        raise V.InvalidModel("terminal operating cash flow is not positive")
    return Projection(tuple(flows),terminal,gt,rate,"firm","operating_forecast")

def capital_distributions(p:dict, years:int, rate:float, gt:float, family:str) -> Projection:
    """Banks/insurers: earnings less growth in required regulatory capital.

    Capital base = RWA for a bank, a documented risk/exposure base for an insurer.
    The ratio is the required capital per unit of that same base (not book E/A).
    Excess capital is distributed only if explicitly declared releasable.
    """
    income,growth,capital_growth,capital,base,ratio,payout_limit,releasable=_require(p,
        "net_income","growth","capital_growth","available_capital","capital_base","required_capital_ratio",
        "payout_limit","releasable_excess")
    _positive(net_income=income,capital_base=base,required_capital_ratio=ratio)
    if min(growth,capital_growth,gt)<=-1 or capital<0 or not 0<=payout_limit<=1 or releasable<0:
        raise V.InvalidModel("invalid capital model domain")
    if capital<base*ratio or releasable>capital-base*ratio:
        raise V.InvalidModel("regulatory capital shortfall or unsupported capital release")
    if (releasable>0 or p.get("release_terminal_excess")) and p.get("excess_income_excluded") is not True:
        raise V.InvalidModel("capital release requires earnings excluding income from released excess")
    capital-=releasable
    flows=[]
    for g,cg in zip(V.fade(growth,gt,years),V.fade(capital_growth,gt,years)):
        income*=1+g;base*=1+cg
        surplus=capital+income-base*ratio
        if surplus<0:
            raise V.InvalidModel("forecast violates required regulatory capital")
        dividend=min(income*payout_limit,surplus)
        flows.append(dividend);capital+=income-dividend
    # Stable capital growth must be funded out of earnings; temporary excess is
    # not repeatedly capitalized as a perpetual dividend.
    terminal_income=income*(1+gt)
    terminal=min(terminal_income*payout_limit,terminal_income-max(base*gt,0)*ratio)
    if terminal<=0:
        raise V.InvalidModel("terminal distributions cannot fund required capital")
    # Retained surplus beyond requirements remains an equity asset at the horizon.
    # Distribute it once only when permission to release excess is documented.
    if p.get("release_terminal_excess") is True:
        flows[-1]+=max(capital-base*ratio,0)
    return Projection(tuple(flows),terminal,gt,rate,"equity",family+"_capital",releasable)

def reit(p:dict, years:int, rate:float, gt:float) -> Projection:
    """Common-equity AFFO distributions with explicit growth-equity reinvestment.

    Input FFO must be attributable to common holders, after financing expenses.
    Straight-line rent (noncash), recurring property capex and leasing costs are deductions, not optional zeros;
    the same bridge as the automatic FFO-to-AFFO path.
    """
    ffo,straight_line,capex,leasing,growth,return_new=_require(p,"ffo_common","straight_line_rent","recurring_capex","leasing_costs","growth","return_on_new_equity")
    if min(capex,leasing)<0:
        raise V.InvalidModel("property maintenance and leasing costs must be nonnegative")
    affo=ffo-straight_line-capex-leasing
    _positive(affo=affo)
    ds,term=V.distributions(affo,growth,gt,years,return_new,p.get("terminal_return",return_new))
    if term<=0:raise V.InvalidModel("REIT terminal distributions are not positive; use asset NAV")
    return Projection(tuple(ds),term,gt,rate,"equity","reit_affo")

def regulated(p:dict, years:int, rate:float, gt:float) -> Projection:
    """Regulated operating value from net rate base and achieved after-tax return.

    Rate base is already net of regulatory depreciation; changes are net capital
    investment. Achieved return and regulatory lag must be supplied, not inferred
    from an allowed headline rate or general book equity.
    """
    base,growth,earned,lag,capacity=_require(p,"rate_base","growth","earned_after_tax_return","regulatory_lag_years","funding_capacity")
    _positive(rate_base=base,earned_after_tax_return=earned)
    if lag<0 or int(lag)!=lag or min(growth,gt)<=-1 or capacity<0:
        raise V.InvalidModel("invalid regulatory lag or growth")
    lag=int(lag)
    bases=p.get("rate_base_history",[base] if lag==0 else None)
    if not isinstance(bases,list) or len(bases)!=lag+1:
        raise V.InvalidModel("regulatory lag requires the actual opening rate-base history")
    V.finite(*bases)
    if min(bases)<=0 or bases[-1]!=base:raise V.InvalidModel("rate-base history must end at current rate base")
    bases=list(bases);flows=[]
    # Run out the actual lag pipeline before assuming a stationary terminal.
    for g in V.fade(growth,gt,years)+[gt]*lag:
        previous=base;base*=1+g
        income=bases[-1-lag]*earned
        cf=income-max(base-previous,0)
        capacity+=min(cf,0)
        if capacity<0:raise V.InvalidModel("regulated forecast exceeds documented funding capacity")
        flows.append(cf);bases.append(base)
    terminal_income=base/(1+gt)**lag*earned
    terminal=terminal_income-max(base*gt,0)
    if terminal<=0:
        raise V.InvalidModel("regulated terminal return cannot fund growth")
    return Projection(tuple(flows),terminal,gt,rate,"firm","regulated_rate_base")

def asset_nav(p:dict) -> float:
    """Attributable asset/SOTP value less all explicit parent claims and costs."""
    values=_require(p,"attributable_assets","parent_debt","other_claims","tax_leakage","holding_cost_pv")
    if any(v<0 for v in values):
        raise V.InvalidModel("NAV inputs must be nonnegative")
    return values[0]-sum(values[1:])

def project(p:dict, rate:float) -> Projection:
    """Finite-life firm cash flows; no invented perpetual growth or probabilities."""
    flows=p.get("cashflows")
    if not isinstance(flows,list) or not flows:
        raise V.InvalidModel("finite project requires annual cashflows")
    V.finite(*flows)
    salvage,closure,capacity=_require(p,"salvage_value","closure_cost","funding_capacity")
    if min(salvage,closure,capacity)<0:
        raise V.InvalidModel("invalid finite project funding/salvage/closure")
    result=list(flows);result[-1]+=salvage-closure
    available=capacity
    for cf in result:
        available+=cf
        if available<0:
            raise V.InvalidModel("project exceeds documented funding capacity")
    return Projection(tuple(result),0.0,0.0,rate,"firm","finite_project")

def evaluate(model:str,p:dict,years:int,rate:float,gt:float) -> Projection | float:
    if model=='asset_recovery':
        from .asset_models import project_recovery
        result=project_recovery(p,years,rate,gt)
        return Projection(result['cashflows'],0.,0.,rate,'equity',model,result['opening_distribution'])
    if model in ('equity_residual_income','operating_equity'):
        from . import financial_models, operating_models
        module=financial_models if model=='equity_residual_income' else operating_models
        result=module.project(p,years,rate,gt)
        return Projection(tuple(result['cashflows']),result['terminal_cashflow'],result['terminal_growth'],
                          rate,'equity',model,result.get('opening_distribution',0.0))
    if model=='sum_of_parts':
        from .business_models import sum_of_parts
        return sum_of_parts(p,years,rate,gt,evaluate).value
    if model=="asset_nav":return asset_nav(p)
    if model=="finite_project":
        V.finite(rate)
        if rate<=-1:raise V.InvalidModel("finite project rate must be above -100%")
        return project(p,rate)
    V.finite(rate,gt);V.horizon(years)
    if rate<=-1 or rate<=gt:
        raise V.InvalidModel("model rate must exceed terminal growth")
    if model=="operating_forecast":return operating(p,years,rate,gt)
    if model in ("bank_capital","insurer_capital","financial_capital"):
        return capital_distributions(p,years,rate,gt,model.split('_')[0])
    if model=="reit_affo":return reit(p,years,rate,gt)
    if model=="regulated_rate_base":return regulated(p,years,rate,gt)
    raise V.InvalidModel(f"unsupported model {model}")
