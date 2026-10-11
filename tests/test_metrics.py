"""Entry-ceiling model on synthetic Yahoo-shaped data: scenarios, essentials, period alignment."""
import math
import json
from datetime import date
from statistics import median

import pandas as pd
import pytest

from screener import metrics
from screener import valuation as V

CFG = {"valuation": {"equity_risk_premium": 0.045, "years": 10, "growth_cap": 0.30, "growth_floor": -0.05,
                     "return_cap": 0.60, "adverse_growth_mode": "half", "adverse_growth_haircut": 0.05,
                     "adverse_return_spread": 0.0, "min_rate_over_terminal_growth": 0.02, "min_hurdle": 0.0,
                     "return_smoothing": False, "cyclical_adverse_margin": "median",
                     "cyclical_adverse_growth_at_terminal": False, "insurer_smooth_earnings": False,
                     "bank_min_equity_to_assets": 0.04, "base_currency": "EUR", "stale_after_days": 270},
       "rates": {"USD": {"rf": 0.05, "g": 0.03}, "EUR": {"rf": 0.03, "g": 0.02}, "CHF": {"rf": 0.005, "g": 0.01},
                 "KZT": {"rf": 0.14, "g": 0.06, "crp": 0.03}}}


def cfg_with(**valuation):
    return {"valuation": {**CFG["valuation"], **valuation}, "rates": CFG["rates"]}
YEARS = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31"), pd.Timestamp("2022-12-31")]
TTM = [pd.Timestamp("2026-06-30")]


class FX:
    def rate(self, a, b):
        return 1.0


def frame(rows: dict, cols) -> pd.DataFrame:
    return pd.DataFrame({c: [v[i] for v in rows.values()] for i, c in enumerate(cols)}, index=list(rows), dtype=float)


def operating(**over) -> dict:
    """Profitable operating company: revenue 1000 (grew 10%/y), EBIT 200, tax 20%, debt 100, cash 50, mcap 2000."""
    inc = {"Total Revenue": [1000, 909, 826, 751], "Gross Profit": [500, 455, 413, 376],
           "Operating Income": [200, 182, 165, 150], "Pretax Income": [190, 172, 155, 140], "Tax Provision": [38, 34, 31, 28],
           "Net Income Common Stockholders": [152, 138, 124, 112], "Diluted Average Shares": [100, 100, 100, 100]}
    cf = {"Operating Cash Flow": [220, 200, 180, 165], "Capital Expenditure": [-40, -36, -33, -30],
          "Stock Based Compensation": [10, 9, 8, 7], "Reconciled Depreciation": [30, 27, 25, 22]}
    bal = {"Total Debt": [100, 100, 100, 100], "Cash Cash Equivalents And Short Term Investments": [50, 45, 40, 35],
           "Stockholders Equity": [600, 500, 400, 300], "Total Assets": [900, 800, 700, 600], "Minority Interest": [0, 0, 0, 0], "Preferred Stock Equity": [0, 0, 0, 0]}
    d = {"symbol": "TEST",
         "info": {"longName": "Test Co", "sector": "Technology", "industry": "Software - Application", "currency": "USD",
                  "financialCurrency": "USD", "regularMarketPrice": 20.0, "marketCap": 2000.0, "sharesOutstanding": 100,
                  "quoteType": "EQUITY"},
         "ttm_income_stmt": frame({k: [v[0] * 1.05] for k, v in inc.items()}, TTM),
         "ttm_cashflow": frame({k: [v[0] * 1.05] for k, v in cf.items()}, TTM),
         "income_stmt": frame(inc, YEARS), "cashflow": frame(cf, YEARS), "balance_sheet": frame(bal, YEARS),
         "quarterly_balance_sheet": frame({k: [v[0]] for k, v in bal.items()}, TTM),
         "revenue_estimate": pd.DataFrame({"growth": [0.10, 0.10]}, index=["0y", "+1y"]), "history": pd.DataFrame()}
    d.update(over)
    return d


def run(data, cfg=CFG, price=20.0, override=None):
    return metrics.compute(data, price, FX(), cfg, override,valuation_date=date(2026,10,10))


