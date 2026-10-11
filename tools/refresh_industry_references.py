"""Explicit, validated refresh of the public NYU industry reference snapshot.

No network access occurs on import or during valuation. Required columns are
matched by name, percentages are converted to fractions, and source observation
dates retain their published month precision. A failed refresh leaves the old
snapshot intact. Sources are reference assumptions, not issuer forecasts/GICS.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "screener" / "resources" / "industry_benchmarks.json"
URLS = {
    "margin": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/margin.html",
    "capex": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/capex.html",
}
FALLBACK = "Total Market (without financials)"
MAX_SOURCE_BYTES = 10 * 1024 * 1024  # Input safety limit, not an economic threshold.
MONTHS = {name.casefold(): i for i, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
MISSING = {"na", "n/a", "n.a.", "", "-", "—", "–"}
REQUIRED = {
    "margin": {"industry": "Industry Name", "firms": "Number of firms", "net_margin": "Net Margin",
               "operating_margin": "Pre-tax Unadjusted Operating Margin"},
    "capex": {"industry": "Industry Name", "firms": "Number of Firms",
              "sales_to_invested_capital": "Sales/ Invested Capital (LTM)"},
}


class ReferenceValidationError(ValueError):
    """Source schema, units, dates or populations do not support a refresh."""


def _space(value):
    return " ".join(value.split())


def _header(value):
    return re.sub(r"[\s\-\u2010-\u2015]+", "", value.casefold())


@dataclass
class Cell:
    text: str
    colspan: int = 1
    rowspan: int = 1


class Tables(HTMLParser):
    """Small HTML table reader; formatting tags do not split financial cells."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.text = []
        self.stack = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1
        if self.ignored:
            return
        if tag == "table":
            rows = []
            self.tables.append(rows)
            self.stack.append({"rows": rows, "row": None, "cell": None})
        elif self.stack:
            state = self.stack[-1]
            if tag == "tr":
                if state["row"] is not None:
                    raise ReferenceValidationError("Nested/unclosed table rows are ambiguous")
                state["row"] = []
            elif tag in ("th", "td") and state["row"] is not None:
                if state["cell"] is not None:
                    raise ReferenceValidationError("Nested/unclosed table cells are ambiguous")
                attributes = dict(attrs)
                try:
                    colspan, rowspan = (int(attributes.get(k, "1")) for k in ("colspan", "rowspan"))
                except ValueError as exc:
                    raise ReferenceValidationError("Invalid table span") from exc
                if min(colspan, rowspan) < 1:
                    raise ReferenceValidationError("Invalid table span")
                state["cell"] = Cell("", colspan, rowspan)
            elif tag in ("br", "p") and state["cell"] is not None:
                state["cell"].text += " "

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored = max(0, self.ignored-1)
            return
        if self.ignored or not self.stack:
            return
        state = self.stack[-1]
        if tag in ("th", "td") and state["cell"] is not None:
            state["cell"].text = _space(state["cell"].text)
            state["row"].append(state["cell"])
            state["cell"] = None
        elif tag == "tr" and state["row"] is not None:
            if state["cell"] is not None:
                raise ReferenceValidationError("Unclosed financial cell")
            state["rows"].append(state["row"])
            state["row"] = None
        elif tag == "table":
            if state["row"] is not None or state["cell"] is not None:
                raise ReferenceValidationError("Unclosed financial row")
            self.stack.pop()

    def handle_data(self, value):
        if self.ignored:
            return
        self.text.append(value)
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].text += value


def _decode(raw: bytes):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ReferenceValidationError("Source must be nonempty HTML bytes within the input-size limit")
    # Damodaran's Excel exports have used Windows-1252 as well as UTF-8.
    # Honor explicit encodings instead of replacing unrecognized bytes.
    declared = re.search(br"charset\s*=\s*[\"']?\s*([A-Za-z0-9_-]+)", raw[:8192], re.I)
    encoding = declared.group(1).decode("ascii") if declared else "utf-8-sig"
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ReferenceValidationError("Source encoding is unsupported or inconsistent with its declaration") from exc


