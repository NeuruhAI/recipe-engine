"""Attribution refuses to invent credit; calibration refuses to mutate anything."""
from __future__ import annotations

from decimal import Decimal

import pytest

from recipe_engine.closed_loop.attribution import (
    AttributionError, AttributionSet, AttributionStrength, Contribution,
    ContributorKind, LEARNABLE_STRENGTHS, strength_for,
)
from recipe_engine.closed_loop.calibration import (
    MIN_SAMPLES_FOR_CLAIM, CalibrationDimension, CalibrationError, CalibrationReport,
    ScoredPair, calibrate,
)


def contribution(**overrides) -> Contribution:
    kwargs = dict(contributor_kind=ContributorKind.ACTION, contributor_key="act-1",
                  strength=AttributionStrength.DIRECT, basis="recorded effect",
                  bound_digest="a" * 64)
    kwargs.update(overrides)
    return Contribution(**kwargs)


class TestAttributionGrading:
    def test_direct_requires_a_bound_digest(self):
        with pytest.raises(AttributionError, match="requires a bound_digest"):
            contribution(bound_digest=None)

    def test_supported_also_requires_a_bound_digest(self):
        with pytest.raises(AttributionError, match="requires a bound_digest"):
            contribution(strength=AttributionStrength.SUPPORTED, bound_digest=None)

    def test_associated_needs_no_binding(self):
        assert contribution(strength=AttributionStrength.ASSOCIATED,
                            bound_digest=None).strength is AttributionStrength.ASSOCIATED

    def test_every_contribution_states_a_basis(self):
        with pytest.raises(AttributionError, match="folklore"):
            contribution(basis="  ")

    def test_the_grading_rule_distinguishes_direct_from_supported(self):
        assert strength_for(bound_digest="d", other_sufficient_contributors=False,
                            dependency_demonstrated=True) is AttributionStrength.DIRECT
        assert strength_for(bound_digest="d", other_sufficient_contributors=True,
                            dependency_demonstrated=True) is AttributionStrength.SUPPORTED

    def test_no_demonstrated_dependency_caps_at_associated(self):
        assert strength_for(bound_digest="d", other_sufficient_contributors=False,
                            dependency_demonstrated=False) is AttributionStrength.ASSOCIATED

    def test_unplaceable_contributors_are_unknown(self):
        assert strength_for(bound_digest=None, other_sufficient_contributors=False,
                            dependency_demonstrated=False) is AttributionStrength.UNKNOWN


class TestShares:
    def test_a_share_requires_a_declared_allocation_basis(self):
        with pytest.raises(AttributionError, match="reads as a measurement"):
            contribution(share=Decimal("0.5"))

    def test_a_declared_basis_permits_a_share(self):
        assert contribution(share=Decimal("0.5"),
                            allocation_basis="operator time split").share == Decimal("0.5")

    def test_shares_may_not_exceed_one(self):
        with pytest.raises(AttributionError, match="exceed 1.0"):
            AttributionSet("attr-1", "out-1", "b" * 64,
                           (contribution(share=Decimal("0.7"), allocation_basis="t"),
                            contribution(contributor_key="act-2", share=Decimal("0.6"),
                                         allocation_basis="t")),
                           (), "2026-08-19T00:00:00Z")


