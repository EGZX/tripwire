"""Independent accounting identities, period domains and unresolved claim scopes."""
import copy
import json
import math

import pandas as pd
import pytest

from screener.evidence_assembly import assemble, capitalization_scenarios


QUARTERS = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])


def frame(rows, dates=QUARTERS):
    return pd.DataFrame(rows, index=dates, dtype=float).T


def sample():
    return {
        "symbol": "ANY", "info": {"currency": "USD", "financialCurrency": "USD",
        "regularMarketPrice": 20., "marketCap": 2000., "sharesOutstanding": 100.,
        "impliedSharesOutstanding": 100., "regularMarketTime": 1791576000},
        "quarterly_income_stmt": frame({"Total Revenue": [130, 120, 110, 100],
            "Total Operating Income As Reported": [13, 12, 11, 10],
            "Operating Income": [15, 14, 13, 12], "Pretax Income": [10, 9, 8, 7],
            "Tax Provision": [2, 1.8, 1.6, 1.4], "Net Income Common Stockholders": [8, 7.2, 6.4, 5.6],
            "Diluted Average Shares": [100, 100, 100, 100]}),
        "ttm_income_stmt": frame({"Total Revenue": [460], "Operating Income": [54],
            "Net Income Common Stockholders": [27.2]}, QUARTERS[:1]),
        "quarterly_cashflow": frame({"Operating Cash Flow": [20, 18, 16, 14],
                                     "Capital Expenditure": [-5, -4, -3, -2]}),
        "quarterly_balance_sheet": frame({"Stockholders Equity": [100], "Common Stock Equity": [100],
            "Total Equity Gross Minority Interest": [105], "Total Assets": [180],
            "Total Debt": [30], "Current Debt": [10], "Long Term Debt": [20],
            "Cash And Cash Equivalents": [15], "Ordinary Shares Number": [100]}, QUARTERS[:1])}


def test_four_quarter_reported_income_preserves_operating_costs_and_raw():
    data = sample(); before = copy.deepcopy(data)
    result = assemble(data)
    assert result.income.frame.loc["Total Operating Income As Reported"].iloc[0] == 46
    assert result.income.frame.loc["Total Revenue"].iloc[0] == 460
    assert "Diluted Average Shares" not in result.income.frame.index  # stocks are not additive flows
    assert result.cashflow.frame.loc["Operating Cash Flow"].iloc[0] == 68
    assert result.cashflow.frame.loc["Capital Expenditure"].iloc[0] == -14
    mismatch = [u for u in result.uncertainties if u["kind"] == "provider_ttm_mismatch"]
    assert len(mismatch) == 1 and mismatch[0]["field"] == "ebit"
    assert mismatch[0]["ttm"] == 54 and mismatch[0]["quarter_sum"] == 46
    for key, value in before.items():
        if isinstance(value, pd.DataFrame):
            pd.testing.assert_frame_equal(data[key], value)
    json.dumps(result.ledger, allow_nan=False)
    json.dumps(result.uncertainties, allow_nan=False)


def test_mixed_provider_ttm_is_replaced_with_single_quarter_basis():
    data = sample()
    data["quarterly_income_stmt"].attrs["accounting_basis"] = "US GAAP"
    data["ttm_income_stmt"].loc["Total Revenue"] = [480]
    data["ttm_income_stmt"].loc["Net Income Common Stockholders"] = [100]
    result = assemble(data)
    assert result.income.source == "ttm_income_from_quarters"
    assert result.ledger["income"]["accounting_basis"] == "US GAAP"
    assert result.income.frame.loc["Net Income Common Stockholders"].iloc[0] == pytest.approx(27.2)
    assert result.ledger["income"]["fields"]["revenue"]["periods"] == [x.date().isoformat() for x in QUARTERS]


def test_financial_ttm_needs_no_ebit_or_revenue():
    data = sample()
    data["quarterly_income_stmt"] = frame({"Net Income Common Stockholders": [8, 7, 6, 5]})
    result = assemble(data, income_required=("net_income",))
    assert result.income.source == "ttm_income_from_quarters"
    assert result.income.frame.iloc[0, 0] == 26


def test_generic_net_income_never_becomes_confirmed_common_income():
    data = sample()
    data["quarterly_income_stmt"] = frame({"Net Income": [8, 7, 6, 5]})
    result = assemble(data, income_required=("net_income",))
    assert result.income.frame.loc["Net Income"].iloc[0] == 26
    assert "Net Income Common Stockholders" not in result.income.frame.index
    assert result.ledger["income"]["fields"]["net_income"]["row"] == "Net Income"


