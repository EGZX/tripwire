# General valuation models and evidence contracts

Tripwire models a listed common-equity claim using reported evidence and explicit
forecast assumptions. A company is not excluded simply because it is a bank,
insurer, property company, utility, diversified group, cyclical or loss-making
business. Numerical validity, model coverage, evidence quality and predictive
accuracy remain different questions.

## Classification is separate from valuation

`business_models.profile` records provider labels, optional source-provided GICS,
explicit economic-family declarations and segment evidence separately. Provider
sector/industry labels are never presented as verified GICS. A sourced GICS label
suggests a route; explicit economic evidence can override it. Unknown labels retain
a general route with uncertainty. Funds and non-company instruments are excluded.

GICS principally classifies a company into one business category. It does not
establish that every segment has that category's economics. See the
[GICS methodology](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-gics.pdf).
The engine supports both consolidated common-equity models and component-level
sum-of-parts. It never averages component values using revenue percentages.

## Evidence assembly

The original provider record is immutable. `evidence_assembly` selects compatible
statement periods and reconstructs trailing flows from four consecutive quarters
when possible. One reported field definition must cover the full window. Annual,
year-to-date and half-year observations are not silently added as quarters.
Weighted-average shares and EPS are not additive cash flows.

Reported operating profit takes precedence over provider-standardized operating
income. Provider totals, quarter sums, source rows, dates, accounting-basis
conflicts and claim alternatives remain in the ledger. Filing checks apply to
current and historical inputs actually used by a model. An unresolved debt
reference does not invalidate a model that values common earnings without a
second debt bridge; an unresolved earnings fact used by that model does.

Missing debt, cash or noncommon claims remain unknown. Alternative capitalization
or accounting interpretations are conditional hypotheses, not verified bounds.
When market cap is missing but a dated quote and share count exist, the explicit
quote/count proxy permits a conditional comparison and blocks candidate status
until its economic units are reconciled. Quote currency, statement currency,
minor units, ADRs and common-share economic rights remain separate.

## Automatic model families

| Economics | Model and observable evidence | What remains estimated |
|---|---|---|
| Operating, growth and cyclical | Revenue, common net margins, observed equity-capital intensity and dated industry references | Future growth, margin convergence, marginal equity investment and financing convention |
| Banks, insurers and financial businesses | Common earnings, common book equity, retained-capital history; distributions reconcile independently to residual income | Future returns, capital allocation, regulatory constraints and credit/catastrophe outcomes |
| Property | Compatible reported common AFFO/FFO when supplied; otherwise explicitly estimated common-income/cash-return and maintenance scenarios | Maintenance allocation, growth investment, partnership scope and asset economics |
| Utilities and mixed infrastructure | Consolidated common earnings and investment evidence | Regulatory lag, merchant exposure and future allocation; total assets are not assumed to be rate base |
| Holdings and diversified groups | Consolidated common-equity model, or sourced business-component SOTP | Future mix and component assumptions when segment detail is absent |
| No positive operating revenue | Explicit balance-sheet recovery hypotheses | Project, mineral, clinical and future-franchise value remains unquantified; partial coverage blocks candidate status |

The automatic operating model uses common earnings after financing and tax.
It does not subtract debt, cash, preferred claims or leases a second time. This
avoids combining expensed operating leases with another full lease-debt deduction.
It does not imply that financing or lease economics are known perfectly.

Earnings are clean common income: common income less discontinued operations and
less unusual items after their tax effect. A divestiture gain or a one-off
write-up therefore cannot become a mature margin. Clean margins above the policy
margin limit (default: 100% of revenue) are treated as one-offs on a tiny revenue
base and excluded; losses of any size are kept. Own history sets the terminal
margin only on a mostly profitable record (at least two profitable fiscal years
and at least half of them; the trailing twelve months overlap the last fiscal
year and do not count as another period); otherwise the dated industry reference applies. Cyclicals
start part of the way from the current towards the historical median margin.

Existing explicit firm forecasts, regulatory-capital distributions, property AFFO,
regulated rate-base, NAV and finite-project models remain available for better
issuer evidence. Firm models require a complete firm-to-common bridge and a
matching discount-rate claim.

## Forecasts, investment and capital calls

Cash and investments beyond debt are separated from the operating business.
The surplus (cash and short-term investments plus long-term investments, less
debt, and at most working capital plus investments less long-term debt, so cash
owed to customers such as merchant funds is not surplus) is valued at par at the
opening date. It does not count as capital the business needs per unit of
revenue, and the after-tax interest and equity-method income it earns leave
operating earnings, so it is never counted twice. Unknown cash, debt or working
capital separates nothing.

The operating projection funds first-year expansion at the opening date and later
expansion from the preceding period. Future equity investment per added revenue
converges to the stated mature earning-power/return assumption. Contraction does
not automatically sell assets at book value. A negative net distribution is a
priced contribution by existing owners, not free new-share funding.

The financial/common-equity kernel uses:

- earnings = opening common book equity x scenario return;
- net owner distribution = earnings - change in common equity;
- value = discounted distributions plus the stated terminal franchise value.

