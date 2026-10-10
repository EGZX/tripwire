"""Entry-ceiling model on synthetic Yahoo-shaped data: scenarios, essentials, period alignment."""
import math

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
           "Stockholders Equity": [600, 500, 400, 300], "Total Assets": [900, 800, 700, 600], "Minority Interest": [0, 0, 0, 0]}
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
    return metrics.compute(data, price, FX(), cfg, override)


def test_operating_entry_ceiling_is_the_reasonable_value_and_adverse_is_scored_separately():
    m = run(operating())
    assert m["not_rankable"] == ""
    nopat = 210 * (1 - 0.20)                      # TTM EBIT x (1 - tax)
    assert m["base_earnings"] == pytest.approx(nopat)
    hurdle, floor, gT = 0.095, 0.05, 0.03
    assert m["hurdle"] == pytest.approx(hurdle) and m["floor_rate"] == pytest.approx(floor)
    R = m["return_on_capital"]
    assert 0 < R <= 0.60
    g0, g_adv = m["est_growth"], m["adverse_growth"]
    assert g_adv == pytest.approx(0.5 * min(g0, m["rev_cagr_3y"], m["analyst_growth"]))
    net_debt = 50
    reasonable = V.present_value(nopat, g0, hurdle, gT, 10, R, R) - net_debt
    adverse = V.present_value(nopat, g_adv, floor, gT, 10, R, hurdle) - net_debt
    assert m["value_reasonable"] == pytest.approx(reasonable)
    assert m["value_adverse"] == pytest.approx(adverse)
    ceiling = reasonable
    assert m["entry_ceiling"] == pytest.approx(ceiling)
    assert "binding_case" not in m
    assert m["adverse_score"] == pytest.approx(max(-100, 100 * (1 - 2000 / adverse)))
    assert m["entry_score"] == pytest.approx(100 * (1 - 2000 / ceiling))
    assert m["ceiling_px"] == pytest.approx(20.0 * ceiling / 2000)
    # implied return solves the same funded path against EV
    assert V.present_value(nopat, g0, m["implied_return"], gT, 10, R, R) == pytest.approx(2050, rel=1e-6)


def test_entry_score_example_from_design():
    # ceiling 75, price 60 -> +20; price 90 -> -20
    assert metrics.entry_score(60, 75) == pytest.approx(20)
    assert metrics.entry_score(90, 75) == pytest.approx(-20)
    assert math.isnan(metrics.entry_score(60, 0))


def test_no_debt_rows_at_all_means_debt_free_and_is_flagged():
    # Yahoo omits every debt row for debt-free issuers (ISRG, ANET, Keyence): treat as zero, say so
    d = operating()
    for key in ("balance_sheet", "quarterly_balance_sheet"):
        d[key] = d[key].drop(index="Total Debt")
    m = run(d)
    assert m["not_rankable"] == "" and "assumed debt-free" in m["flags"]
    assert m["net_debt_fc"] == pytest.approx(-50)


def test_debt_rows_present_but_total_missing_blocks_ranking():
    d = operating()
    for key in ("balance_sheet", "quarterly_balance_sheet"):
        d[key] = d[key].drop(index="Total Debt")
        d[key].loc["Long Term Debt"] = [math.nan] * len(d[key].columns)
    m = run(d)
    assert "debt" in m["not_rankable"]
    assert math.isnan(m.get("entry_score", math.nan))


def test_missing_balance_sheet_blocks_ranking():
    d = operating(balance_sheet=pd.DataFrame(), quarterly_balance_sheet=pd.DataFrame())
    m = run(d)
    assert "debt" in m["not_rankable"] and "cash" in m["not_rankable"]


def test_return_on_new_capital_excludes_goodwill():
    d = operating()
    for key in ("balance_sheet", "quarterly_balance_sheet"):
        d[key].loc["Goodwill"] = [300.0] * len(d[key].columns)
    m = run(d)
    nopat = 210 * 0.8
    invested_avg = ((600 + 100 - 50) + (500 + 100 - 45)) / 2
    assert m["roic"] == pytest.approx(nopat / invested_avg)                    # reported, incl. goodwill
    assert m["return_on_capital"] == pytest.approx(nopat / (invested_avg - 300))   # marginal, ex goodwill


