"""Economic routing provenance and independently calculated component values."""
from copy import deepcopy
import json

import pytest

from screener import models, valuation as V
from screener.business_models import profile, sum_of_parts, validate_sum_of_parts


def bridge(scope, **values):
    fields = ("debt", "cash", "other_claims", "corporate_cost_pv") if scope == "parent_only" else ("debt", "cash", "preferred", "minority", "other_claims")
    result = {k: values.get(k, 0.) for k in fields}
    result.update(source="dated debt and cash schedule", scope=scope,
                  claim_ids={k: [scope + ":" + k] for k in fields if result[k]})
    return result


def parts():
    return {"currency": "USD", "source": "segment accounts and ownership schedule",
            "parent_bridge": bridge("parent_only", debt=10., cash=5., other_claims=2., corporate_cost_pv=20.),
            "components": [
                {"id": "bank", "source": "bank standalone statements", "model": "bank_capital",
                 "claim": "equity", "ownership": 1., "currency": "USD", "fx_to_parent": 1.,
                 "discount_rate": .1, "discount_rate_basis": "cost_of_equity", "terminal_growth": 0.,
                 "parameters": {"net_income": 10., "growth": 0., "capital_growth": 0.,
                    "available_capital": 100., "capital_base": 1000., "required_capital_ratio": .1,
                    "payout_limit": 1., "releasable_excess": 0.}},
                {"id": "factory", "source": "factory standalone statements", "model": "operating_forecast",
                 "claim": "firm", "ownership": .5, "currency": "EUR", "fx_to_parent": 2.,
                 "discount_rate": .1, "discount_rate_basis": "wacc", "terminal_growth": 0.,
                 "firm_bridge": bridge("component_only", debt=50., cash=10.),
                 "parameters": {"revenue": 100., "growth": 0., "margin_start": .2, "margin_terminal": .2,
                    "tax_rate": 0., "sales_to_capital": 1., "maintenance_net_capex": 0., "funding_capacity": 0.}}
            ]}


def test_mixed_bank_and_industrial_ownership_currency_and_parent_costs():
    p = parts(); before = deepcopy(p)
    result = sum_of_parts(p, 10, .1, 0., models.evaluate)
    # Bank pays10 perpetually =>100. Factory FCFF20 =>EV200,
    # local equity200-50+10=160; ownership50% x FX2 =>USD160.
    # Parent-only adjustment5-10-2-20=-27; common value233.
    assert result.value == pytest.approx(233.)
    assert result.claim == "equity"
    assert result.components[0]["component_bridge_effect"] == 0
    assert result.components[1]["common_equity_before_ownership"] == pytest.approx(160.)
    assert result.parent_bridge["effect"] == -27.
    assert result.parent_bridge["application_count"] == 1
    assert p == before
    json.dumps(result.components, allow_nan=False)


def test_ownership_scales_component_after_claim_bridge_not_before():
    p = parts(); p["components"][1]["ownership"] = .25
    result = sum_of_parts(p, 3, .2, 0., models.evaluate)
    assert result.components[1]["value_in_parent_currency"] == pytest.approx(80.)
    assert result.value == pytest.approx(153.)


def test_component_discount_rates_are_independent_of_parent_context_rate():
    p = parts()
    low = sum_of_parts(p, 10, .07, 0., models.evaluate)
    high = sum_of_parts(p, 10, .15, 0., models.evaluate)
    assert low.value == high.value  # explicit local rates govern; not revenue weighting
    p["components"][0]["discount_rate"] = .2
    assert sum_of_parts(p, 10, .1, 0., models.evaluate).value == pytest.approx(183.)


def test_duplicate_component_and_parent_debt_is_rejected():
    p = parts()
    p["parent_bridge"]["claim_ids"]["debt"] = p["components"][1]["firm_bridge"]["claim_ids"]["debt"]
    errors = validate_sum_of_parts(p)
    assert any("duplicate economic claim" in error for error in errors)
    with pytest.raises(V.InvalidModel, match="duplicate economic claim"):
        sum_of_parts(p, 10, .1, 0., models.evaluate)


