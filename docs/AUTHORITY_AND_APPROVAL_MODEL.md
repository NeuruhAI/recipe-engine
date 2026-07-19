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