def test_non_positive_ceiling_is_not_rankable_with_reason():
    d = operating()
    for key in ("balance_sheet", "quarterly_balance_sheet"):
        d[key].loc["Total Debt"] = [50000.0] * len(d[key].columns)   # debt far above any business value
    m = run(d)
    assert "ceiling" in m["not_rankable"]
    assert math.isnan(m.get("entry_score", math.nan))


def test_entry_score_is_floored_at_minus_100():
    assert metrics.entry_score(300, 75) == -100


def test_rates_stay_above_terminal_growth_by_the_configured_margin():
    d = operating()
    d["info"]["financialCurrency"] = "CHF"
    m = run(d)
    assert m["hurdle"] == pytest.approx(0.05)          # 0.005 + 0.045
    assert m["floor_rate"] == pytest.approx(0.03)      # 0.005 + 0.0 lifted to g + 0.02
    assert math.isfinite(m["value_adverse"])


def test_old_cash_flow_statement_is_not_a_critical_stale_flag():
    d = operating()
    d["ttm_cashflow"] = pd.DataFrame()                 # cash flow falls back to FY2025 (older than 270 days)
    m = run(d)
    assert "cash flow stale" in m["flags"] and "stale financials" not in m["flags"]


def test_unknown_reporting_currency_blocks_ranking():
    d = operating()
    d["info"]["financialCurrency"] = "XYZ"
    m = run(d)
    assert "XYZ" in m["not_rankable"]


def test_growth_is_capped_at_return_on_new_capital():
    d = operating(revenue_estimate=pd.DataFrame({"growth": [0.60, 0.60]}, index=["0y", "+1y"]))
    d["balance_sheet"].loc["Stockholders Equity"] = [2000, 1900, 1800, 1700]   # invested ~2000 -> ROIC ~ 8%
    d["quarterly_balance_sheet"].loc["Stockholders Equity"] = [2000]
    m = run(d)
    assert m["est_growth"] == pytest.approx(m["return_on_capital"])
    assert "growth capped" in m["flags"]


def test_financial_uses_net_income_roe_and_market_cap():
    d = operating()
    d["info"]["industry"] = "Banks - Regional"
    m = run(d)
    assert m["class"] == "financial"
    ni = 152 * 1.05
    assert m["base_earnings"] == pytest.approx(ni)
    assert m["return_on_capital"] == pytest.approx(ni / 600)
    assert m["value_reasonable"] == pytest.approx(
        V.present_value(ni, m["est_growth"], 0.095, 0.03, 10, m["return_on_capital"], m["return_on_capital"]))


def test_cash_flow_items_come_from_one_statement():
    d = operating()
    d["ttm_cashflow"] = d["ttm_cashflow"].drop(index="Capital Expenditure")
    m = run(d)
    assert "cash flow: annual not TTM" in m["flags"]
    assert m["fcf_yield"] == pytest.approx((220 - 40 - 10) / 2050)   # all annual, not a TTM/annual mix
    assert m["data_as_of"] == "2025-12-31"                            # oldest period actually used


def test_positive_ebit_with_negative_fcf_is_still_rankable():
    d = operating()
    d["ttm_cashflow"].loc["Capital Expenditure"] = [-400]
    m = run(d)
    assert m["class"] == "operating" and m["not_rankable"] == ""
    assert "negative FCF" in m["flags"]


def test_loss_making_is_not_ranked():
    d = operating()
    d["ttm_income_stmt"].loc["Operating Income"] = [-5]
    m = run(d)
    assert m["class"] == "preprofit"


# ---------------- second round: adverse realism, smoothing, financial sub-families, currency, visibility
def test_adverse_growth_haircut_mode_still_available():
    m = run(operating(), cfg_with(adverse_growth_mode="haircut"))
    assert m["adverse_growth"] == pytest.approx(min(m["est_growth"], m["rev_cagr_3y"], m["analyst_growth"]) - 0.05)


def cyclical(**over):
    d = operating(**over)
    d["info"]["sector"] = "Basic Materials"
    d["ttm_income_stmt"].loc["Operating Income"] = [150.0]   # TTM margin 14.3% vs ~20% in the annual years
    return d