def test_parent_claim_already_inside_equity_component_is_rejected():
    p = parts()
    p["components"][0]["included_claim_ids"] = ["parent_only:debt"]
    assert any("duplicate economic claim" in e for e in validate_sum_of_parts(p))


def test_equity_component_cannot_receive_second_debt_bridge():
    p = parts(); p["components"][0]["firm_bridge"] = bridge("component_only", debt=5.)
    assert any("already reflects component financing" in e for e in validate_sum_of_parts(p))


@pytest.mark.parametrize("mutation,expected", [
    (lambda p: p["components"][1].pop("terminal_growth"), "cross-currency"),
    (lambda p: p["components"][0].update(fx_to_parent=2.), "same-currency"),
    (lambda p: p["components"][1].update(fx_to_parent=0.), "positive fx"),
    (lambda p: p["components"][1].update(ownership=1.1), "ownership"),
    (lambda p: p["components"][1].update(discount_rate_basis="cost_of_equity"), "discount-rate basis"),
    (lambda p: p["parent_bridge"].update(scope="consolidated"), "scope must"),
    (lambda p: p["parent_bridge"].pop("debt"), "explicit finite"),
    (lambda p: p["components"][1]["firm_bridge"]["claim_ids"].pop("debt"), "economic claim IDs"),
    (lambda p: p["components"][1].update(id="bank"), "unique"),
])
def test_invalid_nested_contracts(mutation, expected):
    p = parts(); mutation(p)
    assert any(expected in e for e in validate_sum_of_parts(p))


def test_evaluator_claim_mismatch_is_not_silently_bridged():
    p = parts()
    with pytest.raises(V.InvalidModel, match="different economic claim"):
        sum_of_parts(p, 10, .1, 0., lambda *args: {"claim": "firm", "value": 100.})


def test_negative_subsidiary_equity_requires_explicit_recourse():
    p = parts(); p["components"][1]["firm_bridge"]["debt"] = 250.
    with pytest.raises(V.InvalidModel, match="recourse"):
        sum_of_parts(p, 10, .1, 0., models.evaluate)
    p["components"][1]["recourse"] = "limited_liability"
    assert sum_of_parts(p, 10, .1, 0., models.evaluate).value == pytest.approx(73.)
    p["components"][1]["recourse"] = "pro_rata_support"
    assert sum_of_parts(p, 10, .1, 0., models.evaluate).value == pytest.approx(33.)


def test_scalar_common_nav_and_source_labels():
    p = parts(); p["components"] = p["components"][:1]
    result = sum_of_parts(p, 10, .1, 0., lambda *args: 100.)
    assert result.value == 73.
    assert result.components[0]["source"] == "bank standalone statements"


def test_missing_provider_classification_uses_general_economics_with_uncertainty():
    result = profile({"quoteType": "EQUITY"}, {})
    assert result["family"] == "operating" and result["class"] == "operating"
    assert result["gics"] is None
    assert result["uncertainties"]
    assert "blocker" not in result


def test_yahoo_industry_is_not_represented_as_verified_gics():
    result = profile({"quoteType": "EQUITY", "sector": "Financial Services", "industry": "Banks - Regional"}, {})
    assert result["family"] == "bank"
    assert result["taxonomy"] == "provider_labels"
    assert result["gics"] is None
    assert "not verified GICS" in result["provider_labels"]["taxonomy"]


def test_source_provided_gics_remains_separate_and_does_not_override_economics():
    info = {"quoteType": "EQUITY", "sector": "Technology", "industry": "Software - Application"}
    supplied = {"source": "issuer economics research", "gics": {"sector": "Financials", "sub_industry": "Regional Banks",
                "source": "licensed classification export", "as_of": "2026-10-11"}}
    result = profile(info, supplied)
    assert result["family"] == "bank"
    assert result["gics"]["status"] == "source_provided"
    assert result["provider_labels"]["sector"] == "Technology"
    supplied["model_family"] = "operating"
    assert profile(info, supplied)["family"] == "operating"
    assert profile(info, supplied)["gics"]["sub_industry"] == "Regional Banks"


