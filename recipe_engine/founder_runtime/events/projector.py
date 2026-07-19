"""Projection helpers."""

from __future__ import annotations

from typing import Any, Mapping

from recipe_engine.founder_runtime.ids import hash_payload


def project_state_hash(mission_dict: Mapping[str, Any]) -> str:
    return hash_payload(dict(mission_dict))
