# Tripwire

A weekly entry-price screener for a stock watchlist, built on free Yahoo Finance data. Every company gets a **max entry price** from a funded-growth valuation; Tripwire fires on the names trading at or below it, and a local dashboard shows how far the rest would have to fall. You provide two CSV files: a watchlist and, optionally, your holdings.

It is a **triage tool**. It decides which companies deserve a full valuation; it does not produce buy signals.

## Setup

```powershell
git clone https://github.com/EGZX/tripwire.git
cd tripwire
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
mkdir input
copy examples\watchlist.csv input\          # then replace with your own companies
copy examples\holdings.csv input\           # optional: your positions
```

On macOS or Linux use `python3.12 -m venv .venv`, `.venv/bin/python` and `cp`.

## Run

```powershell
.venv\Scripts\python -m screener                 # weekly run; uses cached fundamentals less than 6 days old
.venv\Scripts\python -m screener --refresh       # refetch everything
.venv\Scripts\python -m screener --only AAPL,SIE.DE   # a few symbols (results are identical to the full run)
.venv\Scripts\python -m screener --resolve-only  # only map tickers to Yahoo symbols
```

Alternatively, run `run_weekly.bat`. It writes a log to `reports\logs\`.

**Runtime:** fundamentals are cached; dated quotes and current share/capitalization metadata are refreshed per symbol on each run. Additional statement/analyst requests can take longer than earlier versions. A failed refresh is exposed, and stale data cannot silently become a candidate.

**Output, in `reports/`:**
- `screen_YYYY-MM-DD.xlsx` with these sheets:
  - Candidates: price at or below the max entry price
  - Operating, Financials: every ranked name with its ceiling, scenarios and context metrics
  - Not rankable: names missing an essential input, with the reason
  - Loss-making, Holdcos & funds
  - Changes (vs the previous run, with the reason: price or ceiling)
  - Symbol check
  - Method
- `screen_YYYY-MM-DD.md`: a short summary.

A CSV copy of each run is kept in `data/history/`. Runs are never overwritten: a second run on the same day gets a `_1`, `_2` suffix, and the Changes sheet always compares against the latest run from a *different* day. If more than a quarter of statuses changed since that run, the summary opens with a **Large shift** warning, which usually means a data or config problem. `data/reference/` keeps the 2026-10-10 runs of the old percentile model and the first entry-ceiling version for comparison.

## Inputs

| What | Where | Notes |
|---|---|---|
| Watchlist | `input/watchlist.csv` | Columns `symbol,name,isin,yahoo_symbol,lists`. Give at least one of `symbol`, `isin` or `yahoo_symbol` per company; a `yahoo_symbol` is used as is, anything else is resolved to a Yahoo listing (the name helps). `lists` takes free labels separated by `;` (e.g. `Core; Semis`), which become filters in the report and dashboard. Template: `examples/watchlist.csv`. |
| Holdings | `input/holdings.csv` | Optional. Columns `isin,symbol,name,shares,value_eur,cost_eur`: current position value and the euro cost basis of the shares held. A holding attaches to the watchlist row with the same ISIN or symbol (else a unique name match) and is screened on its own if not on the watchlist. With `cost_eur`, holdings get a *score at your cost*: your average buy price against today's max entry price. Template: `examples/holdings.csv`. |
| Symbol fixes | `overrides/symbols.csv` | Maps `source_key` (a watchlist symbol or an ISIN) to a Yahoo symbol, or to `SKIP`. Rows you would rather not share (for example ISINs of your own positions) go in `overrides/symbols.local.csv`, which is read too and git-ignored. Check the "Symbol check" sheet after adding names. |
| Class fixes | `overrides/classification.csv` | Forces `operating`, `cyclical`, `financial`, `holdco` or `fund`. Personal rows can go in the git-ignored `overrides/classification.local.csv`. |
| Parameters | `config.toml` | Purchase-policy parameters, the **currency rates table** (10-year yield, terminal growth, `as_of`), and separate **country-risk assumptions**. Update the table when rates move, by hand or in the dashboard's Method view (see `dashboard/README.md`). The run refuses to start if a reporting currency in the universe is missing from it or a rate is malformed. |

`input/` is git-ignored: your watchlist and positions never leave your machine. The shared `overrides/` files are versioned.

## Method

Tripwire separates a **purchase-policy ceiling** from an independently supported intrinsic valuation. The automatic operating-company path is explicitly an **accounting proxy**, not a universal fair-value model. Every value shows its model, evidence basis, cash-flow claim and source dates.

**The data must reconcile.** Each statement uses one coherent period; financial companies use net income without requiring EBIT. Missing debt, equity or noncommon claims are not filled with zero. Quotes refresh capitalization and share metadata together. Unresolved currency units, ownership scope, stale observations and missing essentials prevent candidate status.

**Growth must be funded.** Earnings-based projections validate every year's reinvestment and terminal funding. Falling earnings do not automatically release capital. Accounting ROIC includes acquisition capital; absent sourced alternatives, its terminal return fades to the lower of the current proxy and the policy hurdle. Base and operating-stress cases use the same rate. A stress that raises value is reported as a scenario crossing, not forced into a downside number.

**Business economics choose the model.** Source-labeled issuer inputs support operating revenue/margin forecasts (including funded recovery), bank and insurer capital distributions, REIT AFFO, regulated rate-base cash flows, attributable NAV and finite-life projects. Banks, property businesses, utilities and hybrids without their required inputs abstain. Short-window cyclical proxies are flagged. See [the complete contracts and limitations](docs/valuation-method.md) and [fictional input examples](examples/valuation.toml).

**One absolute score:** `100 × (1 − market cap / positive policy equity ceiling)`, displayed with a −100 floor; the raw score is retained. Scores and eligibility do not depend on which other stocks are in the run. Peer percentiles remain context only.

**Geography is separate from currency.** Reporting currency sets nominal consistency. Sourced country exposures or an issuer premium override the labeled provider-country-risk policy. The optional FX-parity scenario is not an expected EUR return or an executed hedge.

**Inverse results expose ambiguity.** A unique implied growth/yield is shown only when solved and residual-checked. Multiple roots, weak identification, invalid inputs and absent solutions are distinct states. Firm implied yields are not labeled shareholder returns.

## Sources and reproducibility

- `overrides/valuation.toml` and optional git-ignored `valuation.local.toml` hold sourced issuer facts and base/stress assumptions. No example assumptions are loaded automatically.
- yfinance supplies annual/trailing/quarterly statements, analyst ranges/counts, earnings estimates and revisions. Coverage varies; missing data is visible.
- An explicit `sec_cik` plus `SEC_USER_AGENT` enables optional SEC companyfacts retrieval. This is a limited standard US-GAAP balance-fact mapper, not automatic global or regulatory-capital extraction. Configuration/access failures are visible.
- Raw fetched fundamentals and optional filings are saved as immutable snapshots. Each run retains its configuration, code hashes, statement periods and quote/FX observations.
- Experimental inference, cycle estimation and probabilistic ranking are kept outside production in [research/README.md](research/README.md).

## Known limits

Automatic values still use accounting capital, a revenue-growth proxy, effective taxes and a simplified equity bridge. Maintenance investment, restricted cash, pensions, dilution, geographic operations, acquisition economics and marginal returns need issuer-specific evidence. Explicit sector models expose these requirements; they do not invent missing financial facts.

Neither these valuation assumptions nor candidate selection has been validated as predictive of future stock returns. Free timestamped filings can support limited point-in-time fundamental analysis, but complete historical global consensus, delisted stocks and contemporaneous universes remain separate data requirements. Numerical tests and a green regression snapshot do not establish investment performance.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

`tests/test_golden.py` runs the model over 14 frozen Yahoo records in `tests/fixtures/cache/` and compares statuses and scores with `tests/golden/snapshot.json`. Any behaviour change fails it on purpose; after reviewing the diff, regenerate with `UPDATE_GOLDEN=1` in the environment. The fixtures are raw Yahoo data and are not distributed, so the test skips on a fresh clone; to build your own set, copy a few files from `data/cache/` into `tests/fixtures/cache/` after a run and generate the snapshot with `UPDATE_GOLDEN=1`. `yfinance` is pinned in `pyproject.toml`; bump it deliberately and rerun the golden test.

## License

MIT, see [LICENSE](LICENSE). The bundled IBM Plex fonts (`dashboard/static/fonts/`) are under the SIL Open Font License, included alongside them.

Tripwire is a screening aid, not investment advice. Yahoo Finance data is unofficial and can be wrong or incomplete; verify anything that matters against company filings.

Offline real-record validation (no downloads or golden regeneration):

```powershell
.venv\Scripts\python tools/validate_corpus.py --valuation-date YYYY-MM-DD --cache-dir PATH_TO_SAVED_CACHE --fx-json PATH_TO_FROZEN_FX_JSON --output-dir data/validation/corpus
```

FX JSON maps `FROM/TO` to positive rates. Preserve source/as-of evidence in its optional `_provenance` object. Review intentional golden changes before regenerating only the active checkout's local snapshot.
