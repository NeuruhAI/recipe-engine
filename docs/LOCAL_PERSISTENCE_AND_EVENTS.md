# Local Persistence and Events

## Backend

SQLite (`SqliteStore`) — local/temp/gitignored only.

## Authority

- Authoritative history: `mission_events` (append-only)
- `missions.current_state` is a projection/cache
- Optimistic version on every event append
- Unique idempotency keys per mission for events, receipts, approvals, checkpoints

## Schema version

`fr.sqlite.v2` — local init only. No production migrations. No Supabase. No estate-sale Postgres.

P1.2 candidates use disposable fixture databases. A v1 database fails closed on
schema mismatch and must be recreated from its validated event fixture; no
production migration is implied or authorized.


## P1.1

Full mission projection rebuilds from the event stream (mission_created anchors body; deliverable/plan/approval body events apply deterministically). verify_integrity rebuilds and compares.

## P1.2

Mission events form a mission-local SHA-256 chain from a canonical genesis
predecessor. Position, predecessor, payload, IDs, transition, versioned schema,
and timestamp are bound. Creation and its genesis event are atomic. Rebuild and
integrity verification reject insertion, deletion, reorder, substitution, or
projection rewrite. Locks and worker leases are operational metadata only and
cannot change mission truth or authority.
