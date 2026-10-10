"""Earnings-based distribution model.

Earnings e_t grow at g_t (fading linearly from g0 to the terminal rate gT over `years`). Growth has to be
funded: reinvestment during year t at a return on new capital R_t pays for next year's growth, so the
business retains g_{t+1} / R_t of year-t earnings and the owner receives d_t = e_t * (1 - g_{t+1} / R_t).
R fades linearly from R0 to RT. R0 = None means "no reinvestment needed" (plain extrapolation; diagnostics).

After `years`, a Gordon terminal value on d_{N+1} at (r - gT). End-of-year discounting.
Standard result (McKinsey value-driver formula): when R == r throughout, value = e_1 / r regardless of
later growth, i.e. growth beyond year 1 is value-neutral. Year-1 growth was funded by past reinvestment the
buyer never paid for, so it is not charged.
"""
from __future__ import annotations

import math

from scipy.optimize import brentq


def fade(a: float, b: float, years: int) -> list[float]:
    """Linear path from a (year 1) to b (year `years`)."""
    if years == 1:
        return [a]
    return [a + (b - a) * (t / (years - 1)) for t in range(years)]


def distributions(e0: float, g0: float, g_terminal: float, years: int,
                  R0: float | None = None, RT: float | None = None) -> tuple[list[float], float]:
    """Owner distributions for years 1..N and the first terminal-year distribution d_{N+1}."""
    gs = fade(g0, g_terminal, years) + [g_terminal]  # g_{N+1} = terminal growth
    Rs = fade(R0, RT if RT is not None else R0, years) if R0 is not None else [None] * years
    e, out = e0, []
    for t, R in enumerate(Rs):
        e *= 1 + gs[t]
        g_next = gs[t + 1]
        out.append(e * (1 - g_next / R) if R else e)
    R_term = Rs[-1]
    d_term = e * (1 + g_terminal) * ((1 - g_terminal / R_term) if R_term else 1.0)
    return out, d_term


def present_value(e0: float, g0: float, r: float, g_terminal: float, years: int,
                  R0: float | None = None, RT: float | None = None) -> float:
    if r <= g_terminal:
        return math.inf
    ds, d_term = distributions(e0, g0, g_terminal, years, R0, RT)
    pv = sum(d / (1 + r) ** t for t, d in enumerate(ds, start=1))
    return pv + d_term / (r - g_terminal) / (1 + r) ** years


def _root(f, lo: float, hi: float) -> float | None:
    """Bracketed root or None. Never returns a bracket endpoint as if it were a solution."""
    try:
        flo, fhi = f(lo), f(hi)
        if not (math.isfinite(flo) and math.isfinite(fhi)) or flo * fhi > 0:
            return None
        return brentq(f, lo, hi)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def implied_return(e0: float, price: float, g0: float, g_terminal: float, years: int,
                   R0: float | None = None, RT: float | None = None) -> float | None:
    """Discount rate at which the distribution path is worth `price` (None if e0 <= 0 or no root in (gT, 300%])."""
    if not (e0 and e0 > 0 and price and price > 0):
        return None
    return _root(lambda r: present_value(e0, g0, r, g_terminal, years, R0, RT) - price, g_terminal + 1e-4, 3.0)


def implied_growth(e0: float, price: float, r: float, g_terminal: float, years: int,
                   R0: float | None = None, RT: float | None = None) -> float | None:
    """Starting growth (fading to terminal) that justifies `price` at rate r. With reinvestment the search is
    limited to growth the business can fund (g <= R0). None if nothing in the bracket matches."""
    if not (e0 and e0 > 0 and price and price > 0) or r <= g_terminal:
        return None
    hi = min(3.0, R0) if R0 is not None else 3.0
    return _root(lambda g: present_value(e0, g, r, g_terminal, years, R0, RT) - price, -0.9, hi)


# backwards-compatible names
growth_path = fade
implied_irr = implied_return
