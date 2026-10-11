"""Write the weekly Excel + Markdown report and keep a CSV history for week-over-week changes."""
from __future__ import annotations

import glob
import math
import json
import hashlib
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import policy

PCT = "0.0%"
PX = "#,##0.00"
COLUMNS = {  # column -> (header, number format)
    "symbol": ("Symbol", None), "name": ("Name", None), "owned": ("Owned", None),
    "position_eur": ("Position €", "#,##0"), "cost_px": ("Your avg cost", PX), "cost_score": ("Score at your cost", "0"), "class": ("Class", None), "status": ("Status", None),
    "candidate": ("Candidate", None), "entry_score": ("Entry score", "0"), "price": ("Price", PX),
    "quote_ccy": ("Ccy", None), "ceiling_px": ("Model entry estimate", PX), "reasonable_px": ("Modeled base value", PX),
    "adverse_px": ("Operating stress value", PX), "adverse_score": ("Stress score", "0"), "stress_cover": ("Bad-case cover", PCT),
    "implied_return": ("Implied cash-flow yield", PCT), "fx_parity_return": ("FX-parity scenario", PCT),
    "hurdle": ("Hurdle", PCT), "floor_rate": ("Stress rate", PCT), "caps_hit": ("Caps hit", None), "fin_type": ("Fin. type", None),
    "implied_growth": ("Implied growth", PCT), "est_growth": ("Est. growth", PCT), "adverse_growth": ("Adverse growth", PCT),
    "terminal_growth": ("Terminal growth", PCT), "return_on_capital": ("Return on capital", PCT),
    "reinvest_rate": ("Reinvestment rate", PCT), "nopat_yield": ("NOPAT / EV", PCT), "fcf_yield": ("FCF-SBC / equity cap", PCT),
    "ebit_yield": ("EBIT / EV", PCT), "evs_vs_hist": ("EV/Sales vs 5y median", "0.00"),
    "earnings_yield": ("Earnings yield", PCT), "book_yield": ("Book / Mcap", "0.00"), "roe": ("ROE", PCT),
    "equity_to_assets": ("Equity / assets", PCT), "composite": ("Peer percentile", "0"), "score_value": ("Value pct", "0"),
    "score_quality": ("Quality pct", "0"), "score_growth": ("Growth pct", "0"), "score_balance": ("Balance pct", "0"),
    "roic": ("ROIC", PCT), "roic_ex_goodwill": ("ROIC ex goodwill", PCT), "gross_profitability": ("Gross profit / assets", PCT),
    "cash_conversion": ("FCF / NI", "0.00"), "rev_cagr_3y": ("Revenue CAGR 3y", PCT),
    "analyst_growth": ("Analyst rev. growth", PCT), "net_debt_ebitda": ("Net debt / EBITDA", "0.0"),
    "share_cagr": ("Share count CAGR", PCT), "evs": ("EV / Sales", "0.0"), "cash_runway_years": ("Cash runway (y)", "0.0"),
    "mcap_eur_bn": ("Mkt cap €bn", "#,##0.0"), "currency": ("Reporting ccy", None),
    "sector": ("Sector", None), "industry": ("Industry", None), "data_as_of": ("Data as of", None),
    "lists": ("Lists", None), "flags": ("Data flags", None), "not_rankable": ("Not rankable: reason", None),
}
DECISION = ["symbol", "name", "owned", "position_eur", "cost_px", "cost_score", "class", "status", "entry_score", "price", "quote_ccy",
            "ceiling_px", "adverse_px", "stress_cover", "implied_return", "fx_parity_return", "hurdle",
            "floor_rate", "implied_growth", "est_growth", "adverse_growth", "terminal_growth", "return_on_capital",
            "reinvest_rate", "caps_hit"]
