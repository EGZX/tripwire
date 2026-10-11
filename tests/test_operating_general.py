"""Independent valuation identities for the general common-equity operating model."""
import math

import pandas as pd
import pytest

from screener import automatic, models, operating_models as O, statements as S, valuation as V


def parameters(**changes):
    return {"revenue": 100.0, "growth": .03, "margin_start": .10,
            "margin_terminal": .10, "equity_to_sales": 1.0,
            "terminal_equity_return": .10, "terminal_growth": .03, **changes}


@pytest.mark.parametrize("initial_growth", [0, .03, .10, .30, .70])
@pytest.mark.parametrize("years", [1, 2, 10, 30])
def test_no_value_creation_from_growth_when_new_equity_return_equals_discount(initial_growth, years):
    p = parameters(growth=initial_growth)
    out = O.project(p, years, .10)
    # Existing earnings=10. New capital earns exactly its10% opportunity cost,
    # including the investment made immediately to create year-one expansion.
    assert out["value"] == pytest.approx(10 / .10)
    assert out["opening_distribution"] == pytest.approx(-100 * initial_growth)


def test_first_expansion_is_not_free_and_wrapper_keeps_opening_contribution():
    p = parameters(growth=.20)
    raw = O.project(p, 10, .10)
    explicit_and_terminal = V.discount(list(raw["cashflows"]), raw["terminal_cashflow"], .10, .03)
    assert explicit_and_terminal == pytest.approx(120)
    assert raw["opening_distribution"] == -20
    assert raw["value"] == pytest.approx(100)
    wrapped = models.evaluate("operating_equity", p, 10, .10, .03)
    assert wrapped.claim == "equity"
    assert wrapped.opening_distribution == -20
    assert wrapped.value == pytest.approx(raw["value"])


def test_fully_retained_terminal_profit_is_a_finite_payout_domain():
    p=parameters(terminal_equity_return=.03, terminal_growth=.03)
    result=O.project(p,10,-.02)
    assert result['terminal_cashflow']==0.
    independent=result['opening_distribution']+sum(cf/.98**t for t,cf in enumerate(result['cashflows'],1))
    assert result['value']==pytest.approx(independent)


def test_cash_calls_are_discounted_as_negative_owner_flows():
    p = parameters(growth=.40, margin_start=-.10, margin_terminal=.10, equity_to_sales=2)
    raw = O.project(p, 5, .10)
    expected_calls = -raw["opening_distribution"] + sum(-c for c in raw["cashflows"] if c < 0)
    assert raw["cashflows"][0] < 0
    assert raw["funding_required"] == pytest.approx(expected_calls)
    expected_pv = raw["opening_distribution"] + sum(c / 1.1 ** t for t, c in enumerate(raw["cashflows"], 1))
    expected_pv += raw["terminal_cashflow"] / (.10 - .03) / 1.1 ** 5
    assert raw["value"] == pytest.approx(expected_pv)


@pytest.mark.parametrize("scale", [1e-12, 1e-6, 1, 1e6, 1e12])
def test_money_units_do_not_change_the_operating_economics(scale):
    p = parameters(growth=.40, margin_start=-.10)
    original = O.project(p, 10, .10)
    scaled = O.project({**p, "revenue": p["revenue"] * scale}, 10, .10)
    for field in ["value", "opening_distribution", "funding_required", "terminal_cashflow"]:
        assert scaled[field] / scale == pytest.approx(original[field])
    assert [c / scale for c in scaled["cashflows"]] == pytest.approx(original["cashflows"])


def test_contraction_does_not_invent_asset_liquidation():
    p = parameters(growth=-.20, terminal_growth=0)
    out = O.project(p, 10, .10)
    assert out["opening_distribution"] == 0
    assert all(y["new_equity_investment"] == 0 for y in out["ledger"])
    for year in out["ledger"]:
        assert year["net_distribution"] == pytest.approx(year["common_earnings"])
    assert out["value"] < 100


