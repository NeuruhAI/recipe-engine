"""Deterministic template explanations. No LLM. Pure; no I/O."""

from __future__ import annotations

from decimal import Decimal

from .config import EngineConfig
from .models import (
    CandidateStrategy,
    EvaluatedStrategy,
    GovernanceRequirement,
    RejectionReason,
    ScenarioScore,
)


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def explain_selected(
    strategy: CandidateStrategy,
    score: ScenarioScore,
    governance: GovernanceRequirement,
    rank: int,
    adjacent: EvaluatedStrategy | None,
    config: EngineConfig,
) -> str:
    dims = score.dimensions
    strength = dims.get("evidence_strength")
    conf = dims.get("confidence")
    down = dims.get("downside_risk")
    unc = dims.get("uncertainty")
    strongest = strength.inputs[:3] if strength else ()
    weakest = strategy.assumption_claim_ids[:3]
    risks = strategy.failure_modes[:3]
    gaps = strategy.evidence_gaps[:3]
    trade = "none"
    if adjacent is not None:
        trade = (
            f"vs {adjacent.strategy.strategy_id}: "
            f"composite {score.composite} vs {adjacent.score.composite}; "
            f"family {strategy.strategy_family} vs {adjacent.strategy.strategy_family}"
        )
    text = (
        f"SELECTED rank={rank} strategy_id={strategy.strategy_id} | "
        f"why_survived=non_dominated_diverse_eligible | "
        f"composite={score.composite} | "
        f"confidence={conf.value if conf else None} | "
        f"strongest_evidence={list(strongest)} | "
        f"weakest_assumptions={list(weakest)} | "
        f"top_risks={list(risks)} | "
        f"missing_evidence={list(gaps)} | "
        f"uncertainty={unc.value if unc else None} | "
        f"downside_risk={down.value if down else None} | "
        f"tradeoff={trade} | "
        f"next_safe_action={strategy.next_safe_action} | "
        f"approval={governance.state.value} reasons={list(governance.reason_codes)}"
    )
    return _trim(text, config.max_explanation_chars)


def explain_rejected(
    strategy: CandidateStrategy,
    reason: RejectionReason,
    detail: str,
    score: ScenarioScore | None,
    config: EngineConfig,
    *,
    dominators: list[str] | None = None,
    duplicate_parent: str | None = None,
    governance: GovernanceRequirement | None = None,
) -> str:
    parts = [
        f"REJECTED strategy_id={strategy.strategy_id}",
        f"code={reason.value}",
        f"detail={detail}",
    ]
    if dominators:
        parts.append(f"dominated_by={dominators}")
        if score is not None:
            weak = []
            for dim, ds in score.dimensions.items():
                if ds.value is not None:
                    weak.append(f"{dim}={ds.value}")
            parts.append(f"dimensions={weak[:6]}")
    if duplicate_parent:
        parts.append(f"duplicate_parent={duplicate_parent}")
    if governance is not None:
        parts.append(f"governance={governance.state.value}:{list(governance.reason_codes)}")
    if strategy.evidence_gaps:
        parts.append(f"missing_evidence={list(strategy.evidence_gaps[:5])}")
    return _trim(" | ".join(parts), config.max_explanation_chars)


def explain_packet(
    selected: list[EvaluatedStrategy],
    rejected_count: int,
    no_decision: bool,
    readiness: str,
    config: EngineConfig,
) -> str:
    if no_decision:
        text = (
            f"NO_DECISION selected=0 rejected={rejected_count} "
            f"readiness={readiness} reason=insufficient_or_blocked"
        )
    else:
        ids = [s.strategy.strategy_id for s in selected]
        text = (
            f"DECISION selected={len(selected)} ids={ids} "
            f"rejected={rejected_count} readiness={readiness}"
        )
    return _trim(text, config.max_explanation_chars)
