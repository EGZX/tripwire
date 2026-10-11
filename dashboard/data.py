"""Read-only access to the screener's run history (data/history/screen_YYYY-MM-DD.csv) and config.toml.

The dashboard never writes into the screener's directories. Every run CSV is the full scored frame the
screener wrote, so the dashboard depends only on that output, not on the screener's internals.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
import tomllib
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import configedit, inputedit

RUN_RE = re.compile(r"^screen_(\d{4}-\d{2}-\d{2}(?:_\d+)?)\.csv$")  # same-day reruns get _1, _2, ...
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:_\d+)?$")


def run_key(run: str) -> tuple[str, int]:
    """Sort key: date, then rerun number (plain date = 0), so _10 sorts after _2."""
    day, _, n = run.partition("_")
    return day, int(n) if n else 0


SCORED_STATUSES = ("eligible", "weak downside", "data flag", "price above ceiling")
# per-symbol fields tracked across runs
TRACKED = ["price", "ceiling_px", "reasonable_px", "adverse_px", "entry_score", "status", "quote_ccy",
           "implied_return", "hurdle", "adverse_score", "stress_cover"]


def _clean(v):
    """JSON-safe scalar: NaN -> None, Â±inf -> "+inf"/"-inf" (an unbounded value is information), numpy -> python."""
    if v is None:
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None if math.isnan(v) else ("+inf" if v > 0 else "-inf")
    return v


def records(df: pd.DataFrame) -> list[dict]:
    cols = list(df.columns)
    return [{c: _clean(v) for c, v in zip(cols, row)} for row in df.itertuples(index=False, name=None)]


class Store:
    """Caches parsed run CSVs by (path, mtime) so a re-run of the screener shows up without a restart."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.history_dir = self.root / "data" / "history"
        self.config_path = self.root / "config.toml"
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._lock = threading.RLock()  # re-entrant: edit_config holds it while load() takes it too

    # ---------- runs
    def run_dates(self) -> list[str]:
        if not self.history_dir.is_dir():
            return []
        return sorted((m.group(1) for p in self.history_dir.iterdir() if (m := RUN_RE.match(p.name))), key=run_key)

    def load(self, run: str) -> pd.DataFrame:
        if not DATE_RE.match(run or ""):
            raise KeyError(run)
        path = self.history_dir / f"screen_{run}.csv"
        if not path.is_file():
            raise KeyError(run)
        mtime = path.stat().st_mtime
        with self._lock:
            hit = self._cache.get(run)
            if hit and hit[0] == mtime:
                return hit[1]
        df = normalise(pd.read_csv(path))
        with self._lock:
            self._cache[run] = (mtime, df)
        return df

    def previous(self, run: str) -> str | None:
        earlier = [d for d in self.run_dates() if run_key(d) < run_key(run)]
        return earlier[-1] if earlier else None

    def run_written_at(self, run: str) -> str | None:
        path = self.history_dir / f"screen_{run}.csv"
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="minutes") if path.is_file() else None

    # ---------- payloads
    def runs_payload(self) -> dict:
        out = []
        for d in self.run_dates():
            try:
                out.append({"date": d, **summary(self.load(d))})
            except Exception as exc:  # a malformed file must not take the dashboard down
                out.append({"date": d, "error": f"{type(exc).__name__}: {exc}"})
        return {"runs": out, "today": date.today().isoformat(), "root": str(self.root)}

    def run_payload(self, run: str, prev: str | None = None) -> dict:
        df = self.load(run)
        prev = prev if prev and prev != run else self.previous(run)
        prev_df = self.load(prev) if prev else None
        rows = with_changes(df, prev_df)
        reports = self.root / "reports"
        return {
            "date": run,
            "previous": prev,
            "written_at": self.run_written_at(run),
            "summary": summary(df),
            "columns": list(df.columns),
            "rows": records(rows),
            "removed": removed_since(df, prev_df),
            "report_xlsx": str(reports / f"screen_{run}.xlsx") if (reports / f"screen_{run}.xlsx").is_file() else None,
            "report_md": str(reports / f"screen_{run}.md") if (reports / f"screen_{run}.md").is_file() else None,
        }

    def symbol_history(self, symbol: str) -> dict:
        points = []
        for d in self.run_dates():
            df = self.load(d)
            hit = df[df["symbol"] == symbol]
            if len(hit):
                r = hit.iloc[0]
                points.append({"date": d, **{c: _clean(r[c]) if c in r else None for c in TRACKED}})
        return {"symbol": symbol, "points": points}

    def config_payload(self) -> dict:
        if not self.config_path.is_file():
            return {"available": False}
        from screener.report import METHOD
        from screener.scoring import CRITICAL_FLAGS
        raw = self.config_path.read_bytes()
        cfg = tomllib.loads(raw.decode("utf-8"))
        v = cfg.get("valuation", {})
        today = date.today().isoformat()
        rates = [{"currency": ccy, **derived_rates(r, v), "as_of": r.get("as_of"), "age_days": configedit.age_days(r.get("as_of"), today)}
                 for ccy, r in sorted(cfg.get("rates", {}).items())]
        return {"available": True, "method": METHOD, "valuation": v, "scoring": cfg.get("scoring", {}), "rates": rates,
                "critical_flags": list(CRITICAL_FLAGS),
                "editable_keys": configedit.EDITABLE_VALUATION, "rate_ranges": configedit.RATE_RANGES,
                "sha": hashlib.sha256(raw).hexdigest(),
                "modified": datetime.fromtimestamp(self.config_path.stat().st_mtime).isoformat(timespec="minutes")}

    # ---------- input lists (watchlist.csv, holdings.csv)
    def input_paths(self) -> dict[str, Path]:
        ic = {}
        if self.config_path.is_file():
            with open(self.config_path, "rb") as fh:
                ic = tomllib.load(fh).get("inputs", {})
        return {"watchlist": self.root / ic.get("watchlist", "input/watchlist.csv"),
                "holdings": self.root / ic.get("holdings", "input/holdings.csv")}

    def resolved_symbols(self) -> dict[str, str]:
        """Input key -> Yahoo symbol from the screener's symbol cache and overrides, to link rows to run results."""
        from screener.resolve import load_cache, load_overrides  # the dashboard ships with the screener
        out = {k: (r.get("yahoo") or "") for k, r in load_cache(self.root / "data" / "symbol_map.csv").items()}
        out.update(load_overrides(self.root / "overrides" / "symbols.csv"))
        return out

    def inputs_payload(self) -> dict:
        out = {"resolved": self.resolved_symbols()}
        for kind, path in self.input_paths().items():
            raw = path.read_bytes() if path.is_file() else b""
            rows = inputedit.read_rows(path)
            if kind == "holdings":
                for r in rows:
                    for k in ("shares", "value_eur", "cost_eur"):
                        try:
                            r[k] = float(r[k].replace(",", "")) if r.get(k) else None
                        except ValueError:
                            r[k] = None
            out[kind] = {"rows": rows, "sha": hashlib.sha256(raw).hexdigest(), "path": str(path.relative_to(self.root))}
        return out

    def edit_inputs(self, kind: str, rows, dry_run: bool, base_sha: str | None) -> tuple[dict, int]:
        paths = self.input_paths()
        if kind not in paths:
            return {"ok": False, "errors": [f"unknown list {kind!r}"]}, 400
        try:
            clean = inputedit.clean_rows(kind, rows)
        except ValueError as exc:
            return {"ok": False, "errors": str(exc).split("; ")}, 400
        with self._lock:
            path = paths[kind]
            raw = path.read_bytes() if path.is_file() else b""
            if base_sha and base_sha != hashlib.sha256(raw).hexdigest():
                return {"ok": False, "conflict": True, "errors": [f"{path.name} changed since this page loaded it; "
                                                                  "reload and redo the edit"]}, 409
            d = inputedit.diff(kind, inputedit.read_rows(path), clean)
            out = {"ok": True, "diff": d, "rows": len(clean), "written": False}
            if dry_run or not any(d.values()):
                return out, 200
            out.update(written=True, **inputedit.save(path, kind, clean, self.root / "data" / "input-history"))
        return out, 200

    def universe_currencies(self) -> set[str]:
        """Reporting currencies in the latest run (these cannot be removed from the rates table)."""
        dates = self.run_dates()
        if not dates:
            return set()
        df = self.load(dates[-1])
        return {str(c) for c in df["currency"].dropna().unique() if str(c)}

    def edit_config(self, valuation: dict, rates: dict, dry_run: bool, base_sha: str | None = None) -> tuple[dict, int]:
        """Plan (and unless dry_run, write) a config edit. Returns (payload, http status).

        Read, plan and write happen under one lock so two saves cannot overwrite each other. base_sha is the hash of
        the config.toml the page was showing; a save against a file that changed since then (another tab, a hand
        edit) is refused with 409 instead of being applied on top of text nobody reviewed."""
        if not self.config_path.is_file():
            return {"ok": False, "errors": ["config.toml not found"], "changes": [], "written": False}, 404
        with self._lock:
            raw = self.config_path.read_bytes()
            if base_sha and base_sha != hashlib.sha256(raw).hexdigest():
                return {"ok": False, "errors": ["config.toml changed since this page loaded it; reload and redo the edit"],
                        "changes": [], "written": False, "conflict": True}, 409
            p = configedit.plan(raw.decode("utf-8"), valuation or {}, rates or {}, self.universe_currencies())
            out = {"ok": p.ok, "errors": p.errors, "changes": p.changes, "written": False, "rerun_needed": True}
            if not p.ok:
                return out, 400
            if dry_run or not p.changes:
                return out, 200
            res = configedit.save(self.config_path, p, self.root / "data" / "config-history")
        out.update(written=True, **res)
        return out, 200


