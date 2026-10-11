"""Accounting identities, adverse funding, and source/period boundaries."""
import copy

import pandas as pd
import pytest

from screener import financial_models as F, statements as S, valuation as V


def parameters(**changes):
    return {"common_equity": 1000.0, "roe_start": .12, "roe_terminal": .12,
            "book_growth_start": .03, "terminal_growth": .03, **changes}


@pytest.mark.parametrize("years", [1, 10, 30])
@pytest.mark.parametrize("roe", [.04, .10, .16])
def test_constant_return_and_book_growth_match_infinite_geometric_series(years, roe):
    p = parameters()
    p.update(roe_start=roe, roe_terminal=roe)
    out = F.project(p, years, .10)
    # First distribution=(ROE-g)*opening book, not next year's earnings again.
    expected = 1000 * (roe - .03) / (.10 - .03)
    assert out["value"] == pytest.approx(expected)
    assert out["residual_income_value"] == pytest.approx(expected)
    assert out["ledger"][0]["common_earnings"] == pytest.approx(1000 * roe)


def test_growth_at_cost_of_equity_is_value_neutral_even_with_capital_calls():
    p = parameters()
    p.update(roe_start=.10, roe_terminal=.10, book_growth_start=.30)
    out = F.project(p, 10, .10)
    assert out["value"] == pytest.approx(1000)
    assert out["cashflows"][0] == pytest.approx(-200)
    assert out["funding_required"] > 200
    assert out["conditional_on_capital_calls"]
    for year in out["ledger"]:
        assert year["opening_common_equity"] + year["common_earnings"] - year["net_distribution"] == pytest.approx(year["closing_common_equity"])


def test_losses_are_paid_for_not_clipped_or_immediately_refunded():
    p = parameters()
    p.update(roe_start=-.10, roe_terminal=.10)
    out = F.project(p, 5, .10)
    assert out["ledger"][0]["common_earnings"] == -100
    assert out["cashflows"][0] == pytest.approx(-130)
    assert out["funding_required"] >= 130
    assert out["value"] < p["common_equity"]


def test_explicit_book_losses_and_payouts_obey_clean_surplus():
    p = parameters()
    p.update(roe_path=[-.10, .10], book_growth_path=[-.20, .03])
    out = F.project(p, 2, .10)
    first = out["ledger"][0]
    assert first["closing_common_equity"] == 800
    assert first["common_earnings"] == -100
    assert first["net_distribution"] == 100  # explicit200 book release funds100 loss and100 payout
    assert out["value"] == pytest.approx(out["residual_income_value"])


@pytest.mark.parametrize("scale", [1e-12, 1e-6, 1, 1e6, 1e12])
def test_currency_unit_invariance_with_negative_distributions(scale):
    p = parameters()
    p.update(roe_start=-.10, roe_terminal=.10, book_growth_start=.15)
    reference = F.project(p, 10, .10)
    p["common_equity"] *= scale
    out = F.project(p, 10, .10)
    assert out["value"] / scale == pytest.approx(reference["value"])
    assert out["funding_required"] / scale == pytest.approx(reference["funding_required"])
    assert out["residual_income_value"] / scale == pytest.approx(reference["residual_income_value"])


def test_economic_terminal_return_does_not_change_when_policy_rate_changes():
    p = parameters()
    fair = F.project(p, 10, .08)
    policy = F.project(p, 10, .14)
    assert fair["cashflows"] == policy["cashflows"]
    assert fair["terminal_cashflow"] == policy["terminal_cashflow"]
    assert fair["value"] > policy["value"]
    assert policy["value"] == pytest.approx(policy["residual_income_value"])


def test_unfundable_perpetual_growth_is_rejected_and_zero_tail_is_finite():
    p = parameters()
    p["roe_terminal"] = .02
    with pytest.raises(V.InvalidModel, match="permanent book growth"):
        F.project(p, 10, .10)
    p["roe_terminal"] = .03
    out = F.project(p, 1, .03)
    assert out["terminal_cashflow"] == 0
    assert out["value"] == pytest.approx(90 / 1.03)
    assert out["value"] == pytest.approx(out["residual_income_value"])


@pytest.mark.parametrize("book", [0, -100])
def test_nonpositive_opening_book_needs_recapitalization(book):
    p = parameters()
    p["common_equity"] = book
    with pytest.raises(V.InvalidModel, match="recapitalization"):
        F.project(p, 10, .10)


