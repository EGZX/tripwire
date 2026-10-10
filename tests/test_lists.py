"""Lists view backend: validation, saving with backups, search endpoint, screener runs."""
import json
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from dashboard import inputedit
from dashboard.data import Store
from dashboard.jobs import Runner
from dashboard.server import serve

WATCH = "symbol,name,isin,yahoo_symbol,lists\nAAPL,Apple Inc.,,,Core\n,Siemens AG,DE0007236101,SIE.DE,Europe\n"
HOLD = "isin,symbol,name,shares,value_eur,cost_eur\nUS0378331005,AAPL,Apple Inc.,10,1850,1200\n"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "watchlist.csv").write_text(WATCH, encoding="utf-8")
    (tmp_path / "input" / "holdings.csv").write_text(HOLD, encoding="utf-8")
    (tmp_path / "data" / "history").mkdir(parents=True)
    (tmp_path / "config.toml").write_text('[inputs]\nwatchlist = "input/watchlist.csv"\nholdings = "input/holdings.csv"\n'
                                          '[valuation]\nequity_risk_premium = 0.045\n[rates]\nUSD = { rf = 0.05, g = 0.03 }\n',
                                          encoding="utf-8")
    return tmp_path


def test_isin_check_digit():
    assert inputedit.isin_ok("US0378331005") and inputedit.isin_ok("DE0007236101")
    assert not inputedit.isin_ok("US0378331006") and not inputedit.isin_ok("US037833100")


def test_clean_rows_rejects_bad_input_and_keeps_good():
    good = inputedit.clean_rows("watchlist", [{"symbol": "AAPL", "name": "Apple", "lists": ["Core", "Core", "Tech"]}])
    assert good[0]["lists"] == "Core; Tech"
    for bad, msg in [([{"name": "=HYPERLINK(1)", "symbol": "X"}], "formula"),
                     ([{"symbol": "AAPL"}, {"symbol": "aapl"}], "already"),
                     ([{"isin": "US0378331006"}], "ISIN"),
                     ([{"name": "no key"}], "give"),
                     ([{"symbol": "A\nB"}], "control")]:
        with pytest.raises(ValueError, match=msg):
            inputedit.clean_rows("watchlist", bad)
    with pytest.raises(ValueError, match="number"):
        inputedit.clean_rows("holdings", [{"isin": "US0378331005", "shares": -1}])


def _call(url, body=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def _start(root, **kw):
    httpd = serve(Store(root), "127.0.0.1", 0, **kw)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_inputs_roundtrip_with_review_backup_and_conflict(root):
    httpd, base = _start(root)
    try:
        _, cfg = _call(base + "/api/config")
        tok = {"X-CSRF-Token": cfg["csrf"]}
        st, inp = _call(base + "/api/inputs")
        assert st == 200 and inp["editable"] and len(inp["watchlist"]["rows"]) == 2
        assert inp["holdings"]["rows"][0]["shares"] == 10.0                      # numbers, not strings
        rows = inp["watchlist"]["rows"] + [{"symbol": "SAP.DE", "yahoo_symbol": "SAP.DE", "name": "SAP SE", "lists": "Europe"}]
        body = {"kind": "watchlist", "rows": rows, "base_sha": inp["watchlist"]["sha"]}
        st, res = _call(base + "/api/inputs", {**body, "dry_run": True}, tok)
        assert st == 200 and res["diff"] == {"added": ["SAP.DE"], "removed": [], "changed": []} and not res["written"]
        assert _call(base + "/api/inputs", body)[0] == 403                      # no token
        st, res = _call(base + "/api/inputs", body, tok)
        assert st == 200 and res["written"]
        assert "SAP.DE,SAP SE" in (root / "input" / "watchlist.csv").read_text(encoding="utf-8")
        assert list((root / "data" / "input-history").glob("watchlist_*.csv"))  # previous version kept
        st, res = _call(base + "/api/inputs", body, tok)                          # stale page: same old sha
        assert st == 409 and res["conflict"]
        st, res = _call(base + "/api/inputs", {"kind": "watchlist", "rows": [{"name": "=1+1", "symbol": "X"}]}, tok)
        assert st == 400 and "formula" in " ".join(res["errors"])
        st, res = _call(base + "/api/inputs", body, {**tok, "X-Forwarded-For": "100.64.0.9"})   # through a proxy
        assert st == 403
    finally:
        httpd.shutdown(); httpd.server_close()


def test_search_endpoint_uses_the_search_function(root):
    seen = []
    def fake(q):
        seen.append(q)
        return [{"symbol": "SIE.DE", "name": "Siemens AG", "exchange": "XETRA", "type": "Stock", "sector": "Industrials"}]
    httpd, base = _start(root, search=fake)
    try:
        st, res = _call(base + "/api/search?q=siemens")
        assert st == 200 and res["results"][0]["symbol"] == "SIE.DE" and seen == ["siemens"]
    finally:
        httpd.shutdown(); httpd.server_close()


def test_search_maps_and_filters_yahoo_quotes():
    from dashboard import search as s
    s._cache.clear()
    quotes = [{"symbol": "SIE.DE", "longname": "Siemens Aktiengesellschaft", "exchDisp": "XETRA", "quoteType": "EQUITY"},
              {"symbol": "SIEGY", "shortname": "SIEMENS AG ADR", "exchDisp": "OTC", "quoteType": "EQUITY"},
              {"symbol": "^GDAXI", "shortname": "DAX", "quoteType": "INDEX"}]
    out = s.search("unit-test-query", fetch=lambda q: quotes)
    assert [x["symbol"] for x in out] == ["SIE.DE", "SIEGY"] and out[0]["type"] == "Stock"
    assert s.search("unit-test-query", fetch=lambda q: 1 / 0) == out                # second call served from the cache


def test_runner_is_single_flight_and_captures_output(root):
    r = Runner(root, command=[sys.executable, "-c", "import time; print('hello'); time.sleep(0.5); print('done')"])
    assert r.start() and not r.start()                                            # second start refused while running
    for _ in range(100):
        if not r.status()["running"]:
            break
        time.sleep(0.05)
    st = r.status()
    assert st["code"] == 0 and st["log"] == ["hello", "done"]


def test_diff_treats_equal_amounts_as_unchanged():
    old = [{"isin": "US0378331005", "name": "Apple", "shares": "10", "value_eur": "7317.90", "cost_eur": "42477.71"}]
    new = inputedit.clean_rows("holdings", [{"isin": "US0378331005", "name": "Apple", "shares": 10.0, "value_eur": 7317.9,
                                             "cost_eur": 42477.71}])
    assert inputedit.diff("holdings", old, new) == {"added": [], "removed": [], "changed": []}
    new[0]["cost_eur"] = "42477.72"
    assert inputedit.diff("holdings", old, new)["changed"] == ["US0378331005"]
