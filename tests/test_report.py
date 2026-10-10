"""Run files are never overwritten, changes compare against the last different day, big shifts are called out."""
from datetime import date, timedelta

import pandas as pd

from screener import report

CFG = {"valuation": {"equity_risk_premium": 0.045, "adverse_return_spread": 0.025, "adverse_growth_haircut": 0.05,
                     "years": 10, "growth_floor": -0.05, "growth_cap": 0.3, "return_cap": 0.4},
       "rates": {"USD": {"rf": 0.05, "g": 0.03}}}


def frame(status, score):
    return pd.DataFrame({"symbol": ["A", "B"], "name": ["A co", "B co"], "owned": [False, True], "class": ["operating"] * 2,
                         "status": [status] * 2, "candidate": [status == "eligible"] * 2, "entry_score": [score] * 2,
                         "price": [10.0, 20.0], "ceiling_px": [12.0, 15.0], "flags": ["", ""], "not_rankable": ["", ""]})


def test_history_file_is_never_overwritten(tmp_path):
    hist, out = tmp_path / "h", tmp_path / "r"
    report.write(frame("eligible", 5.0), pd.DataFrame(), out, hist, CFG)
    report.write(frame("eligible", 6.0), pd.DataFrame(), out, hist, CFG)
    assert len(list(hist.glob("screen_*.csv"))) == 2
    assert len(list(out.glob("screen_*.xlsx"))) == 2


def test_changes_compare_against_last_different_day_and_flag_large_shift(tmp_path):
    hist, out = tmp_path / "h", tmp_path / "r"
    hist.mkdir()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    frame("eligible", 5.0).to_csv(hist / f"screen_{yesterday}.csv", index=False)
    report.write(frame("eligible", 5.0), pd.DataFrame(), out, hist, CFG)        # a first run today
    _, md = report.write(frame("price above ceiling", -30.0), pd.DataFrame(), out, hist, CFG)
    text = md.read_text(encoding="utf-8")
    assert "dropped as candidate" in text
    assert "Large shift" in text                                                  # 100% of statuses flipped
    changes = report.changes_vs_previous(frame("price above ceiling", -30.0), hist, date.today().isoformat())
    assert (changes["previous_run"] == yesterday).all()
