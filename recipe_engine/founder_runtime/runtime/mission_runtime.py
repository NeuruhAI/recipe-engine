"""MissionRuntime — durable mission kernel orchestration (fixture adapters only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from recipe_engine.founder_runtime import RUNTIME_VERSION
from recipe_engine.founder_runtime.clock import Clock, SystemClock
from recipe_engine.founder_runtime.errors import (
    MissionNotFound, StateTransitionInvalid, ReceiptMissing, AdapterModeInvalid,
    MissionSchemaInvalid,
)
from recipe_engine.founder_runtime.ids import new_uuid, hash_payload
from recipe_engine.founder_runtime.persistence.sqlite import SqliteStore
from recipe_engine.founder_runtime.contracts.mission import Mission, DeliverableStatus
from recipe_engine.founder_runtime.contracts.events import MissionEvent, EventType, SCHEMA_EVENT
from recipe_engine.founder_runtime.contracts.approval import Approval, ApprovalDecision, SCHEMA_APPROVAL
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.state.state_machine import StateMachine
from recipe_engine.founder_runtime.intent.compiler import compile_intent_to_mission
from recipe_engine.founder_runtime.events.replay import replay_mission
from recipe_engine.founder_runtime.runtime.locks import LockManager, WorkerLeaseManager
from recipe_engine.founder_runtime.runtime.checkpoints import make_checkpoint
from recipe_engine.founder_runtime.runtime.recovery import recover_mission
from recipe_engine.founder_runtime.runtime.completion import verify_completion
from recipe_engine.founder_runtime.contracts.completion import AcceptanceResult
from recipe_engine.founder_runtime.observability.logging import RuntimeLog
from recipe_engine.founder_runtime.observability.metrics import Metrics


class MissionRuntime:
    """
    Persistent mission kernel.

    Adapter mode must be explicit fixture. No live services.
    Model output cannot transition state — only MissionEvent via this runtime.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Clock | None = None,
        worker_id: str = "worker_local",
        adapter_mode: str = "fixture",
    ):
        if adapter_mode != "fixture":
            raise AdapterModeInvalid("MissionRuntime P1 requires adapter_mode='fixture'", {"mode": adapter_mode})
        self.adapter_mode = "fixture"
        self.clock = clock or SystemClock()
        self.worker_id = worker_id
        self.store = SqliteStore(db_path)
        self.store.initialize()
        self.locks = LockManager(self.store, self.clock)
        self.workers = WorkerLeaseManager(self.store, self.clock)
        self.log = RuntimeLog()
        self.metrics = Metrics()
        self._step_state: dict[str, dict[str, Any]] = {}  # mission_id -> runtime memory (also checkpointed)

    def close(self) -> None:
        self.store.close()

    # ---- mission lifecycle ----

    def create_mission_from_text(
        self,
        request_text: str,
        *,
        creation_idempotency_key: str = "",
    ) -> Mission:
        now = self.clock.now_iso()
        mission, interpretation = compile_intent_to_mission(
            request_text,
            clock_now=now,
            creation_idempotency_key=creation_idempotency_key,
        )
        # If already BLOCKED_INPUT from compiler, still persist
        created = self.store.create_mission(mission)
        if created.mission_id != mission.mission_id and creation_idempotency_key:
            # idempotent return
            self.metrics.inc("mission_create_idempotent")
            return created
        # append creation event
        if created.version == 0 and not self.store.list_events(created.mission_id):
            ev = self._event(
                created,
                prior=None,
                next_state=created.current_state,
                etype=EventType.MISSION_CREATED,
                reason="mission_created",
                idem=f"create:{created.creation_idempotency_key or created.mission_id}",
                payload={"interpretation": interpretation},
            )
            # for initial create, version is 0; append bumps to 1
            self.store.append_event(ev, expected_version=0, mission_body=created)
            created = self.store.get_mission(created.mission_id) or created
        self.metrics.inc("missions_created")
        self.log.emit(
            event_type="mission_created",
            mission_id=created.mission_id,
            state=created.current_state.value,
            result="ok",
        )
        return created

    def get_mission(self, mission_id: str) -> Mission:
        m = self.store.get_mission(mission_id)
        if m is None:
            raise MissionNotFound("mission not found", {"mission_id": mission_id})
        return m

    def transition(
        self,
        mission_id: str,
        next_state: MissionState,
        *,
        reason: str,
        actor: str = "runtime",
        event_type: EventType = EventType.STATE_TRANSITION,
        idempotency_key: str | None = None,
        payload: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> Mission:
        mission = self.get_mission(mission_id)
        prior = mission.current_state
        ev = self._event(
            mission,
            prior=prior,
            next_state=next_state,
            etype=event_type,
            reason=reason,
            actor=actor,
            idem=idempotency_key or f"tx:{mission_id}:{prior.value}:{next_state.value}:{reason}",
            payload=payload or {},
            evidence_refs=evidence_refs or [],
        )
        StateMachine.assert_transition(prior, next_state, event=ev, require_event=True)
        self.store.append_event(ev, expected_version=mission.version, mission_body=mission)
        updated = self.get_mission(mission_id)
        self.metrics.inc("transitions")
        self.log.emit(
            event_type="state_transition",
            mission_id=mission_id,
            state=updated.current_state.value,
            result="ok",
            extra={"prior": prior.value, "next": next_state.value},
        )
        return updated

    def _event(
        self,
        mission: Mission,
        *,
        prior: MissionState | None,
        next_state: MissionState,
        etype: EventType,
        reason: str,
        idem: str,
        actor: str = "runtime",
        payload: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> MissionEvent:
        now = self.clock.now_iso()
        ev = MissionEvent(
            schema_version=SCHEMA_EVENT,
            event_id=new_uuid("evt"),
            mission_id=mission.mission_id,
            prior_state=prior,
            next_state=next_state,
            event_type=etype,
            actor=actor,
            reason=reason,
            evidence_refs=list(evidence_refs or []),
            timestamp=now,
            idempotency_key=idem,
            causation_id=None,
            correlation_id=mission.mission_id,
            payload=payload or {},
        )
        ev.compute_hash()
        return ev

    def record_approval(self, approval: Approval) -> Approval:
        if not approval.approval_hash:
            approval.compute_hash()
        saved = self.store.save_approval(approval)
        mission = self.get_mission(approval.mission_id)
        # event without state change — stay same state
        ev = self._event(
            mission,
            prior=mission.current_state,
            next_state=mission.current_state,
            etype=EventType.APPROVAL_RECORDED,
            reason="approval_recorded",
            idem=f"approval:{approval.idempotency_key or approval.approval_id}",
            payload={"approval_id": approval.approval_id, "decision": approval.decision.value},
        )
        # same-state transition not in table — so only store approval without state event
        # Spec: every state change requires event. Non-state records are separate.
        return saved

    def record_receipt(self, receipt: ExecutionReceipt) -> ExecutionReceipt:
        return self.store.save_receipt(receipt)

    def checkpoint(
        self,
        mission_id: str,
        *,
        completed_steps: list[str],
        pending_steps: list[str],
        active_step: str | None,
        last_receipt: str | None,
        kind: str,
        plan_id: str | None = None,
    ):
        mission = self.get_mission(mission_id)
        ck = make_checkpoint(
            mission,
            clock_now=self.clock.now_iso(),
            completed_steps=completed_steps,
            pending_steps=pending_steps,
            active_step=active_step,
            last_receipt=last_receipt,
            kind=kind,
            plan_id=plan_id,
        )
        return self.store.save_checkpoint(ck)

    def inspect(self, mission_id: str) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        return {
            "runtime_version": RUNTIME_VERSION,
            "mission": mission.to_dict(),
            "events": [e.to_dict() for e in self.store.list_events(mission_id)],
            "checkpoints": [c.to_dict() for c in self.store.list_checkpoints(mission_id)],
            "receipts": [r.to_dict() for r in self.store.list_receipts(mission_id)],
            "approvals": [a.to_dict() for a in self.store.list_approvals(mission_id)],
            "artifacts": self.store.list_artifacts(mission_id),
            "completion": None if not self.store.get_completion(mission_id) else self.store.get_completion(mission_id).to_dict(),
            "side_effects": self.store.list_side_effects(mission_id),
        }

    def events(self, mission_id: str) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.store.list_events(mission_id)]

    def replay(self, mission_id: str) -> dict[str, Any]:
        return replay_mission(self.store, mission_id)

    def recover(self, mission_id: str) -> dict[str, Any]:
        result = recover_mission(self.store, mission_id, clock_now=self.clock.now_iso())
        self.metrics.inc(f"recovery_{result.get('status')}")
        return result

    def mark_side_effect(self, key: str, mission_id: str, step_id: str, status: str, marker: dict[str, Any], receipt_id: str | None = None):
        return self.store.mark_side_effect(key, mission_id, step_id, status, marker, self.clock.now_iso(), receipt_id)

    def save_completion(self, report) -> Any:
        return self.store.save_completion(report)

    def set_deliverable_status(self, mission_id: str, deliverable_id: str, status: DeliverableStatus) -> Mission:
        mission = self.get_mission(mission_id)
        new_dels = []
        for d in mission.deliverables:
            if d.deliverable_id == deliverable_id:
                from dataclasses import replace
                new_dels.append(replace(d, status=status))
            else:
                new_dels.append(d)
        mission.deliverables = new_dels
        # persist via no-op version bump event? better update body through a synthetic same-state is invalid
        # Use a checkpoint + direct projection update via transition payload and body merge in append
        # Simpler: store mission body by appending a non-transition event is hard with current schema.
        # We update mission by re-reading after a RECONCILING transition with payload that includes deliverables.
        # For P1: allow internal method to write mission body with version CAS.
        c = self.store._c()
        body = mission.to_dict()
        from recipe_engine.founder_runtime.serialization import to_canonical
        from recipe_engine.founder_runtime.ids import hash_payload
        c.execute(
            "UPDATE missions SET body_json=?, projection_hash=? WHERE mission_id=? AND version=?",
            (to_canonical(body), hash_payload(body), mission_id, mission.version),
        )
        return mission