def test_path_domain_validation():
    p = parameters()
    for key, value in [("roe_path", [1]), ("book_growth_path", [.1, -1])]:
        bad = {**p, key: value}
        with pytest.raises(V.InvalidModel):
            F.project(bad, 2, .10)
    with pytest.raises(V.InvalidModel):
        F.project(p, 0, .10)


def fixture():
    annual_dates = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])
    current = pd.to_datetime(["2026-06-30"])
    data = dict(symbol="UNTYPED", info={"financialCurrency": "USD"},
                income_stmt=pd.DataFrame([[120, 108, 96, 84]], index=["Net Income Common Stockholders"], columns=annual_dates),
                balance_sheet=pd.DataFrame([[1000, 900, 800, 700]], index=["Common Stock Equity"], columns=annual_dates),
                ttm_income_stmt=pd.DataFrame([[132], [200], [10]], index=["Net Income Common Stockholders", "Net Income", "Diluted Average Shares"], columns=current),
                quarterly_balance_sheet=pd.DataFrame([[1100], [1300], [200], [10]], index=["Common Stock Equity", "Stockholders Equity", "Preferred Stock Equity", "Ordinary Shares Number"], columns=current),
                cashflow=pd.DataFrame([[-30, -27, -24, -21], [-10, -9, -8, -7]], index=["Common Stock Dividend Paid", "Net Common Stock Issuance"], columns=annual_dates),
                earnings_estimate=pd.DataFrame({"avg": [12, 14], "low": [10, 12], "high": [14, 16], "currency": ["USD", "USD"], "numberOfAnalysts": [5, 5]}, index=["0y", "+1y"]))
    selections = dict(income=S.select([("ttm_income_stmt", data["ttm_income_stmt"]), ("income_stmt", data["income_stmt"])], ("net_income",)),
                      balance=S.select([("quarterly_balance_sheet", data["quarterly_balance_sheet"]), ("balance_sheet", data["balance_sheet"])], ("common_equity",), instant=True))
    context = dict(family="bank", years=10, hurdle=.10, terminal_growth=.03, cfg={})
    data["fetched_at"] = "2026-10-10T23:45:00+00:00"
    data["earnings_estimate_units"] = dict(source="controlled common-share EPS unit definition",
        as_of="2026-10-10", statement_period_end="2026-06-30", statement_share_field="Diluted Average Shares",
        statement_shares_per_estimate_unit=1.0, currency="USD")
    return data, selections, context


def test_builder_uses_common_book_and_aligned_consensus_not_total_equity():
    data, selections, context = fixture()
    result = F.build(data, selections, context)
    weight = 184 / 365  # June30 -> December31 of2026, within forecast endingJune30,2027
    expected = (12 * weight + 14 * (1 - weight)) * 10
    assert result["base"]["common_equity"] == 1100
    assert result["base"]["roe_start"] == pytest.approx(expected / 1100)
    # Mature franchise: hurdle plus half of today's excess return (capped at 5 points); new capital earns the hurdle.
    base_roe = result["base"]["roe_start"]
    assert result["base"]["roe_terminal"] == pytest.approx(.10 + min(.5 * max(0, base_roe - .10), .05))
    assert result["base"]["terminal_incremental_return"] == .10 and result["base"]["self_funded"] is True
    assert result["stress"]["roe_terminal"] == .10 and result["stress"]["roe_start"] <= .75 * base_roe
    assert result["stress"]["roe_start"] <= result["base"]["roe_start"] <= result["upside"]["roe_start"]
    assert result["base"]["book_growth_start"] == pytest.approx(sorted([64/700, 72/800, 80/900])[1])
    assert any("regulatory" in w for w in result["warnings"])
    assert any(e.get("input") == "consensus_avg_common_earnings" for e in result["evidence"])
    assert all("probability" not in e for e in result["evidence"])


def test_consensus_currency_and_fiscal_window_do_not_silently_mix():
    data, selections, context = fixture()
    data["earnings_estimate"]["currency"] = "INR"
    result = F.build(data, selections, context)
    assert result["base"]["roe_start"] == pytest.approx(132/1100)
    assert any("currency" in w for w in result["warnings"])
    data["earnings_estimate"]["currency"] = "USD"
    data["earnings_estimate"] = data["earnings_estimate"].drop(index="0y")
    result = F.forecast_income(data, selections)
    assert result["avg"] == 132
    assert "run rate" in result["basis"]


