"""Recipe Engine caller for LandOS opportunity-intelligence evaluate.

Recipe Engine owns the versioned input.  LandOS owns scoring.  This module is
the smallest HTTP/protocol adapter that:

* refuses to skip the evaluate call or treat a skip as score 0
* refuses a mock/success payload that is not the owned result contract
* refuses to treat an all-UNKNOWN factor as ABSENT/PRESENT (the 0-as-known collapse)
* binds a DecisionReceipt to recipe id + version + config digest

It does not copy LandOS scoring.  The documented HTTP seam, read from
landos-engine ``1779abf``, is:

    POST /v1/opportunity-intelligence/evaluate
    header X-LANDOS-SECRET
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import requests

from recipe_engine.closed_loop.identity import digest

from .contracts import (
    OpportunityContractError,
    RECEIPT_CONTRACT_VERSION,
    RESULT_CONTRACT_VERSION,
    SCORE_VERSION,
    SIGNAL_STATES,
    recipe_config_digest,
    validate_input,
    validate_result,
)


DECISION_RECEIPT_VERSION = "neuruh.opportunity-intelligence.decision-receipt.v1"
EVALUATE_PATH = "/v1/opportunity-intelligence/evaluate"
EVALUATOR_IDENTITY = "landos.opportunity-intelligence.evaluate.v1"
DEFAULT_LANDOS_URL = "http://127.0.0.1:8400"


class OpportunityEvaluateError(RuntimeError):
    """The LandOS evaluate call was skipped, malformed, or unbound."""


class OpportunityEvaluator(Protocol):
    """Anything that can return a LandOS opportunity-intelligence result."""

    def evaluate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class OpportunityDecisionReceipt:
    """Caller-side binding so an outcome cannot silently credit the wrong recipe."""

    schema_version: str
    recipe_id: str
    recipe_version: str
    config_digest: str
    score_version: str
    subject_id: str
    input_digest: str
    landos_receipt_sha256: str
    landos_result_digest: str
    evaluator: str
    evaluate_called: bool
    world_mode: str
    receipt_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "config_digest": self.config_digest,
            "score_version": self.score_version,
            "subject_id": self.subject_id,
            "input_digest": self.input_digest,
            "landos_receipt_sha256": self.landos_receipt_sha256,
            "landos_result_digest": self.landos_result_digest,
            "evaluator": self.evaluator,
            "evaluate_called": self.evaluate_called,
            "world_mode": self.world_mode,
        }


@dataclass(frozen=True)
class OpportunityDecision:
    result: Mapping[str, Any]
    receipt: OpportunityDecisionReceipt


class SkippedEvaluator:
    """Explicit non-call.  A skip is an error, never a default score of 0."""

    def evaluate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise OpportunityEvaluateError(
            "LandOS evaluate was skipped; refusing to collapse the call to score 0"
        )


class LandosOpportunityClient:
    """HTTP caller for the LandOS opportunity-intelligence evaluate seam."""

    def __init__(
        self,
        base_url: str = DEFAULT_LANDOS_URL,
        secret: str = "",
        timeout_s: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.secret = secret
        self.timeout_s = timeout_s
        self._session = session or requests.Session()

    def evaluate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.base_url:
            raise OpportunityEvaluateError("LANDOS_ENGINE_URL is missing")
        if not str(self.secret or "").strip():
            raise OpportunityEvaluateError("LANDOS_SECRET is missing")
        try:
            response = self._session.post(
                f"{self.base_url}{EVALUATE_PATH}",
                headers={
                    "X-LANDOS-SECRET": self.secret,
                    "Content-Type": "application/json",
                },
                json=dict(payload),
                timeout=self.timeout_s,
            )
        except requests.RequestException as exc:
            raise OpportunityEvaluateError(
                f"LandOS evaluate transport failed: {exc}"
            ) from exc
        if response.status_code != 200:
            raise OpportunityEvaluateError(
                f"{EVALUATE_PATH} {response.status_code}: {response.text[:300]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise OpportunityEvaluateError(
                "LandOS evaluate returned non-JSON success"
            ) from exc
        if not isinstance(body, Mapping):
            raise OpportunityEvaluateError("LandOS evaluate returned a non-object")
        return body


def _never_signal_state(state: str) -> bool:
    """Exhaustiveness guard: a new SIGNAL_STATES member must be handled."""
    raise OpportunityEvaluateError(f"unhandled signal state {state!r}")


def _factor_resolved_from_unknown(state: str) -> bool:
    if state not in SIGNAL_STATES:
        raise OpportunityEvaluateError(f"unrecognized factor state {state!r}")
    if state == "UNKNOWN":
        return False
    if state == "CONFLICTED":
        return False
    if state == "PRESENT":
        return True
    if state == "ABSENT":
        return True
    return _never_signal_state(state)


def assert_unknown_not_collapsed(
    payload: Mapping[str, Any], result: Mapping[str, Any]
) -> None:
    """UNKNOWN must stay UNKNOWN.  Scoring it as ABSENT/PRESENT (including 0) is a collapse."""
    recipe_types = {
        str(rule["signal_type"]): bool(rule.get("material"))
        for rule in payload["recipe"]["signal_rules"]
    }
    by_type: dict[str, list[str]] = {}
    for signal in payload["signals"]:
        by_type.setdefault(str(signal["signal_type"]), []).append(str(signal["state"]))

    factors = {
        str(factor["factor_id"]): factor
        for factor in result["stacked_signals"]["normalized_factors"]
    }
    components = {
        str(component["factor_id"]): component
        for component in result["opportunity_score"]["components"]
    }
    unknown_explained = {
        str(item.get("factor_id"))
        for item in result["explanation"]["unknown_material_factors"]
        if isinstance(item, Mapping)
    }
    scored_known = {
        str(item.get("factor_id"))
        for bucket in ("positive_factors", "negative_factors")
        for item in result["explanation"][bucket]
        if isinstance(item, Mapping)
    }
    unrecognized_types = {
        str(item.get("signal_type"))
        for item in result["stacked_signals"]["unrecognized_observations"]
        if isinstance(item, Mapping)
    }

    for signal_type, states in by_type.items():
        if set(states) - {"UNKNOWN"}:
            continue
        if signal_type not in recipe_types:
            if signal_type not in unrecognized_types:
                raise OpportunityEvaluateError(
                    f"UNKNOWN signal type {signal_type!r} was omitted instead of "
                    "being preserved unscored"
                )
            if signal_type in factors or signal_type in components:
                observed = (factors.get(signal_type) or components[signal_type])["state"]
                if _factor_resolved_from_unknown(str(observed)):
                    raise OpportunityEvaluateError(
                        f"unrecognized UNKNOWN signal {signal_type!r} was collapsed "
                        f"to {observed}"
                    )
            continue

        factor = factors.get(signal_type) or components.get(signal_type)
        if factor is None:
            raise OpportunityEvaluateError(
                f"UNKNOWN factor {signal_type!r} was omitted from the LandOS result"
            )
        state = str(factor["state"])
        if _factor_resolved_from_unknown(state):
            raise OpportunityEvaluateError(
                f"UNKNOWN factor {signal_type!r} was collapsed to {state} "
                "(including a known 0 from points_if_absent)"
            )
        if recipe_types[signal_type] and signal_type not in unknown_explained:
            raise OpportunityEvaluateError(
                f"UNKNOWN material factor {signal_type!r} missing from "
                "explanation.unknown_material_factors"
            )
        if signal_type in scored_known:
            raise OpportunityEvaluateError(
                f"UNKNOWN factor {signal_type!r} was credited as a scored known finding"
            )


def _bind_recipe(payload: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    sent = payload["recipe"]
    observed = result["recipe"]
    if observed.get("recipe_id") != sent.get("recipe_id"):
        raise OpportunityEvaluateError(
            "LandOS result recipe_id does not match the sent recipe"
        )
    if observed.get("version") != sent.get("version"):
        raise OpportunityEvaluateError(
            "LandOS result recipe version does not match the sent recipe"
        )
    claimed = sent.get("config_digest")
    if claimed != recipe_config_digest(sent):
        raise OpportunityEvaluateError("sent recipe config_digest does not bind its body")
    if observed.get("config_digest") != claimed:
        raise OpportunityEvaluateError(
            "LandOS result config_digest does not match the sent recipe"
        )
    if result["receipt"].get("recipe_digest") != claimed:
        raise OpportunityEvaluateError(
            "LandOS receipt recipe_digest does not match the sent recipe"
        )
    if result.get("subject_id") != payload["subject"]["subject_id"]:
        raise OpportunityEvaluateError("LandOS result subject_id does not match input")


def _decision_receipt(
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    evaluator: str,
) -> OpportunityDecisionReceipt:
    recipe = payload["recipe"]
    body = OpportunityDecisionReceipt(
        schema_version=DECISION_RECEIPT_VERSION,
        recipe_id=str(recipe["recipe_id"]),
        recipe_version=str(recipe["version"]),
        config_digest=str(recipe["config_digest"]),
        score_version=str(recipe["score_version"]),
        subject_id=str(payload["subject"]["subject_id"]),
        input_digest=digest(payload),
        landos_receipt_sha256=str(result["receipt"]["receipt_sha256"]),
        landos_result_digest=str(result["receipt"]["result_digest"]),
        evaluator=evaluator,
        evaluate_called=True,
        world_mode=str(payload.get("world_mode") or "UNSPECIFIED"),
        receipt_sha256="",
    )
    stamped = digest(body.to_dict())
    return OpportunityDecisionReceipt(**{**body.to_dict(), "receipt_sha256": stamped})


def request_opportunity_evaluation(
    payload: Mapping[str, Any],
    evaluator: OpportunityEvaluator | None,
    *,
    evaluator_identity: str = EVALUATOR_IDENTITY,
) -> OpportunityDecision:
    """Call LandOS evaluate and bind the owned contracts.  Skip is not success."""
    if evaluator is None:
        raise OpportunityEvaluateError(
            "LandOS evaluate was skipped; refusing to collapse the call to score 0"
        )
    validated = validate_input(payload)
    raw = evaluator.evaluate(validated)
    try:
        result = validate_result(raw)
    except OpportunityContractError as exc:
        raise OpportunityEvaluateError(
            f"LandOS evaluate returned a non-contract success: {exc}"
        ) from exc
    _bind_recipe(validated, result)
    assert_unknown_not_collapsed(validated, result)
    receipt = _decision_receipt(
        validated, result, evaluator=evaluator_identity
    )
    if not receipt.evaluate_called:
        raise OpportunityEvaluateError(
            "DecisionReceipt cannot credit an evaluation that was not called"
        )
    if receipt.recipe_id != validated["recipe"]["recipe_id"]:
        raise OpportunityEvaluateError("DecisionReceipt recipe_id drifted from input")
    if receipt.config_digest != validated["recipe"]["config_digest"]:
        raise OpportunityEvaluateError("DecisionReceipt config_digest drifted from input")
    if receipt.recipe_version != validated["recipe"]["version"]:
        raise OpportunityEvaluateError("DecisionReceipt recipe version drifted from input")
    if receipt.score_version != SCORE_VERSION:
        raise OpportunityEvaluateError("DecisionReceipt score_version is not the owned score")
    if result["schema_version"] != RESULT_CONTRACT_VERSION:
        raise OpportunityEvaluateError("result contract drifted after validation")
    if result["receipt"]["schema_version"] != RECEIPT_CONTRACT_VERSION:
        raise OpportunityEvaluateError("LandOS receipt contract drifted after validation")
    return OpportunityDecision(result=result, receipt=receipt)
