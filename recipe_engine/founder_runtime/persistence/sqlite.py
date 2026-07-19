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

    def create_mission(self, mission: Mission) -> Mission:
        c = self._c()
        body = mission.to_dict()
        proj = hash_payload(body)
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
            c.execute(
                """INSERT INTO missions(
                    mission_id, version, current_state, body_json, input_hash,
                    creation_idempotency_key, created_at, updated_at, completed_at, projection_hash
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    mission.mission_id,
                    mission.version,
                    mission.current_state.value,
                    to_canonical(body),
                    mission.input_hash,
                    mission.creation_idempotency_key or None,
                    mission.created_at,
                    mission.updated_at,
                    mission.completed_at,
                    proj,
                ),
            )
            c.execute("COMMIT")
            return mission
        except sqlite3.IntegrityError as e:
            c.execute("ROLLBACK")
            # duplicate mission id
            existing = self.get_mission(mission.mission_id)
            if existing and mission.creation_idempotency_key and existing.creation_idempotency_key == mission.creation_idempotency_key:
                return existing
            raise PersistenceConflict("mission create conflict", {"error": str(e), "mission_id": mission.mission_id}) from e
        except Exception:
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
        from recipe_engine.founder_runtime.events.projector import apply_event, projection_hash

        c = self._c()
        if not event.event_hash:
            event.compute_hash()
        try:
            c.execute("BEGIN IMMEDIATE")
            # idempotent by mission+idempotency_key
            existing = c.execute(
                "SELECT body_json, sequence FROM mission_events WHERE mission_id=? AND idempotency_key=?",
                (event.mission_id, event.idempotency_key),
            ).fetchone()
            if existing:
                c.execute("COMMIT")
                ev = MissionEvent.from_dict(json.loads(existing["body_json"]))
                ev.sequence = existing["sequence"]
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
            # Prefer pure event application; mission_body only used as optional base for create
            base = None if event.event_type.value == "mission_created" else current
            if event.event_type.value == "mission_created":
                # ensure payload has mission body
                if not (event.payload or {}).get("mission_body"):
                    # fallback: use stored or supplied
                    body_src = mission_body.to_dict() if mission_body is not None else current.to_dict()
                    event.payload = dict(event.payload or {})
                    event.payload["mission_body"] = body_src
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
                    body_json, event_hash, idempotency_key, timestamp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
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
        """Validate event hashes and that stored projection matches event rebuild."""
        from recipe_engine.founder_runtime.events.projector import (
            rebuild_mission_from_events,
            projection_hash,
        )
        c = self._c()
        issues: list[str] = []
        events_all = c.execute(
            "SELECT mission_id, body_json, event_hash FROM mission_events ORDER BY sequence ASC"
        ).fetchall()
        by_mission: dict[str, list] = {}
        for e in events_all:
            mid = e["mission_id"]
            by_mission.setdefault(mid, []).append(e)
            body = json.loads(e["body_json"])
            from recipe_engine.founder_runtime.contracts.events import MissionEvent
            ev = MissionEvent.from_dict(body)
            if not ev.verify_hash():
                issues.append(f"event_hash_mismatch:{ev.event_id}")

        missions = c.execute(
            "SELECT mission_id, body_json, projection_hash, version FROM missions"
        ).fetchall()
        for mrow in missions:
            mid = mrow["mission_id"]
            stored = Mission.from_dict(json.loads(mrow["body_json"]))
            # self-hash of stored body vs column
            body_hash = hash_payload(stored.to_dict())
            if body_hash != mrow["projection_hash"]:
                issues.append(f"projection_hash_mismatch:{mid}")
            # rebuild from events must match stored body
            ev_rows = by_mission.get(mid) or []
            if not ev_rows:
                issues.append(f"no_events:{mid}")
                continue
            try:
                evs = []
                for er in ev_rows:
                    from recipe_engine.founder_runtime.contracts.events import MissionEvent
                    evs.append(MissionEvent.from_dict(json.loads(er["body_json"])))
                rebuilt = rebuild_mission_from_events(evs)
                if projection_hash(rebuilt) != body_hash:
                    issues.append(f"rebuild_divergence:{mid}")
                if rebuilt.current_state != stored.current_state:
                    issues.append(f"state_divergence:{mid}")
                if rebuilt.version != stored.version:
                    issues.append(f"version_divergence:{mid}")
            except Exception as ex:
                issues.append(f"rebuild_error:{mid}:{type(ex).__name__}")
        return {
            "ok": len(issues) == 0,
            "schema_version": self.schema_version(),
            "issues": issues,
            "mission_count": len(missions),
            "event_count": len(events_all),
        }

    def rebuild_projection_from_events(self, mission_id: str) -> Mission:
        """Rebuild mission projection from the authoritative event stream."""
        from recipe_engine.founder_runtime.events.projector import rebuild_mission_from_events
        events = self.list_events(mission_id)
        if not events:
            raise MissionNotFound("no events for mission", {"mission_id": mission_id})
        return rebuild_mission_from_events(events)
