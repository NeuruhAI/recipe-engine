# Live-shape court receipt — LandOS evaluate caller

This is the fail-closed record for the `--landos-root` / live-shape court.
`UNKNOWN` is not treated as `0`. A missing HTTP surface is `UNWIRED`, not `PASS`.

## SHAs actually used

| Artifact | SHA |
|---|---|
| recipe-engine `main` (OI contracts) | `e461b2230df8f34011b880ded2122c005f6438cb` |
| recipe-engine caller commit | `16b925fe8b1f732bc8a0769d25b3d43e16ae6c17` |
| landos-engine public contract (required) | `1779abf265814f5595e1153a531c4a0ee09a0faa` |
| landos-engine `app/opportunity_intelligence.py` blob | `bc99185b492bbd7328ee9ceb3a9af1b94ad303f6` |
| landos-engine `app/receipt.py` blob | `e1ac93ea0d7a53378ddf1da730942df45fa0a545` |
| `--landos-root` court `verification_sha256` | `ab75e4dfb0e3a0a396950c43088c826b4e6bca91e1972d997f596bc88afd7eb3` |
| LandOS UNKNOWN-tax evaluate `receipt_sha256` | `a5f24ae5363f4448591c1377fb39d71a55b3bd573871327e62bf8d9776cba082` |

LandOS import graph was materialized read-only at `/home/ubuntu/neuruh-siblings/landos-engine` from GitHub MCP blobs at `1779abf`. It is **not** vendored into this repo. `git clone` / `gh repo view NeuruhAI/landos-engine` return 404 for the recipe-engine token.

## Verdicts

| Court | Verdict | Notes |
|---|---|---|
| `--landos-root` in-process `evaluate_opportunity` | **LIVE / PASSED** | `python3 scripts/verify_opportunity_intelligence.py --landos-root /home/ubuntu/neuruh-siblings/landos-engine`. Validator parity 9/9. 7 fixtures matched expected spine. Full JSON: `landos-evaluate-1779abf.json`. |
| UNKNOWN ≠ ABSENT / 0 against real LandOS evaluate | **LIVE / PASSED** | Synthetic `tax_delinquent` `UNKNOWN` → LandOS factor `state=UNKNOWN`, `delta=0`, listed in `unknown_material_factors`. Not collapsed to ABSENT. Caller `request_opportunity_evaluation` + DecisionReceipt bound. |
| LIVE HTTP `POST /v1/opportunity-intelligence/evaluate` | **UNWIRED** | Not mocked into PASS. Blockers below. |
| `--source-factory-root` 14233/6052 | **NOT_REQUESTED** | Flag not passed. Not a fake 0. |
| `run.py` production scrape → OI evaluate | **UNWIRED** | Intentionally unchanged. |

## HTTP UNWIRED blockers (this VM)

1. Sibling clone: `git clone https://github.com/NeuruhAI/landos-engine.git` → `ERROR: Repository not found` (404).
2. No listener: `127.0.0.1:8400` and `127.0.0.1:8000` → `ConnectionRefusedError`.
3. Secrets/DB: `DATABASE_URL`, `LANDOS_SECRET`, `LANDOS_ENGINE_URL` unset.
4. Partial checkout is evaluate-import-graph only. `from app.main import app` → `ModuleNotFoundError: No module named 'app.main'`. Full FastAPI boot also needs postgres/`psycopg_pool` and the rest of LandOS `app.*`.

Until a real LandOS HTTP process at `1779abf` answers `POST /v1/opportunity-intelligence/evaluate` with `X-LANDOS-SECRET`, the HTTP seam stays **UNWIRED**.

## What this does not prove

- HTTP 200 from LandOS.
- Source-factory acceptance (14233/6052).
- DeedSonar 120 Willow protected replay.
- Any production write from `run.py`.
