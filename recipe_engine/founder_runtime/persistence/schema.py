"""Local SQLite schema for Founder Runtime P1. No production DB."""

SCHEMA_VERSION = "fr.sqlite.v2"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS missions (
  mission_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL,
  current_state TEXT NOT NULL,
  body_json TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  creation_idempotency_key TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  projection_hash TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_missions_creation_idem
  ON missions(creation_idempotency_key)
  WHERE creation_idempotency_key IS NOT NULL AND creation_idempotency_key != '';

CREATE TABLE IF NOT EXISTS mission_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  prior_state TEXT,
  next_state TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  reason TEXT NOT NULL,
  body_json TEXT NOT NULL,
  event_hash TEXT NOT NULL,
  chain_position INTEGER NOT NULL,
  previous_event_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  UNIQUE(mission_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_events_mission ON mission_events(mission_id, sequence);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events_mission_chain
  ON mission_events(mission_id, chain_position);

CREATE TABLE IF NOT EXISTS checkpoints (
  checkpoint_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  plan_id TEXT,
  body_json TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  resumable INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  idempotency_key TEXT,
  kind TEXT NOT NULL DEFAULT 'generic',
  UNIQUE(mission_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_ckpt_mission ON checkpoints(mission_id, created_at);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  body_json TEXT NOT NULL,
  approval_hash TEXT NOT NULL,
  idempotency_key TEXT,
  decision TEXT NOT NULL,
  UNIQUE(mission_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS receipts (
  receipt_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  plan_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  body_json TEXT NOT NULL,
  receipt_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL,
  UNIQUE(mission_id, idempotency_key),
  UNIQUE(mission_id, plan_id, step_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  kind TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  pointer TEXT NOT NULL,
  body_json TEXT NOT NULL,
  registered_at TEXT NOT NULL,
  UNIQUE(mission_id, content_hash, pointer)
);

CREATE TABLE IF NOT EXISTS completion_reports (
  mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id),
  body_json TEXT NOT NULL,
  completion_hash TEXT NOT NULL,
  idempotency_key TEXT,
  completed_at TEXT NOT NULL,
  UNIQUE(idempotency_key)
);

CREATE TABLE IF NOT EXISTS side_effect_ledger (
  side_effect_key TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  step_id TEXT NOT NULL,
  status TEXT NOT NULL, -- proposed | acknowledged | reconciled | uncertain
  marker_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  receipt_id TEXT
);

CREATE TABLE IF NOT EXISTS runtime_locks (
  lock_name TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  mission_id TEXT,
  acquired_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  token TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_leases (
  worker_id TEXT PRIMARY KEY,
  mission_id TEXT,
  leased_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dependency_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  body_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
  outcome_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  body_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""
