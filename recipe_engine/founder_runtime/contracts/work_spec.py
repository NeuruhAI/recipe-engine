"""Immutable executable work specification owned by Founder Runtime."""

from __future__ import annotations

import math
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from recipe_engine.founder_runtime.errors import MissionSchemaInvalid
from recipe_engine.founder_runtime.ids import hash_payload, stable_id


SCHEMA_WORK_SPEC = "fr.work_spec.v1"


class IntelligenceLayer(str, Enum):
    L0 = "L0_DETERMINISTIC"
    L1 = "L1_NEURUH_DETERMINISTIC"
    L2 = "L2_LOCAL_MODEL"
    L3 = "L3_CHEAP_REMOTE_MODEL"
    L4 = "L4_FRONTIER_MODEL"


_REQUEST_FIELDS = frozenset({
    "schema",
    "workflow_name",
    "params",
    "intelligence_layer",
    "evidence_refs",
    "production_mutation",
    "external_message",
    "money_move",
    "irreversible",
    "private_ip_risk",
    "code_custody_required",
})
_PERSISTED_FIELDS = _REQUEST_FIELDS | {"candidate_id"}
_BOOLEAN_FIELDS = (
    "production_mutation",
    "external_message",
    "money_move",
    "irreversible",
    "private_ip_risk",
    "code_custody_required",
)
_FORBIDDEN_KEYS = frozenset({"__proto__", "constructor", "prototype"})


