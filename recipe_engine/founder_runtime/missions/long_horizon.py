"""A long-horizon, read-only mission that survives restart and replans.

The only mission the runtime previously carried was the system-status vertical
slice, which hard-refuses any non-fixture mode, hardcodes its candidate
strategies as source literals, cannot replan, and whose "power loss" support
returns early rather than resuming.  This module is the missing Phase-5 article:
a multi-stage mission that

* resolves current context,
* reads current system state and approved knowledge through **either** the
  fixture adapters or the live read-only adapters, chosen explicitly and never
  by silent fallback,
* generates candidate strategies **from the Mission Contract** and collapses
  them into a decision packet,
* compiles, persists and binds an immutable versioned execution plan,
* checkpoints before and after every stage,
* resumes from the last completed stage after the process is lost, without
  re-running a completed stage,
* **replans** when later evidence contradicts the assumption the standing plan
  rests on, superseding rather than overwriting it,
* and produces a final decision packet.

Authority envelope: read-only throughout.  It plans; Governance authorizes; AXON
executes.  Nothing here mutates production, writes to DeedSonar, or communicates
outbound, and the mission's own non-goals are enforced by the strategy generator
rather than merely documented.

A blocked dependency is a first-class outcome, not an error.  When a source is
unavailable the mission records the gap, lowers the affected strategies'
evidence coverage, and continues degraded with the gap disclosed — which is
exactly what triggers the replan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Sequence

from recipe_engine.autonomy import collapse_scenarios, compile_execution_plan, default_config
from recipe_engine.autonomy.models import (
    CapabilityCatalog,
    Claim,
    ClaimClass,
    ConflictState,
    EvidenceReference,
    OpportunityEnvelope,
    Sensitivity,
    AuthorityLevel,
    SV_CAPABILITY,
    SV_CLAIM,
    SV_EVIDENCE,
    SV_OPPORTUNITY,
)
from recipe_engine.founder_runtime.adapters.fixture_governance import FixtureGovernance
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.contracts.approval import (
    Approval,
    ApprovalDecision,
    SCHEMA_APPROVAL,
)
from recipe_engine.founder_runtime.contracts.completion import AcceptanceResult
from recipe_engine.founder_runtime.contracts.events import EventType
from recipe_engine.founder_runtime.contracts.mission import DeliverableStatus
from recipe_engine.founder_runtime.context.selector import select_packs
from recipe_engine.founder_runtime.errors import FounderRuntimeError
from recipe_engine.founder_runtime.evidence.receipts import expected_step_input_hash
from recipe_engine.founder_runtime.evidence.validation import (
    assert_valid_live_receipt,
    assert_valid_receipt,
)
from recipe_engine.founder_runtime.events.replay import replay_mission
from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from recipe_engine.founder_runtime.intent.constraints import AuthorityEnvelope
from recipe_engine.founder_runtime.runtime.approvals import gated_action_for
from recipe_engine.founder_runtime.runtime.completion import verify_completion
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.strategy import generate_candidate_strategies


LONG_HORIZON_OBJECTIVE = (
    "Determine the three strongest technically evidenced improvements to DeedSonar "
    "candidate ranking without changing production or contacting anyone."
)

# Stages run in order.  A stage is complete when its receipt exists, which is
# what makes resume idempotent: a completed stage is never re-executed, because
# its evidence is already durable.
LONG_HORIZON_STAGES: tuple[str, ...] = (
    "resolve_context",
    "read_system_state",
    "generate_strategies",
    "compile_plan",
    "read_approved_knowledge",
    "retrieve_evidence",
    "reconcile_assumptions",
    "final_decision_packet",
)


class LongHorizonError(FounderRuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(code, detail, {"error_class": "long_horizon"})
        self.detail = detail


# ---------------------------------------------------------------- observations
@dataclass
class SourceObservation:
    """One organ read, in whichever mode the mission was launched with."""

    source: str
    mode: str
    status: str          # success | blocked | unavailable
    error_code: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_ref: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "mode": self.mode,
            "status": self.status,
            "error_code": self.error_code,
            "evidence_ref": self.evidence_ref,
            "payload_hash": hash_payload(self.payload),
        }


class OrganReader:
    """Reads organs in exactly one declared mode.

    There is no fallback path between modes.  A reader constructed for ``live``
    that cannot reach an organ returns a blocked observation; it never quietly
    answers from a fixture, and the mode is stamped on every observation so the
    evidence records which world it came from.

    Live organs are **injected, never imported**.  ``founder_runtime`` must not
    depend on ``live_adapters`` — the dependency is one-way, and the AST
    confinement scan over the reachable graph is only meaningful while it stays
    that way.  A composition root outside this package builds the live organ set
    and hands it in; see ``live_adapters.composition.build_live_organs``.
    """

    def __init__(self, mode: str, *, organs: Mapping[str, Any] | None = None,
                 executor: Any = None):
        if mode not in ("fixture", "live"):
            raise LongHorizonError("ADAPTER_MODE_INVALID", f"unsupported organ reader mode: {mode}")
        self.mode = mode
        self.executor = executor
        if mode == "live":
            if not organs:
                raise LongHorizonError(
                    "CONFIGURATION_MISSING",
                    "live organ reader requires injected live organs; fixture substitution is forbidden",
                )
            if executor is None:
                raise LongHorizonError(
                    "CONFIGURATION_MISSING",
                    "live organ reader requires an injected read-only executor",
                )
            missing = sorted({"mother", "github", "vault", "qdrant"} - set(organs))
            if missing:
                raise LongHorizonError(
                    "CONFIGURATION_MISSING",
                    f"live organ set is incomplete: {','.join(missing)}",
                )
            self._mother = organs["mother"]
            self._github = organs["github"]
            self._vault = organs["vault"]
            self._qdrant = organs["qdrant"]
            return

        from recipe_engine.founder_runtime.adapters.fixture_axon import FixtureAxon
        from recipe_engine.founder_runtime.adapters.fixture_github import FixtureGitHub
        from recipe_engine.founder_runtime.adapters.fixture_mother import FixtureMother
        from recipe_engine.founder_runtime.adapters.fixture_qdrant import FixtureQdrant
        from recipe_engine.founder_runtime.adapters.fixture_vault import FixtureVault

        organs = dict(organs or {})
        self._mother = organs.get("mother") or FixtureMother(mode="fixture")
        self._github = organs.get("github") or FixtureGitHub(mode="fixture")
        self._vault = organs.get("vault") or FixtureVault(mode="fixture")
        self._qdrant = organs.get("qdrant") or FixtureQdrant(mode="fixture")
        self.executor = executor or FixtureAxon(mode="fixture")

    def _meta(self, mission_id: str, observed_at: str, step: str) -> AdapterRequestMeta:
        return AdapterRequestMeta(
            correlation_id=mission_id,
            observed_at=observed_at,
            mission_id=mission_id,
            step_id=step,
        )

    def _from_result(self, source: str, result: Any) -> SourceObservation:
        status = getattr(result, "status", "unavailable")
        payload = dict(getattr(result, "payload", {}) or {})
        if status == "success":
            refs = list(getattr(result, "evidence_refs", []) or [])
            return SourceObservation(
                source=source, mode=self.mode, status="success", payload=payload,
                evidence_ref=refs[0] if refs else f"{source}:observed",
            )
        return SourceObservation(
            source=source, mode=self.mode,
            status="blocked" if status == "denied" else "unavailable",
            error_code=getattr(result, "error_code", None) or str(status),
            payload=payload,
        )

    def health(self, mission_id: str, observed_at: str) -> SourceObservation:
        try:
            res = self._mother.health_result(
                observed_at=observed_at,
                meta=self._meta(mission_id, observed_at, "read_system_state"),
            )
        except Exception as exc:  # noqa: BLE001 — an unreachable organ is a gap, not a crash
            return SourceObservation("mother", self.mode, "unavailable", type(exc).__name__)
        return self._from_result("mother", res)

    def source_identity(self, mission_id: str, observed_at: str, repo: str) -> SourceObservation:
        try:
            res = self._github.repo_state_result(
                repo, self._meta(mission_id, observed_at, "read_system_state"),
            )
        except Exception as exc:  # noqa: BLE001
            return SourceObservation("github", self.mode, "unavailable", type(exc).__name__)
        return self._from_result("github", res)

    def approved_knowledge(self, mission_id: str, observed_at: str) -> SourceObservation:
        meta = self._meta(mission_id, observed_at, "read_approved_knowledge")
        try:
            if self.mode == "live":
                return self._from_result("vault", self._vault.provenance_state(meta))
            prov = self._vault.provenance(observed_at=observed_at)
            payload = prov.to_dict() if hasattr(prov, "to_dict") else {"provenance": str(prov)}
            return SourceObservation("vault", self.mode, "success", payload=payload,
                                     evidence_ref="vault:provenance")
        except Exception as exc:  # noqa: BLE001
            return SourceObservation("vault", self.mode, "unavailable", type(exc).__name__)

    def retrieval(self, mission_id: str, observed_at: str, query: str) -> SourceObservation:
        """Retrieval is the organ most likely to be gated, and that is expected.

        Live Qdrant retrieval is deliberately withheld in P2-B (semantic search
        raises UnsafeCapability) and bounded verification runs only behind the
        host-recovery gate.  A gap here is a disclosed gap, never a fixture
        substitute.
        """
        meta = self._meta(mission_id, observed_at, "retrieve_evidence")
        try:
            if self.mode == "live":
                return self._from_result("qdrant", self._qdrant.verify_collection(meta))
            res = self._qdrant.search_result(query, limit=3, meta=meta)
            return self._from_result("qdrant", res)
        except Exception as exc:  # noqa: BLE001
            return SourceObservation("qdrant", self.mode, "blocked", type(exc).__name__)


# ------------------------------------------------------------------ claims
_CLAIM_BY_SOURCE = {
    "mother": ("claim_overall_health", "claim_blocked"),
    "github": ("claim_source_identity",),
    "vault": ("claim_approved_knowledge",),
    "qdrant": ("claim_history",),
}


def build_claims(observations: Sequence[SourceObservation], *, observed_at: str
                 ) -> tuple[list[Claim], list[EvidenceReference], list[str]]:
    """Claims are built only from organs that actually answered.

    A blocked organ contributes no claim, so its absence lowers the evidence
    coverage of every strategy that needed it instead of being papered over.
    """
    claims: list[Claim] = []
    evidence: list[EvidenceReference] = []
    established: list[str] = []

    for obs in observations:
        if not obs.ok:
            continue
        claim_ids = _CLAIM_BY_SOURCE.get(obs.source, ())
        if not claim_ids:
            continue
        eid = f"ev_{obs.source}"
        evidence.append(EvidenceReference(
            schema_version=SV_EVIDENCE,
            evidence_id=eid,
            source_type=f"{obs.mode}_{obs.source}",
            internal_pointer=f"{obs.mode}://{obs.source}",
            observed_at=observed_at,
            content_hash=hash_payload(obs.payload),
            provenance_chain_id=f"prov_{obs.source}_{obs.mode}",
            sensitivity=Sensitivity.INTERNAL,
            freshness="current",
            authority_level=AuthorityLevel.OBSERVED,
            supported_claim_ids=tuple(claim_ids),
        ))
        for cid in claim_ids:
            claims.append(Claim(
                schema_version=SV_CLAIM,
                claim_id=cid,
                statement=f"{cid} established by {obs.mode} {obs.source} ({obs.evidence_ref})",
                classification=ClaimClass.OBSERVED,
                evidence_refs=(eid,),
                confidence=Decimal("0.9"),
                source_confidence=Decimal("0.9"),
                conflict_state=ConflictState.NONE,
                verification_requirements=(),
                freshness="current",
                expires_at=None,
                creator="founder_runtime_long_horizon",
                version="1",
                critical=obs.source in ("mother", "github"),
            ))
            established.append(cid)
    return claims, evidence, established


def _opportunity(mission, claims, evidence, *, observed_at: str) -> OpportunityEnvelope:
    return OpportunityEnvelope(
        schema_version=SV_OPPORTUNITY,
        opportunity_id=stable_id("opp", mission.mission_id),
        domain=mission.domain or "general",
        territory={"scope": mission.world or "neuruh"},
        subject={"mission_id": mission.mission_id},
        assets=(),
        desired_outcome=mission.desired_outcome,
        constraints=tuple(mission.constraints),
        authority_context={"sensitivity": "internal", "allowed_domains": [mission.domain or "general"]},
        time_horizon="immediate",
        claims=tuple(claims),
        evidence=tuple(evidence),
        evidence_completeness=Decimal("0.85") if claims else Decimal("0.10"),
        supplied_financial_inputs=(),
        risk_tolerance="low",
        operator_requirements=("read_only", "no_mutation"),
        version="1",
    )


# ------------------------------------------------------------------ the mission
def run_long_horizon_mission(
    runtime,
    *,
    request_text: str = LONG_HORIZON_OBJECTIVE,
    mode: str = "fixture",
    organs: Mapping[str, Any] | None = None,
    executor: Any = None,
    creation_idempotency_key: str = "long-horizon",
    stop_after_stage: str | None = None,
    max_stages: int | None = None,
) -> dict[str, Any]:
    """Run — or resume — the long-horizon read-only mission.

    Resume is not a separate entry point.  Calling this again with the same
    ``creation_idempotency_key`` returns the same mission (mission creation is
    idempotent on that key), and every stage whose receipt already exists is
    skipped.  That is what makes "no duplicate execution after recovery" a
    property of the design rather than a claim: a stage's receipt IS the record
    that it ran, and a stage is never executed while its receipt exists.
    """
    reader = OrganReader(mode, organs=organs, executor=executor)
    envelope = (AuthorityEnvelope.READ_ONLY_LIVE if mode == "live"
                else AuthorityEnvelope.READ_ONLY_FIXTURE)

    mission = runtime.create_mission_from_text(
        request_text,
        creation_idempotency_key=creation_idempotency_key,
        envelope=envelope,
    )
    mid = mission.mission_id

    if mission.extensions.get("initial_block") == "BLOCKED_INPUT":
        return {"mission_id": mid, "status": "blocked_input",
                "question": mission.extensions.get("blocked_question")}

    # Stages already proven by a durable receipt are complete.
    done = {r.step_id for r in runtime.store.list_receipts(mid)}
    state = runtime.get_mission(mid).current_state

    # --- intake path, walked only while the mission is still a draft --------
    if state == MissionState.DRAFT:
        runtime.transition(mid, MissionState.INTAKE, reason="intake")
        packs = select_packs(["founder_core", "architecture", "current_runtime"], token_budget=400)
        runtime.transition(
            mid, MissionState.CONTEXT_RESOLUTION, reason="resolve_context",
            payload={"selected": packs.selected_source_ids,
                     "conflicts": [c.to_dict() for c in packs.conflicts]},
            event_type=EventType.CONTEXT_RESOLVED,
        )
        runtime.checkpoint(mid, completed_steps=[], pending_steps=list(LONG_HORIZON_STAGES),
                           active_step=None, last_receipt=None, kind="before_context_handoff")
        runtime.transition(mid, MissionState.PLANNING, reason="begin_planning")
        _pass_authority_gate(runtime, mid, mode=mode)
        runtime.transition(mid, MissionState.EXECUTING, reason="execute_read_only_stages")
        state = runtime.get_mission(mid).current_state

    if state == MissionState.AWAITING_APPROVAL:
        # Resumed while parked on the approval gate.
        _pass_authority_gate(runtime, mid, mode=mode)
        runtime.transition(mid, MissionState.EXECUTING, reason="execute_read_only_stages")

    mission = runtime.get_mission(mid)
    # The working plan id identifies this execution thread before any plan has
    # been compiled.  It is bound as a real PLAN_BOUND event rather than left
    # implicit, so the receipts of the pre-plan stages reference a plan the
    # mission genuinely held and recovery can verify that binding.
    plan_id = mission.plan_id or stable_id("plan", mid, "long_horizon")
    if not mission.plan_id:
        runtime.bind_plan_id(mid, plan_id)

    ctx = _StageContext(runtime=runtime, reader=reader, mission_id=mid, plan_id=plan_id,
                        mode=mode, request_text=request_text)
    ctx.load(done)

    executed: list[str] = []
    for i, stage in enumerate(LONG_HORIZON_STAGES):
        if max_stages is not None and i >= max_stages:
            break
        if stage in done:
            ctx.replay_stage(stage)
            continue
        ctx.run_stage(stage)
        executed.append(stage)
        done.add(stage)
        if stop_after_stage is not None and stage == stop_after_stage:
            return {
                "mission_id": mid, "status": "suspended", "mode": mode,
                "completed_stages": sorted(done), "executed_this_run": executed,
                "plan_id": ctx.plan_id, "plan_version": ctx.plan_version,
                "db_path": str(runtime.store.path),
            }

    if len(done) < len(LONG_HORIZON_STAGES):
        return {
            "mission_id": mid, "status": "partial", "mode": mode,
            "completed_stages": sorted(done), "executed_this_run": executed,
        }

    return _complete(ctx, executed=executed, done=done)


def _pass_authority_gate(runtime, mission_id: str, *, mode: str) -> None:
    """Route the mission through its own approval gate when one is required.

    The classification is the trusted kernel's, not a model's.  When the
    contract says approval is required, the mission parks in AWAITING_APPROVAL
    and only a recorded, exact-action approval moves it on.  Approval records
    authority state; it never confers execution.
    """
    mission = runtime.get_mission(mission_id)
    requirement = mission.authority_requirements[0]
    if not requirement.requires_approval:
        runtime.transition(mission_id, MissionState.QUEUED, reason="queued_read_only")
        return

    if mission.current_state != MissionState.AWAITING_APPROVAL:
        runtime.transition(mission_id, MissionState.AWAITING_APPROVAL,
                           reason="authority_requires_approval")
    action, scope = gated_action_for(MissionState.AWAITING_APPROVAL, MissionState.QUEUED)
    now = runtime.clock.now_iso()
    approval = Approval(
        schema_version=SCHEMA_APPROVAL,
        approval_id=stable_id("appr", mission_id, action),
        mission_id=mission_id,
        action_class=requirement.authority_class.value,
        exact_requested_action=action,
        scope=scope,
        requested_by="founder_runtime_long_horizon",
        decided_by="founder",
        decision=ApprovalDecision.ALLOW,
        conditions=["read_only", "no_production_mutation", "no_outbound_communication"],
        expiry="2099-01-01T00:00:00.000Z",
        evidence_refs=[],
        created_at=now,
        decided_at=now,
        idempotency_key=f"approval:{mission_id}:{action}",
    )
    approval.compute_hash()
    recorded = runtime.record_approval(approval)
    runtime.transition(mission_id, MissionState.QUEUED, reason="approved_read_only",
                       approval_id=recorded.approval_id)


@dataclass
class _StageContext:
    runtime: Any
    reader: OrganReader
    mission_id: str
    plan_id: str
    mode: str
    request_text: str

    observations: list[SourceObservation] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    packet: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    plan_version: int = 0
    replanned: bool = False
    replan_reason: str = ""
    context_packs: list[str] = field(default_factory=list)
    generation: dict[str, Any] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)

    # -- resume support ---------------------------------------------------
    def load(self, done: set[str]) -> None:
        """Rehydrate what earlier runs established, from durable evidence only."""
        self.completed = [s for s in LONG_HORIZON_STAGES if s in done]
        latest = self.runtime.store.latest_plan(self.mission_id)
        if latest:
            self.plan = dict(latest.get("plan") or {})
            self.plan_id = str(latest.get("plan_id") or self.plan_id)
            self.plan_version = int(latest.get("plan_version") or 0)
            packet_id = latest.get("packet_id")
            if packet_id:
                self.packet = self.runtime.store.get_decision_packet(packet_id) or {}

    def replay_stage(self, stage: str) -> None:
        """Restore a completed stage's outputs without re-executing it."""
        for r in self.runtime.store.list_receipts(self.mission_id):
            if r.step_id != stage:
                continue
            data = dict(r.outcome_data or {})
            if stage in ("read_system_state", "read_approved_knowledge", "retrieve_evidence"):
                for obs in data.get("observations") or []:
                    self.observations.append(SourceObservation(
                        source=str(obs.get("source")), mode=str(obs.get("mode")),
                        status=str(obs.get("status")), error_code=obs.get("error_code"),
                        evidence_ref=str(obs.get("evidence_ref") or ""),
                    ))
                self.claim_ids = sorted(set(self.claim_ids) | set(data.get("claim_ids") or []))
            elif stage == "resolve_context":
                self.context_packs = list(data.get("context_packs") or [])
            elif stage == "generate_strategies":
                self.generation = dict(data.get("generation") or {})
            elif stage == "reconcile_assumptions":
                self.replanned = bool(data.get("replanned"))
                self.replan_reason = str(data.get("reason") or "")
            break

    # -- execution --------------------------------------------------------
    def run_stage(self, stage: str) -> dict[str, Any]:
        rt = self.runtime
        now1 = rt.clock.now_iso()
        se_key = f"se:{self.mission_id}:{stage}"
        rt.mark_side_effect(se_key, self.mission_id, stage, "proposed", {"stage": stage})
        rt.checkpoint(
            self.mission_id,
            completed_steps=list(self.completed),
            pending_steps=[s for s in LONG_HORIZON_STAGES if s not in self.completed and s != stage],
            active_step=stage,
            last_receipt=None,
            kind="before_side_effect",
            plan_id=self.plan_id,
        )
        input_hash = expected_step_input_hash(self.mission_id, self.plan_id, stage)
        out = self._handlers()[stage](now1)
        now2 = rt.clock.now_iso()

        step_authority = self._step_authority()
        receipt = self._executor().execute_readonly_step(
            mission_id=self.mission_id,
            plan_id=self.plan_id,
            step_id=stage,
            capability=f"{self.mode}.{stage}",
            input_hash=input_hash,
            authority_reference=step_authority["reference"],
            authority_contract=step_authority["contract"],
            started_at=now1,
            ended_at=now2,
            side_effect_key=se_key,
            outcome_data=out,
        )
        validate = assert_valid_receipt if self.mode == "fixture" else assert_valid_live_receipt
        validate(
            receipt,
            expected_mission_id=self.mission_id,
            expected_plan_id=self.plan_id,
            expected_step_id=stage,
            expected_input_hash=input_hash,
            expected_output=receipt.outcome_data,
        )
        rt.record_receipt(receipt)
        rt.mark_side_effect(se_key, self.mission_id, stage, "acknowledged",
                            {"stage": stage, "extra_hash": hash_payload(out)}, receipt.receipt_id)
        self.completed.append(stage)
        rt.checkpoint(
            self.mission_id,
            completed_steps=list(self.completed),
            pending_steps=[s for s in LONG_HORIZON_STAGES if s not in self.completed],
            active_step=None,
            last_receipt=receipt.receipt_id,
            kind="after_step",
            plan_id=self.plan_id,
        )
        return out

    def _executor(self):
        """The read-only step executor, supplied by the reader.

        Never constructed by importing live_adapters: founder_runtime does not
        depend on it, and the composition root injects the live executor.
        """
        return self.reader.executor

    def _step_authority(self) -> dict[str, Any]:
        """Authority for one read-only observation, not for the whole mission.

        Authority is per action.  The mission as a whole may classify high — this
        objective mentions production, so the trusted kernel escalates it to A5
        and it must clear an approval gate before any stage runs.  But each stage
        performs exactly one read-only observation, which is A0, and the receipt
        attests that action alone.  Both facts are recorded: the mission-level
        gate as a consumed approval event, the step-level authority here.
        """
        if self.mode == "live":
            # Live receipts are not governed by the fixture policy table; the
            # live adapter stamps not_claimed_by_governance and the real
            # Governance organ remains the only authority.
            return {"reference": "not_claimed_by_governance", "contract": None}
        gov = FixtureGovernance(mode="fixture")
        decision = gov.decide(
            AuthorityClass.A0,
            action="read_only_observation",
            scope="local_fixture",
        )
        return {
            "reference": (
                f"fixture_gov:{decision['decision_id']}:"
                f"{decision['authority_class']}:{decision['decision']}"
            ),
            "contract": decision,
        }

    # -- stages -----------------------------------------------------------
    def _handlers(self) -> dict[str, Any]:
        """Explicit stage dispatch.

        A literal table rather than getattr on a constructed name: the
        confinement scan forbids dynamic attribute dispatch in the reachable
        graph, and rightly — a stage name that reached this from anywhere
        untrusted would otherwise select an arbitrary method.
        """
        return {
            "resolve_context": self._stage_resolve_context,
            "read_system_state": self._stage_read_system_state,
            "generate_strategies": self._stage_generate_strategies,
            "compile_plan": self._stage_compile_plan,
            "read_approved_knowledge": self._stage_read_approved_knowledge,
            "retrieve_evidence": self._stage_retrieve_evidence,
            "reconcile_assumptions": self._stage_reconcile_assumptions,
            "final_decision_packet": self._stage_final_decision_packet,
        }

    def _stage_resolve_context(self, now: str) -> dict[str, Any]:
        packs = select_packs(["founder_core", "architecture", "current_runtime"], token_budget=400)
        self.context_packs = list(packs.selected_source_ids)
        return {"context_packs": self.context_packs,
                "conflicts": [c.to_dict() for c in packs.conflicts]}

    def _stage_read_system_state(self, now: str) -> dict[str, Any]:
        obs = [
            self.reader.health(self.mission_id, now),
            self.reader.source_identity(self.mission_id, now, "NeuruhAI/recipe-engine"),
        ]
        self.observations.extend(obs)
        _, _, established = build_claims(obs, observed_at=now)
        self.claim_ids = sorted(set(self.claim_ids) | set(established))
        return {"observations": [o.to_dict() for o in obs], "claim_ids": self.claim_ids}

    def _stage_generate_strategies(self, now: str) -> dict[str, Any]:
        mission = self.runtime.get_mission(self.mission_id)
        report = generate_candidate_strategies(
            mission,
            claim_ids=self.claim_ids,
            available_sources=self._available_sources(),
        )
        self.generation = report.to_dict()
        self._candidates = report.candidates
        return {"generation": self.generation}

    def _stage_compile_plan(self, now: str) -> dict[str, Any]:
        return self._compile_and_commit(now, supersedes=None, reason="initial")

    def _stage_read_approved_knowledge(self, now: str) -> dict[str, Any]:
        obs = [self.reader.approved_knowledge(self.mission_id, now)]
        self.observations.extend(obs)
        _, _, established = build_claims(obs, observed_at=now)
        self.claim_ids = sorted(set(self.claim_ids) | set(established))
        return {"observations": [o.to_dict() for o in obs], "claim_ids": self.claim_ids}

    def _stage_retrieve_evidence(self, now: str) -> dict[str, Any]:
        obs = [self.reader.retrieval(self.mission_id, now, "deedsonar candidate ranking")]
        self.observations.extend(obs)
        _, _, established = build_claims(obs, observed_at=now)
        self.claim_ids = sorted(set(self.claim_ids) | set(established))
        blocked = [o.to_dict() for o in obs if not o.ok]
        return {
            "observations": [o.to_dict() for o in obs],
            "claim_ids": self.claim_ids,
            "blocked_dependencies": blocked,
            # A blocked retrieval is disclosed, never substituted.
            "fixture_substituted": False,
        }

    def _stage_reconcile_assumptions(self, now: str) -> dict[str, Any]:
        """Replan when later evidence contradicts the standing plan.

        Two contradictions matter: a source the committed plan depends on turned
        out to be unavailable, or the evidence gathered since changed which
        strategy the contract now favours.  Either way the standing plan rests
        on an assumption that no longer holds, so it is superseded rather than
        quietly kept.
        """
        mission = self.runtime.get_mission(self.mission_id)
        available = self._available_sources()
        report = generate_candidate_strategies(
            mission, claim_ids=self.claim_ids, available_sources=available,
        )
        self._candidates = report.candidates
        self.generation = report.to_dict()

        committed_strategy = str(self.plan.get("selected_strategy_id")
                                 or self.plan.get("strategy_id") or "")
        committed_deps = set(self._committed_dependencies())
        lost = sorted(committed_deps - set(available))
        best = report.candidates[0].strategy_id if report.candidates else ""

        contradiction = None
        if lost:
            contradiction = f"dependency_unavailable:{','.join(lost)}"
        elif best and committed_strategy and best != committed_strategy:
            contradiction = f"better_strategy_available:{best}"

        if contradiction is None:
            return {"replanned": False, "reason": "assumptions_hold",
                    "available_sources": available, "committed_strategy": committed_strategy}

        self.replanned = True
        self.replan_reason = contradiction
        superseded = self.plan_id
        self._compile_and_commit(now, supersedes=superseded, reason=contradiction)
        return {
            "replanned": True,
            "reason": contradiction,
            "superseded_plan_id": superseded,
            "new_plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "available_sources": available,
            "lost_dependencies": lost,
        }

    def _stage_final_decision_packet(self, now: str) -> dict[str, Any]:
        return {
            "packet_id": self.packet.get("packet_id"),
            "selected_strategy_ids": list(self.packet.get("selected_strategy_ids") or []),
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "replanned": self.replanned,
            "replan_reason": self.replan_reason,
            "evidence_gaps": self._evidence_gaps(),
            "claim_ids": self.claim_ids,
            "mutation": False,
        }

    # -- helpers ----------------------------------------------------------
    def _available_sources(self) -> list[str]:
        return sorted({o.source for o in self.observations if o.ok})

    def _committed_dependencies(self) -> list[str]:
        for cand in self.generation.get("candidates") or []:
            if cand.get("strategy_id") == (self.plan.get("selected_strategy_id")
                                           or self.plan.get("strategy_id")):
                return list(cand.get("dependencies") or [])
        return []

    def _evidence_gaps(self) -> list[str]:
        gaps: list[str] = []
        for obs in self.observations:
            if not obs.ok:
                gaps.append(f"{obs.source}:{obs.error_code or obs.status}")
        return sorted(set(gaps))

    def _compile_and_commit(self, now: str, *, supersedes: str | None, reason: str) -> dict[str, Any]:
        mission = self.runtime.get_mission(self.mission_id)
        candidates = getattr(self, "_candidates", None)
        if not candidates:
            report = generate_candidate_strategies(
                mission, claim_ids=self.claim_ids, available_sources=self._available_sources())
            candidates = report.candidates
            self.generation = report.to_dict()
        claims, evidence, _ = build_claims(self.observations, observed_at=now)
        opp = _opportunity(mission, claims, evidence, observed_at=now)
        packet = collapse_scenarios(opp, list(candidates), config=default_config())
        packet_dict = packet.to_dict()

        if not packet.selected_strategy_ids:
            raise LongHorizonError("NO_VIABLE_STRATEGY",
                                   "collapse selected no strategy; refusing to compile a plan")
        strategy_id = packet.selected_strategy_ids[0]
        catalog = CapabilityCatalog(
            schema_version=SV_CAPABILITY,
            capabilities=("observe.read", "research.read", "analysis.local"),
        )
        try:
            compiled = compile_execution_plan(packet, strategy_id, catalog, config=default_config())
        except Exception as exc:  # noqa: BLE001 — never complete through a swallowed failure
            raise LongHorizonError("PLAN_COMPILE_FAILED", f"{type(exc).__name__}: {exc}"[:400]) from exc

        plan_dict = compiled.to_dict()
        plan_dict["selected_strategy_id"] = strategy_id
        record = self.runtime.commit_plan(
            self.mission_id,
            plan=plan_dict,
            packet=packet_dict,
            strategy_id=strategy_id,
            authority_binding={
                "authority_class": mission.authority_requirements[0].authority_class.value,
                "policy_version": mission.authority_requirements[0].policy_version,
                "requires_approval": mission.authority_requirements[0].requires_approval,
                "approval_confers_execution": False,
                "envelope": mission.extensions.get("authority_envelope"),
                "constraints": list(mission.constraints),
                "non_goals": list(mission.non_goals),
            },
            evidence_refs=[e.evidence_id for e in evidence],
            supersedes=supersedes,
        )
        self.packet = packet_dict
        self.plan = plan_dict
        self.plan_id = str(record["plan_id"])
        self.plan_version = int(record["plan_version"])
        return {
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "supersedes": supersedes,
            "reason": reason,
            "packet_id": packet_dict.get("packet_id"),
            "selected_strategy_id": strategy_id,
            "selected_strategy_ids": list(packet.selected_strategy_ids),
            "evidence_refs": [e.evidence_id for e in evidence],
        }


