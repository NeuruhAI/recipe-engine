# The closed loop — WORLD → DECISION → ACTION → OUTCOME → LEARNING → WORLD

`recipe_engine/closed_loop/` is the front half of Neuruh's learning spine, in one
event grammar, on one hash-chained ledger, with one identity contract.

The back half already existed and is not rebuilt here. `neuruh-outcome-record`
(the shared `neuruh.outcome-record.v1` primitive), Public Commons `011` Evidence
Ledger, `015` Decision Receipt, `017` Outcome Calibration Ledger, `019` Learning
Update Proposal, `020` Promotion Gate, `021` Canary, `023` Deployment
Authorization, `026` Lifecycle State, `029` Drift, `030` Reconciliation — all of
those stay where they are. What was missing, per `neuruh-ledgers`
`C0_CONVERGENCE/LEARNING_LOOP_GAP.md`, was the front half: nothing bound a
source observation through a versioned recipe evaluation and a decision to an
action, so nothing could later be attributed. This package is that binding.

## The doctrine, in one sentence

**The system may learn from what actually happened, and may not use learning as a
way to lie or to deploy itself.**

Every refusal in the package is that sentence restated somewhere it can be
tested.

## Modules

| Module | What it holds | The refusal that matters |
|---|---|---|
| `identity` | world / subject / actor / run / mission / decision / action refs, evidence refs, `WorldMode` | a `KNOWN` world mode is part of identity, so a fixture cannot be relabelled live |
| `events` | the 21-member event family, per-type required bindings, declared causal claims | `DECISION_APPROVED` with no decision ref; any payload carrying chain-of-thought |
| `ledger` | append-only hash chain, JSONL backing | forward causal references; renamed events; replay that grows the ledger |
| `world_state` | `KNOWN` / `INFERRED` / `UNKNOWN` / `EXTERNAL_GATE`, belief deltas | inference alone can never make a thing `KNOWN` |
| `recipe_eval` | `RecipeRef` (id + version + config digest), evaluation record | a score with no components; a qualified candidate naming no evidence |
| `decision` | opportunity + evidence + evaluation + governance + authority | approval that cannot name its authority; prose masquerading as a feature |
| `action` | intent declared before execution, receipt bound to intent | an execution with no declared intent |
| `forecast` | sealed pre-outcome prediction | hindsight: `recorded_at` not strictly before `observed_at` |
| `outcome` | PROCESS / BUSINESS / WORLD tiers, generic metric vocabulary | a business or world outcome asserted without evidence |
| `attribution` | DIRECT / SUPPORTED / ASSOCIATED / UNKNOWN, multi-contributor | a share with no declared allocation basis |
| `calibration` | per-cell Brier, buckets, recipe/source/county/owner-class/decision cuts | a scored claim under the 30-sample floor; any report that mutates production |
| `learning` | candidate version, evidence window, expected benefit, regression risk | a claim with no numbers; a weight change under 100 samples; no holdout |
| `promotion` | ELIGIBLE / HELD / BLOCKED, then `VersionPromotion` | non-live lineage can never be eligible; eligibility is never deployment authority |
| `source_learning` | (owner class × source) yield cells, ranking | a lucky five-attempt source outranking a measured one |
| `recipe_learning` | four independent tracks + `diagnose()` | there is deliberately no composite score |
| `replay` | deterministic reconstruction of the whole story | gaps are reported, never bridged by inference |
| `bridges` | projections onto `outcome-record.v1`, `017`, `019`, `020` | the producer can never assert calibration eligibility |

## Why the tiers and tracks are separate

"Task completed" is a process fact. A loop that learns from process outcomes
learns to complete tasks. So `outcome.OutcomeTier` splits PROCESS from BUSINESS
from WORLD, and business/world observations require evidence.

Similarly, a recipe can produce excellent opportunities whose owners are almost
impossible to reach. Averaged into one number that reads as a bad recipe, the
system learns to stop looking where the value is. `recipe_learning` therefore
tracks OPPORTUNITY_QUALITY, CONTACTABILITY, CALLABILITY and TRANSACTION_SUCCESS
independently, each over its own denominator, and `diagnose()` names the binding
constraint instead of returning a score.

## The world-mode rule

`promotion.evaluate_promotion` HOLDs any proposal whose evidence lineage includes
a non-`LIVE` mode. This is the rule the public `020` primitive has no way to
know, and it is what makes a full synthetic court safe to run: the loop
traverses every stage and stops.

```
$ python3 scripts/run_closed_loop_e2e.py --out-dir /tmp/court
mode              : synthetic
promotion         : HELD (deployment_authority=False)
  · evidence lineage includes non-live modes (synthetic); a fixture may prove
    the mechanism and may never become a production change
versions promoted : 0
```

`--mode live` runs the identical path and reaches ELIGIBLE — which is how we
know the HOLD above is the mode rule biting rather than the loop being unable to
finish. Even then nothing deploys: `promote()` requires an external approval
reference this package cannot mint.

## Relationship to `graduation.py`

`graduation.classify` is not reimplemented. It is the reference recipe, and
`recipe_eval.from_graduation_verdict` lifts its verdict into a loop record,
carrying `policy_version` and `evidence_hash` straight across so a later
attribution names the exact policy that decided.

## The seam to `neuruh-outcome-record`

recipe-engine does not import `neuruh_outcome_record` at runtime. `bridges`
emits the shapes; `neuruh_outcome_record.adapters.from_closed_loop` ingests them
and validates with the real primitive. If the shapes drift, that adapter's court
fails — validation lives on the side that owns the primitive.

## Independent verification

`events.grammar_manifest()` exports the grammar as data.
`neuruh-ledgers/tools/verify_loop_ledger` consumes exactly that shape and can
verify a ledger without importing this package — so the producer is not the only
thing attesting to the producer.
