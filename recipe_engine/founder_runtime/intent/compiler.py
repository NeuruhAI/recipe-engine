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
from .constraints import (
    AuthorityEnvelope,
    DEFAULT_CONSTRAINTS,
    extract_resource_budget,
    extract_time_budget,
    extract_with_provenance,
)
from .authority import classify_authority


# The organism this runtime plans inside.  Bound here rather than imported from
# live_adapters: the founder_runtime -> live_adapters dependency is one-way, and
# the AST confinement scan depends on it staying that way.
DEFAULT_WORLD = "neuruh"

_DOMAIN_BY_CLASS = {
    MissionClass.SYSTEM_STATUS: "organism_status",
    MissionClass.ANALYSIS: "analysis",
    MissionClass.PLANNING: "planning",
    MissionClass.RECOVERY: "recovery",
}

# Mission-level evidence every compiled mission owes, independent of its
# deliverables.  A mission that cannot produce these cannot be completed.
_BASE_REQUIRED_EVIDENCE = ("execution_receipts", "replay_projection", "authority_classification")


def _approval_gates(requirements: list[Any]) -> list[str]:
    """Per-mission approval gates implied by its authority classification.

    Gates were previously a single global frozen set identical for every
    mission.  These are the gates *this* mission must pass, recorded on the
    contract so a reviewer can see them without reading the transition table.
    """
    gates: list[str] = []
    for req in requirements:
        if getattr(req, "requires_approval", False):
            gates.append(f"approval_required:{req.authority_class.value}:{req.action}")
        if getattr(req, "enhanced", False):
            gates.append(f"enhanced_review:{req.authority_class.value}")
    # Approval records authority state; it never confers execution.
    gates.append("approval_confers_no_execution")
    return gates


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
    envelope: AuthorityEnvelope = AuthorityEnvelope.READ_ONLY_FIXTURE,
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

    extraction = extract_with_provenance(request_text, envelope=envelope)
    authority_requirements = classify_authority(request_text, canonical_action=intent)
    required_evidence = list(_BASE_REQUIRED_EVIDENCE)
    for d in deliverables:
        for ev in d.evidence_requirements:
            if ev not in required_evidence:
                required_evidence.append(ev)
    # The IAR's structured output was previously validated and then discarded.
    # It is untrusted, so it never becomes contract truth — but it is retained as
    # declared interpretation material so a reviewer can see what was proposed.
    extensions = dict(extensions)
    extensions["iar_entities"] = list(interpretation.get("entities") or [])
    extensions["iar_candidate_strategy_hints"] = list(interpretation.get("candidate_strategy_hints") or [])
    extensions["constraint_provenance"] = [dict(p) for p in extraction.provenance]
    extensions["authority_envelope"] = envelope.value

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
        constraints=list(extraction.constraints),
        authority_requirements=authority_requirements,
        context_requirements=["founder_core", "architecture", "current_runtime"] if intent == "system_status" else ["founder_core"],
        verification_criteria=["receipts_valid", "deliverables_present", "replay_matches"],
        completion_rule="all_required_deliverables",
        created_at=clock_now,
        updated_at=clock_now,
        creation_idempotency_key=creation_idempotency_key,
        non_goals=list(extraction.non_goals),
        world=DEFAULT_WORLD,
        domain=_DOMAIN_BY_CLASS.get(mission_class, "general"),
        time_budget=extract_time_budget(request_text),
        resource_budget=extract_resource_budget(request_text, mission_class=mission_class.value),
        required_evidence=required_evidence,
        approval_gates=_approval_gates(authority_requirements),
        extensions=extensions,
    )
    mission.recompute_input_hash()
    errs = validate_mission_dict(mission.to_dict())
    # A BLOCKED_INPUT mission is allowed to carry no deliverables — it exists to
    # ask one precise question — but it is not exempt from schema validity.  The
    # previous branch computed two unused lists and fell through to `pass`, so
    # every schema error on a blocked mission was silently discarded.
    # validate_mission_dict reports nothing about deliverables, so there is
    # nothing here to filter: any error it returns is a genuine schema error.
    if errs:
        raise MissionSchemaInvalid(
            "compiled mission invalid",
            {"errors": errs, "state": state.value},
        )
    return mission, interpretation