def _observation_period(text):
    month_pattern = "|".join(MONTHS)
    matches = re.findall(r"data\s+used\s+is\s+as\s+of\s+(" + month_pattern + r")\s+(\d{4})\b", text, re.I)
    if any(int(year) < 1 for _, year in matches):
        raise ReferenceValidationError("Observation year is not a valid calendar year")
    periods = {f"{year}-{MONTHS[month.casefold()]:02d}" for month, year in matches}
    if len(periods) != 1:
        raise ReferenceValidationError("Source must disclose one unambiguous observation month/year")
    return periods.pop()


def _number(text, *, percent=False, integer=False):
    value = _space(text).replace("−", "-")
    if value.casefold() in MISSING:
        return None
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1].strip()
    if percent:
        if not value.endswith("%"):
            raise ReferenceValidationError(f"Percentage value lacks explicit percent units: {text!r}")
        value = value[:-1].strip()
    elif "%" in value or "$" in value:
        raise ReferenceValidationError(f"Expected a dimensionless number, not percent/currency: {text!r}")
    # Reject malformed separators and nonfinite tokens rather than guessing.
    numeric = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    if not re.fullmatch(numeric, value):
        raise ReferenceValidationError(f"Malformed/nonfinite numeric observation: {text!r}")
    number = float(value.replace(",", "")) * (-1 if negative else 1)
    if not math.isfinite(number):
        raise ReferenceValidationError("Nonfinite numeric observation")
    if integer:
        if number <= 0 or int(number) != number:
            raise ReferenceValidationError("Firm count must be a positive integer")
        return int(number)
    return number / 100 if percent else number


def parse_dataset(raw: bytes, kind: str) -> dict:
    if kind not in REQUIRED:
        raise ReferenceValidationError("Unknown dataset kind")
    parser = Tables()
    parser.feed(_decode(raw)); parser.close()
    if parser.stack:
        raise ReferenceValidationError("Unclosed HTML table")
    text = _space(" ".join(parser.text))
    title = "Margins by Sector" if kind == "margin" else "Capital Expenditures by Sector"
    if not re.search(re.escape(title) + r"\s*\(US\)", text, re.I):
        raise ReferenceValidationError("Dataset title/geography does not identify the expected US reference")
    period = _observation_period(text)
    required = {key: _header(label) for key, label in REQUIRED[kind].items()}
    matches = []
    for table in parser.tables:
        for index, row in enumerate(table):
            normalized = [_header(c.text) for c in row]
            if set(required.values()).issubset(normalized):
                if any(normalized.count(label) != 1 for label in required.values()):
                    raise ReferenceValidationError("Duplicate required financial columns")
                if any(c.colspan != 1 or c.rowspan != 1 for c in row):
                    raise ReferenceValidationError("Merged financial header needs explicit schema review")
                matches.append((table, index, normalized))
    if len(matches) != 1:
        raise ReferenceValidationError("Expected exactly one table with the required named financial columns")
    table, header_index, labels = matches[0]
    columns = {key: labels.index(label) for key, label in required.items()}
    rows = {}
    for row in table[header_index+1:]:
        if not any(c.text for c in row):
            continue
        if len(row) != len(labels) or any(c.colspan != 1 or c.rowspan != 1 for c in row):
            raise ReferenceValidationError("Financial row shape changed; positional guessing is forbidden")
        name = _space(row[columns["industry"]].text)
        if not name or name in rows:
            raise ReferenceValidationError(f"Empty or duplicate industry row: {name!r}")
        values = {"firms": _number(row[columns["firms"]].text, integer=True)}
        for field in required.keys() - {"industry", "firms"}:
            values[field] = _number(row[columns[field]].text, percent=kind == "margin")
        values["raw_values"] = {field: row[pos].text for field, pos in columns.items()}
        rows[name] = values
    if not rows:
        raise ReferenceValidationError("Financial table contains no industry observations")
    return {"observation_period": period, "rows": rows, "columns": REQUIRED[kind], "geography": "United States"}


