# Local Persistence and Events

## Backend

SQLite (`SqliteStore`) — local/temp/gitignored only.

## Authority

- Authoritative history: `mission_events` (append-only)
- `missions.current_state` is a projection/cache
- Optimistic version on every event append
- Unique idempotency keys per mission for events, receipts, approvals, checkpoints

## Schema version

`fr.sqlite.v1` — local init only. No production migrations. No Supabase. No estate-sale Postgres.
