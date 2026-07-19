# Recipe Engine Charter (re-chartered)

**Status:** Candidate branch only — not live-integrated.  
**Authority:** Operator owner ruling 2026-07-19 (autonomy-core P0).  
**History:** Prior forensic discovery correctly found recipe-engine as an estate-sale adapter only. This charter is an intentional re-charter, not a falsification of that history.

## Purpose

Recipe Engine converts evidence-backed opportunity inputs and domain-generated candidate strategies into a small, deterministic, auditable set of governed strategy proposals.

## Owns

- recipe and candidate strategy schema
- deterministic collapse (Pareto, dedupe, 0–4 selection)
- decision-packet formation
- execution-plan proposal formation
- outcome reconciliation
- calibration proposal formation (review-required, never auto-promoted)
- domain adapter contract

## Does not own

- sensing
- model inference
- final governance allow/block
- routing / event transport
- persistence authority (except existing estate-sale adapter schema)
- execution / tool running
- product UI
- valuation truth
- memory (Vault/Qdrant)

## Preserved estate-sale adapter

The existing scraper pipeline (`run.py`, `recipe_engine/{fetch,parse,persist,engine,cities}.py`, `db/010_recipe_schema.sql`, scheduler scripts) remains the first domain ingestion adapter/producer. P0 does not regress canon keys, LandOS client, or DB schema.

## Integration direction (intended architecture only — not live)

```
Sensors/domain organs
    ↓ evidence + candidate strategies
Recipe Engine (autonomy core)
    ↓ decision packet + plan proposal
Governance
    ↓ allow / approval / escalate / block
Mother
    ↓ orchestration request
AXON
    ↓ governed delivery
Workers/specialist organs
    ↓ outcome evidence
Recipe Engine reconciliation
    ↓ calibration proposal
Human/Governance review
```

## Non-claims

- not integrated live
- no autonomous execution
- no Mohnal / Vision claims
- no runtime ownership