def test_negative_consensus_and_historical_losses_preserve_capital_calls():
    data, selections, context = fixture()
    data["earnings_estimate"][["avg", "low", "high"]] = -2.0
    result = F.build(data, selections, context)
    assert result["base"]["roe_start"] < 0
    projection = F.project(result["base"], 10, .10)
    assert projection["funding_required"] > 0
    assert any("additional common-owner capital" in w for w in result["warnings"])


def test_missing_payouts_and_history_are_visible_assumptions_not_zero():
    data, selections, context = fixture()
    data["cashflow"] = pd.DataFrame()
    result = F.build(data, selections, context)
    observed = sorted([900/800-1, 800/700-1, 1000/900-1])[1]
    # Observed book change includes issuance: without payout history it is capped at half the base return.
    assert result["base"]["book_growth_start"] == pytest.approx(min(observed, max(.03, .5 * result["base"]["roe_start"])))
    assert any("OCI" in w for w in result["warnings"]) and any("Payout history unavailable" in w for w in result["warnings"])
    data["balance_sheet"] = pd.DataFrame()
    result = F.build(data, selections, context)
    assert result["base"]["book_growth_start"] == .03
    assert any("No aligned annual" in w for w in result["warnings"])


def test_only_common_attributable_earnings_can_feed_common_equity_model():
    data, selections, context = fixture()
    frame = selections["income"].frame.drop(index="Net Income Common Stockholders")
    selections["income"] = S.select([("ttm_income_stmt", frame)], ("net_income",))
    with pytest.raises(V.InvalidModel, match="common-stockholder income"):
        F.build(data, selections, context)


def test_regulatory_shortfall_is_disclosed_not_ignored_or_substituted_for_book():
    data, selections, context = fixture()
    context["regulatory"] = dict(available_capital=80, capital_base=1000, required_capital_ratio=.10, source="independent controlled capital example")
    result = F.build(data, selections, context)
    assert result["base"]["common_equity"] == 1100
    assert any("shortfall" in w for w in result["warnings"])
    assert next(e["value"] for e in result["evidence"] if e["input"] == "reported_regulatory_surplus") == -20


def test_scenarios_are_ordered_and_book_growth_is_self_funded():
    data, selections, context = fixture()
    result = F.build(data, selections, context)
    out = [F.project(result[k], 10, .10) for k in ["stress", "base", "upside"]]
    assert out[0]["value"] <= out[1]["value"] <= out[2]["value"]
    for projection in out:  # no automatic owner capital calls: book never grows faster than its return
        assert projection["funding_required"] <= 1e-9 * projection["value"]
        assert all(y["book_growth"] <= y["roe"] + 1e-12 for y in projection["ledger"])
    assert result["stress"]["book_growth_start"] == pytest.approx(.5 * result["base"]["book_growth_start"])


def test_owner_funded_growth_cannot_create_value_by_assumption():
    # The SEZL pattern: 150% book growth at a 69% return fading to the hurdle. With owner-funded growth the value
    # was ~4x the self-funded value; self-funding caps growth at the return on book.
    p = dict(common_equity=233.0, roe_start=.69, roe_terminal=.0974, book_growth_start=1.5, terminal_growth=.03)
    funded = F.project(p, 10, .0974)
    selfish = F.project({**p, "self_funded": True}, 10, .0974)
    assert funded["funding_required"] > 10 * p["common_equity"] and selfish["funding_required"] <= 1e-9
    assert selfish["value"] < funded["value"] / 3


def test_builder_monetary_rescaling_preserves_returns_and_projection():
    data, selections, context = fixture()
    original = F.build(data, selections, context)
    scale = 1e-6
    for name in ["income_stmt", "balance_sheet", "ttm_income_stmt", "quarterly_balance_sheet", "cashflow"]:
        data[name] = data[name].astype(float)
        for label in data[name].index:
            if "Shares" not in label:
                data[name].loc[label] = data[name].loc[label] * scale
    data["earnings_estimate"] = data["earnings_estimate"].astype({"avg":float, "low":float, "high":float})
    data["earnings_estimate"][["avg", "low", "high"]] *= scale
    selections = dict(income=S.select([("ttm_income_stmt",data["ttm_income_stmt"])],("net_income",)),
                      balance=S.select([("quarterly_balance_sheet",data["quarterly_balance_sheet"])],("common_equity",)))
    rescaled = F.build(data, selections, context)
    for case in ["base", "stress", "upside"]:
        for rate in ["roe_start", "roe_terminal", "book_growth_start", "terminal_growth"]:
            assert rescaled[case][rate] == pytest.approx(original[case][rate])
        assert F.project(rescaled[case],10,.10)["value"] / scale == pytest.approx(F.project(original[case],10,.10)["value"])


