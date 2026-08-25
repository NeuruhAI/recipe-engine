# Opportunity intelligence → LandOS evaluate seam

**Status as of this receipt:** caller/adapter **PROVEN in-process against a contract
stub**. Live LandOS HTTP evaluate is **UNWIRED**. Cross-repo LandOS Python import
court is **UNWIRED in this environment**.

This is not a new organism. Recipe Engine still owns the versioned recipe and
the `contracts/opportunity_intelligence.*.v1.schema.json` shapes. LandOS still
owns scoring / `evaluate_opportunity`. This lane only adds the recipe-engine
caller that posts the owned input and refuses to treat a skip or a non-contract
success as a result.

## SHAs actually inspected

| Organ | SHA | What it is |
|-------|-----|------------|
| recipe-engine `main` (base of this work) | `e461b2230df8f34011b880ded2122c005f6438cb` | “Add versioned opportunity intelligence contracts” |
| recipe-engine caller (this branch) | `16b925fe8b1f732bc8a0769d25b3d43e16ae6c17` | fail-closed LandOS evaluate caller + courts |
| landos-engine `main` (read-only, public contract) | `1779abf265814f5595e1153a531c4a0ee09a0faa` | “Add explainable opportunity intelligence evaluation” |

`landos-engine` was **not checked out** in this environment. The public HTTP
shape was read from `NeuruhAI/landos-engine` at `1779abf` (that SHA is also
`main` as of 2026-08-25):

```
POST /v1/opportunity-intelligence/evaluate
header X-LANDOS-SECRET
body   neuruh.opportunity-intelligence.input.v1
200    neuruh.opportunity-intelligence.result.v1
422    contract error
401    bad auth
```

That endpoint is documented as read-only: no seller contact, no offer, no DB
write. This caller does not add any of those.

## What was proven (this repo)

In-repo pytest `tests/opportunity_intelligence/test_landos_evaluate_caller.py`:

* **Skip is not success.** `evaluator=None` and `SkippedEvaluator` fail closed.
  They do not return score `0`.
* **Mocked-as-success without the result contract fails closed.** `{"ok": true,
  "score": 0}` is rejected.
* **UNKNOWN is not ABSENT 0.** An all-UNKNOWN `tax_delinquent` factor reported as
  `ABSENT` (the known-zero collapse) is rejected. A contract-faithful result that
  keeps the factor `UNKNOWN` and lists it under `unknown_material_factors` is
  accepted.
* **DecisionReceipt binds recipe id + version + config digest.** A result that
  credits a different `version` or `config_digest` is rejected.
* **HTTP path.** `LandosOpportunityClient` posts to
  `/v1/opportunity-intelligence/evaluate` with `X-LANDOS-SECRET`. The legacy
  identity-node `POST /evaluate` is a different seam and is not accepted here.

No LandOS scoring was copied. The HTTP stub in tests returns a hand-built result
document that satisfies the owned result schema.

## What remains UNWIRED (do not claim PASS)

* **LIVE LandOS process.** This environment did not boot landos-engine and did
  not POST to a real `/v1/opportunity-intelligence/evaluate`. LIVE = UNWIRED.
* **Cross-repo court** `scripts/verify_opportunity_intelligence.py --landos-root
  …`. Requires a landos-engine checkout. **NOT RUN HERE.**
* **Protected DeedSonar 120 Willow replay.** Still delegated; the Willow fixture
  here remains cardinality/losslessness only.
* **Estate-sale identity pipeline** `recipe_engine/engine.py` `POST /evaluate`.
  Still the older node-fork path. Not this contract. Daily manifests with
  `engine_called=0` describe that pipeline, not this caller.
* **Production scrape → OI evaluate wiring.** `run.py` is unchanged. No DB
  writes, no outbound contact.

## How to prove LIVE later

With landos-engine at `1779abf` (or a later SHA that still speaks the same
contract) listening locally:

```bash
python scripts/verify_opportunity_intelligence.py --landos-root /path/to/landos-engine
```

That script is still an in-process import court, not the HTTP caller. A LIVE
HTTP proof is a POST of a FIXTURE `world_mode` payload to
`/v1/opportunity-intelligence/evaluate` and a DecisionReceipt whose
`recipe_id` / `version` / `config_digest` match
`land_acquisition_explainable@1.0.0`. Do not treat Codex `engine_called=0` as
this seam until that HTTP call is actually made.
