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

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Sequence

from .autonomy.models import AuthorityLevel, ConflictState

#: Ratified by the founder 2026-08-19. Every verdict carries it so a stored
#: classification can never be silently re-read under different thresholds.
POLICY_VERSION = "graduation.v1"


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
    freshness: str                    # caller's label — ADVISORY ONLY, see age_days
    observed_at: str                  # ISO8601; the age gate is computed from this

    def age_days(self, now: datetime | None = None) -> int | None:
        """Actual age from observed_at. None when the timestamp is unusable —
        which is missing data, and missing data is never a pass.

        The caller's `freshness` label is deliberately NOT trusted here: a
        producer that mislabels stale evidence "current" must not be able to
        talk its way past the age gate.
        """
        raw = (self.observed_at or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (( now or datetime.now(timezone.utc)) - parsed).days

    def provenance_gaps(self) -> tuple[str, ...]:
        """What this piece of evidence fails to say about itself."""
        gaps = []
        if not (self.source_type or "").strip():
            gaps.append("evidence.source_type")
        if not (self.family or "").strip():
            gaps.append("evidence.family")
        if not isinstance(self.authority_level, AuthorityLevel):
            gaps.append("evidence.authority_level")
        if self.age_days() is None:
            gaps.append("evidence.observed_at")
        return tuple(gaps)


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
    #: WHO says this is the parcel, and HOW. A real APN with no stated resolver
    #: is an assertion, not a provenance chain.
    identity_source: str | None = None       # e.g. "berrien_parcels_spatial_intersect"
    parcel_provenance: str | None = None     # e.g. layer URL + match count

    def provenance_gaps(self) -> tuple[str, ...]:
        gaps = []
        if not (self.county_fips or "").strip():
            gaps.append("identity.county_fips")
        if self.apn_is_real_parcel:
            if not (self.parcel_apn or "").strip():
                gaps.append("identity.parcel_apn")
            if not (self.identity_source or "").strip():
                gaps.append("identity.identity_source")
            if not (self.parcel_provenance or "").strip():
                gaps.append("identity.parcel_provenance")
        return tuple(gaps)


@dataclass(frozen=True)
class GraduationPolicy:
    """PROPOSED thresholds. Founder-owned; nothing promotes until these are
    explicitly adopted."""
    min_corroborating_sources: int = 2          # or one AUTHORITATIVE source
    require_real_parcel_identity: bool = True   # research needs a real property
    require_distress_for_candidate: bool = True
    #: 55, not 40. The nc_dps band leaves 45-54 legitimately research/corroborated;
    #: candidate eligibility sits above it. Existing nc_dps scoring is untouched.
    min_opportunity_score: int = 55
    #: INCLUSIVE: age_days <= 365 is current, 366 is expired. 364 passes, 365 is
    #: the last day that passes, 366 blocks — stated because a boundary that is
    #: only implied gets re-argued later.
    max_evidence_age_days: int = 365
    conflict_blocks_promotion: bool = True
    missing_data_is_never_a_pass: bool = True
    require_complete_provenance: bool = True


@dataclass(frozen=True)
class GraduationVerdict:
    state: GraduationState
    why: str
    missing: tuple[str, ...] = ()
    would_promote_to_research: bool = False
    would_promote_to_candidate: bool = False
    corroborating_sources: int = 0
    distinct_families: tuple[str, ...] = field(default_factory=tuple)
    policy_version: str = POLICY_VERSION
    #: Hash of the exact inputs this verdict was computed from, so a stored
    #: classification can be re-derived and audited rather than trusted.
    evidence_hash: str = ""
    provenance_complete: bool = False
    evidence_ages_days: tuple[int | None, ...] = field(default_factory=tuple)


def _distinct_sources(evidence: Sequence[SignalEvidence]) -> int:
    """The corroboration count validation.py already defines: distinct source
    types, with any authoritative source counting as sufficient on its own."""
    if any(e.authority_level in AUTHORITATIVE for e in evidence):
        return max(1, len({e.source_type for e in evidence}))
    return len({e.source_type for e in evidence})


def evidence_hash(identity: PropertyIdentity, evidence: Sequence[SignalEvidence],
                  opportunity_score: int | None, policy: GraduationPolicy) -> str:
    """Stable digest of exactly what a verdict was computed from, so a stored
    classification can be re-derived instead of trusted."""
    payload = {
        "policy_version": POLICY_VERSION,
        "policy": {k: (v if not isinstance(v, bool) else bool(v))
                   for k, v in sorted(policy.__dict__.items())},
        "identity": {"parcel_apn": identity.parcel_apn, "county_fips": identity.county_fips,
                     "apn_is_real_parcel": identity.apn_is_real_parcel,
                     "address_verified": identity.address_verified,
                     "identity_source": identity.identity_source,
                     "parcel_provenance": identity.parcel_provenance},
        "score": opportunity_score,
        "evidence": sorted(
            [{"source_type": e.source_type, "family": e.family,
              "authority_level": e.authority_level.value if isinstance(e.authority_level, AuthorityLevel)
              else str(e.authority_level),
              "observed_at": e.observed_at} for e in evidence],
            key=lambda d: (d["source_type"], d["family"], d["observed_at"])),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def classify(
    identity: PropertyIdentity,
    evidence: Sequence[SignalEvidence],
    *,
    conflict: ConflictState = ConflictState.NONE,
    opportunity_score: int | None = None,
    score_provenance: str | None = None,
    policy: GraduationPolicy | None = None,
    now: datetime | None = None,
) -> GraduationVerdict:
    """Deterministic state for one property. No I/O, no promotion."""
    policy = policy or GraduationPolicy()
    now = now or datetime.now(timezone.utc)
    digest = evidence_hash(identity, evidence, opportunity_score, policy)
    ages = tuple(e.age_days(now) for e in evidence)

    def verdict(state, why, missing=(), research=False, candidate=False,
                sources=0, families=(), provenance_ok=False):
        return GraduationVerdict(
            state=state, why=why, missing=tuple(missing),
            would_promote_to_research=research, would_promote_to_candidate=candidate,
            corroborating_sources=sources, distinct_families=tuple(families),
            policy_version=POLICY_VERSION, evidence_hash=digest,
            provenance_complete=provenance_ok, evidence_ages_days=ages)

    if not evidence:
        return verdict(GraduationState.OBSERVED_SIGNAL, "no evidence attached", ("any_evidence",))

    # THE AGE GATE, computed from observed_at — never from the caller's label.
    # An unparseable timestamp is missing data, so that piece cannot support
    # anything; it is dropped from the count AND named in `missing`.
    current, expired, undated = [], [], []
    for item in evidence:
        age = item.age_days(now)
        if age is None:
            undated.append(item)
        elif age > policy.max_evidence_age_days:
            expired.append(item)
        else:
            current.append(item)

    families = tuple(sorted({e.family for e in current}))
    sources = _distinct_sources(current)

    # Provenance is a gate, not a nicety: a claim that cannot say where it came
    # from cannot support a candidate.
    gaps = list(identity.provenance_gaps())
    for item in evidence:
        gaps.extend(item.provenance_gaps())
    if opportunity_score is not None and not (score_provenance or "").strip():
        gaps.append("score.provenance")
    provenance_ok = not gaps

    if policy.conflict_blocks_promotion and conflict == ConflictState.OPEN:
        return verdict(GraduationState.CONFLICT,
                       "identity conflict is open; no promotion while disputed",
                       ("conflict_resolution",), sources=sources, families=families,
                       provenance_ok=provenance_ok)

    if not current:
        why = ("every piece of evidence is older than "
               f"{policy.max_evidence_age_days} days"
               if expired and not undated else
               "no evidence carries a usable observed_at")
        return verdict(GraduationState.REJECTED_EXPIRED, why,
                       ("fresh_evidence",) + (("evidence.observed_at",) if undated else ()),
                       sources=sources, families=families, provenance_ok=provenance_ok)

    if policy.require_real_parcel_identity and not identity.apn_is_real_parcel:
        return verdict(GraduationState.OBSERVED_SIGNAL,
                       "signal is real but is not yet tied to a real parcel identity",
                       ("real_parcel_apn",), sources=sources, families=families,
                       provenance_ok=provenance_ok)

    # RESEARCH also requires provenance: an unsourced identity is an assertion.
    if policy.require_complete_provenance and gaps:
        return verdict(GraduationState.OBSERVED_SIGNAL,
                       "identity or evidence cannot state its own provenance",
                       sorted(set(gaps)), sources=sources, families=families,
                       provenance_ok=False)

    authoritative = any(e.authority_level in AUTHORITATIVE for e in current)
    corroborated = authoritative or sources >= policy.min_corroborating_sources

    if not corroborated:
        return verdict(GraduationState.RESEARCH_PROPERTY,
                       "real property with one current non-authoritative source — "
                       "research, not corroborated",
                       ("second_distinct_source",), research=True,
                       sources=sources, families=families, provenance_ok=provenance_ok)

    missing: list[str] = []
    if policy.require_distress_for_candidate and not (DISTRESS_FAMILIES & set(families)):
        missing.append("distress_evidence")
    if policy.min_opportunity_score is not None:
        if opportunity_score is None:
            missing.append("opportunity_score")
        elif opportunity_score < policy.min_opportunity_score:
            missing.append(f"opportunity_score>={policy.min_opportunity_score}")

    if missing:
        return verdict(GraduationState.CORROBORATED,
                       "corroborated, but not everything a candidate requires is present",
                       missing, research=True, sources=sources, families=families,
                       provenance_ok=provenance_ok)

    return verdict(GraduationState.QUALIFIED_CANDIDATE,
                   "corroborated, distressed, scored, in-date, unconflicted and fully sourced",
                   (), research=True, candidate=True,
                   sources=sources, families=families, provenance_ok=provenance_ok)
