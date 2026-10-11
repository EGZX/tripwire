"""Economic routing and ownership-consistent sum-of-parts valuation.

GICS is source-provided classification metadata, not a valuation algorithm.
Its principal-business classification does not replace segment economics:
https://www.spglobal.com/spdji/en/documents/methodologies/methodology-gics.pdf
No proprietary GICS code map or company-specific routing is embedded here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import math
from numbers import Real

from . import valuation as V


FAMILIES = {"operating", "cyclical", "bank", "insurer", "reit", "mortgage_reit",
            "regulated", "financial_other", "hybrid", "holdco", "fund"}
FINANCIAL_FAMILIES = {"bank", "insurer", "mortgage_reit", "financial_other", "hybrid"}
CYCLICAL_INDUSTRIES = {"semiconductors", "semiconductor equipment & materials", "auto manufacturers",
    "farm & heavy construction machinery", "airlines", "marine shipping", "steel", "auto parts",
    "trucking", "railroads", "building materials", "residential construction", "lumber & wood production",
    "agricultural inputs", "recreational vehicles"}
GICS_KEYS = {"source", "as_of", "code", "sector", "industry_group", "industry", "sub_industry"}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _classification(family):
    return "financial" if family in FINANCIAL_FAMILIES else family if family in ("fund", "holdco", "cyclical") else "operating"


def _label_family(sector, industry):
    """Small economic keyword rules, independent of a proprietary taxonomy map."""
    sector, industry = sector.casefold(), industry.casefold()
    if "mortgage" in industry and ("reit" in industry or "real estate investment trust" in industry):
        return "mortgage_reit"
    if "reit" in industry or "real estate investment trust" in industry:
        return "reit"
    if any(label in industry for label in ("insurance broker", "payment processing", "financial exchanges", "financial data", "stock exchanges")):
        return "operating"  # Fee businesses are not automatically deposit/insurance capital models.
    if industry.startswith("banks") or industry in {"bank", "regional banks", "diversified banks", "thrifts & mortgage finance"}:
        return "bank"
    if "insurance" in industry or "reinsurance" in industry:
        return "insurer"
    if "multi-sector holdings" in industry or "holding companies" in industry or "shell companies" in industry:
        return "holdco"
    if "conglomerate" in industry:
        return "operating"  # consolidated industrial earnings; not a balance-sheet (bank-style) business
    if "independent power" in industry or "renewable" in industry:
        return "cyclical"   # merchant power prices, not an allowed return on a rate base
    if sector == "utilities" or "regulated" in industry:
        return "regulated"
    if industry.startswith(("asset management", "capital markets", "mortgage finance", "credit services")) or sector in {"financial services", "financials"}:
        return "financial_other"
    if sector in {"basic materials", "materials", "energy"} or industry in CYCLICAL_INDUSTRIES:
        return "cyclical"
    return "operating"


def profile(info: dict, supplied: dict | None, class_override=None) -> dict:
    """Return economic routing with provider labels and GICS provenance separated.

    ``supplied.gics`` (or explicit ``info.gics``) must carry source and as_of to
    be used. This means source-provided, not independently authenticated here.
    Unknown labels select a general operating route with uncertainty; they are
    not evidence that valuation is impossible. Segment data is never converted
    into revenue-weighted multiples or a guessed GICS classification.
    """
    supplied = supplied or {}
    sector, industry = _text(info.get("sector")), _text(info.get("industry"))
    provider = {"sector": sector, "industry": industry, "source": "provider info",
                "taxonomy": "provider labels; not verified GICS"}
    uncertainties = []
    gics_raw = supplied.get("gics", info.get("gics"))
    gics = None
    if isinstance(gics_raw, dict):
        gics = {k: deepcopy(v) for k, v in gics_raw.items() if k in GICS_KEYS}
        valid = bool(_text(gics.get("source")))
        try:
            gics["as_of"] = date.fromisoformat(str(gics["as_of"])).isoformat()
        except (KeyError, ValueError):
            valid = False
        gics["status"] = "source_provided" if valid else "unverified_metadata"
        gics["verification"] = "Source identity and classification have not been independently authenticated by this function."
        if not valid:
            uncertainties.append("GICS metadata lacks a dated source; provider labels remain the routing input.")
    elif gics_raw is not None:
        uncertainties.append("GICS metadata must be a source-labeled table; it was not used for routing.")
    segments = supplied.get("segments", info.get("segments", []))
    if not isinstance(segments, list):
        uncertainties.append("Segment metadata has an unsupported shape and was not interpreted.")
        segments = []
    segments = [deepcopy(x) for x in segments if isinstance(x, dict)]
    if any(not _text(segment.get("source")) for segment in segments):
        uncertainties.append("Some segment metadata lacks its own source and remains unverified.")
    component_sets = [supplied.get(case, {}).get("components", []) for case in ("base", "stress")
                      if isinstance(supplied.get(case), dict)]
    diversified = len(segments) > 1 or any(isinstance(x, list) and len(x) > 1 for x in component_sets)
    quote_type = _text(info.get("quoteType")).upper()
    family = "operating"
    declared_family = supplied.get("model_family")
    taxonomy, basis, source = "provider_labels", "provider industry", "provider info"
    rationale = []
    routing_inputs = {"sector": {"value": sector, "source": "provider info"},
                      "industry": {"value": industry, "source": "provider info"}}
    if "model_family" in supplied and (not isinstance(declared_family, str) or declared_family not in FAMILIES):
        uncertainties.append("Unrecognized economic family declaration; labels/general economics remain available.")
    # Security eligibility precedes economic overrides: an ETF is not a company.
    if quote_type and quote_type != "EQUITY":
        family = "fund"
        rationale.append("Provider security type is not an individual equity.")
    elif class_override == "fund":
        family = "fund"; basis = "explicit override"; taxonomy = "economic_override"
        source = "explicit classification override"
        rationale.append("Explicit exclusion from individual-company valuation.")
    elif isinstance(declared_family, str) and declared_family in FAMILIES:
        family = declared_family
        basis, source, taxonomy = "sourced family declaration", supplied.get("source", "supplied assumptions"), "economic_override"
        rationale.append("Explicit economic-model family takes precedence over classification labels.")
    elif class_override in {"operating", "cyclical", "holdco"}:
        family = class_override
        basis, taxonomy, source = "explicit override", "economic_override", "explicit classification override"
        rationale.append("Existing explicit classification override selects the economic family.")
    else:
        routing_sector, routing_industry = sector, industry
        if gics and gics["status"] == "source_provided":
            routing_sector = _text(gics.get("sector")) or sector
            routing_industry = (_text(gics.get("sub_industry")) or _text(gics.get("industry")) or industry)
            taxonomy, basis, source = "source_provided_gics", "source-provided GICS routing suggestion", gics["source"]
            if _text(gics.get("sector")):
                routing_inputs["sector"] = {"value": routing_sector, "source": gics["source"]}
            if _text(gics.get("sub_industry")) or _text(gics.get("industry")):
                routing_inputs["industry"] = {"value": routing_industry, "source": gics["source"]}
        family = _label_family(routing_sector, routing_industry)
        if class_override == "financial":
            if family not in {"bank", "insurer", "reit", "mortgage_reit"}:
                family = "hybrid"
            basis, taxonomy, source = "explicit override", "economic_override", "explicit classification override"
        if supplied.get("model") == "sum_of_parts" and family not in {"holdco", "fund"}:
            family = "hybrid"; diversified = True
            rationale.append("Explicit component valuation represents multiple economic businesses.")
        if not routing_sector and not routing_industry:
            uncertainties.append("Business labels unavailable: general operating/common-equity estimates need stronger applicability evidence.")
            rationale.append("No classification evidence; use general economic relationships with uncertainty.")
        else:
            rationale.append("Business labels suggest a family; balance-sheet and cashflow economics determine the actual model.")
    if family == "regulated":
        uncertainties.append("Utility/regulated classification does not establish an allowed return or a regulatory rate base.")
    if family == "hybrid" or diversified:
        diversified = True
        uncertainties.append("Diversified activities require segment evidence for a sum of parts; classification alone cannot value the segments.")
    if not quote_type:
        uncertainties.append("Security type was not supplied; individual-equity status is assumed for this profile.")
    return {"family": family, "class": _classification(family), "classification_basis": basis,
            "classification_source": source, "taxonomy": taxonomy, "provider_labels": provider,
            "gics": gics, "diversified": diversified, "segments": segments,
            "routing_inputs": routing_inputs, "routing_rationale": rationale, "uncertainties": uncertainties}


SOTP_KEYS = {"source", "currency", "components", "parent_bridge", "as_of"}
COMPONENT_KEYS = {"id", "name", "source", "model", "parameters", "ownership", "currency", "fx_to_parent",
                  "discount_rate", "discount_rate_basis", "terminal_growth", "claim", "firm_bridge",
                  "included_claim_ids", "as_of", "recourse"}
FIRM_BRIDGE_FIELDS = ("debt", "cash", "preferred", "minority", "other_claims")
PARENT_BRIDGE_FIELDS = ("debt", "cash", "other_claims", "corporate_cost_pv")
BRIDGE_METADATA = {"source", "scope", "claim_ids"}


@dataclass(frozen=True)
class SOTPResult:
    value: float
    components: tuple[dict, ...]
    parent_bridge: dict
    currency: str
    claim: str = "equity"
    model: str = "sum_of_parts"


def _number(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _currency(value):
    return isinstance(value, str) and len(value) == 3 and value.isalpha() and value.isupper()


def _claim_ids(value, location, errors, seen):
    if not isinstance(value, list) or not all(_text(x) for x in value):
        errors.append(f"{location}: claim IDs must be a list of nonempty strings")
        return
    for identity in value:
        identity = identity.strip()
        key = identity.casefold()  # "P:Debt" and "p:debt" are the same claim
        if key in seen:
            errors.append(f"{location}: duplicate economic claim {identity}; already applied at {seen[key]}")
        else:
            seen[key] = location


def _validate_bridge(bridge, fields, scope, location, errors, seen):
    if not isinstance(bridge, dict):
        errors.append(f"{location}: explicit bridge is required")
        return
    for key in bridge.keys() - set(fields) - BRIDGE_METADATA:
        errors.append(f"{location}: unknown bridge field {key}")
    if not _text(bridge.get("source")):
        errors.append(f"{location}: source required")
    if bridge.get("scope") != scope:
        errors.append(f"{location}: scope must be {scope}; consolidated debt must not be deducted twice")
    ids = bridge.get("claim_ids", {})
    if not isinstance(ids, dict):
        errors.append(f"{location}: claim_ids must map bridge fields to IDs")
        ids = {}
    for key in ids.keys() - set(fields):
        errors.append(f"{location}: unknown claim ID field {key}")
    for field in fields:
        value = bridge.get(field)
        if not _number(value) or value < 0:
            errors.append(f"{location}.{field}: explicit finite nonnegative amount required")
        if _number(value) and value > 0 and not ids.get(field):
            errors.append(f"{location}.{field}: nonzero bridge amount requires economic claim IDs")
        if field in ids:
            _claim_ids(ids[field], location + "." + field, errors, seen)


def validate_sum_of_parts(params: dict) -> list[str]:
    """Validate nested claim/FX/source contracts without evaluating any models.

    Claim IDs detect explicit reuse. Source documentation must still establish
    the factual allocation of parent versus component claims and costs.
    """
    if not isinstance(params, dict):
        return ["sum_of_parts: parameters must be a table"]
    errors, seen = [], {}
    for key in params.keys() - SOTP_KEYS:
        errors.append(f"sum_of_parts: unknown parameter {key}")
    if not _text(params.get("source")):
        errors.append("sum_of_parts: source required")
    if not _currency(params.get("currency")):
        errors.append("sum_of_parts: parent currency must be a three-letter uppercase code")
    components = params.get("components")
    if not isinstance(components, list) or not components:
        return errors + ["sum_of_parts: nonempty components required"]
    ids = set()
    for i, component in enumerate(components):
        loc = f"components[{i}]"
        if not isinstance(component, dict):
            errors.append(f"{loc}: must be a table"); continue
        for key in component.keys() - COMPONENT_KEYS:
            errors.append(f"{loc}: unknown field {key}")
        identity = _text(component.get("id"))
        if not identity or identity in ids:
            errors.append(f"{loc}: component ID must be nonempty and unique")
        ids.add(identity)
        for field in ("source", "model"):
            if not _text(component.get(field)):
                errors.append(f"{loc}: {field} required")
        if component.get("model") == "sum_of_parts":
            errors.append(f"{loc}: flatten nested components so ownership and parent claims have one auditable perimeter")
        parameters = component.get("parameters")
        if not isinstance(parameters, dict):
            errors.append(f"{loc}: model parameters must be a table"); parameters = {}
        ownership, fx, rate = (component.get(k) for k in ("ownership", "fx_to_parent", "discount_rate"))
        if not _number(ownership) or not 0 <= ownership <= 1:
            errors.append(f"{loc}: ownership must be a fraction in [0,1]")
        if not _currency(component.get("currency")):
            errors.append(f"{loc}: component currency must be a three-letter uppercase code")
        if not _number(fx) or fx <= 0:
            errors.append(f"{loc}: positive fx_to_parent required (parent units per component unit)")
        if component.get("currency") == params.get("currency") and fx != 1:
            errors.append(f"{loc}: same-currency FX must equal one")
        if "terminal_growth" not in component:
            # Each component's perpetuity is its own assumption; a parent/screener setting must not reprice it.
            errors.append(f"{loc}: component requires its own nominal terminal_growth")
        if not _number(rate) or rate <= -1:
            errors.append(f"{loc}: explicit discount_rate above -100% required")
        if "terminal_growth" in component and (not _number(component["terminal_growth"]) or component["terminal_growth"] <= -1):
            errors.append(f"{loc}: terminal_growth must exceed -100%")
        for key in ("discount_rate", "terminal_growth"):
            if key in component and key in parameters and component[key] != parameters[key]:
                errors.append(f"{loc}: conflicting {key} in component and model parameters")
        claim, basis = component.get("claim"), component.get("discount_rate_basis")
        allowed = {"firm": {"wacc", "unlevered", "policy"}, "equity": {"cost_of_equity", "policy"}}
        if claim not in allowed or basis not in allowed.get(claim, set()):
            errors.append(f"{loc}: discount-rate basis must match declared firm/equity claim")
        if claim == "firm":
            _validate_bridge(component.get("firm_bridge"), FIRM_BRIDGE_FIELDS, "component_only", loc + ".firm_bridge", errors, seen)
        elif "firm_bridge" in component:
            errors.append(f"{loc}: equity value already reflects component financing; firm_bridge would deduct claims again")
        if "included_claim_ids" in component:
            _claim_ids(component["included_claim_ids"], loc + ".included_claim_ids", errors, seen)
        internal_claims = ("debt", "parent_debt", "other_claims", "holding_cost_pv", "corporate_cost_pv")
        if claim == "equity" and any(_number(parameters.get(k)) and parameters[k] != 0 for k in internal_claims) and not component.get("included_claim_ids"):
            errors.append(f"{loc}: explicit claims already deducted inside the equity model require included_claim_ids")
        if component.get("recourse") not in (None, "limited_liability", "pro_rata_support"):
            errors.append(f"{loc}: recourse must be limited_liability or pro_rata_support")
    _validate_bridge(params.get("parent_bridge"), PARENT_BRIDGE_FIELDS, "parent_only", "parent_bridge", errors, seen)
    return errors


def _bridge_effect(bridge, fields):
    return bridge["cash"] - math.fsum(bridge[k] for k in fields if k != "cash")


def sum_of_parts(params: dict, years: int, rate: float, gt: float, evaluate) -> SOTPResult:
    """Sum attributable component common-equity values, then parent claims once.

    Each evaluator returns a value with a firm/equity claim (Projection or dict).
    Scalar outputs are accepted only as declared equity values, e.g. net NAV.
    Component bridges use component currency and apply BEFORE ownership and FX;
    parent bridges use parent currency. Corporate costs are supplied as a PV.
    Negative component equity needs explicit recourse treatment, never a hidden
    zero floor or an unsupported capital-support obligation.
    """
    errors = validate_sum_of_parts(params)
    if errors:
        raise V.InvalidModel("; ".join(errors))
    V.horizon(years); V.finite(rate, gt)
    if rate <= -1 or gt <= -1:
        raise V.InvalidModel("parent discount/growth domain must exceed -100%")
    ledger = []
    for component in params["components"]:
        local_gt = component.get("terminal_growth", gt)
        result = evaluate(component["model"], deepcopy(component["parameters"]), years,
                          component["discount_rate"], local_gt)
        if isinstance(result, dict):
            local_value, claim = result.get("value"), result.get("claim")
        elif isinstance(result, Real) and not isinstance(result, bool):
            local_value, claim = result, "equity"
        else:
            local_value, claim = getattr(result, "value", None), getattr(result, "claim", None)
        if not _number(local_value) or claim != component["claim"]:
            raise V.InvalidModel(f"{component['id']}: evaluator returned invalid value or a different economic claim")
        bridge = _bridge_effect(component["firm_bridge"], FIRM_BRIDGE_FIELDS) if claim == "firm" else 0.0
        common = local_value + bridge
        if common < 0:
            if component.get("recourse") == "limited_liability":
                attributable_base = 0.0
            elif component.get("recourse") == "pro_rata_support":
                attributable_base = common
            else:
                raise V.InvalidModel(f"{component['id']}: negative common equity needs explicit recourse/capital-support treatment")
        else:
            attributable_base = common
        attributable = attributable_base * component["ownership"] * component["fx_to_parent"]
        V.finite(common, attributable)
        ledger.append({"id": component["id"], "name": component.get("name", component["id"]),
                       "model": component["model"], "source": component["source"], "claim": claim,
                       "component_currency": component["currency"], "model_value": float(local_value),
                       "component_bridge_effect": bridge, "common_equity_before_ownership": common,
                       "recourse": component.get("recourse"), "ownership": component["ownership"],
                       "fx_to_parent": component["fx_to_parent"], "value_in_parent_currency": attributable,
                       "discount_rate": component["discount_rate"], "discount_rate_basis": component["discount_rate_basis"],
                       "terminal_growth": local_gt, "firm_bridge": deepcopy(component.get("firm_bridge")),
                       "included_claim_ids": deepcopy(component.get("included_claim_ids", []))})
    subtotal = math.fsum(c["value_in_parent_currency"] for c in ledger)
    effect = _bridge_effect(params["parent_bridge"], PARENT_BRIDGE_FIELDS)
    value = subtotal + effect
    V.finite(subtotal, effect, value)
    parent = {**deepcopy(params["parent_bridge"]), "components_subtotal": subtotal,
              "effect": effect, "currency": params["currency"], "application_count": 1}
    return SOTPResult(value, tuple(ledger), parent, params["currency"])
