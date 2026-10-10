# Audit-remediation verification — 11 October 2026

The pre-change working files were preserved in checkpoint `0989ca8`, on the
isolated `codex/valuation-audit-fixes` branch. The shared checkout was not edited.

## Results

- **169 tests passed**, including the local 14-record frozen Yahoo regression.
  The full command was `python -B -m pytest -q -p no:cacheprovider`.
- **300 reproducible numerical cases**, seed `100102026`: 298 unique solutions
  and two correctly identified multiple-solution cases. Every reported solution
  matched its known forward case; maximum relative forward residual was
  **2.25e-12**. Horizons included 1, 3, 5, 10, 20, 35 and 50 years; monetary scales
  ranged from 1e-12 to 1e12. This validates numerical behavior, not forecasts.
- **131 preserved provider records** replayed at the explicit valuation date
  **2026-10-10**, with no execution errors. All 64 positive-ceiling paths satisfied
  the funded-distribution invariants. The replay produced six eligible, 44 above
  ceiling, 14 data-flagged, 49 not-rankable and 18 not-screened records. These are
  diagnostic counts from saved inputs, not a current investment recommendation.
- Live Yahoo fetch/quote paths were exercised for **ADBE, NU and LSEG.L** on
  10 October. Dated quotes, extended statement/analyst retrieval and immutable
  snapshots worked. NU abstained on unresolved ownership/sector evidence; LSEG
  exposed the older coherent balance period rather than presenting mixed dates
  as current. These probes do not independently certify the reported accounts.
- The CLI-to-Excel/Markdown/CSV/provenance path passed tests, including an entirely
  unrankable run. Dashboard overview, method and issuer inspector were exercised
  in a headless browser: no page errors or document-level horizontal overflow.
  JavaScript syntax and Git whitespace checks passed.

## What changed in the expectations

Golden differences were inspected before updating the isolated checkout's local
expectations. The original shared golden data was preserved. Changes followed
the corrected data/funding contracts, country-risk separation, return assumptions,
and period/ownership alignment; no parameter was tuned to recover old scores.

The core regression cases independently exercise financial TTM selection without
EBIT, coherent dates, missing capital, common/parent/consolidated equity, preferred
claims, ownership units, capital-period alignment, terminal funding, contraction,
nonunique/tangent roots, monetary/currency scaling, and finite-payout domains.
Sector references cover maintenance investment, funding shortfalls, regulatory
capital retention, property distributions, lag runoff, finite-life closure costs,
claim/rate matching and the two entry-price constraints. Source-backed rate
overrides are not ignored, and a low policy hurdle cannot justify paying above
the modeled base value of the same cash flows.

## Evidence and limits

Generated receipts and before/after comparisons are retained locally under
`data/validation/`; live probes under `data/live-validation/`; UI/report checks
under `data/ui-validation/`. A second copy of verification evidence is retained
with the original audit outside this managed checkout. These generated artifacts
and provider fixtures remain untracked, as required by the repository convention.

The frozen FX rates were reconstructed from the saved 2026-10-10 screening report
and carry its hash. They are not freshly synchronized market quotes. A replay
date fixes computation/freshness checks; it does not eliminate look-ahead from
later provider revisions or establish historical investment performance.

No complete issuer-by-issuer filing tie-out or return backtest was performed.
The SEC adapter was tested with controlled publication-date/unit/conflict cases;
a live SEC validation requires an explicit CIK and configured `SEC_USER_AGENT`.
Sector forecasts require their documented sourced inputs; missing inputs abstain.
Automatic operating outputs remain accounting proxies with disclosed assumptions.
Experimental estimation and probabilistic methods remain outside production.

## Integration into main

The user confirmed the previous lease owner had finished and authorized committing
its pending work. Commit `b313055` preserves those edits separately; its tree is
identical to checkpoint `0989ca8`. The valuation-only commit was applied as
`da05774` and merged in `64f31f6` without conflicts. The resulting tree matched
the reviewed implementation `b73bbf9` exactly. A trailing blank line was removed
during the final whitespace check; no model behavior changed during integration.

Post-integration verification ran from the shared `main` checkout:

- All **169 tests passed**. All 14 frozen fixtures matched the isolated checkout
  by SHA-256. The new golden output exactly matched the previously reviewed
  expectations; the old local golden file was backed up before replacement.
- All **131 saved provider records** computed successfully; **64 funded paths**
  passed the invariants. Main retains its local DXYZ fund override, so the result
  was six eligible, 44 above ceiling, 14 data-flagged, 48 not-rankable and 19
  not-screened records. An isolated replay without that local override confirmed
  identical absolute valuations and statuses for every other issuer; only the
  relative financial-sector rankings changed when DXYZ left that comparison set.
  The earlier saved corpus also predates the final broker model labels for HOOD
  and IBKR; this affects labels only, not their valuations or abstention status.
- All **300 numerical cases** were replayed: 298 unique solutions, two correctly
  identified ambiguous cases, maximum relative forward residual **2.25e-12**.
- The integrated dashboard overview, method and issuer inspector passed the
  browser check with no page errors or horizontal overflow. JavaScript syntax
  and the final Git whitespace check passed.

Backups, fixture hashes, raw replay results, differences, test output and browser
receipts are retained beside the original audit in `integration-2026-10-11/`.
These are saved-data checks; no new live filing tie-out or predictive backtest
was performed. Local inputs, provider caches and production reports were preserved.
