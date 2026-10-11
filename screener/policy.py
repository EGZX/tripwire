"""Model policy: every judgement threshold of the automatic models, in one place.

These are general rules: they apply to every issuer, including ones added later, and contain no ticker or
country exceptions. Each can be changed in config.toml under [model_policy] (unknown keys are rejected);
the run reports which values it used. Rates, the equity risk premium and terminal growth stay in [valuation]
and [rates]: they are investment policy, not model mechanics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace


@dataclass(frozen=True)
class Policy:
    # ---- operating model
    margin_limit: float = 1.0
    """Clean net margins above this share of revenue are one-offs on a tiny revenue base, not earning power.
    Losses beyond it are kept: they are real."""
    cyclical_normalisation: float = 0.5
    """Cyclicals start this far from the current margin towards their historical median (0 = current, 1 = median)."""
    growth_half_life: float = 2.0
    """Years after the two estimate years in which excess growth over terminal growth halves."""
    estimate_base_tolerance: float = 0.15
    """Revenue-growth estimates are used only if their base-year revenue matches reported revenue within this share."""
    min_reference_firms: int = 20
    """Industry references with fewer firms fall back to the broad nonfinancial reference."""
    # ---- financial (common-equity) model
    franchise_share: float = 0.5
    """Share of today's excess return over the hurdle a mature franchise keeps (base case)."""
    franchise_cap: float = 0.05
    """Maximum mature excess return over the hurdle (base case)."""
    upside_franchise_share: float = 0.75
    upside_franchise_cap: float = 0.10
    stress_return_factor: float = 0.75
    """Stress starts at most at this fraction of the base return on book."""
    stress_growth_factor: float = 0.5
    """Stress book growth as a fraction of base growth, when base growth exceeds terminal growth."""
    unknown_payout_retention: float = 0.5
    """Without payout history, starting book growth is at most this share of the base return (retention)."""
    # ---- asset recovery
    recovery_noncash_share: float = 0.5
    """Base case recovers cash at par and other assets at this share of carrying value."""
    # ---- data controls
    annual_tie_tolerance: float = 0.02
    """Four quarters ending at a fiscal year end must match the audited annual figure within this share."""
    ownership_income_materiality: float = 0.02
    """Common vs consolidated income gap that indicates a partial economic claim."""
    ownership_unit_materiality: float = 1.10
    """Aggregate units must exceed listed shares by this factor before ownership needs reconciliation."""
    fx_plausibility: float = 0.25
    """Declared component FX further than this from the market rate is treated as inverted or stale."""


_current = Policy()


def get() -> Policy:
    return _current


def configure(cfg: dict | None) -> Policy:
    """Apply [model_policy] from the configuration on top of the defaults."""
    global _current
    overrides = (cfg or {}).get("model_policy") or {}
    errors = validate(overrides)
    if errors:
        raise ValueError("; ".join(errors))
    _current = replace(Policy(), **overrides)
    return _current


def validate(overrides: dict) -> list[str]:
    known = {f.name: f.type for f in fields(Policy)}
    errors = []
    for key, value in overrides.items():
        if key not in known:
            errors.append(f"[model_policy] unknown setting {key}")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            errors.append(f"[model_policy] {key} must be a nonnegative number")
    return errors


def describe() -> dict:
    return asdict(_current)
