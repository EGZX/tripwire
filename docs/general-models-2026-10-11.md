# General valuation-model implementation and validation — 11 October 2026

The automatic engine now produces conditional common-equity valuations across
operating, cyclical, growth, loss-making, financial, property, utility and
diversified businesses. A missing handwritten sector forecast no longer excludes
a stock. No ticker-specific model branches or fitted target prices were added.

## Main changes

- A canonical business profile separates provider labels, sourced GICS,
  economic-model declarations and segment evidence. Fee businesses such as
  exchanges are not automatically modeled as deposit-taking financial firms.
- An immutable evidence assembly reconstructs compatible four-quarter flows,
  preserves source disagreements and keeps unknown claims unknown. Checks follow
  inputs actually consumed, including historical observations and derived share
  and common-capital denominators.
- Operating models project revenue, common net margins and funded equity
  investment. First-year expansion is funded at the opening date. Negative owner
  distributions are charged, and existing financing costs are not deducted twice.
- Financial models value common earnings and common capital using equivalent
  distribution and residual-income identities. Accounting capital is not
  mislabeled as regulatory capital.
- Property and utility models use common earnings, available cash-return and
  maintenance evidence, plus compatible reported common AFFO where supplied.
  A utility's consolidated asset base is not called its regulated rate base.
- A dated 96-industry reference supplies explicit external hypotheses where
  issuer history cannot identify mature margins or capital intensity. The
  reference-refresh command validates source structure and preserves the last
  snapshot if refresh fails. Reference files are included in the package.
- Reports and the dashboard expose model selection, assumptions, uncertainty,
  scenario ranges and gross modeled owner contributions. Yield is shown only
  when its uniqueness is established. Signed future funding can leave yield
  unidentified without preventing a valuation.

## GICS and diversified businesses

[GICS](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-gics.pdf)
assigns a principal-business category; it does not establish that every operation
has the same economics. The engine accepts source-provided GICS as a routing
suggestion, while retaining its source and date. Yahoo labels are not asserted
to be verified GICS, and no proprietary company-classification database is bundled.

Without segment forecasts, diversified firms can use a labeled consolidated
common-equity valuation. With segment evidence, `sum_of_parts` supports a bank,
industrial business, property assets and other component models in one company.
Each component has its own cash-flow claim, discount basis, currency, ownership
and source. Firm-to-equity bridges apply before ownership and currency
conversion. Parent-only cash, debt, claims and corporate costs apply once;
economic claim IDs reject double counting. Revenue shares are never used as
weights on otherwise incompatible valuation multiples.

[The worked example](../examples/diversified-valuation.toml) has a bank worth
USD100 and an industrial stake worth USD160 after debt, ownership and FX. Parent
adjustments are USD-27, giving USD233. Its independent stress case is USD173.
Changing an unrelated parent discount-rate context cannot silently reprice the
components or establish satisfaction of a parent purchase hurdle.

## Evidence and test interpretation

The original 18-listing cohort retains the
[148 targeted filing comparisons](live-validation-2026-10-11.md) and their raw
source ledgers. Those checks are targeted reconciliations, not complete audits.
The revised model was replayed against immutable SHA-256-verified source files.
The original audit's outputs and raw provider records were preserved.

Twelve additional listings were freshly retrieved for model-coverage testing:
O, VICI, DUK, IBE.MC, DBK.DE, HDB, ALV.DE, AIG, BRK-B, BN, SNOW and VALE. Their
final replay disables all issuer and classification overrides. These are tests
of general applicability on unseen inputs, not a predictive holdout or an
independent filing audit of all twelve firms.

The larger 131-record corpus is a frozen-data replay with explicitly recorded FX.
It is not another fresh market acquisition. Cohorts overlap and their counts
must not be added as unique companies.

Regression evidence includes independently calculated perpetuities, clean surplus
and residual income, growth at the opportunity cost, signed capital contributions,
negative and zero payout endpoints, monetary scaling, claim/FX/ownership bridges,
ADR unit evidence, source conflicts, fiscal windows, listing equivalence and
independence from watchlist composition. The local golden-output change was
reviewed and its prior snapshot retained; it is not financial ground truth.

## Final verification

**492 tests passed, zero failed.** The package wheel includes every new general
model and the industry resource; importing the extracted wheel in an isolated
Python process and loading the reference passed. Browser checks covered the
overview, method and five issuer inspectors (ADBE, JPM, NEE, RIVN and the fictional
mixed group), with no script errors or horizontal overflow. Screenshots were
reviewed. A sparse-report missing-column crash and inspector wrapping were fixed.

| Data set | Numeric equity valuations | Candidate | Above entry estimate | Data flag | Excluded instruments |
|---|---:|---:|---:|---:|---:|
| Original live cohort | 18 / 18 | 5 | 10 | 3 | 0 |
| New live issuers, no overrides | 12 / 12 | 2 | 8 | 2 | 0 |
| Preserved corpus | 130 / 130 equities | 10 | 102 | 18 | 1 fund |

All three replays had zero computation failures and zero `not rankable` outcomes.
The corpus checks independently re-priced all **390** base/stress/upside paths
and checked payout/investment identities. TSM and 2330.TW have exactly identical
base issuer parameters and aggregate common-equity values; their listing prices
and resulting entry scores need not be identical.

The five live-cohort data flags are **RIVN, RIO.L, PLD, BN and BRK-B**. Each retains
the exact reason `unresolved ownership scope: multiple share classes or partnership
units require economic-rights reconciliation`. They have numeric valuations;
their candidate status remains conditional on that claim reconciliation.
The [release ledger](general-models-2026-10-11.json) records live-cohort outcomes,
corpus flag counts, source-result hashes and final implementation hashes. The
full symbol-level corpus ledger stays with the local source evidence.

Local immutable receipts and artifacts are under
`data/general-models-2026-10-11/release-original`, `release-heldout`,
`release-corpus`, `package-final` and `ui-final`. Source/config/resource hashes
accompany each replay. The three original filing-ledger hashes still match the
previously committed 148-comparison audit. Historical outputs were not replaced.

## Remaining limits and exact withholding reasons

A numeric estimate does not establish a reliable forecast. Future margins,
capital allocation, maintenance spending, financing access, credit losses,
regulatory constraints and terminal returns remain assumptions unless sourced.
The industry reference is US January-2026 data, with its geographic/date limits
visible. Its estimates are not company facts or global sector forecasts.

The pre-revenue resource case receives a **partial asset-recovery estimate**:
carrying-value recovery and stressed liquid-asset recovery can be quantified,
but mineral/project/franchise cash flows cannot be inferred from those accounts.
The exact gate is `unvalued operating or project assets; partial recovery estimate
only`. This is not a complete mine valuation and does not bound all option value.

Other candidate gates preserve numerical estimates when quote/share units,
ownership, capitalization or freshness remain unresolved. The full exact flags
are retained in result rows. A wholly absent quote/capitalization/FX denominator,
contradictory evidence used by the model, or an invalid model domain can still
prevent a trustworthy numerical value. None is replaced by an invented fact.

Single implied growth is withheld because several growth, margin and investment
paths can price the same security. A single yield is withheld for signed future
distributions when uniqueness has not been established. Those are inverse-model
identifiability limits, not sector-model omissions.

No predictive-return validation, scenario probabilities or universal forecast
accuracy is claimed. Experimental inference remains outside production in
[research](../research/README.md). Historical report files remain unchanged.