COLUMNS.update({"decision_rate":("Required rate",PCT),"discount_rate":("Valuation rate",PCT),"model_label":("Model",None),"model_family":("Business family",None),"model_quality":("Model evidence",None),
    "valuation_basis":("Valuation basis",None),"implied_return_claim":("Yield claim",None),
    "implied_growth_status":("Growth solution status",None),"implied_growth_roots":("Growth solutions",None),
    "terminal_value_fraction":("Terminal PV share",PCT),"price_as_of":("Quote as of",None),
    "country_risk_basis":("Country-risk basis",None),"statement_periods":("Statement provenance",None),
    "assumptions_source":("Issuer assumptions source",None),"reinvestment_gap":("Investment reconciliation gap",PX)})
COLUMNS.update({'model_status':('Model status',None),'valuation_range_low_px':('Scenario low',PX),
    'valuation_range_high_px':('Scenario high',PX),'upside_px':('Upside scenario',PX),
    'funding_required':('Modeled owner capital contributions',PX),'growth_measure':('Growth measure',None),
    'model_selection_reason':('Model selection',None),'model_assumptions':('Forecast assumptions',None),
    'model_uncertainties':('Model uncertainty',None),'business_profile':('Classification and business components',None),
    'implied_return_status':('Yield identification',None)})
DECISION += ["decision_rate","discount_rate","model_label","model_family","model_quality","valuation_basis","implied_return_claim","implied_growth_status",
             "implied_growth_roots","terminal_value_fraction","price_as_of","country_risk_basis","assumptions_source","reinvestment_gap"]
DECISION += ['model_status','valuation_range_low_px','valuation_range_high_px','upside_px','funding_required',
             'growth_measure','model_selection_reason','model_assumptions','model_uncertainties','implied_return_status','business_profile']

TAIL = ["composite", "score_value", "score_quality", "score_growth", "score_balance", "mcap_eur_bn", "currency",
        "sector", "industry", "data_as_of", "lists", "flags", "not_rankable"]
OPERATING_COLS = DECISION + ["nopat_yield", "fcf_yield", "ebit_yield", "evs_vs_hist", "roic", "roic_ex_goodwill", "gross_profitability",
                             "cash_conversion", "rev_cagr_3y", "analyst_growth", "net_debt_ebitda", "share_cagr"] + TAIL
FINANCIAL_COLS = DECISION + ["fin_type", "earnings_yield", "book_yield", "roe", "equity_to_assets", "rev_cagr_3y",
                             "analyst_growth", "share_cagr"] + TAIL
CANDIDATE_COLS = DECISION + ["nopat_yield", "fcf_yield", "earnings_yield", "evs_vs_hist", "roic", "roe",
                             "cash_conversion", "net_debt_ebitda", "share_cagr"] + TAIL
NOT_RANKABLE_COLS = ["symbol", "name", "owned", "class", "not_rankable", "currency", "mcap_eur_bn", "data_as_of", "flags"]
PREPROFIT_COLS = ["symbol", "name", "owned", "position_eur", "evs", "rev_cagr_3y", "analyst_growth",
                  "cash_runway_years", "share_cagr", "mcap_eur_bn", "sector", "industry", "data_as_of", "lists",
                  "flags"]
OTHER_COLS = ["symbol", "name", "owned", "position_eur", "class", "earnings_yield", "book_yield", "mcap_eur_bn",
              "lists", "flags"]

