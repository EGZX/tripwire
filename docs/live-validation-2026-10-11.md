# Live provider and filing validation — 11 October 2026

**Ready for research screening with explicit abstentions; not certified for
automatic investment decisions.** Eighteen listings representing seventeen
issuers were fetched afresh, covering nine listing markets, five reporting
currencies, mature and growth businesses, cyclicals, a loss-making manufacturer,
banks, insurance, property and utilities. All acquisitions and computations
completed without errors. Replays use the original snapshot bytes, verified by
SHA-256, and recorded FX observations. Personal inputs and saved production runs
were not changed.

The filing review made **148 targeted fact comparisons**: 119 exact matches,
nine matches within disclosed rounding, seven reconciled definition/scope
differences, and thirteen discrepancies or incomparable fields in the original
provider data. These are baseline input comparisons, not 148 independent
companies or complete statement audits. [The fact ledger](live-validation-2026-10-11.json)
records periods, units, reported/provider values, source locations and links.
Raw provider snapshots, original documents or accessible primary-source extracts,
hashes and local verification receipts remain under
`data/live-validation-2026-10-11/`, outside Git.

## Corrections and evidence controls

- **Reported operating profit now takes precedence.** Shopify's standardized
  Q2 operating income excluded $141m of recurring transaction and loan losses:
  $629m versus the filed $488m. The TTM difference was $519m. Similar definitions
  affected Coca-Cola, BHP and Tencent. The reported row is retained across both
  current and historical calculations, with the selected field identified.
  No adjustment was tuned to reproduce the previous valuation.
  Sources: [Shopify Q2 release](https://www.shopify.com/news/shopify-q2-2026-financial-results),
  [Coca-Cola Q2 filing](https://investors.coca-colacompany.com/filings-reports/all-sec-filings/content/0001628280-26-050503/ko-20260703.htm),
  [BHP annual results](https://www.bhp.com/-/media/documents/media/reports-and-presentations/2026/260818_bhpresultsfortheyearended30june2026_exchangerelease.pdf),
  [Tencent interim results](https://www.tencent.com/wp-content/uploads/2026/08/E700_IRA.pdf).
- **TTM values are cross-checked against four consecutive quarters.** ASML's
  provider TTM operating profit, €11,550.1m, exactly combines US-GAAP annual
  earnings with IFRS interim earnings. Consistent US-GAAP quarters sum to
  €12,513.4m; the annual/interim calculation gives €12,513.3m within source
  rounding. The control exposes differences rather than replacing figures.
  Rounding allowances require dated, sourced precision; half-year observations
  are never added as if they were quarters.
  Sources: [ASML Q2 US-GAAP detail](https://www.investor.asml.com/static-files/5dc79e92-9e59-4a06-af63-22eb4408af16),
  [IFRS interim report](https://www.sec.gov/Archives/edgar/data/937966/000162828026048235/statutoryinterimreport20.htm).
- **ASML debt requires an accounting-basis reconciliation.** Yahoo's €1,984.4m
  equals disclosed noncurrent debt. IFRS separately reports €1,735.3m current
  borrowings. The same principal bonds are corroborated by the
  [US-GAAP annual debt note](https://www.sec.gov/Archives/edgar/data/937966/000162828026011378/R103.htm),
  but exact June US-GAAP current carrying amounts remain unverified. The €3,719.7m
  sum is an IFRS reference that blocks an unresolved comparison, not a certified
  US-GAAP replacement or a silent override.
- **Lease/debt conventions now gate candidate status.** Adobe and Shopify
  provider debt includes operating leases whose costs are already expensed in
  reported earnings. NVIDIA's statement debt omits $509m of current operating
  leases included in its aggregate debt field. A clean execution cannot certify
  these claim/earnings combinations. Known leases stay visible even when sourced
  balance facts are selected; an explicit clearance expires when the relevant
  income or balance period changes. No lease adjustment is inferred from country.
  Sources: [Adobe 10-Q](https://www.sec.gov/Archives/edgar/data/796343/000079634326000156/adbe-20260828.htm),
  [Shopify 10-Q](https://www.sec.gov/Archives/edgar/data/1594805/000159480526000047/shop-20260630.htm),
  [NVIDIA 10-Q](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/nvda-20260726.htm).
- **Nu's share classes are reconciled correctly.** Both A and B classes represent
  parent common equity. The prior inferred parent/noncontrolling mismatch was
  false. The sourced clearance checks the aggregate common-share count and
  stops applying if current shares diverge. Missing regulatory forecasts still
  prevent a valuation. [Nu Note31 and capital disclosures](https://www.sec.gov/Archives/edgar/data/1691493/000129281426004225/nufs2q26_6k.htm).
- **Verification itself was tightened.** Saved-record validation now loads the
  same issuer evidence as the CLI. Live replays reject altered snapshot hashes
  and never substitute replaceable convenience caches. Acquisition diagnostics
  explicitly identify provider categories: Shopify's $55m equity investment was
  mislabeled as a business acquisition, so that category is not asserted to be
  a verified acquisition cash flow.

## Where the live sample now abstains

Final result: **zero eligible, eight data-flagged, eight not-rankable, one above
the ceiling and one not screened.** A data flag retains conditional calculations
but prevents candidate status. This distribution was not a target.

| Listing(s) | Final outcome | Main remaining evidence or model requirement |
|---|---|---|
| ADBE | Data flag | Reconcile operating leases with earnings, debt and historical capital. Period-end provider shares also differ from the filing; current and weighted-average shares are distinct. |
| NVDA | Data flag | Full-cycle assumptions; current/long-term lease scope. Locked equity classified by the provider as restricted cash needs explicit interpretation. |
| KO | Above ceiling | Reported earnings correction applied. Broader cash includes marketable securities; incremental returns and policy rates remain assumptions. |
| XOM | Data flag | Full-cycle margins, reinvestment and commodity assumptions; missing SBC data is not evidence of zero SBC. |
| SHOP.TO | Not rankable | Corrected earnings cannot fund the assumed growth path. Merchant-loan funding is outside conventional OCF-minus-capex, and lease treatment needs reconciliation. |
| TSM / 2330.TW | Data flag | Full-cycle and lease/capital assumptions. Five ordinary shares per ADR and equal total TWD valuation pass; venue/FX timing and ADR premiums can still differ. |
| ASML.AS | Not rankable | Mixed accounting bases in TTM; current debt and lease scope unresolved. |
| 7203.T | Data flag | Automotive versus financing subsidiaries must be separated; consolidated debt is not a clean industrial financing claim. |
| RIO.L | Not rankable | Dual-listed share pricing/scope, full cycle and cash/debt definitions. Issuer net debt differs from the basic bridge by $716m; the full other-financial-assets balance is not proven eligible cash. |
| BHP.AX | Data flag | Reported operating profit corrected; full-cycle and lease conventions remain open. |
| JPM | Not rankable | Normalized common earnings, binding regulatory buffers, risk-weighted asset trajectory and distributions. CET1/RWA observations alone do not provide forecasts. |
| PLD | Not rankable | Common shares versus partnership units, normalized AFFO, recurring capex/leasing costs and growth funding. |
| NEE | Not rankable | Regulated FPL plus competitive NEER requires segmentation or a sourced consolidated forecast; current tax benefits are not normalized cash taxes. |
| MUV2.DE | Not rankable | Solvency capital, required surplus, catastrophe/reserving scenarios and releasable distributions. |
| 0700.HK | Data flag | Lease convention, material investee portfolio, cash restrictions, AI prepayments/capex and missing TTM SBC. No evidence justified falsely claiming investment gains contaminate operating profit. |
| NU | Not rankable | Country-specific regulatory capital and credit-loss forecasts; provider revenue/cash definitions remain unreconciled. Ownership itself is reconciled. |
| RIVN | Not screened | Loss-making business needs an explicit margin, reinvestment, funding and dilution path. |

## Verification and limits

- **184 tests passed**, including new filing, rounding, ownership, lease,
  accounting-basis and snapshot-integrity counterexamples.
- **18 fresh provider records** and their hash-verified final replay completed
  without acquisition/computation errors; no provider statement-fetch errors.
- **131 preserved records** replayed separately without execution errors;
  **62 positive-ceiling paths** passed funded-distribution invariants. At the
  11 October evaluation date, that corpus had 50 data flags, 48 not-rankable,
  21 not-screened and 12 above-ceiling names; zero eligible.
- The 14-record golden changes were inspected before updating the local
  expectation: reported-profit selection, lease gates, Nu's sourced common
  classes and Intel's reported operating loss explain every change. Original
  expectations and the comparison were retained; no test was weakened.
- Independent source review and independent patch review were completed.
  JavaScript syntax and Git whitespace checks passed.

The filing review is targeted. It does not independently certify every TTM
component, quote, share count, FX timestamp, security right, accounting
normalization or fair value. Fiscal-date labels were bridged explicitly, not
mistaken for filing dates. The live provider was contacted on 11 October in
Europe/Vienna; most stock quotes are from the preceding market session.

Static purchase-policy rates, consensus growth, marginal returns and terminal
economics are not validated investment forecasts. No survivor-aware backtest or
predictive-performance claim was made. Sector models still need sourced forward
assumptions; observed capital or FFO does not identify those assumptions. Missing
lease fields likewise do not establish absence of leases. Existing production
reports remain saved historical runs and should be regenerated before use with
the revised model. Experimental ideas remain outside production.

Reproduce with `tools/validate_live.py --valuation-date 2026-10-11 --output-dir
<new-directory>`; add `--replay <acquisition-directory>` for a network-free replay.
The output directory is isolated from `data/history` and `reports`.
