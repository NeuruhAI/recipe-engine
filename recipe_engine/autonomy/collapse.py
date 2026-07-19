"""Deterministic scenario collapse: 0–4 selection. Pure; no I/O."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from .config import EngineConfig, MAX_SELECTED, validate_config
from .deduplication import collapse_duplicates, diversity_key, strategy_fingerprint
from .errors import ValidationError
from .explanation import explain_packet, explain_rejected, explain_selected
from .governance import propose_governance
from .hashing import content_hash, deterministic_id
from .models import (
    SV_EVALUATED,
    SV_PACKET,
    CandidateStrategy,
    DecisionPacket,
    EvaluatedStrategy,
    GovernanceProposalState,
    OpportunityEnvelope,
    Readiness,
    RejectionReason,
)
from .pareto import composite_tie_key, domination_map, pareto_frontier
from .scoring import score_strategy
from .validation import (
    collect_open_conflicts,
    collect_missing_required_claims,
    validate_candidates,
    validate_opportunity,
)


def _packet_readiness(
    selected: list[EvaluatedStrategy],
    no_decision: bool,
) -> Readiness:
    if no_decision or not selected:
        # distinguish blocked vs hold
        if any(
            e.governance.state == GovernanceProposalState.BLOCKED
            for e in selected
        ):
            return Readiness.BLOCKED
        return Readiness.HOLD
    states = {e.governance.state for e in selected}
    if GovernanceProposalState.BLOCKED in states:
        return Readiness.BLOCKED
    if GovernanceProposalState.ESCALATE in states:
        return Readiness.HOLD
    if GovernanceProposalState.APPROVAL_REQUIRED in states:
        return Readiness.APPROVAL_REQUIRED
    return Readiness.READY_IN_PRINCIPLE


def _select_diverse(
    eligible: list[EvaluatedStrategy],
    max_selected: int,
) -> list[EvaluatedStrategy]:
    """Pick up to max_selected with diversity; never rescue non-eligible."""
    if max_selected <= 0 or not eligible:
        return []
    # eligible already sorted by composite key
    selected: list[EvaluatedStrategy] = []
    used_families: set[str] = set()
    used_risk: set[str] = set()
    # first pass: prefer diversity
    for e in eligible:
        if len(selected) >= max_selected:
            break
        fam = e.diversity_key.get("strategy_family", "")
        risk = e.diversity_key.get("risk_profile", "")
        if fam in used_families and risk in used_risk and len(selected) > 0:
            continue
        selected.append(e)
        used_families.add(fam)
        used_risk.add(risk)
    # second pass: fill remaining slots by rank if still room
    if len(selected) < max_selected:
        selected_ids = {s.strategy.strategy_id for s in selected}
        for e in eligible:
            if e.strategy.strategy_id in selected_ids:
                continue
            selected.append(e)
            selected_ids.add(e.strategy.strategy_id)
            if len(selected) >= max_selected:
                break
    return selected[:max_selected]


def collapse_scenarios(
    opportunity: OpportunityEnvelope,
    candidates: Sequence[CandidateStrategy],
    config: EngineConfig | None = None,
) -> DecisionPacket:
    """
    Pure public entrypoint: validate, score, Pareto, dedupe, select 0–4.
    Deterministic under fixed config.timestamp/id policy.
    """
    cfg = validate_config(config or EngineConfig())
    if cfg.max_selected > MAX_SELECTED:
        raise ValidationError(f"max_selected hard cap is {MAX_SELECTED}")

    opp = validate_opportunity(
        opportunity, max_claims=cfg.max_claims, max_evidence=cfg.max_evidence
    )
    try:
        cand_list = validate_candidates(list(candidates), max_candidates=cfg.max_candidates)
    except ValidationError:
        raise

    input_hash = content_hash(
        {
            "opportunity": opp.to_dict(),
            "candidates": [c.to_dict() for c in cand_list],
            "config_hash": cfg.config_hash(),
        }
    )

    claim_ids = {c.claim_id for c in opp.claims}
    open_conflicts = collect_open_conflicts(opp)
    critical_conflicts = [
        c.claim_id
        for c in opp.claims
        if c.critical and c.conflict_state.value == "open"
    ]

    # Deduplicate
    kept, dup_parent = collapse_duplicates(cand_list, cfg.duplicate_threshold)

    evaluated: list[EvaluatedStrategy] = []
    scores = []

    # Reject duplicates first
    for c in cand_list:
        if c.strategy_id in dup_parent:
            parent = dup_parent[c.strategy_id]
            gov = propose_governance(opp, c)
            sc = score_strategy(opp, c, cfg)
            scores.append(sc)
            evaluated.append(
                EvaluatedStrategy(
                    schema_version=SV_EVALUATED,
                    strategy=c,
                    score=sc,
                    governance=gov,
                    selected=False,
                    rank=None,
                    rejection_reason=RejectionReason.DUPLICATE,
                    rejection_detail=f"near_duplicate_of={parent}",
                    explanation=explain_rejected(
                        c,
                        RejectionReason.DUPLICATE,
                        f"near_duplicate_of={parent}",
                        sc,
                        cfg,
                        duplicate_parent=parent,
                        governance=gov,
                    ),
                    fingerprint=strategy_fingerprint(c),
                    diversity_key=diversity_key(c),
                )
            )

    kept_ids = {c.strategy_id for c in kept}
    kept_scores = []
    for c in kept:
        gov = propose_governance(opp, c)
        sc = score_strategy(opp, c, cfg)
        kept_scores.append(sc)
        scores.append(sc)

        # hard rejections
        reason: RejectionReason | None = None
        detail = ""
        if c.prohibited or gov.state == GovernanceProposalState.BLOCKED:
            reason = RejectionReason.PROHIBITED
            detail = "governance_blocked_or_prohibited"
        elif c.missing_dependencies and any(
            d in c.dependencies for d in c.missing_dependencies
        ):
            # only if all required missing
            if set(c.dependencies) and set(c.missing_dependencies) >= set(c.dependencies):
                reason = RejectionReason.DEPENDENCY_UNAVAILABLE
                detail = f"missing={list(c.missing_dependencies)}"
        miss_claims = collect_missing_required_claims(c, claim_ids)
        completeness = sc.dimensions["evidence_completeness"].value or Decimal("0")
        if reason is None and miss_claims:
            # missing required claims: always insufficient if any critical, or completeness bar fails
            if any(claims_critical(opp, cid) for cid in miss_claims) or any(
                cid not in claim_ids for cid in miss_claims
            ):
                reason = RejectionReason.INSUFFICIENT_EVIDENCE
                detail = f"missing_required_claims={miss_claims};completeness={completeness}"
        if reason is None and completeness < cfg.critical_evidence_completeness_min:
            reason = RejectionReason.INSUFFICIENT_EVIDENCE
            detail = f"completeness={completeness}"
        expired = any(
            cl.claim_id in c.required_claim_ids and cl.freshness == "expired"
            for cl in opp.claims
        )
        if reason is None and expired:
            reason = RejectionReason.EXPIRED_DATA
            detail = "required_claim_expired"
        if reason is None and sc.dimensions["feasibility"].value == Decimal("0"):
            reason = RejectionReason.INFEASIBLE
            detail = "feasibility_zero"
        downside = sc.dimensions["downside_risk"].value or Decimal("0")
        if reason is None and downside >= Decimal("0.95"):
            reason = RejectionReason.EXCESSIVE_DOWNSIDE
            detail = f"downside_risk={downside}"
        # outside authority
        allowed_domains = opp.authority_context.get("allowed_domains")
        if reason is None and isinstance(allowed_domains, (list, tuple)):
            if opp.domain not in allowed_domains:
                reason = RejectionReason.OUTSIDE_AUTHORITY
                detail = f"domain={opp.domain}"

        evaluated.append(
            EvaluatedStrategy(
                schema_version=SV_EVALUATED,
                strategy=c,
                score=sc,
                governance=gov,
                selected=False,
                rank=None,
                rejection_reason=reason,
                rejection_detail=detail or None,
                explanation="" if reason is None else explain_rejected(
                    c, reason, detail, sc, cfg, governance=gov
                ),
                fingerprint=strategy_fingerprint(c),
                diversity_key=diversity_key(c),
            )
        )

    # Pareto among non-pre-rejected kept
    pre_ok = [
        e
        for e in evaluated
        if e.strategy.strategy_id in kept_ids and e.rejection_reason is None
    ]
    score_by_id = {s.strategy_id: s for s in kept_scores}
    frontier_ids = set(
        pareto_frontier([score_by_id[e.strategy.strategy_id] for e in pre_ok])
    ) if pre_ok else set()
    dom_map = domination_map([score_by_id[e.strategy.strategy_id] for e in pre_ok]) if pre_ok else {}

    # mark dominated
    updated: list[EvaluatedStrategy] = []
    for e in evaluated:
        if e.rejection_reason is not None:
            updated.append(e)
            continue
        if e.strategy.strategy_id not in kept_ids:
            updated.append(e)
            continue
        if e.strategy.strategy_id not in frontier_ids and e.strategy.strategy_id in dom_map:
            reason = RejectionReason.DOMINATED
            detail = f"dominated_by={dom_map[e.strategy.strategy_id]}"
            updated.append(
                EvaluatedStrategy(
                    schema_version=SV_EVALUATED,
                    strategy=e.strategy,
                    score=e.score,
                    governance=e.governance,
                    selected=False,
                    rank=None,
                    rejection_reason=reason,
                    rejection_detail=detail,
                    explanation=explain_rejected(
                        e.strategy,
                        reason,
                        detail,
                        e.score,
                        cfg,
                        dominators=dom_map[e.strategy.strategy_id],
                        governance=e.governance,
                    ),
                    fingerprint=e.fingerprint,
                    diversity_key=e.diversity_key,
                )
            )
        else:
            updated.append(e)
    evaluated = updated

    # Critical global no-decision conditions
    force_no_decision = False
    uncertainty_notes: list[str] = []
    if cfg.critical_conflict_blocks and critical_conflicts:
        force_no_decision = True
        uncertainty_notes.append(f"critical_open_conflicts={critical_conflicts}")
    if opp.evidence_completeness < cfg.critical_evidence_completeness_min:
        # only force if no candidate clears bar
        uncertainty_notes.append(
            f"envelope_completeness={opp.evidence_completeness}"
        )

    eligible = [
        e
        for e in evaluated
        if e.rejection_reason is None
        and e.governance.state != GovernanceProposalState.BLOCKED
    ]
    # sort by composite key
    eligible.sort(key=lambda e: composite_tie_key(e.score, cfg))

    # If all candidates fail completeness critical, force no decision
    if not eligible:
        force_no_decision = True
        uncertainty_notes.append("no_eligible_candidates")

    if force_no_decision:
        selected: list[EvaluatedStrategy] = []
    else:
        # Clear dominance preference: if one clearly dominates (sole frontier + margin)
        selected = _select_diverse(eligible, cfg.max_selected)
        # hard enforce max 4
        selected = selected[: MAX_SELECTED]
        selected = selected[: cfg.max_selected]

    selected_ids = {s.strategy.strategy_id for s in selected}

    # rebuild evaluated with selection ranks and explanations
    final_eval: list[EvaluatedStrategy] = []
    # rank map
    rank_map = {s.strategy.strategy_id: i + 1 for i, s in enumerate(selected)}
    for e in sorted(evaluated, key=lambda x: x.strategy.strategy_id):
        if e.strategy.strategy_id in selected_ids:
            rank = rank_map[e.strategy.strategy_id]
            adj = None
            # adjacent = next rank
            for s in selected:
                if rank_map[s.strategy.strategy_id] == rank + 1:
                    adj = s
                    break
            exp = explain_selected(
                e.strategy, e.score, e.governance, rank, adj, cfg
            )
            final_eval.append(
                EvaluatedStrategy(
                    schema_version=SV_EVALUATED,
                    strategy=e.strategy,
                    score=e.score,
                    governance=e.governance,
                    selected=True,
                    rank=rank,
                    rejection_reason=None,
                    rejection_detail=None,
                    explanation=exp,
                    fingerprint=e.fingerprint,
                    diversity_key=e.diversity_key,
                )
            )
        else:
            if e.rejection_reason is None and e.strategy.strategy_id not in selected_ids:
                # not selected due to max-four / diversity — treat as dominated-ish
                reason = RejectionReason.DOMINATED
                detail = "not_selected_after_diversity_cap"
                final_eval.append(
                    EvaluatedStrategy(
                        schema_version=SV_EVALUATED,
                        strategy=e.strategy,
                        score=e.score,
                        governance=e.governance,
                        selected=False,
                        rank=None,
                        rejection_reason=reason,
                        rejection_detail=detail,
                        explanation=explain_rejected(
                            e.strategy, reason, detail, e.score, cfg, governance=e.governance
                        ),
                        fingerprint=e.fingerprint,
                        diversity_key=e.diversity_key,
                    )
                )
            else:
                final_eval.append(e)

    selected_final = [e for e in final_eval if e.selected]
    selected_final.sort(key=lambda e: e.rank or 999)
    no_decision = len(selected_final) == 0

    readiness = _packet_readiness(selected_final, no_decision)
    # blocked governance on selected should never happen; double-check
    if any(e.governance.state == GovernanceProposalState.BLOCKED for e in selected_final):
        # remove them — safety
        selected_final = [
            e for e in selected_final if e.governance.state != GovernanceProposalState.BLOCKED
        ]
        no_decision = len(selected_final) == 0
        readiness = _packet_readiness(selected_final, no_decision)

    rejected = tuple(
        {
            "strategy_id": e.strategy.strategy_id,
            "reason": e.rejection_reason.value if e.rejection_reason else "not_selected",
            "detail": e.rejection_detail,
        }
        for e in final_eval
        if not e.selected
    )

    missing_evidence = sorted(
        {
            g
            for e in final_eval
            for g in e.strategy.evidence_gaps
        }
    )

    gov_reqs = tuple(e.governance for e in selected_final) if selected_final else tuple(
        e.governance for e in final_eval if e.governance.state == GovernanceProposalState.BLOCKED
    )[:1]

    next_action = (
        selected_final[0].strategy.next_safe_action
        if selected_final
        else "gather_critical_evidence"
    )

    det_expl = explain_packet(
        selected_final, len(rejected), no_decision, readiness.value, cfg
    )

    packet_body = {
        "input_hash": input_hash,
        "selected": [e.strategy.strategy_id for e in selected_final],
        "rejected": list(rejected),
        "readiness": readiness.value,
        "config_hash": cfg.config_hash(),
        "timestamp": cfg.fixed_timestamp,
    }
    packet_id = deterministic_id("pkt", cfg.id_salt, packet_body)
    receipt_hash = content_hash({**packet_body, "packet_id": packet_id, "explanation": det_expl})

    assert len(selected_final) <= MAX_SELECTED
    assert len(selected_final) <= cfg.max_selected

    return DecisionPacket(
        schema_version=SV_PACKET,
        packet_id=packet_id,
        opportunity=opp,
        evaluated=tuple(sorted(final_eval, key=lambda e: e.strategy.strategy_id)),
        selected_strategy_ids=tuple(e.strategy.strategy_id for e in selected_final),
        rejected=rejected,
        pareto_metadata={
            "frontier": sorted(frontier_ids),
            "domination_map": dom_map,
            "duplicate_parents": dup_parent,
        },
        uncertainty_notes=tuple(uncertainty_notes),
        conflicts=tuple(open_conflicts),
        missing_evidence=tuple(missing_evidence),
        governance_requirements=gov_reqs,
        recommended_next_safe_action=next_action,
        execution_readiness=readiness,
        deterministic_explanation=det_expl,
        input_hash=input_hash,
        receipt_hash=receipt_hash,
        engine_version=cfg.engine_version,
        config_version=cfg.config_version,
        config_hash=cfg.config_hash(),
        timestamp=cfg.fixed_timestamp,
        no_decision=no_decision,
    )


def claims_critical(opp: OpportunityEnvelope, claim_id: str) -> bool:
    for c in opp.claims:
        if c.claim_id == claim_id:
            return c.critical
    return False