METHOD = [
    ("Purpose", "Source-labeled research triage. Prices are conditional model estimates, not probabilities or buy recommendations."),
    ("Evidence assembly", "Coherent reported quarter flows are assembled into TTM where possible. Source rows, periods, units, alternatives and disagreements are retained. Missing financial facts are not replaced by zeros."),
    ("Classification", "Provider industry labels and source-provided GICS are separate evidence. Economics choose the model; diversified groups are not assumed to be pure-sector businesses."),
    ("Operating and recovery", "Revenue, reported common net margins and equity investment generate owner cash flows. First-year expansion is funded at the opening date; later expansion is funded in advance. Negative distributions are priced capital calls. Industry references are dated estimates, not company facts."),
    ("Financial companies", "Common-equity earnings and book reinvestment generate distributions that independently reconcile to residual income. Book equity is not asserted to be regulatory capital. Missing regulatory detail is disclosed uncertainty."),
    ("Property and utilities", "Compatible reported AFFO/FFO is used when available. Otherwise common-income/cash-return and maintenance scenarios are estimated explicitly. Consolidated utility models include merchant and regulated activities without treating all assets as rate base."),
    ("Diversified businesses", "Sum-of-parts values each declared component with its own economics, claim, discount rate, ownership and currency. Component and parent claims are reconciled once. Without segment facts, a labeled consolidated common-equity model remains available."),
    ("Scenarios", "Base, stress and upside preserve assumptions and signed contributions; displayed minima/maxima are scenario envelopes, not confidence bounds. Zero means nonpositive modeled continuation value with limited liability, not proof that recovery options have no value."),
    ("Funding and claims", "Common-equity models already include financing expenses and do not subtract debt or leases a second time. Owner capital contributions reduce value. Financing availability, participation and dilution remain conditional assumptions."),
    ("Rates and currency", "A sourced equity discount rate and purchase-policy hurdle value the same path separately; both price constraints apply. WACC requires a firm model. Missing local rates use an explicit constant-FX/base-currency policy scenario. Component-model discount rates are declared separately."),
    ("Ranking", "A business is not excluded merely because its sector needs a different equation or a forecast estimate. Missing essentials, invalid economics and unresolved used-source contradictions can still prevent a value. Quote/unit/ownership and freshness flags can block candidate status while retaining conditional estimates."),
    ("Uncertainty", "The inspector and exports show assumptions, model selection, scenario ranges and capital contributions. Retired funded-proxy knobs are no longer active controls. Estimates are independent of which peers appear in a watchlist."),
    ("Validation boundary", "Accounting identities, adverse cases and held-out issuers validate mechanics and generality. They do not establish forecast accuracy or predictive investment performance. Experimental probability models remain outside production."),
]



def _fmt_sheet(ws, cols: list[str], highlight: list[str]):
    header_fill = PatternFill("solid", fgColor="1F4E79")
    for i, c in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=i)
        cell.value = COLUMNS.get(c, (c, None))[0]
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        fmt = COLUMNS.get(c, (c, None))[1]
        letter = get_column_letter(i)
        ws.column_dimensions[letter].width = 34 if c in ("name", "flags", "lists", "not_rankable") else (18 if c in ("industry", "sector") else 11)
        if fmt:
            for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                row[0].number_format = fmt
        if c in highlight and ws.max_row > 2:
            ws.conditional_formatting.add(
                f"{letter}2:{letter}{ws.max_row}",
                ColorScaleRule(start_type="percentile", start_value=5, start_color="F8696B", mid_type="percentile",
                               mid_value=50, mid_color="FFFFFF", end_type="percentile", end_value=95, end_color="63BE7B"))
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    ws.row_dimensions[1].height = 32


def _write(writer, df: pd.DataFrame, sheet: str, cols: list[str], sort: list[str], highlight: list[str]):
    cols = [c for c in cols if c in df.columns]
    sort = [s for s in sort if s in df.columns]
    view = df.sort_values(sort, ascending=False, na_position="last")[cols] if len(df) and sort else df[cols]
    view.to_excel(writer, sheet_name=sheet, index=False)
    _fmt_sheet(writer.sheets[sheet], cols, highlight)


def _run_date(path) -> str:
    return Path(path).stem[len("screen_"):][:10]  # screen_YYYY-MM-DD[_n]


def previous_run(history_dir: Path, today: str) -> Path | None:
    """Latest history file from a day other than today (reruns on the same day never become the baseline)."""
    files = sorted(Path(f) for f in glob.glob(str(Path(history_dir) / "screen_*.csv")) if _run_date(f) != today)
    return files[-1] if files else None


