# Valuation contracts and supported use

Tripwire is a research screener. Numerical convergence, accounting completeness,
model applicability and investment predictiveness are separate questions. A
positive entry score is not a buy recommendation or a probability.

## Evidence comes before a price

Every statement is selected at one coherent period. Trailing income requirements
depend on the model: financial businesses need common net income, not EBIT.
Balance selection chooses the newest complete period across quarterly, annual and
available filing sources. An older complete period keeps its actual date. Missing
fields are never individually backfilled from another period.

Debt is either reported, reconstructed from a complete compatible current/long-term
pair, confirmed by a dated provider aggregate for the exact same period, or supplied
with a source. Missing rows do not mean zero. Preferred and minority claims can
also be reconciled from compatible reported equity totals; otherwise they remain
unknown. Current and historical book equity/debt are not replaced by zero.

`statement_periods`, `price_as_of`, quote metadata, FX observation dates, raw source
hashes, issuer input sources and the model identity are retained in each run.
Fresh quotes include fresh capitalization/share metadata; cached capitalization
is not silently rescaled across a split. Failed quote refreshes and stale fallback
fundamentals prevent candidate status. Freshness policies are explicit in config:
7 calendar days for quotes, 90 for rates and 270 for statement periods. These are
operational review intervals, not scientific constants or guarantees of freshness.

The standard equity bridge includes debt, selected cash, minority and preferred
claims. Pensions, restricted/operating cash, options, deferred taxes and other claims
still require issuer-specific review. `other_claims`, `cash` and other sourced
balance overrides allow that reconciliation. An automatic accounting-proxy output
does not establish that every unreported claim is absent.

## Automatic operating-company proxy

For a profitable company with sufficient data:

- Base earnings are operating income times one minus the observed effective tax
  rate, unless a sourced normalized rate is provided. Missing or invalid tax rates
  are not replaced by 21% or clipped to 35%.
- Growth is a disclosed, bounded blend of historical revenue growth and provider
  next-fiscal-year growth. It is still a constant-margin earnings-growth proxy, not
  a complete operating forecast. Declines below the configured screening domain
  require an explicit recovery/finite-life model; they are not clipped into a
  less severe decline to produce a score. Analyst ranges, counts, currency and period are
  retained; low/high analyst estimates are not confidence intervals.
- Accounting ROIC includes acquisition capital. Ex-goodwill ROIC is diagnostic,
  not assumed to measure organic marginal returns. Nonpositive/unavailable invested
  capital does not receive the highest permitted return. A sourced `marginal_return`
  can replace the proxy.
  Consolidated capital includes noncontrolling interests; common and total parent
  equity are distinct. Opening/closing capital is matched to a calendar/52-/53-week
  earnings window when available. Otherwise the closing-capital proxy and its
  period limitation are explicit. Unrelated fiscal year-ends are not a TTM average.
- Growth must be funded in every explicit period and in perpetuity. Negative
  growth generates no automatic liquidation proceeds. A financing violation
  produces an invalid-model result instead of a favorable score.
- In the absence of a sourced `terminal_return`, return on new capital fades to
  the lower of the initial proxy and policy hurdle. This is an explicit screening
  assumption; it is not calibrated evidence about competitive-advantage duration.
- Observed net investment, acquisition cash flow, R&D and the difference between
  observed and modeled investment are shown for reconciliation. A large difference
  is not automatically classified as fraud, maintenance capex or growth capex.

Short cyclical histories use nonoverlapping annual margins, without adding an
overlapping TTM as an independent year. These outputs carry a critical cycle-proxy
flag; source-backed through-cycle operating forecasts are needed for eligibility.
Raw losses are not automatically treated as proof of permanent failure: a funded
operating forecast can model a documented recovery.

## Sector models

Put researched assumptions in `overrides/valuation.toml` or the git-ignored
`overrides/valuation.local.toml`. See `examples/valuation.toml`. Each issuer needs
source, observation date, accounting period end, reporting currency, and complete
base/stress scenarios. Dates cannot be in the future or precede the accounting
period. All monetary values use the reporting currency, not thousands/millions or
quote minor units. Percentages are annual decimal fractions.

