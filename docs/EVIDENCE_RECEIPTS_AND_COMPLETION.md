# Evidence, Receipts, and Completion

- Agent statements cannot replace receipts
- Invalid/missing receipt cannot complete a step
- Validation is pure: missing hashes are rejected and never backfilled
- Fixture evidence records producer identity and `fixture_only` trust
- Fixture evidence can never authorize live, external, or production mutation
- Completion requires delivered outputs, exact passing acceptance references,
  validated SUCCESS receipts, resolved authority references, and a reconciled outcome
- Generic exceptions cannot waive any mandatory truth gate
- `COMPLETED` is reachable only through the canonical completion verifier and event

Cryptographic signing by a live executor remains a P2 gate. P1.2 does not claim
live receipt authenticity; it makes unsigned fixture evidence structurally
incapable of authorizing a live mutation.
