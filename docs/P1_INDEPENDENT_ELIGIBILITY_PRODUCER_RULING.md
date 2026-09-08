# P1 — Independent Eligibility Producer: Ruling

MISSION_ID=NEURUH-RECIPE-P1-INDEPENDENT-ELIGIBILITY-20260908
ROLE=RECIPE_ENGINE_OWNER_IMPLEMENTATION
BRANCH=astra/p1-independent-eligibility-producer-20260908
BASE_SHA=4eddb8fe332b63f57edcdf0fab23aa4633572103
RISK=SOURCE_ONLY

Astra's convergence court (lead-recipe-os `aaee1daef45d021c389dd62eca2928219f1ebe1e`)
ruled one bounded missing runtime capability: a producer that evaluates one
candidate/property independently against each requested recipe version and
emits an immutable `MATCH` / `NO_MATCH` / `UNKNOWN` receipt. This document is
the Recipe Engine owner's ruling on that seam, the census that preceded it,
and what was built.

## 1. Census ruling (see `artifacts/P1_CURRENT_EVALUATOR_CENSUS.tsv`)

No independent evaluator existed. Three families were found and classified:

- **ORDERED_PRIMARY_ONLY.** `scripts/rank_nc_curative_raw.py::recipe(row)` is an
  if/elif chain that returns exactly one recipe. It reads a missing indicator
  as `False` (`truthy(None)`) and unknown tenure as `0`, so a property with no
  probate evidence is silently "not heir". The oppack match/rank receipts
  explain that single assignment; `graduation.classify`, the NC DPS
  `choose_strategy`, and the autonomy selection engine are likewise one-policy
  or rank-producing paths. None can state that a property is *also* eligible
  for a second recipe, and none can state UNKNOWN as a decision.
- **EXISTING_REUSABLE.** The canonical signal vocabulary
  (`recipes.py` signal_rules), `SIGNAL_STATES` / `CONFIDENCE_LEVELS`
  (`contracts.py`), the signal fixture shape (`fixtures.py`), the
  UNKNOWN-never-collapsed discipline (`evaluate.py`), and the
  id+version+digest binding of `closed_loop/recipe_eval.py::RecipeRef` were
  reused as vocabulary and discipline. The closed-loop `LoopLedger` was
  deliberately **not** bound: routing receipts into the event grammar widens
  scope, and the mission keeps storage binding a separate seam.
- **INDEPENDENT_ALREADY_EXISTS (consumer side only).** Lead Recipe OS ships an
  intake *court* (`lead_recipe_os/kernel/membership.py`) and an 18-field
  intake schema. It validates bindings; it evaluates nothing. It is treated
  as consumer contract evidence, not evaluator authority.
- **MISSING_SEAM.** A recipe contract that *declares eligibility predicates*.
  `neuruh.opportunity-intelligence.recipe.v1` is a points policy; nothing in
  the repository said, per recipe, which signals must be present or absent,
  or whether negative evidence is the opposite fact.

Ruling: build the smallest producer beside the primary feed, not inside it.
`rank_nc_curative_raw.py` and the oppack receipts are untouched and their
outputs are refused as inputs.

## 2. What was built

`recipe_engine/opportunity_intelligence/eligibility.py` — pure functions, a
deterministic receipt emitter, and a process-local journal. No I/O, no clock,
no transport import.

Three contracts (schemas in `contracts/eligibility.*.v1.schema.json`):

| contract | role |
|---|---|
| `neuruh.recipe-engine.eligibility-policy.v1` | one recipe version: `clauses[]` over canonical signal types, each with `expect` (PRESENT/ABSENT), `negative_evidence_decisive` (explicit boolean), `min_confidence`, optional `max_age_seconds`; `validity_seconds`; `composition`; `policy_digest`; `composition_digest` |
| `neuruh.recipe-engine.eligibility-context.v1` | one candidate's evidence: identity, `as_of`, `evidence[]` with custody (`ledger_state`, `ledger_ref`, `withdrawn_at`), `signals[]` citing that evidence. Recipe-agnostic; rank/score/primary_recipe/signal_tags/component_contributions/stage/contact fields are refused |
| `neuruh.recipe-engine.eligibility-receipt.v1` | one candidate × one recipe version × one decision, with clause records, evidence lineage, `evidence_digests`, `membership_key`, `context_digest`, `evaluated_at`, `valid_until`, producer identity/version, `supersedes`, `content_sha256` |

Decision semantics (`derive_decision`, re-run by the receipt validator):

1. A clause is **SATISFIED** or **CONTRADICTED** only by a known observation
   (PRESENT/ABSENT) whose confidence meets the clause floor and which cites at
   least one BOUND, un-withdrawn, fresh evidence record.
2. Anything else — no signal, UNKNOWN or CONFLICTED state, confidence below
   floor, no evidence lineage, unbound / withdrawn / stale evidence, or two
   known observations that disagree — leaves the clause **UNKNOWN** with a
   stated reason code.
3. **NO_MATCH** requires a CONTRADICTED clause whose recipe declared
   `negative_evidence_decisive: true`. A contradiction on a non-decisive
   clause is negative evidence, not the opposite fact: the decision is
   UNKNOWN (this encodes the `RECIPE_SOURCE_COMPATIBILITY.csv` rule that
   "NO_MATCH is negative evidence not opposite legal fact" for probate).
4. A decisive contradiction outranks an UNKNOWN clause in the same recipe: a
   known exclusion is a finding, not a gap.
5. **MATCH** requires every clause SATISFIED. A known decision must cite
   evidence; the producer refuses to emit one otherwise.