def test_operating_values_tie_to_common_owner_cashflows_without_second_debt_bridge():
    m = run(operating())
    assert m["not_rankable"] == ""
    assert m['model']=='operating_equity' and m['implied_return_claim']=='equity'
    results=json.loads(m['scenario_results'])
    for case,column in [('base','value_reasonable'),('stress','stress_scenario_value'),('upside','value_upside')]:
        path=results[case];rate=m['discount_rate']
        # Independent discounted net-owner receipts, including opening capital.
        value=path['opening_distribution']+sum(c/(1+rate)**t for t,c in enumerate(path['cashflows'],1))
        value+=path['terminal_cashflow']/(rate-path['terminal_growth'])/(1+rate)**len(path['cashflows'])
        assert path['raw_value']==pytest.approx(value)
        assert m[column]==pytest.approx(max(0.,value))
    from screener import operating_models as Om
    calibrated,_=Om.calibrated_stress(m['value_reasonable'],m['stress_scenario_value'])
    assert m['value_adverse']==pytest.approx(min(calibrated,m['value_reasonable']))  # the scenario, calibrated
    assert results['base']['ledger'][0]['revenue']==pytest.approx(1050*1.1)
    assert results['base']['ledger'][0]['common_earnings']==pytest.approx(152*1.05*1.1)
    assert results['base']['opening_distribution']==pytest.approx(-600*.10)
    ceiling=m['entry_ceiling']
    assert m['entry_score']==pytest.approx(max(-100,100*(1-2000/ceiling)))
    assert m['ceiling_px']==pytest.approx(20*ceiling/2000)


def test_entry_score_example_from_design():
    # ceiling 75, price 60 -> +20; price 90 -> -20
    assert metrics.entry_score(60, 75) == pytest.approx(20)
    assert metrics.entry_score(90, 75) == pytest.approx(-20)
    assert math.isnan(metrics.entry_score(60, 0))


@pytest.mark.usefixtures("neutral_leverage")
def test_absent_debt_is_unknown_not_assumed_zero():
    # Missing rows do not prove zero; require a reconciled source.
    d = operating()
    for key in ("balance_sheet", "quarterly_balance_sheet"):
        d[key] = d[key].drop(index="Total Debt")
    m = run(d)
    assert m['not_rankable']==''
    assert math.isnan(m["net_debt_fc"])
    assert m['entry_ceiling']==pytest.approx(run(operating())['entry_ceiling'])
    assert 'Unknown is not zero' in m['model_uncertainties']


@pytest.mark.usefixtures("neutral_leverage")
def test_incomplete_debt_is_not_fabricated_or_deducted_from_common_income():
    d = operating()
    for key in ("balance_sheet", "quarterly_balance_sheet"):
        d[key] = d[key].drop(index="Total Debt")
        d[key].loc["Long Term Debt"] = [math.nan] * len(d[key].columns)
    m = run(d)
    assert math.isnan(m['net_debt_fc'])
    assert m['entry_ceiling']==pytest.approx(run(operating())['entry_ceiling'])
    assert m['implied_return_claim']=='equity'


def test_missing_balance_uses_labeled_external_equity_investment_estimate():
    d = operating(balance_sheet=pd.DataFrame(), quarterly_balance_sheet=pd.DataFrame())
    m = run(d)
    assert math.isnan(m['net_debt_fc'])
    params=json.loads(m['scenario_parameters'])['base']
    assert params['equity_to_sales']>0
    assert 'growth assumed fully equity-funded' in m['model_assumptions']
    assert 'industry_reference' in m['model_evidence']


def test_goodwill_is_not_silently_removed_from_common_equity_investment_base():
    baseline=run(operating())
    d = operating()
    for key in ("balance_sheet", "quarterly_balance_sheet"):
        d[key].loc["Goodwill"] = [300.0] * len(d[key].columns)
    m = run(d)
    assert json.loads(m['scenario_parameters'])['base']['equity_to_sales']==pytest.approx(600/1050)
    assert m['entry_ceiling']==pytest.approx(baseline['entry_ceiling'])


def test_nonpositive_continuation_keeps_negative_path_and_explicit_limited_liability_endpoint():
    d = operating()
    d['ttm_income_stmt'].loc['Net Income Common Stockholders']=-1e7
    m=run(d)
    path=json.loads(m['scenario_results'])['base']
    assert path['raw_value']<0 and path['cashflows'][0]<0
    assert path['funding_required']>1e7
    assert m['entry_ceiling']==0 and m['entry_score']==-100
    assert 'limited-liability endpoint' in m['flags']


