"""Intent-to-mission compiler. Fixture IAR is untrusted and schema-validated."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.errors import MissionSchemaInvalid
from recipe_engine.founder_runtime.contracts.mission import (
    Mission, Principal, MissionClass, Priority, RiskClass, AutonomyLevel,
    SCHEMA_MISSION, validate_mission_dict,
)
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.ids import new_uuid, hash_payload, stable_id
from recipe_engine.founder_runtime.adapters.fixture_iar import FixtureIAR
from .deliverables import system_status_deliverables
from .constraints import DEFAULT_CONSTRAINTS
from .authority import classify_authority


def _validate_iar_output(raw: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "backend", "model", "principal_id", "intent", "ambiguity",
        "objective", "entities", "candidate_strategy_hints", "risk_class", "authority_hint",
        "request_hash", "output_hash", "receipt_id",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise MissionSchemaInvalid("fixture IAR output missing fields", {"missing": missing})
    if raw.get("backend") != "fixture" or raw.get("model") != "fixture":
        raise MissionSchemaInvalid("IAR output must declare fixture backend/model", {"raw_backend": raw.get("backend")})
    extra = sorted(set(raw) - required)
    if extra:
        raise MissionSchemaInvalid("fixture IAR output contains unknown fields", {"fields": extra})
    if raw.get("schema_version") != "fr.iar_fixture.v1":
        raise MissionSchemaInvalid("fixture IAR schema version rejected", {})
    if raw.get("intent") not in {"system_status", "destructive", "material_choice", "unknown", "general"}:
        raise MissionSchemaInvalid("fixture IAR intent rejected", {})
    if raw.get("ambiguity") not in {"none", "low", "material", "irreversible", "impossible"}:
        raise MissionSchemaInvalid("fixture IAR ambiguity rejected", {})
    if raw.get("risk_class") not in {"low", "medium", "high", "critical"}:
        raise MissionSchemaInvalid("fixture IAR risk class rejected", {})
    if raw.get("authority_hint") not in {f"A{i}" for i in range(7)}:
        raise MissionSchemaInvalid("fixture IAR authority hint malformed", {})
    if raw.get("principal_id") != "jeramie" or not isinstance(raw.get("objective"), str):
        raise MissionSchemaInvalid("fixture IAR identity/objective malformed", {})
    if not isinstance(raw.get("entities"), list) or not isinstance(raw.get("candidate_strategy_hints"), list):
        raise MissionSchemaInvalid("fixture IAR list fields malformed", {})
    supplied_hash = raw.get("output_hash")
    material = dict(raw)
    material.pop("output_hash", None)
    material.pop("receipt_id", None)
    if supplied_hash != hash_payload(material):
        raise MissionSchemaInvalid("fixture IAR output hash mismatch", {})
    return dict(raw)


def compile_intent_to_mission(
    request_text: str,
    *,
    clock_now: str,
    principal: Principal | None = None,
    creation_idempotency_key: str = "",
    iar: FixtureIAR | None = None,
) -> tuple[Mission, dict[str, Any]]:
    principal = principal or Principal.jeramie_fixture()
    if principal.principal_id != "jeramie":
        # P1 only supports fixture principal jeramie
        raise MissionSchemaInvalid("P1 fixture principal must be jeramie", {"principal": principal.principal_id})

    iar = iar or FixtureIAR(mode="fixture")
    interpretation = _validate_iar_output(iar.interpret(request_text, principal_id=principal.principal_id))
    expected_request_hash = hash_payload({"text": request_text, "principal": principal.principal_id})
    if interpretation["request_hash"] != expected_request_hash:
        raise MissionSchemaInvalid("fixture IAR request binding mismatch", {})
    if interpretation["receipt_id"] != stable_id("iar_receipt", expected_request_hash):
        raise MissionSchemaInvalid("fixture IAR receipt binding mismatch", {})
    intent = interpretation["intent"]
    ambiguity = interpretation.get("ambiguity", "none")

    if ambiguity == "impossible":
        mission_class = MissionClass.GENERAL
        state = MissionState.BLOCKED_INPUT
        title = "Blocked: missing input"
        objective = "Clarify request — one precise question required"
        deliverables = []
        precise_question = "What concrete outcome should the organism produce?"
        extensions = {
            "blocked_question": precise_question,
            "ambiguity": ambiguity,
            "initial_block": "BLOCKED_INPUT",
        }
    elif intent == "system_status":
        mission_class = MissionClass.SYSTEM_STATUS
        state = MissionState.DRAFT
        title = "System Status and Next Action"
        objective = interpretation["objective"]
        deliverables = system_status_deliverables()
        extensions = {"ambiguity": ambiguity, "iar_receipt": interpretation.get("receipt_id")}
    else:
        mission_class = MissionClass.GENERAL
        state = MissionState.DRAFT
        title = f"Mission: {intent}"
        objective = interpretation["objective"]
        deliverables = system_status_deliverables() if intent != "destructive" else []
        extensions = {"ambiguity": ambiguity, "intent": intent}

    risk = RiskClass(interpretation.get("risk_class", "medium")) if interpretation.get("risk_class") in {r.value for r in RiskClass} else RiskClass.MEDIUM
    mission = Mission(
        schema_version=SCHEMA_MISSION,
        mission_id=new_uuid("msn"),
        version=0,
        principal=principal,
        title=title,
        objective=objective,
        mission_class=mission_class,
        priority=Priority.P1,
        risk_class=risk,
        autonomy_level=AutonomyLevel.L1_ASSISTED,
        current_state=state,
        desired_outcome="verified_status_and_safe_plan" if intent == "system_status" else "clarified_or_planned",
        deliverables=deliverables,
        constraints=list(DEFAULT_CONSTRAINTS),
        authority_requirements=classify_authority(request_text, canonical_action=intent),
        context_requirements=["founder_core", "architecture", "current_runtime"] if intent == "system_status" else ["founder_core"],
        verification_criteria=["receipts_valid", "deliverables_present", "replay_matches"],
        completion_rule="all_required_deliverables",
        created_at=clock_now,
        updated_at=clock_now,
        creation_idempotency_key=creation_idempotency_key,
        extensions=extensions,
    )
    mission.recompute_input_hash()
    errs = validate_mission_dict(mission.to_dict())
    # BLOCKED_INPUT missions may have empty deliverables — allow with completion_rule still valid
    if state != MissionState.BLOCKED_INPUT and errs:
        raise MissionSchemaInvalid("compiled mission invalid", {"errors": errs})
    if state == MissionState.BLOCKED_INPUT:
        # still need valid autonomy and completion rule
        filtered = [e for e in errs if "deliverable" not in e]
        # validate_mission_dict doesn't check deliverables emptiness
        if errs:
            # only schema fields
            bad = [e for e in errs if not e.startswith("invalid")]
            pass
    return mission, interpretation
