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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from recipe_engine.autonomy import collapse_scenarios, compile_execution_plan, default_config
from recipe_engine.autonomy.models import (
    CandidateStrategy, CapabilityCatalog, MetricValue, OpportunityEnvelope,
    SV_CAPABILITY, SV_METRIC, SV_OPPORTUNITY, SV_STRATEGY,
)
from recipe_engine.founder_runtime import RUNTIME_VERSION
from recipe_engine.founder_runtime.adapters.fixture_governance import FixtureGovernance
from recipe_engine.founder_runtime.api.founder_signing import (
    APPROVAL_TTL_SECONDS,
    FounderApprovalSigner,
)
from recipe_engine.founder_runtime.adapters.fixture_mother import FixtureMother
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.clock import SystemClock
from recipe_engine.founder_runtime.contracts.approval import Approval, ApprovalDecision, SCHEMA_APPROVAL
from recipe_engine.founder_runtime.contracts.events import EventType
from recipe_engine.founder_runtime.contracts.mission import Mission
from recipe_engine.founder_runtime.contracts.work_spec import validate_work_spec_request
from recipe_engine.founder_runtime.errors import (
    ApprovalRequired, AuthorityDenied, ExecutionOutcomeMalformed, ExecutionOutcomeRejected,
    FounderRuntimeError, MissionNotFound,
    MissionSchemaInvalid, PersistenceConflict, StateTransitionInvalid,
)
from recipe_engine.founder_runtime.evidence.claims import build_claims_from_fixtures
from recipe_engine.founder_runtime.ids import hash_payload, new_uuid, stable_id
from recipe_engine.founder_runtime.missions.long_horizon import (
    GOV_APPROVAL_REF_PREFIX,
    governance_approval_from,
)

# Condition recorded on a gate ALLOW whose Governance artifact was minted late,
# naming the earlier artifact-less record of the same decision it binds.
GOV_ARTIFACT_LATE_BOUND_PREFIX = "governance_artifact_late_bound_for:"
from recipe_engine.founder_runtime.runtime import approval_resolver as resolver
from recipe_engine.founder_runtime.runtime.approvals import assert_approval_usable, gated_action_for
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.state.states import MissionState, TERMINAL_STATES
from recipe_engine.founder_runtime.state.transitions import is_transition_allowed

API_VERSION = "fr-api-v1"

# Where a paused mission came from, recorded on the pause event so resume can
# put it back rather than guess.
PAUSED_FROM_KEY = "paused_from"

# How long the resolver waits for Governance's answer. Clamped by the adapter
# to its policy ceiling; a timeout parks the mission (never a default-open).
RESOLVER_AUTHORIZE_TIMEOUT_MS = 8_000

# What the operator may do next, by mission state. Execution is never allowed.
_ALLOWED_NEXT = {
    "DRAFT": ["get", "cancel"],
    "INTAKE": ["get", "pause", "cancel"],
    "CONTEXT_RESOLUTION": ["get", "pause", "cancel"],
    "PLANNING": ["get", "pause", "cancel"],
    "AWAITING_DECISION": ["get", "decision_packet", "pause", "cancel"],
    "AWAITING_APPROVAL": ["get", "decision_packet", "plan", "approve", "reject", "resolve", "pause", "cancel"],
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
    # No "approve": BLOCKED_POLICY -> APPROVED_NO_EXECUTION is not an edge the
    # state machine has, so advertising it invited a call that wrote an approval
    # and then failed. No "pause" either: the only exits from a policy block are
    # the approval-gated ones and cancellation, and pause/resume was a way around
    # both. Its designed exit is a consumed approval back to AWAITING_APPROVAL.
    "BLOCKED_POLICY": ["get", "decision_packet", "plan", "reject", "cancel"],
}
_BLOCKED_ALWAYS = ["execute_plan", "external_action", "mutate_axon", "workers_dispatch", "production_persist"]


