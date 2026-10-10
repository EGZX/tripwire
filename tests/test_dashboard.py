import json
import math
import threading
import urllib.error
import urllib.request

import pandas as pd
import pytest

from dashboard.data import Store, derived_rates, records, summary, with_changes
from dashboard.server import serve


def _run(rows):
    base = {"name": "", "class": "operating", "owned": False, "position_eur": math.nan, "candidate": False,
            "status": "price above ceiling", "entry_score": math.nan, "price": math.nan, "ceiling_px": math.nan}
    return pd.DataFrame([{**base, **r} for r in rows])


@pytest.fixture
def root(tmp_path):
    hist = tmp_path / "data" / "history"
    hist.mkdir(parents=True)
    _run([
        {"symbol": "AAA", "entry_score": -10.0, "price": 110.0, "ceiling_px": 100.0},
        {"symbol": "BBB", "entry_score": 20.0, "price": 80.0, "ceiling_px": 100.0, "candidate": True, "status": "eligible"},
        {"symbol": "GONE", "entry_score": 5.0, "price": 95.0, "ceiling_px": 100.0, "candidate": True, "status": "eligible"},
    ]).to_csv(hist / "screen_2026-10-03.csv", index=False)
    _run([
        # price fell 20%, ceiling unchanged -> new candidate, driven by price
        {"symbol": "AAA", "entry_score": 12.0, "price": 88.0, "ceiling_px": 100.0, "candidate": True, "status": "eligible",
         "owned": True, "position_eur": 1000.0},
        # ceiling cut 30%, price flat -> dropped, driven by the ceiling
        {"symbol": "BBB", "entry_score": -14.3, "price": 80.0, "ceiling_px": 70.0, "owned": True, "position_eur": 3000.0},
        {"symbol": "NEW", "entry_score": math.nan, "status": "not rankable", "not_rankable": "no debt figure",
         "adverse_px": math.inf},
    ]).to_csv(hist / "screen_2026-10-10.csv", index=False)
    (hist / "notes.txt").write_text("ignored")
    (tmp_path / "config.toml").write_text(
        '[valuation]\nequity_risk_premium = 0.045\nadverse_return_spread = 0.0\n'
        '[rates]\nUSD = { rf = 0.05, g = 0.03 }\n', encoding="utf-8")
    return tmp_path


def test_run_dates_sorted_and_filtered(root):
    assert Store(root).run_dates() == ["2026-10-03", "2026-10-10"]


def test_changes_classify_events_and_driver(root):
    st = Store(root)
    m = with_changes(st.load("2026-10-10"), st.load("2026-10-03")).set_index("symbol")
    assert m.loc["AAA", "event"] == "new candidate" and m.loc["AAA", "reason"] == "price"
    assert m.loc["BBB", "event"] == "dropped as candidate" and m.loc["BBB", "reason"] == "ceiling"
    assert m.loc["NEW", "event"] == "new in universe"
    assert m.loc["AAA", "score_change"] == pytest.approx(22.0)
    assert m.loc["AAA", "price_change"] == pytest.approx(-0.2)


def test_payload_lists_removed_names_and_is_strict_json(root):
    p = Store(root).run_payload("2026-10-10")
    assert p["previous"] == "2026-10-03"
    assert [r["symbol"] for r in p["removed"]] == ["GONE"]
    json.dumps(p, allow_nan=False)  # NaN must never reach the browser
    new = next(r for r in p["rows"] if r["symbol"] == "NEW")
    assert new["adverse_px"] == "+inf" and new["entry_score"] is None


def test_summary_weights_holdings_by_euro_value(root):
    s = summary(Store(root).load("2026-10-10"))
    assert s["candidates"] == 1 and s["owned"] == 2
    assert s["owned_value_eur"] == pytest.approx(4000.0)
    assert s["owned_at_or_below_ceiling_eur"] == pytest.approx(1000.0)


def test_first_run_has_no_changes(root):
    p = Store(root).run_payload("2026-10-03")
    assert p["previous"] is None and all(r["event"] == "" for r in p["rows"])


def test_symbol_history_and_config(root):
    st = Store(root)
    pts = st.symbol_history("AAA")["points"]
    assert [p["price"] for p in pts] == [110.0, 88.0]
    rates = st.config_payload()["rates"]
    assert rates[0]["hurdle"] == pytest.approx(0.095) and rates[0]["floor"] == pytest.approx(0.05)


