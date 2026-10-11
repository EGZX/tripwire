"""Independent yields for distributions from any claim-consistent model."""
import math

import pytest

from screener import valuation as V


@pytest.mark.parametrize("years", [1, 5, 30])
def test_level_perpetuity_has_cash_yield_regardless_of_explicit_horizon(years):
    solution = V.solve_distribution_return([10.] * years, 10., 125., 0.)
    assert solution.status == "solved"
    assert solution.value == pytest.approx(10 / 125)
    assert abs(solution.residuals[0]) <= 125 * V.RESIDUAL_RTOL


def test_growing_perpetuity_matches_its_first_payment_closed_form():
    growth, first, price = .03, 10.3, 150.
    flows = [first * (1 + growth) ** t for t in range(7)]
    terminal = first * (1 + growth) ** 7
    solution = V.solve_distribution_return(flows, terminal, price, growth)
    assert solution.value == pytest.approx(growth + first / price)
    assert solution.value > growth


@pytest.mark.parametrize("payment,price,expected", [(110,100,.10),(80,100,-.20),(100,100,0),(400,100,3.)])
def test_single_finite_payment_supports_negative_zero_and_upper_endpoint_yields(payment,price,expected):
    # A zero terminal payment makes a nominal terminal-growth parameter irrelevant.
    solution = V.solve_distribution_return([payment], 0., price, .5)
    assert solution.status == "solved"
    assert solution.value == pytest.approx(expected, abs=1e-10)


def test_opening_contribution_is_additional_initial_investment_not_future_nonmonotonicity():
    solution = V.solve_distribution_return([10.] * 10, 10., 100., 0., opening_distribution=-25.)
    assert solution.status == "solved"
    assert solution.value == pytest.approx(10/125)
    value = -25 + V.discount([10.]*10,10.,solution.value,0.)
    assert value == pytest.approx(100.)


def test_opening_distribution_reduces_investment_without_changing_timing():
    solution = V.solve_distribution_return([90.],0.,100.,0.,opening_distribution=25.)
    assert solution.value == pytest.approx(90/75-1)


def test_zero_distributions_distinguish_no_solution_from_an_unidentified_discount_rate():
    assert V.solve_distribution_return([0.,0.],0.,100.,0.).status == "no_solution"
    exact = V.solve_distribution_return([0.,0.],0.,100.,0.,opening_distribution=100.)
    assert exact.status == "indeterminate" and exact.value is None
    assert V.solve_distribution_return([1.],0.,100.,0.,opening_distribution=100.).status == "no_solution"
    assert V.solve_distribution_return([1.],0.,100.,0.,opening_distribution=101.).status == "no_solution"


@pytest.mark.parametrize("cashflows,terminal", [([10.,-2.,20.],10.), ([-10.],0.), ([100.],-1.)])
def test_future_signed_flows_do_not_receive_an_unproved_unique_yield(cashflows,terminal):
    value = V.discount(cashflows,terminal,.10,.03)
    assert math.isfinite(value)  # signed cash-flow valuation itself remains available
    solution = V.solve_distribution_return(cashflows,terminal,100.,.03)
    assert solution.status == "not_identified"
    assert solution.value is None and not solution.roots
    assert "uniqueness" in solution.reason


@pytest.mark.parametrize("scale", [1e-12,1e-6,1,1e6,1e12])
def test_monetary_rescaling_preserves_return_with_initial_funding(scale):
    solution = V.solve_distribution_return([10.*scale]*10,10.*scale,100.*scale,0.,-25.*scale)
    assert solution.status == "solved"
    assert solution.value == pytest.approx(.08)
    assert abs(solution.residuals[0]/(125*scale)) <= V.RESIDUAL_RTOL


def test_legacy_earnings_solver_delegates_to_the_same_distribution_root():
    flows, terminal = V.distributions(10,.08,.03,10,.20,.12)
    direct = V.solve_distribution_return(flows,terminal,130.,.03)
    legacy = V.solve_return(10,130.,.08,.03,10,.20,.12)
    assert legacy == direct
    assert legacy.status == "solved"


def test_positive_terminal_domain_and_upper_search_bound_are_respected():
    assert V.solve_distribution_return([500.],0.,100.,0.).status == "outside_domain"
    assert V.solve_distribution_return([10.],10.,100.,3.).status == "outside_domain"
    solution = V.solve_distribution_return([0.],1.,1e6,.03)
    assert solution.status == "solved"
    assert solution.value > .03


@pytest.mark.parametrize("years", [2,5,30,50])
def test_single_deferred_finite_payment_has_compound_annual_yield(years):
    solution = V.solve_distribution_return([0.]*(years-1)+[32.],0.,100.,.5)
    assert solution.status == "solved"
    assert solution.value == pytest.approx((32/100)**(1/years)-1)


def test_actual_upper_endpoint_root_is_not_lost_when_terminal_growth_is_close():
    growth = math.nextafter(3.,0.)
    price = 1/(3-growth)/4
    solution = V.solve_distribution_return([0.],1.,price,growth)
    assert solution.status == "solved" and solution.value == 3.


@pytest.mark.parametrize("args", [([],0.,100.,0.), ([1.],0.,0.,0.), ([1.],0.,100.,-1.),
                                  ([float('nan')],0.,100.,0.), ([1.],float('inf'),100.,0.),
                                  (None,0.,100.,0.), ([True],0.,100.,0.)])
def test_invalid_cashflow_inputs_return_explicit_failure(args):
    solution = V.solve_distribution_return(*args)
    assert solution.status == "invalid" and solution.value is None
