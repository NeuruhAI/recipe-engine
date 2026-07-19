"""Deliverable compilation helpers."""

from __future__ import annotations

from recipe_engine.founder_runtime.contracts.mission import Deliverable, DeliverableStatus, SCHEMA_DELIVERABLE


def system_status_deliverables() -> list[Deliverable]:
    return [
        Deliverable(
            schema_version=SCHEMA_DELIVERABLE,
            deliverable_id="del_status_report",
            description="Evidence-backed organism status summary",
            required=True,
            acceptance_criteria=("claims_present", "health_snapshot_present"),
            evidence_requirements=("fixture_mother_health", "fixture_github_state"),
            status=DeliverableStatus.PENDING,
        ),
        Deliverable(
            schema_version=SCHEMA_DELIVERABLE,
            deliverable_id="del_next_plan",
            description="Safest next execution plan proposal (not executed)",
            required=True,
            acceptance_criteria=("decision_packet_present", "plan_proposal_present"),
            evidence_requirements=("p0_collapse_receipt",),
            status=DeliverableStatus.PENDING,
        ),
        Deliverable(
            schema_version=SCHEMA_DELIVERABLE,
            deliverable_id="del_blocked_list",
            description="List of blocked/degraded dependencies from fixtures",
            required=True,
            acceptance_criteria=("blocked_or_degraded_disclosed",),
            evidence_requirements=("fixture_mother_health",),
            status=DeliverableStatus.PENDING,
        ),
    ]