def test_entry_score_is_floored_at_minus_100():
    assert metrics.entry_score(300, 75) == -100


@pytest.mark.usefixtures("neutral_leverage")
def test_rates_stay_above_terminal_growth_by_the_configured_margin():
    d = operating()
    d["info"]["financialCurrency"] = "CHF"
    m = run(d)
    assert m["hurdle"] == pytest.approx(0.05)          # 0.005 + 0.045
    assert m["floor_rate"] == pytest.approx(m["hurdle"])  # same discount basis
    assert math.isfinite(m["value_adverse"])


def test_old_cash_flow_statement_is_not_a_critical_stale_flag():
    d = operating()
    d["ttm_cashflow"] = pd.DataFrame()                 # cash flow falls back to FY2025 (older than 270 days)
    m = run(d)
    assert "cash flow stale" in m["flags"] and "stale financials" not in m["flags"]


@pytest.mark.usefixtures("neutral_leverage")
def test_missing_local_rate_uses_explicit_constant_fx_base_policy_not_a_hidden_rate():
    d = operating()
    d["info"]["financialCurrency"] = "XYZ"
    m = run(d)
    assert 'XYZ/EUR' in m['currency_assumption']
    assert 'Constant future' in m['currency_assumption']
    assert m['hurdle']==pytest.approx(.03+.045)
    same=operating();same['info']['financialCurrency']='EUR'
    assert m['entry_ceiling']==pytest.approx(run(same)['entry_ceiling'])


def test_growth_beyond_book_return_requires_priced_opening_and_future_equity():
    d = operating(revenue_estimate=pd.DataFrame({"growth": [0.60, 0.60]}, index=["0y", "+1y"]))
    d["balance_sheet"].loc["Stockholders Equity"] = [2000, 1900, 1800, 1700]   # invested ~2000 -> ROIC ~ 8%
    d["quarterly_balance_sheet"].loc["Stockholders Equity"] = [2000]
    m = run(d)
    params=json.loads(m['scenario_parameters'])['base']
    assert params['growth']==pytest.approx(.60)
    path=json.loads(m['scenario_results'])['base']
    assert path['opening_distribution']==pytest.approx(-2000*.6)
    assert path['funding_required']>=1200
    assert 'owner capital contributions' in m['flags']


def test_financial_uses_net_income_roe_and_market_cap():
    d = operating()
    d["info"]["industry"] = "Banks - Regional"
    m = run(d)
    assert m["class"] == "financial"
    ni = 152 * 1.05
    params=json.loads(m['scenario_parameters'])['base']
    assert params['roe_start']==pytest.approx(ni/600)
    path=json.loads(m['scenario_results'])['base']
    assert path['ledger'][0]['common_earnings']==pytest.approx(ni)
    assert path['ledger'][0]['net_distribution']==pytest.approx(ni-params['book_growth_start']*600)
    assert 'not regulatory capital' in m['model_uncertainties']


def test_cash_flow_items_come_from_one_statement():
    d = operating()
    d["ttm_cashflow"] = d["ttm_cashflow"].drop(index="Capital Expenditure")
    m = run(d)
    assert "cash flow: annual not TTM" in m["flags"]
    assert m["fcf_yield"] == pytest.approx((220 - 40 - 10) / 2000)   # after-financing cash / common market cap
    assert m["data_as_of"] == "2025-12-31"                            # oldest period actually used


def test_positive_ebit_with_negative_fcf_is_still_rankable():
    d = operating()
    d["ttm_cashflow"].loc["Capital Expenditure"] = [-400]
    m = run(d)
    assert m["class"] == "operating" and m["not_rankable"] == ""
    assert m['fcf_yield']==pytest.approx((220*1.05-400-10*1.05)/2000)
    assert m['fcf_yield']<0
    assert 'capital_structure' in m['model_assumptions']


def test_common_losses_are_retained_before_conditional_recovery():
    d = operating()
    d["ttm_income_stmt"].loc["Operating Income"] = [-5]
    d['ttm_income_stmt'].loc['Net Income Common Stockholders']=-50
    m = run(d)
    path=json.loads(m['scenario_results'])['base']
    assert path['ledger'][0]['common_earnings']==pytest.approx(-50*1.1)
    assert path['cashflows'][0]<-50
    assert path['funding_required']>50
    assert 'Recovery scenario' in m['model_uncertainties']


