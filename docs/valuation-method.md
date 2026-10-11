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

Existing explicit firm forecasts, regulatory-capital distributions, property AFFO,
regulated rate-base, NAV and finite-project models remain available for better
issuer evidence. Firm models require a complete firm-to-common bridge and a
matching discount-rate claim.

## Forecasts, investment and capital calls

The operating projection funds first-year expansion at the opening date and later
expansion from the preceding period. Future equity investment per added revenue
converges to the stated mature earning-power/return assumption. Contraction does
not automatically sell assets at book value. A negative net distribution is a
priced contribution by existing owners, not free new-share funding.

The financial/common-equity kernel uses:

- earnings = opening common book equity x scenario return;
- net owner distribution = earnings - change in common equity;
- value = discounted distributions plus the stated terminal franchise value.

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

Rates remain declared valuation/purchase-policy assumptions. A sourced equity
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

## Ranking and controls

A stock can have a conditional valuation without qualifying as a candidate.
Missing essentials, invalid economic domains and contradictory used evidence
can still prevent valuation. Stale quotes, unresolved quote units, unverified
ownership/capitalization and partial asset coverage can block candidate status
while preserving a numeric comparison. Missing a sector-specific manual forecast
is no longer a blanket blocker.

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
