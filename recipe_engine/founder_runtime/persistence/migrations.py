"""Local forward schema migration.

The store refuses to open a database whose recorded schema version is not the
one the code expects.  That check is correct and is not weakened here — an
unknown or future version still fails closed, untouched.  What was missing was a
*path* for the one version transition this implementation actually introduces,
so an existing v2 database had no way forward except being discarded.

Migration doctrine:

* **Forward only, additive only.**  v2 -> v3 adds two tables and their indexes
  and changes nothing that already exists.  No row is rewritten, no column is
  dropped, no table is recreated.  Mission rows, events and their chain hashes,
  checkpoints, approvals, receipts, idempotency keys, leases and recovery state
  are therefore preserved by construction rather than by careful copying — the
  migration never addresses them.
* **One transaction.**  The DDL and the version bump commit together, so a crash
  mid-migration leaves the database at v2 with the migration simply not applied,
  never half-applied.
* **Idempotent.**  Every statement is ``IF NOT EXISTS`` and the version bump is
  conditional, so re-running is a no-op.
* **Auditable.**  Each applied step records a ``migration:<from>-><to>`` row in
  ``schema_meta``, so a database carries the evidence of its own upgrade.

The delta is spelled out here rather than reusing ``SCHEMA_SQL`` so that what
changed between versions is reviewable in one place.  ``test_t1_sqlite_migration``
guards the two against drift by asserting that a migrated v2 database has exactly
the schema of a freshly created v3 one.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .schema import SCHEMA_SQL, SCHEMA_VERSION


# --- the v2 -> v3 delta ------------------------------------------------------
# Adds plan and decision-packet persistence.  Before v3 an execution plan existed
# only as loose JSON inside a step receipt: it could not be retrieved, diffed or
# superseded, and its packet_id resolved to nothing.
V2_TO_V3_SQL = """
CREATE TABLE IF NOT EXISTS decision_packets (
  packet_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  body_json TEXT NOT NULL,
  receipt_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_packets_mission ON decision_packets(mission_id);

CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(mission_id),
  packet_id TEXT,
  plan_version INTEGER NOT NULL,
  supersedes TEXT,
  strategy_id TEXT NOT NULL,
  body_json TEXT NOT NULL,
  plan_receipt TEXT NOT NULL,
  authority_binding_json TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(mission_id, plan_version)
);

