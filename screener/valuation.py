"""Annual nominal cash-flow valuation with explicit domains and inverse results.

Growth in t+1 is funded in t. Contraction does not imply liquidation at book
value. The funded-growth model cannot silently assume external financing.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from numbers import Integral, Real
import numpy as np
from numpy.polynomial import Chebyshev
from scipy.optimize import brentq

# Numerical error budgets, not economic/materiality thresholds.
RESIDUAL_RTOL = 1e-9
ROOT_XTOL = 1e-12

class InvalidModel(ValueError):
    """Invalid assumptions or infeasible financing."""

@dataclass(frozen=True)
class Solution:
    status: str
    roots: tuple[float, ...] = ()
    residuals: tuple[float, ...] = ()
    reason: str = ""

    @property
    def value(self) -> float | None:
        return self.roots[0] if self.status == "solved" else None

def finite(*xs: float) -> None:
    if any(isinstance(x, bool) or not isinstance(x, Real) or not math.isfinite(x) for x in xs):
        raise InvalidModel("inputs must be finite real numbers")

def horizon(years: int) -> None:
    if isinstance(years, bool) or not isinstance(years, Integral) or years < 1:
        raise InvalidModel("years must be a positive integer")

def fade(a: float, b: float, years: int) -> list[float]:
    finite(a, b)
    horizon(years)
    return [a] if years == 1 else [a + (b-a)*t/(years-1) for t in range(years)]

def distributions(e0: float, g0: float, g_terminal: float, years: int,
                  R0: float | None = None, RT: float | None = None) -> tuple[list[float], float]:
    finite(e0, g0, g_terminal)
    horizon(years)
    if e0 < 0 or min(g0, g_terminal) <= -1:
        raise InvalidModel("earnings must be nonnegative and growth greater than -100%")
    if R0 is None and RT is not None:
        raise InvalidModel("terminal return requires an initial return")
    if R0 is not None:
        finite(R0, R0 if RT is None else RT)
        if min(R0, R0 if RT is None else RT) <= 0:
            raise InvalidModel("return on new capital must be positive")
    gs = fade(g0, g_terminal, years) + [g_terminal]
    Rs = fade(R0, R0 if RT is None else RT, years) if R0 is not None else [None]*years
    e, out = e0, []
    for t, R in enumerate(Rs):
        e *= 1 + gs[t]
        retention = max(gs[t+1], 0.0)/R if R is not None else 0.0
        if retention > 1 + 16*np.finfo(float).eps:
            raise InvalidModel(f"growth exceeds return on capital in year {t+1}: external funding required")
        out.append(e*(1-min(retention, 1.0)))
    terminal_R = RT if RT is not None else R0
    terminal_retention = max(g_terminal, 0.0)/terminal_R if terminal_R is not None else 0.0
    if terminal_retention > 1 + 16*np.finfo(float).eps:
        raise InvalidModel("terminal growth exceeds return on capital")
    d_term = e*(1+g_terminal)*(1-min(terminal_retention, 1.0))
    finite(*out, d_term)
    return out, d_term

def discount(cashflows: list[float], terminal_cashflow: float, rate: float, terminal_growth: float) -> float:
    """PV of explicit firm OR equity cash flows; caller owns claim matching."""
    finite(*cashflows, terminal_cashflow, rate, terminal_growth)
    if not cashflows or rate <= -1 or terminal_growth <= -1 or (terminal_cashflow != 0 and rate <= terminal_growth):
        raise InvalidModel("discount rate must exceed terminal growth and -100%; path must be nonempty")
    value = math.fsum(cf/(1+rate)**t for t, cf in enumerate(cashflows, 1))
    if terminal_cashflow != 0:
        value += terminal_cashflow/(rate-terminal_growth)/(1+rate)**len(cashflows)
    finite(value)
    return value

def present_value(e0: float, g0: float, r: float, g_terminal: float, years: int,
                  R0: float | None = None, RT: float | None = None) -> float:
    ds, terminal = distributions(e0, g0, g_terminal, years, R0, RT)
    return discount(ds, terminal, r, g_terminal)

def solve_distribution_return(cashflows, terminal_cashflow: float, price: float,
                              terminal_growth: float, opening_distribution: float = 0.0) -> Solution:
    """Yield for a priced distribution path when uniqueness is established.

    Solve ``opening_distribution + PV(future distributions) = price``.
    A negative opening distribution is an additional initial contribution, so
    the effective investment is price minus opening_distribution. Nonnegative
    future cash flows with at least one positive payment have a strictly
    decreasing PV. Owner contributions (negative future distributions) keep the
    yield unique when they all come before the payouts: the owner's cash-flow
    sequence then changes sign once, and by Descartes' rule of signs (Laguerre's
    extension for the convergent perpetuity series in 1/(1+r)) the value equation
    has at most one root. Contributions after payouts begin (more than one sign
    change) remain valid valuations without a claimed unique yield.

    Residuals use reporting-currency units; the numerical budget is relative to
    the effective initial investment (the existing price budget when opening0).
    Positive perpetuities require r>g; finite paths require r>-1. Search caps at3.
    """
    try:
        ds = list(cashflows)
        finite(*ds, terminal_cashflow, price, terminal_growth, opening_distribution)
        if not ds or price <= 0 or terminal_growth <= -1:
            raise InvalidModel("positive price, nonempty distributions and terminal growth above -100% required")
        investment = price - opening_distribution
        if any(x < 0 for x in [*ds, terminal_cashflow]):
            signs = [x > 0 for x in [*ds, terminal_cashflow] if x != 0]
            first_payout = next((k for k, positive in enumerate(signs) if positive), len(signs))
            calls_after_payouts = not all(signs[first_payout:])
            if investment <= 0 or terminal_cashflow < 0 or calls_after_payouts or not any(signs):
                return Solution("not_identified", reason="owner contributions follow payouts (more than one sign change); yield uniqueness has not been established")
        if not math.isfinite(investment):
            return Solution("unresolved", reason="net opening investment exceeds numerical range")
        if not any(x > 0 for x in [*ds, terminal_cashflow]):
            if investment == 0:
                return Solution("indeterminate", reason="opening distribution exactly settles price; no future payment identifies a yield")
            return Solution("no_solution", reason="no positive future distributions to match net opening investment")
        if investment <= 0:
            return Solution("no_solution", reason="opening distribution covers price and future distributions are positive; no finite yield equates value")
        normalized = [d / investment for d in ds]
        normalized_terminal = terminal_cashflow / investment
        f = lambda r: discount(normalized, normalized_terminal, r, terminal_growth)-1
        boundary, hi = (terminal_growth if terminal_cashflow else -1.0), 3.0
        if boundary >= hi:
            return Solution("outside_domain", reason="no return in the admissible discount domain up to 300%")
        fhi = f(hi)
        if fhi > 0:
            return Solution("outside_domain", reason="matching return is above the 300% search bound")
        if fhi == 0:
            return Solution("solved", (hi,), (0.0,))
        # Starting at0 avoids evaluating very long finite paths unnecessarily
        # close to -100%. Approach the actual lower boundary only as needed.
        lo = max(0.0, boundary + max(1e-12, abs(boundary)*1e-12)) if terminal_cashflow else 0.0
        if lo >= hi:
            return Solution("outside_domain", reason="admissible lower boundary is above the 300% search bound")
        while f(lo) < 0:
            next_lo = (lo + boundary) / 2
            if next_lo == lo or next_lo <= boundary:
                return Solution("unresolved", reason="return approaches the lower discount boundary beyond floating-point resolution")
            lo = next_lo
        root = brentq(f, lo, hi, xtol=ROOT_XTOL)
        residual = f(root) * investment
        if abs(residual / investment) > RESIDUAL_RTOL:
            return Solution("unresolved", reason="return residual exceeds numerical tolerance")
        return Solution("solved", (root,), (residual,))
    except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
        return Solution("invalid", reason=str(exc))


def solve_return(e0: float, price: float, g0: float, g_terminal: float, years: int,
                 R0: float | None = None, RT: float | None = None) -> Solution:
    try:
        finite(e0, price, g0, g_terminal)
        if price <= 0 or e0 <= 0:
            raise InvalidModel("positive price and earnings required")
        ds, terminal = distributions(e0, g0, g_terminal, years, R0, RT)
        return solve_distribution_return(ds, terminal, price, g_terminal)
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        return Solution("invalid", reason=str(exc))

def _poly_roots(poly: Chebyshev) -> list[float]:
    """All roots on [-1,1], partitioned by derivative roots, including tangencies.

    Each partition is monotone. Scaling and the Chebyshev basis avoid a fixed
    grid that could miss close roots. Original PV residuals are checked later.
    """
    scale = float(np.max(np.abs(poly.coef)))
    poly = poly.trim(tol=32*np.finfo(float).eps*scale)
    if poly.degree() == 0:
        return []
    points = [-1.0, *_poly_roots(poly.deriv()), 1.0]
    roots = [x for x in points if abs(poly(x)) <= 64*np.finfo(float).eps*scale*len(poly.coef)]
    for a, b in zip(points, points[1:]):
        if poly(a)*poly(b) < 0:
            roots.append(brentq(poly, a, b, xtol=ROOT_XTOL))
    out = []
    for x in sorted(roots):
        if not out or abs(x-out[-1]) > 100*ROOT_XTOL:
            out.append(x)
    return out

def _monotone_growth(f,lo,hi,price):
    if f(lo)*f(hi)>0:
        return Solution("no_solution",reason="no solution in the documented admissible growth search domain")
    root=brentq(f,lo,hi,xtol=ROOT_XTOL)
    residual=f(root)*price
    if abs(residual/price)>RESIDUAL_RTOL:
        return Solution("unresolved",reason="growth residual exceeds numerical tolerance")
    return Solution("solved",(root,),(residual,))

def solve_growth(e0: float, price: float, r: float, g_terminal: float, years: int,
                 R0: float | None = None, RT: float | None = None) -> Solution:
    """All admissible starting-growth roots; do not conflate ambiguity and absence.

    PV is piecewise polynomial in g0, with breaks when growth investment is zero.
    Each polynomial is isolated using its derivative, not an arbitrary grid.
    """
    try:
        finite(e0, price, r, g_terminal)
        horizon(years)
        terminal_R=RT if RT is not None else R0
        zero_terminal=(terminal_R is not None and g_terminal>=0 and g_terminal==terminal_R)
        if e0 <= 0 or price <= 0 or (r <= g_terminal and not zero_terminal) or r <= -1 or g_terminal <= -1:
            raise InvalidModel("positive earnings/price and rate above terminal growth required")
        if years > 50:
            return Solution("unsupported", reason="growth inversion supports at most 50 annual periods")
        Rs = fade(R0, R0 if RT is None else RT, years) if R0 is not None else [None]*years
        if R0 is None and RT is not None:
            raise InvalidModel("terminal return requires initial return")
        if R0 is None:
            # Positive distributions without reinvestment are strictly increasing
            # in initial growth. Direct bracketing avoids unnecessary high-degree
            # polynomial conditioning over a broad growth interval.
            f=lambda g:present_value(e0/price,g,r,g_terminal,years)-1
            return _monotone_growth(f,-.9,3.0,price)
        if R0 is not None and min(Rs + ([RT] if RT is not None else [])) <= 0:
            raise InvalidModel("return on capital must be positive")
        if R0 is not None and g_terminal > (RT if RT is not None else R0):
            raise InvalidModel("terminal growth exceeds return on capital")
        lo, hi = -0.9, 3.0  # year-1 growth uses opening capital; subsequent funding bounds below
        a = fade(1.0, 0.0, years) + [0.0]
        b = fade(0.0, g_terminal, years) + [g_terminal]
        cuts = []
        for t, R in enumerate(Rs):
            if R is not None:
                if a[t+1] == 0 and b[t+1] > R:
                    raise InvalidModel("terminal growth exceeds return on capital")
                if a[t+1] > 0:
                    hi = min(hi, (R-b[t+1])/a[t+1])
            if a[t+1] > 0:
                cuts.append(-b[t+1]/a[t+1])
        if hi <= lo:
            return Solution("outside_domain", reason="empty funded growth domain")
        if 0<=g_terminal<r and min([*Rs,terminal_R])>=r:
            # Every funded positive-growth investment earns at least its cost;
            # after growth turns positive it stays nonnegative. Higher initial
            # growth then increases value (including the opening earnings base).
            f=lambda g:present_value(e0/price,g,r,g_terminal,years,R0,RT)-1
            return _monotone_growth(f,lo,hi,price)
        edges = [lo, *sorted(set(x for x in cuts if lo < x < hi)), hi]
        roots = []
        for left, right in zip(edges, edges[1:]):
            mid, half = (left+right)/2, (right-left)/2
            variable = Chebyshev([mid, half])
            e, pv = Chebyshev([e0]), Chebyshev([0.0])
            for t, R in enumerate(Rs):
                e = e*(1+a[t]*variable+b[t])
                next_g = a[t+1]*variable+b[t+1]
                retention = next_g/R if R is not None and next_g(0) > 0 else Chebyshev([0.0])
                pv += e*(1-retention)/(1+r)**(t+1)
            terminal_R = RT if RT is not None else R0
            retention = max(g_terminal, 0)/terminal_R if terminal_R is not None else 0
            if retention!=1:
                pv += e*(1+g_terminal)*(1-retention)/(r-g_terminal)/(1+r)**years
            objective = (pv-price)/price
            if np.max(np.abs(objective.coef)) <= RESIDUAL_RTOL:
                return Solution("indeterminate", reason="price does not identify growth on a whole interval")
            for critical in _poly_roots(objective.deriv()):
                if abs(objective(critical)) <= RESIDUAL_RTOL:
                    return Solution("weakly_identified", reason="price is within numerical resolution of a turning point")
            roots.extend(mid+half*x for x in _poly_roots(objective))
        unique, residuals = [], []
        for g in sorted(roots):
            residual = present_value(e0,g,r,g_terminal,years,R0,RT)-price
            if abs(residual) > RESIDUAL_RTOL*price:
                return Solution("unresolved", reason="growth residual exceeds numerical tolerance")
            if not unique or abs(g-unique[-1]) > 100*ROOT_XTOL:
                unique.append(g); residuals.append(residual)
        status = "no_solution" if not unique else "solved" if len(unique)==1 else "nonunique"
        return Solution(status, tuple(unique), tuple(residuals))
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        return Solution("invalid", reason=str(exc))

def implied_return(*args, **kwargs) -> float | None:
    return solve_return(*args, **kwargs).value

def implied_growth(*args, **kwargs) -> float | None:
    return solve_growth(*args, **kwargs).value

growth_path = fade
implied_irr = implied_return