def test_same_currency_adr_cannot_multiply_ordinary_shares_without_unit_proof():
    ordinary, selections, context = fixture()
    del ordinary["earnings_estimate_units"]
    adr = copy.deepcopy(ordinary)
    adr["earnings_estimate"][["avg", "low", "high"]] *= 5
    ordinary["info"].update(currency="USD", sharesOutstanding=10, regularMarketPrice=100)
    adr["info"].update(currency="USD", sharesOutstanding=2, regularMarketPrice=500)
    for data in (ordinary, adr):
        estimate = F.forecast_income(data, selections)
        assert estimate["avg"] == 132
        assert "run rate" in estimate["basis"]
        unit = next(e for e in estimate["evidence"] if e["input"] == "consensus_unit_reconciliation")
        assert unit["status"] == "rejected" and unit["estimate_rows"]
        assert any("unit evidence rejected" in w for w in estimate["warnings"])
    assert F.build(ordinary, selections, context)["base"] == F.build(adr, selections, context)["base"]


@pytest.mark.parametrize("factor", [5.0, .1])
def test_source_backed_adr_or_equivalent_share_class_factor_normalizes_earnings(factor):
    ordinary, selections, context = fixture()
    other_class = copy.deepcopy(ordinary)
    other_class["earnings_estimate"] = other_class["earnings_estimate"].astype({"avg":float,"low":float,"high":float})
    other_class["earnings_estimate"][["avg", "low", "high"]] *= factor
    other_class["earnings_estimate_units"]["statement_shares_per_estimate_unit"] = factor
    expected = F.forecast_income(ordinary, selections)
    normalized = F.forecast_income(other_class, selections)
    for metric in ("avg", "low", "high"):
        assert normalized[metric] == pytest.approx(expected[metric])
    a, b = F.build(ordinary,selections,context), F.build(other_class,selections,context)
    for case in ("base", "stress", "upside"):
        assert F.project(a[case],10,.1)["value"] == pytest.approx(F.project(b[case],10,.1)["value"])
    unit = next(e for e in normalized["evidence"] if e["input"] == "consensus_unit_reconciliation")
    assert unit["status"] == "verified" and unit["statement_shares_per_estimate_unit"] == factor


def _identity_fixture():
    data, selections, context = fixture()
    del data["earnings_estimate_units"]
    data["income_stmt"] = data["income_stmt"].astype(float)
    data["income_stmt"].loc["Diluted Average Shares"] = [10.,10.,10.,10.]
    data["income_stmt"].loc["Diluted EPS"] = [12.,10.8,9.6,8.4]
    selections["income"].frame.loc["Diluted EPS"] = [13.2]
    data["earnings_estimate"]["yearAgoEps"] = [12.,12.]
    return data, selections, context


def test_exact_matched_period_common_earnings_identity_can_establish_factor_one():
    data, selections, _ = _identity_fixture()
    estimate = F.forecast_income(data, selections)
    assert estimate["avg"] == pytest.approx((12*184/365+14*181/365)*10)
    unit = next(e for e in estimate["evidence"] if e["input"] == "consensus_unit_reconciliation")
    assert unit["status"] == "verified"
    assert unit["basis"] == "matched-period common-income/weighted-share/EPS identities within EPS rounding"


def test_share_count_or_price_ratio_is_not_evidence_of_economic_rights():
    data, selections, _ = _identity_fixture()
    data["earnings_estimate"]["yearAgoEps"] = [60.,60.]
    data["earnings_estimate"][["avg", "low", "high"]] *= 5
    data["info"].update(sharesOutstanding=2, impliedSharesOutstanding=10, regularMarketPrice=500, marketCap=5000)
    estimate = F.forecast_income(data, selections)
    assert estimate["avg"] == 132
    assert next(e for e in estimate["evidence"] if e["input"]=="consensus_unit_reconciliation")["status"] == "rejected"


