"""Regression tests for the 2026-10-11 model validation: each test pins one confirmed defect.

The failures they guard against were reproduced on the live run before the fix (NBIS +99.9 from discontinued
income, LUNR from one non-operating profit year, SEZL from owner-funded growth, AMD stress above base,
large caps blocked by a 0.03% income difference, growth from the wrong fiscal year).
"""
import json
import math

import pandas as pd
import pytest

from screener import market, operating_models as O, scoring, statements as S
from test_metrics import YEARS, frame, operating, run


def _set(d, key, row, values):
    d[key].loc[row] = values


def test_clean_income_removes_discontinued_operations_and_after_tax_unusual_items():
    df = frame({"Net Income Common Stockholders": [82.5, 241.3], "Net Income Discontinuous Operations": [72.7, 564.9],
                "Total Unusual Items": [681.3, -2.4], "Tax Effect Of Unusual Items": [197.6, -0.6]}, YEARS[:2])
    clean = S.clean_common_income(df).sort_index(ascending=False)
    assert list(clean.round(1)) == [round(82.5 - 72.7 - (681.3 - 197.6), 1), round(241.3 - 564.9 - (-2.4 + 0.6), 1)]
    assert S.clean_common_income(frame({"Total Revenue": [1.0]}, YEARS[:1])).empty  # no income row: still missing


def test_divestiture_income_cannot_become_a_mature_operating_margin():
    # NBIS pattern: tiny revenue years carrying large discontinued-operations income.
    d = operating()
    _set(d, "income_stmt", "Total Revenue", [530, 92, 10, 13.5])
    _set(d, "income_stmt", "Net Income Common Stockholders", [82.5, -641, 241, 746])
    d["income_stmt"].loc["Net Income Discontinuous Operations"] = [72.7, -289, 565, 1051]
    m = run(d)
    base = json.loads(m["scenario_parameters"])["base"]
    assert base["margin_terminal"] < 0.5          # was 12.4 (1,239%) before the fix
    assert m["entry_score"] < 50


def test_one_profitable_year_among_losses_falls_back_to_the_industry_reference():
    d = operating()
    _set(d, "income_stmt", "Net Income Common Stockholders", [-50, -40, 60, -30])  # LUNR: one positive year
    m = run(d)
    assumptions = json.loads(m["model_assumptions"])
    terminal = next(a for a in assumptions if isinstance(a, dict) and a.get("parameter") == "terminal_margin")
    assert terminal["basis"].startswith("industry reference")


def test_large_losses_are_kept_and_large_gains_are_excluded():
    d = operating()
    _set(d, "ttm_income_stmt", "Net Income Common Stockholders", [-1e7])  # loss far beyond revenue: real, kept
    assert json.loads(run(d)["scenario_results"])["base"]["raw_value"] < 0
    d = operating()
    _set(d, "income_stmt", "Net Income Common Stockholders", [152, 138, 5000, 112])  # +600% margin year
    evidence = json.loads(run(d)["model_evidence"])
    excluded = next(e for e in evidence if e.get("field") == "excluded_margins")["value"]
    assert [x["period"] for x in excluded] == [YEARS[2].date().isoformat()]


def test_ntm_growth_uses_the_overlapping_fiscal_years():
    est = pd.DataFrame({"growth": [0.1791, 0.1960]}, index=["0y", "+1y"])
    fy = pd.Timestamp("2026-06-30")
    g, nxt, basis, _ = O.ntm_growth(est, fy, fy)                   # TTM ends at the fiscal year end
    assert g == pytest.approx(0.1791) and nxt == pytest.approx(0.1960) and "100% current year" in basis
    g, _, _, _ = O.ntm_growth(est, pd.Timestamp("2026-12-31"), fy) # half way into the fiscal year
    w = 181 / 365  # 2026-12-31 -> 2027-06-30 of the fiscal year 2026-06-30 -> 2027-06-30
    assert g == pytest.approx(0.1791 * w + 0.1960 * (1 - w))


