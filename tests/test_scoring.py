"""Candidate status is absolute: it depends on a name's own price and ceiling, not on what else is in the run."""
import pandas as pd

from screener import scoring

CFG = {"scoring": {"w_value": 0.4, "w_quality": 0.3, "w_growth": 0.2, "w_balance": 0.1}}


def row(sym, score, flags="", not_rankable="", cls="operating"):
    return {"symbol": sym, "class": cls, "entry_score": score, "flags": flags, "not_rankable": not_rankable,
            "fcf_yield": 0.05, "roic": 0.2, "rev_cagr_3y": 0.1, "share_cagr": 0.0, "evs_vs_hist": float("nan")}


def test_candidate_status_does_not_depend_on_other_names():
    for n in (1, 2, 3):
        out = scoring.score(pd.DataFrame([row(f"X{i}", 12.0) for i in range(n)]), CFG)
        assert out["candidate"].tolist() == [True] * n
        assert out["status"].tolist() == ["eligible"] * n


def test_statuses():
    df = pd.DataFrame([row("A", 5.0), row("B", -3.0), row("C", float("nan"), not_rankable="no debt figure"),
                       row("D", 8.0, flags="stale financials"), row("E", float("nan"), cls="holdco")])
    out = scoring.score(df, CFG).set_index("symbol")
    assert out.loc["A", "status"] == "eligible" and bool(out.loc["A", "candidate"])
    assert out.loc["B", "status"] == "price above ceiling" and not bool(out.loc["B", "candidate"])
    assert out.loc["C", "status"] == "not rankable" and not bool(out.loc["C", "candidate"])
    assert out.loc["D", "status"] == "data flag" and not bool(out.loc["D", "candidate"])
    assert out.loc["E", "status"] == "not screened"


def test_context_percentiles_still_computed_without_crashing_on_missing_columns():
    out = scoring.score(pd.DataFrame([row("A", 1.0), row("B", 2.0)]).drop(columns=["evs_vs_hist"]), CFG)
    assert "score_quality" in out and out["score_quality"].notna().all()