class TestAttributionSet:
    def test_an_empty_set_is_refused_in_favour_of_a_recorded_unknown(self):
        with pytest.raises(AttributionError, match="record an UNKNOWN"):
            AttributionSet("attr-1", "out-1", "b" * 64, (), (), "2026-08-19T00:00:00Z")

    def test_only_direct_and_supported_are_learnable(self):
        assert LEARNABLE_STRENGTHS == {AttributionStrength.DIRECT,
                                       AttributionStrength.SUPPORTED}

    def test_learnable_excludes_associated_and_unknown(self):
        attribution = AttributionSet(
            "attr-1", "out-1", "b" * 64,
            (contribution(),
             contribution(contributor_key="src-1", contributor_kind=ContributorKind.SOURCE,
                          strength=AttributionStrength.ASSOCIATED, bound_digest=None),
             contribution(contributor_key="src-2", contributor_kind=ContributorKind.SOURCE,
                          strength=AttributionStrength.UNKNOWN, bound_digest=None)),
            ("what closed the deal",), "2026-08-19T00:00:00Z")
        assert [c.contributor_key for c in attribution.learnable()] == ["act-1"]

    def test_multiple_contributors_of_one_kind_are_supported(self):
        attribution = AttributionSet(
            "attr-1", "out-1", "b" * 64,
            (contribution(contributor_kind=ContributorKind.SOURCE, contributor_key="s1"),
             contribution(contributor_kind=ContributorKind.SOURCE, contributor_key="s2",
                          strength=AttributionStrength.SUPPORTED)),
            (), "2026-08-19T00:00:00Z")
        assert len(attribution.contributors_of(ContributorKind.SOURCE)) == 2


def pairs(count: int, *, probability="0.8", hit_ratio=0.25, **dimensions) -> list[ScoredPair]:
    return [ScoredPair(f"s{i}", "f" * 64, "o" * 64, Decimal(probability),
                       Decimal(1) if i < int(count * hit_ratio) else Decimal(0),
                       "2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z",
                       dimensions or {"recipe": "graduation@v1"})
            for i in range(count)]


class TestCalibration:
    def test_a_pair_that_is_not_pre_outcome_is_refused(self):
        with pytest.raises(CalibrationError, match="hindsight"):
            ScoredPair("s", "f" * 64, "o" * 64, Decimal("0.5"), Decimal(1),
                       "2026-06-01T00:00:00Z", "2026-01-01T00:00:00Z")

    def test_brier_is_deterministic(self):
        pair = pairs(1)[0]
        assert pair.brier == pair.brier == Decimal("0.6400000000")

    def test_a_thin_cell_reports_insufficiency_rather_than_a_number(self):
        report = calibrate(pairs(5), report_id="r", window_start="a", window_end="b",
                           computed_at="c")
        cell = report.cell(CalibrationDimension.RECIPE, "graduation@v1")
        assert cell is not None and not cell.sufficient
        assert cell.mean_brier is None
        assert str(MIN_SAMPLES_FOR_CLAIM) in cell.insufficiency_reason

    def test_a_scored_cell_reports_gap_and_buckets(self):
        report = calibrate(pairs(40), report_id="r", window_start="a", window_end="b",
                           computed_at="c")
        cell = report.cell(CalibrationDimension.RECIPE, "graduation@v1")
        assert cell.sufficient and cell.count == 40
        assert cell.mean_predicted == Decimal("0.8000000000")
        assert cell.observed_rate == Decimal("0.2500000000")
        assert cell.calibration_gap == Decimal("-0.5500000000")
        assert cell.buckets and cell.buckets[0].bucket == 8

    def test_dimensions_are_cut_independently(self):
        rows = (pairs(32, recipe="graduation@v1", source="county")
                + pairs(32, recipe="graduation@v1", source="paid"))
        report = calibrate(rows, report_id="r", window_start="a", window_end="b",
                           computed_at="c",
                           dimensions=(CalibrationDimension.RECIPE,
                                       CalibrationDimension.SOURCE))
        assert report.cell(CalibrationDimension.RECIPE, "graduation@v1").count == 64
        assert report.cell(CalibrationDimension.SOURCE, "county").count == 32

    def test_a_report_grants_no_authority_and_mutates_nothing(self):
        report = calibrate(pairs(32), report_id="r", window_start="a", window_end="b",
                           computed_at="c")
        assert report.authority == "NONE"
        assert report.mutates_production is False

    def test_a_report_claiming_to_mutate_production_is_refused(self):
        with pytest.raises(CalibrationError, match="never mutates production"):
            CalibrationReport("r", "a", "b", 0, (), (), "c", mutates_production=True)

    def test_calibrating_nothing_is_refused(self):
        with pytest.raises(CalibrationError, match="nothing to calibrate"):
            calibrate([], report_id="r", window_start="a", window_end="b", computed_at="c")
