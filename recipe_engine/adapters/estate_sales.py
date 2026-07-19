"""
Estate-sale adapter: fixture-oriented compatibility wrapper.

Does NOT query DB, call estatesales.net, or call LandOS.
Preserves canon keys: listing_id, parcel_apn=estate-{id}, owner_hash=sha256(id).
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any, Sequence

from recipe_engine.autonomy.hashing import content_hash
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


def canon_keys(listing_id: str) -> dict[str, str]:
    """Mirror recipe_engine.parse.canon_keys without importing parse (keeps pure path optional)."""
    return {
        "parcel_apn": f"estate-{listing_id}",
        "owner_hash": hashlib.sha256(listing_id.encode("utf-8")).hexdigest(),
    }


class EstateSaleAdapter:
    domain = "estate_sale_signal"

    def to_opportunity(self, raw: dict[str, Any]) -> OpportunityEnvelope:
        listing_id = str(raw["listing_id"])
        keys = canon_keys(listing_id)
        county = str(raw.get("county_slug") or "report-only:unknown")
        report_only = county.startswith("report-only") or bool(raw.get("report_only"))
        pointer = f"fixture:estate_sale:{listing_id}"
        payload = raw.get("payload") or raw
        ev_hash = content_hash(payload)
        evidence = EvidenceReference(
            schema_version=SV_EVIDENCE,
            evidence_id=f"ev_estate_{listing_id}",
            source_type="json_ld_sale_event",
            internal_pointer=pointer,
            observed_at=str(raw.get("observed_at") or "2026-06-17T00:00:00Z"),
            content_hash=ev_hash,
            provenance_chain_id=f"prov_estate_{listing_id}",
            sensitivity=Sensitivity.INTERNAL,
            freshness="current",
            authority_level=AuthorityLevel.OBSERVED,
            supported_claim_ids=(f"cl_listing_{listing_id}",),
            conflict_ids=(),
        )
        claim = Claim(
            schema_version=SV_CLAIM,
            claim_id=f"cl_listing_{listing_id}",
            statement=f"Estate sale listing {listing_id} observed via JSON-LD SaleEvent",
            classification=ClaimClass.OBSERVED,
            evidence_refs=(evidence.evidence_id,),
            confidence=Decimal("0.85"),
            source_confidence=Decimal("0.8"),
            conflict_state=ConflictState.NONE,
            verification_requirements=("territory_gate",),
            freshness="current",
            expires_at=None,
            creator="adapter:estate_sales",
            version="1",
            critical=False,
        )
        return OpportunityEnvelope(
            schema_version=SV_OPPORTUNITY,
            opportunity_id=f"opp_estate_{listing_id}",
            domain=self.domain,
            territory={
                "county_slug": county,
                "city": raw.get("city"),
                "state": raw.get("state"),
                "report_only": report_only,
            },
            subject={
                "listing_id": listing_id,
                "parcel_apn": keys["parcel_apn"],
                "owner_hash": keys["owner_hash"],
                "url": raw.get("url"),
                "title": raw.get("title"),
            },
            assets=(),
            desired_outcome="evaluate_signal_for_downstream_domain_organs",
            constraints=("no_unverified_distress_as_canon", "no_engine_invented_valuation"),
            authority_context={
                "sensitivity": "internal",
                "allowed_domains": [self.domain, "residential_acquisition"],
            },
            time_horizon=str(raw.get("time_horizon") or "days"),
            claims=(claim,),
            evidence=(evidence,),
            evidence_completeness=Decimal("0.55"),
            supplied_financial_inputs=(),  # never invent valuation
            risk_tolerance="medium",
            operator_requirements=("signal_review",),
            version="1",
            extensions={"report_only": report_only, "source": "estatesales.net"},
        )

    def candidate_strategies(
        self, opportunity: OpportunityEnvelope, raw: dict[str, Any] | None = None
    ) -> Sequence[CandidateStrategy]:
        """Domain organ supplies strategies; adapter provides non-executing examples only."""
        lid = opportunity.subject.get("listing_id", "unknown")
        report_only = bool(opportunity.territory.get("report_only"))
        return (
            CandidateStrategy(
                schema_version=SV_STRATEGY,
                strategy_id=f"str_signal_review_{lid}",
                title="Signal review only",
                thesis="Treat estate sale as unverified signal; queue human/domain review without fabricating distress.",
                strategy_family="signal_review",
                required_claim_ids=tuple(c.claim_id for c in opportunity.claims),
                assumption_claim_ids=(),
                expected_outcomes=(
                    MetricValue(
                        schema_version=SV_METRIC,
                        metric_id="econ.attractiveness",
                        value=Decimal("0.3"),
                        unit="score",
                        scale="0..1",
                        source="domain_supplied",
                        label="signal_attractiveness_placeholder",
                    ),
                ),
                resources=("analyst_time",),
                dependencies=("review_queue",),
                duration="1d",
                reversibility="reversible",
                failure_modes=("false_positive_signal",),
                legal_policy_checks=("no_unsolicited_contact_without_authority",),
                governance_class="standard",
                next_safe_action="queue_for_domain_review",
                stop_conditions=("operator_stop", "duplicate_listing"),
                evidence_gaps=("owner_verification", "parcel_match"),
                action_classes=("research",),
                capital_intensity="low",
                time_profile="fast",
                available_dependencies=("review_queue",),
                missing_dependencies=(),
                feasibility_notes=() if not report_only else ("report_only_territory",),
                prohibited=False,
                policy_known=True,
                stages_hint=(),
                extensions={"report_only": report_only},
            ),
        )
