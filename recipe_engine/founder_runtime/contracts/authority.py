"""Authority classes A0–A6. Fixture policy only in P1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AuthorityClass(str, Enum):
    A0 = "A0"  # local read-only inspection
    A1 = "A1"  # local reversible analysis
    A2 = "A2"  # local code/document mutation
    A3 = "A3"  # contained governed execution
    A4 = "A4"  # commit/persistent local change
    A5 = "A5"  # external send/push/spend/deploy/production mutation
    A6 = "A6"  # destructive/high-risk operation


# Fixture policy defaults (NOT real Governance)
FIXTURE_POLICY = {
    AuthorityClass.A0: "allow",
    AuthorityClass.A1: "allow",
    AuthorityClass.A2: "approval_required",
    AuthorityClass.A3: "approval_required",
    AuthorityClass.A4: "approval_required",
    AuthorityClass.A5: "approval_required",
    AuthorityClass.A6: "enhanced_approval_required",
}


@dataclass(frozen=True)
class AuthorityRequirement:
    schema_version: str
    authority_class: AuthorityClass
    action: str
    scope: str
    reason: str
    requires_approval: bool
    enhanced: bool = False
    policy_version: str = "fr.authority.kernel.v1"
    reason_code: str = "UNSPECIFIED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "authority_class": self.authority_class.value,
            "action": self.action,
            "scope": self.scope,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "enhanced": self.enhanced,
            "policy_version": self.policy_version,
            "reason_code": self.reason_code,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "AuthorityRequirement":
        return AuthorityRequirement(
            schema_version=d["schema_version"],
            authority_class=AuthorityClass(d["authority_class"]),
            action=d["action"],
            scope=d["scope"],
            reason=d["reason"],
            requires_approval=bool(d["requires_approval"]),
            enhanced=bool(d.get("enhanced", False)),
            policy_version=str(d.get("policy_version") or "fr.authority.kernel.v1"),
            reason_code=str(d.get("reason_code") or "UNSPECIFIED"),
        )
