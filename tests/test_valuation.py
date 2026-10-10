"""Earnings-based distribution model: growth must be funded by reinvestment at the return on new capital."""
import pytest

from screener import valuation as V


def test_growth_path_fades_to_terminal():
    p = V.fade(0.20, 0.03, 10)
    assert p[0] == pytest.approx(0.20) and p[-1] == pytest.approx(0.03) and len(p) == 10


def test_no_reinvestment_zero_growth_is_a_perpetuity():
    assert V.present_value(10, 0.0, 0.10, 0.0, 10) == pytest.approx(100.0)


def test_bank_growth_must_be_funded_from_retained_earnings():
    # earnings 10 growing 6% forever at a 10% equity rate: 265 if fully paid out, 132.5 if 50% must be retained (g/ROE)
    assert V.present_value(10, 0.06, 0.10, 0.06, 10) == pytest.approx(265.0)
    assert V.present_value(10, 0.06, 0.10, 0.06, 10, R0=0.12, RT=0.12) == pytest.approx(132.5)


def test_growth_is_value_neutral_when_return_equals_cost_of_capital():
    # McKinsey value-driver identity: with R == k, value = next year's earnings / k whatever the later growth path
    k = 0.10
    assert V.present_value(10, 0.0, k, 0.0, 10, R0=k, RT=k) == pytest.approx(100.0)
    for g0 in (0.03, 0.08, 0.15):
        assert V.present_value(10, g0, k, 0.03, 10, R0=k, RT=k) == pytest.approx(10 * (1 + g0) / k)


def test_growth_above_cost_of_capital_return_adds_value_and_below_destroys_it():
    k = 0.10
    neutral = V.present_value(10, 0.08, k, 0.03, 10, R0=k, RT=k)
    assert V.present_value(10, 0.08, k, 0.03, 10, R0=0.20, RT=0.20) > neutral
    assert V.present_value(10, 0.08, k, 0.03, 10, R0=0.06, RT=0.06) < neutral


def test_implied_return_and_implied_growth_are_inverse():
    e, price, g0 = 5.0, 120.0, 0.12
    r = V.implied_return(e, price, g0, 0.03, 10, R0=0.25, RT=0.25)
    assert V.present_value(e, g0, r, 0.03, 10, R0=0.25, RT=0.25) == pytest.approx(price, rel=1e-6)
    assert V.implied_growth(e, price, r, 0.03, 10, R0=0.25, RT=0.25) == pytest.approx(g0, abs=1e-6)


def test_root_finders_return_none_outside_bracket_instead_of_endpoints():
    assert V.implied_return(-1.0, 100, 0.1, 0.03, 10) is None
    assert V.implied_return(1.0, 0.01, 0.3, 0.03, 10) is None        # would need r > 300%
    assert V.implied_growth(0.0, 100, 0.1, 0.03, 10) is None
    assert V.implied_growth(1.0, 1e6, 0.0975, 0.03, 10) is None      # no growth in bracket justifies the price
    assert V.implied_growth(1.0, 0.001, 0.0975, 0.03, 10) is None    # price below any path in bracket


def test_implied_growth_cannot_exceed_return_on_capital():
    # with reinvestment, growth above R gives negative distributions; the search stays within [-0.9, R]
    g = V.implied_growth(1.0, 30.0, 0.0975, 0.03, 10, R0=0.15, RT=0.15)
    assert g is None or g <= 0.15
