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
KOYFIN = """Ticker,Name,Market Cap,Last Price
Not owned
RHM,Rheinmetall AG,N/A,1.0
700,Tencent Holdings Limited,N/A,1.0
Weighted Average  ,-
Owned
ADBE,Adobe Inc.,N/A,1.0
Median  ,-
"""


def test_load_koyfin_groups(tmp_path):
    f = tmp_path / "k.csv"
    f.write_text(KOYFIN, encoding="utf-8")
    entries = {e.ticker: e for e in universe.load_koyfin(str(tmp_path / "*.csv"))}
    assert set(entries) == {"RHM", "700", "ADBE"}
    assert entries["ADBE"].lists == {"Koyfin: Owned"}
    assert entries["RHM"].lists == {"Koyfin: Not owned"}


@pytest.mark.parametrize("a,b,expected", [
    ("Adobe Systems", "Adobe Inc.", True),
    ("Kaspi.kz", "Joint Stock Company Kaspi.kz", True),
    ("NAC Kazatomprom GDR", "National Atomic Company Kazatomprom JSC", True),
    ("Eli Lilly & Co", "Eli Lilly and Company", True),
    ("TSMC (ADR)", "Taiwan Semiconductor Manufacturing Company Limited", False),
    ("S&P Global", "Moody's Corporation", False),
    ("Meta Platforms", "Microsoft Corporation", False),
])
def test_names_match(a, b, expected):
    assert universe.names_match(a, b) is expected


def test_merge_attaches_position_and_dedupe():
    k = [universe.Entry(key="AAPL", name="Apple Inc.", ticker="AAPL", lists={"Koyfin: Owned"})]
    p = [universe.Entry(key="US0378331005", name="Apple", isin="US0378331005", owned=True,
                        position_eur=1000.0, lists={"Parqet"}),
         universe.Entry(key="DE0007236101", name="Siemens AG", isin="DE0007236101", owned=True, lists={"Parqet"})]
    merged = universe.merge(k, p, [])
    assert len(merged) == 2 and merged[0].owned and merged[0].position_eur == 1000.0
    merged[0].yahoo, merged[1].yahoo = "AAPL", "AAPL"
    assert len(universe.dedupe_by_symbol(merged)) == 1


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
