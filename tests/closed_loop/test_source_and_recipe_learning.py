"""Thin cells stay unranked; the four recipe tracks stay separate."""
from __future__ import annotations

from decimal import Decimal

import pytest

from recipe_engine.closed_loop.recipe_learning import (
    MIN_SAMPLES_FOR_TRACK, RecipeLearningError, RecipeObservationCounts,
    TRACK_DENOMINATOR, Track, diagnose, score_recipe,
)
from recipe_engine.closed_loop.source_learning import (
    MIN_ATTEMPTS_FOR_RANKING, SourceAttempt, SourceLearningError, build_cells,
    rank_sources,
)


def attempts(source: str, count: int, *, resolved: int, high_confidence: int,
             false_positives: int = 0, minutes="3", cost="0.10",
             owner_class="heirs_estate") -> list[SourceAttempt]:
    rows = []
    for index in range(count):
        rows.append(SourceAttempt(
            subject_id=f"{source}-{index:03d}", owner_class=owner_class,
            source_type=source, identity_resolved=index < resolved,
            high_confidence=index < high_confidence,
            false_positive=index < false_positives,
            manual_minutes=Decimal(minutes), cost=Decimal(cost),
            attempted_at="2026-05-01T00:00:00Z"))
    return rows


class TestSourceAttemptIntegrity:
    def test_high_confidence_without_resolution_is_a_contradiction(self):
        with pytest.raises(SourceLearningError, match="contradiction"):
            SourceAttempt("s", "oc", "src", identity_resolved=False,
                          high_confidence=True, false_positive=False,
                          manual_minutes=Decimal(1), cost=Decimal(0),
                          attempted_at="2026-05-01T00:00:00Z")

    def test_a_false_positive_requires_a_resolution_to_have_been_wrong(self):
        with pytest.raises(SourceLearningError, match="simply a miss"):
            SourceAttempt("s", "oc", "src", identity_resolved=False,
                          high_confidence=False, false_positive=True,
                          manual_minutes=Decimal(1), cost=Decimal(0),
                          attempted_at="2026-05-01T00:00:00Z")


class TestSourceYield:
    def test_a_measured_cell_reports_every_yield(self):
        cells = build_cells(attempts("county", 40, resolved=32, high_confidence=28,
                                     false_positives=2))
        cell = cells[0]
        assert cell.sufficient
        assert cell.identity_yield == Decimal("0.8000")
        assert cell.high_confidence_yield == Decimal("0.7000")
        assert cell.false_positive_rate == Decimal("0.0625")
        assert cell.cost_per_resolved_identity == Decimal("0.1250")

    def test_a_thin_cell_is_unranked_not_bad(self):
        cells = build_cells(attempts("obits", 5, resolved=5, high_confidence=5))
        assert not cells[0].sufficient
        assert "unranked, not bad" in cells[0].insufficiency_reason
        assert str(MIN_ATTEMPTS_FOR_RANKING) in cells[0].insufficiency_reason

    def test_a_lucky_thin_source_does_not_outrank_a_measured_one(self):
        cells = build_cells(
            attempts("county", 40, resolved=32, high_confidence=28, false_positives=2)
            + attempts("obits", 5, resolved=5, high_confidence=5))
        ranking = rank_sources(cells, owner_class="heirs_estate",
                               computed_at="2026-08-19T00:00:00Z")
        assert ranking.recommended_first().source_type == "county"
        assert [c.source_type for c in ranking.unranked] == ["obits"]

    def test_ranking_prefers_high_confidence_yield(self):
        cells = build_cells(
            attempts("county", 40, resolved=32, high_confidence=28, cost="0.10")
            + attempts("paid", 35, resolved=20, high_confidence=10, cost="0.75"))
        ranking = rank_sources(cells, owner_class="heirs_estate",
                               computed_at="2026-08-19T00:00:00Z")
        assert [c.source_type for c in ranking.ranked] == ["county", "paid"]

    def test_ranking_is_total_and_reproducible(self):
        cells = build_cells(
            attempts("a", 30, resolved=15, high_confidence=15)
            + attempts("b", 30, resolved=15, high_confidence=15, cost="0.50"))
        first = rank_sources(cells, owner_class="heirs_estate", computed_at="t")
        second = rank_sources(list(reversed(cells)), owner_class="heirs_estate",
                              computed_at="t")
        assert first.digest == second.digest

    def test_cells_are_scoped_to_one_owner_class(self):
        cells = build_cells(
            attempts("county", 40, resolved=32, high_confidence=28)
            + attempts("county", 40, resolved=8, high_confidence=4,
                       owner_class="absentee"))
        ranking = rank_sources(cells, owner_class="absentee", computed_at="t")
        assert ranking.recommended_first().high_confidence_yield == Decimal("0.1000")