def test_net_margin_above_revenue_is_not_arbitrarily_capped():
    # Common net income can include non-operating/associate investment income.
    # Applicability warnings belong outside the numeric accounting domain.
    p = parameters(margin_start=1.20, margin_terminal=1.20, equity_to_sales=12)
    out = O.project(p, 10, .10)
    assert out["ledger"][0]["common_earnings"] == pytest.approx(123.6)
    assert out["value"] == pytest.approx(1200)


def test_zero_terminal_earnings_are_a_finite_payout_not_a_gordon_pole():
    p = parameters(growth=0, margin_terminal=0, terminal_growth=0)
    out = O.project(p, 2, .10)
    assert out["terminal_cashflow"] == 0
    assert out["value"] == pytest.approx(sum(c / 1.1 ** t for t, c in enumerate(out["cashflows"], 1)))


def test_zero_terminal_allows_a_finite_zero_discount_rate():
    p = parameters(growth=0, margin_terminal=0, terminal_growth=0)
    out = O.project(p, 2, 0)
    assert out["terminal_cashflow"] == 0
    assert out["value"] == pytest.approx(sum(out["cashflows"]))


def test_perpetual_equity_capital_calls_are_an_infeasible_terminal():
    with pytest.raises(V.InvalidModel):
        O.project(parameters(terminal_equity_return=.02), 10, .10)


def test_fixed_cashflow_path_is_separately_valued_at_policy_and_economic_rate():
    p = parameters(growth=.1)
    lower = O.project(p, 10, .07)
    higher = O.project(p, 10, .13)
    assert lower["cashflows"] == higher["cashflows"]
    assert lower["terminal_cashflow"] == higher["terminal_cashflow"]
    assert lower["opening_distribution"] == higher["opening_distribution"]
    assert lower["value"] > higher["value"]


def minimal_data():
    d = pd.to_datetime(["2026-06-30"])
    income = pd.DataFrame([[100.0], [10.0], [10.0]], columns=d,
                          index=["Total Revenue", "Net Income Common Stockholders", "Diluted Average Shares"])
    balance = pd.DataFrame([[100.0]], columns=d, index=["Common Stock Equity"])
    data = dict(info={"financialCurrency": "USD", "industry": "Unmapped controlled test"},
                ttm_income_stmt=income, quarterly_balance_sheet=balance,
                income_stmt=pd.DataFrame(), balance_sheet=pd.DataFrame(), earnings_estimate=pd.DataFrame())
    selections = dict(income=S.select([("ttm_income_stmt", income)], ("revenue", "net_income")),
                      balance=S.select([("quarterly_balance_sheet", balance)], ("common_equity",)))
    context = dict(family="operating", years=10, hurdle=.10, terminal_growth=.03, cfg={})
    return data, selections, context


def test_broad_reference_substitution_is_visible_and_source_dated():
    data, selections, context = minimal_data()
    built = O.build(data, selections, context)
    reference = next(x["value"] for x in built["evidence"] if x["field"] == "industry_reference")
    assert not reference["mapped"]
    assert reference["sources"] and reference["as_of"] and reference["geography"]
    assert any("broad nonfinancial reference" in w for w in built["warnings"])


def test_missing_reference_is_an_explicit_per_issuer_failure(monkeypatch):
    data, selections, context = minimal_data()
    read_text = O.Path.read_text
    def unavailable(path, *args, **kwargs):
        if path.name == "industry_benchmarks.json":
            raise FileNotFoundError("independent industry reference missing")
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(O.Path, "read_text", unavailable)
    O.references.cache_clear()
    try:
        m = dict(**{"class": "operating"}, mcap_fc=500, price=10)
        result = automatic.evaluate(data, selections, context, m, [], [])
        assert result["not_rankable"]
        assert "reference" in result["not_rankable"]
        assert "entry_score" not in result
    finally:
        O.references.cache_clear()