The base return is clean common income over average common book of the trailing
year (or the interpolated EPS consensus when its units reconcile). Automatic
plans are self-funded: book growth cannot exceed the return on book, so no owner
capital call is assumed. At maturity the existing book keeps the hurdle plus a
share of today's excess return, capped (policy franchise share and cap; stress:
the hurdle). Capital added for terminal growth earns the hurdle, so growth adds no
value at maturity. Stress starts at most at a fixed fraction of the base return,
with reduced book growth; without payout history, starting book growth is
limited to a retention share of the base return.

It independently checks the equivalent residual-income identity. Common book
capital is not represented as regulatory capital. Missing regulatory forecasts
are disclosed limitations, not proof that a bank has no valuation model. More
specific regulatory-capital forecasts can use the explicit capital model.
[Financial-service valuation reference](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/finfirm09.pdf).

All base, stress and upside assumptions are retained. Scenario extrema are not
probability quantiles, guaranteed bounds or backtested safety margins. Large
future owner contributions are shown in reporting-currency units and already
reduce value. Participation, financing availability and dilution to owners who
do not participate are not assured.

A nonpositive modeled continuation value produces a zero common-equity endpoint,
with the negative underlying continuation result retained where applicable.
It does not prove that default, recovery or abandonment options have zero value.
The displayed -100 score at a zero ceiling is the limiting display endpoint;
the ratio itself is not fabricated as a finite raw score.

## International estimates and industry references

Revenue-growth estimates are used only when the provider's base-year revenue
reconciles to the last fiscal year or the TTM within the policy tolerance;
otherwise the period or revenue definition differs and observed growth applies.
Estimates in another currency than the statements (ADRs) use the provider's
growth rate, which is currency-neutral. Next-twelve-month growth weights the
current and next fiscal-year estimates by the share of the fiscal year still
ahead. After the two estimate years, excess growth over terminal growth halves
every policy half-life (default half a year); one estimate is never compounded
for a decade. Equity needed per added dollar of revenue moves only a quarter of
the way (policy capital_fade) from today's level towards the mature level at
which new equity earns just the hurdle; the terminal value always uses the
mature level. Both defaults come from the fundamentals backtest
(tools/backtest_fundamentals.py, 2012-2016 cohorts, 10-year outcomes): companies
needed 0.51 of equity per added dollar of revenue against 0.76 assumed at full
convergence, and with these settings realized/model value is 1.03 pooled and
1.02 for the typical company. Companies starting above 20% growth remain
overvalued in the typical case (0.78): only about one in seven sustained 15%
revenue growth for five years.

Analyst EPS consensus is often on an adjusted basis that does not tie to reported
EPS, so its levels are not used; its growth rates are. Years 1-2 margins apply
the consensus earnings growth (time-weighted like revenue) to reported clean
income, then fade linearly to the terminal margin. A forecast earnings collapse
or ramp therefore reaches the valuation; without a positive consensus base the
reported margin path is used. Consensus-implied margins stay within the
company's own recorded margin range: current-year consensus EPS can contain
one-off gains that reported clean income excludes, and their reversal would
otherwise count twice; a real collapse remains possible when the record holds
losses.

At maturity, new equity earns the hurdle plus half of today's excess return on
equity (terminal margin / equity per revenue), capped at 10 points (upside:
three quarters, 15 points; stress: the hurdle). The financial model uses the
same franchise settings. Returns on equity persist: in the backtest, companies
starting at 15-25% (above 25%) return on equity still earned a median 18% (26%)
ten years later, and new equity added in years 5-10 earned 20-40%. Persistence
beyond ten years cannot be observed in the data; the cap keeps about half of the
excess still seen after a decade.

Annual EPS estimates need fiscal-period, currency and economic share-unit
reconciliation. Same currency alone is insufficient for an ADR or another class.
Unproved estimates remain in evidence with a rejection reason; reported common
income still supplies a model. Optional `earnings_estimate_units` source metadata
can prove a conversion; its `as_of` uses the immutable estimate snapshot's UTC
date, not the date of a later quote refresh. Its statement share field and period
must match the denominator. No ratio is inferred from prices alone.

Operating base assumptions use issuer-level margins and revenue evidence, so
listing-specific EPS panels do not create different base business values for an
ADR and its ordinary shares. Compatible EPS forecasts enrich scenario evidence.

The packaged industry reference is dated, independent of the watchlist, and
explicitly US-based. It is not GICS or a company forecast. Own history remains the
primary evidence; an unmapped industry uses a disclosed broad reference rather
than a hidden ticker exception. Source metadata and resource hashes are retained.
Refresh explicitly with `python tools/refresh_industry_references.py`. Parsing,
units, observation month, industry populations and fallback availability are
validated before atomic replacement; failed refreshes preserve the old snapshot.
[Margin data](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/margin.html),
[capital-investment data](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/capex.html).

