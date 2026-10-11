"""Offline examples preserving the observed NYU header/date/unit contracts."""
from datetime import datetime, timezone
import hashlib
from html import escape
import json

import pytest

from tools import refresh_industry_references as R


STAMP = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
MARGIN_ROWS = [("Advertising", "52", "36.24%", "-0.30%", "13.30%", "10.07%"),
               (R.FALLBACK, "4,822", "34.39%", "8.56%", "14.65%", "13.14%")]
CAPITAL_ROWS = [("Advertising", "52", "$705.57", "37.43%", "3.85"),
                (R.FALLBACK, "4,822", "$1,446,178.46", "144.74%", "1.43")]


def html(kind, rows=None, month="January", year="2026", headers=None):
    if kind == "margin":
        title = "Margins by Sector (US)"
        headings = ["Industry Name", "Number of firms", "Gross Margin", "Net Margin",
                    "Pre-tax, Pre-stock compensation Operating Margin", "Pre-tax Unadjusted Operating Margin"]
        rows = MARGIN_ROWS if rows is None else rows
    else:
        title = "Capital Expenditures by Sector (US)"
        headings = ["Industry Name", "Number of Firms", "Capital Expenditures (US $ millions)",
                    "Cap Ex/Deprecn", "Sales/ Invested Capital (LTM)"]
        rows = CAPITAL_ROWS if rows is None else rows
    headings = headings if headers is None else headers
    header = "<tr>" + "".join(f"<th><b>{escape(x)}</b></th>" for x in headings) + "</tr>"
    body = "".join("<tr>" + "".join(f"<td><font>{escape(x)}</font></td>" for x in row) + "</tr>" for row in rows)
    return (f'<html><meta charset="utf-8"><h2>{title}</h2><p>Date of Analysis: Data used is as of {month} {year}</p>'
            '<table><tr><td>Navigation</td></tr></table><table>' + header + body +
            '</table><p>Last updated January 2026</p></html>').encode()


def build(margin=None, capital=None):
    return R.build_snapshot(margin or html("margin"), capital or html("capex"), retrieved_at=STAMP)


def test_expected_columns_units_month_precision_and_raw_provenance():
    margin, capex = html("margin"), html("capex")
    snapshot = build(margin, capex)
    assert snapshot["as_of"] == "2026-01"
    assert snapshot["as_of_precision"] == "month"
    assert snapshot["retrieved_on"] == "2026-10-11"
    assert snapshot["retrieval_timezone"] == "UTC"
    row = snapshot["industries"]["Advertising"]
    assert row["net_margin"] == pytest.approx(-.003)
    assert row["operating_margin"] == pytest.approx(.1007)  # NOT pre-SBC13.30%
    assert row["sales_to_invested_capital"] == 3.85
    assert row["firms"] == 52
    assert snapshot["sources"][0]["sha256"] == hashlib.sha256(margin).hexdigest()
    assert snapshot["sources"][1]["sha256"] == hashlib.sha256(capex).hexdigest()
    assert snapshot["sources"][0]["url"] == R.URLS["margin"]
    json.dumps(snapshot, allow_nan=False)


def test_column_reordering_uses_names_not_positions():
    rows = [(r[3], r[0], r[5], r[1]) for r in MARGIN_ROWS]
    margin = html("margin", rows=rows, headers=["Net Margin", "Industry Name", "Pre-tax Unadjusted Operating Margin", "Number of firms"])
    assert build(margin)["industries"]["Advertising"]["operating_margin"] == pytest.approx(.1007)


def test_whitespace_entities_and_formatting_do_not_change_industry_identity():
    margin = html("margin").replace(b"Total Market", b"Total&nbsp;\n Market").replace(b"Unadjusted Operating", b"Unadjusted<br>Operating")
    snapshot = build(margin)
    assert R.FALLBACK in snapshot["industries"]


def test_parentheses_negative_percent_and_zero_operating_margin_are_valid():
    rows = [(*MARGIN_ROWS[0][:3], "(0.30%)", "13.30%", "0.00%"), MARGIN_ROWS[1]]
    values = build(html("margin", rows))["industries"]["Advertising"]
    assert values["net_margin"] == -.003
    assert values["operating_margin"] == 0