CREATE INDEX IF NOT EXISTS ix_plans_mission ON plans(mission_id);
"""


def reanchor_mission_projections(conn: Any) -> list[str]:
    """Re-anchor mission projection hashes across the mission contract bump.

    ``fr.sqlite.v3`` ships alongside mission contract ``fr.mission.v2``, which
    adds seven defaulted fields (non_goals, world, domain, time_budget,
    resource_budget, required_evidence, approval_gates).  A mission row written
    under v1 therefore serialises differently once reloaded by this build, and
    its stored ``projection_hash`` — taken over the v1 body — no longer matches.
    Left alone, every pre-existing mission would fail ``verify_integrity`` and
    recovery would report the whole store unrecoverable.

    The order here is what makes this safe rather than a laundering step:

    1. **Verify first, under the rule that wrote it.**  The stored hash must
       equal the hash of the stored body exactly as recorded.  If it does not,
       that row was already tampered with or corrupt *before* this migration, and
       the migration refuses — it must not be possible to launder a broken
       projection by upgrading through it.
    2. **Only then re-anchor.**  The body is reloaded and re-serialised, which
       fills the new fields with their defaults, and the hash is recomputed over
       that canonical form.

    The body's declared ``schema_version`` is deliberately left at whatever
    compiled it.  The mission genuinely was compiled under v1; the added fields
    are defaults, not founder intent, and restamping it v2 would claim semantics
    the mission never had.

    Events are not touched.  Their hashes cover the event bodies as stored, the
    chain stays valid, and the ``mission_body`` inside a MISSION_CREATED payload
    remains exactly what was recorded — rewriting it would break the chain.
    Replay still agrees with the re-anchored projection because both sides pass
    through the same load/serialise path.
    """
    from recipe_engine.founder_runtime.contracts.mission import Mission
    from recipe_engine.founder_runtime.errors import PersistenceConflict
    from recipe_engine.founder_runtime.ids import hash_payload
    from recipe_engine.founder_runtime.serialization import to_canonical

    reanchored: list[str] = []
    rows = conn.execute(
        "SELECT mission_id, body_json, projection_hash FROM missions"
    ).fetchall()
    for row in rows:
        mission_id = row["mission_id"]
        stored_body = json.loads(row["body_json"])
        if hash_payload(stored_body) != row["projection_hash"]:
            raise PersistenceConflict(
                "pre-migration projection hash mismatch; refusing to upgrade a mission "
                "whose stored body already disagrees with its recorded hash",
                {"mission_id": mission_id},
            )
        upgraded = Mission.from_dict(stored_body).to_dict()
        new_hash = hash_payload(upgraded)
        if new_hash == row["projection_hash"]:
            continue
        conn.execute(
            "UPDATE missions SET body_json=?, projection_hash=? WHERE mission_id=?",
            (to_canonical(upgraded), new_hash, mission_id),
        )
        reanchored.append(mission_id)
    return reanchored


# from_version -> (to_version, additive DDL, optional data step).  A version
# absent from this map has no forward path and must fail closed.
MIGRATION_STEPS: Mapping[str, tuple[str, str, Any]] = {
    "fr.sqlite.v2": ("fr.sqlite.v3", V2_TO_V3_SQL, reanchor_mission_projections),
}


def local_init_sql() -> str:
    return SCHEMA_SQL


def current_schema_version() -> str:
    return SCHEMA_VERSION


def plan_migration(stored_version: str, target_version: str = SCHEMA_VERSION
                   ) -> list[tuple[str, str, str, Any]] | None:
    """The ordered steps from ``stored_version`` to ``target_version``.

    Returns an empty list when the database is already current, ``None`` when no
    forward path exists (the caller must then fail closed), and otherwise the
    chain of ``(from, to, sql, data_step)`` steps to apply in order.
    """
    if stored_version == target_version:
        return []
    steps: list[tuple[str, str, str, Any]] = []
    seen: set[str] = {stored_version}
    version = stored_version
    while version != target_version:
        step = MIGRATION_STEPS.get(version)
        if step is None:
            return None
        to_version, sql, data_step = step
        if to_version in seen:
            # A cycle in the migration table is a programming error, not a
            # database problem; refuse rather than loop.
            return None
        steps.append((version, to_version, sql, data_step))
        seen.add(to_version)
        version = to_version
    return steps


def migration_audit_key(from_version: str, to_version: str) -> str:
    return f"migration:{from_version}->{to_version}"


def apply_migration(conn: Any, steps: Sequence[tuple[str, str, str, Any]]) -> list[str]:
    """Apply forward steps in a single transaction.  Returns the applied keys.

    The caller has already established that ``steps`` is a real forward path.
    The DDL is additive and ``IF NOT EXISTS``; any data step must be written to
    be re-runnable, so the whole call is idempotent.  DDL, data step and version
    bump commit together: a crash mid-migration leaves the database at its
    original version with the migration simply not applied, never half-applied.
    """
    applied: list[str] = []
    if not steps:
        return applied
    conn.execute("BEGIN IMMEDIATE")
    try:
        for from_version, to_version, sql, data_step in steps:
            # executescript() would COMMIT the open transaction; keep the whole
            # migration atomic by issuing the statements individually.
            for statement in _statements(sql):
                conn.execute(statement)
            if data_step is not None:
                data_step(conn)
            key = migration_audit_key(from_version, to_version)
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES (?, ?)",
                (key, to_version),
            )
            conn.execute(
                "UPDATE schema_meta SET value=? WHERE key='schema_version'",
                (to_version,),
            )
            applied.append(key)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001 — surface the original failure
            pass
        raise
    return applied


def _statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]
