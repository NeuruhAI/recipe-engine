# Governance Boundary

Recipe Engine **proposes** `GovernanceRequirement` states:

- allow
- approval_required
- escalate
- blocked

Rules:

- missing/unknown policy → escalate (never allow)
- irreversible / external comms / financial → approval_required
- prohibited → blocked
- real Governance response overrides any proposal

This package does **not** import or mutate `autonomous-governance-core`.