@pytest.mark.parametrize("value", ["NA", "N/A", "0", "-1.5"])
def test_unusable_industry_is_retained_with_reason_not_silently_dropped(value):
    rows = [(*CAPITAL_ROWS[0][:-1], value), CAPITAL_ROWS[1]]
    snapshot = build(capital=html("capex", rows))
    assert "Advertising" not in snapshot["industries"]
    assert snapshot["unusable_industries"]["Advertising"]["reasons"]
    assert snapshot["validation"]["matched_industries"] == 2
    assert snapshot["validation"]["usable_industries"] == 1


def test_unavailable_net_margin_is_explicitly_unusable():
    rows = [(*MARGIN_ROWS[0][:3], "NA", *MARGIN_ROWS[0][4:]), MARGIN_ROWS[1]]
    snapshot = build(html("margin", rows))
    assert snapshot["unusable_industries"]["Advertising"]["observations"]["net_margin"] is None


@pytest.mark.parametrize("mutation,match", [
    (lambda m, c: (m, html("capex", month="February")), "different observation periods"),
    (lambda m, c: (m, html("capex", rows=CAPITAL_ROWS[:1])), "Industry populations differ"),
    (lambda m, c: (m, html("capex", rows=[("Advertising", "53", *CAPITAL_ROWS[0][2:]), CAPITAL_ROWS[1]])), "firm counts differ"),
    (lambda m, c: (m.replace(b"10.07%", b"0.1007"), c), "percent units"),
    (lambda m, c: (m, c.replace(b"3.85", b"385%")), "dimensionless"),
    (lambda m, c: (m, c.replace(b"3.85", b"Infinity")), "Malformed/nonfinite"),
    (lambda m, c: (m.replace(b"(US)", b"(Global)"), c), "title/geography"),
    (lambda m, c: (m.replace(b"Data used is as of", b"No observation date"), c), "observation month/year"),
    (lambda m, c: (m.replace(b"Pre-tax Unadjusted Operating Margin", b"After-tax Operating Margin"), c), "required named financial columns"),
])
def test_source_drift_is_rejected(mutation, match):
    margin, capital = mutation(html("margin"), html("capex"))
    with pytest.raises(R.ReferenceValidationError, match=match):
        build(margin, capital)


def test_duplicate_industry_rows_are_rejected():
    with pytest.raises(R.ReferenceValidationError, match="duplicate industry"):
        build(html("margin", [MARGIN_ROWS[0], MARGIN_ROWS[0], MARGIN_ROWS[1]]))


def test_missing_required_fallback_prevents_refresh():
    with pytest.raises(R.ReferenceValidationError, match="fallback"):
        build(html("margin", MARGIN_ROWS[:1]), html("capex", CAPITAL_ROWS[:1]))


def test_unusable_fallback_prevents_refresh():
    rows = [CAPITAL_ROWS[0], (*CAPITAL_ROWS[1][:-1], "0")]
    with pytest.raises(R.ReferenceValidationError, match="fallback"):
        build(capital=html("capex", rows))


def test_conflicting_observation_dates_are_not_reduced_to_one():
    margin = html("margin").replace(b"</html>", b"<p>Data used is as of February 2026</p></html>")
    with pytest.raises(R.ReferenceValidationError, match="unambiguous"):
        build(margin)


def test_old_snapshot_survives_parser_failure(tmp_path):
    target = tmp_path / "reference.json"; target.write_bytes(b'original reference')
    responses = {R.URLS["margin"]: html("margin"), R.URLS["capex"]: b"<html>upstream unavailable</html>"}
    with pytest.raises(R.ReferenceValidationError):
        R.refresh(target, fetcher=responses.__getitem__, retrieved_at=STAMP)
    assert target.read_bytes() == b'original reference'
    assert list(tmp_path.iterdir()) == [target]


