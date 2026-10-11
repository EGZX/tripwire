"""Point-in-time rules of the research tools: no fact filed after the cohort date may enter a forecast."""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import backtest_fundamentals as B  # noqa: E402


def fact(end, val, filed, start=None, form="10-K"):
    f = {"end": end, "val": val, "filed": filed, "form": form}
    if start:
        f["start"] = start
    return f


PAYLOAD = {"facts": {
    "Revenues": [fact("2013-12-31", 100, "2014-02-20", "2013-01-01"),
                 fact("2013-12-31", 90, "2015-02-20", "2013-01-01"),       # restated a year later
                 fact("2014-12-31", 120, "2015-02-20", "2014-01-01"),
                 fact("2014-09-30", 30, "2014-10-30", "2014-07-01", "10-Q")],  # quarter: never an annual
    "SalesRevenueNet": [fact("2014-12-31", 999, "2015-02-20", "2014-01-01")],
    "StockholdersEquity": [fact("2013-12-31", 50, "2014-02-20")],
}}


def test_point_in_time_uses_only_facts_filed_by_the_cohort_date():
    known = B.annual(PAYLOAD, ["Revenues"], known=date(2014, 6, 30))
    assert known == {pd.Timestamp("2013-12-31"): 100.0}            # the later restatement and 2014 are unknown


def test_outcomes_are_the_figures_as_first_published():
    first = B.annual(PAYLOAD, ["Revenues"], first=True)
    assert first[pd.Timestamp("2013-12-31")] == 100.0
    assert B.annual(PAYLOAD, ["Revenues"])[pd.Timestamp("2013-12-31")] == 90.0   # latest view, for contrast


def test_quarters_and_instants_are_kept_apart():
    assert pd.Timestamp("2014-09-30") not in B.annual(PAYLOAD, ["Revenues"])
    assert B.annual(PAYLOAD, ["StockholdersEquity"], instant=True) == {pd.Timestamp("2013-12-31"): 50.0}
    assert B.annual(PAYLOAD, ["Revenues"], instant=True) == {}


def test_one_revenue_definition_per_company():
    order = B.preferred(PAYLOAD, B.REVENUE, known=date(2015, 6, 30))
    assert order[0] == "Revenues"                                      # first listed concept for the latest year
    assert B.annual(PAYLOAD, order)[pd.Timestamp("2014-12-31")] == 120.0
