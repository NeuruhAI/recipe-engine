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

## Execution-outcome write-back (R02D, closes G-03)

The Founder Runtime is the mission-state authority, but until R02D it never
learned that a dispatch it authorised had finished: a Workers execution reached a
sealed terminal receipt while the mission stayed `QUEUED`, and the SOPS dispatch
cursor was the only guard against a second dispatch. The runtime now **learns**
the outcome from evidence; nothing tells it a state.

Route: `POST /v1/missions/{mission_id}/execution-outcomes` (operator token,
`Idempotency-Key` required, `X-Expected-Version` optional — the envelope binds to
the *dispatched* mission version inside the evidence, and the kernel's CAS still
guards the append). Body: one `fr.execution_outcome.v1` envelope.

| field | binding |
|---|---|
| `mission_id` | must equal the route mission; a receipt naming another mission is refused |
| `mission_version` | the version the dispatch consumed (first outcome: the current `QUEUED` version; later outcomes: the recorded `dispatched_version`) |
| `work_spec_sha256` | must equal the mission's immutable work specification digest |
| `authority_reference` | `fr-api-v1:approval:<appr>:work-spec:<sha>`; `<appr>` must be this mission's **consumed** dispatch approval and `<sha>` the same work spec |
| `execution_id` | the Workers job id; fixed for the life of the dispatch |
| `outcome` | `DISPATCHED` · `EXECUTING` · `SUCCEEDED` · `FAILED` · `COURT_PASSED` · `COURT_FAILED` |
| `receipt` | required for `SUCCEEDED`: the sealed `nrh.receipt.fr-dispatch.v1` fields; `PAYLOAD_HASH` is recomputed with the emitter's canonicalisation and every field the runtime has a stake in must agree (`MISSION_ID`, `MISSION_VERSION`, `WORK_SPEC_SHA256`, `AUTHORITY_REFERENCE`, `TASK_ID`=`JOB_ID`=`execution_id`, `DISPATCH_RESULT=SUCCEEDED`, `SOURCE_HEAD`, loopback `NETWORK_POLICY`); `SENTINEL_CLOSURE` and `PRIME_VERDICT` must be `NOT_JUDGED` — a receipt cannot carry its own verdict |
| `court` | required for `COURT_*`: independent Sentinel record bound to the **accepted** receipt sha |

State effect (the canonical state set is unchanged; the fine-grained phase lives in
`mission.extensions.execution`):

| outcome | from | to | projection |
|---|---|---|---|
| `DISPATCHED` / `EXECUTING` | `QUEUED` (or `EXECUTING`) | `EXECUTING` | `phase`, `court=NOT_REQUESTED` |
| `SUCCEEDED` | `QUEUED`/`EXECUTING` | `VERIFYING` | `phase=SUCCEEDED`, `court=PENDING`, `receipt_sha` |
| `FAILED` | `QUEUED`/`EXECUTING` | `FAILED_RETRYABLE` | `phase=FAILED` |
| `COURT_PASSED` | `VERIFYING` | `RECONCILING` | `court=PASSED`, `court_id` |
| `COURT_FAILED` | `VERIFYING` | `FAILED_RETRYABLE` | `court=FAILED`, receipt retained |

A `SUCCEEDED` or `FAILED` reported straight from `QUEUED` appends an honest
*implied* `EXECUTING` event first (`implied: true`), then the terminal one.
`COURT_PENDING` is a projection value, never an input. **Nothing here reaches
`COMPLETED`**: execution completing is not verification, a court passing is not
completion, and `extensions.execution.verified` is always `false`. Completion
remains reachable only through the canonical completion verifier.

Ordering and idempotency: the same evidence is a replay (no event, whatever the
idempotency key); the same outcome with different evidence is `OUTCOME_CONFLICT`;
a lower-ranked outcome after a higher one is `OUTCOME_STALE`; a court before an
accepted `SUCCEEDED` receipt is `OUTCOME_OUT_OF_ORDER`; a terminal mission is
`MISSION_TERMINAL` and is never regressed; a paused mission is
`MISSION_STATE_CONFLICT` until resumed. Malformed or credential-shaped evidence
is `EXECUTION_OUTCOME_MALFORMED` (400). Every refusal changes nothing.

`GET /v1/missions/{id}/receipts` now also returns `execution` (the projection) and
`execution_outcomes` (each accepted event with the evidence it carried). These are
distinct from `receipts`, which stay `fr.receipt.v1` step receipts.