def _iso_plus_seconds(iso: str, seconds: int) -> str:
    """ISO-8601 UTC, `seconds` after `iso`, in the millisecond form Governance parses."""
    base = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (base + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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

    def __init__(self, db_path: str | Path, *, clock=None, signer=None, governance=None,
                 attested_source_sha: str = "", founder_grant: dict[str, Any] | None = None,
                 auto_resolve: bool = True):
        self.db_path = str(db_path)
        # The approval resolver's authority port: something with
        # ``authorize(facts, founder_approval=..., meta=..., grant=...)`` that
        # reaches Governance /v2/authorize. None means this process cannot
        # resolve anything on its own and says so; it never guesses.
        self.governance = governance
        # The git source SHA this runtime honestly attests to Governance. It is
        # supplied by the composition root (never derived in the kernel) and an
        # empty value leaves every mission parked with SOURCE_SHA_UNATTESTED.
        self.attested_source_sha = str(attested_source_sha or "")
        # An externally issued, founder-signed gov.grant.v1, submitted verbatim.
        # This process can present a grant; it cannot mint one.
        self.founder_grant = dict(founder_grant) if founder_grant else None
        self.auto_resolve = bool(auto_resolve)
        self.clock = clock or SystemClock()
        self._lock = threading.RLock()
        self.runtime = MissionRuntime(self.db_path, clock=self.clock, adapter_mode="fixture", check_same_thread=False)
        # The founder's signing key lives on this side of the boundary and only
        # this side. It is what turns a recorded decision into something a party
        # other than the beneficiary can verify. With none configured the channel
        # still records decisions honestly — it simply cannot mint a
        # Governance-verifiable artifact, and a live mission parks rather than
        # proceeding on an unsigned record.
        self.signer = signer if signer is not None else FounderApprovalSigner()
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
        dispatch_approval = self._dispatch_approval(mission, approvals)
        authority_state = self._authority_state(mission, approvals)
        execution_authorized = bool(
            mission.work_spec_sha256
            and mission.current_state == MissionState.QUEUED
            and dispatch_approval is not None
        )
        authority = {
            "requirements": [a.to_dict() for a in mission.authority_requirements],
            "latest_approval": latest_approval,
            "state": authority_state,
            "work_spec_sha256": mission.work_spec_sha256,
            # Compatibility field retained with the now-strict semantics.
            "work_spec_authorized": execution_authorized,
            "work_spec_execution_authorized": execution_authorized,
            "execution_authorized": execution_authorized,
            "dispatch_approval_id": (
                dispatch_approval.approval_id if execution_authorized else None
            ),
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
            "objective": mission.objective,
            "work_spec": mission.work_spec.to_dict() if mission.work_spec is not None else None,
            "work_spec_sha256": mission.work_spec_sha256,
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
        except MissionSchemaInvalid as e:
            raise ApiError(
                409,
                "INTEGRITY_FAILURE",
                "mission work specification failed integrity validation",
                {"mission_id": mission_id, **e.details},
            ) from e

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
    def _dispatch_approval(mission: Mission, approvals: list[Approval]) -> Approval | None:
        action, scope = gated_action_for(
            MissionState.AWAITING_APPROVAL,
            MissionState.QUEUED,
        )
        for approval in reversed(approvals):
            if approval.decision != ApprovalDecision.CONSUMED:
                continue
            recorded_hash = approval.approval_hash
            if not recorded_hash or approval.expected_hash() != recorded_hash:
                continue
            if approval.exact_requested_action != action or approval.scope != scope:
                continue
            if (
                mission.work_spec_sha256 is not None
                and resolver.bound_work_spec_sha256(approval) != mission.work_spec_sha256
            ):
                continue
            return approval
        return None

    @classmethod
    def _authority_state(cls, mission: Mission, approvals: list[Approval]) -> str:
        """Expose dispatch authority only after the exact gate was consumed.

        The mission's own state has the last word. A mission Governance blocked
        on policy is not approved, whatever approval history it carries — an
        earlier ALLOW must never make a blocked mission read as dispatchable to
        a consumer that keys on this field.
        """
        if mission.current_state == MissionState.BLOCKED_POLICY:
            return "BLOCKED"
        if cls._dispatch_approval(mission, approvals) is not None:
            if mission.current_state == MissionState.QUEUED:
                return "APPROVED"
            return "EXECUTION_APPROVED_NOT_DISPATCHABLE"
        for a in reversed(approvals):
            if a.decision == ApprovalDecision.DENY:
                return "REJECTED"
            if (
                a.decision == ApprovalDecision.ALLOW
                and a.exact_requested_action.startswith("plan_decision:")
                and mission.current_state == MissionState.APPROVED_NO_EXECUTION
            ):
                return "PLAN_APPROVED_NO_EXECUTION"
        return "PENDING"

    def _assert_not_corrupted(self, mission_id: str) -> None:
        """Fail closed if the mission's event chain fails integrity verification."""
        try:
            verdict = self.runtime.store.verify_mission_chain(mission_id)
            self.runtime.replay(mission_id)
        except Exception as exc:  # noqa: BLE001 — malformed persisted material is untrusted
            raise ApiError(
                409,
                "INTEGRITY_FAILURE",
                "mission projection does not match its authoritative event chain",
                {"mission_id": mission_id, "cause": type(exc).__name__},
            ) from exc
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

    def _reconstruct_creation_response(self, mission: Mission) -> dict[str, Any]:
        if mission.extensions.get("initial_block") == "BLOCKED_INPUT":
            body = self._envelope(self._get(mission.mission_id))
            body["blocked_question"] = mission.extensions.get("blocked_question")
            return {
                "envelope": body,
                "decision_packet": None,
                "proposed_plan": None,
                "idempotent_replay": True,
            }

        packet = None
        plan = None
        plan_id = None
        for event in self.runtime.store.list_events(mission.mission_id):
            if event.event_type != EventType.OUTCOME_RECORDED:
                continue
            if "packet" in event.payload:
                packet = event.payload.get("packet")
                plan = event.payload.get("plan")
                plan_id = event.payload.get("plan_id")
        if packet is None:
            raise ApiError(
                409,
                "IDEMPOTENCY_RECOVERY_INCOMPLETE",
                "mission creation exists but its decision packet is not committed",
                {"mission_id": mission.mission_id},
            )
        body = self._envelope(
            self._get(mission.mission_id),
            evidence_refs=[packet.get("packet_id") or ""],
        )
        return {
            "envelope": body,
            "decision_packet": packet,
            "proposed_plan": plan,
            "plan_id": plan_id,
            "authority_resolution": None,
            "idempotent_replay": True,
        }

    # ---- public operations ----
    def create_mission(self, *, objective: str, title: str = "", done_criteria: str = "",
                       work_spec: Mapping[str, Any] | None = None,
                       idempotency_key: str, expected_version: int | None = None) -> dict[str, Any]:
        try:
            normalized_work_spec = (
                validate_work_spec_request(work_spec) if work_spec is not None else None
            )
        except MissionSchemaInvalid as exc:
            raise ApiError(422, "WORK_SPEC_INVALID", exc.message, exc.details) from exc
        request_material: dict[str, Any] = {
            "objective": objective,
            "title": title,
            "done_criteria": done_criteria,
        }
        if normalized_work_spec is not None:
            request_material["work_spec"] = normalized_work_spec
        request_hash = hash_payload(request_material)
        cached = self._idem_lookup(idempotency_key, request_hash)
        if cached is not None:
            cached["idempotent_replay"] = True
            return cached
        with self._lock:
            existing = self.runtime.store.get_mission_by_creation_key(idempotency_key)
            request_text = objective if not done_criteria else f"{objective}\nDone when: {done_criteria}"
            try:
                mission = self.runtime.create_mission_from_text(
                    request_text,
                    creation_idempotency_key=idempotency_key,
                    work_spec=normalized_work_spec,
                )
            except PersistenceConflict as exc:
                raise ApiError(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "mission creation idempotency key was reused for different material",
                    {"key": idempotency_key},
                ) from exc
            mid = mission.mission_id
            result: dict[str, Any]
            if existing is not None:
                result = self._reconstruct_creation_response(mission)
            elif mission.extensions.get("initial_block") == "BLOCKED_INPUT":
                body = self._envelope(self._get(mid))
                body["blocked_question"] = mission.extensions.get("blocked_question")
                result = {"envelope": body, "decision_packet": None, "proposed_plan": None}
            else:
                pipe = self._run_decision_pipeline(mid)
                # The mission is parked. Whether anyone is actually required to
                # un-park it is Governance's question, asked right now, once,
                # with this runtime's attested identity. A safe mission leaves
                # here QUEUED; a consequential one leaves with the exact
                # founder gate it is waiting on.
                resolution = None
                if self.auto_resolve:
                    resolution = self._resolve_impl(
                        mid, idempotency_key=f"resolve:{idempotency_key}",
                        expected_version=None, internal=True,
                    )["resolution"]
                body = self._envelope(self._get(mid), evidence_refs=[pipe["packet"].get("packet_id") or ""])
                result = {"envelope": body, "decision_packet": pipe["packet"],
                          "proposed_plan": pipe["plan"], "plan_id": pipe["plan_id"],
                          "authority_resolution": resolution}
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
        try:
            missions = self.runtime.store.list_missions()
        except MissionSchemaInvalid as exc:
            raise ApiError(
                409,
                "INTEGRITY_FAILURE",
                "a persisted mission failed work specification validation",
                exc.details,
            ) from exc
        for mission in missions:
            self._assert_not_corrupted(mission.mission_id)
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
            self._assert_not_corrupted(mission_id)
            request_material = {
                "decision": decision.value,
                "reason": reason,
                "mission_id": mission_id,
                "gate": gate,
            }
            if mission.work_spec_sha256 is not None:
                request_material["work_spec_sha256"] = mission.work_spec_sha256
            request_hash = hash_payload(request_material)
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
            same_decision: list[Approval] = []
            for existing in self.runtime.store.list_approvals(mission_id):
                if existing.decision not in (ApprovalDecision.ALLOW, ApprovalDecision.DENY):
                    continue
                if existing.scope != scope:
                    continue
                if (existing.decision == decision
                        and existing.exact_requested_action == requested_action):
                    same_decision.append(existing)
                    continue
                raise ApiError(409, "DECISION_CONFLICT",
                               "a terminal authority decision already exists for this "
                               "mission/plan/scope; conflicting decisions are refused",
                               {"existing_decision": existing.decision.value,
                                "approval_id": existing.approval_id})
            # Identical semantic replay: the decision stands, exactly once. The one
            # thing a repeated gate ALLOW may add is the Governance artifact that a
            # decision recorded BEFORE the mission published its authorization
            # request could never carry: Governance was not yet answering
            # founder_required for it (an expired ratification parks the mission
            # as unresolved and publishes nothing), so there was no scope_key to
            # bind a signature to, and the latch then replayed that artifact-less
            # record forever -- the founder's decision existed but could never be
            # presented. Late binding mints the missing artifact against the
            # request the mission has SINCE published, bound to the same scope,
            # source SHA and tier Governance named. The decision never changes and
            # is never stacked: with no publication, no signer, or an artifact
            # already bound, the existing record is returned exactly as before.
            late_bind_source: Approval | None = None
            if same_decision:
                bound = [a for a in same_decision if governance_approval_from(a) is not None]
                can_late_bind = (
                    not bound
                    and gate
                    and decision == ApprovalDecision.ALLOW
                    and self.signer.configured
                    and self.runtime.published_authorization_request(mission_id) is not None
                )
                if not can_late_bind:
                    return self._replay_decision(mission_id, bound[0] if bound else same_decision[0])
                late_bind_source = same_decision[0]
            if mission.current_state not in (MissionState.AWAITING_APPROVAL, MissionState.BLOCKED_POLICY):
                raise ApiError(409, "NOT_AWAITING_APPROVAL",
                               "mission is not awaiting an approval decision",
                               {"state": mission.current_state.value})
            # An ALLOW that is not a gate decision parks the plan in
            # APPROVED_NO_EXECUTION. Prove that edge exists BEFORE anything is
            # written: recording the approval first and discovering afterwards
            # that the transition is impossible left a terminal ALLOW on a
            # mission that could never consume it, and burned the
            # single-decision latch for the scope.
            if decision == ApprovalDecision.ALLOW and not gate:
                if not is_transition_allowed(mission.current_state, MissionState.APPROVED_NO_EXECUTION):
                    raise ApiError(409, "DECISION_NOT_APPLICABLE",
                                   "a plan approval cannot be applied from this state; "
                                   "no approval was recorded",
                                   {"state": mission.current_state.value,
                                    "would_require": "APPROVED_NO_EXECUTION"})
            now = self.clock.now_iso()
            classified = mission.authority_requirements[0] if mission.authority_requirements else None
            # A gate ALLOW is the founder's authority decision, so it carries the
            # artifact Governance can verify. It is bound to the scope and source
            # SHA the MISSION published — not to coordinates configured here —
            # so the two processes cannot drift into signing one thing and
            # presenting another. A DENY carries nothing: there is no such thing
            # as a signed refusal to spend, and minting one would only create an
            # artifact whose existence could be mistaken for permission.
            evidence_refs = [mission.plan_id or ""]
            if mission.work_spec_sha256 is not None:
                evidence_refs.append(
                    resolver.WORK_SPEC_SHA256_EVIDENCE_PREFIX + mission.work_spec_sha256,
                )
            signed_note = None
            if gate and decision == ApprovalDecision.ALLOW:
                signed_note = self._mint_gate_artifact(
                    mission_id,
                    action=requested_action,
                    scope=scope,
                    now=now,
                    work_spec_sha256=mission.work_spec_sha256,
                )
                if signed_note is not None:
                    evidence_refs.append(signed_note)
            conditions = [reason] if reason else []
            if late_bind_source is not None:
                if signed_note is None:
                    # The published request does not match this gate (or the signer
                    # refused): nothing new can be bound, so nothing new is recorded.
                    return self._replay_decision(mission_id, late_bind_source)
                conditions.append(GOV_ARTIFACT_LATE_BOUND_PREFIX + late_bind_source.approval_id)
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
                conditions=conditions,
                expiry=None,
                evidence_refs=evidence_refs,
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

    def _replay_decision(self, mission_id: str, existing: Approval) -> dict[str, Any]:
        """Return the terminal decision already recorded for this scope, unchanged."""
        body = self._envelope(self._get(mission_id), receipt_id=existing.approval_id,
                              evidence_refs=[existing.approval_id])
        return {"envelope": body, "approval": existing.to_dict(),
                "authority_state": existing.decision.value,
                "executed": False, "idempotent_replay": True,
                "note": "approval already recorded; plan NOT executed (no mutation authority in V1)"}

    def _mint_gate_artifact(
        self,
        mission_id: str,
        *,
        action: str,
        scope: str,
        now: str,
        work_spec_sha256: str | None,
    ) -> str | None:
        """The signed Governance approval for this mission's published request.

        Returns None — never a partial or improvised artifact — when the mission
        has not published a request yet, when the published request is about a
        different action or scope, or when this channel holds no signing key. In
        every one of those cases the decision is still recorded truthfully and
        the live mission simply parks, because an approval Governance cannot
        verify is not authority and must not be dressed up as one.
        """
        published = self.runtime.published_authorization_request(mission_id)
        if not published:
            return None
        if published.get("action") != action or published.get("scope") != scope:
            return None
        if published.get("work_spec_sha256") != work_spec_sha256:
            return None
        scope_key = published.get("scope_key")
        source_sha = published.get("source_sha")
        if not scope_key or not source_sha:
            return None
        if not self.signer.configured:
            return None
        expires_at = _iso_plus_seconds(now, APPROVAL_TTL_SECONDS)
        artifact = self.signer.sign_scope_once(
            scope_key=str(scope_key),
            source_sha=str(source_sha),
            issued_at=now,
            expires_at=expires_at,
            risk_tier=str(published.get("risk_tier") or "R0"),
        )
        return GOV_APPROVAL_REF_PREFIX + json.dumps(artifact, sort_keys=True, separators=(",", ":"))

    # ---- the approval resolver ------------------------------------------
    # A parked mission does not need a human by default; it needs an AUTHORITY.
    # Governance is that authority. The resolver presents the mission's
    # consequence (classified by the trusted kernel, never by a model), lets
    # Governance answer allow / founder_required / deny, and applies exactly
    # that answer through the kernel's own approval-gated transition. It holds
    # no signing key, writes no human decision, and lowers no class.
    def resolve_authority(self, mission_id: str, *, idempotency_key: str,
                          expected_version: int | None = None) -> dict[str, Any]:
        return self._resolve_impl(mission_id, idempotency_key=idempotency_key,
                                  expected_version=expected_version, internal=False)

    def _resolve_impl(self, mission_id: str, *, idempotency_key: str,
                      expected_version: int | None, internal: bool) -> dict[str, Any]:
        with self._lock:
            if not internal:
                self._require_expected_version(expected_version)
            mission = self._get(mission_id)
            self._assert_not_corrupted(mission_id)
            request_material = {"resolve": mission_id}
            if mission.work_spec_sha256 is not None:
                request_material["work_spec_sha256"] = mission.work_spec_sha256
            request_hash = hash_payload(request_material)
            cached = self._idem_lookup(idempotency_key, request_hash)
            if cached is not None:
                cached["idempotent_replay"] = True
                return cached
            if mission.current_state in TERMINAL_STATES:
                raise ApiError(409, "MISSION_TERMINAL", "mission is terminal", {"state": mission.current_state.value})
            if not internal:
                self._assert_version(mission, expected_version)
            if mission.current_state != MissionState.AWAITING_APPROVAL:
                raise ApiError(409, "NOT_AWAITING_APPROVAL",
                               "only a mission parked at AWAITING_APPROVAL can be resolved",
                               {"state": mission.current_state.value})
            if not mission.authority_requirements:
                raise ApiError(409, "AUTHORITY_UNCLASSIFIED",
                               "mission carries no trusted authority classification", {})
            requirement = mission.authority_requirements[0]
            action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
            resolution = self._resolve_with_governance(mission, requirement, action=action, scope=scope)
            body = self._envelope(self._get(mission_id), evidence_refs=list(resolution.get("evidence_refs") or []))
            result = {
                "envelope": body,
                "resolution": resolution,
                "authority_state": resolution["outcome"],
                "executed": False,
                "note": "authority resolved by Governance; nothing executed (no executor in this surface)",
            }
            self._idem_store(idempotency_key, request_hash, result)
            return result

    def _usable(self, approval: Approval, *, mission_id: str, action: str, scope: str, now: str) -> bool:
        try:
            assert_approval_usable(approval, mission_id=mission_id, action=action, scope=scope, now_iso=now)
        except FounderRuntimeError:
            return False
        return True

    def _resolve_with_governance(self, mission: Mission, requirement, *, action: str, scope: str) -> dict[str, Any]:
        mission_id = mission.mission_id
        now = self.clock.now_iso()
        approvals = self.runtime.store.list_approvals(mission_id)

        # 0. A refusal already recorded for THIS exact gate ends the question.
        #    A DENY is not merely an unusable approval to be skipped past: it is
        #    a decision, and the resolver is not entitled to reopen it. Asking
        #    Governance here would be worse than pointless — for a read-only
        #    mission Governance auto-allows, so the refusal would be overwritten
        #    by an allow the refuser never saw.
        for existing in approvals:
            if existing.decision != ApprovalDecision.DENY:
                continue
            if existing.mission_id != mission_id:
                continue
            if existing.exact_requested_action == action and existing.scope == scope:
                return self._resolution_dict(resolver.founder_denied(existing),
                                             facts=None, founder_approval=None)

        # 1. Preconditions, BEFORE anything can be spent. These were once below
        #    the replay path, which meant a leftover authorization could queue a
        #    mission in a process that had no authority port at all and could
        #    not have obtained one.
        if self.governance is None:
            return self._resolution_dict(
                resolver.unresolved("RESOLVER_GOVERNANCE_PORT_ABSENT",
                                    hint="run the API through the live composition root to attach Governance"),
                facts=None, founder_approval=None)
        if not self.attested_source_sha:
            return self._resolution_dict(
                resolver.unresolved("SOURCE_SHA_UNATTESTED",
                                    hint="the composition root must attest this runtime's git source SHA"),
                facts=None, founder_approval=None)

        # 2. A Governance-decided ALLOW this resolver recorded but never spent
        #    (a crash between record and transition). Governance already decided,
        #    so it is not asked twice — but the record only stands if every
        #    coordinate of that decision still holds: the exact mission, action
        #    and scope (assert_approval_usable), the exact consequence class the
        #    mission carries now, and the exact runtime version the decision was
        #    made about. An authorization is not a bearer token for whatever the
        #    process happens to be running later.
        for existing in approvals:
            if not resolver.is_governance_decided(existing):
                continue
            if not self._usable(existing, mission_id=mission_id, action=action, scope=scope, now=now):
                continue
            if existing.action_class != requirement.authority_class.value:
                continue
            if resolver.bound_source_sha(existing) != self.attested_source_sha:
                continue
            if (
                mission.work_spec_sha256 is not None
                and resolver.bound_work_spec_sha256(existing) != mission.work_spec_sha256
            ):
                continue
            self.runtime.transition(mission_id, MissionState.QUEUED, reason="approval_resolved_replay",
                                    actor=resolver.RESOLVER_ACTOR, approval_id=existing.approval_id)
            res = resolver.Resolution(outcome=resolver.RESOLVED_QUEUED, reason_code="RESOLVER_REPLAY",
                                      authorization_id=existing.decided_by.split(":", 1)[1],
                                      evidence_refs=list(existing.evidence_refs),
                                      detail={"spent_approval": existing.approval_id, "replayed": True,
                                              "revalidated": ["mission", "action", "scope", "expiry",
                                                              "unspent", "action_class", "source_sha",
                                                              "work_spec_sha256"]})
            return self._resolution_dict(res, facts=None, founder_approval=None)

        # 3. An externally decided ALLOW (the founder, over the gate route). It
        #    is never spent on its own say-so: its SIGNED artifact is presented
        #    to Governance, and only Governance's allow spends it.
        #    Of the usable human decisions, the one carrying a signed artifact is
        #    the one Governance can verify; an earlier artifact-less record of the
        #    same decision (recorded before the request was published) is not
        #    preferred merely for being first.
        external = None
        for existing in approvals:
            if resolver.is_governance_decided(existing):
                continue
            if not (
                self._usable(existing, mission_id=mission_id, action=action, scope=scope, now=now)
                and (
                    mission.work_spec_sha256 is None
                    or resolver.bound_work_spec_sha256(existing) == mission.work_spec_sha256
                )
            ):
                continue
            if external is None:
                external = existing
            if governance_approval_from(existing) is not None:
                external = existing
                break
        founder_approval = governance_approval_from(external) if external is not None else None

        # 4. The consequence, stated once, and Governance's answer to it.
        nonce = resolver.resolver_nonce(mission_id, now, attempt=str(len(approvals)))
        facts = resolver.resolver_facts(
            mission_id, requirement, action=action, scope=scope,
            source_sha=self.attested_source_sha, nonce=nonce, issued_at=now,
            work_spec_sha256=mission.work_spec_sha256,
        )
        meta = AdapterRequestMeta(correlation_id=mission_id, observed_at=now, mission_id=mission_id,
                                  step_id=resolver.RESOLVER_STEP, authority_class=requirement.authority_class,
                                  timeout_ms=RESOLVER_AUTHORIZE_TIMEOUT_MS)
        try:
            answer = self.governance.authorize(facts, founder_approval=founder_approval, meta=meta,
                                               grant=self.founder_grant)
        except Exception as exc:  # noqa: BLE001 — an unreachable authority parks the mission
            return self._resolution_dict(
                resolver.unresolved("GOVERNANCE_UNREACHABLE", cause=type(exc).__name__),
                facts=facts, founder_approval=founder_approval)
        if getattr(answer, "status", None) != "success":
            return self._resolution_dict(
                resolver.unresolved("GOVERNANCE_ADAPTER_" + str(getattr(answer, "error_code", None) or "FAILED"),
                                    adapter_status=str(getattr(answer, "status", "unknown"))),
                facts=facts, founder_approval=founder_approval)
        envelope = dict(getattr(answer, "payload", {}) or {})
        res = resolver.resolve_envelope(
            envelope, mission_id=mission_id, requirement=requirement, action=action, scope=scope,
            now_iso=now, external_approval_id=external.approval_id if external is not None else None,
            source_sha=self.attested_source_sha,
            work_spec_sha256=mission.work_spec_sha256,
        )

        # 4. Record Governance's answer on the mission's own chain, then apply
        #    exactly that answer through the kernel.
        self.runtime._record_same_state_event(
            mission_id, EventType.GOVERNANCE_RECORDED, f"approval_resolver:{res.outcome}",
            {"approval_resolver": {
                **res.to_dict(),
                "facts_hash": hash_payload(facts),
                "facts_nonce": nonce,
                "source_sha": self.attested_source_sha,
                "work_spec_sha256": mission.work_spec_sha256,
                "founder_approval_presented": founder_approval is not None,
                "grant_presented": self.founder_grant is not None,
                "decision_receipt_hash": envelope.get("decision_receipt_hash"),
            }},
            f"resolver:{mission_id}:{res.authorization_id or nonce}",
            evidence_refs=list(res.evidence_refs),
        )
        if res.outcome == resolver.RESOLVED_QUEUED:
            if external is not None:
                approval_id = external.approval_id
            else:
                approval_id = self.runtime.record_approval(res.approval).approval_id
            self.runtime.transition(mission_id, MissionState.QUEUED, reason="approval_resolved",
                                    actor=resolver.RESOLVER_ACTOR, approval_id=approval_id)
        elif res.outcome == resolver.FOUNDER_REQUIRED:
            # Publish what the founder's signature must bind to, unless the
            # mission already published its own request (a long-horizon
            # mission is its own publisher; never overwrite it).
            # An api-mode publication that names a source SHA this runtime no
            # longer attests (the supervised pin moved under a parked mission)
            # is stale: a signature bound to it can only ever be refused
            # APPROVAL_SOURCE_MISMATCH, so the request is published again for
            # the SHA that will actually present it.
            published = self.runtime.published_authorization_request(mission_id)
            stale_api_publication = (
                published is not None
                and published.get("mode") == "api"
                and published.get("source_sha") != self.attested_source_sha
            )
            if (published is None or stale_api_publication) and res.scope_key:
                self.runtime.record_authorization_request(mission_id, {
                    "mission_id": mission_id,
                    "action": action,
                    "scope": scope,
                    "source_sha": self.attested_source_sha,
                    "scope_key": res.scope_key,
                    "risk_tier": res.risk_tier or "",
                    "component": facts["component"],
                    "lane": facts["lane"],
                    "action_class": facts["action_class"],
                    "work_spec_sha256": mission.work_spec_sha256,
                    "mode": "api",
                })
        elif res.outcome == resolver.BLOCKED_POLICY:
            self.runtime.transition(mission_id, MissionState.BLOCKED_POLICY,
                                    reason=f"governance_deny:{res.reason_code}",
                                    actor=resolver.RESOLVER_ACTOR)
        return self._resolution_dict(res, facts=facts, founder_approval=founder_approval, envelope=envelope)

    def _resolution_dict(self, res, *, facts, founder_approval, envelope=None) -> dict[str, Any]:
        out = res.to_dict()
        out["facts_nonce"] = facts["nonce"] if facts else None
        out["facts_hash"] = hash_payload(facts) if facts else None
        out["source_sha"] = self.attested_source_sha or None
        out["founder_approval_presented"] = founder_approval is not None
        out["grant_presented"] = self.founder_grant is not None
        if envelope is not None:
            out["governance"] = {k: envelope.get(k) for k in (
                "authorization_id", "decision", "reason_code", "risk_tier", "risk_model_version",
                "decision_receipt_hash", "receipt_seq", "issued_at", "expires_at", "approver_ids",
            )}
        if res.outcome == resolver.FOUNDER_REQUIRED:
            out["founder_gate"] = {
                "approve_route": "/v1/missions/{mission_id}/gate/approve",
                "reject_route": "/v1/missions/{mission_id}/gate/reject",
                "resolve_route": "/v1/missions/{mission_id}/gate/resolve",
                "signs": "gov.founder.approval.v1 scope_once bound to the published scope_key + source_sha + risk_tier",
                "note": "a human decision is required for this consequence; the resolver will not manufacture one",
            }
        return out

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
                    if mission.current_state == MissionState.BLOCKED_POLICY:
                        # Pause/resume was a way out of a policy block that no
                        # approval had to pay for: BLOCKED_POLICY -> PAUSED ->
                        # AWAITING_APPROVAL is two legal edges, and only the
                        # direct one is approval-gated. The block is not a
                        # pausable condition; its exits are the gated ones.
                        raise ApiError(409, "BLOCKED_POLICY_NOT_PAUSABLE",
                                       "a policy-blocked mission cannot be paused; its exits are a "
                                       "consumed approval back to the gate, or cancellation",
                                       {"state": mission.current_state.value})
                    # Remember the state being paused, so resume restores the
                    # authority situation instead of inferring one.
                    updated = self.runtime.transition(
                        mission_id, MissionState.PAUSED, reason="operator_pause",
                        payload={PAUSED_FROM_KEY: mission.current_state.value})
                elif action == "resume":
                    mission = self._get(mission_id)
                    if mission.current_state != MissionState.PAUSED:
                        raise ApiError(409, "NOT_PAUSED", "only a paused mission can resume",
                                       {"state": mission.current_state.value})
                    # Resume restores the state that was paused when the kernel
                    # allows it. Inferring a target from authority history
                    # stranded a mission the resolver had legitimately QUEUED:
                    # it inferred APPROVED_NO_EXECUTION, which has no edge back
                    # to QUEUED, so authorized work could never run again.
                    target = self._resume_target(mission_id, mission)
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

    def _resume_target(self, mission_id: str, mission: Mission) -> MissionState:
        """Where a paused mission belongs: where it was, when that is legal."""
        for event in reversed(self.runtime.store.list_events(mission_id)):
            if event.next_state != MissionState.PAUSED:
                continue
            recorded = (event.payload or {}).get(PAUSED_FROM_KEY)
            if not recorded:
                break
            try:
                prior = MissionState(recorded)
            except ValueError:
                break
            if is_transition_allowed(MissionState.PAUSED, prior):
                return prior
            break
        # Missions paused before this runtime recorded the prior state, or
        # paused out of a state the kernel will not restore, fall back to the
        # authority-correct parking state.
        approvals = self.runtime.store.list_approvals(mission_id)
        return (MissionState.APPROVED_NO_EXECUTION
                if self._authority_state(mission, approvals) == "APPROVED"
                else MissionState.AWAITING_APPROVAL)

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
                "approvals": approvals, "checkpoints": checkpoints,
                # R02D: the sealed dispatch receipt / court records the runtime learned
                # from (fr.execution_outcome.v1). Distinct from fr.receipt.v1 step
                # receipts, which remain the completion verifier's evidence class.
                "execution": self.runtime.execution_projection(mission_id),
                "execution_outcomes": self.runtime.execution_outcomes(mission_id)}

    # ---- R02D: execution-outcome write-back (G-03) ----------------------------
    def record_execution_outcome(self, mission_id: str, *, body: Mapping[str, Any],
                                 idempotency_key: str, expected_version: int | None = None) -> dict[str, Any]:
        """Learn a Workers execution outcome from sealed evidence.

        Idempotent twice over: the Idempotency-Key ledger returns the stored
        response for an exact replay (and refuses the key over a different body),
        and the kernel itself treats already-learned evidence as a replay, so a
        restarted sender with a fresh key still applies nothing twice.
        X-Expected-Version is optional here: the envelope binds to the DISPATCHED
        mission version inside the evidence, which is the stronger claim, and the
        kernel's own CAS still guards the append.
        """
        with self._lock:
            mission = self._get(mission_id)
            self._assert_not_corrupted(mission_id)
            if not isinstance(body, Mapping):
                raise ApiError(400, "EXECUTION_OUTCOME_MALFORMED", "execution outcome body must be an object", {})
            try:
                request_hash = hash_payload({"route": "execution_outcome", "mission_id": mission_id,
                                             "body": dict(body)})
            except TypeError as exc:
                raise ApiError(400, "EXECUTION_OUTCOME_MALFORMED",
                               "execution outcome body is not canonical JSON", {"cause": str(exc)[:200]}) from exc
            cached = self._idem_lookup(idempotency_key, request_hash)
            if cached is not None:
                # An exact replay applies nothing NOW; the first response already
                # said what it appended. Report the replay honestly.
                cached["idempotent_replay"] = True
                cached["applied"] = False
                cached["events_appended"] = []
                return cached
            if expected_version is not None:
                self._assert_version(mission, expected_version)
            try:
                result = self.runtime.record_execution_outcome(mission_id, body)
            except ExecutionOutcomeMalformed as e:
                raise ApiError(400, "EXECUTION_OUTCOME_MALFORMED", e.message, dict(e.details)) from e
            except ExecutionOutcomeRejected as e:
                raise ApiError(409, e.reason_code, e.message, dict(e.details)) from e
            except MissionNotFound as e:
                raise ApiError(404, "MISSION_NOT_FOUND", "mission not found", {"mission_id": mission_id}) from e
            except StateTransitionInvalid as e:
                raise ApiError(409, "STATE_TRANSITION_INVALID", str(e), dict(e.details)) from e
            except PersistenceConflict as e:
                raise ApiError(409, "PERSISTENCE_CONFLICT", str(e), dict(e.details)) from e
            response = {
                "envelope": self._envelope(self._get(mission_id)),
                **result,
                "verified": False,
                "note": ("execution outcome recorded from sealed evidence; the mission is NOT verified or "
                         "completed by an execution or court outcome"),
            }
            self._idem_store(idempotency_key, request_hash, response)
            return response

    def _recover_impl(self, mission_id: str) -> dict[str, Any]:
        mission = self._get(mission_id)
        self._assert_not_corrupted(mission_id)
        result = self.runtime.recover(mission_id)
        return {"envelope": self._envelope(self._get(mission_id)), "recovery": result}

    def _replay_impl(self, mission_id: str) -> dict[str, Any]:
        mission = self._get(mission_id)
        self._assert_not_corrupted(mission_id)
        return {"envelope": self._envelope(mission), "replay": self.runtime.replay(mission_id)}
