"""Evaluations pin their version; decisions name their authority."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from recipe_engine.autonomy.models import AuthorityLevel
from recipe_engine.closed_loop.decision import (
    Alternative, AuthorityRef, DeclaredRationale, DecisionDisposition, DecisionError,
    DecisionRecord, GovernanceVerdict,
)
from recipe_engine.closed_loop.recipe_eval import (
    GRADUATION_TO_QUALIFICATION, QualificationState, RecipeEvalError,
    RecipeEvaluationRecord, RecipeRef, ScoreComponent, from_graduation_verdict,
)
from recipe_engine.graduation import (
    GraduationState, PropertyIdentity, SignalEvidence, classify,
)


def authority() -> AuthorityRef:
    return AuthorityRef("auth:desk", "operator", "founder", "ledger://authority/desk")


def evaluation(**overrides) -> RecipeEvaluationRecord:
    kwargs = dict(
        evaluation_id="eval-1",
        recipe=RecipeRef.of("graduation", "graduation.v1", {"floor": 55}),
        subject_id="subject-1", candidate_id="cand-1", inputs={"score": 71},
        input_evidence_digests=("a" * 64,), score=Decimal("71"),
        components=(ScoreComponent("distress", Decimal("40"), Decimal("0.5")),),
        qualification=QualificationState.QUALIFIED,
        reason_codes=("corroborated",), evaluated_at="2026-08-19T00:00:00Z")
    kwargs.update(overrides)
    return RecipeEvaluationRecord(**kwargs)


class TestRecipeRef:
    def test_key_includes_the_version_so_two_versions_are_two_things(self):
        assert RecipeRef.of("graduation", "v1", {}).key == "graduation@v1"
        assert RecipeRef.of("graduation", "v1", {}).key != RecipeRef.of("graduation", "v2", {}).key

    def test_config_digest_changes_with_the_config(self):
        assert (RecipeRef.of("g", "v1", {"floor": 55}).config_digest
                != RecipeRef.of("g", "v1", {"floor": 40}).config_digest)


class TestEvaluationRecord:
    def test_a_verdict_must_state_a_reason(self):
        with pytest.raises(RecipeEvalError, match="reason code"):
            evaluation(reason_codes=())

    def test_a_score_without_components_is_refused(self):
        with pytest.raises(RecipeEvalError, match="unauditable"):
            evaluation(components=())

    def test_a_qualified_candidate_must_name_its_evidence(self):
        with pytest.raises(RecipeEvalError, match="must name the evidence"):
            evaluation(input_evidence_digests=())

    def test_an_unqualified_evaluation_needs_no_evidence_digests(self):
        record = evaluation(qualification=QualificationState.NOT_QUALIFIED,
                            input_evidence_digests=(), score=None, components=())
        assert record.qualification is QualificationState.NOT_QUALIFIED


class TestGraduationAdapter:
    def test_every_graduation_state_is_mapped(self):
        assert {s.value for s in GraduationState} == set(GRADUATION_TO_QUALIFICATION)

    def test_a_real_classifier_verdict_lifts_and_pins_its_policy_version(self):
        now = datetime(2026, 8, 19, tzinfo=timezone.utc)
        verdict = classify(
            PropertyIdentity(parcel_apn="000123", county_fips="37021",
                             apn_is_real_parcel=True, address_verified=True,
                             identity_source="county_parcel_layer",
                             parcel_provenance="layer://37021 match=1"),
            [SignalEvidence("county_tax_roll", "tax_distress",
                            AuthorityLevel.DOMAIN_AUTHORITY, "current",
                            "2026-06-01T00:00:00Z")],
            opportunity_score=71, score_provenance="nc_dps.v3", now=now)
        record = from_graduation_verdict(
            verdict, evaluation_id="eval-1", subject_id="subject-1",
            candidate_id="cand-1", inputs={"score": 71},
            input_evidence_digests=("a" * 64,), evaluated_at="2026-08-19T00:00:00Z")
        assert record.qualification is QualificationState.QUALIFIED
        assert record.recipe.version == verdict.policy_version
        assert record.recipe.config_digest == verdict.evidence_hash

    def test_an_expired_verdict_lifts_as_expired(self):
        now = datetime(2026, 8, 19, tzinfo=timezone.utc)
        verdict = classify(
            PropertyIdentity("000123", "37021", True, True, "src", "prov"),
            [SignalEvidence("county_tax_roll", "tax_distress",
                            AuthorityLevel.DOMAIN_AUTHORITY, "current",
                            "2020-01-01T00:00:00Z")],
            opportunity_score=71, score_provenance="nc_dps.v3", now=now)
        record = from_graduation_verdict(
            verdict, evaluation_id="e", subject_id="s", candidate_id="c",
            inputs={}, input_evidence_digests=(), evaluated_at="2026-08-19T00:00:00Z")
        assert record.qualification is QualificationState.EXPIRED


class TestDecisionRecord:
    def decision(self, **overrides) -> DecisionRecord:
        kwargs = dict(
            decision_id="dec-1", subject_id="subject-1", opportunity_id="opp-1",
            recipe_evaluation_digest=evaluation().digest, evidence_ids=("ev-1",),
            disposition=DecisionDisposition.APPROVED,
            rationale=DeclaredRationale(("qualified",), {"score": 71}),
            governance_verdict=GovernanceVerdict.ALLOW, authority=authority(),
            alternatives=(), alternatives_declared=False,
            decided_at="2026-08-19T00:00:00Z")
        kwargs.update(overrides)
        return DecisionRecord(**kwargs)

    def test_an_approved_decision_must_name_its_authority(self):
        with pytest.raises(DecisionError, match="must name the authority"):
            self.decision(authority=None)

    def test_governance_blocked_cannot_be_recorded_as_approved(self):
        with pytest.raises(DecisionError, match="cannot be recorded as approved"):
            self.decision(governance_verdict=GovernanceVerdict.BLOCKED)

    def test_authority_fields_may_not_be_blank(self):
        with pytest.raises(DecisionError, match="never inferred"):
            AuthorityRef("auth", "operator", "", "ref")

    def test_rationale_rejects_prose_masquerading_as_a_feature(self):
        with pytest.raises(DecisionError, match="prose, not a declared value"):
            DeclaredRationale(("qualified",), {"analysis": "x" * 600})

    def test_rationale_rejects_chain_of_thought_features(self):
        with pytest.raises(DecisionError, match="un-declared model reasoning"):
            DeclaredRationale(("qualified",), {"chain_of_thought": "…"})

    def test_alternatives_must_be_declared_artifacts(self):
        alternative = Alternative("alt-1", "wait for probate", "b" * 64, "slower")
        record = self.decision(alternatives=(alternative,), alternatives_declared=True)
        assert record.alternatives[0].artifact_digest == "b" * 64

    def test_claiming_deliberation_with_no_alternatives_is_refused(self):
        with pytest.raises(DecisionError, match="rather than implying deliberation"):
            self.decision(alternatives=(), alternatives_declared=True)

    def test_the_digest_is_the_receipt_the_outcome_binds_back_to(self):
        first = self.decision()
        second = self.decision(decided_at="2026-08-20T00:00:00Z")
        assert first.digest != second.digest
        assert len(first.digest) == 64