def test_cyclical_adverse_case_uses_trough_margin_and_trend_growth():
    import numpy as np
    m = run(cyclical(), cfg_with(cyclical_adverse_margin="min", cyclical_adverse_growth_at_terminal=True))
    assert m["class"] == "cyclical"
    margins = [200 / 1000, 182 / 909, 165 / 826, 150 / 751, 150 / 1050]
    assert m["base_earnings"] == pytest.approx(float(np.median(margins)) * 1050 * 0.8)
    rev_adverse = min(1050, (826 + 909 + 1000 + 1050) / 4)
    assert m["base_earnings_adverse"] == pytest.approx(min(margins) * rev_adverse * 0.8)
    assert m["adverse_growth"] == pytest.approx(0.03)          # capped at terminal growth


def test_return_on_capital_uses_lower_of_current_and_three_year_average():
    m0 = run(operating())
    m = run(operating(), cfg_with(return_smoothing=True))
    tax = 0.2
    annual = [(200 * (1 - tax)) / (600 + 100 - 50), (182 * (1 - tax)) / (500 + 100 - 45), (165 * (1 - tax)) / (400 + 100 - 40)]
    avg3 = sum(annual) / 3
    assert m["return_on_capital"] == pytest.approx(min(m0["return_on_capital"], avg3))
    assert m["return_on_capital"] <= m0["return_on_capital"]


def test_insurer_base_is_lower_of_ttm_and_three_year_average():
    d = operating()
    d["info"]["industry"] = "Insurance - Reinsurance"
    m = run(d, cfg_with(insurer_smooth_earnings=True))
    assert m["fin_type"] == "insurer"
    assert m["base_earnings"] == pytest.approx(min(152 * 1.05, (152 + 138 + 124) / 3))


def test_thin_bank_capital_is_flagged():
    d = operating()
    d["info"]["industry"] = "Banks - Regional"
    for key in ("balance_sheet", "quarterly_balance_sheet"):
        d[key].loc["Total Assets"] = [20000.0] * len(d[key].columns)   # equity / assets = 3%
    m = run(d)
    assert m["fin_type"] == "bank" and "thin capital" in m["flags"]


def test_platform_override_is_flagged_as_conservative():
    m = run(operating(), override="financial")
    assert m["fin_type"] == "platform" and "platform" in m["flags"]


def test_minimum_hurdle_applies_after_currency_lookup():
    d = operating()
    d["info"]["financialCurrency"] = "CHF"
    m = run(d, cfg_with(min_hurdle=0.07))
    assert m["hurdle"] == pytest.approx(0.07) and "min hurdle" in m["caps_hit"]
    assert m["floor_rate"] <= m["hurdle"]


def test_country_risk_premium_raises_both_rates():
    d = operating()
    d["info"]["financialCurrency"] = "KZT"
    m = run(d)
    assert m["hurdle"] == pytest.approx(0.14 + 0.045 + 0.03)
    assert m["floor_rate"] == pytest.approx(0.14 + 0.0 + 0.03)


def test_implied_return_in_base_currency_terms():
    m = run(operating())
    assert m["implied_return_base"] == pytest.approx(m["implied_return"] - (0.05 - 0.03))


def test_caps_hit_lists_binding_parameters():
    d = operating(revenue_estimate=pd.DataFrame({"growth": [0.60, 0.60]}, index=["0y", "+1y"]))
    d["balance_sheet"].loc["Stockholders Equity"] = [2000, 1900, 1800, 1700]
    d["quarterly_balance_sheet"].loc["Stockholders Equity"] = [2000]
    m = run(d)
    assert "growth cap" in m["caps_hit"] and "growth <= R" in m["caps_hit"]
    assert run(operating())["caps_hit"] == ""


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
    # Parqet says EUR 40 per share, Yahoo USD 150 = EUR 120: a different unit (e.g. GDR ratio) -> no score
    out = metrics.cost_basis(150.0, 125.0, "USD", 10, 800.0, 400.0, fx)
    assert "cost_score" not in out and "disagree" in out["cost_flag"]
    assert metrics.cost_basis(150.0, 125.0, "USD", 10, 0.0, 1200.0, fx) == {}        # no cost basis known
    assert "no FX" in metrics.cost_basis(150.0, 125.0, "SEK", 10, 800.0, 1200.0, fx)["cost_flag"]
    unscored = metrics.cost_basis(150.0, math.nan, "USD", 10, 800.0, 1200.0, fx)     # fund: no ceiling
    assert unscored["cost_px"] == pytest.approx(100.0) and math.isnan(unscored["cost_score"])