def run_stem(history_dir: Path, today: str) -> str:
    """screen_<today>, or screen_<today>_<n> if that day already has runs. Nothing is ever overwritten."""
    n = len(glob.glob(str(Path(history_dir) / f"screen_{today}*.csv")))
    return f"screen_{today}" if n == 0 else f"screen_{today}_{n}"


def changes_vs_previous(df: pd.DataFrame, history_dir: Path, today: str) -> pd.DataFrame:
    prev_file = previous_run(history_dir, today)
    if prev_file is None:
        return pd.DataFrame()
    prev = pd.read_csv(prev_file)
    prev_date = _run_date(prev_file)
    keep = ["entry_score", "ceiling_px", "price", "candidate", "status"]
    for c in keep:
        if c not in prev:
            prev[c] = math.nan
    m = df.merge(prev[["symbol"] + keep], on="symbol", how="outer", suffixes=("", "_prev"))
    both = m["status"].notna() & m["status_prev"].notna()
    shift = float((m.loc[both, "status"] != m.loc[both, "status_prev"]).mean()) if both.any() else 0.0
    m["score_change"] = m["entry_score"] - m["entry_score_prev"]
    m["price_change"] = m["price"] / m["price_prev"] - 1
    m["ceiling_change"] = m["ceiling_px"] / m["ceiling_px_prev"] - 1
    cand = m["candidate"].fillna(False).astype(bool)
    cand_prev = m["candidate_prev"].fillna(False).astype(bool)
    m["event"] = ""
    m.loc[cand & ~cand_prev, "event"] = "new candidate"
    m.loc[~cand & cand_prev, "event"] = "dropped as candidate"
    m.loc[m["entry_score_prev"].isna() & m["entry_score"].notna(), "event"] = "new or newly rankable"
    m.loc[m["entry_score_prev"].notna() & m["entry_score"].isna(), "event"] = "no longer rankable"
    m["reason"] = ""
    moved = m["score_change"].notna()
    m.loc[moved & (m["price_change"].abs() >= m["ceiling_change"].abs()), "reason"] = "price"
    m.loc[moved & (m["price_change"].abs() < m["ceiling_change"].abs()), "reason"] = "ceiling (fundamentals, FX or assumptions)"
    big = m["score_change"].abs() >= 10
    out = m[(m["event"] != "") | big].copy()
    out["previous_run"] = prev_date
    out = out[["symbol", "name", "event", "reason", "entry_score_prev", "entry_score", "score_change", "price_prev",
               "price", "price_change", "ceiling_px_prev", "ceiling_px", "ceiling_change", "previous_run"]
              ].sort_values("score_change", key=lambda s: s.abs(), ascending=False)
    out.attrs["status_shift"] = shift
    out.attrs["previous_run"] = prev_date
    return out


