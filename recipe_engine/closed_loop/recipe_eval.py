"""Recipe evaluation, recorded so an outcome can later be blamed on the exact
version that produced it.

The failure this prevents: six months from now a recipe is on v7, an outcome
arrives from something decided under v3, and the calibration silently credits
v7. Every evaluation therefore pins `RecipeRef` — id, version, and a digest of
the config that was actually in force — and every downstream artifact carries
that ref rather than the recipe's *current* version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from recipe_engine.closed_loop.identity import canonical_json, digest


SCHEMA_RECIPE_REF = "neuruh.closed-loop.recipe-ref.v1"
SCHEMA_RECIPE_EVAL = "neuruh.closed-loop.recipe-evaluation.v1"


class QualificationState(str, Enum):
    """What the evaluation concluded about this candidate.

    Deliberately parallel to `graduation.GraduationState` so nothing has to be
    translated twice, but stated in loop vocabulary because a recipe evaluation
    is not only about properties.
    """
    NOT_QUALIFIED = "not_qualified"
    RESEARCH = "research"
    CORROBORATED = "corroborated"
    QUALIFIED = "qualified"
    CONFLICTED = "conflicted"
    EXPIRED = "expired"


class RecipeEvalError(ValueError):
    """The evaluation cannot be recorded as stated."""


@dataclass(frozen=True)
class RecipeRef:
    """Which recipe, at which version, under which config."""
    recipe_id: str
    version: str
    config_digest: str

    def __post_init__(self) -> None:
        for name in ("recipe_id", "version", "config_digest"):
            if not str(getattr(self, name)).strip():
                raise RecipeEvalError(f"recipe_ref.{name} must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_RECIPE_REF, "recipe_id": self.recipe_id,
                "version": self.version, "config_digest": self.config_digest}

    @property
    def key(self) -> str:
        """The stable join key for calibration and learning. Includes version:
        two versions of one recipe are two things to be measured, not one."""
        return f"{self.recipe_id}@{self.version}"

    @staticmethod
    def of(recipe_id: str, version: str, config: Mapping[str, Any]) -> "RecipeRef":
        return RecipeRef(recipe_id, version, digest(dict(config)))


@dataclass(frozen=True)
class ScoreComponent:
    """One named contribution to a score, with the inputs it used.

    A composite with no components is a number nobody can argue with, which is
    the same as a number nobody can trust.
    """
    name: str
    value: Decimal | None
    weight: Decimal
    inputs: tuple[str, ...] = ()
    missing_inputs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name,
                "value": None if self.value is None else str(self.value),
                "weight": str(self.weight), "inputs": list(self.inputs),
                "missing_inputs": list(self.missing_inputs)}


@dataclass(frozen=True)
class RecipeEvaluationRecord:
    """One recipe, one candidate, one verdict — with everything needed to
    re-derive it and to attribute an outcome to it later."""
    evaluation_id: str
    recipe: RecipeRef
    subject_id: str
    candidate_id: str
    inputs: Mapping[str, Any]
    input_evidence_digests: tuple[str, ...]
    score: Decimal | None
    components: tuple[ScoreComponent, ...]
    qualification: QualificationState
    reason_codes: tuple[str, ...]
    evaluated_at: str
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.evaluation_id).strip():
            raise RecipeEvalError("evaluation_id must be non-empty")
        if not str(self.subject_id).strip():
            raise RecipeEvalError("subject_id must be non-empty")
        if not isinstance(self.qualification, QualificationState):
            raise RecipeEvalError("qualification must be a QualificationState")
        if not self.reason_codes:
            raise RecipeEvalError(
                "an evaluation must state at least one reason code — a verdict "
                "with no stated reason cannot be reviewed")
        if self.score is not None and not self.components:
            raise RecipeEvalError(
                "a score without components is unauditable; state what produced it")
        if self.qualification is QualificationState.QUALIFIED and not self.input_evidence_digests:
            raise RecipeEvalError(
                "a qualified candidate must name the evidence it qualified on")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_RECIPE_EVAL,
            "evaluation_id": self.evaluation_id,
            "recipe": self.recipe.to_dict(),
            "subject_id": self.subject_id,
            "candidate_id": self.candidate_id,
            "inputs": dict(self.inputs),
            "input_evidence_digests": list(self.input_evidence_digests),
            "score": None if self.score is None else str(self.score),
            "components": [c.to_dict() for c in self.components],
            "qualification": self.qualification.value,
            "reason_codes": list(self.reason_codes),
            "missing": list(self.missing),
            "evaluated_at": self.evaluated_at,
        }

    @property
    def digest(self) -> str:
        return digest(self.to_dict())


#: How the existing graduation classifier's states map into loop vocabulary.
#: Stated as data so the mapping is reviewable rather than buried in a branch.
GRADUATION_TO_QUALIFICATION: dict[str, QualificationState] = {
    "observed_signal": QualificationState.NOT_QUALIFIED,
    "research_property": QualificationState.RESEARCH,
    "corroborated": QualificationState.CORROBORATED,
    "qualified_candidate": QualificationState.QUALIFIED,
    "conflict": QualificationState.CONFLICTED,
    "rejected_expired": QualificationState.EXPIRED,
}


def from_graduation_verdict(
    verdict: Any,
    *,
    evaluation_id: str,
    subject_id: str,
    candidate_id: str,
    inputs: Mapping[str, Any],
    input_evidence_digests: Sequence[str],
    evaluated_at: str,
    score: Decimal | None = None,
    components: Sequence[ScoreComponent] = (),
) -> RecipeEvaluationRecord:
    """Lift the existing `graduation.classify` verdict into a loop record.

    The classifier is not reimplemented here — it is the reference recipe, and
    this is the adapter that makes its verdict attributable. `policy_version`
    and `evidence_hash` come straight off the verdict, which is exactly what
    makes the later attribution honest.
    """
    state = getattr(verdict, "state", None)
    state_value = getattr(state, "value", state)
    qualification = GRADUATION_TO_QUALIFICATION.get(str(state_value))
    if qualification is None:
        raise RecipeEvalError(f"unmapped graduation state: {state_value!r}")
    recipe = RecipeRef(
        recipe_id="graduation",
        version=str(getattr(verdict, "policy_version", "")) or "unknown",
        config_digest=str(getattr(verdict, "evidence_hash", "")) or "unknown",
    )
    reasons = [str(getattr(verdict, "why", "")).strip() or "no stated reason"]
    return RecipeEvaluationRecord(
        evaluation_id=evaluation_id,
        recipe=recipe,
        subject_id=subject_id,
        candidate_id=candidate_id,
        inputs=dict(inputs),
        input_evidence_digests=tuple(input_evidence_digests),
        score=score,
        components=tuple(components),
        qualification=qualification,
        reason_codes=tuple(reasons),
        evaluated_at=evaluated_at,
        missing=tuple(getattr(verdict, "missing", ()) or ()),
    )
