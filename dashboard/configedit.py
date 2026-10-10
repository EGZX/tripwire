"""Validated, in-place edits of config.toml for the dashboard.

Values are replaced on their own lines so comments and layout survive. Only whitelisted [valuation] keys and the
[rates] table are editable. A plan is validated (types, ranges, then the screener's own checks on the parsed result)
before anything is written; saving keeps config.toml.bak and a dated copy under data/config-history/ and replaces the
file atomically.
"""
from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from numbers import Real
from pathlib import Path

from screener import checks

# key -> spec. "pct" values are stored as fractions (0.045) and shown in percent by the UI.
EDITABLE_VALUATION: dict[str, dict] = {
    "equity_risk_premium": {"type": "pct", "min": 0.0, "max": 0.20, "label": "Equity risk premium", "tier": 2,
                            "help": "Added to the currency 10-year yield to form the hurdle."},
    "min_hurdle": {"type": "pct", "min": 0.0, "max": 0.30, "label": "Minimum hurdle", "tier": 2,
                   "help": "Never require less than this nominal return, whatever the local rates say."},
    "base_currency": {"type": "currency", "label": "Base currency", "tier": 2,
                      "help": "Shows an FX-parity scenario; not an expected equity return in this currency."},
    "adverse_growth_mode": {"type": "choice", "choices": ["half", "haircut"], "label": "Adverse growth", "tier": 2,
                            "help": "half: half the lowest growth input (declines kept). haircut: lowest input minus the haircut."},
    "adverse_growth_haircut": {"type": "pct", "min": 0.0, "max": 0.50, "label": "Adverse growth haircut", "tier": 2,
                               "help": "Only used in haircut mode."},
    "growth_cap": {"type": "pct", "min": 0.0, "max": 1.0, "label": "Growth cap", "tier": 2,
                   "help": "Cap on the reasonable-case starting growth."},
    "growth_floor": {"type": "pct", "min": -0.5, "max": 0.0, "label": "Growth floor", "tier": 2},
    "return_cap": {"type": "pct", "min": 0.05, "max": 1.0, "label": "Return on capital cap", "tier": 2,
                   "help": "Book capital is unreliable above this."},
    "stale_after_days": {"type": "int", "min": 30, "max": 1000, "label": "Stale after (days)", "tier": 2},
    "bank_min_equity_to_assets": {"type": "pct", "min": 0.0, "max": 0.20, "label": "Bank thin-capital threshold", "tier": 2},
}
RATE_RANGES = {"rf": (-0.05, 0.50), "g": (-0.05, 0.20), "crp": (0.0, 0.20)}
CCY_RE = re.compile(r"^[A-Z]{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SECTION_RE = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(#.*)?$")
RATE_LINE_RE = re.compile(r"^(?P<ccy>[A-Z]{3})\s*=\s*\{(?P<body>[^}]*)\}(?P<tail>\s*(#.*)?)$")


@dataclass
class Plan:
    text: str
    changes: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def age_days(as_of: str | None, today: str | None = None) -> int | None:
    if not as_of or not DATE_RE.match(str(as_of)):
        return None
    try:
        d = date.fromisoformat(str(as_of))
    except ValueError:
        return None
    t = date.fromisoformat(today) if today else date.today()
    return (t - d).days


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = f"{v:.6f}".rstrip("0")
        return s + "0" if s.endswith(".") else s
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sections(lines: list[str]) -> dict[str, tuple[int, int]]:
    """section name -> (first line index after the header, end index exclusive)."""
    out, current, start = {}, None, 0
    for i, line in enumerate(lines):
        m = SECTION_RE.match(line)
        if m:
            if current is not None:
                out[current] = (start, i)
            current, start = m.group(1), i + 1
    if current is not None:
        out[current] = (start, len(lines))
    return out


def _coerce(key: str, spec: dict, value):
    t = spec["type"]
    if t == "pct":
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{key}: must be a number")
        v = float(value)
        if not (spec["min"] <= v <= spec["max"]):
            raise ValueError(f"{key}: {v:.4f} is outside {spec['min']} .. {spec['max']}")
        return round(v, 6)
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, Real) or int(value) != value:
            raise ValueError(f"{key}: must be a whole number")
        v = int(value)
        if not (spec["min"] <= v <= spec["max"]):
            raise ValueError(f"{key}: {v} is outside {spec['min']} .. {spec['max']}")
        return v
    if t == "choice":
        if value not in spec["choices"]:
            raise ValueError(f"{key}: must be one of {', '.join(spec['choices'])}")
        return str(value)
    if t == "currency":
        if not isinstance(value, str) or not CCY_RE.match(value):
            raise ValueError(f"{key}: must be a three-letter currency code")
        return value
    raise ValueError(f"{key}: unknown type")


def _rate_entry(ccy: str, r) -> dict:
    if not CCY_RE.match(ccy):
        raise ValueError(f"rates: '{ccy}' is not a three-letter upper-case currency code")
    if not isinstance(r, dict):
        raise ValueError(f"rates {ccy}: expected an object with rf and g")
    out = {}
    for k in ("rf", "g", "crp"):
        if k in r and r[k] is not None:
            if isinstance(r[k], bool) or not isinstance(r[k], Real):
                raise ValueError(f"rates {ccy}: {k} must be a number")
            lo, hi = RATE_RANGES[k]
            if not (lo <= float(r[k]) <= hi):
                raise ValueError(f"rates {ccy}: {k} {float(r[k]):.4f} is outside {lo} .. {hi}")
            out[k] = round(float(r[k]), 6)
    if "rf" not in out or "g" not in out:
        raise ValueError(f"rates {ccy}: rf and g are required")
    if r.get("as_of"):
        if not DATE_RE.match(str(r["as_of"])) or age_days(str(r["as_of"]), "2100-01-01") is None:
            raise ValueError(f"rates {ccy}: as_of must be a date YYYY-MM-DD")
        out["as_of"] = str(r["as_of"])
    return out