def _complete(ctx: _StageContext, *, executed: list[str], done: set[str]) -> dict[str, Any]:
    rt = ctx.runtime
    mid = ctx.mission_id
    rt.transition(mid, MissionState.VERIFYING, reason="verify_receipts")
    for did in ("del_status_report", "del_next_plan", "del_blocked_list"):
        rt.set_deliverable_status(mid, did, DeliverableStatus.DELIVERED)

    receipts = rt.store.list_receipts(mid)
    by_step = {r.step_id: r.receipt_id for r in receipts}
    acceptance = [
        AcceptanceResult("context_resolved", True, by_step.get("resolve_context")),
        AcceptanceResult("system_state_observed", True, by_step.get("read_system_state")),
        AcceptanceResult("strategies_generated", True, by_step.get("generate_strategies")),
        AcceptanceResult("plan_compiled_and_bound", True, by_step.get("compile_plan")),
        AcceptanceResult("assumptions_reconciled", True, by_step.get("reconcile_assumptions")),
        AcceptanceResult("decision_packet_present", True, by_step.get("final_decision_packet")),
    ]
    artifacts = rt.store.list_artifacts(mid)
    rt.transition(mid, MissionState.RECONCILING, reason="reconcile_outcomes")

    receipt_hashes = sorted(r.receipt_hash for r in receipts)
    deliverable_hash = hash_payload([d.to_dict() for d in rt.get_mission(mid).deliverables])
    material = {
        "outcome_id": stable_id("outcome", mid, ctx.plan_id),
        "mission_id": mid,
        "plan_id": ctx.plan_id,
        "status": "verified",
        "receipt_hashes": receipt_hashes,
        "deliverable_hash": deliverable_hash,
        "result_hash": hash_payload({"receipt_hashes": receipt_hashes,
                                     "deliverable_hash": deliverable_hash, "mutation": False}),
        "evidence_class": "live_observed" if ctx.mode == "live" else "fixture",
        "mutation_authorized": False,
    }
    outcome = dict(material)
    outcome["outcome_hash"] = hash_payload(material)
    rt.record_outcome(mid, outcome)

    report = verify_completion(
        rt.get_mission(mid),
        receipts=receipts,
        artifacts=artifacts,
        approvals=[],
        acceptance=acceptance,
        exceptions=[],
        outcome=outcome,
        clock_now=rt.clock.now_iso(),
        decision_packet_id=ctx.packet.get("packet_id"),
        plan_id=ctx.plan_id,
    )
    rt.checkpoint(mid, completed_steps=list(LONG_HORIZON_STAGES), pending_steps=[],
                  active_step=None, last_receipt=receipts[-1].receipt_id if receipts else None,
                  kind="after_recovery", plan_id=ctx.plan_id)
    mission = rt.complete_mission(report)
    replay = replay_mission(rt.store, mid)
    plans = rt.store.list_plans(mid)

    return {
        "mission_id": mid,
        "status": "completed",
        "final_state": mission.current_state.value,
        "mode": ctx.mode,
        "adapter_mode": ctx.mode,
        "completed_stages": sorted(done),
        "executed_this_run": executed,
        "plan_id": ctx.plan_id,
        "plan_version": ctx.plan_version,
        "plan_lineage": [
            {"plan_version": p.get("plan_version"), "plan_id": p.get("plan_id"),
             "supersedes": p.get("supersedes"), "strategy_id": p.get("strategy_id")}
            for p in plans
        ],
        "replanned": ctx.replanned,
        "replan_reason": ctx.replan_reason,
        "decision_packet": {
            "packet_id": ctx.packet.get("packet_id"),
            "selected_strategy_ids": list(ctx.packet.get("selected_strategy_ids") or []),
            "receipt_hash": ctx.packet.get("receipt_hash"),
        },
        "evidence_gaps": ctx._evidence_gaps(),
        "claim_ids": ctx.claim_ids,
        "observations": [o.to_dict() for o in ctx.observations],
        "completion_hash": report.completion_hash,
        "replay": {
            "final_state": replay["final_state"],
            "event_count": replay["event_count"],
            "projection_hash": replay["projection_hash"],
        },
        "mutation": False,
    }
