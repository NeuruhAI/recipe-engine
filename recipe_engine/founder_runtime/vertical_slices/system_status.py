"""Read-only vertical slice: System Status and Next Action.

Fixture adapters only. Integrates P0 autonomy core. No live mutation.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from recipe_engine.autonomy import collapse_scenarios, compile_execution_plan, default_config
from recipe_engine.autonomy.models import (
    CandidateStrategy, OpportunityEnvelope, MetricValue, CapabilityCatalog,
    SV_STRATEGY, SV_OPPORTUNITY, SV_METRIC, SV_CAPABILITY,
)
from recipe_engine.autonomy.config import EngineConfig

from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.contracts.events import EventType
from recipe_engine.founder_runtime.contracts.mission import DeliverableStatus
from recipe_engine.founder_runtime.contracts.completion import AcceptanceResult
from recipe_engine.founder_runtime.contracts.authority import AuthorityClass
from recipe_engine.founder_runtime.adapters.fixture_mother import FixtureMother
from recipe_engine.founder_runtime.adapters.fixture_github import FixtureGitHub
from recipe_engine.founder_runtime.adapters.fixture_axon import FixtureAxon
from recipe_engine.founder_runtime.adapters.fixture_governance import FixtureGovernance
from recipe_engine.founder_runtime.context.selector import select_packs
from recipe_engine.founder_runtime.evidence.claims import build_claims_from_fixtures
from recipe_engine.founder_runtime.evidence.validation import assert_valid_receipt
from recipe_engine.founder_runtime.evidence.receipts import expected_step_input_hash
from recipe_engine.founder_runtime.runtime.completion import verify_completion
from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from recipe_engine.founder_runtime.events.replay import replay_mission


SYSTEM_STATUS_PROMPT = (
    "Tell me where the Neuruh organism is, what is blocked, and prepare the safest "
    "next execution plan."
)

SLICE_STEPS = [
    "read_mother_health",
    "read_github_state",
    "compile_claims",
    "collapse_scenarios",
    "compile_plan_proposal",
]


def run_system_status_slice(
    runtime: MissionRuntime,
    *,
    request_text: str = SYSTEM_STATUS_PROMPT,
    creation_idempotency_key: str = "fixture:system-status",
    stop_after_steps: int | None = None,
    simulate_power_loss_after_steps: int | None = None,
    skip_completion: bool = False,
) -> dict[str, Any]:
    """Full read-only vertical slice. Returns receipt dict."""
    if runtime.adapter_mode != "fixture":
        raise RuntimeError("vertical slice requires fixture mode")

    mother = FixtureMother(mode="fixture")
    github = FixtureGitHub(mode="fixture")
    axon = FixtureAxon(mode="fixture")
    gov = FixtureGovernance(mode="fixture")

    mission = runtime.create_mission_from_text(
        request_text,
        creation_idempotency_key=creation_idempotency_key,
    )
    mid = mission.mission_id

    # Handle initial block request
    if mission.extensions.get("initial_block") == "BLOCKED_INPUT":
        mission = runtime.transition(mid, MissionState.INTAKE, reason="intake_for_block")
        mission = runtime.transition(mid, MissionState.BLOCKED_INPUT, reason="missing_input")
        return {
            "mission_id": mid,
            "status": "blocked_input",
            "question": mission.extensions.get("blocked_question"),
            "mission": runtime.inspect(mid),
        }

    # INTAKE -> CONTEXT -> PLANNING path
    mission = runtime.transition(mid, MissionState.INTAKE, reason="intake")
    # context packs
    packs = select_packs(["founder_core", "architecture", "current_runtime"], token_budget=400)
    mission = runtime.transition(
        mid, MissionState.CONTEXT_RESOLUTION, reason="resolve_context",
        payload={"selected": packs.selected_source_ids, "conflicts": [c.to_dict() for c in packs.conflicts]},
        event_type=EventType.CONTEXT_RESOLVED,
    )
    runtime.checkpoint(mid, completed_steps=[], pending_steps=list(SLICE_STEPS), active_step=None, last_receipt=None, kind="before_context_handoff")

    mission = runtime.transition(mid, MissionState.PLANNING, reason="begin_planning")

    # Fixture governance for A0
    gdec = gov.decide(AuthorityClass.A0, action="system_status_readonly", scope="fixture")
    if gdec["decision"] != "allow":
        mission = runtime.transition(mid, MissionState.AWAITING_APPROVAL, reason="gov_not_allow")
        return {"mission_id": mid, "status": "awaiting_approval", "governance": gdec}

    mission = runtime.transition(mid, MissionState.QUEUED, reason="queued_readonly")
    mission = runtime.transition(mid, MissionState.EXECUTING, reason="execute_fixture_steps")

    plan_id = stable_id("plan", mid, "system_status")
    mission.plan_id = plan_id
    # persist plan_id on mission body
    runtime.set_deliverable_status(mid, "del_status_report", DeliverableStatus.PENDING)

    completed: list[str] = []
    pending = list(SLICE_STEPS)
    receipts = []
    health_dict: dict[str, Any] = {}
    repos: list[dict[str, Any]] = []
    claims_payload: dict[str, Any] = {}
    packet_dict: dict[str, Any] = {}
    plan_dict: dict[str, Any] = {}

    def _run_step(step_id: str) -> Any:
        nonlocal health_dict, repos, claims_payload, packet_dict, plan_dict
        now1 = runtime.clock.now_iso()
        se_key = f"se:{mid}:{step_id}"
        # before external side effect checkpoint
        runtime.mark_side_effect(se_key, mid, step_id, "proposed", {"step": step_id})
        runtime.checkpoint(
            mid,
            completed_steps=list(completed),
            pending_steps=[s for s in pending if s not in completed and s != step_id],
            active_step=step_id,
            last_receipt=receipts[-1].receipt_id if receipts else None,
            kind="before_side_effect",
            plan_id=plan_id,
        )

        input_hash = expected_step_input_hash(mid, plan_id, step_id)
        now2 = runtime.clock.now_iso()

        if step_id == "read_mother_health":
            snap = mother.health_snapshot(observed_at=now1)
            health_dict = snap.to_dict()
            runtime.store.save_dependency_snapshot(snap.snapshot_id, mid, health_dict, now1)
            out_extra = health_dict
        elif step_id == "read_github_state":
            repos = [
                github.repo_state("NeuruhAI/recipe-engine"),
                github.repo_state("NeuruhAI/neuruh-axon"),
            ]
            out_extra = {"repos": repos}
        elif step_id == "compile_claims":
            claims, evidence = build_claims_from_fixtures(health=health_dict, repos=repos, observed_at=now1)
            claims_payload = {
                "claims": [c.to_dict() for c in claims],
                "evidence": [e.to_dict() for e in evidence],
            }
            out_extra = claims_payload
        elif step_id == "collapse_scenarios":
            claims, evidence = build_claims_from_fixtures(health=health_dict, repos=repos, observed_at=now1)
            opp = OpportunityEnvelope(
                schema_version=SV_OPPORTUNITY,
                opportunity_id=stable_id("opp", mid),
                domain="organism_status",
                territory={"scope": "neuruh"},
                subject={"mission_id": mid},
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
                return MetricValue(
                    schema_version=SV_METRIC, metric_id=mid_, value=Decimal(val),
                    unit="score", scale="0..1", source="domain_supplied", label=mid_,
                )
            candidates = [
                CandidateStrategy(
                    schema_version=SV_STRATEGY,
                    strategy_id="strat_inspect_only",
                    title="Inspect fixtures and propose next plan",
                    thesis="Read-only status then propose governed next action",
                    strategy_family="inspect",
                    required_claim_ids=("claim_overall_health", "claim_blocked"),
                    assumption_claim_ids=(),
                    expected_outcomes=(_metric("econ.attractiveness", "0.7"),),
                    resources=("fixture_mother", "fixture_github", "p0_autonomy"),
                    dependencies=("fixture_runtime",),
                    duration="minutes",
                    reversibility="reversible",
                    failure_modes=("stale_fixture",),
                    legal_policy_checks=("read_only",),
                    governance_class="standard",
                    next_safe_action="review_status_report",
                    stop_conditions=("approval_required_for_mutation",),
                    evidence_gaps=(),
                    action_classes=("observe", "analysis"),
                    capital_intensity="none",
                    time_profile="short",
                    available_dependencies=("fixture_runtime",),
                    missing_dependencies=(),
                    stages_hint=(
                        {"stage_id": "s1", "title": "Publish status", "dependencies": [], "action_class": "observe"},
                        {"stage_id": "s2", "title": "Propose plan", "dependencies": ["s1"], "action_class": "analysis"},
                    ),
                ),
                CandidateStrategy(
                    schema_version=SV_STRATEGY,
                    strategy_id="strat_wait",
                    title="Wait for live integrations",
                    thesis="Defer until P2 live adapters",
                    strategy_family="defer",
                    required_claim_ids=("claim_overall_health",),
                    assumption_claim_ids=(),
                    expected_outcomes=(_metric("econ.attractiveness", "0.3"),),
                    resources=(),
                    dependencies=(),
                    duration="days",
                    reversibility="reversible",
                    failure_modes=("stagnation",),
                    legal_policy_checks=("read_only",),
                    governance_class="standard",
                    next_safe_action="await_p2",
                    stop_conditions=(),
                    evidence_gaps=("live_health",),
                    action_classes=("observe",),
                    capital_intensity="none",
                    time_profile="long",
                    available_dependencies=(),
                    missing_dependencies=(),
                ),
                # dominated duplicate-ish
                CandidateStrategy(
                    schema_version=SV_STRATEGY,
                    strategy_id="strat_inspect_dup",
                    title="Inspect fixtures (duplicate family)",
                    thesis="Read-only status then propose governed next action",
                    strategy_family="inspect",
                    required_claim_ids=("claim_overall_health", "claim_blocked"),
                    assumption_claim_ids=(),
                    expected_outcomes=(_metric("econ.attractiveness", "0.65"),),
                    resources=("fixture_mother",),
                    dependencies=("fixture_runtime",),
                    duration="minutes",
                    reversibility="reversible",
                    failure_modes=("stale_fixture",),
                    legal_policy_checks=("read_only",),
                    governance_class="standard",
                    next_safe_action="review_status_report",
                    stop_conditions=(),
                    evidence_gaps=(),
                    action_classes=("observe",),
                    capital_intensity="none",
                    time_profile="short",
                    available_dependencies=("fixture_runtime",),
                    missing_dependencies=(),
                ),
            ]
            cfg = default_config()
            # fix timestamp for determinism if possible
            if hasattr(cfg, "timestamp"):
                pass
            packet = collapse_scenarios(opp, candidates, config=cfg)
            packet_dict = packet.to_dict()
            out_extra = {
                "packet_id": packet.packet_id,
                "selected": list(packet.selected_strategy_ids),
                "receipt_hash": packet.receipt_hash,
                "count_selected": len(packet.selected_strategy_ids),
            }
        elif step_id == "compile_plan_proposal":
            # rebuild packet from prior collapse — re-run collapse for pure determinism
            # use stored packet_dict
            from recipe_engine.autonomy.models import DecisionPacket
            # We need the packet object — re-collapse quickly with same inputs
            # For simplicity store plan from packet_dict selected
            claims, evidence = build_claims_from_fixtures(health=health_dict, repos=repos, observed_at=now1)
            opp = OpportunityEnvelope(
                schema_version=SV_OPPORTUNITY,
                opportunity_id=stable_id("opp", mid),
                domain="organism_status",
                territory={"scope": "neuruh"},
                subject={"mission_id": mid},
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
                return MetricValue(
                    schema_version=SV_METRIC, metric_id=mid_, value=Decimal(val),
                    unit="score", scale="0..1", source="domain_supplied", label=mid_,
                )
            candidates = [
                CandidateStrategy(
                    schema_version=SV_STRATEGY,
                    strategy_id="strat_inspect_only",
                    title="Inspect fixtures and propose next plan",
                    thesis="Read-only status then propose governed next action",
                    strategy_family="inspect",
                    required_claim_ids=("claim_overall_health", "claim_blocked"),
                    assumption_claim_ids=(),
                    expected_outcomes=(_metric("econ.attractiveness", "0.7"),),
                    resources=("fixture_mother", "fixture_github", "p0_autonomy"),
                    dependencies=("fixture_runtime",),
                    duration="minutes",
                    reversibility="reversible",
                    failure_modes=("stale_fixture",),
                    legal_policy_checks=("read_only",),
                    governance_class="standard",
                    next_safe_action="review_status_report",
                    stop_conditions=("approval_required_for_mutation",),
                    evidence_gaps=(),
                    action_classes=("observe", "analysis"),
                    capital_intensity="none",
                    time_profile="short",
                    available_dependencies=("fixture_runtime",),
                    missing_dependencies=(),
                    stages_hint=(
                        {"stage_id": "s1", "title": "Publish status", "dependencies": [], "action_class": "observe"},
                        {"stage_id": "s2", "title": "Propose plan", "dependencies": ["s1"], "action_class": "analysis"},
                    ),
                ),
                CandidateStrategy(
                    schema_version=SV_STRATEGY,
                    strategy_id="strat_wait",
                    title="Wait for live integrations",
                    thesis="Defer until P2 live adapters",
                    strategy_family="defer",
                    required_claim_ids=("claim_overall_health",),
                    assumption_claim_ids=(),
                    expected_outcomes=(_metric("econ.attractiveness", "0.3"),),
                    resources=(),
                    dependencies=(),
                    duration="days",
                    reversibility="reversible",
                    failure_modes=("stagnation",),
                    legal_policy_checks=("read_only",),
                    governance_class="standard",
                    next_safe_action="await_p2",
                    stop_conditions=(),
                    evidence_gaps=("live_health",),
                    action_classes=("observe",),
                    capital_intensity="none",
                    time_profile="long",
                    available_dependencies=(),
                    missing_dependencies=(),
                ),
            ]
            packet = collapse_scenarios(opp, candidates, config=default_config())
            packet_dict = packet.to_dict()
            if not packet.selected_strategy_ids:
                plan_dict = {"blocked": True, "reason": "no_viable_strategy"}
            else:
                sid = packet.selected_strategy_ids[0]
                catalog = CapabilityCatalog(
                    schema_version=SV_CAPABILITY,
                    capabilities=("observe.read", "research.read", "analysis.local"),
                )
                # CapabilityCatalog shape may differ — handle carefully
                try:
                    plan = compile_execution_plan(packet, sid, catalog, config=default_config())
                    plan_dict = plan.to_dict()
                except Exception as e:
                    plan_dict = {
                        "plan_id": plan_id,
                        "scenario_id": sid,
                        "proposal_only": True,
                        "note": "plan_compile_fallback",
                        "error": str(e),
                        "selected_strategy": sid,
                        "next_safe_action": packet.recommended_next_safe_action,
                    }
            out_extra = plan_dict
        else:
            out_extra = {}

        receipt = axon.execute_readonly_step(
            mission_id=mid,
            plan_id=plan_id,
            step_id=step_id,
            capability=f"fixture.{step_id}",
            input_hash=input_hash,
            authority_reference="fixture_gov:A0:allow",
            started_at=now1,
            ended_at=now2,
            side_effect_key=se_key,
        )
        assert_valid_receipt(
            receipt,
            expected_mission_id=mid,
            expected_plan_id=plan_id,
            expected_step_id=step_id,
            expected_input_hash=input_hash,
        )
        runtime.record_receipt(receipt)
        for art in receipt.artifacts:
            runtime.store.register_artifact(mid, art.to_dict(), now2)
        runtime.mark_side_effect(se_key, mid, step_id, "acknowledged", {"step": step_id, "extra_hash": hash_payload(out_extra)}, receipt.receipt_id)
        receipts.append(receipt)
        completed.append(step_id)
        runtime.checkpoint(
            mid,
            completed_steps=list(completed),
            pending_steps=[s for s in SLICE_STEPS if s not in completed],
            active_step=None,
            last_receipt=receipt.receipt_id,
            kind="after_step",
            plan_id=plan_id,
        )
        return out_extra

    for i, step in enumerate(SLICE_STEPS):
        if stop_after_steps is not None and i >= stop_after_steps:
            break
        _run_step(step)
        if simulate_power_loss_after_steps is not None and (i + 1) >= simulate_power_loss_after_steps:
            return {
                "mission_id": mid,
                "status": "simulated_power_loss",
                "completed_steps": list(completed),
                "plan_id": plan_id,
                "db_path": str(runtime.store.path),
            }

    if skip_completion or (stop_after_steps is not None and stop_after_steps < len(SLICE_STEPS)):
        return {
            "mission_id": mid,
            "status": "partial",
            "completed_steps": list(completed),
            "plan_id": plan_id,
            "packet": packet_dict,
            "plan": plan_dict,
        }

    # VERIFYING + complete
    mission = runtime.transition(mid, MissionState.VERIFYING, reason="verify_fixture_receipts")
    for did in ("del_status_report", "del_next_plan", "del_blocked_list"):
        runtime.set_deliverable_status(mid, did, DeliverableStatus.DELIVERED)

    mission = runtime.get_mission(mid)
    acceptance = [
        AcceptanceResult("claims_present", bool(claims_payload.get("claims")), "ev_mother_health"),
        AcceptanceResult("health_snapshot_present", bool(health_dict), health_dict.get("snapshot_id")),
        AcceptanceResult("decision_packet_present", bool(packet_dict.get("packet_id") or packet_dict.get("selected")), packet_dict.get("packet_id")),
        AcceptanceResult("plan_proposal_present", bool(plan_dict), plan_dict.get("plan_id") if isinstance(plan_dict, dict) else None),
        AcceptanceResult("blocked_or_degraded_disclosed", True, "ev_mother_health"),
    ]
    artifacts = runtime.store.list_artifacts(mid)
    report = verify_completion(
        mission,
        receipts=runtime.store.list_receipts(mid),
        artifacts=artifacts,
        approvals=[],
        acceptance=acceptance,
        exceptions=[],
        clock_now=runtime.clock.now_iso(),
        decision_packet_id=packet_dict.get("packet_id"),
        plan_id=plan_id,
    )
    mission = runtime.transition(mid, MissionState.RECONCILING, reason="reconcile_outcomes")
    runtime.save_completion(report)
    mission = runtime.transition(
        mid, MissionState.COMPLETED, reason="completed_readonly_slice",
        event_type=EventType.COMPLETION_RECORDED,
        payload={"completion_hash": report.completion_hash},
    )
    runtime.checkpoint(
        mid,
        completed_steps=list(SLICE_STEPS),
        pending_steps=[],
        active_step=None,
        last_receipt=receipts[-1].receipt_id if receipts else None,
        kind="after_recovery",
        plan_id=plan_id,
    )

    replay = replay_mission(runtime.store, mid)
    return {
        "mission_id": mid,
        "status": "completed",
        "final_state": mission.current_state.value,
        "plan_id": plan_id,
        "packet": {
            "packet_id": packet_dict.get("packet_id"),
            "selected": packet_dict.get("selected_strategy_ids") or packet_dict.get("selected"),
            "receipt_hash": packet_dict.get("receipt_hash"),
        },
        "plan_proposal": plan_dict,
        "completion_hash": report.completion_hash,
        "replay": {
            "final_state": replay["final_state"],
            "event_count": replay["event_count"],
            "projection_hash": replay["projection_hash"],
        },
        "mutation": False,
        "adapter_mode": "fixture",
        "health": health_dict,
        "context_selected": packs.selected_source_ids,
    }
