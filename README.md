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

**Runtime:** about 2 seconds per symbol on a fresh fetch, so 131 names took 4 minutes and 500 names takes about 15–20 minutes. Re-runs within the cache window take seconds; only prices are refreshed.

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
| Class fixes | `overrides/classification.csv` | Forces `operating`, `cyclical`, `financial`, `holdco` or `fund`. |
| Parameters | `config.toml` | Equity risk premium, adverse-case settings, horizon and caps, and the **per-currency rates table** (10-year yield, terminal growth, country risk premium, `as_of` date). Update the table when rates move, by hand or in the dashboard's Method view (see `dashboard/README.md`). The run refuses to start if a reporting currency in the universe is missing from it or a rate is malformed. |

`input/` is git-ignored: your watchlist and positions never leave your machine. The shared `overrides/` files are versioned.

## Method

Metrics are computed per company in its reporting currency. Market cap is converted from the quote currency, and GBp/ADR quirks are handled. Each statement (income, cash flow, balance sheet) is used as one unit, so fields never mix a TTM and an annual period; "Data as of" is the oldest period used.

**One absolute score.** Every ranked name gets a **max entry price** and the **entry score** `100 × (1 − price / max entry price)`. +20 means the price is 20% below the ceiling, negative means above it. It is a signed price gap, not a return forecast. Status does not depend on which other names are in the run.

**Base earnings.** Operating companies: NOPAT = EBIT × (1 − tax). Cyclicals: median EBIT margin over the annual years plus TTM, times TTM revenue (the adverse case uses the lower of TTM and 3-year average revenue). Financials: net income.

**Growth has to be funded.** Earnings grow at *g*, fading to the currency's terminal growth over 10 years. The owner only receives earnings × (1 − g / R), where R is the return on new capital (ROIC excluding goodwill for operating companies, ROE for financials; the lower of the current and the 3-year average, capped at 40%). Goodwill is excluded because an acquisition premium is sunk and does not fund organic growth; the reported ROIC is still shown for context. When R equals the discount rate, growth adds no value; the starting growth is also capped at R because a self-funded business cannot grow faster than it earns on reinvestment.

**Two mechanical cases.** The reasonable case sets the max entry price; the adverse case is a separate downside check (the **adverse score**, same formula against the adverse value). Taking the lower of the two stacked every adverse assumption onto the timing signal: it set the ceiling for 62 of 104 names in the 2026-10-10 run and pushed names priced above their hurdle return to −100.
1. **Reasonable:** g = mean of 3-year revenue CAGR (incl. TTM) and analyst next-year growth, clipped to −5%…30%. R held at today's level. Discounted at the **hurdle** = currency 10-year government yield + equity risk premium.
2. **Adverse:** g = half the lowest growth input (a decline is kept as is); cyclicals never above terminal growth, and on the trough EBIT margin of the window. R fades linearly to the hurdle (excess returns competed away). Discounted at the **floor** = currency 10-year yield + 2.5 points (half the premium) + country risk premium: the return still required if the adverse case happens. Both rates are lifted to at least terminal growth + 2 points so the terminal value stays finite, and the hurdle is never below 7% nominal.

For operating companies the firm value is discounted at the equity hurdle (conservative for levered names) and net debt and minorities are deducted.

**Candidate** = entry score ≥ 0 and no critical data flag (stale statements, a yield above 25%, no market cap, a bank with equity below 4% of assets).

**Financial sub-families** share the equation but differ in inputs: banks as above; insurers use the lower of TTM and 3-year average net income; fintech/marketplace platforms from the override file keep the ROE growth cap and carry a flag saying the score is a conservative floor, because their equity is cash-inflated.

**Visibility:** the "Caps hit" column lists every parameter that bound a name (growth cap, growth ≤ R, R cap, R smoothed, minimum hurdle, invested capital ≤ 0). "Implied return, base ccy" converts the local implied return to EUR terms by the rate differential so names in different currencies can be compared on one number.

**Not rankable** (listed with the reason, never silently assumed): missing debt or cash figure, no positive book equity (financials), no return on capital, no positive base earnings, a ceiling at or below zero (net debt exceeds what the business earns on new capital), or a reporting currency absent from the rates table. Yahoo omits every debt row for debt-free issuers; those are treated as debt-free and flagged.

**Context, not gates:** implied return (the discount rate at which the funded reasonable path equals EV), implied growth (the starting growth the price needs at the hurdle), peer-group percentiles for value / quality / growth / balance, and EV/Sales vs own 5-year median.

**Special cases:**
- **Fintech/marketplace hybrids** whose customer funds distort cash flow (Kaspi, MercadoLibre, Adyen, Wise, Euronext's clearing house) are treated as financials via `overrides/classification.csv`.
- **Not scored:** loss-making companies (EBIT ≤ 0; financials: net income ≤ 0), holding companies (value via NAV), and funds. A profitable company with negative free cash flow is still ranked and flagged.

## Known limitations

- yfinance uses unofficial endpoints, which can break or rate-limit. Rate limits trigger a 30→240 s backoff, and the stale cache is used as a fallback.
- The return on capital comes from book invested capital (average of the last two year-ends, ex goodwill). Buybacks, large cash piles and expensed intangibles distort it; names with negative invested capital get the return cap and a flag.
- Many non-US issuers report no stock compensation; their FCF yield is flattered (flagged). NOPAT already expenses SBC, so the ceiling is unaffected.
- One-offs in TTM data are not removed. Yields above 25% are flagged and excluded from candidates.
- Historical multiples use today's FX rate.
- Analyst growth is Yahoo's revenue estimate and may be missing or stale.
- Neither this score nor its predecessor has been validated against future returns; that needs point-in-time history that free data does not provide.
- Free API keys (Finnhub, Alpha Vantage) are not used yet. If added, read them from environment variables (e.g. `FINNHUB_API_KEY`), never from committed files.

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

`tests/test_golden.py` runs the model over 14 frozen Yahoo records in `tests/fixtures/cache/` and compares statuses and scores with `tests/golden/snapshot.json`. Any behaviour change fails it on purpose; after reviewing the diff, regenerate with `UPDATE_GOLDEN=1` in the environment. `yfinance` is pinned in `pyproject.toml`; bump it deliberately and rerun the golden test.
