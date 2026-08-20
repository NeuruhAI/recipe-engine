"""Mission service — bridges the HTTP API to the persistent mission kernel.

Authority rules enforced here:
  - creating a mission runs the read-only decision pipeline and parks the
    mission at AWAITING_APPROVAL with a proposed plan (never executes it);
  - approve/reject changes AUTHORITY STATE ONLY; it never executes the plan;
  - pause/resume/cancel are operator lifecycle controls;
  - every state-changing call is idempotent and optimistic-version checked.

No external action, no mutation, no Workers dispatch, no production persistence.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

from recipe_engine.autonomy import collapse_scenarios, compile_execution_plan, default_config
from recipe_engine.autonomy.models import (
    CandidateStrategy, CapabilityCatalog, MetricValue, OpportunityEnvelope,
    SV_CAPABILITY, SV_METRIC, SV_OPPORTUNITY, SV_STRATEGY,
)
from recipe_engine.founder_runtime import RUNTIME_VERSION
from recipe_engine.founder_runtime.adapters.fixture_governance import FixtureGovernance
from recipe_engine.founder_runtime.adapters.fixture_mother import FixtureMother
from recipe_engine.founder_runtime.clock import SystemClock
from recipe_engine.founder_runtime.contracts.approval import Approval, ApprovalDecision, SCHEMA_APPROVAL
from recipe_engine.founder_runtime.contracts.events import EventType
from recipe_engine.founder_runtime.contracts.mission import Mission
from recipe_engine.founder_runtime.errors import (
    ApprovalRequired, AuthorityDenied, FounderRuntimeError, MissionNotFound,
    PersistenceConflict, StateTransitionInvalid,
)
from recipe_engine.founder_runtime.evidence.claims import build_claims_from_fixtures
from recipe_engine.founder_runtime.ids import hash_payload, new_uuid, stable_id
from recipe_engine.founder_runtime.runtime.approvals import gated_action_for
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.state.states import MissionState, TERMINAL_STATES

API_VERSION = "fr-api-v1"

# What the operator may do next, by mission state. Execution is never allowed.
_ALLOWED_NEXT = {
    "DRAFT": ["get", "cancel"],
    "INTAKE": ["get", "pause", "cancel"],
    "CONTEXT_RESOLUTION": ["get", "pause", "cancel"],
    "PLANNING": ["get", "pause", "cancel"],
    "AWAITING_DECISION": ["get", "decision_packet", "pause", "cancel"],
    "AWAITING_APPROVAL": ["get", "decision_packet", "plan", "approve", "reject", "pause", "cancel"],
    "APPROVED_NO_EXECUTION": ["get", "decision_packet", "plan", "pause", "cancel"],
    "QUEUED": ["get", "pause", "cancel"],
    "EXECUTING": ["get", "pause", "cancel"],
    "VERIFYING": ["get", "pause", "cancel"],
    "RECONCILING": ["get", "pause", "cancel"],
    "PAUSED": ["get", "resume", "cancel", "recover"],
    "RECOVERING": ["get", "recover", "cancel"],
    "FAILED_RETRYABLE": ["get", "recover", "cancel"],
    "BLOCKED_INPUT": ["get", "cancel"],
    "BLOCKED_DEPENDENCY": ["get", "recover", "cancel"],
    "BLOCKED_POLICY": ["get", "approve", "reject", "cancel"],
}
_BLOCKED_ALWAYS = ["execute_plan", "external_action", "mutate_axon", "workers_dispatch", "production_persist"]


def _iso_now(clock) -> str:
    return clock.now_iso()


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class MissionService:
    """Owns the runtime and idempotency ledger. Single-writer via a lock."""

    def __init__(self, db_path: str | Path, *, clock=None):
        self.db_path = str(db_path)
        self.clock = clock or SystemClock()
        self._lock = threading.RLock()
        self.runtime = MissionRuntime(self.db_path, clock=self.clock, adapter_mode="fixture", check_same_thread=False)
        self._idem = sqlite3.connect(self._idem_path(), isolation_level=None, check_same_thread=False)
        self._idem.row_factory = sqlite3.Row
        self._idem.execute(
            "CREATE TABLE IF NOT EXISTS idempotency ("
            " key TEXT PRIMARY KEY, request_hash TEXT NOT NULL,"
            " response_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )

    def _idem_path(self) -> str:
        return self.db_path + ".idem"

    def close(self) -> None:
        try:
            self._idem.close()
        finally:
            self.runtime.close()

    # ---- idempotency ----
    def _idem_lookup(self, key: str, request_hash: str) -> dict[str, Any] | None:
        row = self._idem.execute(
            "SELECT request_hash, response_json FROM idempotency WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT",
                           "idempotency key replayed with a different request",
                           {"key": key})
        return json.loads(row["response_json"])

    def _idem_store(self, key: str, request_hash: str, response: dict[str, Any]) -> None:
        self._idem.execute(
            "INSERT OR IGNORE INTO idempotency(key, request_hash, response_json, created_at)"
            " VALUES (?,?,?,?)",
            (key, request_hash, json.dumps(response, sort_keys=True), self.clock.now_iso()),
        )

    # ---- envelope ----
    def _envelope(self, mission: Mission, *, receipt_id: str | None = None,
                  evidence_refs: list[str] | None = None) -> dict[str, Any]:
        state = mission.current_state.value
        approvals = self.runtime.store.list_approvals(mission.mission_id)
        latest_approval = approvals[-1].to_dict() if approvals else None
        authority = {
            "requirements": [a.to_dict() for a in mission.authority_requirements],
            "latest_approval": latest_approval,
            "state": self._authority_state(mission, approvals),
            "mutation_authorized": False,
            "external_action_authorized": False,
            "production_persistence_authorized": False,
            "executed": False,
            "execution_enabled": False,
        }
        receipts = self.runtime.store.list_receipts(mission.mission_id)
        rid = receipt_id or (receipts[-1].receipt_id if receipts else None) or stable_id(
            "env", mission.mission_id, mission.version, mission.current_state.value)
        return {
            "api_version": API_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "mission_id": mission.mission_id,
            "state": state,
            "version": mission.version,
            "evidence_refs": list(evidence_refs or []) + [r.receipt_id for r in receipts],
            "authority": authority,
            "allowed_next_actions": list(_ALLOWED_NEXT.get(state, ["get"])),
            "blocked_actions": list(_BLOCKED_ALWAYS),
            "freshness": {"observed_at": self.clock.now_iso(), "stale": False},
            "receipt_id": rid,
        }

    # ---- helpers ----
    def _get(self, mission_id: str) -> Mission:
        try:
            return self.runtime.get_mission(mission_id)
        except MissionNotFound as e:
            raise ApiError(404, "MISSION_NOT_FOUND", "mission not found", {"mission_id": mission_id}) from e

    def _assert_version(self, mission: Mission, expected_version: int | None) -> None:
        if expected_version is not None and expected_version != mission.version:
            raise ApiError(409, "VERSION_CONFLICT",
                           "stale version; reload the mission and retry",
                           {"expected": expected_version, "actual": mission.version})

    def _require_expected_version(self, expected_version: int | None) -> int:
        """X-Expected-Version is mandatory on every mission/authority mutation."""
        if expected_version is None:
            raise ApiError(428, "EXPECTED_VERSION_REQUIRED",
                           "X-Expected-Version header is required for state-changing calls",
                           {})
        return expected_version

    @staticmethod
    def _authority_state(mission: Mission, approvals: list) -> str:
        """Explicit authority truth: PENDING until a terminal ALLOW/DENY exists."""
        for a in reversed(approvals):
            if a.decision == ApprovalDecision.ALLOW:
                return "APPROVED"
            if a.decision == ApprovalDecision.DENY:
                return "REJECTED"
        return "PENDING"

    def _assert_not_corrupted(self, mission_id: str) -> None:
        """Fail closed if the mission's event chain fails integrity verification."""
        verdict = self.runtime.store.verify_mission_chain(mission_id)
        if not verdict["ok"]:
            raise ApiError(409, "INTEGRITY_FAILURE",
                           "mission event store failed integrity verification; "
                           "refusing to expose a projection as trustworthy",
                           {"mission_id": mission_id,
                            "issue_count": len(verdict["issues"]),
                            "first_issue": verdict["issues"][0] if verdict["issues"] else ""})

    # ---- decision pipeline (read-only, fixture) ----
    def _run_decision_pipeline(self, mission_id: str) -> dict[str, Any]:
        """Produce decision packet + proposed plan, park at AWAITING_APPROVAL."""
        rt = self.runtime
        mission = rt.transition(mission_id, MissionState.INTAKE, reason="api_intake")
        mission = rt.transition(mission_id, MissionState.CONTEXT_RESOLUTION, reason="api_context")
        mission = rt.transition(mission_id, MissionState.PLANNING, reason="api_planning")

        mother = FixtureMother(mode="fixture")
        now = self.clock.now_iso()
        snap = mother.health_snapshot(observed_at=now)
        health = snap.to_dict()
        repos: list[dict[str, Any]] = []
        claims, evidence = build_claims_from_fixtures(health=health, repos=repos, observed_at=now)

        opp = OpportunityEnvelope(
            schema_version=SV_OPPORTUNITY,
            opportunity_id=stable_id("opp", mission_id),
            domain="organism_status",
            territory={"scope": "neuruh"},
            subject={"mission_id": mission_id},
            assets=(),
            desired_outcome="safe_next_action_plan",
            constraints=tuple(mission.constraints),
            authority_context={"sensitivity": "internal", "allowed_domains": ["organism_status"]},
            time_horizon="immediate",
            claims=tuple(claims),
            evidence=tuple(evidence),
            evidence_completeness=Decimal("0.85"),
            supplied_financial_inputs=(),
            risk_tolerance="low",
            operator_requirements=("read_only", "no_mutation"),
            version="1",
        )

        def _metric(mid_: str, val: str) -> MetricValue:
            return MetricValue(schema_version=SV_METRIC, metric_id=mid_, value=Decimal(val),
                               unit="score", scale="0..1", source="domain_supplied", label=mid_)

        candidates = [
            CandidateStrategy(
                schema_version=SV_STRATEGY, strategy_id="strat_inspect_only",
                title="Inspect status and propose governed next plan",
                thesis="Read-only status then propose governed next action",
                strategy_family="inspect",
                required_claim_ids=("claim_overall_health", "claim_blocked"),
                assumption_claim_ids=(),
                expected_outcomes=(_metric("econ.attractiveness", "0.7"),),
                resources=("fixture_mother", "p0_autonomy"),
                dependencies=("fixture_runtime",),
                duration="minutes", reversibility="reversible",
                failure_modes=("stale_fixture",),
                legal_policy_checks=("read_only",),
                governance_class="standard",
                next_safe_action="review_status_report",
                stop_conditions=("approval_required_for_mutation",),
                evidence_gaps=(),
                action_classes=("observe", "analysis"),
                capital_intensity="none", time_profile="short",
                available_dependencies=("fixture_runtime",),
                missing_dependencies=(),
                stages_hint=(
                    {"stage_id": "s1", "title": "Publish status", "dependencies": [], "action_class": "observe"},
                    {"stage_id": "s2", "title": "Propose plan", "dependencies": ["s1"], "action_class": "analysis"},
                ),
            ),
            CandidateStrategy(
                schema_version=SV_STRATEGY, strategy_id="strat_wait",
                title="Wait for live integrations",
                thesis="Defer until live adapters wired",
                strategy_family="defer",
                required_claim_ids=("claim_overall_health",),
                assumption_claim_ids=(),
                expected_outcomes=(_metric("econ.attractiveness", "0.3"),),
                resources=(), dependencies=(),
                duration="days", reversibility="reversible",
                failure_modes=("stagnation",),
                legal_policy_checks=("read_only",),
                governance_class="standard",
                next_safe_action="await_integrations",
                stop_conditions=(),
                evidence_gaps=("live_health",),
                action_classes=("observe",),
                capital_intensity="none", time_profile="long",
                available_dependencies=(), missing_dependencies=(),
            ),
        ]
        packet = collapse_scenarios(opp, candidates, config=default_config())
        packet_dict = packet.to_dict()

        plan_dict: dict[str, Any] = {"blocked": True, "reason": "no_viable_strategy"}
        if packet.selected_strategy_ids:
            catalog = CapabilityCatalog(
                schema_version=SV_CAPABILITY,
                capabilities=("observe.read", "research.read", "analysis.local"),
            )
            try:
                plan = compile_execution_plan(packet, packet.selected_strategy_ids[0], catalog, config=default_config())
                plan_dict = plan.to_dict()
            except Exception as e:  # noqa: BLE001
                plan_dict = {"failed": True, "error_class": "plan_compile_failed", "detail": str(e)[:300]}

        # Persist packet + plan on the mission body via a same-state event payload.
        plan_id = stable_id("plan", mission_id, "founder_objective")
        rt.bind_plan_id(mission_id, plan_id)
        rt._record_same_state_event(
            mission_id, EventType.OUTCOME_RECORDED, "decision_packet_and_plan_proposed",
            {"packet": packet_dict, "plan": plan_dict, "plan_id": plan_id},
            f"plan_compiled:{mission_id}:{packet_dict.get('packet_id')}",
            evidence_refs=[packet_dict.get("packet_id") or "", plan_id],
        )
        mission = rt.transition(mission_id, MissionState.AWAITING_APPROVAL, reason="plan_proposed")
        return {"packet": packet_dict, "plan": plan_dict, "plan_id": plan_id, "health": health}


    def get_mission(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            return self._get_mission_impl(mission_id)

    def decision_packet(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            return self._decision_packet_impl(mission_id)

    def proposed_plan(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            return self._proposed_plan_impl(mission_id)

    def events(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            return self._events_impl(mission_id)

    def events_page(self, mission_id: str, *, after_sequence: int,
                    limit: int = 500) -> dict[str, Any]:
        with self._lock:
            return self._events_page(mission_id, after_sequence=after_sequence, limit=limit)

    def receipts(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            return self._receipts_impl(mission_id)

    def recover(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            return self._recover_impl(mission_id)

    def replay(self, mission_id: str) -> dict[str, Any]:
        with self._lock:
            return self._replay_impl(mission_id)

    # ---- public operations ----
    def create_mission(self, *, objective: str, title: str = "", done_criteria: str = "",
                       idempotency_key: str, expected_version: int | None = None) -> dict[str, Any]:
        request_hash = hash_payload({"objective": objective, "title": title, "done_criteria": done_criteria})
        cached = self._idem_lookup(idempotency_key, request_hash)
        if cached is not None:
            cached["idempotent_replay"] = True
            return cached
        with self._lock:
            request_text = objective if not done_criteria else f"{objective}\nDone when: {done_criteria}"
            mission = self.runtime.create_mission_from_text(request_text, creation_idempotency_key=idempotency_key)
            mid = mission.mission_id
            result: dict[str, Any]
            if mission.extensions.get("initial_block") == "BLOCKED_INPUT":
                body = self._envelope(self._get(mid))
                body["blocked_question"] = mission.extensions.get("blocked_question")
                result = {"envelope": body, "decision_packet": None, "proposed_plan": None}
            else:
                pipe = self._run_decision_pipeline(mid)
                body = self._envelope(self._get(mid), evidence_refs=[pipe["packet"].get("packet_id") or ""])
                result = {"envelope": body, "decision_packet": pipe["packet"],
                          "proposed_plan": pipe["plan"], "plan_id": pipe["plan_id"]}
            self._idem_store(idempotency_key, request_hash, result)
            return result

    def _get_mission_impl(self, mission_id: str) -> dict[str, Any]:
        mission = self._get(mission_id)
        self._assert_not_corrupted(mission_id)
        return {"envelope": self._envelope(mission), "mission": mission.to_dict()}

    def list_missions(self) -> dict[str, Any]:
        with self._lock:
            return self._list_missions_impl()

    def _list_missions_impl(self) -> dict[str, Any]:
        missions = self.runtime.store.list_missions()
        return {
            "count": len(missions),
            "missions": [self._envelope(m) for m in missions],
        }

    def _decision_packet_impl(self, mission_id: str) -> dict[str, Any]:
        mission = self._get(mission_id)
        self._assert_not_corrupted(mission_id)
        events = self.runtime.store.list_events(mission_id)
        packet = None
        for ev in events:
            if ev.event_type == EventType.OUTCOME_RECORDED and "packet" in ev.payload:
                packet = ev.payload.get("packet")
        if packet is None:
            raise ApiError(404, "DECISION_PACKET_NOT_FOUND",
                           "no decision packet for mission", {"mission_id": mission_id})
        return {"envelope": self._envelope(mission), "decision_packet": packet}

    def _proposed_plan_impl(self, mission_id: str) -> dict[str, Any]:
        mission = self._get(mission_id)
        self._assert_not_corrupted(mission_id)
        events = self.runtime.store.list_events(mission_id)
        plan = None
        plan_id = None
        for ev in events:
            if ev.event_type == EventType.OUTCOME_RECORDED and "plan" in ev.payload:
                plan = ev.payload.get("plan")
                plan_id = ev.payload.get("plan_id")
        if plan is None:
            raise ApiError(404, "PLAN_NOT_FOUND", "no proposed plan for mission", {"mission_id": mission_id})
        return {"envelope": self._envelope(mission), "proposed_plan": plan, "plan_id": plan_id}

    def _record_decision(self, mission_id: str, *, decision: ApprovalDecision, reason: str,
                         idempotency_key: str, expected_version: int | None,
                         gate: bool = False) -> dict[str, Any]:
        with self._lock:
            self._require_expected_version(expected_version)
            mission = self._get(mission_id)
            request_hash = hash_payload({"decision": decision.value, "reason": reason,
                                         "mission_id": mission_id, "gate": gate})
            # Exact replay returns the stored receipt even after the state advanced.
            cached = self._idem_lookup(idempotency_key, request_hash)
            if cached is not None:
                cached["idempotent_replay"] = True
                return cached
            if mission.current_state in TERMINAL_STATES:
                raise ApiError(409, "MISSION_TERMINAL", "mission is terminal", {"state": mission.current_state.value})
            self._assert_version(mission, expected_version)
            # Single-decision latch: exactly one terminal authority decision per
            # (mission, plan/proposal version, requested scope). Backed by the
            # UNIQUE(mission_id, idempotency_key) approvals constraint plus this
            # atomic in-lock check — no stacked approvals, no last-approval-wins.
            # Checked before the awaiting-approval gate so a later conflicting
            # decision gets the precise DECISION_CONFLICT, not a state error.
            # A gate decision is bound to the transition the mission kernel will
            # actually consume it for, using the kernel's own vocabulary, so the
            # approval this records satisfies assert_approval_usable exactly.
            # A plan decision keeps its plan-scoped identity.
            if gate:
                requested_action, scope = gated_action_for(
                    MissionState.AWAITING_APPROVAL, MissionState.QUEUED,
                )
            else:
                requested_action = f"plan_decision:{decision.value}"
                scope = mission.plan_id or mission_id
            for existing in self.runtime.store.list_approvals(mission_id):
                if existing.decision not in (ApprovalDecision.ALLOW, ApprovalDecision.DENY):
                    continue
                if existing.scope != scope:
                    continue
                if (existing.decision == decision
                        and existing.exact_requested_action == requested_action):
                    # Identical semantic replay: return the existing terminal decision.
                    body = self._envelope(self._get(mission_id), receipt_id=existing.approval_id,
                                          evidence_refs=[existing.approval_id])
                    return {"envelope": body, "approval": existing.to_dict(),
                            "authority_state": existing.decision.value,
                            "executed": False, "idempotent_replay": True,
                            "note": "approval already recorded; plan NOT executed (no mutation authority in V1)"}
                raise ApiError(409, "DECISION_CONFLICT",
                               "a terminal authority decision already exists for this "
                               "mission/plan/scope; conflicting decisions are refused",
                               {"existing_decision": existing.decision.value,
                                "approval_id": existing.approval_id})
            if mission.current_state not in (MissionState.AWAITING_APPROVAL, MissionState.BLOCKED_POLICY):
                raise ApiError(409, "NOT_AWAITING_APPROVAL",
                               "mission is not awaiting an approval decision",
                               {"state": mission.current_state.value})
            now = self.clock.now_iso()
            classified = mission.authority_requirements[0] if mission.authority_requirements else None
            approval = Approval(
                schema_version=SCHEMA_APPROVAL,
                approval_id=new_uuid("appr"),
                mission_id=mission_id,
                action_class=classified.authority_class.value if classified else "A0",
                exact_requested_action=requested_action,
                scope=scope,
                requested_by="founder",
                decided_by="jeramie",
                decision=decision,
                conditions=[reason] if reason else [],
                expiry=None,
                evidence_refs=[mission.plan_id or ""],
                created_at=now,
                decided_at=now,
                idempotency_key=idempotency_key,
            )
            recorded = self.runtime.record_approval(approval)
            # Approval changes authority state only. An allow parks the plan in
            # APPROVED_NO_EXECUTION (a precise non-executing state); it NEVER
            # queues/executes it (no executor in V1).
            # A plan approval parks in APPROVED_NO_EXECUTION: this surface has no
            # executor and will not advance the mission. A GATE approval must not
            # advance it either, but for the opposite reason — the mission kernel
            # is the one that consumes it, and it can only do that from
            # AWAITING_APPROVAL. Advancing here would strand the approval in a
            # state the kernel cannot gate from.
            if decision == ApprovalDecision.ALLOW and not gate:
                self.runtime.transition(mission_id, MissionState.APPROVED_NO_EXECUTION,
                                        reason="plan_approved_no_execution_authority")
            body = self._envelope(self._get(mission_id), receipt_id=recorded.approval_id,
                                  evidence_refs=[recorded.approval_id])
            result = {"envelope": body, "approval": recorded.to_dict(),
                      "authority_state": decision.value,
                      "executed": False,
                      "note": "approval recorded; plan NOT executed (no mutation authority in V1)"}
            self._idem_store(idempotency_key, request_hash, result)
            return result

    def approve_plan(self, mission_id: str, *, reason: str, idempotency_key: str,
                     expected_version: int | None = None) -> dict[str, Any]:
        return self._record_decision(mission_id, decision=ApprovalDecision.ALLOW, reason=reason,
                                     idempotency_key=idempotency_key, expected_version=expected_version)

    def reject_plan(self, mission_id: str, *, reason: str, idempotency_key: str,
                    expected_version: int | None = None) -> dict[str, Any]:
        if not reason:
            raise ApiError(422, "REASON_REQUIRED", "rejection requires a reason", {})
        return self._record_decision(mission_id, decision=ApprovalDecision.DENY, reason=reason,
                                     idempotency_key=idempotency_key, expected_version=expected_version)

    def approve_mission_gate(self, mission_id: str, *, reason: str, idempotency_key: str,
                             expected_version: int | None = None) -> dict[str, Any]:
        """Record the founder's ALLOW for the mission's authority gate.

        This is the external origin the live mission gate requires. It records
        authority state and nothing else: no execution, no state advance, no
        transitive authority. The mission process — separately, on its own
        resume — consults Governance and consumes this exactly once.
        """
        return self._record_decision(mission_id, decision=ApprovalDecision.ALLOW, reason=reason,
                                     idempotency_key=idempotency_key,
                                     expected_version=expected_version, gate=True)

    def reject_mission_gate(self, mission_id: str, *, reason: str, idempotency_key: str,
                            expected_version: int | None = None) -> dict[str, Any]:
        """Record the founder's DENY for the mission's authority gate."""
        if not reason.strip():
            raise ApiError(422, "REASON_REQUIRED", "rejection requires a reason", {})
        return self._record_decision(mission_id, decision=ApprovalDecision.DENY, reason=reason,
                                     idempotency_key=idempotency_key,
                                     expected_version=expected_version, gate=True)

    def _lifecycle(self, mission_id: str, *, action: str, idempotency_key: str,
                   expected_version: int | None) -> dict[str, Any]:
        with self._lock:
            self._require_expected_version(expected_version)
            mission = self._get(mission_id)
            request_hash = hash_payload({"action": action, "mission_id": mission_id})
            cached = self._idem_lookup(idempotency_key, request_hash)
            if cached is not None:
                cached["idempotent_replay"] = True
                return cached
            self._assert_version(mission, expected_version)
            try:
                if action == "pause":
                    updated = self.runtime.transition(mission_id, MissionState.PAUSED, reason="operator_pause")
                elif action == "resume":
                    mission = self._get(mission_id)
                    if mission.current_state != MissionState.PAUSED:
                        raise ApiError(409, "NOT_PAUSED", "only a paused mission can resume",
                                       {"state": mission.current_state.value})
                    # Resume returns to the authority-correct parking state: an
                    # approved plan resumes to APPROVED_NO_EXECUTION; a still-
                    # pending plan resumes to AWAITING_APPROVAL.
                    existing_approvals = self.runtime.store.list_approvals(mission_id)
                    target = (MissionState.APPROVED_NO_EXECUTION
                              if self._authority_state(mission, existing_approvals) == "APPROVED"
                              else MissionState.AWAITING_APPROVAL)
                    updated = self.runtime.transition(mission_id, target, reason="operator_resume")
                elif action == "cancel":
                    updated = self.runtime.transition(mission_id, MissionState.CANCELLED, reason="operator_cancel")
                else:
                    raise ApiError(400, "UNKNOWN_ACTION", "unknown lifecycle action", {"action": action})
            except StateTransitionInvalid as e:
                raise ApiError(409, "STATE_TRANSITION_INVALID", str(e), {}) from e
            result = {"envelope": self._envelope(self._get(mission_id)), "action": action}
            self._idem_store(idempotency_key, request_hash, result)
            return result

    def pause(self, mission_id: str, *, idempotency_key: str, expected_version: int | None = None):
        return self._lifecycle(mission_id, action="pause", idempotency_key=idempotency_key, expected_version=expected_version)

    def resume(self, mission_id: str, *, idempotency_key: str, expected_version: int | None = None):
        return self._lifecycle(mission_id, action="resume", idempotency_key=idempotency_key, expected_version=expected_version)

    def cancel(self, mission_id: str, *, idempotency_key: str, expected_version: int | None = None):
        return self._lifecycle(mission_id, action="cancel", idempotency_key=idempotency_key, expected_version=expected_version)

    def _events_impl(self, mission_id: str) -> dict[str, Any]:
        mission = self._get(mission_id)
        self._assert_not_corrupted(mission_id)
        evs = [e.to_dict() for e in self.runtime.store.list_events(mission_id)]
        return {"envelope": self._envelope(mission), "events": evs, "count": len(evs)}

    def _events_page(self, mission_id: str, *, after_sequence: int,
                     limit: int = 500) -> dict[str, Any]:
        """Read-only page of events strictly after a verified cursor."""
        self._assert_not_corrupted(mission_id)
        mission = self._get(mission_id)
        rows = [e for e in self.runtime.store.list_events(mission_id)
                if (e.sequence or 0) > after_sequence]
        page = rows[:limit]
        tail = rows[-1].sequence if rows else after_sequence
        return {"envelope": self._envelope(mission),
                "events": [e.to_dict() for e in page],
                "count": len(page),
                "tail_sequence": tail,
                "has_more": len(rows) > len(page)}

    def resolve_sse_cursor(self, mission_id: str, cursor: str | None) -> int:
        """Validate a resume cursor against the verified chain. Fail closed."""
        self._get(mission_id)  # 404 if unknown mission
        verdict = self.runtime.store.verify_mission_chain(mission_id)
        if not verdict["ok"]:
            raise ApiError(409, "STREAM_CORRUPTED",
                           "mission event stream failed integrity verification",
                           {"mission_id": mission_id})
        tail = verdict["tail_sequence"] or 0
        if cursor is None or cursor == "":
            return 0
        try:
            seq = int(cursor)
        except (TypeError, ValueError):
            raise ApiError(409, "UNKNOWN_CURSOR",
                           "cursor is not a valid event sequence", {"cursor": str(cursor)[:80]})
        if seq < 0:
            raise ApiError(409, "UNKNOWN_CURSOR", "cursor precedes the stream",
                           {"cursor": seq})
        if seq > tail:
            raise ApiError(409, "CURSOR_BEYOND_TAIL",
                           "cursor is beyond the committed tail",
                           {"cursor": seq, "tail": tail})
        if seq > 0:
            row = self.runtime.store._c().execute(
                "SELECT 1 FROM mission_events WHERE mission_id=? AND sequence=?",
                (mission_id, seq),
            ).fetchone()
            if row is None:
                raise ApiError(409, "UNKNOWN_CURSOR",
                               "cursor does not match a committed event for this mission",
                               {"cursor": seq})
        return seq

    def _receipts_impl(self, mission_id: str) -> dict[str, Any]:
        mission = self._get(mission_id)
        self._assert_not_corrupted(mission_id)
        receipts = [r.to_dict() for r in self.runtime.store.list_receipts(mission_id)]
        approvals = [a.to_dict() for a in self.runtime.store.list_approvals(mission_id)]
        checkpoints = [c.to_dict() for c in self.runtime.store.list_checkpoints(mission_id)]
        return {"envelope": self._envelope(mission), "receipts": receipts,
                "approvals": approvals, "checkpoints": checkpoints}

    def _recover_impl(self, mission_id: str) -> dict[str, Any]:
        mission = self._get(mission_id)
        self._assert_not_corrupted(mission_id)
        result = self.runtime.recover(mission_id)
        return {"envelope": self._envelope(self._get(mission_id)), "recovery": result}

    def _replay_impl(self, mission_id: str) -> dict[str, Any]:
        mission = self._get(mission_id)
        self._assert_not_corrupted(mission_id)
        return {"envelope": self._envelope(mission), "replay": self.runtime.replay(mission_id)}
