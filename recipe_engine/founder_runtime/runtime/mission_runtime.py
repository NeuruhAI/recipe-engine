"""MissionRuntime — durable mission kernel (P1.1: event-sourced body + approvals)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from recipe_engine.founder_runtime import RUNTIME_VERSION
from recipe_engine.founder_runtime.clock import Clock, SystemClock
from recipe_engine.founder_runtime.errors import (
    MissionNotFound, StateTransitionInvalid, AdapterModeInvalid,
    MissionSchemaInvalid, ApprovalRequired, AuthorityDenied, ApprovalExpired,
    PersistenceConflict,
)
from recipe_engine.founder_runtime.ids import new_uuid, hash_payload
from recipe_engine.founder_runtime.serialization import to_canonical
from recipe_engine.founder_runtime.persistence.sqlite import SqliteStore
from recipe_engine.founder_runtime.contracts.mission import Mission, DeliverableStatus
from recipe_engine.founder_runtime.contracts.events import (
    MissionEvent, EventType, SCHEMA_EVENT, SAME_STATE_EVENT_TYPES,
)
from recipe_engine.founder_runtime.contracts.approval import Approval, ApprovalDecision
from recipe_engine.founder_runtime.contracts.receipts import ExecutionReceipt
from recipe_engine.founder_runtime.contracts.completion import CompletionReport
from recipe_engine.founder_runtime.state.states import MissionState, TERMINAL_STATES
from recipe_engine.founder_runtime.state.state_machine import StateMachine
from recipe_engine.founder_runtime.intent.compiler import compile_intent_to_mission
from recipe_engine.founder_runtime.intent.constraints import AuthorityEnvelope
from recipe_engine.founder_runtime.events.replay import replay_mission
from recipe_engine.founder_runtime.runtime.locks import LockManager, WorkerLeaseManager, default_worker_id
from recipe_engine.founder_runtime.runtime.checkpoints import make_checkpoint
from recipe_engine.founder_runtime.runtime.recovery import recover_mission
from recipe_engine.founder_runtime.runtime.approvals import (
    is_approval_gated, validate_approval_record, assert_approval_usable, gated_action_for,
)
from recipe_engine.founder_runtime.observability.logging import RuntimeLog
from recipe_engine.founder_runtime.observability.metrics import Metrics

# The two worlds a runtime's organs may come from. "live_readonly" is a label
# for evidence provenance, not a capability: it grants no execution, no
# production writes, no Governance authority, no provider actions and no
# outbound communication. Anything else fails closed.
RUNTIME_ADAPTER_MODES: frozenset[str] = frozenset({"fixture", "live_readonly"})


class MissionRuntime:
    """
    Persistent mission kernel.

    Adapter mode is an explicit, truthful label: "fixture" or "live_readonly".
    It widens self-description, never authority — see __init__.
    Model output cannot transition state — only MissionEvent via this runtime.
    Mission body mutations only via append-only events.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Clock | None = None,
        worker_id: str | None = None,
        adapter_mode: str = "fixture",
        check_same_thread: bool = True,
    ):
        # The runtime declares which world its organs came from. It was pinned
        # to "fixture" and rejected anything else, which is now a false
        # self-description: the long-horizon mission genuinely drives live
        # read-only organs, and a runtime that reports "fixture" while doing so
        # mislabels its own evidence.
        #
        # This widens a LABEL, not authority. "live_readonly" grants nothing —
        # organs are injected by a composition root, the store is local either
        # way, and the read-only envelope is unchanged. The system-status
        # vertical slice still requires "fixture" and refuses anything else.
        if adapter_mode not in RUNTIME_ADAPTER_MODES:
            raise AdapterModeInvalid(
                "MissionRuntime adapter mode must be fixture or live_readonly",
                {"mode": adapter_mode, "allowed": sorted(RUNTIME_ADAPTER_MODES)},
            )
        self.adapter_mode = adapter_mode
        self.clock = clock or SystemClock()
        self.worker_id = worker_id if worker_id is not None else default_worker_id()
        self.store = SqliteStore(db_path, check_same_thread=check_same_thread)
        self.store.initialize()
        self.locks = LockManager(self.store, self.clock)
        self.workers = WorkerLeaseManager(self.store, self.clock)
        self.log = RuntimeLog()
        self.metrics = Metrics()
        self._step_state: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        self.store.close()

    def create_mission_from_text(
        self,
        request_text: str,
        *,
        creation_idempotency_key: str = "",
        envelope: AuthorityEnvelope = AuthorityEnvelope.READ_ONLY_FIXTURE,
    ) -> Mission:
        now = self.clock.now_iso()
        mission, interpretation = compile_intent_to_mission(
            request_text,
            clock_now=now,
            creation_idempotency_key=creation_idempotency_key,
            envelope=envelope,
        )
        creation_event = self._event(
            mission, prior=None, next_state=mission.current_state,
            etype=EventType.MISSION_CREATED, reason="mission_created",
            idem=f"create:{mission.creation_idempotency_key or mission.mission_id}",
            payload={"interpretation": interpretation, "mission_body": mission.to_dict()},
        )
        created = self.store.create_mission(mission, creation_event)
        if created.mission_id != mission.mission_id and creation_idempotency_key:
            self.metrics.inc("mission_create_idempotent")
            return created
        if len(self.store.list_events(created.mission_id)) == 1:
            authority_payload = {
                "requirements": [r.to_dict() for r in created.authority_requirements],
                "policy_version": created.authority_requirements[0].policy_version,
                "reason_code": created.authority_requirements[0].reason_code,
            }
            created = self._record_same_state_event(
                created.mission_id, EventType.AUTHORITY_CLASSIFIED,
                "trusted_kernel_authority_classified", authority_payload,
                f"authority:{created.mission_id}:{created.input_hash}",
            )
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
        approval_id: str | None = None,
    ) -> Mission:
        mission = self.get_mission(mission_id)
        prior = mission.current_state
        if next_state in (MissionState.COMPLETED, MissionState.COMPLETED_WITH_EXCEPTIONS):
            raise StateTransitionInvalid(
                "completion is reachable only through complete_mission",
                {"mission_id": mission_id},
            )

        # Approval gate
        if is_approval_gated(prior, next_state):
            if not approval_id:
                raise ApprovalRequired(
                    "approval-gated transition requires approval_id",
                    {"prior": prior.value, "next": next_state.value},
                )
            action, scope = gated_action_for(prior, next_state)
            self._consume_approval_for_transition(
                mission_id=mission_id,
                approval_id=approval_id,
                action=action,
                scope=scope,
                transition_reason=reason,
            )
            # re-load after consumption event
            mission = self.get_mission(mission_id)
            prior = mission.current_state
            evidence_refs = list(evidence_refs or []) + [f"approval:{approval_id}"]

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
        return ev

    def _record_same_state_event(
        self, mission_id: str, event_type: EventType, reason: str,
        payload: dict[str, Any], idempotency_key: str,
        *, evidence_refs: list[str] | None = None,
    ) -> Mission:
        mission = self.get_mission(mission_id)
        if mission.current_state in TERMINAL_STATES:
            raise StateTransitionInvalid("terminal mission cannot append truth events", {})
        ev = self._event(
            mission, prior=mission.current_state, next_state=mission.current_state,
            etype=event_type, reason=reason, idem=idempotency_key,
            payload=payload, evidence_refs=evidence_refs or [],
        )
        StateMachine.assert_transition(mission.current_state, mission.current_state, event=ev, require_event=True)
        self.store.append_event(ev, expected_version=mission.version, mission_body=mission)
        return self.get_mission(mission_id)

    def set_deliverable_status(
        self,
        mission_id: str,
        deliverable_id: str,
        status: DeliverableStatus,
        *,
        idempotency_key: str | None = None,
        actor: str = "runtime",
    ) -> Mission:
        """Event-sourced deliverable status change — no raw body mutation."""
        mission = self.get_mission(mission_id)
        if mission.current_state in TERMINAL_STATES:
            raise StateTransitionInvalid(
                "terminal mission cannot change deliverables",
                {"state": mission.current_state.value},
            )
        prior_status = None
        for d in mission.deliverables:
            if d.deliverable_id == deliverable_id:
                prior_status = d.status
                break
        if prior_status is None:
            raise MissionSchemaInvalid(
                "unknown deliverable_id",
                {"deliverable_id": deliverable_id, "mission_id": mission_id},
            )
        if not isinstance(status, DeliverableStatus):
            try:
                status = DeliverableStatus(status)
            except ValueError as e:
                raise MissionSchemaInvalid("invalid deliverable status", {"status": str(status)}) from e
        if prior_status == status:
            return mission  # no-op, no event
        idem = idempotency_key or f"deliv:{mission_id}:{deliverable_id}:{prior_status.value}->{status.value}"
        ev = self._event(
            mission,
            prior=mission.current_state,
            next_state=mission.current_state,
            etype=EventType.DELIVERABLE_STATUS_CHANGED,
            reason="deliverable_status_changed",
            actor=actor,
            idem=idem,
            payload={
                "deliverable_id": deliverable_id,
                "prior_status": prior_status.value,
                "next_status": status.value,
            },
        )
        StateMachine.assert_transition(
            mission.current_state, mission.current_state, event=ev, require_event=True,
        )
        self.store.append_event(ev, expected_version=mission.version, mission_body=mission)
        return self.get_mission(mission_id)

    def bind_plan_id(self, mission_id: str, plan_id: str, *, idempotency_key: str | None = None) -> Mission:
        mission = self.get_mission(mission_id)
        if mission.current_state in TERMINAL_STATES:
            raise StateTransitionInvalid("terminal mission cannot bind plan", {"state": mission.current_state.value})
        if mission.plan_id == plan_id:
            return mission
        idem = idempotency_key or f"plan_bound:{mission_id}:{plan_id}"
        ev = self._event(
            mission,
            prior=mission.current_state,
            next_state=mission.current_state,
            etype=EventType.PLAN_BOUND,
            reason="plan_bound",
            idem=idem,
            payload={"plan_id": plan_id},
        )
        StateMachine.assert_transition(mission.current_state, mission.current_state, event=ev, require_event=True)
        self.store.append_event(ev, expected_version=mission.version, mission_body=mission)
        return self.get_mission(mission_id)

    def commit_plan(
        self,
        mission_id: str,
        *,
        plan: dict[str, Any],
        packet: dict[str, Any],
        strategy_id: str,
        authority_binding: dict[str, Any],
        evidence_refs: list[str],
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        """Persist a decision packet and the plan compiled from it, and bind it.

        This closes four gaps at once.  A plan used to exist only as loose JSON
        inside a step receipt: it could not be retrieved, diffed or superseded;
        its ``packet_id`` pointed at nothing storable; it carried no mission_id;
        and the mission's ``plan_id`` was an independently minted string that
        never equalled the compiled plan's own id, so the mission was bound to a
        plan no compiled plan had.

        The plan body stays immutable and content-addressed.  What is added is
        the binding (mission, packet, authority, evidence) and the lineage
        (``plan_version``, ``supersedes``) that replanning needs.
        """
        mission = self.get_mission(mission_id)
        if mission.current_state in TERMINAL_STATES:
            raise StateTransitionInvalid("terminal mission cannot compile a plan", {})

        plan_id = str(plan.get("plan_id") or "")
        if not plan_id:
            raise MissionSchemaInvalid("compiled plan has no plan_id", {})

        # Committing the same plan twice is a no-op, not a new version.  A plan
        # is immutable and content-addressed, so re-committing it must return
        # what is stored rather than mint a second version whose event would
        # collide with the first on the same idempotency key.
        existing = self.store.get_plan(plan_id)
        if existing is not None:
            if existing.get("mission_id") != mission_id:
                raise PersistenceConflict(
                    "plan id belongs to a different mission",
                    {"plan_id": plan_id, "owner": existing.get("mission_id")},
                )
            self.bind_plan_id(mission_id, plan_id)
            return existing

        version = self.store.next_plan_version(mission_id)
        record = {
            "schema_version": "fr.plan_record.v1",
            "plan_id": plan_id,
            "mission_id": mission_id,
            "packet_id": str(packet.get("packet_id") or "") or None,
            "plan_version": version,
            "supersedes": supersedes,
            "strategy_id": strategy_id,
            "plan": dict(plan),
            "plan_receipt": str(plan.get("plan_receipt") or ""),
            "authority_binding": dict(authority_binding),
            "evidence_refs": list(evidence_refs),
            "created_at": self.clock.now_iso(),
        }

        # One event carries both objects, so persistence can require each of
        # them to have been logged before it will store them.
        self._record_same_state_event(
            mission_id,
            EventType.PLAN_COMPILED,
            "plan_compiled",
            {"plan": record, "decision_packet": dict(packet), "plan_version": version},
            f"plan_compiled:{mission_id}:{plan_id}",
            evidence_refs=list(evidence_refs),
        )
        self.store.save_decision_packet(mission_id, dict(packet), created_at=record["created_at"])
        stored = self.store.save_plan(record)
        self.bind_plan_id(mission_id, plan_id)
        self.metrics.inc("plans_committed")
        return stored

    def record_approval(self, approval: Approval) -> Approval:
        now = self.clock.now_iso()
        validate_approval_record(approval, now_iso=now)
        if not approval.approval_hash:
            approval.compute_hash()
        if not approval.created_at:
            approval.created_at = now
        mission = self.get_mission(approval.mission_id)
        existing = self.store.get_approval(approval.approval_id)
        if existing is not None:
            if existing.to_dict() != approval.to_dict():
                raise PersistenceConflict("duplicate approval changed authority evidence", {"approval_id": approval.approval_id})
            return existing
        # Append APPROVAL_RECORDED to event log (same-state audit event)
        idem = f"approval:{approval.idempotency_key or approval.approval_id}"
        ev = self._event(
            mission,
            prior=mission.current_state,
            next_state=mission.current_state,
            etype=EventType.APPROVAL_RECORDED,
            reason="approval_recorded",
            idem=idem,
            payload={
                "approval_id": approval.approval_id,
                "decision": approval.decision.value,
                "action": approval.exact_requested_action,
                "scope": approval.scope,
                "expiry": approval.expiry,
            },
        )
        StateMachine.assert_transition(mission.current_state, mission.current_state, event=ev, require_event=True)
        # Use current version; if duplicate approval idempotency, save_approval returned existing
        # Event may also be duplicate
        try:
            self.store.append_event(ev, expected_version=mission.version, mission_body=mission)
        except PersistenceConflict:
            # if version raced, reload and try once with idempotent key
            mission = self.get_mission(approval.mission_id)
            self.store.append_event(ev, expected_version=mission.version, mission_body=mission)
        return self.store.save_approval(approval)

    def _consume_approval_for_transition(
        self,
        *,
        mission_id: str,
        approval_id: str,
        action: str,
        scope: str,
        transition_reason: str,
    ) -> Approval:
        mission = self.get_mission(mission_id)
        approval = self.store.get_approval(approval_id)
        if approval is None:
            raise ApprovalRequired("approval not found", {"approval_id": approval_id})
        now = self.clock.now_iso()
        assert_approval_usable(
            approval,
            mission_id=mission_id,
            action=action,
            scope=scope,
            now_iso=now,
        )
        # Compute the consumed record, then authorize it on-log before materializing.
        approval.decision = ApprovalDecision.CONSUMED
        approval.consumed_by = f"transition:{transition_reason}"
        approval.decided_at = now
        approval.compute_hash()
        # Append APPROVAL_CONSUMED event
        idem = f"approval_consumed:{approval_id}:{action}"
        ev = self._event(
            mission,
            prior=mission.current_state,
            next_state=mission.current_state,
            etype=EventType.APPROVAL_CONSUMED,
            reason="approval_consumed",
            idem=idem,
            payload={
                "approval_id": approval_id,
                "action": action,
                "scope": scope,
                "consumed_by": approval.consumed_by,
            },
            evidence_refs=[f"approval:{approval_id}"],
        )
        StateMachine.assert_transition(mission.current_state, mission.current_state, event=ev, require_event=True)
        self.store.append_event(ev, expected_version=mission.version, mission_body=mission)
        c = self.store._c()
        c.execute(
            "UPDATE approvals SET body_json=?, approval_hash=?, decision=? WHERE approval_id=?",
            (to_canonical(approval.to_dict()), approval.approval_hash, approval.decision.value, approval.approval_id),
        )
        return approval

    def record_plan_compile_failure(
        self,
        mission_id: str,
        *,
        detail: str,
        error_class: str = "plan_compile_failed",
        final: bool = True,
    ) -> Mission:
        """Fail-closed path for plan compilation failure — never COMPLETED."""
        mission = self.get_mission(mission_id)
        # Bound detail — no secrets
        bounded = (detail or "unknown")[:500]
        target = MissionState.FAILED_FINAL if final else MissionState.FAILED_RETRYABLE
        # Path to failure from EXECUTING/VERIFYING/PLANNING
        # Move step by step if needed using allowed edges
        state = mission.current_state
        if state == MissionState.EXECUTING:
            # EXECUTING -> FAILED_FINAL is allowed
            pass
        elif state == MissionState.PLANNING:
            # PLANNING cannot go directly to FAILED_FINAL — use CANCELLED or path via QUEUED?
            # transitions: PLANNING -> AWAITING_DECISION, AWAITING_APPROVAL, QUEUED, BLOCKED_*, CANCELLED, PAUSED
            # From QUEUED -> EXECUTING -> FAILED_FINAL
            # Or CANCELLED from PLANNING
            # Prefer FAILED_RETRYABLE from EXECUTING. From PLANNING use CANCELLED? Spec says FAILED preferably.
            # Looking at table: FAILED_FINAL only from EXECUTING, VERIFYING, RECONCILING, RECOVERING, FAILED_RETRYABLE
            # So from PLANNING we need to go QUEUED -> EXECUTING -> FAILED_FINAL or use CANCELLED
            # Better: PLANNING -> QUEUED -> EXECUTING -> FAILED_FINAL with events
            if state == MissionState.PLANNING:
                self.transition(mission_id, MissionState.QUEUED, reason="queue_for_failure_path")
                self.transition(mission_id, MissionState.EXECUTING, reason="enter_exec_for_failure")
                mission = self.get_mission(mission_id)
                state = mission.current_state
        elif state == MissionState.VERIFYING:
            pass  # VERIFYING -> FAILED_FINAL allowed
        elif state not in (
            MissionState.EXECUTING, MissionState.FAILED_RETRYABLE, MissionState.RECOVERING,
            MissionState.RECONCILING, MissionState.VERIFYING,
        ):
            # try reach EXECUTING
            if state == MissionState.QUEUED:
                self.transition(mission_id, MissionState.EXECUTING, reason="enter_exec_for_failure")
                mission = self.get_mission(mission_id)

        mission = self.get_mission(mission_id)
        payload = {
            "error_class": error_class,
            "detail": bounded,
        }
        # Prefer PLAN_COMPILE_FAILED then state to FAILED_FINAL
        ev = self._event(
            mission,
            prior=mission.current_state,
            next_state=target,
            etype=EventType.PLAN_COMPILE_FAILED,
            reason="plan_compile_failed",
            idem=f"plan_fail:{mission_id}:{error_class}:{hash_payload(payload)[:12]}",
            payload=payload,
        )
        StateMachine.assert_transition(mission.current_state, target, event=ev, require_event=True)
        self.store.append_event(ev, expected_version=mission.version, mission_body=mission)
        self.metrics.inc("plan_compile_failures")
        return self.get_mission(mission_id)

    def record_receipt(self, receipt: ExecutionReceipt) -> ExecutionReceipt:
        from recipe_engine.founder_runtime.evidence.validation import (
            assert_valid_live_receipt,
            assert_valid_receipt,
        )
        # Validate against the trust class the receipt itself declares.  The
        # fixture validator requires evidence_class=="fixture" and a fixture_gov
        # authority reference, so routing every receipt through it made a live
        # receipt unrecordable — the live path could not produce evidence at all.
        # Dispatching does not loosen either validator: a receipt claiming
        # fixture trust still faces every fixture check, and one claiming live
        # trust faces the live equivalents including "never mutation_authorized".
        if receipt.evidence_class == "live_observed":
            assert_valid_live_receipt(
                receipt, expected_mission_id=receipt.mission_id,
                expected_plan_id=receipt.plan_id, expected_step_id=receipt.step_id,
                expected_input_hash=receipt.input_hash, expected_output=receipt.outcome_data,
            )
        else:
            assert_valid_receipt(
                receipt, expected_mission_id=receipt.mission_id,
                expected_plan_id=receipt.plan_id, expected_step_id=receipt.step_id,
                expected_input_hash=receipt.input_hash, expected_output=receipt.outcome_data,
            )
        existing = self.store.get_receipt(receipt.receipt_id)
        if existing is not None:
            if existing.to_dict() != receipt.to_dict():
                raise PersistenceConflict("duplicate receipt changed evidence", {"receipt_id": receipt.receipt_id})
            return existing
        self._record_same_state_event(
            receipt.mission_id, EventType.RECEIPT_RECORDED, "validated_receipt_recorded",
            {"receipt": receipt.to_dict(), "trust_level": receipt.trust_level,
             "producer_identity": receipt.producer_identity},
            f"receipt:{receipt.idempotency_key}", evidence_refs=[receipt.receipt_id],
        )
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
        # The checkpoint key names a resume BOUNDARY, and a re-attempted stage
        # revisits the same boundary with a legitimately different body (fresh
        # checkpoint_id, timestamp, mission version). Same doctrine as
        # mark_side_effect: a re-emission is a new attempt, scoped by count;
        # the first emission keeps the legacy key.
        base = f"checkpoint:{ck.idempotency_key or ck.checkpoint_id}"
        prior = self.store._c().execute(
            "SELECT COUNT(*) FROM mission_events WHERE mission_id=? AND (idempotency_key=? OR idempotency_key LIKE ?)",
            (mission_id, base, f"{base}:attempt:%"),
        ).fetchone()[0]
        idem = base if prior == 0 else f"{base}:attempt:{prior + 1}"
        self._record_same_state_event(
            mission_id, EventType.CHECKPOINT_RECORDED, "checkpoint_recorded",
            {"checkpoint": ck.to_dict()}, idem,
            evidence_refs=[ck.checkpoint_id],
        )
        return self.store.save_checkpoint(ck)

    def register_artifact(self, mission_id: str, artifact: dict[str, Any], registered_at: str) -> dict[str, Any]:
        self._record_same_state_event(
            mission_id, EventType.ARTIFACT_REGISTERED, "artifact_registered",
            {"artifact": dict(artifact), "registered_at": registered_at},
            f"artifact:{artifact['artifact_id']}", evidence_refs=[artifact["artifact_id"]],
        )
        return self.store.register_artifact(mission_id, artifact, registered_at)

    def record_outcome(self, mission_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
        required = {
            "outcome_id", "mission_id", "plan_id", "status", "outcome_hash", "result_hash",
            "receipt_hashes", "deliverable_hash", "mutation_authorized", "evidence_class",
        }
        if required - set(outcome) or outcome.get("mission_id") != mission_id or outcome.get("status") != "verified":
            raise MissionSchemaInvalid("outcome evidence malformed", {})
        material = dict(outcome)
        supplied = material.pop("outcome_hash")
        if supplied != hash_payload(material):
            raise MissionSchemaInvalid("outcome evidence hash mismatch", {})
        receipts = self.store.list_receipts(mission_id)
        expected_receipts = sorted(r.receipt_hash for r in receipts)
        mission = self.get_mission(mission_id)
        expected_deliverables = hash_payload([d.to_dict() for d in mission.deliverables])
        expected_result = hash_payload({
            "receipt_hashes": expected_receipts,
            "deliverable_hash": expected_deliverables,
            "mutation": False,
        })
        # The outcome must declare the trust class of the evidence it actually
        # reconciles.  This was pinned to the literal "fixture", which both
        # blocked any live outcome and was weaker than it looked: it asserted a
        # constant rather than checking that the declared class matched the
        # stored receipts.  Deriving it means an outcome cannot claim fixture
        # provenance over live evidence, or the reverse.
        receipt_classes = {r.evidence_class for r in receipts}
        if not receipt_classes:
            expected_evidence_class = "fixture"
        elif len(receipt_classes) == 1:
            expected_evidence_class = next(iter(receipt_classes))
        else:
            expected_evidence_class = "mixed"
        if (
            sorted(outcome.get("receipt_hashes") or []) != expected_receipts
            or outcome.get("deliverable_hash") != expected_deliverables
            or outcome.get("result_hash") != expected_result
            or outcome.get("evidence_class") != expected_evidence_class
            or outcome.get("mutation_authorized") is not False
        ):
            raise MissionSchemaInvalid("outcome does not reconcile stored evidence", {})
        self._record_same_state_event(
            mission_id, EventType.OUTCOME_RECORDED, "verified_outcome_recorded",
            {"outcome": dict(outcome)}, f"outcome:{outcome['outcome_id']}",
            evidence_refs=[outcome["outcome_id"]],
        )
        self.store._c().execute(
            "INSERT OR IGNORE INTO outcomes(outcome_id, mission_id, body_json, created_at) VALUES (?,?,?,?)",
            (outcome["outcome_id"], mission_id, to_canonical(outcome), self.clock.now_iso()),
        )
        return outcome

    def record_governance(self, mission_id: str, decision: dict[str, Any]) -> Mission:
        return self._record_same_state_event(
            mission_id, EventType.GOVERNANCE_RECORDED, "fixture_governance_opinion_recorded",
            {"decision": dict(decision)}, f"governance:{decision['decision_id']}",
            evidence_refs=[decision["decision_id"]],
        )

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
        et = EventType.SIDE_EFFECT_RECONCILED if status == "reconciled" else EventType.SIDE_EFFECT_MARKED
        # A re-emission of the same (key, status) is a new attempt, not a replay
        # of the first: its body legitimately differs (fresh timestamp), and
        # append_event rightly refuses a reused key over a different body. A
        # stage interrupted between "proposed" and its receipt was therefore
        # impossible to re-propose after reconciliation — resume died on
        # PersistenceConflict. Scope re-attempts by count; the first emission
        # keeps the legacy key, so no existing event chain changes shape.
        base = f"side_effect:{key}:{status}"
        prior = self.store._c().execute(
            "SELECT COUNT(*) FROM mission_events WHERE mission_id=? AND (idempotency_key=? OR idempotency_key LIKE ?)",
            (mission_id, base, f"{base}:attempt:%"),
        ).fetchone()[0]
        idem = base if prior == 0 else f"{base}:attempt:{prior + 1}"
        self._record_same_state_event(
            mission_id, et, "side_effect_state_recorded",
            {"side_effect_key": key, "step_id": step_id, "status": status,
             "marker": dict(marker), "receipt_id": receipt_id},
            idem, evidence_refs=[] if not receipt_id else [receipt_id],
        )
        return self.store.mark_side_effect(key, mission_id, step_id, status, marker, self.clock.now_iso(), receipt_id)

    def save_completion(self, report) -> Any:
        raise StateTransitionInvalid("use complete_mission; direct completion persistence is forbidden", {})

    def complete_mission(self, report: CompletionReport) -> Mission:
        mission = self.get_mission(report.mission_id)
        if mission.current_state != MissionState.RECONCILING:
            raise StateTransitionInvalid("completion requires RECONCILING", {})
        if not report.completion_hash or report.completion_hash != report.computed_hash():
            raise MissionSchemaInvalid("completion report hash invalid", {})
        if report.final_state != MissionState.COMPLETED or not report.outcome_ref:
            raise MissionSchemaInvalid("completion report is not canonical", {})
        outcome = self.store._c().execute(
            "SELECT 1 FROM outcomes WHERE outcome_id=? AND mission_id=?",
            (report.outcome_ref, report.mission_id),
        ).fetchone()
        if not outcome:
            raise MissionSchemaInvalid("completion outcome reference unresolved", {})
        ev = self._event(
            mission, prior=mission.current_state, next_state=MissionState.COMPLETED,
            etype=EventType.COMPLETION_RECORDED, reason="canonical_completion_verified",
            idem=report.idempotency_key, payload={"completion_report": report.to_dict()},
            evidence_refs=list(report.evidence_refs) + [report.outcome_ref],
        )
        StateMachine.assert_transition(mission.current_state, MissionState.COMPLETED, event=ev, require_event=True)
        self.store.append_event(ev, expected_version=mission.version, mission_body=mission)
        self.store.save_completion(report)
        return self.get_mission(report.mission_id)
