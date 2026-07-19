"""Contract validation tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.autonomy.config import DEFAULT_WEIGHTS, EngineConfig, validate_config
from recipe_engine.autonomy.errors import ConfigError, ValidationError
from recipe_engine.autonomy.models import (
    AuthorityLevel,
    ClaimClass,
    MetricValue,
    OpportunityEnvelope,
    SV_METRIC,
)
from recipe_engine.autonomy.validation import (
    validate_candidates,
    validate_claim,
    validate_evidence,
    validate_metric,
    validate_opportunity,
)
from tests.autonomy.fixtures.builders import claim, evidence, opportunity, strategy


class ValidationTests(unittest.TestCase):
    def test_invalid_id(self):
        e = evidence("bad id!")
        with self.assertRaises(ValidationError):
            validate_evidence(e)

    def test_unknown_authority_fails_closed(self):
        e = evidence("ev_x", authority=AuthorityLevel.UNKNOWN)
        with self.assertRaises(ValidationError):
            validate_evidence(e)

    def test_observed_requires_evidence(self):
        c = claim("c1", classification=ClaimClass.OBSERVED, evidence_refs=())
        with self.assertRaises(ValidationError):
            validate_claim(c, {})

    def test_model_cannot_self_promote_corroborated(self):
        e1 = evidence("ev1")
        e2 = evidence("ev2", source_type="other")
        c = claim(
            "c1",
            classification=ClaimClass.CORROBORATED,
            evidence_refs=("ev1", "ev2"),
            creator="model:gpt",
        )
        with self.assertRaises(ValidationError):
            validate_claim(c, {"ev1": e1, "ev2": e2})

    def test_corroborated_needs_two_sources(self):
        e1 = evidence("ev1")
        c = claim(
            "c1",
            classification=ClaimClass.CORROBORATED,
            evidence_refs=("ev1",),
        )
        with self.assertRaises(ValidationError):
            validate_claim(c, {"ev1": e1})

    def test_nan_weight_rejected(self):
        w = dict(DEFAULT_WEIGHTS)
        with self.assertRaises(ConfigError):
            validate_config(EngineConfig(weights={**w, "confidence": float("nan")}))

    def test_negative_weight_rejected(self):
        w = dict(DEFAULT_WEIGHTS)
        w["confidence"] = Decimal("-1")
        with self.assertRaises(ConfigError):
            validate_config(EngineConfig(weights=w))

    def test_duplicate_strategy_ids(self):
        s = strategy("s1")
        with self.assertRaises(ValidationError):
            validate_candidates([s, s], max_candidates=10)

    def test_assumption_not_observed_silently(self):
        c = claim("a1", classification=ClaimClass.ASSUMPTION, evidence_refs=())
        out = validate_claim(c, {})
        self.assertEqual(out.classification, ClaimClass.ASSUMPTION)

    def test_expired_evidence_allowed_with_known_authority(self):
        e1 = evidence("ev1", freshness="expired")
        validate_evidence(e1)

    def test_engine_invented_metric_rejected(self):
        m = MetricValue(
            schema_version=SV_METRIC,
            metric_id="econ.x",
            value=Decimal("1"),
            unit="usd",
            scale="absolute",
            source="engine_invented",
            label="x",
        )
        with self.assertRaises(ValidationError):
            validate_metric(m)

    def test_unsupported_schema_version(self):
        e1 = evidence("ev1")
        c1 = claim("c1", evidence_refs=("ev1",))
        opp = opportunity("o1", "d", [c1], [e1])
        bad2 = OpportunityEnvelope(
            schema_version="wrong",
            opportunity_id=opp.opportunity_id,
            domain=opp.domain,
            territory=opp.territory,
            subject=opp.subject,
            assets=opp.assets,
            desired_outcome=opp.desired_outcome,
            constraints=opp.constraints,
            authority_context=opp.authority_context,
            time_horizon=opp.time_horizon,
            claims=opp.claims,
            evidence=opp.evidence,
            evidence_completeness=opp.evidence_completeness,
            supplied_financial_inputs=opp.supplied_financial_inputs,
            risk_tolerance=opp.risk_tolerance,
            operator_requirements=opp.operator_requirements,
            version=opp.version,
        )
        with self.assertRaises(ValidationError):
            validate_opportunity(bad2, max_claims=10, max_evidence=10)


if __name__ == "__main__":
    unittest.main()
