from screener import checks

CFG = {"valuation": {"equity_risk_premium": 0.045, "adverse_return_spread": 0.025},
       "rates": {"USD": {"rf": 0.05, "g": 0.03}, "EUR": {"rf": 0.03, "g": 0.02}}}


def test_missing_currency_is_reported():
    errors = checks.validate(CFG, {"USD", "KZT", "JPY"})
    assert any("KZT" in e for e in errors) and any("JPY" in e for e in errors)
    assert checks.validate(CFG, {"USD", "EUR"}) == []


def test_malformed_rate_entries_are_reported():
    bad = {"valuation": CFG["valuation"], "rates": {"USD": {"rf": "five"}, "EUR": {"rf": 0.03, "g": 0.30}}}
    errors = checks.validate(bad, {"USD", "EUR"})
    assert any("USD" in e for e in errors)
    assert any("EUR" in e and "terminal growth" in e for e in errors)


def test_rate_as_of_must_be_a_date_when_present():
    cfg = {"valuation": CFG["valuation"],
           "rates": {"USD": {"rf": 0.05, "g": 0.03, "as_of": "2026-10-10"}, "EUR": {"rf": 0.03, "g": 0.02, "as_of": "last week"}}}
    errors = checks.validate(cfg, {"USD", "EUR"})
    assert any("EUR" in e and "as_of" in e for e in errors) and not any("USD" in e for e in errors)
