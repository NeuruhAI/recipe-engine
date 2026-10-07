"""Deliverable compilation helpers."""

from __future__ import annotations

from recipe_engine.founder_runtime.contracts.mission import Deliverable, DeliverableStatus, SCHEMA_DELIVERABLE


#: The one deliverable an executable (work-spec) mission owes: its authorised work
#: executed, sealed, and independently judged. The system-status fixture
#: deliverables below describe a status report nobody executing a work spec
#: produces, so a work-spec mission that carried them could never be completed
#: honestly -- only by marking fixture deliverables delivered on execution
#: evidence that says nothing about them.
EXECUTION_DELIVERABLE_ID = "del_work_spec_execution"
EXECUTION_ACCEPTANCE_CRITERIA: tuple[str, ...] = (
    "sealed_dispatch_receipt_verified",
    "independent_court_passed",
    "authority_bound",
    "no_consequential_effect",
)
EXECUTION_EVIDENCE_REQUIREMENTS: tuple[str, ...] = (
    "nrh.receipt.fr-dispatch.v1",
    "independent_sentinel_court",
)


def execution_deliverables() -> list[Deliverable]:
    return [
        Deliverable(
            schema_version=SCHEMA_DELIVERABLE,
            deliverable_id=EXECUTION_DELIVERABLE_ID,
            description="Authorised work spec executed, receipt sealed, independent court PASS",
            required=True,
            acceptance_criteria=EXECUTION_ACCEPTANCE_CRITERIA,
            evidence_requirements=EXECUTION_EVIDENCE_REQUIREMENTS,
            status=DeliverableStatus.PENDING,
        ),
    ]


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
