import math

import pandas as pd
import pytest

from screener import scoring, universe
from screener import valuation as V
from screener.fetch import major_currency
from screener.resolve import base_symbol


# ---------------- valuation
def test_zero_growth_perpetuity_matches_closed_form():
    # constant cash flow, g = 0 throughout -> value = cf / r
    assert V.present_value(10, 0.0, 0.10, 0.0, 10) == pytest.approx(100.0)


# ---------------- universe parsing / matching
WATCHLIST = """symbol,name,isin,yahoo_symbol,lists
RHM,Rheinmetall AG,,,Core; Defence
700,Tencent Holdings Limited,,0700.HK,Core
,Siemens AG,DE0007236101,,
RHM,Rheinmetall AG,,,Momentum
,,,,
"""
HOLDINGS = """isin,symbol,name,shares,value_eur,cost_eur
US0378331005,,Apple Inc.,10,"1,850.00",1200
DE0007236101,,Siemens AG,5,1000,
"""


def test_load_watchlist_merges_lists_and_keeps_given_symbols(tmp_path):
    f = tmp_path / "watchlist.csv"
    f.write_text(WATCHLIST, encoding="utf-8")
    entries = {e.key: e for e in universe.load_watchlist(f)}
    assert set(entries) == {"RHM", "700", "DE0007236101"}             # blank row skipped, duplicate merged
    assert entries["RHM"].lists == {"Core", "Defence", "Momentum"}
    assert entries["700"].yahoo == "0700.HK" and entries["700"].ticker == "700"
    assert entries["DE0007236101"].lists == {"Watchlist"} and entries["DE0007236101"].isin == "DE0007236101"
    assert universe.load_watchlist(tmp_path / "missing.csv") == []


def test_load_holdings_parses_numbers(tmp_path):
    f = tmp_path / "holdings.csv"
    f.write_text(HOLDINGS, encoding="utf-8")
    h = universe.load_holdings(f)
    assert [e.key for e in h] == ["US0378331005", "DE0007236101"] and all(e.owned for e in h)
    assert h[0].position_eur == 1850.0 and h[0].cost_eur == 1200.0 and h[1].cost_eur == 0.0


@pytest.mark.parametrize("a,b,expected", [
    ("Siemens", "Siemens Aktiengesellschaft", True),
    ("Unilever PLC", "Unilever", True),
    ("Procter & Gamble", "The Procter and Gamble Company", True),
    ("Toyota Motor ADR", "Toyota Motor Corporation", True),
    ("BMW", "Bayerische Motoren Werke AG", False),        # abbreviations need a symbol or ISIN
    ("Ford Motor Company", "Toyota Motor Corporation", False),
    ("Apple Inc.", "Applied Materials Inc.", False),
])
def test_names_match(a, b, expected):
    assert universe.names_match(a, b) is expected


def test_merge_attaches_holdings_by_isin_symbol_or_name():
    w = [universe.Entry(key="AAPL", name="Apple Inc.", ticker="AAPL", lists={"Core"}),
         universe.Entry(key="SIE", name="Siemens", ticker="SIE", isin="DE0007236101", lists={"Core"}),
         universe.Entry(key="NOVO", name="Novo Nordisk", ticker="NOVO", lists={"Core"})]
    h = [universe.Entry(key="US0378331005", name="Apple", isin="US0378331005", owned=True, position_eur=1000.0,
                        lists={"Holdings"}),                                              # by name
         universe.Entry(key="DE0007236101", name="Siemens AG (reg.)", isin="DE0007236101", owned=True,
                        lists={"Holdings"}),                                              # by ISIN
         universe.Entry(key="NOVO", name="Novo B", ticker="NOVO", owned=True, lists={"Holdings"}),   # by symbol
         universe.Entry(key="JP3633400001", name="Unmatched Co", isin="JP3633400001", owned=True, lists={"Holdings"})]
    merged = universe.merge(w, h)
    assert len(merged) == 4 and all(e.owned for e in merged)
    assert merged[0].position_eur == 1000.0 and merged[0].isin == "US0378331005"
    assert merged[0].lists == {"Core", "Holdings"}
    merged[0].yahoo, merged[3].yahoo = "AAPL", "AAPL"
    assert len(universe.dedupe_by_symbol(merged)) == 3


def test_base_symbol_and_minor_currency():
    assert base_symbol("0700.HK") == base_symbol("700") == "700"
    assert base_symbol("RHM.DE") == "RHM"
    assert major_currency("GBp") == ("GBP", 100.0)
    assert major_currency("USD") == ("USD", 1.0)


# ---------------- scoring (context percentiles; the candidate gate is tested in test_scoring.py)
def test_context_percentiles_rank_cheaper_better_quality_higher():
    cfg = {"scoring": {"w_value": 0.4, "w_quality": 0.3, "w_growth": 0.2, "w_balance": 0.1}}
    df = pd.DataFrame({
        "symbol": ["CHEAP", "MID", "DEAR"], "class": ["operating"] * 3, "flags": ["", "", ""], "not_rankable": ["", "", ""],
        "entry_score": [15.0, 0.0, -20.0],
        "nopat_yield": [0.09, 0.06, 0.02], "fcf_yield": [0.08, 0.05, 0.01], "ebit_yield": [0.10, 0.06, 0.02],
        "roic": [0.3, 0.2, 0.1], "gross_profitability": [0.5, 0.4, 0.3],
        "margin_stability": [0.01, 0.03, 0.08], "cash_conversion": [1.1, 1.0, 0.6],
        "rev_cagr_3y": [0.10, 0.08, 0.05], "analyst_growth": [0.10, 0.07, 0.05],
        "net_debt_ebitda": [0.0, 1.0, 3.0], "share_cagr": [-0.02, 0.0, 0.03],
    })
    out = scoring.score(df, cfg).set_index("symbol")
    assert out.loc["CHEAP", "composite"] > out.loc["MID", "composite"] > out.loc["DEAR", "composite"]
    assert out["candidate"].tolist() == [True, True, False]   # score >= 0 is eligible, whatever the peers look like


def test_symbol_overrides_merge_a_local_file_that_wins(tmp_path):
    from screener.resolve import load_overrides
    shared = tmp_path / "symbols.csv"
    shared.write_text("source_key,yahoo_symbol,note\nAIR,AIR.PA,Airbus\nNOVO,NOVO-B.CO,Novo Nordisk\n", encoding="utf-8")
    assert load_overrides(shared) == {"AIR": "AIR.PA", "NOVO": "NOVO-B.CO"}   # no local file: shared only
    (tmp_path / "symbols.local.csv").write_text("source_key,yahoo_symbol,note\nDK0062498333,NOVO-B.CO,local\nAIR,SKIP,\n",
                                                encoding="utf-8")
    assert load_overrides(shared) == {"AIR": "SKIP", "NOVO": "NOVO-B.CO", "DK0062498333": "NOVO-B.CO"}
