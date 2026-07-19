"""Execution-plan proposal compiler. Never executes. Pure; no I/O."""

from __future__ import annotations

from .config import EngineConfig, validate_config
from .errors import PlanningError, ValidationError
from .hashing import content_hash, deterministic_id
from .models import (
    SV_PLAN,
    SV_STAGE,
    CapabilityCatalog,
    DecisionPacket,
    ExecutionPlanProposal,
    GovernanceProposalState,
    PlanStage,
    Readiness,
)

# Capability defaults for action classes
_ACTION_CAPS = {
    "research": ("research.read",),
    "analysis": ("analysis.local",),
    "financial_commit": ("finance.commit",),
    "payment": ("finance.payment",),
    "external_message": ("comms.outbound",),
    "email": ("comms.outbound",),
    "sms": ("comms.outbound",),
    "seller_contact": ("comms.outbound",),
    "irreversible_write": ("data.write.irreversible",),
    "write": ("data.write",),
    "observe": ("observe.read",),
}


def _toposort(stages: list[PlanStage]) -> list[PlanStage]:
    by_id = {s.stage_id: s for s in stages}
    pending = set(by_id)
    ordered: list[PlanStage] = []
    while pending:
        progress = False
        for sid in sorted(pending):
            deps = by_id[sid].dependencies
            if all(d not in pending for d in deps):
                if any(d not in by_id for d in deps):
                    raise PlanningError(f"unknown dependency for stage {sid}", {"deps": list(deps)})
                ordered.append(by_id[sid])
                pending.remove(sid)
                progress = True
        if not progress:
            raise PlanningError("dependency cycle detected", {"pending": sorted(pending)})
    return ordered