def test_unattributed_gics_label_is_not_used_as_verified_routing():
    result = profile({"quoteType": "EQUITY", "industry": "Software - Application"},
                     {"gics": {"sub_industry": "Regional Banks"}})
    assert result["family"] == "operating"
    assert result["gics"]["status"] == "unverified_metadata"


@pytest.mark.parametrize("industry,sector,family", [
    ("REIT - Mortgage", "Real Estate", "mortgage_reit"),
    ("Mortgage Real Estate Investment Trusts", "Financials", "mortgage_reit"),
    ("REIT - Industrial", "Real Estate", "reit"),
    ("Insurance - Life", "Financial Services", "insurer"),
    ("Insurance Brokers", "Financial Services", "operating"),
    ("Utilities - Regulated Electric", "Utilities", "regulated"),
    ("Capital Markets", "Financial Services", "financial_other"),
    ("Credit Services", "Financial Services", "financial_other"),
    ("Steel", "Basic Materials", "cyclical"),
    ("Conglomerates", "Industrials", "hybrid"),
    ("Multi-Sector Holdings", "Financials", "holdco"),
])
def test_economic_family_routes(industry, sector, family):
    assert profile({"quoteType": "EQUITY", "industry": industry, "sector": sector}, {})["family"] == family


def test_fund_exclusion_precedes_operating_override():
    assert profile({"quoteType": "ETF"}, {"model_family": "operating"}, "operating")["family"] == "fund"


def test_explicit_financial_hybrid_and_operating_overrides():
    info = {"quoteType": "EQUITY", "industry": "Credit Services", "sector": "Financial Services"}
    assert profile(info, {}, "operating")["family"] == "operating"
    assert profile(info, {}, "financial")["family"] == "hybrid"
    assert profile(info, {}, "operating")["classification_source"] == "explicit classification override"


def test_diversified_metadata_does_not_manufacture_gics_or_weighted_multiples():
    supplied = {"segments": [{"name": "bank", "source": "segment note", "revenue": 10},
                              {"name": "industrial", "source": "segment note", "revenue": 90}]}
    result = profile({"quoteType": "EQUITY", "industry": "Conglomerates"}, supplied)
    assert result["diversified"]
    assert result["segments"] == supplied["segments"]
    assert result["gics"] is None
    assert "multiple" not in result


def test_model_parameter_rate_conflict_is_rejected():
    p = parts(); p["components"][0]["parameters"]["discount_rate"] = .2
    assert any("conflicting discount_rate" in e for e in validate_sum_of_parts(p))


def test_partial_gics_provenance_identifies_provider_label_fallback():
    result = profile({"quoteType": "EQUITY", "industry": "Banks - Regional"},
                     {"gics": {"sector": "Financials", "source": "licensed export", "as_of": "2026-10-11"}})
    assert result["routing_inputs"]["sector"]["source"] == "licensed export"
    assert result["routing_inputs"]["industry"]["source"] == "provider info"


def test_specific_property_family_survives_broad_financial_override():
    assert profile({"quoteType": "EQUITY", "industry": "REIT - Industrial"}, {}, "financial")["family"] == "reit"


def test_explicit_claims_inside_common_nav_require_inventory():
    p = parts(); p["components"][0]["parameters"]["parent_debt"] = 10.
    assert any("require included_claim_ids" in e for e in validate_sum_of_parts(p))


def test_malformed_family_does_not_crash_general_profile():
    result = profile({"quoteType": "EQUITY"}, {"model_family": {"unknown": True}})
    assert result["family"] == "operating"
    assert any("Unrecognized" in message for message in result["uncertainties"])
