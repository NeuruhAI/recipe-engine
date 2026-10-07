# Multi-membership projection — `DEEDSONAR_MEMBERSHIP_PROJECTION.csv`

STATUS: BUILT. Not live, not merged, not deployed, not loaded.

## The collapse point

`scripts/rank_nc_curative_raw.py::recipe` (the first-match-wins ladder) returns
exactly ONE `recipe_id` per row. It is consumed by `rank_rows` and stamped on
the row as `recipe_id`; every other ladder branch whose predicate is also true
is discarded there. The function has no docstring and its only commit
(`9a409e6`, "feat(nc-dps): rank raw title-curative opportunity universe") gives
no rationale, so the collapse is an implementation choice, not a written
contract. The single-winner contract is nonetheless courted and load-bearing
(`recipe_engine_receipts_stage` in DeedSonar was loaded 1:1 from it), so the
ladder is **not modified**.

Two existing columns are often misread as membership and are not:

- `recipe_membership_count` is the cohort size of the winning recipe.
- `component_contributions` is the score decomposition. Its `factor_id`s are
  thresholds and proxies; inferring recipes from them is refused.

## What was added (additive only)

- `scripts/rank_nc_curative_raw.py::recipe_matches(row)` — the same predicates
  as the ladder, in the same order, without the collapse. `recipe_matches(row)[0]
  == recipe(row)[0]` for every input (exhaustively tested). The fallthrough
  `published_tax_distress` is emitted only when no positive branch fires,
  because the ladder never evaluates a tax-distress predicate; deriving one from
  `source_types` or a score factor would be a new recipe predicate.
- `scripts/project_deedsonar_receiver.py` — emits both artifacts from one
  ranked run:
  1. `DEEDSONAR_RECEIVER_PROJECTION.csv` — the existing 27-column single-winner
     contract, ported verbatim from the private Aug-27 materializer so it is
     reproducible from the repository. Unchanged contract.
  2. `DEEDSONAR_MEMBERSHIP_PROJECTION.csv` — the new contract below.
- `tests/test_membership_projection.py` — 15 unittest cases.

## Membership contract `deedsonar.membership-projection.v1`

One row per `(property_key, recipe_id)` MATCH. Multiple rows per property.

| column | meaning |
|---|---|
| `property_key` | `STATE:FIPS:APN`, identical to the receiver projection |
| `county_fips`, `parcel_apn` | native ranker identity (the join key) |
| `recipe_id` | a ladder recipe whose predicate holds for this property |
| `recipe_version` | `nc_curative_recipes.v1` |
| `match_rank` | 1-based position in ladder precedence; the primary is 1 |
| `is_primary` | `true` on exactly one row per property |
| `why_matched_receipt` | `build_why_this_matched` for this `(property, recipe_id)` |
| `ranking_run_id` | `ranking_run_<YYYYMMDD>_<input sha256[:12]>` |
| `input_hash` | the receipt's `input_hash` (binds `recipe_id`, so it differs per membership) |

Invariants asserted at emission time (fail closed, `ProjectionParityError`):

- the `is_primary=true` row's `recipe_id`, `why_matched_receipt` and
  `input_hash` equal the single-winner row for the same property;
- exactly one primary per property; `match_rank` contiguous from 1; no
  duplicate `(property_key, recipe_id)`;
- the fallthrough id never appears beside a positive match;
- secondary receipts share `facts_used` with the primary (same property, same
  facts, different recipe id).

The artifact is additive: the receiver projection is byte-identical to the
Aug-27 artifact except the `ranking_run_id` column (proven 2026-09-09, see
`~/neuruh-workspace/RECIPE_MULTIMEMBERSHIP_20260909/RECEIPT.md`).

## Running

```
PYTHONPATH=. python3 scripts/rank_nc_curative_raw_v3.py --input <ranker input> --output <run dir>
PYTHONPATH=. python3 scripts/project_deedsonar_receiver.py \
    --ranked <run dir>/NC_CURATIVE_ALL.csv --input <ranker input> \
    --output-dir <projection dir> --run-date YYYYMMDD [--master <deedsonar master csv>]
```

Emits files only. No database write, no outbound, no provider spend. A human
loads the artifacts; `is_primary=true` maps to DeedSonar
`candidate_recipe_membership.membership_class = 'PRIMARY_PROJECTION'`, the
class for secondary rows is the loader's decision under the DeedSonar court
`docs/courts/CANDIDATE_RECIPE_MEMBERSHIP_V1_20260907.md`.

## What multi-membership does not unlock by itself

`published_tax_distress` and `out_of_state` are not positive predicates in the
ladder (fallthrough and half of a conjunction respectively), so tax-conjunction
and absentee-conjunction recipes remain inexpressible from this emission even
though their source facts are present on the input. That is an engine
predicate decision, not a source gap, and is reported separately in the receipt.
