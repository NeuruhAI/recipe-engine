"""Fixture IAR — structured interpretation only. No live inference."""

from __future__ import annotations

from typing import Any

from recipe_engine.founder_runtime.ids import hash_payload, stable_id
from .base import BaseFixtureAdapter


class FixtureIAR(BaseFixtureAdapter):
    name = "iar"

    def interpret(self, request_text: str, *, principal_id: str = "jeramie") -> dict[str, Any]:
        request_hash = hash_payload({"text": request_text, "principal": principal_id})
        # Deterministic fixture classification
        lower = request_text.lower()
        if "where" in lower and ("organism" in lower or "status" in lower or "blocked" in lower):
            intent = "system_status"
            ambiguity = "none"
        elif "delete" in lower or "destroy" in lower or "drop production" in lower:
            intent = "destructive"
            ambiguity = "irreversible"
        elif "or" in lower and "either" in lower:
            intent = "material_choice"
            ambiguity = "material"
        elif lower.strip() in ("", "???"):
            intent = "unknown"
            ambiguity = "impossible"
        else:
            intent = "general"
            ambiguity = "low"

        output = {
            "schema_version": "fr.iar_fixture.v1",
            "backend": "fixture",
            "model": "fixture",
            "principal_id": principal_id,
            "intent": intent,
            "ambiguity": ambiguity,
            "objective": request_text.strip() or "unspecified",
            "entities": ["neuruh_organism"] if intent == "system_status" else [],
            "candidate_strategy_hints": (
                ["inspect_fixture_health", "inspect_fixture_repos", "propose_next_safe_plan"]
                if intent == "system_status"
                else ["request_clarification"]
            ),
            "risk_class": "low" if intent == "system_status" else "medium",
            "authority_hint": "A0" if intent == "system_status" else "A1",
            "request_hash": request_hash,
        }
        output["output_hash"] = hash_payload(output)
        output["receipt_id"] = stable_id("iar_receipt", request_hash)
        return output
