# Opportunity intelligence → LandOS evaluate seam

**Status as of this receipt:** `--landos-root` in-process `evaluate_opportunity`
against landos-engine `1779abf` is **LIVE / PASSED**. LIVE HTTP
`POST /v1/opportunity-intelligence/evaluate` is **UNWIRED**. UNKNOWN is not
treated as 0. A missing HTTP surface is not a PASS.

This is not a new organism. Recipe Engine still owns the versioned recipe and
the `contracts/opportunity_intelligence.*.v1.schema.json` shapes. LandOS still
owns scoring / `evaluate_opportunity`. This lane only adds the recipe-engine
caller that posts the owned input and refuses to treat a skip or a non-contract
success as a result.

## SHAs actually used

| Organ | SHA | What it is |
|-------|-----|------------|
| recipe-engine `main` (base of this work) | `e461b2230df8f34011b880ded2122c005f6438cb` | “Add versioned opportunity intelligence contracts” |
| recipe-engine caller | `16b925fe8b1f732bc8a0769d25b3d43e16ae6c17` | fail-closed LandOS evaluate caller + stub courts |
| landos-engine public contract (required) | `1779abf265814f5595e1153a531c4a0ee09a0faa` | “Add explainable opportunity intelligence evaluation” |
| landos-engine `app/opportunity_intelligence.py` blob | `bc99185b492bbd7328ee9ceb3a9af1b94ad303f6` | evaluate graph actually imported |
| landos-engine `app/receipt.py` blob | `e1ac93ea0d7a53378ddf1da730942df45fa0a545` | receipt helper actually imported |
| `--landos-root` `verification_sha256` | `ab75e4dfb0e3a0a396950c43088c826b4e6bca91e1972d997f596bc88afd7eb3` | court JSON in `docs/courts/landos-evaluate-1779abf.json` |
| UNKNOWN-tax LandOS `receipt_sha256` | `a5f24ae5363f4448591c1377fb39d71a55b3bd573871327e62bf8d9776cba082` | real evaluate, not a stub |

`landos-engine` could not be `git clone`d here (`Repository not found` / 404 for
the recipe-engine token). The evaluate import graph was materialized read-only
from GitHub blobs at `1779abf` to `/home/ubuntu/neuruh-siblings/landos-engine`
and is **not vendored** into this repo.

Public HTTP shape at that SHA:

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

## Verdicts

| Court | Verdict |
|---|---|
| In-repo stub fail-closed courts | Proven (pytest) |
| `--landos-root` in-process `evaluate_opportunity` at `1779abf` | **LIVE / PASSED** |
| UNKNOWN ≠ ABSENT / 0 against real LandOS evaluate | **LIVE / PASSED** |
| Caller wrapping real `evaluate_opportunity` | **LIVE / PASSED** |
| LIVE HTTP `POST /v1/opportunity-intelligence/evaluate` | **UNWIRED** |
| `--source-factory-root` 14233/6052 | **NOT_REQUESTED** |
| DeedSonar 120 Willow protected replay | Delegated |
| `run.py` production scrape → OI evaluate | Unchanged / unwired |

Command actually run:

```bash
python3 scripts/verify_opportunity_intelligence.py \
  --landos-root /home/ubuntu/neuruh-siblings/landos-engine
```

Validator parity 9/9. 7 fixtures matched expected spine. Willow score 0 is the
ABSENT probate cardinality fixture, not an UNKNOWN collapse. Full JSON:
`docs/courts/landos-evaluate-1779abf.json`. Narrative receipt:
`docs/courts/live-shape-receipt.md`.

## What the stub courts still prove

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
* **Transport failure is not score 0.** Connection refused raises
  `OpportunityEvaluateError`.

No LandOS scoring was copied. `LandosInProcessEvaluator` only forwards to
`evaluate_opportunity`.

## HTTP UNWIRED blockers (this VM)

1. Sibling clone: `git clone https://github.com/NeuruhAI/landos-engine.git` →
   `ERROR: Repository not found` (404).
2. No listener: `127.0.0.1:8400` and `127.0.0.1:8000` → `ConnectionRefusedError`.
3. Secrets/DB: `DATABASE_URL`, `LANDOS_SECRET`, `LANDOS_ENGINE_URL` unset.
4. Partial checkout is evaluate-import-graph only.
   `from app.main import app` → `ModuleNotFoundError: No module named 'app.main'`.
   Full FastAPI boot also needs postgres/`psycopg_pool` and the rest of LandOS
   `app.*`.

Until a real LandOS HTTP process at `1779abf` answers
`POST /v1/opportunity-intelligence/evaluate` with `X-LANDOS-SECRET`, the HTTP
seam stays **UNWIRED**. Do not mock a 200 into PASS.

## How to re-run

```bash
python3 scripts/verify_opportunity_intelligence.py --landos-root /path/to/landos-engine
LANDOS_ROOT=/path/to/landos-engine python3 -m pytest tests/opportunity_intelligence -v
```

A LIVE HTTP proof is a POST of a FIXTURE `world_mode` payload to
`/v1/opportunity-intelligence/evaluate` with `LANDOS_LIVE_HTTP=1`. Do not treat
Codex `engine_called=0` as this seam until that HTTP call is actually made.
`run.py` is unchanged.
