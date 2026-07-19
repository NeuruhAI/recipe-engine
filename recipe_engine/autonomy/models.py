"""Versioned autonomy contracts. Pure dataclasses; no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence


class ClaimClass(str, Enum):
    OBSERVED = "observed"
    CORROBORATED = "corroborated"
    INFERRED = "inferred"
    ASSUMPTION = "assumption"
    REJECTED = "rejected"


class GovernanceProposalState(str, Enum):
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    ESCALATE = "escalate"
    BLOCKED = "blocked"


class RejectionReason(str, Enum):
    DOMINATED = "dominated"
    DUPLICATE = "duplicate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROHIBITED = "prohibited"
    INFEASIBLE = "infeasible"
    OUTSIDE_AUTHORITY = "outside_authority"
    EXPIRED_DATA = "expired_data"
    EXCESSIVE_DOWNSIDE = "excessive_downside"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    INVALID_INPUT = "invalid_input"


class Readiness(str, Enum):
    READY_IN_PRINCIPLE = "ready_in_principle"
    APPROVAL_REQUIRED = "approval_required"
    HOLD = "hold"
    BLOCKED = "blocked"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class AuthorityLevel(str, Enum):
    UNKNOWN = "unknown"
    UNTRUSTED = "untrusted"
    OBSERVED = "observed"
    DOMAIN_AUTHORITY = "domain_authority"
    OPERATOR = "operator"
    GOVERNANCE = "governance"


class ConflictState(str, Enum):
    NONE = "none"
    OPEN = "open"
    RESOLVED = "resolved"


class ScoreDirection(str, Enum):
    HIGHER_BETTER = "higher_better"
    LOWER_BETTER = "lower_better"


class ErrorClass(str, Enum):
    PREDICTION = "prediction"
    STRATEGY = "strategy"
    EXECUTION = "execution"
    GOVERNANCE = "governance"
    DATA_QUALITY = "data_quality"
    EXTERNAL_SHOCK = "external_shock"
    INCOMPLETE = "incomplete"


class CalibrationType(str, Enum):
    SCOPED_WEIGHT_ADJUSTMENT = "scoped_weight_adjustment"
    EVIDENCE_REQUIREMENT_CHANGE = "evidence_requirement_change"
    CONFIDENCE_CALIBRATION = "confidence_calibration"
    STRATEGY_ELIGIBILITY_CHANGE = "strategy_eligibility_change"
    STOP_CONDITION_ADDITION = "stop_condition_addition"
    DATA_SOURCE_DOWNGRADE = "data_source_downgrade"
    FOLLOW_UP_EXPERIMENT = "follow_up_experiment"


@dataclass(frozen=True)
class EvidenceReference:
    schema_version: str
    evidence_id: str
    source_type: str
    internal_pointer: str
    observed_at: str
    content_hash: str
    provenance_chain_id: str
    sensitivity: Sensitivity
    freshness: str  # current | stale | expired
    authority_level: AuthorityLevel
    supported_claim_ids: tuple[str, ...] = ()
    conflict_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "internal_pointer": self.internal_pointer,
            "observed_at": self.observed_at,
            "content_hash": self.content_hash,
            "provenance_chain_id": self.provenance_chain_id,
            "sensitivity": self.sensitivity.value,
            "freshness": self.freshness,
            "authority_level": self.authority_level.value,
            "supported_claim_ids": list(self.supported_claim_ids),
            "conflict_ids": list(self.conflict_ids),
        }


@dataclass(frozen=True)
class Claim:
    schema_version: str
    claim_id: str
    statement: str
    classification: ClaimClass
    evidence_refs: tuple[str, ...]
    confidence: Decimal
    source_confidence: Decimal
    conflict_state: ConflictState
    verification_requirements: tuple[str, ...]
    freshness: str
    expires_at: str | None
    creator: str
    version: str
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "claim_id": self.claim_id,
            "statement": self.statement,
            "classification": self.classification.value,
            "evidence_refs": list(self.evidence_refs),
            "confidence": str(self.confidence),
            "source_confidence": str(self.source_confidence),
            "conflict_state": self.conflict_state.value,
            "verification_requirements": list(self.verification_requirements),
            "freshness": self.freshness,
            "expires_at": self.expires_at,
            "creator": self.creator,
            "version": self.version,
            "critical": self.critical,
        }


@dataclass(frozen=True)
class MetricValue:
    schema_version: str
    metric_id: str
    value: Decimal
    unit: str
    scale: str
    source: str  # must be domain_supplied | operator_supplied — never engine_invented
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "metric_id": self.metric_id,
            "value": str(self.value),
            "unit": self.unit,
            "scale": self.scale,
            "source": self.source,
            "label": self.label,
        }


@dataclass(frozen=True)
class OpportunityEnvelope:
    schema_version: str
    opportunity_id: str
    domain: str
    territory: Mapping[str, Any]
    subject: Mapping[str, Any]
    assets: tuple[Mapping[str, Any], ...]
    desired_outcome: str
    constraints: tuple[str, ...]
    authority_context: Mapping[str, Any]
    time_horizon: str
    claims: tuple[Claim, ...]
    evidence: tuple[EvidenceReference, ...]
    evidence_completeness: Decimal
    supplied_financial_inputs: tuple[MetricValue, ...]
    risk_tolerance: str
    operator_requirements: tuple[str, ...]
    version: str
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "opportunity_id": self.opportunity_id,
            "domain": self.domain,
            "territory": dict(self.territory),
            "subject": dict(self.subject),
            "assets": [dict(a) for a in self.assets],
            "desired_outcome": self.desired_outcome,
            "constraints": list(self.constraints),
            "authority_context": dict(self.authority_context),
            "time_horizon": self.time_horizon,
            "claims": [c.to_dict() for c in self.claims],
            "evidence": [e.to_dict() for e in self.evidence],
            "evidence_completeness": str(self.evidence_completeness),
            "supplied_financial_inputs": [m.to_dict() for m in self.supplied_financial_inputs],
            "risk_tolerance": self.risk_tolerance,
            "operator_requirements": list(self.operator_requirements),
            "version": self.version,
            "extensions": dict(self.extensions),
        }


@dataclass(frozen=True)
class CandidateStrategy:
    schema_version: str
    strategy_id: str
    title: str
    thesis: str
    strategy_family: str
    required_claim_ids: tuple[str, ...]
    assumption_claim_ids: tuple[str, ...]
    expected_outcomes: tuple[MetricValue, ...]
    resources: tuple[str, ...]
    dependencies: tuple[str, ...]
    duration: str
    reversibility: str  # reversible | partially_reversible | irreversible
    failure_modes: tuple[str, ...]
    legal_policy_checks: tuple[str, ...]
    governance_class: str
    next_safe_action: str
    stop_conditions: tuple[str, ...]
    evidence_gaps: tuple[str, ...]
    action_classes: tuple[str, ...] = ()
    capital_intensity: str = "unknown"
    time_profile: str = "unknown"
    available_dependencies: tuple[str, ...] = ()
    missing_dependencies: tuple[str, ...] = ()
    feasibility_notes: tuple[str, ...] = ()
    prohibited: bool = False
    policy_known: bool = True
    stages_hint: tuple[Mapping[str, Any], ...] = ()
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "title": self.title,
            "thesis": self.thesis,
            "strategy_family": self.strategy_family,
            "required_claim_ids": list(self.required_claim_ids),
            "assumption_claim_ids": list(self.assumption_claim_ids),
            "expected_outcomes": [m.to_dict() for m in self.expected_outcomes],
            "resources": list(self.resources),
            "dependencies": list(self.dependencies),
            "duration": self.duration,
            "reversibility": self.reversibility,
            "failure_modes": list(self.failure_modes),
            "legal_policy_checks": list(self.legal_policy_checks),
            "governance_class": self.governance_class,
            "next_safe_action": self.next_safe_action,
            "stop_conditions": list(self.stop_conditions),
            "evidence_gaps": list(self.evidence_gaps),
            "action_classes": list(self.action_classes),
            "capital_intensity": self.capital_intensity,
            "time_profile": self.time_profile,
            "available_dependencies": list(self.available_dependencies),
            "missing_dependencies": list(self.missing_dependencies),
            "feasibility_notes": list(self.feasibility_notes),
            "prohibited": self.prohibited,
            "policy_known": self.policy_known,
            "stages_hint": [dict(s) for s in self.stages_hint],
            "extensions": dict(self.extensions),
        }


@dataclass(frozen=True)
class DimensionScore:
    schema_version: str
    dimension: str
    value: Decimal | None
    scale: str
    direction: ScoreDirection
    weight: Decimal
    rationale: str
    inputs: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    score_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dimension": self.dimension,
            "value": None if self.value is None else str(self.value),
            "scale": self.scale,
            "direction": self.direction.value,
            "weight": str(self.weight),
            "rationale": self.rationale,
            "inputs": list(self.inputs),
            "missing_inputs": list(self.missing_inputs),
            "score_version": self.score_version,
        }


@dataclass(frozen=True)
class ScenarioScore:
    schema_version: str
    strategy_id: str
    dimensions: Mapping[str, DimensionScore]
    composite: Decimal | None
    score_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "dimensions": {k: self.dimensions[k].to_dict() for k in sorted(self.dimensions.keys())},
            "composite": None if self.composite is None else str(self.composite),
            "score_version": self.score_version,
        }


@dataclass(frozen=True)
class GovernanceRequirement:
    schema_version: str
    state: GovernanceProposalState
    reason_codes: tuple[str, ...]
    required_authority: tuple[str, ...]
    required_evidence: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    expires_at: str | None
    policy_version: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "reason_codes": list(self.reason_codes),
            "required_authority": list(self.required_authority),
            "required_evidence": list(self.required_evidence),
            "prohibited_actions": list(self.prohibited_actions),
            "expires_at": self.expires_at,
            "policy_version": self.policy_version,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class EvaluatedStrategy:
    schema_version: str
    strategy: CandidateStrategy
    score: ScenarioScore
    governance: GovernanceRequirement
    selected: bool
    rank: int | None
    rejection_reason: RejectionReason | None
    rejection_detail: str | None
    explanation: str
    fingerprint: str
    diversity_key: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy": self.strategy.to_dict(),
            "score": self.score.to_dict(),
            "governance": self.governance.to_dict(),
            "selected": self.selected,
            "rank": self.rank,
            "rejection_reason": None if self.rejection_reason is None else self.rejection_reason.value,
            "rejection_detail": self.rejection_detail,
            "explanation": self.explanation,
            "fingerprint": self.fingerprint,
            "diversity_key": dict(self.diversity_key),
        }


@dataclass(frozen=True)
class DecisionPacket:
    schema_version: str
    packet_id: str
    opportunity: OpportunityEnvelope
    evaluated: tuple[EvaluatedStrategy, ...]
    selected_strategy_ids: tuple[str, ...]
    rejected: tuple[Mapping[str, Any], ...]
    pareto_metadata: Mapping[str, Any]
    uncertainty_notes: tuple[str, ...]
    conflicts: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    governance_requirements: tuple[GovernanceRequirement, ...]
    recommended_next_safe_action: str
    execution_readiness: Readiness
    deterministic_explanation: str
    input_hash: str
    receipt_hash: str
    engine_version: str
    config_version: str
    config_hash: str
    timestamp: str
    no_decision: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "packet_id": self.packet_id,
            "opportunity": self.opportunity.to_dict(),
            "evaluated": [e.to_dict() for e in self.evaluated],
            "selected_strategy_ids": list(self.selected_strategy_ids),
            "rejected": [dict(r) for r in self.rejected],
            "pareto_metadata": dict(self.pareto_metadata),
            "uncertainty_notes": list(self.uncertainty_notes),
            "conflicts": list(self.conflicts),
            "missing_evidence": list(self.missing_evidence),
            "governance_requirements": [g.to_dict() for g in self.governance_requirements],
            "recommended_next_safe_action": self.recommended_next_safe_action,
            "execution_readiness": self.execution_readiness.value,
            "deterministic_explanation": self.deterministic_explanation,
            "input_hash": self.input_hash,
            "receipt_hash": self.receipt_hash,
            "engine_version": self.engine_version,
            "config_version": self.config_version,
            "config_hash": self.config_hash,
            "timestamp": self.timestamp,
            "no_decision": self.no_decision,
        }


@dataclass(frozen=True)
class PlanStage:
    schema_version: str
    stage_id: str
    title: str
    dependencies: tuple[str, ...]
    preconditions: tuple[str, ...]
    capability_classes: tuple[str, ...]
    expected_evidence_outputs: tuple[str, ...]
    idempotency_key: str
    approval_checkpoint: bool
    stop_conditions: tuple[str, ...]
    rollback_proposal: str | None
    max_attempts: int
    timeout_seconds: int | None
    completion_criteria: tuple[str, ...]
    action_class: str
    reversible: bool
    owner: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage_id": self.stage_id,
            "title": self.title,
            "dependencies": list(self.dependencies),
            "preconditions": list(self.preconditions),
            "capability_classes": list(self.capability_classes),
            "expected_evidence_outputs": list(self.expected_evidence_outputs),
            "idempotency_key": self.idempotency_key,
            "approval_checkpoint": self.approval_checkpoint,
            "stop_conditions": list(self.stop_conditions),
            "rollback_proposal": self.rollback_proposal,
            "max_attempts": self.max_attempts,
            "timeout_seconds": self.timeout_seconds,
            "completion_criteria": list(self.completion_criteria),
            "action_class": self.action_class,
            "reversible": self.reversible,
            "owner": self.owner,
        }


@dataclass(frozen=True)
class CapabilityCatalog:
    schema_version: str
    capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "capabilities": list(self.capabilities),
        }

    def has(self, name: str) -> bool:
        return name in self.capabilities


@dataclass(frozen=True)
class ExecutionPlanProposal:
    schema_version: str
    plan_id: str
    packet_id: str
    scenario_id: str
    stages: tuple[PlanStage, ...]
    readiness: Readiness
    executable_in_principle: bool
    blocked: bool
    approval_required: bool
    missing_capabilities: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    plan_receipt: str
    explanation: str
    timestamp: str
    engine_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "packet_id": self.packet_id,
            "scenario_id": self.scenario_id,
            "stages": [s.to_dict() for s in self.stages],
            "readiness": self.readiness.value,
            "executable_in_principle": self.executable_in_principle,
            "blocked": self.blocked,
            "approval_required": self.approval_required,
            "missing_capabilities": list(self.missing_capabilities),
            "missing_evidence": list(self.missing_evidence),
            "plan_receipt": self.plan_receipt,
            "explanation": self.explanation,
            "timestamp": self.timestamp,
            "engine_version": self.engine_version,
        }


@dataclass(frozen=True)
class PredictedMetric:
    schema_version: str
    metric_id: str
    value: Decimal
    unit: str
    scale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "metric_id": self.metric_id,
            "value": str(self.value),
            "unit": self.unit,
            "scale": self.scale,
        }


@dataclass(frozen=True)
class ActualMetric:
    schema_version: str
    metric_id: str
    value: Decimal | None
    unit: str
    scale: str
    evidence_refs: tuple[str, ...]
    incomplete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "metric_id": self.metric_id,
            "value": None if self.value is None else str(self.value),
            "unit": self.unit,
            "scale": self.scale,
            "evidence_refs": list(self.evidence_refs),
            "incomplete": self.incomplete,
        }


@dataclass(frozen=True)
class OutcomeRecord:
    schema_version: str
    opportunity_id: str
    scenario_id: str
    predicted: tuple[PredictedMetric, ...]
    actual: tuple[ActualMetric, ...]
    operator_decisions: tuple[str, ...]
    external_shocks: tuple[str, ...]
    execution_failed: bool
    governance_failed: bool
    incomplete: bool
    recorded_at: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "opportunity_id": self.opportunity_id,
            "scenario_id": self.scenario_id,
            "predicted": [p.to_dict() for p in self.predicted],
            "actual": [a.to_dict() for a in self.actual],
            "operator_decisions": list(self.operator_decisions),
            "external_shocks": list(self.external_shocks),
            "execution_failed": self.execution_failed,
            "governance_failed": self.governance_failed,
            "incomplete": self.incomplete,
            "recorded_at": self.recorded_at,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class CalibrationProposal:
    schema_version: str
    proposal_id: str
    proposal_type: CalibrationType
    scope: str
    evidence: tuple[str, ...]
    required_reviewer: str
    confidence: Decimal
    promotion_prohibited: bool
    detail: str
    suggested_delta: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type.value,
            "scope": self.scope,
            "evidence": list(self.evidence),
            "required_reviewer": self.required_reviewer,
            "confidence": str(self.confidence),
            "promotion_prohibited": self.promotion_prohibited,
            "detail": self.detail,
            "suggested_delta": dict(self.suggested_delta),
        }


@dataclass(frozen=True)
class ReconciliationResult:
    schema_version: str
    result_id: str
    opportunity_id: str
    scenario_id: str
    prediction_errors: tuple[Mapping[str, Any], ...]
    failed_assumptions: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    error_classes: tuple[ErrorClass, ...]
    strategy_outcome: str
    process_outcome: str
    governance_outcome: str
    data_quality_findings: tuple[str, ...]
    calibration_proposals: tuple[CalibrationProposal, ...]
    proposal_confidence: Decimal
    required_reviewer: str
    promotion_prohibited: bool
    receipt: str
    timestamp: str
    engine_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "result_id": self.result_id,
            "opportunity_id": self.opportunity_id,
            "scenario_id": self.scenario_id,
            "prediction_errors": [dict(p) for p in self.prediction_errors],
            "failed_assumptions": list(self.failed_assumptions),
            "missing_evidence": list(self.missing_evidence),
            "error_classes": [e.value for e in self.error_classes],
            "strategy_outcome": self.strategy_outcome,
            "process_outcome": self.process_outcome,
            "governance_outcome": self.governance_outcome,
            "data_quality_findings": list(self.data_quality_findings),
            "calibration_proposals": [c.to_dict() for c in self.calibration_proposals],
            "proposal_confidence": str(self.proposal_confidence),
            "required_reviewer": self.required_reviewer,
            "promotion_prohibited": self.promotion_prohibited,
            "receipt": self.receipt,
            "timestamp": self.timestamp,
            "engine_version": self.engine_version,
        }


# Schema version constants
SV_EVIDENCE = "autonomy.evidence.v1"
SV_CLAIM = "autonomy.claim.v1"
SV_OPPORTUNITY = "autonomy.opportunity.v1"
SV_STRATEGY = "autonomy.strategy.v1"
SV_METRIC = "autonomy.metric.v1"
SV_DIMENSION = "autonomy.dimension_score.v1"
SV_SCENARIO = "autonomy.scenario_score.v1"
SV_GOVERNANCE = "autonomy.governance_requirement.v1"
SV_EVALUATED = "autonomy.evaluated_strategy.v1"
SV_PACKET = "autonomy.decision_packet.v1"
SV_STAGE = "autonomy.plan_stage.v1"
SV_PLAN = "autonomy.execution_plan.v1"
SV_CAPABILITY = "autonomy.capability_catalog.v1"
SV_PREDICTED = "autonomy.predicted_metric.v1"
SV_ACTUAL = "autonomy.actual_metric.v1"
SV_OUTCOME = "autonomy.outcome.v1"
SV_CALIBRATION = "autonomy.calibration.v1"
SV_RECONCILE = "autonomy.reconciliation.v1"
