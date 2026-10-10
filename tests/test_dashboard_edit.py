"""The config endpoint: CSRF token, loopback-only writes, dry runs, read-only mode."""
import json
import threading
import tomllib
import urllib.error
import urllib.request

import pytest

from dashboard.data import Store
from dashboard.server import serve

CONFIG = '''[valuation]
equity_risk_premium = 0.045
adverse_return_spread = 0.025
min_hurdle = 0.07
base_currency = "EUR"
[rates]
USD = { rf = 0.05, g = 0.03, as_of = "2026-09-01" }
EUR = { rf = 0.03, g = 0.02 }
'''


@pytest.fixture
def root(tmp_path):
    (tmp_path / "data" / "history").mkdir(parents=True)
    (tmp_path / "config.toml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def start(root, **kw):
    httpd = serve(Store(root), "127.0.0.1", 0, **kw)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def call(url, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_config_payload_has_token_editability_and_rate_age(root):
    httpd, base = start(root)
    try:
        st, cfg = call(base + "/api/config")
        assert st == 200 and cfg["editable"] is True and len(cfg["csrf"]) >= 20
        usd = next(r for r in cfg["rates"] if r["currency"] == "USD")
        assert usd["as_of"] == "2026-09-01" and isinstance(usd["age_days"], int) and usd["age_days"] > 30
        eur = next(r for r in cfg["rates"] if r["currency"] == "EUR")
        assert eur["as_of"] is None and eur["age_days"] is None
        assert "equity_risk_premium" in cfg["editable_keys"] and "years" not in cfg["editable_keys"]
    finally:
        httpd.shutdown(); httpd.server_close()


def test_post_requires_token_and_writes_with_it(root):
    httpd, base = start(root)
    try:
        _, cfg = call(base + "/api/config")
        body = {"valuation": {"min_hurdle": 0.08}, "rates": {"USD": {"rf": 0.051, "g": 0.03, "as_of": "2026-10-10"}}}
        st, res = call(base + "/api/config", body)                                  # no token
        assert st == 403
        st, res = call(base + "/api/config", {**body, "dry_run": True}, {"X-CSRF-Token": cfg["csrf"]})
        assert st == 200 and res["ok"] and res["written"] is False and len(res["changes"]) == 2
        assert tomllib.loads((root / "config.toml").read_text())["valuation"]["min_hurdle"] == 0.07   # dry run wrote nothing
        st, res = call(base + "/api/config", body, {"X-CSRF-Token": cfg["csrf"]})
        assert st == 200 and res["ok"] and res["written"] is True and res["rerun_needed"] is True
        new = tomllib.loads((root / "config.toml").read_text())
        assert new["valuation"]["min_hurdle"] == 0.08 and new["rates"]["USD"]["rf"] == 0.051
        assert (root / "config.toml.bak").exists() and list((root / "data" / "config-history").glob("config_*.toml"))
        st, res = call(base + "/api/config", {"valuation": {"return_cap": 9}}, {"X-CSRF-Token": cfg["csrf"]})
        assert st == 400 and not res["ok"] and any("return_cap" in e for e in res["errors"])
    finally:
        httpd.shutdown(); httpd.server_close()


def test_edits_are_loopback_only_unless_enabled(root):
    httpd, base = start(root, allowed_hosts=("laptop.tail1234.ts.net",))
    try:
        st, cfg = call(base + "/api/config", headers={"Host": "laptop.tail1234.ts.net"})
        assert st == 200 and cfg["editable"] is False
        st, res = call(base + "/api/config", {"valuation": {"min_hurdle": 0.08}},
                       {"X-CSRF-Token": cfg["csrf"], "Host": "laptop.tail1234.ts.net"})
        assert st == 403
        st, res = call(base + "/api/config", {"valuation": {"min_hurdle": 0.08}},
                       {"X-CSRF-Token": cfg["csrf"], "Origin": "http://evil.example"})            # cross-site POST
        assert st == 403
    finally:
        httpd.shutdown(); httpd.server_close()
    httpd, base = start(root, allowed_hosts=("laptop.tail1234.ts.net",), allow_remote_edit=True)
    try:
        st, cfg = call(base + "/api/config", headers={"Host": "laptop.tail1234.ts.net"})
        assert cfg["editable"] is True
    finally:
        httpd.shutdown(); httpd.server_close()


def test_read_only_mode_rejects_writes(root):
    httpd, base = start(root, read_only=True)
    try:
        st, cfg = call(base + "/api/config")
        assert cfg["editable"] is False
        st, res = call(base + "/api/config", {"valuation": {"min_hurdle": 0.08}}, {"X-CSRF-Token": cfg["csrf"]})
        assert st == 405
    finally:
        httpd.shutdown(); httpd.server_close()


def test_proxied_requests_cannot_edit_even_with_a_loopback_host(root):
    # behind Tailscale Serve or cloudflared every request comes from 127.0.0.1 and Host is client-controlled
    httpd, base = start(root, allowed_hosts=("laptop.tail1234.ts.net",))
    try:
        for hdr in ({"Tailscale-User-Login": "someone@example.com"}, {"X-Forwarded-For": "100.64.0.7"}):
            st, cfg = call(base + "/api/config", headers=hdr)
            assert cfg["editable"] is False
            st, res = call(base + "/api/config", {"valuation": {"min_hurdle": 0.08}}, {"X-CSRF-Token": cfg["csrf"], **hdr})
            assert st == 403
        assert tomllib.loads((root / "config.toml").read_text())["valuation"]["min_hurdle"] == 0.07
    finally:
        httpd.shutdown(); httpd.server_close()


def test_save_against_a_changed_file_is_refused(root):
    httpd, base = start(root)
    try:
        _, cfg = call(base + "/api/config")
        tok = {"X-CSRF-Token": cfg["csrf"]}
        st, res = call(base + "/api/config", {"valuation": {"min_hurdle": 0.08}, "base_sha": cfg["sha"]}, tok)
        assert st == 200 and res["written"]
        # a second tab still holding the old hash must not overwrite the first save
        st, res = call(base + "/api/config", {"valuation": {"equity_risk_premium": 0.05}, "base_sha": cfg["sha"]}, tok)
        assert st == 409 and res["conflict"] and not res["written"]
        new = tomllib.loads((root / "config.toml").read_text())["valuation"]
        assert new["min_hurdle"] == 0.08 and new["equity_risk_premium"] == 0.045
    finally:
        httpd.shutdown(); httpd.server_close()


def test_malformed_content_length_is_a_clean_400(root):
    import http.client
    httpd, base = start(root)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1])
        conn.putrequest("POST", "/api/config")
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status in (400, 403)  # 403 first: no CSRF token; never a crash
    finally:
        httpd.shutdown(); httpd.server_close()


def test_save_with_a_run_present_does_not_deadlock(root):
    # edit_config holds the store lock and reads the latest run (to protect its currencies); that read takes the lock too
    (root / "data" / "history" / "screen_2026-10-10.csv").write_text("symbol,currency,status\nAAA,USD,eligible\n", encoding="utf-8")
    httpd, base = start(root)
    try:
        _, cfg = call(base + "/api/config")
        st, res = call(base + "/api/config", {"valuation": {"min_hurdle": 0.075}, "base_sha": cfg["sha"]}, {"X-CSRF-Token": cfg["csrf"]})
        assert st == 200 and res["written"]
        st, res = call(base + "/api/config", {"rates": {"USD": None}}, {"X-CSRF-Token": cfg["csrf"]})
        assert st == 400 and any("USD cannot be removed" in e for e in res["errors"])
    finally:
        httpd.shutdown(); httpd.server_close()