class TestRecipeTracks:
    def counts(self, **overrides) -> RecipeObservationCounts:
        kwargs = dict(opportunities_produced=200, opportunities_accepted=160,
                      contactable=36, callable_permitted=30, pursued=30, converted=9)
        kwargs.update(overrides)
        return RecipeObservationCounts(**kwargs)

    def test_impossible_counts_are_refused(self):
        with pytest.raises(RecipeLearningError, match="cannot exceed produced"):
            self.counts(contactable=500)
        with pytest.raises(RecipeLearningError, match="cannot exceed contactable"):
            self.counts(callable_permitted=100)
        with pytest.raises(RecipeLearningError, match="cannot exceed pursued"):
            self.counts(converted=100)

    def test_each_track_uses_its_own_denominator(self):
        card = score_recipe(self.counts(), recipe_key="graduation@v1",
                            window_start="a", window_end="b", computed_at="c")
        assert card.track(Track.CONTACTABILITY).denominator == 200
        assert card.track(Track.CALLABILITY).denominator == 36
        assert card.track(Track.TRANSACTION_SUCCESS).denominator == 30
        assert TRACK_DENOMINATOR[Track.CALLABILITY] == "contactable"

    def test_there_is_deliberately_no_composite(self):
        card = score_recipe(self.counts(), recipe_key="graduation@v1",
                            window_start="a", window_end="b", computed_at="c")
        assert card.composite() is None

    def test_good_opportunities_with_bad_contactability_is_named_as_such(self):
        card = score_recipe(self.counts(), recipe_key="graduation@v1",
                            window_start="a", window_end="b", computed_at="c")
        result = diagnose(card)
        assert result.constraint is Track.CONTACTABILITY
        assert "hard-to-resolve owners" in result.verdict
        assert "not a recipe problem" in result.detail

    def test_a_bad_recipe_is_diagnosed_upstream_of_contactability(self):
        card = score_recipe(self.counts(opportunities_accepted=40),
                            recipe_key="graduation@v1", window_start="a",
                            window_end="b", computed_at="c")
        result = diagnose(card)
        assert result.constraint is Track.OPPORTUNITY_QUALITY

    def test_a_permission_constraint_is_distinguished_from_a_recipe_one(self):
        card = score_recipe(
            self.counts(contactable=180, callable_permitted=30, pursued=30, converted=20),
            recipe_key="graduation@v1", window_start="a", window_end="b", computed_at="c")
        result = diagnose(card)
        assert result.constraint is Track.CALLABILITY
        assert "no amount of recipe tuning" in result.detail

    def test_a_thin_track_claims_no_rate(self):
        card = score_recipe(
            self.counts(opportunities_produced=200, opportunities_accepted=160,
                        contactable=10, callable_permitted=8, pursued=8, converted=4),
            recipe_key="graduation@v1", window_start="a", window_end="b", computed_at="c")
        callability = card.track(Track.CALLABILITY)
        assert not callability.sufficient and callability.rate is None
        assert str(MIN_SAMPLES_FOR_TRACK) in callability.insufficiency_reason

    def test_an_entirely_unmeasured_recipe_is_reported_as_such(self):
        card = score_recipe(
            RecipeObservationCounts(opportunities_produced=4, opportunities_accepted=2,
                                    contactable=2, callable_permitted=1, pursued=1,
                                    converted=0),
            recipe_key="graduation@v1", window_start="a", window_end="b", computed_at="c")
        result = diagnose(card)
        assert result.constraint is None and result.verdict == "not yet measurable"
        assert len(result.unmeasured_tracks) == 4
