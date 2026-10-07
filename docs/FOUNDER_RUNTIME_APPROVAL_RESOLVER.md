# Founder Runtime — the approval resolver

Mission `NEURUH-APPROVAL-AND-PROMOTION-SEAMS-20260902`. Closes the parking
defect where every API-created mission sat at AWAITING_APPROVAL (or
APPROVED_NO_EXECUTION) even when no founder was required.

```
AWAITING_APPROVAL
  → consequence classification   (trusted kernel A-class → gov.action.facts.v1; closed, monotone)
  → Governance /v2/authorize      (component founder_runtime, lane founder-runtime-approval-resolver,
                                   attested source SHA, fresh nonce, optional founder-signed grant)
  → allow            → Approval decided_by "governance:<authorization_id>" (evidence: Governance receipt)
                       → kernel transition AWAITING_APPROVAL→QUEUED consumes it exactly once
    founder_required → stays parked; one concise founder_prompt surfaced; the exact request
                       (scope_key + source_sha + risk_tier) published for the founder's scope_once signature;
                       the next resolve PRESENTS that signed artifact to Governance and spends the human approval
    deny (policy)    → BLOCKED_POLICY
    deny (identity / ratification / malformed / replay) → stays parked, reason surfaced (UNRESOLVED)
    unreachable / no port / no attested SHA → stays parked, reason surfaced (UNRESOLVED)
```

Invariants (courted in `tests/founder_runtime/api/test_approval_resolver.py`, 22 cases):
never writes `decided_by="jeramie"`, never sets `caller_approved`, never signs
(a configured signer is untouched), never lowers a class, asks Governance
exactly once per attempt with a fresh nonce, idempotent on the key, version
checked before asking, spends a recorded-but-unspent Governance decision on
retry without re-asking, records Governance's answer on the mission chain.

Surfaces: `runtime/approval_resolver.py` (pure), `api/service.py`
(`resolve_authority`, auto-resolve on create), `POST /v1/missions/{id}/gate/resolve`,
client `resolve_gate`, `live_adapters/founder_api.py` (composition root:
`python -m recipe_engine.live_adapters.founder_api --db … --port 19090`), and
`LiveGovernanceAdapter.authorize(..., grant=)`.

Live: the runtime on :19090 is `38c359f`, which Governance's lineage does not
qualify (`520ae43` pinned), so on the live surface the resolver parks every
mission `SOURCE_SHA_MISMATCH` today (live court in the mission receipt). The
unlock is the founder's re-qualification of the lineage; see
autonomous-governance-core `GOVERNANCE_V2_PROMOTION_SEAMS.md`.
