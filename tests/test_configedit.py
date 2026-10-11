"""In-place, validated, backed-up edits of config.toml from the dashboard."""
import tomllib
from pathlib import Path

import pytest

from dashboard import configedit as CE

SAMPLE = '''# Screener configuration.

[inputs]
watchlist = "input/watchlist.csv"   # keep

[valuation]
equity_risk_premium = 0.045    # hurdle = currency risk-free + this
adverse_return_spread = 0.025  # half the ERP
min_hurdle = 0.07
base_currency = "EUR"
adverse_growth_mode = "half"   # or "haircut"
adverse_growth_haircut = 0.05
years = 10                     # structural, not editable
growth_cap = 0.30
growth_floor = -0.05
return_cap = 0.40
return_smoothing = true
stale_after_days = 270

# rates comment stays
[rates]
USD = { rf = 0.0524, g = 0.030, as_of = "2026-10-10" }
EUR = { rf = 0.027, g = 0.020, as_of = "2026-10-10" }   # trailing comment
KZT = { rf = 0.140, g = 0.060, as_of = "2026-10-10" }

[scoring]
w_value = 0.40
'''


def test_plan_edits_values_in_place_and_keeps_everything_else():
    p = CE.plan(SAMPLE, {"equity_risk_premium": 0.05, "stale_after_days": 300, "base_currency": "USD"}, {}, {"USD", "EUR"})
    assert p.errors == []
    cfg = tomllib.loads(p.text)
    assert cfg["valuation"]["equity_risk_premium"] == 0.05
    assert cfg["valuation"]["stale_after_days"] == 300
    assert cfg["valuation"]["base_currency"] == "USD"
    assert "# hurdle = currency risk-free + this" in p.text          # comments survive
    assert "# rates comment stays" in p.text and 'watchlist = "input/watchlist.csv"   # keep' in p.text
    assert cfg["valuation"]["years"] == 10 and cfg["scoring"]["w_value"] == 0.40
    assert {(c["section"], c["key"]) for c in p.changes} == {("valuation", "equity_risk_premium"), ("valuation", "stale_after_days"), ("valuation", "base_currency")}
    assert any(c["old"] == 0.045 and c["new"] == 0.05 for c in p.changes)


def test_plan_updates_adds_and_removes_rates():
    p = CE.plan(SAMPLE, {}, {"USD": {"rf": 0.048, "g": 0.03, "as_of": "2026-11-01"},
                            "JPY": {"rf": 0.016, "g": 0.01, "crp": 0.0, "as_of": "2026-11-01"},
                            "KZT": None}, {"USD", "EUR", "JPY"})
    assert p.errors == []
    cfg = tomllib.loads(p.text)
    assert cfg["rates"]["USD"] == {"rf": 0.048, "g": 0.03, "as_of": "2026-11-01"}
    assert cfg["rates"]["JPY"]["rf"] == 0.016 and "KZT" not in cfg["rates"]
    assert 'EUR = { rf = 0.027, g = 0.020, as_of = "2026-10-10" }   # trailing comment' in p.text  # untouched line
    kinds = {(c["section"], c["key"], c["kind"]) for c in p.changes}
    assert ("rates", "USD", "update") in kinds and ("rates", "JPY", "add") in kinds and ("rates", "KZT", "remove") in kinds


def test_plan_rejects_bad_values_without_producing_text():
    p = CE.plan(SAMPLE, {"equity_risk_premium": 0.9, "years": 5, "min_hurdle": "seven"}, {}, {"USD"})
    msgs = " | ".join(p.errors)
    assert "equity_risk_premium" in msgs and "years" in msgs and "min_hurdle" in msgs
    p = CE.plan(SAMPLE, {}, {"usd": {"rf": 0.05, "g": 0.03}, "GBP": {"rf": 0.05, "g": 0.03, "as_of": "yesterday"}}, set())
    msgs = " | ".join(p.errors)
    assert "usd" in msgs and "as_of" in msgs
    p = CE.plan(SAMPLE, {}, {"EUR": None}, {"USD"})          # base currency cannot be removed
    assert any("base_currency" in e for e in p.errors)
    p = CE.plan(SAMPLE, {}, {"USD": None}, {"USD"})          # a currency the universe reports in cannot be removed
    assert any("USD" in e and "reporting" in e for e in p.errors)
    p = CE.plan(SAMPLE, {}, {"EUR": {"rf": 0.01, "g": 0.15, "as_of": "2026-10-10"}}, {"EUR"})   # g in range but above the hurdle
    assert any("terminal growth" in e for e in p.errors)


def test_save_backs_up_and_writes_atomically(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(SAMPLE, encoding="utf-8")
    hist = tmp_path / "data" / "config-history"
    p = CE.plan(SAMPLE, {"min_hurdle": 0.08}, {}, {"USD"})
    res = CE.save(cfg, p, hist)
    assert tomllib.loads(cfg.read_text(encoding="utf-8"))["valuation"]["min_hurdle"] == 0.08
    assert Path(res["backup"]).read_text(encoding="utf-8") == SAMPLE
    copies = list(hist.glob("config_*.toml"))
    assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == SAMPLE
    assert not list(tmp_path.glob("*.tmp"))


def test_save_refuses_a_plan_with_errors(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(SAMPLE, encoding="utf-8")
    p = CE.plan(SAMPLE, {"return_cap": 5.0}, {}, {"USD"})
    with pytest.raises(ValueError):
        CE.save(cfg, p, tmp_path / "h")
    assert cfg.read_text(encoding="utf-8") == SAMPLE


def test_rate_age_in_days():
    assert CE.age_days("2026-10-01", "2026-10-10") == 9
    assert CE.age_days(None, "2026-10-10") is None


def test_save_keeps_line_endings_and_backups_are_byte_exact(tmp_path):
    from dashboard import configedit
    for nl in ("\n", "\r\n"):
        path = tmp_path / f"config_{len(nl)}.toml"
        original = nl.join(["[valuation]", "equity_risk_premium = 0.045  # note", "[rates]", "USD = { rf = 0.05, g = 0.03 }", ""]).encode()
        path.write_bytes(original)
        p = configedit.plan(original.decode(), {"equity_risk_premium": 0.05}, {}, set())
        res = configedit.save(path, p, tmp_path / f"hist_{len(nl)}")
        assert Path(res["backup"]).read_bytes() == original and Path(res["history"]).read_bytes() == original
        written = path.read_bytes()
        assert written.count(nl.encode()) == original.count(nl.encode()) and b"0.05  # note" in written
        if nl == "\n":
            assert b"\r" not in written