Quote dotted tickers in TOML, for example `[issuers."MUV2.DE"]` and
`[issuers."MUV2.DE".base]`. A source-backed `model_family` may correct an absent
or incorrect provider classification; its model still has to satisfy that
family's input/claim contracts. Mortgage REITs require an asset-value route, not
the property-FFO model. Missing industry data does not silently imply an
ordinary operating business.

| Model | Cash-flow claim | Required economic inputs |
|---|---|---|
| `operating_forecast` | Firm | Revenue, starting growth, starting/terminal margins, tax rate, sales-to-capital ratio, sustaining net capex, funding capacity. Revenue and margin evolve separately. |
| `bank_capital` | Common equity | Common net income, income growth, capital-base growth, available regulatory common capital, risk-weighted capital base, required capital ratio, payout limit, explicitly releasable excess. |
| `financial_capital` | Common equity | Broker/other financial distributions using a documented common-capital requirement base and matching ratio; never assume bank RWA definitions apply to a broker. |
| `insurer_capital` | Common equity | Same capital-distribution machinery, with an insurance risk base and matching solvency-capital ratio; it must not mix CET1, total own funds and common equity. Income must reflect underwriting/reserve/investment assumptions. |
| `reit_affo` | Common equity | Common-holder FFO after financing costs, recurring property capex, leasing costs, growth and return on new equity. This is not appropriate for mortgage REITs without a different equity-capital model. |
| `regulated_rate_base` | Firm | Net regulatory rate base, growth, achieved after-tax return, integer regulatory lag, actual opening rate-base history, funding capacity. The lag pipeline clears before terminal valuation. |
| `asset_nav` | Common equity | Attributable assets, parent debt, other claims, tax leakage and present value of holding costs. Ownership, cross-holdings and parent/subsidiary boundaries must already be reconciled. |
| `finite_project` | Firm | Explicit annual cash flows, salvage, closure costs and funding capacity. There is no perpetual terminal growth. Closure funding is checked. |

For regulatory lag L, `rate_base_history` contains L+1 actual bases, oldest to
newest, ending at `rate_base`. For bank/insurance models, `capital_base` and
`required_capital_ratio` must refer to the same regulatory denominator and common
capital claim. Book equity/assets is only a descriptive flag, not a substitute.
Capital released immediately or at the horizon requires
`excess_income_excluded = true`: forecast income must exclude earnings on that
released excess. Retained capital that is not explicitly permitted to be released
is not credited as an immediate dividend. Payout and growth constraints remain
explicit assumptions, not an automated solvency assessment.

Financing capacity is a documented availability bound, not an invented source of
free capital. It does not establish intrayear liquidity, future financing terms or
the viability of a distressed firm. High-growth equity issuance, convertible
instruments and major changes in capital structure require a separately reconciled
ownership/funding case before using the resulting per-share price.

Banks, insurers, property businesses, regulated utilities and financial hybrids
without required sector evidence abstain rather than inherit a generic earnings
equation. Holdcos and preprofit issuers can use an applicable explicit model. Funds
remain outside the single-company models.

## Value, purchase policy and inverse problems

Firm flows use a declared WACC/unlevered rate; equity flows use cost of equity.
`discount_rate_basis = "policy"` explicitly denotes a purchase-policy calculation.
The code rejects equity-rate/firm-flow and WACC/equity-flow mismatches. With a
sourced intrinsic discount rate, modeled base value and purchase-policy ceiling
are separate outputs. The entry ceiling is the lower of the base value and the
value of the same base cash flows at the configured purchase hurdle. Thus a low
policy hurdle cannot authorize paying above the source-assumed risk valuation;
no adverse growth scenario is mixed into this constraint. The unconstrained
policy value is retained for inspection. Sourced WACC/unlevered rates also work
with the automatic operating proxy; an equity rate is rejected for its firm flows. Static NAV uses NAV as its ceiling and does not imply a return
hurdle or an expected shareholder return.

The automatic operating proxy supplies a policy ceiling, not an intrinsic value.
The policy rate is currency yield plus the configured premium and geographic risk,
subject to explicit configured floors. Country risk is not derived from reporting
currency. Sourced `geographic_exposures` weights or a sourced issuer premium take
priority over the labeled provider-country proxy. Proxy premiums migrated from the old
configuration are policy assumptions, not newly researched market estimates.
Custom configurations with a nonzero currency-level `crp` are rejected explicitly;
move that assumption to the appropriate country/exposure input rather than
quietly continuing the currency/geography conflation.

