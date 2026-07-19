# Scoring and Selection

See also prior doctrine in audit-output. Implementation notes:

- 13 dimensions with explicit direction and validated non-negative weights
- Decimal quantization for hash stability
- Uncertainty and downside_risk are separate dimensions
- Economics only from domain/operator supplied metrics (`econ.*`); no ARV/MAO/tax inventing
- Pareto frontier + near-duplicate collapse + diversity selection
- Hard max 4 selected; zero is valid
- Blocked/prohibited never selected
- Injected `fixed_timestamp` / `id_salt` for determinism
