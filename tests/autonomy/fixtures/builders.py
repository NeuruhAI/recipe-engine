"""Deterministic fixture builders for autonomy tests. Pure; no I/O."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from recipe_engine.autonomy.models import (
    SV_CLAIM,
    SV_EVIDENCE,
    SV_METRIC,
    SV_OPPORTUNITY,
    SV_STRATEGY,
    AuthorityLevel,
    CandidateStrategy,
    Claim,
    ClaimClass,
    ConflictState,
    EvidenceReference,
    MetricValue,
    OpportunityEnvelope,
    Sensitivity,
)


def evidence(
    eid: str,
    *,
    source_type: str = "fixture",
    authority: AuthorityLevel = AuthorityLevel.OBSERVED,
    freshness: str = "current",
    claims: Sequence[str] = (),
) -> EvidenceReference:
    return EvidenceReference(
        schema_version=SV_EVIDENCE,
        evidence_id=eid,
        source_type=source_type,
        internal_pointer=f"opaque:{eid}",
        observed_at="2026-01-01T00:00:00Z",
        content_hash=f"hash_{eid}",
        provenance_chain_id=f"prov_{eid}",
        sensitivity=Sensitivity.INTERNAL,
        freshness=freshness,
        authority_level=authority,
        supported_claim_ids=tuple(claims),
        conflict_ids=(),
    )


def claim(
    cid: str,
    *,
    classification: ClaimClass = ClaimClass.OBSERVED,
    evidence_refs: Sequence[str] = (),
    confidence: str = "0.8",
    source_confidence: str = "0.8",
    creator: str = "fixture",
    critical: bool = False,
    conflict: ConflictState = ConflictState.NONE,
    freshness: str = "current",
) -> Claim:
    return Claim(
        schema_version=SV_CLAIM,
        claim_id=cid,
        statement=f"statement for {cid}",
        classification=classification,
        evidence_refs=tuple(evidence_refs),
        confidence=Decimal(confidence),
        source_confidence=Decimal(source_confidence),
        conflict_state=conflict,
        verification_requirements=(),
        freshness=freshness,
        expires_at=None,
        creator=creator,
        version="1",
        critical=critical,
    )


def metric(mid: str, value: str, *, unit: str = "score", scale: str = "0..1") -> MetricValue:
    return MetricValue(
        schema_version=SV_METRIC,
        metric_id=mid,
        value=Decimal(value),
        unit=unit,
        scale=scale,
        source="domain_supplied",
        label=mid,
    )


def opportunity(
    oid: str,
    domain: str,
    claims: Sequence[Claim],
    evidence_list: Sequence[EvidenceReference],
    *,
    completeness: str = "0.8",
    financials: Sequence[MetricValue] = (),
    risk: str = "medium",
    requirements: Sequence[str] = (),
    authority: dict[str, Any] | None = None,
) -> OpportunityEnvelope:
    return OpportunityEnvelope(
        schema_version=SV_OPPORTUNITY,
        opportunity_id=oid,
        domain=domain,
        territory={"region": "fixture"},
        subject={"id": oid},
        assets=(),
        desired_outcome="maximize_governed_value",
        constraints=("no_illegal",),
        authority_context=authority
        or {"sensitivity": "internal", "allowed_domains": [domain]},
        time_horizon="months",
        claims=tuple(claims),
        evidence=tuple(evidence_list),
        evidence_completeness=Decimal(completeness),
        supplied_financial_inputs=tuple(financials),
        risk_tolerance=risk,
        operator_requirements=tuple(requirements),
        version="1",
        extensions={},
    )


def strategy(
    sid: str,
    *,
    family: str = "generic",
    thesis: str = "default thesis",
    required: Sequence[str] = (),
    assumptions: Sequence[str] = (),
    econ: str = "0.5",
    reversibility: str = "reversible",
    capital: str = "low",
    time_profile: str = "medium",
    actions: Sequence[str] = ("research",),
    gaps: Sequence[str] = (),
    deps: Sequence[str] = (),
    avail: Sequence[str] = (),
    missing: Sequence[str] = (),
    failures: Sequence[str] = (),
    gov_class: str = "standard",
    prohibited: bool = False,
    policy_known: bool = True,
    next_action: str = "review_evidence",
    stages: Sequence[dict] = (),
    legal: Sequence[str] = (),
) -> CandidateStrategy:
    return CandidateStrategy(
        schema_version=SV_STRATEGY,
        strategy_id=sid,
        title=sid,
        thesis=thesis,
        strategy_family=family,
        required_claim_ids=tuple(required),
        assumption_claim_ids=tuple(assumptions),
        expected_outcomes=(metric("econ.attractiveness", econ),),
        resources=("time",),
        dependencies=tuple(deps),
        duration="30d",
        reversibility=reversibility,
        failure_modes=tuple(failures),
        legal_policy_checks=tuple(legal),
        governance_class=gov_class,
        next_safe_action=next_action,
        stop_conditions=("operator_stop",),
        evidence_gaps=tuple(gaps),
        action_classes=tuple(actions),
        capital_intensity=capital,
        time_profile=time_profile,
        available_dependencies=tuple(avail if avail or not deps else deps),
        missing_dependencies=tuple(missing),
        feasibility_notes=(),
        prohibited=prohibited,
        policy_known=policy_known,
        stages_hint=tuple(stages),
        extensions={},
    )


def domain_pack(name: str) -> tuple[OpportunityEnvelope, list[CandidateStrategy]]:
    """Cross-domain fixture packs."""
    e1 = evidence("ev1", claims=("c1",), authority=AuthorityLevel.DOMAIN_AUTHORITY)
    e2 = evidence("ev2", source_type="county_record", claims=("c1",), authority=AuthorityLevel.OBSERVED)
    c1 = claim("c1", evidence_refs=("ev1", "ev2"), classification=ClaimClass.CORROBORATED)
    c_assump = claim(
        "c_a",
        classification=ClaimClass.ASSUMPTION,
        confidence="0.4",
        source_confidence="0.4",
        evidence_refs=(),
    )

    if name == "residential_acquisition":
        opp = opportunity(
            "opp_res",
            "residential_acquisition",
            [c1, c_assump],
            [e1, e2],
            requirements=("buy_and_hold",),
        )
        strats = [
            strategy(
                "res_wholesale",
                family="wholesale",
                thesis="assign contract to buyer after inspection",
                required=("c1",),
                assumptions=("c_a",),
                econ="0.7",
                next_action="order_inspection",
                capital="medium",
            ),
            strategy(
                "res_retail_flip",
                family="flip",
                thesis="renovate and list retail after contractor bids",
                required=("c1",),
                assumptions=("c_a",),
                econ="0.85",
                capital="high",
                reversibility="partially_reversible",
                failures=("cost_overrun", "dom_extension"),
                next_action="gather_contractor_bids",
                time_profile="slow",
            ),
            strategy(
                "res_walk",
                family="pass",
                thesis="pass due to thin equity",
                required=("c1",),
                econ="0.2",
                next_action="archive_opportunity",
                capital="low",
                time_profile="fast",
            ),
        ]
        return opp, strats

    if name == "vacant_land_lot_split":
        opp = opportunity("opp_land", "vacant_land", [c1], [e1, e2])
        strats = [
            strategy(
                "land_split",
                family="lot_split",
                thesis="plat split into two buildable lots",
                required=("c1",),
                econ="0.75",
                deps=("surveyor", "zoning"),
                avail=("surveyor", "zoning"),
                next_action="order_survey",
                capital="medium",
            ),
            strategy(
                "land_hold",
                family="hold",
                thesis="hold raw land for appreciation",
                required=("c1",),
                econ="0.4",
                next_action="tax_estimate_review",
                time_profile="slow",
            ),
        ]
        return opp, strats

    if name == "small_multifamily":
        opp = opportunity("opp_mf", "small_multifamily", [c1], [e1, e2])
        strats = [
            strategy(
                "mf_brrrr",
                family="brrrr",
                thesis="refinance after stabilization",
                required=("c1",),
                econ="0.8",
                capital="high",
                actions=("financial_commit", "research"),
                next_action="underwrite_rents",
            ),
            strategy(
                "mf_turnkey",
                family="turnkey",
                thesis="buy turnkey with existing tenants",
                required=("c1",),
                econ="0.65",
                next_action="request_rent_roll",
            ),
        ]
        return opp, strats

    if name == "business_acquisition":
        opp = opportunity("opp_biz", "business_acquisition", [c1], [e1, e2])
        strats = [
            strategy(
                "biz_asset",
                family="asset_purchase",
                thesis="asset purchase with seller financing",
                required=("c1",),
                econ="0.7",
                actions=("financial_commit",),
                next_action="request_financials",
                capital="high",
            ),
            strategy(
                "biz_pass",
                family="pass",
                thesis="pass on concentration risk",
                required=("c1",),
                econ="0.25",
                next_action="decline_politely",
            ),
        ]
        return opp, strats

    if name == "estate_sale_signal":
        from recipe_engine.adapters.estate_sales import EstateSaleAdapter

        adapter = EstateSaleAdapter()
        raw = {
            "listing_id": "4937331",
            "url": "https://www.estatesales.net/IN/Hammond/46324/4937331",
            "title": "Fixture Estate Sale",
            "city": "Hammond",
            "state": "IN",
            "county_slug": "report-only:lake-in",
            "payload": {"@type": "SaleEvent", "url": "https://www.estatesales.net/IN/Hammond/46324/4937331"},
        }
        opp = adapter.to_opportunity(raw)
        return opp, list(adapter.candidate_strategies(opp, raw))

    if name == "non_re_ops":
        opp = opportunity(
            "opp_ops",
            "operations_efficiency",
            [c1],
            [e1, e2],
            requirements=("ops_improve",),
        )
        strats = [
            strategy(
                "ops_automate",
                family="automation",
                thesis="automate intake triage with rule engine",
                required=("c1",),
                econ="0.6",
                next_action="pilot_rule_set",
            ),
            strategy(
                "ops_hire",
                family="staffing",
                thesis="hire contractor for backlog clearance",
                required=("c1",),
                econ="0.45",
                next_action="draft_scope",
                capital="medium",
            ),
        ]
        return opp, strats

    raise ValueError(f"unknown domain pack: {name}")
