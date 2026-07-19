# Autonomy Core Architecture

**Package:** `recipe_engine.autonomy`  
**Purity:** no DB, network, env secrets, filesystem writes, models, AXON/Governance/Vault clients, shell.

## Modules

| Module | Role |
|---|---|
| models | versioned contracts |
| validation | fail-closed rules |
| scoring | multi-dimension scores |
| pareto | dominance / frontier |
| deduplication | near-duplicate collapse |
| governance | GovernanceRequirement proposals only |
| collapse | `collapse_scenarios` entrypoint |
| explanation | deterministic templates |
| planning | `compile_execution_plan` |
| reconciliation | `reconcile_outcome` |
| hashing / serialization | deterministic receipts |
| config | versioned weights |

## Public API

- `collapse_scenarios(opportunity, candidates, config) -> DecisionPacket`
- `compile_execution_plan(packet, scenario_id, capability_catalog) -> ExecutionPlanProposal`
- `reconcile_outcome(packet, outcome, config) -> ReconciliationResult`

## Adapters

`recipe_engine.adapters` defines interfaces; `EstateSaleAdapter` is fixture-only and does not call network/DB.