@pytest.mark.parametrize("dates", [
    pd.to_datetime(["2026-06-30", "2025-12-31", "2025-06-30", "2024-12-31"]),
    pd.to_datetime(["2026-06-30", "2026-03-31", "2025-09-30", "2025-06-30"]),
])
def test_halfyears_and_missing_quarters_are_not_summed(dates):
    data = sample(); data["quarterly_income_stmt"].columns = dates
    result = assemble(data)
    assert result.income.source == "ttm_income_stmt"
    assert any(u["kind"] == "quarter_window_unavailable" for u in result.uncertainties)


def test_ytd_table_is_not_misread_as_four_quarters():
    data = sample(); data["quarterly_income_stmt"].attrs["period_type"] = "YTD"
    assert assemble(data).income.source == "ttm_income_stmt"


def test_fifty_three_week_calendar_and_local_dates_are_valid():
    data = sample()
    dates = pd.DatetimeIndex(["2026-07-04", "2026-04-04", "2026-01-03", "2025-09-27"], tz="Pacific/Auckland")
    data["quarterly_income_stmt"].columns = dates
    result = assemble(data)
    assert result.income.source == "ttm_income_from_quarters"
    assert result.income.date == pd.Timestamp("2026-07-04")


def test_different_statement_currency_never_summed_into_reporting_currency():
    data = sample(); data["quarterly_income_stmt"].attrs["currency"] = "JPY"
    result = assemble(data)
    assert result.income.source == "ttm_income_stmt"
    assert any(u["kind"] == "statement_currency_conflict" for u in result.uncertainties)


def test_alias_definitions_are_not_spliced_between_quarters():
    data = sample(); data["quarterly_income_stmt"].loc["Total Operating Income As Reported", QUARTERS[1]] = math.nan
    result = assemble(data)
    assert "Total Operating Income As Reported" not in result.income.frame.index
    assert result.income.frame.loc["Operating Income"].iloc[0] == 54
    assert result.ledger["income"]["fields"]["ebit"]["row"] == "Operating Income"


def test_missing_quarter_value_does_not_turn_into_zero():
    data = sample(); data["quarterly_cashflow"].iloc[1, 1] = math.nan
    result = assemble(data)
    assert result.cashflow.missing == ("ocf", "capex")
    assert result.cashflow.frame.empty


def test_newer_provider_ttm_is_not_replaced_by_old_quarters():
    data = sample(); data["ttm_income_stmt"].columns = pd.to_datetime(["2026-09-30"])
    assert assemble(data).income.date == pd.Timestamp("2026-09-30")
    assert assemble(data).income.source == "ttm_income_stmt"


def test_balance_claims_reconcile_parent_common_and_noncommon_arithmetically():
    data = sample()
    data["quarterly_balance_sheet"].loc["Common Stock Equity"] = [90]
    result = assemble(data)
    assert result.claims["equity"]["value"] == 100
    assert result.claims["common_equity"]["value"] == 90
    assert result.claims["preferred"]["value"] == 10
    assert result.claims["minority"]["value"] == 5
    assert result.claims["preferred"]["status"] == "derived"


def test_incomplete_debt_is_unbounded_not_assumed_debt_free():
    data = sample()
    data["quarterly_balance_sheet"] = data["quarterly_balance_sheet"].drop(index=["Total Debt", "Current Debt"])
    result = assemble(data)
    assert result.claims["debt"]["value"] is None
    assert result.claims["debt"]["bounds"]["minimum_disclosed_claims"] == 20
    assert result.claims["debt"]["bounds"]["maximum"] is None


def test_truncated_total_retains_cross_basis_filing_alternative_not_false_bound():
    data = sample()
    data["quarterly_balance_sheet"].loc["Total Debt"] = [20]
    data["quarterly_balance_sheet"] = data["quarterly_balance_sheet"].drop(index="Current Debt")
    fact = {"statement": "balance", "field": "debt", "period_end": "2026-06-30", "value": 35,
            "relation": "minimum", "source": "filing", "source_basis": "IFRS", "provider_basis": "US GAAP", "currency": "USD"}
    result = assemble(data, sourced_facts=[fact])
    debt = result.claims["debt"]
    assert debt["value"] == 20 and debt["status"] == "scope_uncertain"
    assert debt["bounds"]["minimum_disclosed_claims"] == 20
    assert debt["alternatives"][-1]["value"] == 35 and debt["alternatives"][-1]["conditional"]


def test_leases_create_conditional_cashflow_conventions_not_automatic_subtraction():
    data = sample(); bal = data["quarterly_balance_sheet"]
    bal.loc["Total Debt"] = [35]
    bal.loc["Capital Lease Obligations"] = [5]
    result = assemble(data)
    debt = result.claims["debt"]
    assert debt["value"] == 35
    assert any(a["value"] == 30 and a["conditional"] for a in debt["alternatives"])
    assert any(a["value"] == 30 and a["scope"] == "borrowings_excluding_reported_leases" for a in debt["alternatives"])


