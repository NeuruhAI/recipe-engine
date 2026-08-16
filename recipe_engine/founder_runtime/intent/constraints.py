"""Constraint, non-goal and budget extraction from a founder objective.

Extraction is deterministic and phrase-driven on purpose.  Model output is
untrusted structured input, so nothing here consults a model: a constraint that
binds the mission must be derivable from the founder's own words by a rule a
human can audit and reproduce.  Every extracted item carries the phrase that
produced it (see :func:`extract_with_provenance`), so a reviewer can ask "why is
this mission constrained?" and get a literal answer.

The previous implementation was a fixed 5-tuple applied to every mission, which
meant no founder-stated constraint could ever enter the contract and every
mission — including a deliberately live read-only one — asserted
``fixture_mode_only``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class AuthorityEnvelope(str, Enum):
    """The authority a mission is compiled under.

    This is the mission's ceiling, not a request.  It decides which baseline
    constraints are stamped onto the contract; Governance still authorizes and
    AXON still executes, and neither is bound by what appears here.
    """

    READ_ONLY_FIXTURE = "read_only_fixture"
    READ_ONLY_LIVE = "read_only_live"


# Baseline constraints per envelope.  Both envelopes forbid mutation, secrets and
# shell; they differ only on whether live read-only adapters may be reached.
_ENVELOPE_CONSTRAINTS: dict[AuthorityEnvelope, tuple[str, ...]] = {
    AuthorityEnvelope.READ_ONLY_FIXTURE: (
        "no_live_adapters",
        "no_production_mutation",
        "no_secret_access",
        "fixture_mode_only",
        "no_shell_execution",
    ),
    AuthorityEnvelope.READ_ONLY_LIVE: (
        "live_read_only_adapters_permitted",
        "no_production_mutation",
        "no_secret_access",
        "no_shell_execution",
        "no_outbound_communication",
    ),
}

# Retained: the historical P1 default is exactly the fixture envelope.
DEFAULT_CONSTRAINTS = _ENVELOPE_CONSTRAINTS[AuthorityEnvelope.READ_ONLY_FIXTURE]


# Phrase -> constraint.  Matched against the lowercased objective.  Conservative
# by construction: a phrase that is not present adds nothing, and no rule here
# can ever *widen* authority — every entry is a restriction.
_CONSTRAINT_PHRASES: tuple[tuple[str, str], ...] = (
    ("without changing production", "no_production_mutation"),
    ("without modifying production", "no_production_mutation"),
    ("no production changes", "no_production_mutation"),
    ("do not change production", "no_production_mutation"),
    ("read-only", "read_only"),
    ("read only", "read_only"),
    ("without contacting", "no_outbound_communication"),
    ("contacting anyone", "no_outbound_communication"),
    ("contact anyone", "no_outbound_communication"),
    ("do not contact", "no_outbound_communication"),
    ("no outbound", "no_outbound_communication"),
    ("without writing", "no_write_operations"),
    ("no writes", "no_write_operations"),
    ("do not write", "no_write_operations"),
    ("without deploying", "no_deployment"),
    ("do not deploy", "no_deployment"),
    ("without spending", "no_spend"),
    ("no spend", "no_spend"),
)

# Phrase -> non-goal.  A non-goal is an outcome the mission must NOT pursue even
# if it would otherwise look like progress.  Distinct from a constraint: a
# constraint bounds the method, a non-goal bounds the objective.
_NON_GOAL_PHRASES: tuple[tuple[str, str], ...] = (
    ("without changing production", "changing_production_state"),
    ("without modifying production", "changing_production_state"),
    ("no production changes", "changing_production_state"),
    ("without contacting", "contacting_third_parties"),
    ("contacting anyone", "contacting_third_parties"),
    ("contact anyone", "contacting_third_parties"),
    ("do not contact", "contacting_third_parties"),
    ("without deploying", "deploying_anything"),
    ("without writing", "writing_to_any_system"),
    ("no writes", "writing_to_any_system"),
    ("without spending", "spending_money"),
)


@dataclass(frozen=True)
class ExtractionResult:
    """Extracted contract material plus the evidence for each item."""

    constraints: tuple[str, ...]
    non_goals: tuple[str, ...]
    provenance: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
            "provenance": [dict(p) for p in self.provenance],
        }


def extract_with_provenance(
    request_text: str,
    *,
    envelope: AuthorityEnvelope = AuthorityEnvelope.READ_ONLY_FIXTURE,
) -> ExtractionResult:
    """Derive constraints and non-goals from the objective plus the envelope."""
    lowered = (request_text or "").lower()
    constraints: list[str] = list(_ENVELOPE_CONSTRAINTS[envelope])
    provenance: list[dict[str, str]] = [
        {"item": c, "kind": "constraint", "source": f"envelope:{envelope.value}"}
        for c in constraints
    ]

    for phrase, constraint in _CONSTRAINT_PHRASES:
        if phrase in lowered and constraint not in constraints:
            constraints.append(constraint)
            provenance.append({"item": constraint, "kind": "constraint", "source": f"phrase:{phrase}"})

    non_goals: list[str] = []
    for phrase, non_goal in _NON_GOAL_PHRASES:
        if phrase in lowered and non_goal not in non_goals:
            non_goals.append(non_goal)
            provenance.append({"item": non_goal, "kind": "non_goal", "source": f"phrase:{phrase}"})

    return ExtractionResult(
        constraints=tuple(constraints),
        non_goals=tuple(non_goals),
        provenance=tuple(provenance),
    )


def extract_constraints(
    request_text: str,
    *,
    envelope: AuthorityEnvelope = AuthorityEnvelope.READ_ONLY_FIXTURE,
) -> tuple[str, ...]:
    return extract_with_provenance(request_text, envelope=envelope).constraints


def extract_non_goals(
    request_text: str,
    *,
    envelope: AuthorityEnvelope = AuthorityEnvelope.READ_ONLY_FIXTURE,
) -> tuple[str, ...]:
    return extract_with_provenance(request_text, envelope=envelope).non_goals


# --- budgets -----------------------------------------------------------------
# A mission with no stated bound gets the conservative default rather than an
# unbounded one.  "No budget" must never mean "infinite budget".
DEFAULT_TIME_BUDGET = "PT30M"
DEFAULT_RESOURCE_BUDGET: Mapping[str, int] = {
    "max_steps": 24,
    "max_adapter_calls": 64,
    "max_replans": 3,
}

_TIME_PHRASES: tuple[tuple[str, str], ...] = (
    ("within an hour", "PT1H"),
    ("within the hour", "PT1H"),
    ("within a day", "P1D"),
    ("by end of day", "P1D"),
    ("within a week", "P7D"),
    ("immediately", "PT5M"),
)


def extract_time_budget(request_text: str) -> str:
    lowered = (request_text or "").lower()
    for phrase, iso in _TIME_PHRASES:
        if phrase in lowered:
            return iso
    return DEFAULT_TIME_BUDGET


def extract_resource_budget(request_text: str, *, mission_class: str = "") -> dict[str, int]:
    budget = dict(DEFAULT_RESOURCE_BUDGET)
    lowered = (request_text or "").lower()
    # A long-horizon objective is allowed more steps, but still a finite number.
    if any(p in lowered for p in ("thorough", "comprehensive", "exhaustive", "strongest")):
        budget["max_steps"] = 48
        budget["max_adapter_calls"] = 128
    return budget