# ---------------- second round: adverse realism, smoothing, financial sub-families, currency, visibility
def test_scenario_envelope_retains_observed_crossing_and_is_not_a_probability_claim():
    m=run(operating())
    values=[m[k] for k in ('value_reasonable','value_adverse','value_upside')]
    assert m['valuation_range_low_px']==pytest.approx(m['price']*min(values)/m['mcap_fc'])
    assert m['valuation_range_high_px']==pytest.approx(m['price']*max(values)/m['mcap_fc'])
    assert 'not confidence bounds' in m['model_assumptions']


def cyclical(**over):
    d = operating(**over)
    d["info"]["sector"] = "Basic Materials"
    d["ttm_income_stmt"].loc["Operating Income"] = [150.0]   # TTM margin 14.3% vs ~20% in the annual years
    return d


def test_cyclical_model_preserves_common_margin_observations_and_cycle_uncertainty():
    m = run(cyclical(), cfg_with(cyclical_adverse_margin="min", cyclical_adverse_growth_at_terminal=True))
    assert m["class"] == "cyclical"
    margins=[152/1000,138/909,124/826,112/751]  # fiscal years; the overlapping TTM is not an extra period
    params=json.loads(m['scenario_parameters'])
    assert params['base']['margin_terminal']==pytest.approx(median(margins))
    assert params['stress']['margin_terminal']<=params['base']['margin_terminal']
    assert 'not a certified full-cycle forecast' in m['model_uncertainties']


def test_historical_capital_intensity_widens_investment_stress_without_changing_current_capital():
    data=operating();baseline=run(data)
    data['balance_sheet'].loc['Stockholders Equity',pd.Timestamp('2022-12-31')]=3000
    changed=run(data)
    original=json.loads(baseline['scenario_parameters']);params=json.loads(changed['scenario_parameters'])
    assert params['base']['equity_to_sales']==original['base']['equity_to_sales']
    assert params['stress']['equity_to_sales']==pytest.approx(3000/751)
    assert changed['funding_required_stress']>baseline['funding_required_stress']


def test_insurer_uses_common_equity_scenarios_without_pretending_book_capital_is_solvency():
    d = operating()
    d["info"]["industry"] = "Insurance - Reinsurance"
    m = run(d, cfg_with(insurer_smooth_earnings=True))
    assert m['model_family']=='insurer'
    path=json.loads(m['scenario_results'])['base']
    assert path['ledger'][0]['common_earnings']==pytest.approx(152*1.05)
    assert 'credit/catastrophe stress are not identified' in m['model_uncertainties']


def test_bank_book_capital_ratio_is_context_not_a_false_regulatory_capital_test():
    d = operating()
    d["info"]["industry"] = "Banks - Regional"
    for key in ("balance_sheet", "quarterly_balance_sheet"):
        d[key].loc["Total Assets"] = [20000.0] * len(d[key].columns)   # equity / assets = 3%
    m = run(d)
    assert m['model_family']=='bank' and m['equity_to_assets']==pytest.approx(.03)
    assert 'not regulatory capital' in m['model_uncertainties']
    assert 'thin capital' not in m['flags']


def test_platform_override_supports_group_equity_and_exposes_missing_segment_evidence():
    m = run(operating(), override="financial")
    assert m['model_family']=='hybrid' and m['model']=='equity_residual_income'
    assert json.loads(m['business_profile'])['diversified']
    assert 'segment evidence' in m['flags']
    assert m['implied_return_claim']=='equity'


def test_minimum_hurdle_applies_after_currency_lookup():
    d = operating()
    d["info"]["financialCurrency"] = "CHF"
    m = run(d, cfg_with(min_hurdle=0.07))
    assert m["hurdle"] == pytest.approx(0.07)
    assert m['discount_rate']==pytest.approx(.07)
    params=json.loads(m['scenario_parameters'])
    assert params['stress']['terminal_equity_return']==pytest.approx(.07)          # stress: the edge is competed away
    assert .07<=params['base']['terminal_equity_return']<=.07+0.10+1e-12           # base: hurdle plus capped franchise