def _validate_string(value: Any, path: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        raise MissionSchemaInvalid("work spec string field is malformed", {"path": path})
    if nonempty and not value.strip():
        raise MissionSchemaInvalid("work spec string field is empty", {"path": path})
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise MissionSchemaInvalid("work spec string is not canonical", {"path": path})
    return value


def _validate_json(value: Any, path: str = "$.params", depth: int = 0) -> Any:
    if depth > 64:
        raise MissionSchemaInvalid("work spec params exceed nesting limit", {"path": path})
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _validate_string(value, path)
    if isinstance(value, int):
        if not (-(2 ** 53 - 1) <= value <= 2 ** 53 - 1):
            raise MissionSchemaInvalid("work spec integer is outside the safe range", {"path": path})
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or (value == 0.0 and math.copysign(1.0, value) < 0):
            raise MissionSchemaInvalid("work spec float is not canonical", {"path": path})
        return value
    if isinstance(value, list):
        return [_validate_json(item, f"{path}[{index}]", depth + 1) for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            key = _validate_string(key, f"{path}.<key>", nonempty=True)
            if key in _FORBIDDEN_KEYS:
                raise MissionSchemaInvalid("work spec params contain a forbidden key", {"path": f"{path}.{key}"})
            normalized[key] = _validate_json(item, f"{path}.{key}", depth + 1)
        return normalized
    raise MissionSchemaInvalid(
        "work spec params contain a non-JSON value",
        {"path": path, "type": type(value).__name__},
    )


def validate_work_spec_request(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise MissionSchemaInvalid("work_spec must be an object", {})
    if any(not isinstance(key, str) for key in raw):
        raise MissionSchemaInvalid("work_spec field names must be strings", {})
    keys = set(raw)
    if keys != _REQUEST_FIELDS:
        raise MissionSchemaInvalid(
            "work_spec fields are incomplete or extended",
            {"missing": sorted(_REQUEST_FIELDS - keys), "unknown": sorted(keys - _REQUEST_FIELDS)},
        )
    if raw.get("schema") != SCHEMA_WORK_SPEC:
        raise MissionSchemaInvalid(
            "unsupported work_spec schema",
            {"expected": SCHEMA_WORK_SPEC, "actual": raw.get("schema")},
        )
    workflow_name = _validate_string(raw.get("workflow_name"), "$.workflow_name", nonempty=True)
    params = raw.get("params")
    if not isinstance(params, Mapping):
        raise MissionSchemaInvalid("work_spec params must be an object", {"path": "$.params"})
    params = _validate_json(params)
    try:
        layer = IntelligenceLayer(raw.get("intelligence_layer"))
    except (TypeError, ValueError) as exc:
        raise MissionSchemaInvalid(
            "unsupported work_spec intelligence layer",
            {"actual": raw.get("intelligence_layer")},
        ) from exc
    evidence_refs = raw.get("evidence_refs")
    if not isinstance(evidence_refs, (list, tuple)):
        raise MissionSchemaInvalid("work_spec evidence_refs must be an array", {})
    refs = [
        _validate_string(ref, f"$.evidence_refs[{index}]", nonempty=True)
        for index, ref in enumerate(evidence_refs)
    ]
    flags: dict[str, bool] = {}
    for field_name in _BOOLEAN_FIELDS:
        value = raw.get(field_name)
        if type(value) is not bool:
            raise MissionSchemaInvalid(
                "work_spec consequence flags must be boolean",
                {"field": field_name},
            )
        flags[field_name] = value
    return {
        "schema": SCHEMA_WORK_SPEC,
        "workflow_name": workflow_name,
        "params": params,
        "intelligence_layer": layer.value,
        "evidence_refs": refs,
        **flags,
    }


@dataclass(frozen=True)
class WorkSpec:
    schema: str
    candidate_id: str
    workflow_name: str
    params: dict[str, Any]
    intelligence_layer: IntelligenceLayer
    evidence_refs: tuple[str, ...]
    production_mutation: bool
    external_message: bool
    money_move: bool
    irreversible: bool
    private_ip_risk: bool
    code_custody_required: bool

    @staticmethod
    def create(mission_id: str, raw: Mapping[str, Any]) -> "WorkSpec":
        material = validate_work_spec_request(raw)
        candidate_id = stable_id("candidate", mission_id, hash_payload(material))
        return WorkSpec._from_material({**material, "candidate_id": candidate_id}, mission_id=mission_id)

    @staticmethod
    def from_dict(raw: Mapping[str, Any], *, mission_id: str) -> "WorkSpec":
        if not isinstance(raw, Mapping):
            raise MissionSchemaInvalid("persisted work_spec must be an object", {})
        if any(not isinstance(key, str) for key in raw):
            raise MissionSchemaInvalid("persisted work_spec field names must be strings", {})
        keys = set(raw)
        if keys != _PERSISTED_FIELDS:
            raise MissionSchemaInvalid(
                "persisted work_spec fields are incomplete or extended",
                {"missing": sorted(_PERSISTED_FIELDS - keys), "unknown": sorted(keys - _PERSISTED_FIELDS)},
            )
        request = {key: raw[key] for key in _REQUEST_FIELDS}
        material = validate_work_spec_request(request)
        material["candidate_id"] = _validate_string(
            raw.get("candidate_id"), "$.candidate_id", nonempty=True,
        )
        return WorkSpec._from_material(material, mission_id=mission_id)

    @staticmethod
    def _from_material(material: Mapping[str, Any], *, mission_id: str) -> "WorkSpec":
        request = {key: material[key] for key in _REQUEST_FIELDS}
        expected_candidate_id = stable_id("candidate", mission_id, hash_payload(request))
        if material.get("candidate_id") != expected_candidate_id:
            raise MissionSchemaInvalid(
                "work_spec candidate identity does not match mission and specification",
                {"mission_id": mission_id},
            )
        return WorkSpec(
            schema=SCHEMA_WORK_SPEC,
            candidate_id=expected_candidate_id,
            workflow_name=str(material["workflow_name"]),
            params=deepcopy(dict(material["params"])),
            intelligence_layer=IntelligenceLayer(material["intelligence_layer"]),
            evidence_refs=tuple(material["evidence_refs"]),
            production_mutation=material["production_mutation"],
            external_message=material["external_message"],
            money_move=material["money_move"],
            irreversible=material["irreversible"],
            private_ip_risk=material["private_ip_risk"],
            code_custody_required=material["code_custody_required"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "candidate_id": self.candidate_id,
            "workflow_name": self.workflow_name,
            "params": deepcopy(self.params),
            "intelligence_layer": self.intelligence_layer.value,
            "evidence_refs": list(self.evidence_refs),
            "production_mutation": self.production_mutation,
            "external_message": self.external_message,
            "money_move": self.money_move,
            "irreversible": self.irreversible,
            "private_ip_risk": self.private_ip_risk,
            "code_custody_required": self.code_custody_required,
        }

    def sha256(self) -> str:
        return hash_payload(self.to_dict())


__all__ = [
    "IntelligenceLayer",
    "SCHEMA_WORK_SPEC",
    "WorkSpec",
    "validate_work_spec_request",
]