def test_balance_chooses_newest_complete_point_in_time_source():
    data = sample()
    data["balance_sheet"] = data["quarterly_balance_sheet"].copy()
    data["quarterly_balance_sheet"].columns = pd.to_datetime(["2025-12-31"])
    result = assemble(data)
    assert result.balance.source == "balance_sheet"
    assert result.balance.date == pd.Timestamp("2026-06-30")


def test_multi_class_or_dual_listing_produces_scenarios_not_nci_assertion():
    data = sample(); data["info"].update(impliedSharesOutstanding=200., marketCap=4000.)
    result = assemble(data)
    caps = {x["id"]: x for x in result.capitalizations}
    assert caps["provider_market_cap"]["value_quote"] == 4000
    assert caps["provider_market_cap"]["scope"] == "listing_based_aggregate_equity_proxy"
    assert caps["listed_share_proxy"]["value_quote"] == 2000
    assert caps["aggregate_share_proxy"]["value_quote"] == 4000
    assert "parent-only" not in json.dumps(result.uncertainties)


def test_adr_balance_share_count_does_not_scale_listing_marketcap():
    data = sample(); data["info"].update(currency="USD", financialCurrency="TWD", sharesOutstanding=20., impliedSharesOutstanding=20., marketCap=400.)
    caps, _ = capitalization_scenarios(data)
    assert {c["value_quote"] for c in caps} == {400.}
    assert {c["quote_currency"] for c in caps} == {"USD"}


@pytest.mark.parametrize("provider_cap", [2000., 200000.])
def test_pence_quote_and_major_or_minor_marketcap_share_one_unit(provider_cap):
    data = sample(); data["info"].update(currency="GBp", financialCurrency="GBP", regularMarketPrice=2000., marketCap=provider_cap)
    caps, _ = capitalization_scenarios(data)
    assert {c["value_quote"] for c in caps} == {2000.}
    assert {c["quote_currency"] for c in caps} == {"GBP"}


def test_missing_marketcap_still_preserves_conditional_listing_scenario():
    data = sample(); del data["info"]["marketCap"]
    result = assemble(data)
    assert result.capitalizations
    assert all(c["conditional"] for c in result.capitalizations)


def test_empty_data_returns_explicit_unknowns_and_serializable_evidence():
    result = assemble({"symbol": "UNKNOWN"})
    assert result.income.missing == ("revenue", "ebit")
    assert result.claims["debt"]["value"] is None
    assert result.capitalizations == ()
    json.dumps(result.claims, allow_nan=False)


def test_duplicate_dates_do_not_choose_an_arbitrary_observation():
    data = sample(); data["quarterly_income_stmt"].columns = pd.to_datetime(["2026-06-30", "2026-06-30", "2025-12-31", "2025-09-30"])
    result = assemble(data)
    assert result.income.source == "ttm_income_stmt"
    assert any(u["kind"] == "duplicate_period" for u in result.uncertainties)


@pytest.mark.parametrize("key,values", [
    ("currencies", ["USD", "EUR", "USD", "USD"]),
    ("accounting_bases", ["US GAAP", "IFRS", "US GAAP", "US GAAP"]),
])
def test_known_mixed_period_definitions_prevent_quarter_summation(key, values):
    data = sample()
    data["quarterly_income_stmt"].attrs[key] = dict(zip(QUARTERS, values))
    assert assemble(data).income.source == "ttm_income_stmt"


def test_same_basis_sourced_minimum_tightens_debt_bound_without_overwrite():
    data = sample()
    fact = {"statement": "balance", "field": "debt", "period_end": "2026-06-30", "value": 35,
            "relation": "minimum", "source": "filing", "source_basis": "US GAAP", "provider_basis": "US GAAP"}
    debt = assemble(data, sourced_facts=[fact]).claims["debt"]
    assert debt["value"] == 30
    assert debt["bounds"]["minimum_disclosed_claims"] == 35
    assert debt["alternatives"][-1]["conditional"] is False
    assert debt["status"] == "sourced_constraint_unreconciled"


def test_undated_or_wrong_currency_sourced_fact_does_not_fill_claim():
    data = sample()
    facts = [{"statement": "balance", "field": "debt", "value": 35, "source": "filing"},
             {"statement": "balance", "field": "debt", "value": 35, "source": "filing",
              "period_end": "2026-06-30", "currency": "EUR"}]
    result = assemble(data, sourced_facts=facts)
    assert result.claims["debt"]["value"] == 30
    assert any(u["kind"] == "sourced_period_mismatch" for u in result.uncertainties)
    assert any(u["kind"] == "sourced_currency_mismatch" for u in result.uncertainties)
