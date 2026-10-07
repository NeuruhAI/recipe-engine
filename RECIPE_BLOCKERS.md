# T08 Recipe Blockers

## Active True Blockers

1. `JOIN_KEY_AUTHORITY_BLOCKED` for tax-sale scoring
   - `source_factory` tax-sale rows are present, but T02 does not publish an authoritative `source_id/source_key -> canonical tax-sale-stage` binding in `JOIN_CONTRACTS.yaml`.
   - T08 preserves tax-sale observations as explicit unscored signals (`source_factory.taxsale`, `source_factory.tax_sale`) and does not fabricate a scoring join.

2. Probate source coverage remains rights-bounded
   - `gap.probate.court_index` is WALLED/SOURCE_REQUIRED in T03 handoff and cannot be promoted to PASS without authorized source operations.
   - Open-probate and pre-probate recipe families remain partial/contract-only in this repository.

3. Recorder-dependent title/equity proof remains WALLED
   - `gap.deed.recorded_instruments` and mortgage/release/lien stack proof remain unavailable as free authoritative feeds in this lane.
   - Equity stays proxy-only; UNKNOWN is preserved and not coerced into zero.

4. Listing history and vacancy official feeds remain unavailable
   - `gap.listing_history.mls` is WALLED.
   - `gap.vacancy.official` is MISSING.
   - Related recipes remain contract-only or missing.

5. Live HTTP LandOS evaluate seam remains unwired unless a real endpoint exists
   - T08 did not edit LandOS Engine.
   - Without a real listener and auth surface, `LIVE_HTTP_LANDOS_EVALUATE` stays `UNWIRED`.

## Non-Blockers Already Implemented In T08

- `tax_foreclosure_case` now maps to canonical scored signal `pre_foreclosure` in the source-factory adapter.
- Deterministic receipts are emitted for:
  - Why This Matched
  - Why This Ranked
  - Next Best Evidence
- Tax-sale observations are preserved fail-closed with explicit `JOIN_KEY_AUTHORITY_BLOCKED` metadata.
