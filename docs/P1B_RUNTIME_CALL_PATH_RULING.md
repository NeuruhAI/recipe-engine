# P1B — Runtime call-path census: CALLER_ABSENT

> **SUPERSEDED 2026-09-15 (T11):** `RUNTIME_CALL_PATH=BOUND`. The three seams below are closed by `scripts/run_opportunity_eligibility.py`, the compiled canonical 30 and DeedSonar `candidate_recipe_membership_eligibility_apply_v1`. See `docs/OPPORTUNITY_ELIGIBILITY_RUNTIME.md`. This ruling stays as the 2026-09-08 seal at `e93f2ac`.

MISSION_ID=NEURUH-DEEDSONAR-LIVE-SYSTEM-CONVERGENCE-20260908 / CHILD_1_REAL_P1B_RUNTIME_BIND
ROLE=RECIPE_ENGINE_OWNER_IMPLEMENTATION
BRANCH=cursor/p1b-runtime-caller-census-9a26
BASE_SHA=e93f2accf1c675a78655399b421f4e77b39a5223
RISK=SOURCE_ONLY
SUPABASE_MUTATED=NO
MERGE=NO

## Success criterion

**RUNTIME_CALL_PATH=CALLER_ABSENT**

Searched every named candidate for an existing loop that already owns:

1. normalized canonical evidence (`eligibility-context.v1` or an existing lossless adapter into it);
2. an explicit recipe version set (`eligibility-policy.v1` documents, not one ordered `recipe_id`);
3. a receipt channel that can carry N immutable `MATCH` / `NO_MATCH` / `UNKNOWN` receipts

and that should call `evaluate_recipes(...)` without inventing a daemon, store, context producer, catalog, or second membership path.

No such caller exists on `main` @ `e93f2ac`. Binding any of the candidates below would invent a seam P1 already classified as next / not-modified / not-membership.

Census table: `artifacts/P1B_RUNTIME_CALLER_CENSUS.tsv`.
Lock: `tests/opportunity_intelligence/test_p1b_runtime_caller_absent.py`.

## Already bound (not a runtime)

| Surface | Status |
|---|---|
| `recipe_engine.opportunity_intelligence.eligibility.evaluate_recipes` | LIBRARY. Pure functions. UNKNOWN preserved. Order-independent. Version-pinned. Evidence lineage + freshness. Replay idempotent. |
| `scripts/p1_independent_eligibility_proof.py` | HARNESS. Sealed P1 fixture → 4 receipts. JGI / Bob: LIBRARY+HARNESS = BIND_PROOF. |
| `scripts/run_independent_eligibility.py` | CLI. stdout JSONL. Parent: CLI_EXISTS is not RUNTIME_CALL_PATH. |

Verified callers of `evaluate_recipes` on this SHA (non-test): the definition, the package re-export, the proof harness, and the thin CLI. Nothing else.

## Candidates searched (why not bound)

### Founder Runtime — `recipe_engine/founder_runtime`

Mission orchestration: intent → missions → approvals → work specs → fixture adapters. Zero imports of `evaluate_recipes` / `eligibility`. Evidence packs are mission context, not `eligibility-context.v1`. Receipts are FR execution / authority receipts.

Binding here invents a new mission capability and would touch Founder Runtime persistence, which P1 listed under `NOT_IN_SCOPE_BY_MISSION` ("storage binding … Founder Runtime persistence") and `FOUNDER_ACTION_REQUIRED` ("choose the storage binding seam … before any runtime promotion").

### daily_run / run.py — estate-sale SOURCE_REFRESH

`scripts/daily_run.sh` health-checks LandOS, then `run.py` scrapes estatesales.net JSON-LD `SaleEvent`, persists `recipe.sale_events`, and optionally POSTs `/evaluate` for identity-node creation.

That `/evaluate` is the old LandOS identity-node seam (`recipe_engine/engine.py`), not opportunity-intelligence scoring and not eligibility. There is no RECIPE_EVALUATION receipt distinct from SOURCE_REFRESH. JGI: NOT membership. Mission: do not wire eligibility into estate-sale scrape blindly.

### Opportunity Intelligence evaluation — `evaluate.py`

`request_opportunity_evaluation` is the fail-closed LandOS score caller for **one** `land_acquisition_explainable@1.0.0` recipe. It emits `OpportunityDecisionReceipt` (score binding). It does not accept an eligibility policy set and does not emit MATCH / NO_MATCH / UNKNOWN.

JGI: NOT membership. P1 census: NOT MODIFIED (UNKNOWN-preservation discipline was mirrored, not the caller).

`scripts/verify_opportunity_intelligence.py` is the same scoring court. Binding eligibility there still requires an invented OI-input → eligibility-context adapter.

### Closed-loop evaluation — `closed_loop/recipe_eval.py` + `e2e.py`

`RecipeRef` (id + version + digest) and `RecipeEvaluationRecord` are the record shape P1 reused as discipline. Vocabulary is `QualificationState`, lifted from `graduation.classify`. `run_full_loop` **constructs** one graduation evaluation by hand; it does not evaluate N policies.

The existing output channel is `LoopLedger` (`RECIPE_EVALUATED`) plus `summary.json`. P1 ruled LoopLedger **NOT BOUND**: "binding receipts into the event grammar widens scope; storage binding is a separate seam." This mission does not reopen that seam.

### Source-factory adapter — `source_factory.py`

`adapt_source_factory_run` maps normalized source-factory rows onto opportunity-intelligence signals and stamps `source-factory-adapter.v1`. That is SOURCE_REFRESH / context upstream.

P1 census: "future eligibility-context producer; NOT MODIFIED." No function on main produces `eligibility-context.v1` from those signals (identity + custody `evidence[]` + recipe-agnostic `signals[]`). There is also no production eligibility-policy catalog (P3). Calling `evaluate_recipes` here would invent both missing inputs.

### Ordered-primary paths (confirmed, not membership)

`rank_nc_curative_raw*.py::recipe`, `oppack_receipts.py`, `graduation.classify`, `autonomy/collapse.py`, `nc_dps_contract.choose_strategy`: one policy or one rank. Missing input collapses toward false / 0. P1: NOT MODIFIED; their outputs are refused as eligibility authority.

## Missing seams that would have to be invented

A runtime bind that satisfies

```
NORMALIZED CANONICAL EVIDENCE + EXPLICIT RECIPE VERSION SET
  → evaluate_recipes(...)
  → N immutable MATCH|NO_MATCH|UNKNOWN receipts
  → existing receipt/output channel
```

still lacks, on this SHA:

1. **eligibility-context producer** from any live or adapted source (source-factory, OI input, closed-loop evidence, estate-sale rows).
2. **production recipe version set** (P3 catalog crosswalk). Fixture policies are not that catalog.
3. **chosen receipt channel** among closed-loop ledger / Founder Runtime persistence / DeedSonar membership (P1 founder action; DeedSonar is out of scope here).

Inventing any of those is a new seam, not a smallest bind.

## Explicitly not done

- No new daemon, HTTP service, or scheduler.
- No Supabase, DeedSonar, CRM, contact, or membership write.
- No edit to `run.py`, `daily_run.sh`, `rank_nc_curative_raw*.py`, `evaluate.py`, `source_factory.py`, `closed_loop/*`, or `founder_runtime/*`.
- No second evaluator.

## Next consumer

A later mission may bind `evaluate_recipes` only after an existing owner publishes (1) an eligibility-context producer and (2) an explicit policy set, and the founder chooses the receipt channel. Until then the proven surfaces remain LIBRARY + HARNESS + CLI.
