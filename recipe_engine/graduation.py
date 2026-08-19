"""
graduation.py — the governed bridge from a real observed signal to a candidate.

    SIGNAL -> RESEARCH PROPERTY -> CORROBORATED -> QUALIFIED CANDIDATE

WHY THIS EXISTS. The 2026-08-18 run put nine real in-territory estate-sale
events through the engine. They are SIGNALS. `landos.identity_nodes` holds
(tenant, parcel_apn, owner_hash, county, state, status) and no score, no
distress, no opportunity — the engine records IDENTITY, not qualification — and
`engine.py` sends every distress/owner/arbitrage input as unknown because an
estate sale is not verified probate. Minting them as candidates would be
fabricated qualification.

NOTHING NEW IS SCORED HERE. This module composes vocabularies that already
exist rather than adding a second scoring system:

  * recipe_engine.autonomy.models — ClaimClass, AuthorityLevel, ConflictState
  * recipe_engine.autonomy.validation — the standing corroboration rule:
    "corroborated claims require two distinct sources or authoritative source",
    and "model-originated claims cannot self-promote to corroborated"
  * nc_dps_wave_a_pipeline — the domain's own evidence families: ownership,
    court, title, probate, vacancy, entity, tax distress, absentee
  * the estate's own candidate classes: RESEARCH_CANDIDATE, QUALIFIED_CANDIDATE

It is PURE and deterministic: same evidence in, same state out, no I/O, so a
replay classifies identically. It PROPOSES policy; it does not promote anything.
Production candidate status is a founder gate and this module never writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from .autonomy.models import AuthorityLevel, ConflictState


class GraduationState(str, Enum):
    """Where a property stands. Deliberately parallel to the estate's own
    candidate classes so nothing has to be translated twice."""
    OBSERVED_SIGNAL = "observed_signal"          # something happened here
    RESEARCH_PROPERTY = "research_property"      # -> estate RESEARCH_CANDIDATE
    CORROBORATED = "corroborated"                # two sources, or one authority
    QUALIFIED_CANDIDATE = "qualified_candidate"  # -> estate QUALIFIED_CANDIDATE
    CONFLICT = "conflict"                        # identity disputed; no promotion
    REJECTED_EXPIRED = "rejected_expired"        # evidence aged out


#: Evidence families the domain already recognises (nc_dps_wave_a_pipeline).
#: An estate sale is a LIFE-EVENT signal — suggestive of a sale, never itself
#: proof of distress or of probate.
DISTRESS_FAMILIES = frozenset({"tax_distress", "court", "foreclosure", "lien", "probate"})
OWNERSHIP_FAMILIES = frozenset({"ownership", "title", "entity", "absentee"})
LIFE_EVENT_FAMILIES = frozenset({"estate_sale", "obituary", "vacancy"})

#: Authority at or above this level lets ONE source corroborate on its own —
#: the existing validation.py rule, named here rather than re-derived.
AUTHORITATIVE = frozenset({
    AuthorityLevel.DOMAIN_AUTHORITY, AuthorityLevel.OPERATOR, AuthorityLevel.GOVERNANCE,
})


@dataclass(frozen=True)
class SignalEvidence:
    """One piece of evidence about one property."""
    source_type: str                  # e.g. "estatesales.net", "county_tax_roll"
    family: str                       # one of the families above
    authority_level: AuthorityLevel
    freshness: str                    # current | stale | expired
    observed_at: str


@dataclass(frozen=True)
class PropertyIdentity:
    """How well we know WHICH property this is."""
    parcel_apn: str | None
    county_fips: str | None
    #: True only when parcel_apn is a real county parcel identifier. The engine's
    #: `estate-<listing_id>` key is a stable idempotency anchor for the SALE, not
    #: a parcel — treating it as one is how a listing becomes a fake property.
    apn_is_real_parcel: bool
    address_verified: bool


@dataclass(frozen=True)
class GraduationPolicy:
    """PROPOSED thresholds. Founder-owned; nothing promotes until these are
    explicitly adopted."""
    min_corroborating_sources: int = 2          # or one AUTHORITATIVE source
    require_real_parcel_identity: bool = True   # research needs a real property
    require_distress_for_candidate: bool = True
    min_opportunity_score: int = 40             # matches nc_dps priority >= 45 band, held lower
    max_evidence_age_days: int = 365
    conflict_blocks_promotion: bool = True
    missing_data_is_never_a_pass: bool = True


@dataclass(frozen=True)
class GraduationVerdict:
    state: GraduationState
    why: str
    missing: tuple[str, ...] = ()
    would_promote_to_research: bool = False
    would_promote_to_candidate: bool = False
    corroborating_sources: int = 0
    distinct_families: tuple[str, ...] = field(default_factory=tuple)


def _distinct_sources(evidence: Sequence[SignalEvidence]) -> int:
    """The corroboration count validation.py already defines: distinct source
    types, with any authoritative source counting as sufficient on its own."""
    if any(e.authority_level in AUTHORITATIVE for e in evidence):
        return max(1, len({e.source_type for e in evidence}))
    return len({e.source_type for e in evidence})


def classify(
    identity: PropertyIdentity,
    evidence: Sequence[SignalEvidence],
    *,
    conflict: ConflictState = ConflictState.NONE,
    opportunity_score: int | None = None,
    policy: GraduationPolicy | None = None,
) -> GraduationVerdict:
    """Deterministic state for one property. No I/O, no promotion."""
    policy = policy or GraduationPolicy()
    families = tuple(sorted({e.family for e in evidence}))
    sources = _distinct_sources(evidence)

    if not evidence:
        return GraduationVerdict(GraduationState.OBSERVED_SIGNAL,
                                 "no evidence attached", ("any_evidence",))

    # Order matters: a disputed identity and dead evidence both outrank anything
    # the evidence might otherwise support.
    if policy.conflict_blocks_promotion and conflict == ConflictState.OPEN:
        return GraduationVerdict(GraduationState.CONFLICT,
                                 "identity conflict is open; no promotion while disputed",
                                 ("conflict_resolution",), corroborating_sources=sources,
                                 distinct_families=families)

    if evidence and all(e.freshness == "expired" for e in evidence):
        return GraduationVerdict(GraduationState.REJECTED_EXPIRED,
                                 "every piece of evidence has expired",
                                 ("fresh_evidence",), corroborating_sources=sources,
                                 distinct_families=families)

    # RESEARCH needs to know WHICH property. A synthetic listing key is not one.
    if policy.require_real_parcel_identity and not identity.apn_is_real_parcel:
        return GraduationVerdict(
            GraduationState.OBSERVED_SIGNAL,
            "signal is real but is not yet tied to a real parcel identity",
            ("real_parcel_apn",), corroborating_sources=sources, distinct_families=families)

    authoritative = any(e.authority_level in AUTHORITATIVE for e in evidence)
    corroborated = authoritative or sources >= policy.min_corroborating_sources
    has_distress = bool(DISTRESS_FAMILIES & set(families))

    if not corroborated:
        return GraduationVerdict(
            GraduationState.RESEARCH_PROPERTY,
            "real property with one non-authoritative source — research, not corroborated",
            ("second_distinct_source",), would_promote_to_research=True,
            corroborating_sources=sources, distinct_families=families)

    missing: list[str] = []
    if policy.require_distress_for_candidate and not has_distress:
        missing.append("distress_evidence")
    if policy.min_opportunity_score is not None:
        if opportunity_score is None:
            missing.append("opportunity_score")
        elif opportunity_score < policy.min_opportunity_score:
            missing.append(f"opportunity_score>={policy.min_opportunity_score}")

    if missing:
        return GraduationVerdict(
            GraduationState.CORROBORATED,
            "corroborated, but not everything a candidate requires is present",
            tuple(missing), would_promote_to_research=True,
            corroborating_sources=sources, distinct_families=families)

    return GraduationVerdict(
        GraduationState.QUALIFIED_CANDIDATE,
        "corroborated, distressed, scored, unexpired and unconflicted",
        (), would_promote_to_research=True, would_promote_to_candidate=True,
        corroborating_sources=sources, distinct_families=families)