# ---------- pure functions (unit-tested)
def derived_rates(r: dict, v: dict) -> dict:
    """Currency-only policy preview; issuer country exposure is added per company."""
    from screener.checks import policy_rates
    return policy_rates(r,v)


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce the columns the dashboard relies on, tolerating older or newer CSV schemas."""
    df = df.copy()
    for col in ("symbol", "name", "class", "status", "flags", "not_rankable", "lists", "quote_ccy", "currency"):
        if col not in df:
            df[col] = None
    df["symbol"] = df["symbol"].fillna("").astype(str)
    for col in ("owned", "candidate"):
        df[col] = df[col].map(_truthy) if col in df else False
    for col in ('entry_score','price','ceiling_px','position_eur'):
        if col not in df:
            df[col]=math.nan
    if "status" in df and df["status"].isna().all():
        df["status"] = None
    return df


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else False


def summary(df: pd.DataFrame) -> dict:
    status = df["status"].fillna("") if "status" in df else pd.Series("", index=df.index)
    score = pd.to_numeric(df.get("entry_score"), errors="coerce")
    scored = score[status.isin(SCORED_STATUSES)]
    owned = df["owned"].astype(bool)
    pos = pd.to_numeric(df.get("position_eur"), errors="coerce").fillna(0.0)
    owned_scored = owned & status.isin(SCORED_STATUSES)
    return {
        "universe": int(len(df)),
        "candidates": int(df["candidate"].astype(bool).sum()),
        "by_status": {k: int(v) for k, v in status.replace("", "unknown").value_counts().items()},
        "by_class": {k: int(v) for k, v in df["class"].fillna("unknown").value_counts().items()},
        "median_score": _clean(scored.median()) if len(scored) else None,
        "owned": int(owned.sum()),
        "owned_value_eur": _clean(pos[owned].sum()),
        "owned_scored_value_eur": _clean(pos[owned_scored].sum()),
        "owned_at_or_below_ceiling_eur": _clean(pos[owned_scored & (score >= 0)].sum()),
        "oldest_data_as_of": _clean(df["data_as_of"].dropna().min()) if "data_as_of" in df and df["data_as_of"].notna().any() else None,
    }


def with_changes(df: pd.DataFrame, prev: pd.DataFrame | None) -> pd.DataFrame:
    """Attach previous-run values and the change classification used by the screener's Changes sheet.

    event: new candidate / dropped as candidate / new or newly rankable / no longer rankable / new in universe.
    reason: 'price' when the price moved at least as much as the ceiling (in relative terms), else 'ceiling'.
    """
    out = df.copy()
    cols = ["entry_score", "ceiling_px", "price", "candidate", "status"]
    if prev is None or not len(prev):
        for c in cols:
            out[f"{c}_prev"] = None
        for c in ("score_change", "price_change", "ceiling_change"):
            out[c] = math.nan
        out["event"] = ""
        out["reason"] = ""
        return out
    p = prev[prev["symbol"] != ""].drop_duplicates("symbol")
    p = p[["symbol"] + [c for c in cols if c in p]].rename(columns={c: f"{c}_prev" for c in cols})
    for c in cols:
        if f"{c}_prev" not in p:
            p[f"{c}_prev"] = math.nan
    has_sym = out["symbol"] != ""
    m = out.merge(p, on="symbol", how="left") if has_sym.all() else pd.concat(
        [out[has_sym].merge(p, on="symbol", how="left"), out[~has_sym]], ignore_index=True)
    num = lambda c: pd.to_numeric(m[c], errors="coerce")
    m["score_change"] = num("entry_score") - num("entry_score_prev")
    m["price_change"] = num("price") / num("price_prev") - 1
    m["ceiling_change"] = num("ceiling_px") / num("ceiling_px_prev") - 1
    known = m["symbol"].isin(set(p["symbol"]))
    cand = m["candidate"].map(_truthy)
    cand_prev = m["candidate_prev"].map(_truthy)
    es, es_prev = num("entry_score"), num("entry_score_prev")
    m["event"] = ""
    m.loc[known & cand & ~cand_prev, "event"] = "new candidate"
    m.loc[known & ~cand & cand_prev, "event"] = "dropped as candidate"
    m.loc[known & es_prev.isna() & es.notna(), "event"] = "newly rankable"
    m.loc[known & es_prev.notna() & es.isna(), "event"] = "no longer rankable"
    m.loc[~known & (m["symbol"] != ""), "event"] = "new in universe"
    moved = m["score_change"].notna()
    by_price = m["price_change"].abs() >= m["ceiling_change"].abs()
    m["reason"] = ""
    m.loc[moved & by_price, "reason"] = "price"
    m.loc[moved & ~by_price, "reason"] = "ceiling"
    return m


def removed_since(df: pd.DataFrame, prev: pd.DataFrame | None) -> list[dict]:
    """Names in the previous run that are gone from this one."""
    if prev is None:
        return []
    gone = prev[(prev["symbol"] != "") & ~prev["symbol"].isin(set(df["symbol"]))]
    return records(gone[[c for c in ("symbol", "name", "entry_score", "status") if c in gone]])