def compile_execution_plan(
    packet: DecisionPacket,
    scenario_id: str,
    capability_catalog: CapabilityCatalog,
    config: EngineConfig | None = None,
) -> ExecutionPlanProposal:
    cfg = validate_config(config or EngineConfig())
    if scenario_id not in packet.selected_strategy_ids:
        raise PlanningError(
            "scenario_id not in selected strategies",
            {"scenario_id": scenario_id, "selected": list(packet.selected_strategy_ids)},
        )
    evaluated = next(e for e in packet.evaluated if e.strategy.strategy_id == scenario_id)
    strategy = evaluated.strategy
    gov = evaluated.governance

    # blocked governance → blocked plan
    if (
        gov.state == GovernanceProposalState.BLOCKED
        or packet.execution_readiness == Readiness.BLOCKED
    ):
        plan_id = deterministic_id("plan", packet.packet_id, scenario_id, "blocked")
        receipt = content_hash({"plan_id": plan_id, "blocked": True})
        return ExecutionPlanProposal(
            schema_version=SV_PLAN,
            plan_id=plan_id,
            packet_id=packet.packet_id,
            scenario_id=scenario_id,
            stages=(),
            readiness=Readiness.BLOCKED,
            executable_in_principle=False,
            blocked=True,
            approval_required=False,
            missing_capabilities=(),
            missing_evidence=tuple(strategy.evidence_gaps),
            plan_receipt=receipt,
            explanation="PLAN_BLOCKED governance_or_packet_blocked",
            timestamp=cfg.fixed_timestamp,
            engine_version=cfg.engine_version,
        )

    # Build stages from stages_hint or default safe pipeline
    raw_stages = list(strategy.stages_hint)
    if not raw_stages:
        raw_stages = [
            {
                "stage_id": "s1_observe",
                "title": "Observe and verify evidence",
                "dependencies": [],
                "preconditions": ["opportunity_validated"],
                "capability_classes": ["observe.read"],
                "expected_evidence_outputs": ["evidence_snapshot"],
                "action_class": "observe",
                "reversible": True,
                "approval_checkpoint": False,
                "stop_conditions": list(strategy.stop_conditions) or ["operator_stop"],
                "rollback_proposal": None,
                "max_attempts": 2,
                "timeout_seconds": 300,
                "completion_criteria": ["evidence_snapshot_present"],
                "owner": "domain_organ",
            },
            {
                "stage_id": "s2_next_safe",
                "title": strategy.next_safe_action,
                "dependencies": ["s1_observe"],
                "preconditions": ["evidence_snapshot_present"],
                "capability_classes": list(
                    _ACTION_CAPS.get(
                        (strategy.action_classes[0] if strategy.action_classes else "research"),
                        ("research.read",),
                    )
                ),
                "expected_evidence_outputs": ["action_receipt"],
                "action_class": (
                    strategy.action_classes[0] if strategy.action_classes else "research"
                ),
                "reversible": strategy.reversibility != "irreversible",
                "approval_checkpoint": gov.state
                in (
                    GovernanceProposalState.APPROVAL_REQUIRED,
                    GovernanceProposalState.ESCALATE,
                ),
                "stop_conditions": list(strategy.stop_conditions) or ["operator_stop"],
                "rollback_proposal": "abort_without_side_effects"
                if strategy.reversibility == "reversible"
                else "compensating_transaction_required",
                "max_attempts": 1,
                "timeout_seconds": 600,
                "completion_criteria": ["action_receipt_present"],
                "owner": "operator" if strategy.reversibility == "irreversible" else "worker",
            },
        ]

    stages: list[PlanStage] = []
    for raw in raw_stages:
        sid = str(raw.get("stage_id") or "")
        if not sid:
            raise PlanningError("stage_id required")
        action_class = str(raw.get("action_class") or "observe")
        caps = tuple(raw.get("capability_classes") or _ACTION_CAPS.get(action_class, ()))
        if not caps:
            raise PlanningError(f"stage {sid} missing capability_classes")
        reversible = bool(raw.get("reversible", True))
        approval = bool(raw.get("approval_checkpoint", False))
        if action_class in ("financial_commit", "payment", "external_message", "email", "sms", "seller_contact"):
            approval = True
        if action_class in ("irreversible_write",) or not reversible:
            approval = True
        owner = raw.get("owner")
        rollback = raw.get("rollback_proposal")
        if action_class in ("write", "irreversible_write") or not reversible:
            if not owner:
                raise PlanningError(f"write/irreversible stage {sid} requires owner")
            if not rollback:
                raise PlanningError(f"write/irreversible stage {sid} requires rollback_proposal")
        idem = str(
            raw.get("idempotency_key")
            or deterministic_id("idem", packet.packet_id, scenario_id, sid)
        )
        stages.append(
            PlanStage(
                schema_version=SV_STAGE,
                stage_id=sid,
                title=str(raw.get("title") or sid),
                dependencies=tuple(raw.get("dependencies") or ()),
                preconditions=tuple(raw.get("preconditions") or ()),
                capability_classes=caps,
                expected_evidence_outputs=tuple(raw.get("expected_evidence_outputs") or ()),
                idempotency_key=idem,
                approval_checkpoint=approval,
                stop_conditions=tuple(raw.get("stop_conditions") or ("operator_stop",)),
                rollback_proposal=rollback,
                max_attempts=int(raw.get("max_attempts") or 1),
                timeout_seconds=raw.get("timeout_seconds"),
                completion_criteria=tuple(raw.get("completion_criteria") or ()),
                action_class=action_class,
                reversible=reversible,
                owner=owner,
            )
        )

    ordered = _toposort(stages)

    missing_caps: list[str] = []
    for st in ordered:
        for cap in st.capability_classes:
            if not capability_catalog.has(cap):
                missing_caps.append(cap)
    missing_caps = sorted(set(missing_caps))
    missing_ev = list(strategy.evidence_gaps)

    approval_required = any(st.approval_checkpoint for st in ordered) or gov.state in (
        GovernanceProposalState.APPROVAL_REQUIRED,
        GovernanceProposalState.ESCALATE,
    )

    if missing_caps:
        readiness = Readiness.HOLD
        executable = False
        blocked = False
    elif approval_required:
        readiness = Readiness.APPROVAL_REQUIRED
        executable = True  # in principle after approval
        blocked = False
    else:
        readiness = Readiness.READY_IN_PRINCIPLE
        executable = True
        blocked = False

    # No executable code/shell in stages
    for st in ordered:
        blob = " ".join(
            [st.title, st.action_class, st.rollback_proposal or ""]
            + list(st.preconditions)
            + list(st.completion_criteria)
        ).lower()
        for bad in ("os.system", "subprocess", "eval(", "exec(", "/bin/sh", "bash -c"):
            if bad in blob:
                raise PlanningError("plan must not contain shell/executable directives", {"stage": st.stage_id})

    plan_id = deterministic_id(
        "plan", packet.packet_id, scenario_id, [s.stage_id for s in ordered]
    )
    receipt = content_hash(
        {
            "plan_id": plan_id,
            "stages": [s.to_dict() for s in ordered],
            "readiness": readiness.value,
            "missing_capabilities": missing_caps,
        }
    )
    explanation = (
        f"PLAN scenario={scenario_id} stages={len(ordered)} "
        f"readiness={readiness.value} missing_caps={missing_caps} "
        f"approval_required={approval_required}"
    )

    return ExecutionPlanProposal(
        schema_version=SV_PLAN,
        plan_id=plan_id,
        packet_id=packet.packet_id,
        scenario_id=scenario_id,
        stages=tuple(ordered),
        readiness=readiness,
        executable_in_principle=executable and not blocked,
        blocked=blocked,
        approval_required=approval_required,
        missing_capabilities=tuple(missing_caps),
        missing_evidence=tuple(missing_ev),
        plan_receipt=receipt,
        explanation=explanation,
        timestamp=cfg.fixed_timestamp,
        engine_version=cfg.engine_version,
    )