def _format_rate(ccy: str, r: dict) -> str:
    parts = [f"rf = {_toml_value(r['rf'])}", f"g = {_toml_value(r['g'])}"]
    if "crp" in r:
        parts.append(f"crp = {_toml_value(r['crp'])}")
    if "as_of" in r:
        parts.append(f"as_of = {_toml_value(r['as_of'])}")
    return f"{ccy} = {{ {', '.join(parts)} }}"


def plan(text: str, valuation: dict, rates: dict, required_currencies: set[str]) -> Plan:
    """Build the edited text. valuation: {key: value} for editable keys. rates: {CCY: {rf, g, crp?, as_of?} | None}
    where None removes the currency. required_currencies: reporting currencies in the universe (cannot be removed)."""
    errors: list[str] = []
    try:
        current = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return Plan(text, [], [f"config.toml does not parse: {exc}"])
    cur_val, cur_rates = current.get("valuation", {}), current.get("rates", {})

    new_val: dict = {}
    for key, value in (valuation or {}).items():
        spec = EDITABLE_VALUATION.get(key)
        if spec is None:
            errors.append(f"{key}: not editable from the dashboard")
            continue
        try:
            new_val[key] = _coerce(key, spec, value)
        except ValueError as exc:
            errors.append(str(exc))
    new_rates: dict[str, dict | None] = {}
    for ccy, r in (rates or {}).items():
        if r is None:
            new_rates[ccy] = None
            continue
        try:
            new_rates[ccy] = _rate_entry(ccy, r)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        return Plan(text, [], errors)

    lines = text.splitlines()
    changes: list[dict] = []
    sections = _sections(lines)

    # ---- [valuation]
    if new_val:
        if "valuation" not in sections:
            errors.append("config.toml has no [valuation] section")
            return Plan(text, [], errors)
        start, end = sections["valuation"]
        for key, value in new_val.items():
            old = cur_val.get(key)
            if old == value and type(old) is type(value):
                continue
            pat = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)(.*?)(\s*#.*)?$")
            for i in range(start, end):
                m = pat.match(lines[i])
                if m:
                    lines[i] = f"{m.group(1)}{_toml_value(value)}{m.group(3) or ''}"
                    break
            else:
                lines.insert(end, f"{key} = {_toml_value(value)}")
                end += 1
                sections = _sections(lines)
            changes.append({"section": "valuation", "key": key, "old": old, "new": value, "kind": "update" if key in cur_val else "add"})

    # ---- [rates]
    if new_rates:
        sections = _sections(lines)
        if "rates" not in sections:
            errors.append("config.toml has no [rates] section")
            return Plan(text, [], errors)
        start, end = sections["rates"]
        index = {}
        for i in range(start, end):
            m = RATE_LINE_RE.match(lines[i])
            if m:
                index[m.group("ccy")] = i
        removals = []
        for ccy, r in new_rates.items():
            old = cur_rates.get(ccy)
            if r is None:
                if ccy in index:
                    removals.append(index[ccy])
                    changes.append({"section": "rates", "key": ccy, "old": old, "new": None, "kind": "remove"})
                continue
            if old == r:
                continue
            if ccy in index:
                i = index[ccy]
                tail = RATE_LINE_RE.match(lines[i]).group("tail") or ""
                lines[i] = _format_rate(ccy, r) + tail
                changes.append({"section": "rates", "key": ccy, "old": old, "new": r, "kind": "update"})
            else:
                last = max(index.values()) if index else start - 1
                lines.insert(last + 1, _format_rate(ccy, r))
                index = {k: (v + 1 if v > last else v) for k, v in index.items()}
                index[ccy] = last + 1
                removals = [v + 1 if v > last else v for v in removals]
                changes.append({"section": "rates", "key": ccy, "old": None, "new": r, "kind": "add"})
        for i in sorted(removals, reverse=True):
            del lines[i]

    new_text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    try:
        cfg = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        return Plan(text, [], [f"edited config does not parse: {exc}"])
    problems = checks.validate(cfg, set(required_currencies) | {cfg.get("valuation", {}).get("base_currency", "")})
    for ccy in (c for c, r in new_rates.items() if r is None):
        if ccy in required_currencies:
            problems.append(f"{ccy} cannot be removed: names in the current run report in it (reporting currency)")
        if ccy == cfg.get("valuation", {}).get("base_currency"):
            problems.append(f"{ccy} cannot be removed: it is the base_currency")
    if problems:
        return Plan(text, [], problems)
    return Plan(new_text, changes, [])


def save(path: Path, p: Plan, history_dir: Path) -> dict:
    """Write the plan: config.toml.bak, a dated copy in history_dir, then an atomic replace."""
    if not p.ok:
        raise ValueError("; ".join(p.errors))
    path, history_dir = Path(path), Path(history_dir)
    original = path.read_bytes()  # backups are byte-exact copies (text mode on Windows would rewrite line endings)
    newline = "\r\n" if b"\r\n" in original else "\n"  # keep the file's own line-ending style
    history_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    copy = history_dir / f"config_{stamp}.toml"
    n = 1
    while copy.exists():
        copy = history_dir / f"config_{stamp}_{n}.toml"
        n += 1
    copy.write_bytes(original)
    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_bytes(original)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(p.text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"path": str(path), "backup": str(backup), "history": str(copy)}