Base and automatic operating stress use the same discount rate. A user-supplied
joint rate/operating scenario must be read as such. Lower growth can raise value
when investment destroys value; scenario crossings are reported rather than
clipped or reordered to manufacture downside. Stress values are not calibrated
confidence limits or probabilities.

`solve_growth` reports `solved`, `nonunique`, `weakly_identified`, `indeterminate`,
`no_solution`, `outside_domain`, `invalid`, `unsupported` or `unresolved` as
appropriate. A scalar is supplied only for a numerically resolved unique root.
Root residuals and alternative roots are retained. The self-funded domain checks
subsequent growth funding; first-year growth uses already invested opening capital.
Monotone cases use direct bracketing. Funded-growth inversion uses piecewise
polynomials partitioned by derivative roots, including turning-point checks.

Growth inversion is limited to -90%..300% starting growth and at most 50 explicit
years, further restricted by financing feasibility. Very long horizons and wide
return/growth domains can be ill-conditioned; an unresolved result is an
intentional abstention, not permission to loosen the residual check. Finite payout
paths do not inherit a Gordon-bound restriction when their terminal payout is zero.

The implied cash-flow yield identifies its firm/equity claim. `fx_parity_return`
is an illustrative compound-rate parity scenario, not expected unhedged EUR return
or an executed currency hedge.

## International ownership and optional filings

Provider cap, quote units, implied/current shares, common earnings and consolidated
earnings are checked for incompatible ownership scopes. A multiclass share-count
difference alone is not treated as an error. An unresolved combination with
noncommon earnings requires reconciliation. Source-backed
`market_cap_basis = "listed_shares"` with `ownership_reconciled = true` values the
verified listed common claim using refreshed shares/price. Do not apply that rule
globally to ADRs or companies with other common classes. A direct
`market_cap_quote` override requires `market_cap_as_of` matching the quote date.

With an explicit `sec_cik` and a configured `SEC_USER_AGENT`, the CLI retrieves and
preserves SEC companyfacts. Publication-date and currency selection are tested.
Only mapped whole-entity US-GAAP instant concepts feed the balance selector;
incomplete debt components cannot become total debt. Custom concepts, segments,
most regulatory-capital facts and IFRS mapping are not automatically interpreted.
An unavailable configured filing check is visible. No API credential is embedded.

Sources: [SEC API contracts](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[yfinance financials](https://ranaroussi.github.io/yfinance/reference/yfinance.financials.html),
[yfinance analyst data](https://ranaroussi.github.io/yfinance/reference/yfinance.analysis.html),
[cash-flow/rate matching](https://pages.stern.nyu.edu/adamodar/New_Home_Page/lectures/approach.html),
[terminal consistency](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm),
[country exposure](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/CountryRisk.htm).

## Validation and reproducibility

Run the full pytest suite. The audit regressions cover unfunded terminal growth,
contraction, nonunique/tangent roots, unit invariance, financial TTM selection,
period dating, missing capital, ownership, minor units and rate geography. Sector
tests use independent stationary/reference calculations and explicit failure cases.

`tools/validate_corpus.py` replays a specified local cache with an explicit valuation date and frozen FX
file, checks every scored funding path, and records source/config hashes. It does
not download data or regenerate golden expectations. Existing golden fixtures are
local and optional; after reviewing intentional model changes, update only the
current checkout's golden snapshot. Preserve before/after comparisons.

Each production report keeps its configuration and implementation hashes under
`data/history/provenance/`. Raw fetched fundamentals and optional filings are
immutable snapshots. These controls support prospective and limited filing-based
point-in-time analysis; they do not supply global historical consensus, a
survivorship-free universe or an investment-return validation.

A replay date fixes freshness checks for reproducibility. It does not turn later
restated provider data into point-in-time evidence; the raw snapshot/publication
dates must still precede any historical decision being studied.

The revenue/sales-to-capital forecast uses a same-year investment convention and
a constant supplied tax rate. It does not infer construction lead times or NOL
utilization; use a suitable explicit project/regulatory case where those drive
value. FX timestamps identify provider observations/daily bars, not guaranteed
synchronized intraday execution prices.