def test_old_snapshot_survives_fetch_failure(tmp_path):
    target = tmp_path / "reference.json"; target.write_bytes(b'original reference')
    def fail(url):
        if url == R.URLS["margin"]: return html("margin")
        raise OSError("network unavailable")
    with pytest.raises(OSError):
        R.refresh(target, fetcher=fail, retrieved_at=STAMP)
    assert target.read_bytes() == b'original reference'


def test_atomic_replace_failure_preserves_old_file_and_cleans_temporary(tmp_path, monkeypatch):
    target = tmp_path / "reference.json"; target.write_bytes(b'original reference')
    def fail(*args): raise OSError("replacement failed")
    monkeypatch.setattr(R.os, "replace", fail)
    with pytest.raises(OSError): R.write_snapshot_atomic(build(), target)
    assert target.read_bytes() == b'original reference'
    assert list(tmp_path.iterdir()) == [target]


def test_successful_explicit_refresh_writes_complete_json(tmp_path):
    target = tmp_path / "reference.json"
    responses = {R.URLS["margin"]: html("margin"), R.URLS["capex"]: html("capex")}
    snapshot = R.refresh(target, fetcher=responses.__getitem__, retrieved_at=STAMP)
    assert json.loads(target.read_text()) == snapshot
    assert list(tmp_path.iterdir()) == [target]


def test_cli_local_inputs_write_only_requested_output(tmp_path):
    margin = tmp_path / "margin.html"; margin.write_bytes(html("margin"))
    capital = tmp_path / "capex.html"; capital.write_bytes(html("capex"))
    output = tmp_path / "candidate.json"
    assert R.main(["--margin-html", str(margin), "--capex-html", str(capital), "--output", str(output)]) == 0
    snapshot = json.loads(output.read_text())
    assert all(s["retrieval_mode"] == "local preserved HTML" for s in snapshot["sources"])


def test_no_day_precision_is_manufactured():
    snapshot = build()
    assert snapshot["as_of"] != "2026-01-01"
    assert all(s["observation_precision"] == "month" for s in snapshot["sources"])


def test_invalid_snapshot_never_replaces_old_output(tmp_path):
    target = tmp_path / "reference.json"; target.write_bytes(b'original reference')
    snapshot = build(); snapshot["industries"][R.FALLBACK]["sales_to_invested_capital"] = float("nan")
    with pytest.raises(R.ReferenceValidationError): R.write_snapshot_atomic(snapshot, target)
    assert target.read_bytes() == b'original reference'


def test_declared_windows_encoding_is_honored():
    margin = html("margin").replace(b'charset="utf-8"', b'charset="windows-1252"').replace(b"<html>", b"<html><!--\x96-->")
    assert build(margin)["as_of"] == "2026-01"


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(sources=[]),
    lambda s: s["sources"][0].update(sha256="missing"),
    lambda s: s["sources"][0].update(observation_period="2025-01"),
    lambda s: s["sources"][0].update(columns={}),
    lambda s: s.update(retrieved_at="2026-10-11T12:00:00"),
    lambda s: s.update(observation_period="2026-02"),
])
def test_publication_requires_complete_provenance_and_schema(tmp_path, mutation):
    target = tmp_path / "reference.json"; target.write_bytes(b'original reference')
    snapshot = build(); mutation(snapshot)
    with pytest.raises(R.ReferenceValidationError): R.write_snapshot_atomic(snapshot, target)
    assert target.read_bytes() == b'original reference'


def test_zero_observation_year_is_rejected():
    with pytest.raises(R.ReferenceValidationError, match="calendar year"):
        build(html("margin", year="0000"), html("capex", year="0000"))


def test_cli_failure_retains_old_output(tmp_path):
    margin = tmp_path / "margin.html"; margin.write_bytes(html("margin"))
    capital = tmp_path / "capex.html"; capital.write_bytes(b"<html>changed provider schema</html>")
    output = tmp_path / "reference.json"; output.write_bytes(b'original reference')
    assert R.main(["--margin-html", str(margin), "--capex-html", str(capital), "--output", str(output)]) == 1
    assert output.read_bytes() == b'original reference'
