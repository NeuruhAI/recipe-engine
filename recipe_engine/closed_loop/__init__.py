"""The closed loop: WORLD → DECISION → ACTION → OUTCOME → LEARNING → WORLD.

One event grammar (`events`), written to one hash-chained ledger (`ledger`),
carrying one identity contract (`identity`). Around it: the world-state delta
that refuses to change quietly, the recipe evaluation that pins its own
version, the decision that names its authority, the action declared before it
is taken, the outcome split into process/business/world, graded attribution,
calibration that mutates nothing, a learning proposal that must show its
numbers, and a promotion gate that never grants deployment authority.

The design constraint the whole package exists to satisfy: **the system may
learn from what actually happened, and may not use learning as a way to lie or
to deploy itself.** Every refusal in here is a way of saying that once more, in
a place where it can be tested.
"""

from recipe_engine.closed_loop.identity import (
    EvidenceRef, EventIdentity, IdentityError, PROMOTABLE_MODES, Ref, RefKind,
    WorldMode, canonical_json, digest,
)
from recipe_engine.closed_loop.events import (
    CausalClaim, CausalRelation, EVIDENCE_REQUIRED, EventGrammarError,
    FORBIDDEN_PAYLOAD_KEYS, GENESIS_HASH, LoopEvent, LoopEventType, REQUIRED_REFS,
    SCHEMA_EVENT, grammar_digest, grammar_manifest,
)
from recipe_engine.closed_loop.ledger import (
    LedgerIntegrityError, LoopLedger, verify_chain,
)
from recipe_engine.closed_loop.world_state import (
    Belief, BeliefChange, EpistemicStatus, WorldState, WorldStateDelta,
    WorldStateError, apply_delta,
)
from recipe_engine.closed_loop.recipe_eval import (
    QualificationState, RecipeEvalError, RecipeEvaluationRecord, RecipeRef,
    ScoreComponent, from_graduation_verdict,
)
from recipe_engine.closed_loop.decision import (
    Alternative, AuthorityRef, DeclaredRationale, DecisionDisposition,
    DecisionError, DecisionRecord, GovernanceVerdict,
)
from recipe_engine.closed_loop.action import (
    ActionError, ActionExecutionReceipt, ActionIntent, ExecutionStatus,
    ExpectedEffect, Reversibility, RiskClass, bind_execution,
)
from recipe_engine.closed_loop.forecast import (
    Forecast, ForecastError, ForecastedMetric,
)
from recipe_engine.closed_loop.outcome import (
    GENERIC_METRICS, LoopOutcomeRecord, OutcomeError, OutcomeGrade, OutcomeMetric,
    OutcomeObservation, OutcomeTier,
)
from recipe_engine.closed_loop.attribution import (
    AttributionError, AttributionSet, AttributionStrength, Contribution,
    ContributorKind, LEARNABLE_STRENGTHS, strength_for,
)
from recipe_engine.closed_loop.calibration import (
    BucketPerformance, CalibrationCell, CalibrationDimension, CalibrationError,
    CalibrationReport, ScoredPair, calibrate,
)
from recipe_engine.closed_loop.learning import (
    ChangeKind, EvidenceWindow, ExpectedBenefit, LearningProposal, LearningRefusal,
    ProposedChange, RegressionRisk, propose,
)
from recipe_engine.closed_loop.promotion import (
    PromotionDecision, PromotionError, PromotionOutcome, PromotionPolicy,
    PromotionPrerequisites, Stage, VersionPromotion, evaluate_promotion, promote,
)
from recipe_engine.closed_loop.source_learning import (
    SourceAttempt, SourceLearningError, SourceRanking, SourceYieldCell,
    build_cells, rank_sources,
)
from recipe_engine.closed_loop.recipe_learning import (
    Diagnosis, RecipeLearningError, RecipeObservationCounts, RecipeScorecard,
    Track, diagnose, score_recipe,
)
from recipe_engine.closed_loop.replay import (
    LoopReconstruction, ReplayError, SubjectStory, replay,
)
from recipe_engine.closed_loop import bridges

__all__ = [
    # identity + grammar
    "EvidenceRef", "EventIdentity", "IdentityError", "PROMOTABLE_MODES", "Ref",
    "RefKind", "WorldMode", "canonical_json", "digest",
    "CausalClaim", "CausalRelation", "EVIDENCE_REQUIRED", "EventGrammarError",
    "FORBIDDEN_PAYLOAD_KEYS", "GENESIS_HASH", "LoopEvent", "LoopEventType",
    "REQUIRED_REFS", "SCHEMA_EVENT", "grammar_digest", "grammar_manifest",
    "LedgerIntegrityError", "LoopLedger", "verify_chain",
    # world
    "Belief", "BeliefChange", "EpistemicStatus", "WorldState", "WorldStateDelta",
    "WorldStateError", "apply_delta",
    # decide
    "QualificationState", "RecipeEvalError", "RecipeEvaluationRecord", "RecipeRef",
    "ScoreComponent", "from_graduation_verdict",
    "Alternative", "AuthorityRef", "DeclaredRationale", "DecisionDisposition",
    "DecisionError", "DecisionRecord", "GovernanceVerdict",
    # act
    "ActionError", "ActionExecutionReceipt", "ActionIntent", "ExecutionStatus",
    "ExpectedEffect", "Reversibility", "RiskClass", "bind_execution",
    # predict + observe
    "Forecast", "ForecastError", "ForecastedMetric",
    "GENERIC_METRICS", "LoopOutcomeRecord", "OutcomeError", "OutcomeGrade",
    "OutcomeMetric", "OutcomeObservation", "OutcomeTier",
    "AttributionError", "AttributionSet", "AttributionStrength", "Contribution",
    "ContributorKind", "LEARNABLE_STRENGTHS", "strength_for",
    # learn
    "BucketPerformance", "CalibrationCell", "CalibrationDimension",
    "CalibrationError", "CalibrationReport", "ScoredPair", "calibrate",
    "ChangeKind", "EvidenceWindow", "ExpectedBenefit", "LearningProposal",
    "LearningRefusal", "ProposedChange", "RegressionRisk", "propose",
    "PromotionDecision", "PromotionError", "PromotionOutcome", "PromotionPolicy",
    "PromotionPrerequisites", "Stage", "VersionPromotion", "evaluate_promotion",
    "promote",
    "SourceAttempt", "SourceLearningError", "SourceRanking", "SourceYieldCell",
    "build_cells", "rank_sources",
    "Diagnosis", "RecipeLearningError", "RecipeObservationCounts",
    "RecipeScorecard", "Track", "diagnose", "score_recipe",
    # replay + seams
    "LoopReconstruction", "ReplayError", "SubjectStory", "replay",
    "bridges",
]
