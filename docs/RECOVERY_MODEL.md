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
9. Side-effect ledger reconcile  
10. Next eligible step  
11. Resume only from verified boundary  
12. Escalate uncertainty  

Statuses: `safe_to_resume`, `approval_required`, `blocked_dependency`, `uncertain_side_effect`, `invalid_receipt`, `unrecoverable`.

Never blindly rerun a step whose side effect may already exist.
