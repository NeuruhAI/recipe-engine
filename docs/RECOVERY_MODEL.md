# Recovery Model

Startup recovery:

1. Schema/version check  
2. Integrity  
3. Nonterminal missions  
4. Latest checkpoint  
5. Event replay  
6. Projection hash  
7. Stale locks/workers  
8. Receipt validation  
9. Observe side-effect ledger without external reconciliation
10. Produce a next-step plan only
11. Keep execution resume disabled
12. Escalate uncertainty  

Statuses: `safe_to_resume`, `approval_required`, `blocked_dependency`, `uncertain_side_effect`, `invalid_receipt`, `unrecoverable`.

Never blindly rerun a step whose side effect may already exist. P1.2 has no
resume dispatcher. Resume execution (R-L), external reconciliation (R-M),
retry/concurrent dispatch (R-N), and production persistence (R-O) are explicit
P2 gates and fail closed in the fixture kernel.
