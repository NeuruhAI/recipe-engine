"""SQLite-backed event-sourced persistence. Local/fixture only."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from recipe_engine.founder_runtime.errors import PersistenceConflict, MissionNotFound
from recipe_engine.founder_runtime.ids import hash_payload
from recipe_engine.founder_runtime.serialization import to_canonical
from recipe_engine.founder_runtime.contracts.mission import Mission
from recipe_engine.founder_runtime.contracts.events import MissionEvent
from recipe_engine.founder_runtime.contracts.checkpoint import Checkpoint
from recipe_engine.founder_runtime.contracts.approval import Approval
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt
from recipe_engine.founder_runtime.contracts.completion import CompletionReport
from .schema import SCHEMA_SQL, SCHEMA_VERSION


class SqliteStore:
    """Local transactional store. path must be temp or gitignored developer path."""

    def __init__(self, path: str | Path, *, wal: bool = True):
        self.path = Path(path)
        self._wal = wal
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._wal:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
        self._conn.executescript(SCHEMA_SQL)
        cur = self._conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'")
        row = cur.fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
        elif row["value"] != SCHEMA_VERSION:
            raise PersistenceConflict(
                "schema version mismatch",
                {"found": row["value"], "expected": SCHEMA_VERSION},
            )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _c(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("store not initialized")
        return self._conn

    def schema_version(self) -> str:
        row = self._c().execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return row["value"] if row else ""

    def create_mission(self, mission: Mission, creation_event: MissionEvent) -> Mission:
        """Atomically create the projection and its genesis event."""
        from recipe_engine.founder_runtime.contracts.events import EventType, GENESIS_EVENT_HASH, SCHEMA_EVENT
        from recipe_engine.founder_runtime.events.projector import apply_event
        from recipe_engine.founder_runtime.state.state_machine import StateMachine

        c = self._c()
        try:
            c.execute("BEGIN IMMEDIATE")
            if mission.creation_idempotency_key:
                existing = c.execute(
                    "SELECT body_json FROM missions WHERE creation_idempotency_key=?",
                    (mission.creation_idempotency_key,),
                ).fetchone()
                if existing:
                    c.execute("COMMIT")
                    return Mission.from_dict(json.loads(existing["body_json"]))
            if (
                creation_event.event_type != EventType.MISSION_CREATED
                or creation_event.schema_version != SCHEMA_EVENT
                or creation_event.mission_id != mission.mission_id
                or creation_event.prior_state is not None
                or (creation_event.payload or {}).get("mission_body") != mission.to_dict()
            ):
                c.execute("ROLLBACK")
                raise PersistenceConflict("mission creation requires exact genesis event", {})
            StateMachine.assert_transition(None, mission.current_state, event=creation_event, require_event=True)
            creation_event.chain_position = 0
            creation_event.previous_event_hash = GENESIS_EVENT_HASH
            creation_event.compute_hash()
            projected = apply_event(None, creation_event)
            projected.version = 1
            projected.updated_at = creation_event.timestamp
            body = projected.to_dict()
            proj = hash_payload(body)
            c.execute(
                """INSERT INTO missions(
                    mission_id, version, current_state, body_json, input_hash,
                    creation_idempotency_key, created_at, updated_at, completed_at, projection_hash
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    projected.mission_id,
                    projected.version,
                    projected.current_state.value,
                    to_canonical(body),
                    projected.input_hash,
                    projected.creation_idempotency_key or None,
                    projected.created_at,
                    projected.updated_at,
                    projected.completed_at,
                    proj,
                ),
            )
            event_body = creation_event.to_dict()
            cur = c.execute(
                """INSERT INTO mission_events(
                    event_id, mission_id, prior_state, next_state, event_type, actor, reason,
                    body_json, event_hash, chain_position, previous_event_hash,
                    idempotency_key, timestamp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    creation_event.event_id, creation_event.mission_id, None,
                    creation_event.next_state.value, creation_event.event_type.value,
                    creation_event.actor, creation_event.reason, to_canonical(event_body),
                    creation_event.event_hash, 0, GENESIS_EVENT_HASH,
                    creation_event.idempotency_key, creation_event.timestamp,
                ),
            )
            creation_event.sequence = cur.lastrowid
            c.execute("COMMIT")
            return projected
        except sqlite3.IntegrityError as e:
            if c.in_transaction:
                c.execute("ROLLBACK")
            # duplicate mission id
            existing = self.get_mission(mission.mission_id)
            if existing and mission.creation_idempotency_key and existing.creation_idempotency_key == mission.creation_idempotency_key:
                return existing
            raise PersistenceConflict("mission create conflict", {"error": str(e), "mission_id": mission.mission_id}) from e
        except Exception:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise

    def get_mission(self, mission_id: str) -> Mission | None:
        row = self._c().execute("SELECT body_json FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
        if not row:
            return None
        return Mission.from_dict(json.loads(row["body_json"]))

    def list_nonterminal_missions(self) -> list[Mission]:
        terminal = (
            "COMPLETED", "COMPLETED_WITH_EXCEPTIONS", "FAILED_FINAL", "CANCELLED", "SUPERSEDED"
        )
        placeholders = ",".join("?" * len(terminal))
        rows = self._c().execute(
            f"SELECT body_json FROM missions WHERE current_state NOT IN ({placeholders})",
            terminal,
        ).fetchall()
        return [Mission.from_dict(json.loads(r["body_json"])) for r in rows]

    def append_event(
        self,
        event: MissionEvent,
        *,
        expected_version: int,
        mission_body: Mission | None = None,
    ) -> MissionEvent:
        """Append event and update projection atomically.

        Projection body is derived by applying the event to the stored mission
        (or mission_created payload). CAS enforces expected_version with rowcount.
        """
        from recipe_engine.founder_runtime.events.projector import apply_event
        from recipe_engine.founder_runtime.contracts.events import GENESIS_EVENT_HASH, EventType, SCHEMA_EVENT
        from recipe_engine.founder_runtime.state.state_machine import StateMachine
        from recipe_engine.founder_runtime.state.states import TERMINAL_STATES

        c = self._c()
        try:
            c.execute("BEGIN IMMEDIATE")
            # idempotent by mission+idempotency_key
            existing = c.execute(
                "SELECT body_json, sequence FROM mission_events WHERE mission_id=? AND idempotency_key=?",
                (event.mission_id, event.idempotency_key),
            ).fetchone()
            if existing:
                ev = MissionEvent.from_dict(json.loads(existing["body_json"]))
                ev.sequence = existing["sequence"]
                # Idempotency is safe only for an exact duplicate delivery.
                candidate = event.to_dict()
                stored = ev.to_dict()
                for body in (candidate, stored):
                    body.pop("sequence", None)
                    body.pop("event_hash", None)
                    body.pop("chain_position", None)
                    body.pop("previous_event_hash", None)
                if candidate != stored:
                    c.execute("ROLLBACK")
                    raise PersistenceConflict(
                        "idempotency key reused for different event",
                        {"mission_id": event.mission_id, "idempotency_key": event.idempotency_key},
                    )
                c.execute("COMMIT")
                return ev
            row = c.execute(
                "SELECT version, body_json, projection_hash FROM missions WHERE mission_id=?",
                (event.mission_id,),
            ).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                raise MissionNotFound("mission missing for event", {"mission_id": event.mission_id})
            if int(row["version"]) != expected_version:
                c.execute("ROLLBACK")
                raise PersistenceConflict(
                    "optimistic version conflict",
                    {"expected": expected_version, "actual": row["version"], "mission_id": event.mission_id},
                )
            current = Mission.from_dict(json.loads(row["body_json"]))
            if current.current_state in TERMINAL_STATES:
                c.execute("ROLLBACK")
                raise PersistenceConflict(
                    "terminal mission event append rejected",
                    {"mission_id": event.mission_id, "state": current.current_state.value},
                )
            if (
                event.schema_version != SCHEMA_EVENT
                or event.event_type == EventType.MISSION_CREATED
                or not event.idempotency_key
                or not event.actor
                or event.correlation_id != event.mission_id
                or event.prior_state != current.current_state
            ):
                c.execute("ROLLBACK")
                raise PersistenceConflict("event identity or prior-state binding invalid", {})
            base = current
            StateMachine.assert_transition(
                current.current_state,
                event.next_state,
                event=event,
                require_event=True,
            )
            if event.next_state.value in ("COMPLETED", "COMPLETED_WITH_EXCEPTIONS"):
                if event.event_type != EventType.COMPLETION_RECORDED:
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion requires canonical completion event", {})
                report = (event.payload or {}).get("completion_report")
                if not isinstance(report, dict) or not report.get("completion_hash"):
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion event missing validated report", {})
                parsed_report = CompletionReport.from_dict(report)
                if parsed_report.completion_hash != parsed_report.computed_hash():
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion report hash mismatch", {})
                if parsed_report.mission_id != event.mission_id or parsed_report.final_state != event.next_state:
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion report binding mismatch", {})
                if not parsed_report.outcome_ref or not c.execute(
                    "SELECT 1 FROM outcomes WHERE outcome_id=? AND mission_id=?",
                    (parsed_report.outcome_ref, event.mission_id),
                ).fetchone():
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion outcome unresolved", {})
                stored_receipts = {r["receipt_id"] for r in c.execute(
                    "SELECT receipt_id FROM receipts WHERE mission_id=?", (event.mission_id,)
                ).fetchall()}
                if not set(parsed_report.evidence_refs) or not set(parsed_report.evidence_refs).issubset(stored_receipts):
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion receipt references unresolved", {})
                if parsed_report.exceptions or any(not a.passed or not a.evidence_ref for a in parsed_report.acceptance_criteria_results):
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion truth gate waived or acceptance failed", {})
                accepted_refs = {a.evidence_ref for a in parsed_report.acceptance_criteria_results}
                if not accepted_refs.issubset(stored_receipts):
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion acceptance references unresolved", {})
                if any(d.required and d.status.value != "delivered" for d in current.deliverables):
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("required deliverables not delivered", {})
                stored_artifacts = {r["artifact_id"] for r in c.execute(
                    "SELECT artifact_id FROM artifacts WHERE mission_id=?", (event.mission_id,)
                ).fetchall()}
                if not set(parsed_report.delivered_artifacts).issubset(stored_artifacts):
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion artifact references unresolved", {})
                receipt_authorities = {
                    ExecutionReceipt.from_dict(json.loads(r["body_json"])).authority_reference
                    for r in c.execute("SELECT body_json FROM receipts WHERE mission_id=?", (event.mission_id,)).fetchall()
                }
                if set(parsed_report.authority_refs) != receipt_authorities:
                    c.execute("ROLLBACK")
                    raise PersistenceConflict("completion authority references mismatch", {})

            latest = c.execute(
                "SELECT body_json FROM mission_events WHERE mission_id=? ORDER BY chain_position DESC LIMIT 1",
                (event.mission_id,),
            ).fetchone()
            prior_event = MissionEvent.from_dict(json.loads(latest["body_json"])) if latest else None
            expected_position = 0 if prior_event is None else int(prior_event.chain_position) + 1
            expected_predecessor = GENESIS_EVENT_HASH if prior_event is None else prior_event.event_hash
            if event.chain_position not in (None, expected_position):
                c.execute("ROLLBACK")
                raise PersistenceConflict("event chain position mismatch", {})
            if event.previous_event_hash not in ("", expected_predecessor):
                c.execute("ROLLBACK")
                raise PersistenceConflict("event predecessor mismatch", {})
            event.chain_position = expected_position
            event.previous_event_hash = expected_predecessor
            event.compute_hash()
            applied = apply_event(base, event)
            new_version = expected_version + 1
            applied.version = new_version
            applied.current_state = event.next_state
            applied.updated_at = event.timestamp
            if event.next_state.value in (
                "COMPLETED", "COMPLETED_WITH_EXCEPTIONS", "FAILED_FINAL", "CANCELLED", "SUPERSEDED"
            ):
                applied.completed_at = event.timestamp
            body = applied.to_dict()
            proj = hash_payload(body)
            event_body = event.to_dict()
            cur = c.execute(
                """INSERT INTO mission_events(
                    event_id, mission_id, prior_state, next_state, event_type, actor, reason,
                    body_json, event_hash, chain_position, previous_event_hash,
                    idempotency_key, timestamp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.event_id,
                    event.mission_id,
                    None if event.prior_state is None else event.prior_state.value,
                    event.next_state.value,
                    event.event_type.value,
                    event.actor,
                    event.reason,
                    to_canonical(event_body),
                    event.event_hash,
                    event.chain_position,
                    event.previous_event_hash,
                    event.idempotency_key,
                    event.timestamp,
                ),
            )
            seq = cur.lastrowid
            # CAS projection update — must affect exactly one row
            cur2 = c.execute(
                """UPDATE missions SET version=?, current_state=?, body_json=?, updated_at=?,
                   completed_at=?, projection_hash=? WHERE mission_id=? AND version=?""",
                (
                    new_version,
                    event.next_state.value,
                    to_canonical(body),
                    event.timestamp,
                    applied.completed_at,
                    proj,
                    event.mission_id,
                    expected_version,
                ),
            )
            if cur2.rowcount != 1:
                c.execute("ROLLBACK")
                raise PersistenceConflict(
                    "CAS rowcount failed on mission projection update",
                    {"mission_id": event.mission_id, "expected_version": expected_version, "rowcount": cur2.rowcount},
                )
            c.execute("COMMIT")
            event.sequence = seq
            # mutate caller's mission_body if provided so callers see updates
            if mission_body is not None:
                mission_body.version = applied.version
                mission_body.current_state = applied.current_state
                mission_body.updated_at = applied.updated_at
                mission_body.completed_at = applied.completed_at
                mission_body.deliverables = applied.deliverables
                mission_body.plan_id = applied.plan_id
                mission_body.extensions = applied.extensions
            return event
        except (PersistenceConflict, MissionNotFound):
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as e:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            existing = c.execute(
                "SELECT body_json, sequence FROM mission_events WHERE mission_id=? AND idempotency_key=?",
                (event.mission_id, event.idempotency_key),
            ).fetchone()
            if existing:
                ev = MissionEvent.from_dict(json.loads(existing["body_json"]))
                ev.sequence = existing["sequence"]
                return ev
            raise PersistenceConflict("event append conflict", {"error": str(e)}) from e
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def list_events(self, mission_id: str) -> list[MissionEvent]:
        rows = self._c().execute(
            "SELECT body_json, sequence FROM mission_events WHERE mission_id=? ORDER BY sequence ASC",
            (mission_id,),
        ).fetchall()
        out: list[MissionEvent] = []
        for r in rows:
            ev = MissionEvent.from_dict(json.loads(r["body_json"]))
            ev.sequence = r["sequence"]
            out.append(ev)
        return out

    def save_checkpoint(self, checkpoint: Checkpoint) -> Checkpoint:
        c = self._c()
        self._require_logged_object(checkpoint.mission_id, "checkpoint_recorded", "checkpoint", checkpoint.to_dict())
        body = checkpoint.to_dict()
        try:
            c.execute("BEGIN IMMEDIATE")
            if checkpoint.idempotency_key:
                ex = c.execute(
                    "SELECT body_json FROM checkpoints WHERE mission_id=? AND idempotency_key=?",
                    (checkpoint.mission_id, checkpoint.idempotency_key),
                ).fetchone()
                if ex:
                    c.execute("COMMIT")
                    return Checkpoint.from_dict(json.loads(ex["body_json"]))
            c.execute(
                """INSERT INTO checkpoints(
                    checkpoint_id, mission_id, plan_id, body_json, state_hash, resumable,
                    created_at, idempotency_key, kind
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    checkpoint.checkpoint_id,
                    checkpoint.mission_id,
                    checkpoint.plan_id,
                    to_canonical(body),
                    checkpoint.state_hash,
                    1 if checkpoint.resumable else 0,
                    checkpoint.created_at,
                    checkpoint.idempotency_key or None,
                    checkpoint.kind,
                ),
            )
            c.execute("COMMIT")
            return checkpoint
        except sqlite3.IntegrityError as e:
            c.execute("ROLLBACK")
            if checkpoint.idempotency_key:
                ex = c.execute(
                    "SELECT body_json FROM checkpoints WHERE mission_id=? AND idempotency_key=?",
                    (checkpoint.mission_id, checkpoint.idempotency_key),
                ).fetchone()
                if ex:
                    return Checkpoint.from_dict(json.loads(ex["body_json"]))
            raise PersistenceConflict("checkpoint conflict", {"error": str(e)}) from e
        except Exception:
            c.execute("ROLLBACK")
            raise

    def latest_checkpoint(self, mission_id: str) -> Checkpoint | None:
        row = self._c().execute(
            "SELECT body_json FROM checkpoints WHERE mission_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (mission_id,),
        ).fetchone()
        return Checkpoint.from_dict(json.loads(row["body_json"])) if row else None

    def list_checkpoints(self, mission_id: str) -> list[Checkpoint]:
        rows = self._c().execute(
            "SELECT body_json FROM checkpoints WHERE mission_id=? ORDER BY created_at ASC, rowid ASC",
            (mission_id,),
        ).fetchall()
        return [Checkpoint.from_dict(json.loads(r["body_json"])) for r in rows]

    def save_approval(self, approval: Approval) -> Approval:
        c = self._c()
        self._require_logged_reference(approval.mission_id, "approval_recorded", approval.approval_id)
        if not approval.approval_hash:
            approval.compute_hash()
        body = approval.to_dict()
        try:
            c.execute("BEGIN IMMEDIATE")
            if approval.idempotency_key:
                ex = c.execute(
                    "SELECT body_json FROM approvals WHERE mission_id=? AND idempotency_key=?",
                    (approval.mission_id, approval.idempotency_key),
                ).fetchone()
                if ex:
                    c.execute("COMMIT")
                    return Approval.from_dict(json.loads(ex["body_json"]))
            c.execute(
                """INSERT INTO approvals(approval_id, mission_id, body_json, approval_hash, idempotency_key, decision)
                   VALUES (?,?,?,?,?,?)""",
                (
                    approval.approval_id,
                    approval.mission_id,
                    to_canonical(body),
                    approval.approval_hash,
                    approval.idempotency_key or None,
                    approval.decision.value,
                ),
            )
            c.execute("COMMIT")
            return approval
        except sqlite3.IntegrityError as e:
            c.execute("ROLLBACK")
            if approval.idempotency_key:
                ex = c.execute(
                    "SELECT body_json FROM approvals WHERE mission_id=? AND idempotency_key=?",
                    (approval.mission_id, approval.idempotency_key),
                ).fetchone()
                if ex:
                    return Approval.from_dict(json.loads(ex["body_json"]))
            raise PersistenceConflict("approval conflict", {"error": str(e)}) from e
        except Exception:
            c.execute("ROLLBACK")
            raise

    def list_approvals(self, mission_id: str) -> list[Approval]:
        rows = self._c().execute(
            "SELECT body_json FROM approvals WHERE mission_id=?", (mission_id,)
        ).fetchall()
        return [Approval.from_dict(json.loads(r["body_json"])) for r in rows]

    def get_approval(self, approval_id: str) -> Approval | None:
        row = self._c().execute(
            "SELECT body_json FROM approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        return Approval.from_dict(json.loads(row["body_json"])) if row else None

    def save_receipt(self, receipt: ExecutionReceipt) -> ExecutionReceipt:
        c = self._c()
        self._require_logged_object(receipt.mission_id, "receipt_recorded", "receipt", receipt.to_dict())
        if not receipt.receipt_hash:
            receipt.compute_hash()
        body = receipt.to_dict()
        try:
            c.execute("BEGIN IMMEDIATE")
            ex = c.execute(
                "SELECT body_json FROM receipts WHERE mission_id=? AND idempotency_key=?",
                (receipt.mission_id, receipt.idempotency_key),
            ).fetchone()
            if ex:
                c.execute("COMMIT")
                return ExecutionReceipt.from_dict(json.loads(ex["body_json"]))
            c.execute(
                """INSERT INTO receipts(
                    receipt_id, mission_id, plan_id, step_id, body_json, receipt_hash, idempotency_key, status
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    receipt.receipt_id,
                    receipt.mission_id,
                    receipt.plan_id,
                    receipt.step_id,
                    to_canonical(body),
                    receipt.receipt_hash,
                    receipt.idempotency_key,
                    receipt.status.value,
                ),
            )
            c.execute("COMMIT")
            return receipt
        except sqlite3.IntegrityError as e:
            c.execute("ROLLBACK")
            ex = c.execute(
                "SELECT body_json FROM receipts WHERE mission_id=? AND idempotency_key=?",
                (receipt.mission_id, receipt.idempotency_key),
            ).fetchone()
            if ex:
                return ExecutionReceipt.from_dict(json.loads(ex["body_json"]))
            raise PersistenceConflict("receipt conflict", {"error": str(e)}) from e
        except Exception:
            c.execute("ROLLBACK")
            raise

    def list_receipts(self, mission_id: str) -> list[ExecutionReceipt]:
        rows = self._c().execute(
            "SELECT body_json FROM receipts WHERE mission_id=?", (mission_id,)
        ).fetchall()
        return [ExecutionReceipt.from_dict(json.loads(r["body_json"])) for r in rows]

    def get_receipt(self, receipt_id: str) -> ExecutionReceipt | None:
        row = self._c().execute(
            "SELECT body_json FROM receipts WHERE receipt_id=?", (receipt_id,)
        ).fetchone()
        return ExecutionReceipt.from_dict(json.loads(row["body_json"])) if row else None

    def register_artifact(self, mission_id: str, artifact: dict[str, Any], registered_at: str) -> dict[str, Any]:
        c = self._c()
        self._require_logged_object(mission_id, "artifact_registered", "artifact", artifact)
        aid = artifact["artifact_id"]
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                """INSERT OR IGNORE INTO artifacts(
                    artifact_id, mission_id, kind, content_hash, pointer, body_json, registered_at
                ) VALUES (?,?,?,?,?,?,?)""",
                (
                    aid,
                    mission_id,
                    artifact["kind"],
                    artifact["content_hash"],
                    artifact["pointer"],
                    to_canonical(artifact),
                    registered_at,
                ),
            )
            c.execute("COMMIT")
            return artifact
        except Exception:
            c.execute("ROLLBACK")
            raise

    def list_artifacts(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self._c().execute(
            "SELECT body_json FROM artifacts WHERE mission_id=?", (mission_id,)
        ).fetchall()
        return [json.loads(r["body_json"]) for r in rows]

    def save_completion(self, report: CompletionReport) -> CompletionReport:
        c = self._c()
        if not report.completion_hash or report.completion_hash != report.computed_hash():
            raise PersistenceConflict("completion hash invalid", {})
        row = c.execute(
            "SELECT body_json FROM mission_events WHERE mission_id=? AND event_type='completion_recorded' ORDER BY sequence DESC LIMIT 1",
            (report.mission_id,),
        ).fetchone()
        if not row or (json.loads(row["body_json"]).get("payload") or {}).get("completion_report") != report.to_dict():
            raise PersistenceConflict("completion not authorized by canonical event", {})
        if not report.completion_hash:
            report.compute_hash()
        body = report.to_dict()
        try:
            c.execute("BEGIN IMMEDIATE")
            if report.idempotency_key:
                ex = c.execute(
                    "SELECT body_json FROM completion_reports WHERE idempotency_key=?",
                    (report.idempotency_key,),
                ).fetchone()
                if ex:
                    c.execute("COMMIT")
                    return CompletionReport.from_dict(json.loads(ex["body_json"]))
            ex2 = c.execute(
                "SELECT body_json FROM completion_reports WHERE mission_id=?",
                (report.mission_id,),
            ).fetchone()
            if ex2:
                c.execute("COMMIT")
                return CompletionReport.from_dict(json.loads(ex2["body_json"]))
            c.execute(
                """INSERT INTO completion_reports(mission_id, body_json, completion_hash, idempotency_key, completed_at)
                   VALUES (?,?,?,?,?)""",
                (
                    report.mission_id,
                    to_canonical(body),
                    report.completion_hash,
                    report.idempotency_key or None,
                    report.completed_at,
                ),
            )
            c.execute("COMMIT")
            return report
        except sqlite3.IntegrityError as e:
            c.execute("ROLLBACK")
            ex = c.execute(
                "SELECT body_json FROM completion_reports WHERE mission_id=?",
                (report.mission_id,),
            ).fetchone()
            if ex:
                return CompletionReport.from_dict(json.loads(ex["body_json"]))
            raise PersistenceConflict("completion conflict", {"error": str(e)}) from e
        except Exception:
            c.execute("ROLLBACK")
            raise

    def get_completion(self, mission_id: str) -> CompletionReport | None:
        row = self._c().execute(
            "SELECT body_json FROM completion_reports WHERE mission_id=?", (mission_id,)
        ).fetchone()
        return CompletionReport.from_dict(json.loads(row["body_json"])) if row else None

    def mark_side_effect(
        self,
        key: str,
        mission_id: str,
        step_id: str,
        status: str,
        marker: dict[str, Any],
        ts: str,
        receipt_id: str | None = None,
    ) -> dict[str, Any]:
        c = self._c()
        self._require_logged_reference(mission_id, "side_effect_marked" if status != "reconciled" else "side_effect_reconciled", key)
        try:
            c.execute("BEGIN IMMEDIATE")
            ex = c.execute("SELECT * FROM side_effect_ledger WHERE side_effect_key=?", (key,)).fetchone()
            if ex:
                c.execute(
                    """UPDATE side_effect_ledger SET status=?, marker_json=?, updated_at=?, receipt_id=COALESCE(?, receipt_id)
                       WHERE side_effect_key=?""",
                    (status, to_canonical(marker), ts, receipt_id, key),
                )
            else:
                c.execute(
                    """INSERT INTO side_effect_ledger(
                        side_effect_key, mission_id, step_id, status, marker_json, created_at, updated_at, receipt_id
                    ) VALUES (?,?,?,?,?,?,?,?)""",
                    (key, mission_id, step_id, status, to_canonical(marker), ts, ts, receipt_id),
                )
            c.execute("COMMIT")
            return self.get_side_effect(key) or {}
        except Exception:
            c.execute("ROLLBACK")
            raise

    def get_side_effect(self, key: str) -> dict[str, Any] | None:
        row = self._c().execute(
            "SELECT * FROM side_effect_ledger WHERE side_effect_key=?", (key,)
        ).fetchone()
        if not row:
            return None
        return {
            "side_effect_key": row["side_effect_key"],
            "mission_id": row["mission_id"],
            "step_id": row["step_id"],
            "status": row["status"],
            "marker": json.loads(row["marker_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "receipt_id": row["receipt_id"],
        }

    def list_side_effects(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self._c().execute(
            "SELECT side_effect_key FROM side_effect_ledger WHERE mission_id=?", (mission_id,)
        ).fetchall()
        out = []
        for r in rows:
            se = self.get_side_effect(r["side_effect_key"])
            if se:
                out.append(se)
        return out

    def acquire_lock(
        self,
        lock_name: str,
        owner: str,
        token: str,
        acquired_at: str,
        expires_at: str,
        mission_id: str | None = None,
    ) -> bool:
        """Acquire a new lock lease.

        Unexpired locks cannot be stolen — even by the same owner string —
        unless the exact token is presented (renew path).
        Expired locks may be taken over by any owner with a new token.
        """
        c = self._c()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM runtime_locks WHERE lock_name=?", (lock_name,)).fetchone()
            if row:
                unexpired = row["expires_at"] > acquired_at
                if unexpired:
                    # renew only with exact owner+token
                    if row["owner"] == owner and row["token"] == token:
                        c.execute(
                            "UPDATE runtime_locks SET expires_at=?, acquired_at=? WHERE lock_name=? AND owner=? AND token=?",
                            (expires_at, acquired_at, lock_name, owner, token),
                        )
                        c.execute("COMMIT")
                        return True
                    # any other attempt on unexpired lock fails closed
                    c.execute("ROLLBACK")
                    return False
                # expired — takeover with new token/owner
                c.execute(
                    """UPDATE runtime_locks SET owner=?, token=?, acquired_at=?, expires_at=?, mission_id=?
                       WHERE lock_name=?""",
                    (owner, token, acquired_at, expires_at, mission_id, lock_name),
                )
                c.execute("COMMIT")
                return True
            c.execute(
                """INSERT INTO runtime_locks(lock_name, owner, mission_id, acquired_at, expires_at, token)
                   VALUES (?,?,?,?,?,?)""",
                (lock_name, owner, mission_id, acquired_at, expires_at, token),
            )
            c.execute("COMMIT")
            return True
        except sqlite3.IntegrityError:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            return False
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def renew_lock(
        self,
        lock_name: str,
        owner: str,
        token: str,
        acquired_at: str,
        expires_at: str,
    ) -> bool:
        """Renew only the exact owner+token lease."""
        return self.acquire_lock(lock_name, owner, token, acquired_at, expires_at)

    def release_lock(self, lock_name: str, owner: str, token: str) -> bool:
        c = self._c()
        cur = c.execute(
            "DELETE FROM runtime_locks WHERE lock_name=? AND owner=? AND token=?",
            (lock_name, owner, token),
        )
        return cur.rowcount > 0

    def get_lock(self, lock_name: str) -> dict[str, Any] | None:
        row = self._c().execute(
            "SELECT * FROM runtime_locks WHERE lock_name=?", (lock_name,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def force_expire_lock(self, lock_name: str, now: str) -> bool:
        c = self._c()
        cur = c.execute(
            "DELETE FROM runtime_locks WHERE lock_name=? AND expires_at<=?",
            (lock_name, now),
        )
        return cur.rowcount > 0

    def upsert_worker_lease(
        self,
        worker_id: str,
        mission_id: str | None,
        leased_at: str,
        expires_at: str,
        heartbeat_at: str,
        status: str,
    ) -> None:
        c = self._c()
        c.execute(
            """INSERT INTO worker_leases(worker_id, mission_id, leased_at, expires_at, heartbeat_at, status)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(worker_id) DO UPDATE SET
                 mission_id=excluded.mission_id,
                 leased_at=excluded.leased_at,
                 expires_at=excluded.expires_at,
                 heartbeat_at=excluded.heartbeat_at,
                 status=excluded.status""",
            (worker_id, mission_id, leased_at, expires_at, heartbeat_at, status),
        )

    def get_worker_lease(self, worker_id: str) -> dict[str, Any] | None:
        row = self._c().execute(
            "SELECT * FROM worker_leases WHERE worker_id=?", (worker_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_stale_workers(self, now: str) -> list[dict[str, Any]]:
        rows = self._c().execute(
            "SELECT * FROM worker_leases WHERE expires_at<=?", (now,)
        ).fetchall()
        return [dict(r) for r in rows]

    def save_dependency_snapshot(
        self, snapshot_id: str, mission_id: str, body: dict[str, Any], created_at: str
    ) -> None:
        self._c().execute(
            """INSERT OR REPLACE INTO dependency_snapshots(snapshot_id, mission_id, body_json, created_at)
               VALUES (?,?,?,?)""",
            (snapshot_id, mission_id, to_canonical(body), created_at),
        )

    def verify_integrity(self) -> dict[str, Any]:
        """Full-store verification: every event hash recomputed from canonical
        stored material, column-vs-body hash agreement, chain linkage, order,
        and projection rebuild. Any tamper mode fails (ok=False)."""
        from recipe_engine.founder_runtime.events.projector import (
            rebuild_mission_from_events,
            projection_hash,
        )
        c = self._c()
        issues: list[str] = []
        events_all = c.execute(
            "SELECT mission_id, body_json, event_hash, sequence FROM mission_events ORDER BY sequence ASC"
        ).fetchall()
        by_mission: dict[str, list] = {}
        for e in events_all:
            mid = e["mission_id"]
            by_mission.setdefault(mid, []).append(e)
            body = json.loads(e["body_json"])
            ev = MissionEvent.from_dict(body)
            # stored event_hash column must agree with the stored body
            if body.get("event_hash") != e["event_hash"]:
                issues.append(f"event_hash_column_stomp:{ev.event_id}")
                continue
            # recomputed hash from canonical material must match stored hash
            if not ev.verify_hash():
                issues.append(f"event_hash_mismatch:{ev.event_id}")

        missions = c.execute(
            "SELECT mission_id, body_json, projection_hash, version FROM missions"
        ).fetchall()
        for mrow in missions:
            mid = mrow["mission_id"]
            stored = Mission.from_dict(json.loads(mrow["body_json"]))
            body_hash = hash_payload(stored.to_dict())
            if body_hash != mrow["projection_hash"]:
                issues.append(f"projection_hash_mismatch:{mid}")
            chain = self.verify_mission_chain(mid)
            if not chain["ok"]:
                issues.extend(chain["issues"])
            ev_rows = by_mission.get(mid) or []
            if not ev_rows:
                issues.append(f"no_events:{mid}")
                continue
            try:
                evs = [MissionEvent.from_dict(json.loads(er["body_json"])) for er in ev_rows]
                rebuilt = rebuild_mission_from_events(evs)
                if projection_hash(rebuilt) != body_hash:
                    issues.append(f"rebuild_divergence:{mid}")
                if rebuilt.current_state != stored.current_state:
                    issues.append(f"state_divergence:{mid}")
                if rebuilt.version != stored.version:
                    issues.append(f"version_divergence:{mid}")
            except Exception as ex:
                issues.append(f"rebuild_error:{mid}:{type(ex).__name__}")
        # orphan events spliced onto a missing/foreign mission identity
        mission_ids = {m["mission_id"] for m in missions}
        for mid in by_mission:
            if mid not in mission_ids:
                issues.append(f"orphan_events:{mid}")
        return {
            "ok": len(issues) == 0,
            "schema_version": self.schema_version(),
            "issues": issues,
            "mission_count": len(missions),
            "event_count": len(events_all),
        }

    def verify_mission_chain(self, mission_id: str) -> dict[str, Any]:
        """Fail-closed verification of one mission's event chain.

        Recomputes every event hash from canonical stored material and checks
        stored-vs-column hash agreement, genesis link, previous-hash linkage,
        strict sequence/chain-position contiguity (no gaps, no duplicates, no
        reorder), mission identity, and event-version consistency. Returns a
        dict with ok/issues; never mutates the store.
        """
        from recipe_engine.founder_runtime.contracts.events import GENESIS_EVENT_HASH

        c = self._c()
        issues: list[str] = []
        mrow = c.execute(
            "SELECT version, current_state FROM missions WHERE mission_id=?", (mission_id,)
        ).fetchone()
        if mrow is None:
            return {"ok": False, "issues": [f"mission_missing:{mission_id}"],
                    "mission_id": mission_id, "event_count": 0, "tail_sequence": None}
        rows = c.execute(
            "SELECT sequence, event_id, mission_id, chain_position, previous_event_hash,"
            " event_hash, body_json, timestamp FROM mission_events WHERE mission_id=?"
            " ORDER BY sequence ASC",
            (mission_id,),
        ).fetchall()
        if not rows:
            return {"ok": False, "issues": [f"event_chain_empty:{mission_id}"],
                    "mission_id": mission_id, "event_count": 0, "tail_sequence": None}
        prev_hash = GENESIS_EVENT_HASH
        prev_sequence: int | None = None
        seen_positions: set[int] = set()
        for expected_position, r in enumerate(rows):
            body = json.loads(r["body_json"])
            ev = MissionEvent.from_dict(body)
            # column-vs-body agreement for immutable metadata used by the hash
            if body.get("event_hash") != r["event_hash"]:
                issues.append(f"event_hash_column_stomp:{ev.event_id}")
                continue
            if body.get("previous_event_hash") != r["previous_event_hash"]:
                issues.append(f"previous_hash_column_stomp:{ev.event_id}")
            if int(body.get("chain_position", -1)) != r["chain_position"]:
                issues.append(f"chain_position_column_stomp:{ev.event_id}")
            if body.get("timestamp") != r["timestamp"]:
                issues.append(f"timestamp_column_stomp:{ev.event_id}")
            # recomputed hash from canonical stored material
            if not ev.verify_hash():
                issues.append(f"event_hash_mismatch:{ev.event_id}")
            # mission identity binding
            if ev.mission_id != mission_id or ev.correlation_id != mission_id:
                issues.append(f"cross_mission_splice:{ev.event_id}")
            # previous-event link
            if ev.previous_event_hash != prev_hash:
                issues.append(f"previous_hash_tamper:{ev.event_id}")
            # strict contiguous chain position (detects deletion/insertion/reorder/dup)
            if ev.chain_position != expected_position:
                issues.append(f"chain_position_gap:{ev.event_id}")
            if ev.chain_position in seen_positions:
                issues.append(f"duplicate_chain_position:{ev.event_id}")
            seen_positions.add(int(ev.chain_position))
            # strict sequence contiguity
            if prev_sequence is not None and int(r["sequence"]) != prev_sequence + 1:
                issues.append(f"sequence_gap:{ev.event_id}")
            prev_sequence = int(r["sequence"])
            prev_hash = ev.event_hash
        # tail event count must match the mission's recorded version (truncated tail)
        if int(mrow["version"]) != len(rows):
            issues.append(f"tail_version_mismatch:{mission_id}")
        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "mission_id": mission_id,
            "event_count": len(rows),
            "tail_sequence": prev_sequence,
        }

    def rebuild_projection_from_events(self, mission_id: str) -> Mission:
        """Rebuild mission projection from the authoritative event stream."""
        from recipe_engine.founder_runtime.events.projector import rebuild_mission_from_events
        events = self.list_events(mission_id)
        if not events:
            raise MissionNotFound("no events for mission", {"mission_id": mission_id})
        return rebuild_mission_from_events(events)

    def _event_payloads(self, mission_id: str, event_type: str) -> list[dict[str, Any]]:
        rows = self._c().execute(
            "SELECT body_json FROM mission_events WHERE mission_id=? AND event_type=? ORDER BY sequence",
            (mission_id, event_type),
        ).fetchall()
        return [(json.loads(row["body_json"]).get("payload") or {}) for row in rows]

    def _require_logged_object(self, mission_id: str, event_type: str, key: str, value: dict[str, Any]) -> None:
        if not any(payload.get(key) == value for payload in self._event_payloads(mission_id, event_type)):
            raise PersistenceConflict("mission truth mutation lacks canonical event", {"event_type": event_type})

    def _require_logged_reference(self, mission_id: str, event_type: str, reference: str) -> None:
        def contains(payload: dict[str, Any]) -> bool:
            return reference in {
                str(payload.get("approval_id") or ""), str(payload.get("side_effect_key") or ""),
            }
        if not any(contains(payload) for payload in self._event_payloads(mission_id, event_type)):
            raise PersistenceConflict("mission truth mutation lacks canonical event", {"event_type": event_type})
