"""GATE A — fr.sqlite.v2 -> v3 forward migration.

The store must refuse a database whose schema version it does not understand.
That check is not weakened here: an unknown or future version still fails
closed, and now does so *before* anything is written. What is added is a forward
path for the one transition this implementation introduces, so an existing v2
database has somewhere to go other than the bin.

The v2 database in these tests is built from a frozen copy of the real v2 DDL
(``v2_schema_fixture``), not from the current schema with parts removed, so the
test is not circular.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.errors import PersistenceConflict
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.persistence.migrations import (
    MIGRATION_STEPS,
    migration_audit_key,
    plan_migration,
)
from recipe_engine.founder_runtime.persistence.schema import SCHEMA_VERSION
from recipe_engine.founder_runtime.persistence.sqlite import SqliteStore
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.state.states import MissionState

from tests.founder_runtime.v2_schema_fixture import V2_SCHEMA_SQL, V2_SCHEMA_VERSION

V3 = "fr.sqlite.v3"
NEW_TABLES = {"plans", "decision_packets"}
V2_ADDED_MISSION_FIELDS = {
    "non_goals", "world", "domain", "time_budget",
    "resource_budget", "required_evidence", "approval_gates",
}


def _tmp() -> str:
    t = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    p = t.name
    t.close()
    return p


def _make_v2_db(path: str) -> None:
    """A database exactly as the previous build would have left it."""
    con = sqlite3.connect(path)
    con.executescript(V2_SCHEMA_SQL)
    con.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (V2_SCHEMA_VERSION,))
    con.commit()
    con.close()


def _tables(path: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()


def _version(path: str) -> str | None:
    con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return None if row is None else row[0]
    finally:
        con.close()


class MigrationPlanningTests(unittest.TestCase):
    def test_current_version_needs_no_steps(self):
        self.assertEqual(plan_migration(V3, V3), [])

    def test_v2_has_exactly_one_forward_step(self):
        steps = plan_migration(V2_SCHEMA_VERSION, V3)
        self.assertIsNotNone(steps)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0][0], V2_SCHEMA_VERSION)
        self.assertEqual(steps[0][1], V3)

    def test_unknown_version_has_no_path(self):
        self.assertIsNone(plan_migration("totally.unknown.v9", V3))

    def test_future_version_has_no_path(self):
        self.assertIsNone(plan_migration("fr.sqlite.v4", V3))

    def test_migration_table_declares_no_backward_step(self):
        for source, (target, _sql, _data) in MIGRATION_STEPS.items():
            self.assertLess(source, target, "migrations are forward-only")


class ForwardMigrationTests(unittest.TestCase):
    def test_v2_database_opens_and_upgrades(self):
        path = _tmp()
        _make_v2_db(path)
        self.assertEqual(_version(path), V2_SCHEMA_VERSION)
        store = SqliteStore(path)
        store.initialize()
        try:
            self.assertEqual(store.schema_version(), V3)
            self.assertEqual(len(store.migrations_applied), 1)
        finally:
            store.close()

    def test_new_structures_appear(self):
        path = _tmp()
        _make_v2_db(path)
        before = _tables(path)
        self.assertFalse(NEW_TABLES & before)
        store = SqliteStore(path)
        store.initialize()
        store.close()
        self.assertTrue(NEW_TABLES <= _tables(path))
        con = sqlite3.connect(path)
        idx = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        con.close()
        self.assertIn("ix_plans_mission", idx)
        self.assertIn("ix_packets_mission", idx)

    def test_migration_is_idempotent(self):
        path = _tmp()
        _make_v2_db(path)
        applied = []
        for _ in range(3):
            store = SqliteStore(path)
            store.initialize()
            applied.append(list(store.migrations_applied))
            store.close()
        self.assertEqual(len(applied[0]), 1)
        self.assertEqual(applied[1], [])
        self.assertEqual(applied[2], [])

    def test_migration_is_recorded_exactly_once(self):
        path = _tmp()
        _make_v2_db(path)
        for _ in range(3):
            store = SqliteStore(path)
            store.initialize()
            store.close()
        con = sqlite3.connect(path)
        rows = list(con.execute("SELECT key, value FROM schema_meta WHERE key LIKE 'migration:%'"))
        con.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], migration_audit_key(V2_SCHEMA_VERSION, V3))

    def test_migrated_schema_matches_a_fresh_v3_database(self):
        """Guards the hand-written delta against drift from SCHEMA_SQL."""
        migrated = _tmp()
        _make_v2_db(migrated)
        s = SqliteStore(migrated); s.initialize(); s.close()

        fresh = _tmp()
        f = SqliteStore(fresh); f.initialize(); f.close()

        def objects(p):
            con = sqlite3.connect(p)
            try:
                return sorted(r[0] for r in con.execute(
                    "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
            finally:
                con.close()

        self.assertEqual(objects(migrated), objects(fresh))

    def test_fresh_initialization_still_works_and_migrates_nothing(self):
        path = _tmp()
        store = SqliteStore(path)
        store.initialize()
        try:
            self.assertEqual(store.schema_version(), V3)
            self.assertEqual(store.migrations_applied, [])
        finally:
            store.close()


class FailClosedTests(unittest.TestCase):
    def _db_at_version(self, version: str) -> str:
        path = _tmp()
        s = SqliteStore(path); s.initialize(); s.close()
        con = sqlite3.connect(path)
        con.execute("UPDATE schema_meta SET value=? WHERE key='schema_version'", (version,))
        con.commit()
        con.close()
        return path

    def test_future_version_is_refused(self):
        path = self._db_at_version("fr.sqlite.v4")
        with self.assertRaises(PersistenceConflict):
            SqliteStore(path).initialize()

    def test_unknown_version_is_refused(self):
        path = self._db_at_version("totally.unknown.v9")
        with self.assertRaises(PersistenceConflict):
            SqliteStore(path).initialize()

    def test_refusal_writes_nothing(self):
        """A database this build cannot serve must be left untouched."""
        path = self._db_at_version("fr.sqlite.v4")
        con = sqlite3.connect(path)
        before = sorted(r[0] for r in con.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        con.close()
        with self.assertRaises(PersistenceConflict):
            SqliteStore(path).initialize()
        con = sqlite3.connect(path)
        after = sorted(r[0] for r in con.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        version_after = con.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        con.close()
        self.assertEqual(before, after)
        self.assertEqual(version_after, "fr.sqlite.v4")


class ProjectionReanchorTests(unittest.TestCase):
    """The mission contract moved v1 -> v2 alongside the store schema.

    Seven defaulted fields were added, so a v1 mission body serialises
    differently once reloaded and its stored projection hash no longer matches.
    The migration re-anchors it — but only after verifying the old hash under the
    rule that wrote it, so a broken projection cannot be laundered by upgrading.
    """

    def _v2_db_with_mission(self) -> tuple[str, str, dict]:
        """A faithfully v1-shaped database.

        A real v2 database holds the mission body in v1 shape in *both* places
        it appears — the projection row and the MISSION_CREATED event payload —
        so both normalise to the same defaults when reloaded. Stripping only the
        projection would leave replay rebuilding real values against a projection
        holding defaults, which is an artefact of the fixture, not of migration.
        The genesis event is therefore stripped too and its hash recomputed, so
        the chain stays internally valid.
        """
        from recipe_engine.founder_runtime.contracts.events import (
            GENESIS_EVENT_HASH,
            MissionEvent,
        )
        from recipe_engine.founder_runtime.serialization import to_canonical

        path = _tmp()
        rt = MissionRuntime(path, clock=SteppingClock(), adapter_mode="fixture")
        m = rt.create_mission_from_text("status organism", creation_idempotency_key="reanchor")
        mid = m.mission_id
        rt.close()

        def _strip(body: dict) -> dict:
            return {k: v for k, v in body.items() if k not in V2_ADDED_MISSION_FIELDS}

        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row

        # Strip the payload, then re-chain: rewriting an event's hash
        # invalidates its successor's back-link, so the whole mission is
        # re-linked to leave a chain that is internally valid the way a real v2
        # database's would be.
        prev = GENESIS_EVENT_HASH
        for row in con.execute(
            "SELECT sequence, body_json FROM mission_events WHERE mission_id=? ORDER BY sequence",
            (mid,),
        ).fetchall():
            ev = MissionEvent.from_dict(json.loads(row["body_json"]))
            payload = dict(ev.payload or {})
            if "mission_body" in payload:
                payload["mission_body"] = _strip(payload["mission_body"])
                ev.payload = payload
            ev.previous_event_hash = prev
            ev.compute_hash()
            prev = ev.event_hash
            con.execute(
                "UPDATE mission_events SET body_json=?, event_hash=?, previous_event_hash=? "
                "WHERE sequence=?",
                (to_canonical(ev.to_dict()), ev.event_hash, ev.previous_event_hash,
                 row["sequence"]),
            )

        body = json.loads(con.execute(
            "SELECT body_json FROM missions WHERE mission_id=?", (mid,)).fetchone()["body_json"])
        v1_body = _strip(body)
        con.execute("UPDATE missions SET body_json=?, projection_hash=? WHERE mission_id=?",
                    (to_canonical(v1_body), hash_payload(v1_body), mid))
        con.execute("UPDATE schema_meta SET value=? WHERE key='schema_version'",
                    (V2_SCHEMA_VERSION,))
        con.execute("DELETE FROM schema_meta WHERE key LIKE 'migration:%'")
        con.commit()
        con.close()
        return path, mid, v1_body

    def test_v1_projection_is_reanchored_and_verifies(self):
        path, mid, v1_body = self._v2_db_with_mission()
        store = SqliteStore(path)
        store.initialize()
        try:
            integrity = store.verify_integrity()
        finally:
            store.close()
        self.assertTrue(integrity["ok"], integrity.get("issues"))

    def test_reanchoring_preserves_every_v1_field(self):
        path, mid, v1_body = self._v2_db_with_mission()
        store = SqliteStore(path)
        store.initialize()
        store.close()
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        after = json.loads(con.execute(
            "SELECT body_json FROM missions WHERE mission_id=?", (mid,)).fetchone()["body_json"])
        con.close()
        for key, value in v1_body.items():
            self.assertEqual(after[key], value, f"v1 field changed: {key}")
        self.assertEqual(set(after) - set(v1_body), V2_ADDED_MISSION_FIELDS)

    def test_a_tampered_projection_is_refused_not_laundered(self):
        """Upgrading must not silently bless a row that was already broken."""
        path, mid, _ = self._v2_db_with_mission()
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        body = json.loads(con.execute(
            "SELECT body_json FROM missions WHERE mission_id=?", (mid,)).fetchone()["body_json"])
        body["objective"] = "tampered after the fact"
        con.execute("UPDATE missions SET body_json=? WHERE mission_id=?",
                    (json.dumps(body, sort_keys=True, separators=(",", ":")), mid))
        con.commit()
        con.close()
        with self.assertRaises(PersistenceConflict):
            SqliteStore(path).initialize()


class MigratedDatabaseIsFullyUsableTests(unittest.TestCase):
    def test_mission_survives_and_resumes_after_upgrade(self):
        path = _tmp()
        _make_v2_db(path)
        rt = MissionRuntime(path, clock=SteppingClock(), adapter_mode="fixture")
        m = rt.create_mission_from_text("status organism", creation_idempotency_key="post-mig")
        mid = m.mission_id
        rt.transition(mid, MissionState.INTAKE, reason="in")
        rt.transition(mid, MissionState.CONTEXT_RESOLUTION, reason="ctx")
        before_events = len(rt.store.list_events(mid))
        rt.close()

        rt2 = MissionRuntime(path, clock=SteppingClock("2026-08-16T12:00:00.000Z"),
                             adapter_mode="fixture")
        try:
            self.assertEqual(len(rt2.store.list_events(mid)), before_events,
                             "migration duplicated or dropped events")
            self.assertTrue(rt2.store.verify_mission_chain(mid)["ok"])
            self.assertEqual(rt2.recover(mid)["status"], "safe_to_resume")
        finally:
            rt2.close()

    def test_new_v3_capability_works_on_a_migrated_database(self):
        path = _tmp()
        _make_v2_db(path)
        rt = MissionRuntime(path, clock=SteppingClock(), adapter_mode="fixture")
        try:
            m = rt.create_mission_from_text("status organism", creation_idempotency_key="v3-cap")
            record = rt.commit_plan(
                m.mission_id,
                plan={"plan_id": "plan_after_migration", "plan_receipt": "r"},
                packet={"packet_id": "pkt_after_migration", "receipt_hash": "h"},
                strategy_id="strat_x",
                authority_binding={"authority_class": "A0", "approval_confers_execution": False},
                evidence_refs=["ev_1"],
            )
            self.assertEqual(record["plan_version"], 1)
            self.assertIsNotNone(rt.store.get_decision_packet("pkt_after_migration"))
            self.assertEqual(rt.get_mission(m.mission_id).plan_id, "plan_after_migration")
        finally:
            rt.close()


if __name__ == "__main__":
    unittest.main()