def test_growth_estimate_is_not_compounded_for_a_decade():
    path = O.growth_path(0.68, 0.40, 0.03, 10)
    assert path[:2] == [0.68, 0.40] and path[-1] == pytest.approx(0.03 + 0.37 * 0.5 ** 4)
    multiple = math.prod(1 + x for x in path)
    linear = math.prod(1 + 0.68 + (0.03 - 0.68) * t / 9 for t in range(10))
    assert multiple < linear / 2                                   # 18.6x revenue under the old linear fade


def test_stress_never_exceeds_base():
    # AMD pattern: new equity earns less than the hurdle, so the low-growth case is worth MORE than base.
    d = operating(revenue_estimate=pd.DataFrame({"growth": [0.75, 0.75]}, index=["0y", "+1y"]))
    _set(d, "balance_sheet", "Stockholders Equity", [4000, 3900, 3800, 3700])
    d["quarterly_balance_sheet"].loc["Stockholders Equity"] = [4000]
    _set(d, "ttm_income_stmt", "Net Income Common Stockholders", [60])
    m = run(d)
    assert m["value_adverse"] <= m["value_reasonable"] + 1e-9


def test_ownership_scope_needs_a_material_income_gap():
    info = {"sharesOutstanding": 5.8e9, "impliedSharesOutstanding": 12.2e9}   # dual share classes
    assert market.ownership_check(info, 244.119e9, 244.205e9, {}) is None   # 0.035%: not a partial claim
    assert market.ownership_check({"sharesOutstanding": 453e6, "impliedSharesOutstanding": 1.704e9},
                                  1.126e9, 4.896e9, {}) is not None          # listed class: a quarter of the earnings


def test_quarters_that_do_not_add_up_to_the_annual_are_flagged():
    q_dates = pd.to_datetime(["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"])
    annual = frame({"Total Revenue": [1000.0], "Net Income Common Stockholders": [-265.1]}, YEARS[:1])
    quarters = pd.DataFrame({"Total Revenue": [250.0] * 4, "Net Income Common Stockholders": [-88.1] * 4}, index=q_dates).T
    checks = {c["field"]: c["status"] for c in S.annual_tieout(annual, quarters, ("revenue", "net_income"))}
    assert checks == {"revenue": "ties", "net_income": "unreconciled"}       # AVAV: -352 vs -265


def test_data_flag_list_blocks_only_real_data_problems():
    cfg = {"scoring": {"w_value": .4, "w_quality": .3, "w_growth": .2, "w_balance": .1}}
    status = lambda flag: scoring.score(pd.DataFrame([{"class": "operating", "entry_score": 20, "flags": flag,
                                                       "not_rankable": ""}]), cfg).iloc[0]["status"]
    for informational in ("country exposure uses provider-country proxy", "country exposure unavailable",
                          "owner capital contributions included in valuation; financing availability is uncertain",
                          "fx USD->EUR"):
        assert status(informational) == "eligible", informational
    for blocking in ("stale or future quote", "unresolved ownership scope: x", "stale financials",
                     "TTM/quarterly inputs unreconciled: quarters do not add up to the audited annual figure",
                     "holding company valued on consolidated book; NAV / sum-of-parts not modeled"):
        assert status(blocking) == "data flag", blocking


def test_missing_revenue_is_missing_evidence_not_zero_revenue():
    d = operating()
    d["ttm_income_stmt"] = d["ttm_income_stmt"].drop(index="Total Revenue")
    d["income_stmt"] = d["income_stmt"].drop(index="Total Revenue")
    m = run(d)
    assert m["model"] != "asset_recovery" and m["not_rankable"]


