# ASTRA P1 EXECUTION — Independent Eligibility Receipt Producer

MISSION_ID=NEURUH-RECIPE-P1-INDEPENDENT-ELIGIBILITY-20260908
ROLE=RECIPE_ENGINE_OWNER_IMPLEMENTATION
CANONICAL_OWNER=NeuruhAI/recipe-engine
BASE_SHA=4eddb8fe332b63f57edcdf0fab23aa4633572103
BRANCH=astra/p1-independent-eligibility-producer-20260908
RISK=SOURCE_ONLY
SUPABASE_MUTATION=FORBIDDEN
DEEDSONAR_MUTATION=FORBIDDEN
PRODUCTION_OUTREACH=FORBIDDEN
MERGE=FORBIDDEN
DEPLOY=FORBIDDEN

## Why this mission exists

Astra's universal convergence court closed at:
lead-recipe-os aaee1daef45d021c389dd62eca2928219f1ebe1e

The court found exactly one bounded missing runtime capability:

P1 — independent eligibility receipt producer.

The current Recipe Engine can rank/evaluate a row through ordered recipe logic, but there is no proven producer that evaluates one candidate/property independently against each requested recipe and emits immutable per-candidate/per-recipe eligibility receipts.

Do not rebuild Recipe Engine.
Do not build a second membership store.
Do not touch Supabase.
Do not wire DeedSonar QuerySpec.
Do not derive secondary memberships from rank, primary_recipe, signal tags, component contributions, or UI labels.

## Required semantic output

For each:

tenant
universe
candidate/opportunity
asset/property
recipe
recipe_version
policy/composition version
evidence set
as_of/freshness context

evaluate independently and emit exactly one decision:

MATCH
NO_MATCH
UNKNOWN

UNKNOWN must remain distinct from NO_MATCH.

A missing/blocked/stale source must never silently become NO_MATCH.

The receipt must preserve:

tenant_id
universe_id
opportunity_id
asset_id
recipe_id
recipe_version_id
policy_digest
composition_digest
evidence digests / source lineage
decision
clause-level explanation/evidence
evaluated_at
valid_until / freshness boundary
content hash
producer identity/version
withdrawal/supersession relationship if reevaluated

The producer must not:

mint a candidate
promote a lead
authorize contact
write CRM
write Supabase
contact anyone
collapse multiple recipes into primary_recipe

## Astra intake contract

Read the source-only intake contract created by Astra on:

NeuruhAI/lead-recipe-os
commit aaee1daef45d021c389dd62eca2928219f1ebe1e

Relevant artifacts include:

contracts/kernel/independent_eligibility_intake.v1.schema.json
contracts/kernel/membership_observation.v1.schema.json
lead_recipe_os/kernel/membership.py
tests/fixtures/convergence/independent_eligibility.json
MULTI_RECIPE_MEMBERSHIP_RULING.md
ASTRA_TOP_7_CONVERGENCE_PREDICATES.md

Treat Lead Recipe OS as consumer contract evidence, not as evaluator authority.

Recipe Engine remains producer owner.

## First court — current owner census

Before implementation, locate current canonical evaluation machinery, especially:

scripts/rank_nc_curative_raw.py
recipe_engine/opportunity_intelligence/oppack_receipts.py
recipe_engine/opportunity_intelligence/*
contracts/opportunity_intelligence*
RECIPE_SOURCE_COMPATIBILITY.csv
RECIPE_NBE_COVERAGE.csv
RECIPE_PROCESS_CLOCK_COVERAGE.csv
RECIPE_SCORE_VECTOR_COVERAGE.csv

Classify:

EXISTING_REUSABLE
ORDERED_PRIMARY_ONLY
INDEPENDENT_ALREADY_EXISTS
MISSING_SEAM
DUPLICATE
SUPERSEDED

If an independent evaluator already exists, do not create another one. Bind/test it.

## Required implementation

Implement the minimum canonical producer that can:

1. accept one normalized candidate/property evidence context;
2. accept an explicit set of recipe IDs/versions to evaluate;
3. evaluate each recipe independently;
4. preserve source coverage/freshness/UNKNOWN;
5. emit deterministic immutable eligibility receipts;
6. replay idempotently;
7. withdraw/supersede stale prior evaluation without deleting history;
8. keep recipe order irrelevant to eligibility truth;
9. produce no storage side effects by default.

Prefer pure functions + deterministic receipt emitter first.

Runtime/storage binding remains separate unless an existing safe source-only ledger boundary already owns it.

## Mandatory proof fixture

Create at least one property/candidate fixture with enough evidence to prove:

Recipe A = MATCH
Recipe B = NO_MATCH
Recipe C = UNKNOWN

on the same asset.

Then prove:

- changing evaluation order does not change decisions;
- same inputs + same versions reproduce same receipt hashes;
- changing recipe version changes receipt identity/version but preserves candidate/property identity;
- source withdrawal/staleness does not become NO_MATCH;
- missing required evidence yields UNKNOWN;
- negative evidence can produce NO_MATCH only when the recipe contract defines that predicate;
- no primary_recipe or rank field is accepted as authority;
- no candidate/lead/contact transition occurs;
- two independent MATCH receipts can coexist for one property.

## Required output / handoff

Create:

docs/P1_INDEPENDENT_ELIGIBILITY_PRODUCER_RULING.md
artifacts/P1_CURRENT_EVALUATOR_CENSUS.tsv
artifacts/P1_RECEIPT_CONTRACT_CROSSWALK.tsv
fixtures / tests
source implementation
P1_FINAL_RECEIPT.json

Final receipt:

STATUS=
MISSION_ID=
BRANCH=
START_SHA=
FINAL_SHA=
CURRENT_EVALUATOR=
NEW_PRODUCER=
RECIPES_IN_FIXTURE=
MATCH_PROOF=
NO_MATCH_PROOF=
UNKNOWN_PROOF=
ORDER_INDEPENDENCE=
IDEMPOTENT_REPLAY=
WITHDRAWAL_SUPERSESSION=
LEAD_PROMOTION=NO
CONTACT_AUTHORITY=NO
SUPABASE_MUTATED=NO
DEEDSONAR_MUTATED=NO
TESTS=
BLOCKERS=
NEXT_CONSUMER=DEEDSONAR_MEMBERSHIP_READ_AFTER_P3_AND_PR52_APPLY
FOUNDER_ACTION_REQUIRED=

Stop if implementation would require Supabase, DeedSonar, production runtime promotion, or a second recipe engine.