def write(df: pd.DataFrame, resolution: pd.DataFrame, out_dir: Path, history_dir: Path, cfg: dict, *, run_date:date|None=None) -> tuple[Path, Path]:
    today = (run_date or date.today()).isoformat()
    out_dir, history_dir = Path(out_dir), Path(history_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    changes = changes_vs_previous(df, history_dir, today)
    stem = run_stem(history_dir, today)
    df.to_csv(history_dir / f"{stem}.csv", index=False)
    provenance=history_dir/"provenance"
    provenance.mkdir(exist_ok=True)
    sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")}
    resources={str(p.relative_to(Path(__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (Path(__file__).parent/'resources').glob('*.json')}
    with (provenance/f"{stem}.json").open("x",encoding="utf-8") as fh:
        json.dump({"run":stem,"date":today,"configuration":cfg,"implementation_sha256":sources,'resource_sha256':resources,
                   "evidence_columns":[c for c in df.columns if c.startswith("source_") or c.startswith("filing_") or c in ("statement_periods","quote_metadata","price_as_of","fx_observations")]},fh,indent=2,default=str)


    ops = df[df["class"].isin(["operating", "cyclical", "asset"])]
    fin = df[df["class"] == "financial"]
    pre = df[df["class"] == "preprofit"]
    blocked = df[df["status"] == "not rankable"] if "status" in df else df.iloc[0:0]
    other = df[~df["class"].isin(["operating", "cyclical", "asset", "financial", "preprofit"])]
    cands = df[df["candidate"].fillna(False).astype(bool)]
    if len(resolution):
        auto = ~resolution["method"].isin(["override", "given"])  # deliberate mappings need no review
        sym_check = resolution[auto & ((resolution["similarity"] < 0.5) | (resolution["yahoo"] == ""))]
    else:
        sym_check = resolution

    v = cfg["valuation"]
    xlsx = out_dir / f"{stem}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        _write(w, cands, "Candidates", CANDIDATE_COLS, ["entry_score"], ["entry_score", "implied_return"])
        _write(w, ops, "Operating", OPERATING_COLS, ["entry_score"], ["entry_score", "implied_return", "composite"])
        _write(w, fin, "Financials", FINANCIAL_COLS, ["entry_score"], ["entry_score", "implied_return", "composite"])
        _write(w, blocked, "Not rankable", NOT_RANKABLE_COLS, ["mcap_eur_bn"], [])
        _write(w, pre, "Loss-making", PREPROFIT_COLS, ["rev_cagr_3y"], [])
        _write(w, other, "Holdcos & funds", OTHER_COLS, ["mcap_eur_bn"], [])
        (changes if len(changes) else pd.DataFrame({"note": ["No previous run to compare with."]})).to_excel(
            w, sheet_name="Changes", index=False)
        sym_check.to_excel(w, sheet_name="Symbol check", index=False)
        rates = ", ".join(f"{k} {r['rf']:.2%}/{r['g']:.1%}" for k, r in sorted(cfg.get("rates", {}).items()))
        pd.DataFrame(METHOD + [("Equity risk premium", f"{v['equity_risk_premium']:.2%}"),
                               ("Stress discount basis", "same as base"),
                               ("Scenario basis", "Observed company ranges, compatible estimates and dated references; see each model's assumptions"),
                               ("Horizon", f"{v['years']} years"),
                               ("Rates (10y yield / terminal g)", rates),
                               ("Model policy", ", ".join(f"{k} {x:g}" for k, x in policy.describe().items()))],
                     columns=["Item", "Definition"]).to_excel(w, sheet_name="Method", index=False)
        w.sheets["Method"].column_dimensions["A"].width = 24
        w.sheets["Method"].column_dimensions["B"].width = 150

    md = out_dir / f"{stem}.md"
    md.write_text(_markdown(df, cands, ops, fin, pre, blocked, changes, sym_check, cfg, today), encoding="utf-8")
    return xlsx, md


def _p(x, digits=1):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.{digits}f}%"


def _n(x, digits=0):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def _g(r, col, default=math.nan):
    v = getattr(r, col, default)
    return default if v is None else v


def _table(df: pd.DataFrame, limit: int) -> str:
    lines = ["| # | Symbol | Name | Status | Score | Price | Max entry | Bad-case cover | Impl. return | Hurdle | Est. g | Adv. g | R | Owned | Flags |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(df.head(limit).itertuples(), start=1):
        lines.append(f"| {i} | {r.symbol} | {str(r.name)[:30]} | {_g(r, 'status', '')} | {_n(_g(r, 'entry_score'))} | "
                     f"{_n(_g(r, 'price'), 2)} | {_n(_g(r, 'ceiling_px'), 2)} | {_p(_g(r, 'stress_cover'))} | "
                     f"{_p(_g(r, 'implied_return'))} | {_p(_g(r, 'hurdle'))} | {_p(_g(r, 'est_growth'))} | "
                     f"{_p(_g(r, 'adverse_growth'))} | {_p(_g(r, 'return_on_capital'))} | "
                     f"{'yes' if _g(r, 'owned', False) else ''} | {str(_g(r, 'flags', '') or '')[:50]} |")
    return "\n".join(lines)


def _markdown(df, cands, ops, fin, pre, blocked, changes, sym_check, cfg, today) -> str:
    owned = df[df["owned"].fillna(False).astype(bool)]
    out = [f"# Watchlist screen — {today}", ""]
    shift = changes.attrs.get("status_shift", 0.0) if len(changes) else 0.0
    if shift > 0.25:
        out += [f"**Large shift:** {shift:.0%} of statuses changed since the previous run ({changes.attrs.get('previous_run')}). "
                "That is usually a data or config problem, not a market move. Check the Data flags and Not rankable sheets before reading the list.", ""]
    out += [f"Universe {len(df)} names: {len(ops)} operating/cyclical/asset, {len(fin)} financial, {len(pre)} other loss-making, "
            f"{len(blocked)} not rankable, {len(df) - len(ops) - len(fin) - len(pre)} holdco/fund/other.", "",
           "This is a triage list, not a buy list: candidates need a full valuation before any decision.", "",
           "Score = 100 × (1 − price / model entry estimate). The entry estimate applies the stated valuation and purchase-policy assumptions. "
           "Bad-case cover is the stress value as a share of the price; for operating companies the stress value is the backtested one-in-four bad outcome of companies with a similar stress scenario. Implied flow yield is reported only when the priced distribution path identifies a unique yield; "
           "signed future capital calls can leave yield unidentified while still supporting valuation. Growth measures and cash-flow claims depend on the selected model. "
           "Zero is the limited-liability endpoint of a nonpositive modeled continuation value. Scenario ranges are not confidence bounds.", "",
           f"## Candidates ({len(cands)})", "",
           "Price at or below the max entry price, no critical data flag.", "",
           _table(cands.sort_values("entry_score", ascending=False), 30) if len(cands) else "_None this week._", "",
           "## Closest to the ceiling: operating companies", "",
           _table(ops[ops["entry_score"].notna()].sort_values("entry_score", ascending=False), 15), "",
           "## Financials", "",
           _table(fin[fin["entry_score"].notna()].sort_values("entry_score", ascending=False), 10) if len(fin) else "_None._", "",
           "## Your holdings", "",
           _table(owned.sort_values("entry_score", ascending=False, na_position="last"), 60), ""]
    if len(changes):
        out += ["## Changes since previous run", "", "| Symbol | Event | Reason | Score | Î” | Price Î” | Ceiling Î” |", "|---|---|---|---|---|---|---|"]
        for r in changes.head(25).itertuples():
            out.append(f"| {r.symbol} | {r.event} | {r.reason} | {_n(r.entry_score)} | {_n(r.score_change)} | "
                       f"{_p(r.price_change)} | {_p(r.ceiling_change)} |")
        out.append("")
    if len(blocked):
        out += [f"## Not rankable ({len(blocked)})", ""]
        for r in blocked.itertuples():
            out.append(f"- {r.symbol} {str(r.name)[:30]}: {r.not_rankable}")
        out.append("")
    if len(sym_check):
        out += [f"## Symbol checks needed ({len(sym_check)})", "",
                "Low name similarity or unresolved: verify and add corrections to `overrides/symbols.csv`.", ""]
        for r in sym_check.itertuples():
            out.append(f"- `{r.key}` {r.name} → `{r.yahoo or '??'}` ({r.yahoo_name})")
        out.append("")
    flagged = df[df["flags"].fillna("").str.contains("no SBC")]
    out += [f"## Data caveats", "",
            f"- {len(flagged)} names lack stock-compensation data (mostly non-US); their FCF yield is flattered. NOPAT already expenses SBC.",
            "- Yahoo data is unofficial and unaudited; verify any candidate against filings before acting.",
            "- The rates table in config.toml drives hurdles per currency; keep it current.", ""]
    return "\n".join(out)
