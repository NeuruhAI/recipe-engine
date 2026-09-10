# Recipe Compiler — the canonical 30, executable

`recipe_engine/opportunity_intelligence/recipe_compiler.py` compiles the canonical 30
recipes (`canonical_recipes.py`) and evaluates every compiled recipe **independently**
against one candidate. For each recipe it produces:

```
INPUT SIGNALS → ELIGIBILITY → SCORE VECTOR → REQUIRED EVIDENCE → MISSING EVIDENCE → NBE → ECONOMICS → QUALIFICATION
```

It adds no new catalog and no new decision semantics. Recipe source is transcribed
from existing artifacts, and each one is pinned by commit and sha256 in
`SPEC_PROVENANCE`: the lead-recipe-os canonical-30 matrix, the GENOME-B ingredient
and process-clock registries, the DeedSonar `property_recipe_registry.v1` WHEN
clauses, and this repo's source-compatibility and NBE coverage CSVs. Membership
is decided by `eligibility.derive_decision`, the same function the P1 producer uses.

## Roles

| role | meaning | changes membership? |
|---|---|---|
| `REQUIRED` | the thesis (membership predicate) | yes: `derive_decision` |
| `NEGATIVE` | defeater (closed estate, re-listed, probate already opened) | only once **found**; an unobserved defeater never blocks |
| `QUALIFYING` | needed before action (stall, authority, value, threshold state) | no |
| `DISQUALIFIER` | hard stop (government owner) | no: a disqualified property is still a member |
| `AMPLIFIER` | optional evidence | no: score vector only |

A numeric contradiction is decisive only when the recipe says so **and** every
contradicting measurement is `VERIFIED`. An estimated equity below 65% counts as
negative evidence. It does not prove the opposite.

## Consume it

```bash
python scripts/compile_canonical_recipes.py compile            # rewrite artifacts/recipe_compiler/*
python scripts/compile_canonical_recipes.py compile --check    # CI: artifacts are current
python scripts/compile_canonical_recipes.py evaluate --input candidate.json
```

Input: `contracts/recipe-evaluation-input.v1.schema.json`. That is an `eligibility-context.v1`
(boolean ingredients as signals, `signal_type` = ingredient id) plus `measurements`
for numeric, date and category ingredients.

Output: `recipe-evaluation-set.v1`

- `memberships[]`: every recipe's MATCH / NO_MATCH / UNKNOWN plus qualification state,
  keyed by the P1 `membership_key`. No primary recipe, no rank, no total.
- `evaluations[]`: clause records, `score_vector` (10 dimensions, each with its
  owning organ; value stays null unless EVALUATED), `required_evidence`,
  `missing_evidence`, `next_best_evidence` (ranked gaps, `information_gain` is
  planner ranking, not an opportunity score), `economics` (gate + basis),
  `qualification` (`action_authority` is always `NOT_EVALUATED`).
- `p1_eligibility_receipts[]`: for recipes that lower **exactly** to
  `eligibility-policy.v1`, the real P1 receipt. Its decision is asserted equal to
  the compiled decision.
- `data_gap_nbe[]`: R30 as a projection: UNKNOWN memberships blocked by exactly
  one membership predicate.

Projections into lead-recipe-os kernel shapes: `to_score_vector_components()`
(names `<recipe_id>.<dimension>`) and `to_next_best_evidence_gaps()`.

## What the compile says today (set in `artifacts/recipe_compiler/`)

- 19 PARTIAL, 9 SOURCE_BLOCKED, 1 PREDICATE_UNDEFINED (R27: no land/improvement
  threshold is defined anywhere), 1 META (R30), 0 EXECUTABLE. No membership
  predicate has a fully AVAILABLE producer.
- Only R1 `tax_threshold_watch` lowers exactly to the P1 vocabulary. The other
  28 evaluable recipes need numeric, date or category predicates, ANY_OF, defeaters, or
  ingredients with no authorized engine signal type. Lowering them would widen
  or drop a predicate, so they stay compiled evaluations.
- Membership divergences from the DeedSonar registry are written in each recipe's
  `notes`. Examples: R3/R16 use `absentee.out_of_state`, because `absentee.long_hold` is derived
  from `primary_recipe`. R7/R12/R18 use estate/probate/death evidence, because a
  living trust isn't an estate. R8 needs the tax foreclosure case, and its stall is QUALIFYING, which is the
  existing NBE "validate stall via latest docket".