def test_estimates_are_used_only_when_their_base_year_reconciles():
    # 6301.T: Yahoo's '0y' yearAgoRevenue is one quarter (1.116tn) instead of the fiscal year (4.133tn): +297%.
    est = pd.DataFrame({"avg": [4.4334e12, 4.6229e12], "yearAgoRevenue": [1.116e12, 4.4334e12],
                        "growth": [2.9724, 0.0427], "currency": ["JPY", "JPY"]}, index=["0y", "+1y"])
    g, notes, _ = O.estimate_growths(est, 4.132751e12, "JPY", ttm_revenue=4.132751e12)
    assert g == {} and "does not reconcile" in notes[0]
    # ICE pattern: estimates on net revenue (9.9bn) vs reported gross (12.6bn): another definition, not used either.
    ice = pd.DataFrame({"avg": [11.06e9], "yearAgoRevenue": [9.93e9], "growth": [0.114]}, index=["0y"])
    assert O.estimate_growths(ice, 12.64e9, "USD", 13.43e9)[0] == {}
    # reconciled: the provider's own growth rates are used unchanged
    ok = pd.DataFrame({"avg": [349.0e9, 380.0e9], "yearAgoRevenue": [332.2e9, 349.0e9], "growth": [0.051, 0.089]}, index=["0y", "+1y"])
    assert O.estimate_growths(ok, 323.9e9, "USD", 361.1e9)[0] == {"0y": 0.051, "+1y": 0.089}


def test_estimate_base_is_the_just_closed_fiscal_year_when_annuals_lag():
    # Annual table ends a year earlier than the TTM; '0y' is built on the TTM (the just-closed fiscal year).
    est = pd.DataFrame({"avg": [74.0e9, 82.0e9], "yearAgoRevenue": [37.4e9, 74.0e9], "growth": [0.979, 0.108],
                        "currency": ["USD", "USD"]}, index=["0y", "+1y"])
    g, notes, base = O.estimate_growths(est, 25.1e9, "USD", ttm_revenue=37.4e9)
    assert base == "ttm" and g["0y"] == pytest.approx(0.979) and not notes
    g1, _, _, _ = O.ntm_growth(est, pd.Timestamp("2026-08-31"), pd.Timestamp("2025-08-31"), 25.1e9, "USD", 37.4e9)
    assert g1 == pytest.approx(0.979)   # the next twelve months are exactly the fiscal year in '0y'


def test_numpy_integer_estimates_are_numbers():
    import numpy as np
    est = pd.DataFrame({"avg": np.array([4433427671770, 4622887206430], dtype=np.int64),
                        "yearAgoRevenue": np.array([4132751000000, 4433427671770], dtype=np.int64),
                        "growth": [0.0727, 0.0427], "currency": ["JPY", "JPY"]}, index=["0y", "+1y"])
    g, notes, _ = O.estimate_growths(est, 4.132751e12, "JPY")
    assert g["0y"] == pytest.approx(0.0727) and not notes


def test_estimates_in_another_currency_use_the_provider_growth_rate():
    # ADR: estimates in USD, statements in TWD; a revenue comparison is meaningless, a growth rate is not.
    est = pd.DataFrame({"avg": [120e9], "yearAgoRevenue": [100e9], "growth": [0.20], "currency": ["USD"]}, index=["0y"])
    g, notes, _ = O.estimate_growths(est, 3.2e12, "TWD", 3.4e12)
    assert g == {"0y": 0.20} and "provider growth rates used" in notes[0]


def test_model_policy_is_configurable_and_rejects_unknown_settings():
    from screener import policy
    try:
        assert policy.configure({"model_policy": {"growth_half_life": 4}}).growth_half_life == 4
        assert O.growth_path(0.5, 0.5, 0.0, 4)[-1] == pytest.approx(0.5 * 0.5 ** 0.5)  # read at call time
        with pytest.raises(ValueError, match="unknown setting"):
            policy.configure({"model_policy": {"growth_halflife": 4}})
        with pytest.raises(ValueError, match="nonnegative"):
            policy.configure({"model_policy": {"margin_limit": -1}})
    finally:
        policy.configure({})
    assert policy.get() == policy.Policy()


def test_every_industry_crosswalk_entry_points_at_a_reference_industry():
    # Adding a provider label is one line; a typo in the target would silently fall back to the broad reference.
    industries = O.references()["industries"]
    assert not {k: v for k, v in O.INDUSTRY_REFERENCE.items() if v not in industries}
