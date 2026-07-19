"""Evidence-backed claims from fixture reads."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from recipe_engine.autonomy.models import (
    Claim, ClaimClass, ConflictState, EvidenceReference, Sensitivity, AuthorityLevel,
    SV_CLAIM, SV_EVIDENCE,
)


def build_claims_from_fixtures(
    *,
    health: dict[str, Any],
    repos: list[dict[str, Any]],
    observed_at: str,
) -> tuple[list[Claim], list[EvidenceReference]]:
    evidence: list[EvidenceReference] = []
    claims: list[Claim] = []

    eh = EvidenceReference(
        schema_version=SV_EVIDENCE,
        evidence_id="ev_mother_health",
        source_type="fixture_mother",
        internal_pointer="fixture://mother/health",
        observed_at=observed_at,
        content_hash=str(health.get("snapshot_id", "health")),
        provenance_chain_id="prov_mother_fixture",
        sensitivity=Sensitivity.INTERNAL,
        freshness="current",
        authority_level=AuthorityLevel.OBSERVED,
        supported_claim_ids=("claim_overall_health", "claim_blocked"),
    )
    evidence.append(eh)
    claims.append(Claim(
        schema_version=SV_CLAIM,
        claim_id="claim_overall_health",
        statement=f"Organism overall health (fixture): {health.get('overall')}",
        classification=ClaimClass.OBSERVED,
        evidence_refs=("ev_mother_health",),
        confidence=Decimal("0.9"),
        source_confidence=Decimal("0.9"),
        conflict_state=ConflictState.NONE,
        verification_requirements=(),
        freshness="current",
        expires_at=None,
        creator="founder_runtime_p1",
        version="1",
        critical=True,
    ))
    blocked = health.get("blocked") or []
    degraded = [d["name"] for d in health.get("dependencies", []) if d.get("status") == "degraded"]
    claims.append(Claim(
        schema_version=SV_CLAIM,
        claim_id="claim_blocked",
        statement=f"Blocked={list(blocked)}; Degraded={degraded}",
        classification=ClaimClass.OBSERVED,
        evidence_refs=("ev_mother_health",),
        confidence=Decimal("0.9"),
        source_confidence=Decimal("0.9"),
        conflict_state=ConflictState.NONE,
        verification_requirements=(),
        freshness="current",
        expires_at=None,
        creator="founder_runtime_p1",
        version="1",
        critical=True,
    ))

    for i, repo in enumerate(repos):
        eid = f"ev_repo_{i}"
        evidence.append(EvidenceReference(
            schema_version=SV_EVIDENCE,
            evidence_id=eid,
            source_type="fixture_github",
            internal_pointer=f"fixture://github/{repo.get('repo')}",
            observed_at=observed_at,
            content_hash=str(repo.get("head_sha") or repo.get("repo")),
            provenance_chain_id="prov_github_fixture",
            sensitivity=Sensitivity.INTERNAL,
            freshness="current",
            authority_level=AuthorityLevel.OBSERVED,
            supported_claim_ids=(f"claim_repo_{i}",),
        ))
        claims.append(Claim(
            schema_version=SV_CLAIM,
            claim_id=f"claim_repo_{i}",
            statement=f"Repo {repo.get('repo')} head={repo.get('head_sha')} (fixture)",
            classification=ClaimClass.OBSERVED,
            evidence_refs=(eid,),
            confidence=Decimal("0.85"),
            source_confidence=Decimal("0.85"),
            conflict_state=ConflictState.NONE,
            verification_requirements=(),
            freshness="current",
            expires_at=None,
            creator="founder_runtime_p1",
            version="1",
            critical=False,
        ))
    return claims, evidence