def build_snapshot(margin_html: bytes, capex_html: bytes, *, retrieved_at=None, source_urls=None) -> dict:
    datasets = {"margin": parse_dataset(margin_html, "margin"), "capex": parse_dataset(capex_html, "capex")}
    periods = {d["observation_period"] for d in datasets.values()}
    if len(periods) != 1:
        raise ReferenceValidationError("Margin and capital datasets have different observation periods")
    margins, capitals = datasets["margin"]["rows"], datasets["capex"]["rows"]
    if margins.keys() != capitals.keys():
        missing_capital = sorted(margins.keys() - capitals.keys())
        missing_margin = sorted(capitals.keys() - margins.keys())
        raise ReferenceValidationError(f"Industry populations differ: absent from capex={missing_capital}; absent from margins={missing_margin}")
    industries, unusable = {}, {}
    for name, margin in margins.items():
        capital = capitals[name]
        if margin["firms"] != capital["firms"]:
            raise ReferenceValidationError(f"Industry firm counts differ for {name!r}")
        fields = {"net_margin": margin["net_margin"], "operating_margin": margin["operating_margin"],
                  "sales_to_invested_capital": capital["sales_to_invested_capital"], "firms": margin["firms"]}
        reasons = [f"{key} unavailable" for key, value in fields.items() if value is None]
        if fields["sales_to_invested_capital"] is not None and fields["sales_to_invested_capital"] <= 0:
            reasons.append("sales_to_invested_capital is not positive")
        if reasons:
            unusable[name] = {"reasons": reasons, "observations": fields,
                              "margin_source_values": margin["raw_values"], "capital_source_values": capital["raw_values"]}
        else:
            industries[name] = fields
    if FALLBACK not in industries:
        raise ReferenceValidationError("Usable Total Market (without financials) fallback is required")
    stamp = retrieved_at or datetime.now(timezone.utc)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ReferenceValidationError("Retrieval timestamp must include a timezone")
    stamp = stamp.astimezone(timezone.utc)
    urls = source_urls or URLS
    if set(urls) != set(URLS) or not all(isinstance(v, str) and v for v in urls.values()):
        raise ReferenceValidationError("Both source URLs must be explicitly identified")
    period = periods.pop()
    return {"schema_version": 1, "as_of": period, "as_of_precision": "month", "observation_period": period,
            "retrieved_on": stamp.date().isoformat(), "retrieved_at": stamp.isoformat(), "retrieval_timezone": "UTC",
            "geography": "United States", "usage": "Industry reference for explicit scenario assumptions; not a company forecast or GICS classification.",
            "units": {"net_margin": "fraction", "operating_margin": "pre-tax unadjusted fraction",
                      "sales_to_invested_capital": "dimensionless sales / invested capital (LTM)", "firms": "count"},
            "sources": [{"dataset": kind, "url": urls[kind], "sha256": hashlib.sha256(raw).hexdigest(),
                         "observation_period": period, "observation_precision": "month", "columns": datasets[kind]["columns"],
                         "retrieved_at": stamp.isoformat()} for kind, raw in (("margin", margin_html), ("capex", capex_html))],
            "industries": industries, "unusable_industries": unusable,
            "validation": {"matched_industries": len(margins), "usable_industries": len(industries),
                           "unusable_industries": len(unusable), "population_join": "exact names and firm counts"}}


