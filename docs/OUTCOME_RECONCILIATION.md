# Outcome Reconciliation

`reconcile_outcome` compares predicted vs actual metrics with unit/scale checks.

- separates prediction / strategy / execution / governance / data-quality / external shock / incomplete
- calibration proposals are scoped and `promotion_prohibited=true`
- one outcome cannot create a global weight/policy change
- never mutates EngineConfig weights