6. `evaluated_at` is the caller's `as_of`. `valid_until` is the tighter of
   `as_of + validity_seconds` and every supporting evidence's
   `observed_at + max_age_seconds`. It is always after `evaluated_at`.
7. Each policy is evaluated with no other policy in scope; `evaluate_recipes`
   returns receipts in canonical `(recipe_id, recipe_version_id)` order.

Identity and replay:

- `membership_key = content_hash({tenant_id, universe_id, opportunity_id, recipe_id})`
  — version-independent, identical to the consumer's derivation.
- `context_digest` is taken over the canonical context (evidence and signals
  sorted by id) so caller ordering cannot change a receipt.
- `content_sha256` uses the consumer's algorithm exactly
  (`ensure_ascii=False`, sorted keys, compact separators, self omitted). The
  Astra fixture receipt's hash reproduces from this repository; the
  repository's own `identity.digest` differs only for non-ASCII payloads and
  is therefore not used for receipts.
- Same context + same policy + same `supersedes` → same hash. Replay into the
  journal appends nothing.

Supersession without deletion (`EligibilityReceiptJournal`):

- one live receipt per `membership_key`; a second must name what it
  supersedes; the superseded receipt stays byte-identical in `history()`;
  a receipt cannot supersede another membership, an unknown receipt, a later
  evaluation, or a receipt already superseded by a different one.
- Source withdrawal is expressed by re-evaluation: withdrawn evidence makes
  the clause UNKNOWN, and the UNKNOWN receipt supersedes the prior MATCH.

Consumer projection (`to_intake_receipt`): exactly the 18 fields of
`neuruh.lead-recipe-os.independent-eligibility-intake.v1`, re-hashed with the
consumer algorithm, refused for any mode but FIXTURE (the intake court is
fixture-only; relabelling would be a lie).

## 3. Mandatory fixture and proofs

`tests/opportunity_intelligence/fixtures/p1_independent_eligibility.json` —
ONE property (`parcel-37183-0123456`, candidate `candidate-willow-120`),
four evidence records, six signals, four recipe versions:

| recipe | decision | why |
|---|---|---|
| A `published_tax_distress` | MATCH | tax_delinquent PRESENT (HIGH, tax roll) and government_owner ABSENT |
| B `stalled_tax_foreclosure_heirship` | NO_MATCH | pre_foreclosure ABSENT on a searched docket, clause decisive; estate clause UNKNOWN in the same recipe does not soften it |
| C `open_probate_property` | UNKNOWN | probate_or_estate UNKNOWN (index search inconclusive); non-decisive clause |
| D `long_term_absentee_potential_equity` | MATCH | absentee_owner + long_hold PRESENT, 30-day freshness satisfied; second MATCH on the same asset |

Sealed content hashes live in the fixture's `expected` block and in
`artifacts/P1_FIXTURE_PROOF.json` (byte-stable across runs; no wall clock,
no SHA, no host path).

Proved by `tests/opportunity_intelligence/test_independent_eligibility.py`
(62 tests) and `scripts/p1_independent_eligibility_proof.py` (13 proofs):

- order independence: all 24 recipe permutations, reversed evidence/signal
  order, and each-recipe-alone produce identical hashes;
- idempotent replay: identical hashes; journal append returns `False` on
  replay and does not grow;
- version binding: a new `recipe_version_id` changes `policy_digest` and
  `content_sha256`, keeps `membership_key`, `opportunity_id`, `asset_id`;
- source withdrawal / staleness / unbound custody / missing signal / low
  confidence / no lineage / conflict → UNKNOWN, never NO_MATCH;
- negative evidence → NO_MATCH only when the clause is decisive;
- rank / primary_recipe / score / signal_tags / component_contributions /
  stage / contact fields refused as inputs and never emitted;
- no I/O (files, sockets, sqlite patched to raise during evaluation);
- two MATCH receipts coexist for one property under distinct membership keys;
- tampered receipts fail on digest, then on clause derivation.

Cross-repo court `tests/opportunity_intelligence/test_p1_lead_recipe_os_intake_court.py`
(6 tests, run against the Lead Recipe OS kernel at `aaee1da`): the consumer's
own `accept_fixture_eligibility` accepts all four projections, preserves each
decision, derives the same `membership_key`, reports
`production_write=false` and `contact_authorized=false`, and rejects a
tampered projection. Skipped unless `LEAD_RECIPE_OS_KERNEL_PATH` is set.

## 4. What was deliberately not done

- No Supabase, DeedSonar, migration, outreach, CRM, lead or contact code path.
- No second recipe engine and no second membership store: the journal is a
  process-local supersession court, not a store; DeedSonar's
  `candidate_recipe_membership` remains the membership store.
- No change to `rank_nc_curative_raw*.py`, oppack receipts, or the canon CSVs.
- No storage binding (JSONL / closed-loop ledger / Founder Runtime
  persistence). That is the next seam and stays separate.
- No production recipe catalog: the four fixture policies encode existing
  census recipe ids but are fixture policies, not the P3 crosswalk.

## 5. Next consumer

`NEXT_CONSUMER=DEEDSONAR_MEMBERSHIP_READ_AFTER_P3_AND_PR52_APPLY`. When P3
produces the canonical catalog crosswalk and DeedSonar PR52 lands its
membership table, the DeedSonar owner reads accepted MATCH memberships
(`membership_key`, `receipt_sha256`, `valid_until`) into the existing
QuerySpec. UNKNOWN and NO_MATCH remain observable, never membership.