def write_snapshot_atomic(snapshot: dict, output: Path) -> None:
    """Publish only a complete supported schema; replacement is one operation."""
    if not isinstance(snapshot, dict) or snapshot.get("as_of_precision") != "month" or not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", str(snapshot.get("as_of"))):
        raise ReferenceValidationError("Snapshot must retain observation-month precision")
    if snapshot.get("schema_version") != 1 or snapshot.get("observation_period") != snapshot["as_of"] or snapshot["as_of"].startswith("0000"):
        raise ReferenceValidationError("Unsupported snapshot schema or inconsistent observation period")
    try:
        stamp = datetime.fromisoformat(snapshot["retrieved_at"])
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0 or snapshot.get("retrieved_on") != stamp.date().isoformat():
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ReferenceValidationError("Snapshot requires a consistent UTC retrieval date/time") from None
    sources = snapshot.get("sources")
    if not isinstance(sources, list) or len(sources) != 2 or not all(isinstance(s, dict) for s in sources):
        raise ReferenceValidationError("Snapshot requires both source provenance records")
    if {s.get("dataset") for s in sources} != set(REQUIRED):
        raise ReferenceValidationError("Snapshot source dataset identities are inconsistent")
    for source in sources:
        if (not re.fullmatch(r"[a-f0-9]{64}", str(source.get("sha256"))) or
                not re.match(r"https?://[^/]+/", str(source.get("url"))) or
                source.get("observation_period") != snapshot["as_of"] or
                source.get("observation_precision") != "month" or
                source.get("retrieved_at") != snapshot["retrieved_at"] or
                source.get("columns") != REQUIRED[source["dataset"]]):
            raise ReferenceValidationError("Source hashes, periods, URLs or column definitions are incomplete")
    industries = snapshot.get("industries", {})
    if not isinstance(industries, dict) or FALLBACK not in industries:
        raise ReferenceValidationError("Snapshot lacks its required usable fallback")
    for name, fields in industries.items():
        if not isinstance(name, str) or not name.strip():
            raise ReferenceValidationError("Industry identities must be nonempty strings")
        try:
            values = [fields[k] for k in ("net_margin", "operating_margin", "sales_to_invested_capital")]
            valid = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)
            valid = valid and fields["sales_to_invested_capital"] > 0 and isinstance(fields["firms"], int) and not isinstance(fields["firms"], bool) and fields["firms"] > 0
        except (KeyError, TypeError):
            valid = False
        if not valid:
            raise ReferenceValidationError(f"Unusable published industry: {name!r}")
    payload = json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False) + "\n"
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Tripwire industry reference refresh (public NYU datasets)"})
    with urlopen(request, timeout=30) as response:
        raw = response.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ReferenceValidationError("Source exceeded the HTML input-size limit")
    return raw


def refresh(output: Path, *, fetcher=fetch_bytes, retrieved_at=None) -> dict:
    raw = {kind: fetcher(url) for kind, url in URLS.items()}
    snapshot = build_snapshot(raw["margin"], raw["capex"], retrieved_at=retrieved_at)
    write_snapshot_atomic(snapshot, output)
    return snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--margin-html", type=Path, help="Use a preserved source HTML file instead of downloading")
    parser.add_argument("--capex-html", type=Path, help="Use with --margin-html; both inputs must be supplied")
    args = parser.parse_args(argv)
    if bool(args.margin_html) != bool(args.capex_html):
        parser.error("--margin-html and --capex-html must be supplied together")
    try:
        if args.margin_html:
            snapshot = build_snapshot(args.margin_html.read_bytes(), args.capex_html.read_bytes())
            for source, path in zip(snapshot["sources"], (args.margin_html, args.capex_html)):
                source.update(retrieval_mode="local preserved HTML", local_path=str(path.resolve()))
            write_snapshot_atomic(snapshot, args.output)
        else:
            snapshot = refresh(args.output)
        print(f"Wrote {len(snapshot['industries'])} usable industries for {snapshot['as_of']} (month precision); "
              f"{len(snapshot['unusable_industries'])} explicitly unusable rows -> {args.output}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Refresh failed; existing snapshot retained: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