@pytest.mark.usefixtures("neutral_leverage")
def test_country_risk_premium_raises_both_rates():
    d = operating()
    d["info"]["financialCurrency"] = "KZT"
    d["info"]["country"]="Kazakhstan"
    cfg={**CFG,"country_risk":{"Kazakhstan":{"premium":0.03,"source":"test policy"}}}
    m = run(d,cfg)
    assert m["hurdle"] == pytest.approx(0.14 + 0.045 + 0.03)
    assert m["floor_rate"] == pytest.approx(m["hurdle"])


def test_multi_driver_valuation_can_identify_yield_without_identifying_growth_or_fx_returns():
    m = run(operating())
    assert "implied_return_base" not in m
    assert 'fx_parity_return' not in m
    path=json.loads(m['scenario_results'])['base']
    assert min(path['cashflows']+[path['terminal_cashflow']])>=0
    assert m['implied_return_status']=='solved'
    repriced=path['opening_distribution']+V.discount(path['cashflows'],path['terminal_cashflow'],m['implied_return'],path['terminal_growth'])
    assert repriced==pytest.approx(m['mcap_fc'],rel=V.RESIDUAL_RTOL)
    assert 'not identified' in m['implied_growth_status']


def test_growth_estimate_is_exposed_as_assumption_instead_of_hidden_book_return_cap():
    d = operating(revenue_estimate=pd.DataFrame({"growth": [0.60, 0.60]}, index=["0y", "+1y"]))
    d["balance_sheet"].loc["Stockholders Equity"] = [2000, 1900, 1800, 1700]
    d["quarterly_balance_sheet"].loc["Stockholders Equity"] = [2000]
    m = run(d)
    params=json.loads(m['scenario_parameters'])['base']
    assert params['growth']==pytest.approx(.6)
    assumption=next(a for a in json.loads(m['model_assumptions']) if isinstance(a,dict) and a.get('parameter')=='initial_growth')
    assert assumption['value']==pytest.approx(.6)
    assert 'revenue-growth estimate' in assumption['basis']


class _FX:
    def __init__(self, rates):
        self.rates = rates

    def rate(self, frm, to):
        return 1.0 if frm == to else self.rates.get((frm, to))


def test_cost_basis_scores_your_buy_price_against_todays_ceiling():
    # bought 10 shares for EUR 800 (EUR 80 each); USD->EUR 0.80 today, so the cost is USD 100 at today's rate
    fx = _FX({("USD", "EUR"): 0.80})
    out = metrics.cost_basis(price=150.0, ceiling_px=125.0, quote_ccy="USD", shares=10, cost_eur=800.0,
                             value_eur=1200.0, fx=fx)  # 1200 / 10 = EUR 120 = USD 150: units agree
    assert out["cost_px"] == pytest.approx(100.0)
    assert out["cost_score"] == pytest.approx(20.0)  # paid 20% below today's max entry price
    assert metrics.entry_score(150.0, 125.0) == pytest.approx(-20.0)  # while today's price is 20% above it


def test_cost_basis_skips_when_units_disagree_or_inputs_missing():
    fx = _FX({("USD", "EUR"): 0.80})
    # the holdings file says EUR 40 per share, Yahoo USD 150 = EUR 120: a different unit (e.g. GDR ratio) -> no score
    out = metrics.cost_basis(150.0, 125.0, "USD", 10, 800.0, 400.0, fx)
    assert "cost_score" not in out and "disagree" in out["cost_flag"]
    assert metrics.cost_basis(150.0, 125.0, "USD", 10, 0.0, 1200.0, fx) == {}        # no cost basis known
    assert "no FX" in metrics.cost_basis(150.0, 125.0, "SEK", 10, 800.0, 1200.0, fx)["cost_flag"]
    unscored = metrics.cost_basis(150.0, math.nan, "USD", 10, 800.0, 1200.0, fx)     # fund: no ceiling
    assert unscored["cost_px"] == pytest.approx(100.0) and math.isnan(unscored["cost_score"])


def test_position_value_uses_todays_price_and_fx():
    fx = _FX({("USD", "EUR"): 0.80})
    assert metrics.position_value(150.0, "USD", 10, fx) == pytest.approx(1200.0)
    assert math.isnan(metrics.position_value(150.0, "SEK", 10, fx))      # no FX rate
    assert math.isnan(metrics.position_value(150.0, "USD", 0, fx))       # no shares