def test_intervening_split_requires_current_dated_unit_normalization():
    data, selections, _ = _identity_fixture()
    data["info"]["lastSplitDate"] = pd.Timestamp("2026-08-01T00:00:00Z").timestamp()
    data["earnings_estimate"] = data["earnings_estimate"].astype({"avg":float,"low":float,"high":float})
    data["earnings_estimate"][["avg", "low", "high"]] /= 2
    rejected = F.forecast_income(data, selections)
    assert rejected["avg"] == 132
    assert any("split" in w for w in rejected["warnings"])
    # Each post-split estimate unit is one-half of an old statement share.
    data["earnings_estimate_units"] = {**fixture()[0]["earnings_estimate_units"], "statement_shares_per_estimate_unit": .5}
    normalized = F.forecast_income(data, selections)
    assert normalized["avg"] == pytest.approx((12*184/365+14*181/365)*10)


@pytest.mark.parametrize("change", [
    {"source":""}, {"as_of":"2026-10-11"}, {"statement_period_end":"2026-03-31"},
    {"statement_share_field":"Ordinary Shares Number"}, {"currency":"EUR"},
    {"statement_shares_per_estimate_unit":0}, {"statement_shares_per_estimate_unit":-5},
    {"statement_shares_per_estimate_unit":float("inf")}, {"statement_shares_per_estimate_unit":True},
])
def test_incompatible_unit_metadata_does_not_disable_reported_income_valuation(change):
    data, selections, context = fixture()
    data["earnings_estimate_units"].update(change)
    estimate = F.forecast_income(data,selections)
    assert estimate["avg"] == 132
    assert next(e for e in estimate["evidence"] if e["input"]=="consensus_unit_reconciliation")["status"] == "rejected"
    assert F.project(F.build(data,selections,context)["base"],10,.1)["value"] > 0


def test_unit_observation_date_is_snapshot_utc_not_client_or_refreshed_quote_date():
    data, selections, _ = fixture()
    data["fetched_at"] = "2026-10-11T01:45:00+02:00"  # still October10 in UTC
    data["info"]["regularMarketTime"] = pd.Timestamp("2026-10-12T12:00:00Z").timestamp()
    assert "consensus" in F.forecast_income(data,selections)["basis"]
    data["earnings_estimate_units"]["as_of"] = "2026-10-11"
    assert F.forecast_income(data,selections)["avg"] == 132


def test_forecast_dependencies_name_only_the_verified_denominator_actually_used():
    data,selections,_=fixture()
    average=F.forecast_income(data,selections)
    assert average['dependencies']=={'income':['net_income','shares_avg']}
    assert any(d['field']=='shares_avg' and d['period_type']=='ttm' and d['period_end']=='2026-06-30'
               for d in average['dependency_evidence'])
    del data['earnings_estimate_units']
    rejected=F.forecast_income(data,selections)
    assert rejected['dependencies']=={'income':['net_income']}
    assert not any(d['field'].startswith('shares_') for d in rejected['dependency_evidence'])


def test_outstanding_share_fallback_does_not_claim_to_use_missing_weighted_shares():
    data,selections,_=fixture()
    frame=selections['income'].frame.drop(index='Diluted Average Shares')
    selections['income']=S.select([('ttm_income_stmt',frame)],('net_income',))
    data['earnings_estimate_units']['statement_share_field']='Ordinary Shares Number'
    estimate=F.forecast_income(data,selections)
    assert estimate['dependencies']=={'income':['net_income'],'balance':['shares_out']}
    assert any(d['field']=='shares_out' and d['period_type']=='instant' for d in estimate['dependency_evidence'])


def test_historical_diluted_common_income_alias_is_preserved_in_valuation_dependencies():
    data,selections,context=fixture()
    data['income_stmt']=data['income_stmt'].rename(index={'Net Income Common Stockholders':'Diluted NI Availto Com Stockholders'})
    plan=F.build(data,selections,context)
    historical=[e for e in plan['evidence'] if e.get('input')=='historical_common_book']
    assert any(e.get('common_income')==120 for e in historical)
    assert any(d['statement']=='income' and d['field']=='net_income' and d['period_type']=='annual'
               and d['period_end']=='2025-12-31' for d in plan['dependency_evidence'])
    assert S.latest(data['income_stmt'],'net_income')==120
