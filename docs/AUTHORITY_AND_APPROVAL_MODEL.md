# Authority and Approval Model

## Classes

A0–A6 as specified. P1 fixture policy:

- A0/A1 allow
- A2–A5 approval_required
- A6 enhanced_approval_required

## Real authority

`autonomous-governance-core` remains final policy authority.  
IAR/AEGIS may produce opinions; Founder Runtime records fixture decisions only in P1.

Approvals are exact, scoped, expiring, non-transitive, revocable before use.


## P1.1 enforcement

Approvals are recorded as APPROVAL_RECORDED events, consumed once with exact action/scope/expiry checks for gated transitions (AWAITING_APPROVAL→QUEUED and related).

## P1.2 enforcement

The trusted kernel classifies the operator request deterministically under
`fr.authority.kernel.v1`; IAR/model hints are non-authoritative. Unknown and
ambiguous actions escalate to A5, destructive actions to A6, and the classifier
receipts its reason code and policy version in `AUTHORITY_CLASSIFIED`. Fixture
Governance opinions resolve only A0/A1 read-only fixture actions.