Rates remain declared valuation/purchase-policy assumptions. For operating and
cyclical businesses the equity risk premium is relevered for financial leverage
(Hamada): multiplied by (1 + 0.75 D/E) / (1 + 0.75 D/E_ref), with D = net debt
and preferred (surplus cash counts as zero), E = market capitalisation and
D/E_ref = 0.084, the capitalisation-weighted S&P 500 nonfinancial level, so the
market-average company keeps the configured premium. Regulated, property and
financial families carry structural leverage against lower-risk assets and are
not relevered (no asset beta is modeled). The backtest cannot test discount
rates without prices; it shows leverage widening the downside tail of outcomes
(10th percentile realized/model 0.18 with net cash, about -0.5 above 1x book
net debt/equity) rather than lowering the typical outcome. A sourced equity
rate and the policy hurdle value the same cash-flow path separately; the entry
ceiling satisfies both price constraints. WACC requires a firm model. Missing
local rates can use an explicit constant-FX/base-currency policy scenario. This
is not an assertion about future exchange rates or currency-neutral risk.

## Diversified businesses and sum-of-parts

A `sum_of_parts` model declares each component's model, source, cash-flow claim,
ownership, currency, FX conversion and discount basis. Firm values receive their
component-only bridge before ownership and FX; common-equity values do not receive
another debt deduction. Parent-only cash, claims and corporate costs apply once.
Stable claim IDs detect duplicated debt/cash/costs across components and parent.
Negative subsidiary equity needs an explicit limited-liability or support
assumption. Cross-currency components also require their own terminal assumptions.

Component discount rates govern SOTP. Changing an unrelated parent context rate
does not silently reprice components or assert that a parent purchase hurdle was
satisfied. The output states this distinction. Source-labeled segment metadata
can coexist with a consolidated model when segment forecasts are not supplied;
that coverage limitation stays visible.

## Model policy

Every judgement threshold of the automatic models lives in `screener/policy.py`:
margin limit, cyclical normalisation, growth half-life, estimate tolerance,
minimum reference sample, franchise share and cap, stress factors, payout
retention, asset-recovery share and the data-control materialities. They contain
no ticker, country or sector exceptions, so they apply unchanged to every issuer
added later. Each can be set in `config.toml` under `[model_policy]`; unknown keys
or negative values stop the run, and the Method sheet lists the values used.
Discount rates, the equity risk premium and terminal growth stay in
`[valuation]` and `[rates]`.

Provider labels route automatically (`business_models`). Per-ticker rows in
`overrides/classification.local.csv` (git-ignored) are only for issuers whose provider label
misstates their economics; they are not needed when the label already routes
correctly. A new provider industry needs at most one line in the industry
crosswalk, and a test checks every crosswalk target exists in the reference.

## Ranking and controls

Index lists ([inputs] index_lists, e.g. the S&P 500 from
tools/refresh_index_list.py) are screened with the same rules and labeled in the
Lists column, so a watchlist can be compared with its market. On 2026-10-11 the
model implied a median 7.0% for S&P 500 members, and 25 of 503 were candidates.

A candidate needs more than a price below the entry ceiling: its stress value
must cover at least the policy share of the price (stress_floor, default half).
Otherwise its status is "weak downside". In the backtest the realized value fell
below the stress value for about 29% of companies (21-33% across growth
groups), so the stress case is roughly a one-in-three bad outcome, and the floor
asks that such an outcome still leaves half the price.

A stock can have a conditional valuation without qualifying as a candidate.
Missing essentials, invalid economic domains and contradictory used evidence
can still prevent valuation. Stale quotes, unresolved quote units, unverified
ownership/capitalization and partial asset coverage can block candidate status
while preserving a numeric comparison. Missing a sector-specific manual forecast
is no longer a blanket blocker.

Data flags are either blocking or informational. Blocking flags describe a data
problem that can change the value: stale or future quotes, stale financials,
unresolved ownership scope (a material common-vs-consolidated income gap with
more units than listed shares), quarters that do not add up to the audited annual
figure, declared FX far from the market rate, and holding companies valued on
consolidated book without a NAV or sum-of-parts. Provider-country proxies,
currency conversion and modeled owner contributions are disclosed but do not
block. Small differences (share classes, rounding) are below materiality and are
not flagged.

The inspector and exports show the chosen model, selection reason, scenario
range, assumptions, uncertainties and modeled capital contributions. Old growth
clamps, book-ROIC caps and sector-exclusion settings were retired with the previous
funded-proxy default; they are no longer dashboard controls. Historical saved
reports retain their original numbers and model labels.

A priced path with nonnegative future distributions and at least one positive
payment identifies a unique cash-flow yield. Opening contributions increase the
initial investment and do not destroy uniqueness. Signed future distributions
remain valid valuation inputs, but a single yield is withheld unless uniqueness
is established. Finite distributions permit returns above -100%; positive
perpetuities require return above terminal growth. The numerical search reports
an explicit outside-domain result above its 300% upper bound. Multi-driver
scenarios do not identify a unique market-implied growth rate.

Validation covers cash-flow/accounting identities, claim matching, negative
profits/contributions, fiscal periods, units, ADR equivalence, unseen issuers,
portfolio independence, source conflicts and package/runtime integration. It
establishes mathematical/implementation evidence, not investment predictiveness.
Experimental probabilistic models and fitted trading claims remain separate.
