"""Golden-file regression: the model over a frozen set of real Yahoo records must reproduce the stored statuses and scores.

Any behaviour change has to be deliberate: review the diff, then regenerate with
    UPDATE_GOLDEN=1 .venv\\Scripts\\python -m pytest tests/test_golden.py
Fixtures live in tests/fixtures/cache (copied from data/cache on 2026-10-10); FX rates are frozen in the snapshot.
"""
import json
import os
import pickle
import tomllib
from pathlib import Path

import pandas as pd
import pytest

from screener import metrics, scoring
from screener.__main__ import load_class_overrides

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "cache"
GOLDEN = Path(__file__).parent / "golden" / "snapshot.json"
FROZEN_FX = {("USD", "EUR"): 0.86, ("TWD", "EUR"): 0.028, ("CHF", "EUR"): 1.07, ("JPY", "EUR"): 0.0057, ("KZT", "EUR"): 0.0016,
             ("USD", "TWD"): 30.6, ("USD", "KZT"): 540.0, ("USD", "CHF"): 0.80, ("CAD", "EUR"): 0.62}


class FrozenFX:
    def rate(self, frm, to):
        if frm == to:
            return 1.0
        if (frm, to) in FROZEN_FX:
            return FROZEN_FX[(frm, to)]
        if (to, frm) in FROZEN_FX:
            return 1.0 / FROZEN_FX[(to, frm)]
        raise KeyError(f"frozen FX has no {frm}->{to}; add it to FROZEN_FX")


def current_snapshot() -> dict:
    with open(ROOT / "config.toml", "rb") as fh:
        cfg = tomllib.load(fh)
    overrides = load_class_overrides(ROOT / cfg["inputs"]["classification_overrides"])
    rows = []
    for f in sorted(FIXTURES.glob("*.pkl")):
        with open(f, "rb") as fh:  # our own fixture files
            data = pickle.load(fh)
        m = metrics.compute(data, None, FrozenFX(), cfg, overrides.get(data["symbol"]))
        rows.append({"symbol": data["symbol"], "name": "", "owned": False, "position_eur": None, "lists": "", "isin": "", **m})
    out = scoring.score(pd.DataFrame(rows), cfg)
    snap = {}
    for r in out.itertuples():
        score = getattr(r, "entry_score", float("nan"))
        snap[r.symbol] = {"status": r.status, "entry_score": None if pd.isna(score) else round(float(score), 1),
                          "not_rankable": (getattr(r, "not_rankable", "") or "") if isinstance(getattr(r, "not_rankable", ""), str) else ""}
    return snap


@pytest.mark.skipif(not FIXTURES.exists() or not any(FIXTURES.glob("*.pkl")), reason="no fixtures")
def test_golden_snapshot():
    snap = current_snapshot()
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(snap, indent=1, sort_keys=True), encoding="utf-8")
    assert GOLDEN.exists(), "no golden snapshot: run with UPDATE_GOLDEN=1 after reviewing the model"
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(snap) == set(expected), f"fixture set changed: {set(snap) ^ set(expected)}"
    diffs = {s: (expected[s], snap[s]) for s in snap if snap[s] != expected[s]}
    assert not diffs, "behaviour changed; review and regenerate if intended:\n" + json.dumps(diffs, indent=1)
