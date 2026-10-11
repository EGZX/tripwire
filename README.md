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
| Holdings | `input/holdings.csv` | Optional. Columns `isin,symbol,name,yahoo_symbol,shares,value_eur,cost_eur`: the euro cost basis of the shares held and, optionally, the current value (left empty, each run values the position at shares × today's price). A holding attaches to the watchlist row with the same ISIN or symbol (else a unique name match) and is screened on its own if not on the watchlist. With `cost_eur`, holdings get a *score at your cost*: your average buy price against today's max entry price. Template: `examples/holdings.csv`. |
| Symbol fixes | `overrides/symbols.csv` | Maps `source_key` (a watchlist symbol or an ISIN) to a Yahoo symbol, or to `SKIP`. Rows you would rather not share (for example ISINs of your own positions) go in `overrides/symbols.local.csv`, which is read too and git-ignored. Check the "Symbol check" sheet after adding names. |
| Class fixes | `overrides/classification.csv` | Forces `operating`, `cyclical`, `financial`, `holdco` or `fund`. Personal rows can go in the git-ignored `overrides/classification.local.csv`. |
| Parameters | `config.toml` | Purchase-policy parameters, the **currency rates table** (10-year yield, terminal growth, `as_of`), and separate **country-risk assumptions**. Update the table when rates move, by hand or in the dashboard's Method view (see `dashboard/README.md`). The run refuses to start if a reporting currency in the universe is missing from it or a rate is malformed. |

`input/` is git-ignored: your watchlist and positions never leave your machine. The shared `overrides/` files are versioned.

## Method

Tripwire builds **general, source-labeled valuation scenarios**. Reported facts,
derived inputs, forecasts and uncertainties are separated. The dashboard shows
base/stress/upside values, the model chosen, owner capital contributions and the
assumptions behind each result.

**Business economics choose the model.** Operating and recovery forecasts,
common-equity/residual-income models for financial businesses, property cash and
maintenance scenarios, and consolidated utility/hybrid models work without a
handwritten forecast entry for each ticker. Source-provided GICS is optional
classification evidence, not a one-model-per-sector rule. Diversified groups can
use a consolidated model or an ownership/currency/claim-consistent sum-of-parts.

**Evidence is assembled before estimation.** Coherent quarters can reconstruct
TTM data; source disagreements are retained. Missing facts remain unknown. An
unused debt uncertainty does not invalidate an equity-earnings model, while a
contradiction in earnings actually used by that model still matters. EPS forecasts
must have compatible periods, currency and share units, including ADR ratios.

**Growth is not free.** Future equity investment and negative owner distributions
are priced explicitly. Common-equity models do not subtract debt or leases again.
Source-backed firm models retain full claim bridges. Scenario ranges are not
probability bounds; a partial asset-recovery view does not invent project value.

**Ranking is distinct from evidence quality.** Each stock's score depends on its
own price and positive entry estimate, not peers. A zero-equity endpoint receives
the limiting display score, without dividing by zero. Uncertain quote units,
ownership and partial asset coverage can still block candidate status while
preserving a conditional valuation. Funds remain outside single-company models.

See [the model contracts](docs/valuation-method.md),
[general-model validation](docs/general-models-2026-10-11.md), and
[optional source-input examples](examples/valuation.toml), including a
[mixed bank/industrial group](examples/diversified-valuation.toml).

## Sources and reproducibility

- `overrides/valuation.toml` and optional git-ignored `valuation.local.toml` hold sourced issuer facts and base/stress assumptions. No example assumptions are loaded automatically.
- yfinance supplies annual/trailing/quarterly statements, analyst ranges/counts, earnings estimates and revisions. Coverage varies; missing data is visible.
- An explicit `sec_cik` plus `SEC_USER_AGENT` enables optional SEC companyfacts retrieval. This is a limited standard US-GAAP balance-fact mapper, not automatic global or regulatory-capital extraction. Configuration/access failures are visible.
- Raw fetched fundamentals and optional filings are saved as immutable snapshots. Each run retains configuration, code/resource hashes, statement periods and quote/FX observations.
- Dated public industry references ship with the package and can be refreshed explicitly with `python tools/refresh_industry_references.py`; failed source validation preserves the previous snapshot.
- Experimental inference, cycle estimation and probabilistic ranking are kept outside production in [research/README.md](research/README.md).

## Known limits

Automatic values are estimates. Future margins, capital allocation, regulatory limits, maintenance requirements, financing availability, dilution, geographic exposure and business mix remain assumptions unless stronger evidence is supplied. Missing detail changes the model, scenario range or uncertainty label; it is not a blanket sector exclusion. Pre-revenue asset recovery is explicitly partial when projects cannot yet be valued. Sourced overrides and component models support higher-detail research without ticker-specific code.

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
