# Opportunity eligibility runtime (T11): `RUNTIME_CALL_PATH=BOUND`

On 2026-09-08, P1B sealed `RUNTIME_CALL_PATH=CALLER_ABSENT` (`docs/P1B_RUNTIME_CALL_PATH_RULING.md`). The independent eligibility producer existed and nothing in a runtime called it. The ruling named three missing seams. This document records how each one is closed, and the path that closes them.

```
Recipe Engine eligibility producer        eligibility.derive_decision + recipe_compiler (compiled canonical 30)
  -> canonical runtime invocation         scripts/run_opportunity_eligibility.py  (evaluate -> forecast -> apply)
  -> Lead Recipe OS coordination          membership_key + hashing canon + membership-observation semantics
  -> DeedSonar membership consumer        public.candidate_recipe_membership_eligibility_apply_v1 -> candidate_recipe_membership
```

| P1B missing seam | Closed by |
|---|---|
| live eligibility-context producer | `estate_row_evaluation_input`. The declared `NC_ESTATE_CROSSWALK` maps the NC curative estate ranker input onto the ingredients the canonical spec names a producer for on that input. |
| P3 production eligibility-policy catalog | `recipe_compiler` compiled canonical 30 (PR #22): 29 evaluable recipes plus R30 META. |
| chosen receipt/storage channel | DeedSonar `candidate_recipe_membership`, through the owner-only apply function, plus content-addressed run artifacts. |

## What a run does

1. **evaluate** (files only). Takes the exact ranking input the production ranking run consumed: `--ranking-run-id` must carry `sha256(input)[:12]`, and the parent input receipt must declare the same sha. For every property and every compiled recipe, it runs one independent evaluation, which yields MATCH, NO_MATCH or UNKNOWN plus a qualification state. It writes:
   - `PROPERTY_DECISIONS.jsonl`: every decision for every property, with the recipe-evaluation content sha. NO_MATCH and UNKNOWN stay observable.
   - `ELIGIBILITY_MEMBERSHIPS.jsonl`: MATCH only. Each line carries its eligibility-membership receipt.
   - `ELIGIBILITY_RUN_RECEIPT.json`, containing:
     - the input hash;
     - output hashes;
     - pipeline version;
     - code commit, with `tree_clean` and `on_origin_main`;
     - schema versions;
     - `as_of`, `generated_at` and `valid_until`;
     - the parent receipt;
     - the census: per recipe, MATCH, NO_MATCH and UNKNOWN each counted.
2. **apply --mode forecast**. The DeedSonar function validates and plans inside a `READ ONLY` transaction and writes nothing.
3. **apply --mode apply --confirm-run-id <run_id>**. It can be scoped with `--property-key` for a controlled activation. Production writes need a run evaluated from a clean tree at a commit on `origin/main`, with no override.

## Laws

- **UNKNOWN != zero.** A blank cell produces no observation, so the recipe stays UNKNOWN.
  - An explicit `False` is non-decisive negative evidence, which also leaves the recipe UNKNOWN. It becomes NO_MATCH only where the spec marks the clause decisive.
  - A non-positive assessed value is a placeholder, not a valuation.
  - UNKNOWN is counted per recipe in the census. It is never projected, and never written as 0 or NO_MATCH.
- **rank != eligibility.** A ranked output (rank, recipe_id, score, tier, receipts) is refused as input. Ranking, routing and contact keys are refused in every artifact. Moving non-evidence columns never moves a decision.
- **recipe membership != result-set membership.** A membership row is exactly one MATCH decision with its own receipt. No query or cohort produces one. DeedSonar's `membership.ts` (result-set membership, `deedsonar_result_set_members_v1`) is untouched.
- **Owner text.** The same `strict_v2` classifier the production ranking consumed (`scripts/rank_nc_curative_raw_v2`), without its blank -> False collapse.

## The consumer (DeedSonar `20260915140000`)

- MATCH lands as `ELIGIBLE` with:
  - `recipe_version = <recipe_id>@compiled.<digest12>`
  - `scoring_version = ''`
  - `recipe_rank` / `recipe_score` NULL
  - `source_receipt_id` = recipe-evaluation sha
  - `explanation` = the canonical receipt bytes
- The explanation answers DeedSonar's existing reader fields (`signals_triggered`, `negative_evidence`, `unknown_inputs`, `disqualifiers_checked`, `source_ids`, `coverage_state`), with their meaning kept. An unresolved disqualifier is in `disqualifiers_unresolved`, never in `disqualifiers_checked`.
- **Lineage lock.** Every bound estate row's bag must be ranked on the input the eligibility run evaluated. Membership moves with the estate's current ranking input, or not at all.
- **Retirement.** Within a call's scope, this writer's rows that a newer run no longer asserts are deleted: a MATCH that went UNKNOWN or NO_MATCH, or a superseded compiled version. Omission never preserves a stale MATCH. PRIMARY_PROJECTION, HELD and ladder ELIGIBLE rows are never touched.
- The batch stamp `source_context.recipe_eligibility` carries the run identity and its full census.

## Supervision

The runtime is on demand, with no ports and no daemon (doctrine of `neuruh-sops` `recipe-engine.pin`). It is not a Founder Runtime capability.

`recipe-engine-supervised` is shared with the Founder Runtime API, and Governance trusts the Founder Runtime at the pinned sha. Advancing that checkout needs the founder lineage supersede. Running this runtime does not.

## Courts

- `tests/opportunity_intelligence/test_opportunity_eligibility_runtime.py`: crosswalk, the three laws, provenance, replay. With `RECIPE_ENGINE_LOCAL_PG_URL` and `DEEDSONAR_ELIGIBILITY_MIGRATION` set, it also runs the cross-repo DB court: forecast -> tracer apply -> full apply -> no-op -> lineage refusal -> supersession/retirement.
- `tests/opportunity_intelligence/test_p1b_runtime_caller_absent.py`: exactly one bound runtime. The named candidates (ranker, daily hunt, Founder Runtime, ...) still never call the producer.
- DeedSonar `scripts/membership-eligibility-court.sh` (local PG17).