def test_unknown_or_malformed_run_is_rejected(root):
    with pytest.raises(KeyError):
        Store(root).load("../config")
    with pytest.raises(KeyError):
        Store(root).load("2020-01-01")


def test_records_cleans_numpy_and_nan():
    df = pd.DataFrame({"a": [1.0, math.nan, -math.inf], "b": ["x", None, "z"]})
    assert records(df) == [{"a": 1.0, "b": "x"}, {"a": None, "b": None}, {"a": "-inf", "b": "z"}]


def test_server_serves_api_and_static_and_blocks_foreign_hosts(root):
    httpd = serve(Store(root), "127.0.0.1", 0)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        runs = json.load(urllib.request.urlopen(base + "/api/runs"))
        assert [r["date"] for r in runs["runs"]] == ["2026-10-03", "2026-10-10"]
        run = json.load(urllib.request.urlopen(base + "/api/run?date=2026-10-10"))
        assert len(run["rows"]) == 3
        assert b"Tripwire" in urllib.request.urlopen(base + "/").read()
        for bad in ("/../config.toml", "/%2e%2e/config.toml", "/api/run?date=../../x"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + bad)
            assert e.value.code == 404
        req = urllib.request.Request(base + "/api/runs", headers={"Host": "evil.example:80"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_derived_rates_mirror_the_screener():
    v = {"equity_risk_premium": 0.045, "adverse_return_spread": 0.025, "min_hurdle": 0.07, "min_rate_over_terminal_growth": 0.02}
    chf = derived_rates({"rf": 0.005, "g": 0.01}, v)          # 5.0% -> lifted to the 7% minimum
    assert chf["hurdle"] == pytest.approx(0.07) and chf["min_hurdle_binds"]
    assert chf["floor"] == pytest.approx(0.03)                 # max(0.5% + 2.5%, 1% + 2%)
    kzt = derived_rates({"rf": 0.14, "g": 0.06, "crp": 0.03}, v)
    assert kzt["hurdle"] == pytest.approx(0.215) and kzt["floor"] == pytest.approx(0.195)
    hi_g = derived_rates({"rf": 0.01, "g": 0.08}, v)           # terminal growth lifts both rates; floor never above hurdle
    assert hi_g["hurdle"] == pytest.approx(0.10) and hi_g["floor"] == pytest.approx(0.10)


def _get(url, host=None):
    req = urllib.request.Request(url, headers={"Host": host} if host else {})
    return urllib.request.urlopen(req)


def test_server_behind_proxy_with_allowed_host_and_base_path(root):
    httpd = serve(Store(root), "127.0.0.1", 0, allowed_hosts=("laptop.tail1234.ts.net",), base_path="/screener")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert _get(base + "/screener/api/runs", "laptop.tail1234.ts.net").status == 200
        assert _get(base + "/api/runs").status == 200           # a proxy that already stripped the prefix
        assert b"Tripwire" in _get(base + "/screener/").read()
        with pytest.raises(urllib.error.HTTPError) as e:        # still no other host names
            _get(base + "/screener/api/runs", "other.example")
        assert e.value.code == 403
        conn = __import__("http.client").client.HTTPConnection("127.0.0.1", httpd.server_address[1])
        conn.request("GET", "/screener")
        resp = conn.getresponse()
        assert resp.status == 308 and resp.getheader("Location") == "/screener/"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_same_day_reruns_are_runs_in_order(root):
    hist = root / "data" / "history"
    for n in ("_1", "_2", "_10"):
        (hist / f"screen_2026-10-10{n}.csv").write_bytes((hist / "screen_2026-10-10.csv").read_bytes())
    st = Store(root)
    assert st.run_dates() == ["2026-10-03", "2026-10-10", "2026-10-10_1", "2026-10-10_2", "2026-10-10_10"]
    assert st.previous("2026-10-10_10") == "2026-10-10_2" and st.previous("2026-10-10_1") == "2026-10-10"
    assert st.run_payload("2026-10-10_2")["previous"] == "2026-10-10_1"
    with pytest.raises(KeyError):
        st.load("2026-10-10_x")
