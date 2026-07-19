"""Context pack contracts — fixture documents only in P1."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


SCHEMA_CONTEXT_PACK = "fr.context_pack.v1"
SCHEMA_CONTEXT_SOURCE = "fr.context_source.v1"


class TrustClass(str, Enum):
    CANONICAL = "canonical"
    APPROVED = "approved"
    OPERATIONAL = "operational"
    RETRIEVED = "retrieved"
    MODEL = "model"
    UNTRUSTED = "untrusted"


class FreshnessClass(str, Enum):
    CURRENT = "current"
    RECENT = "recent"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


# Precedence (lower number = higher priority)
PRECEDENCE_ORDER = [
    "explicit_founder_instruction",
    "active_mission_contract",
    "governance_decision",
    "canonical_source_truth",
    "approved_recent_decisions",
    "founder_constitution",
    "project_canon",
    "historical_mission_evidence",
    "retrieval_candidates",
    "general_model_knowledge",
]

PACK_IDS = (
    "founder_core",
    "architecture",
    "current_runtime",
    "mission_history",
    "code_operations",
    "governance",
    "real_estate",
)


@dataclass(frozen=True)
class ConflictRecord:
    conflict_id: str
    source_a: str
    source_b: str
    description: str
    resolved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "source_a": self.source_a,
            "source_b": self.source_b,
            "description": self.description,
            "resolved": self.resolved,
        }


@dataclass(frozen=True)
class ContextSource:
    schema_version: str
    source_id: str
    pack_id: str
    title: str
    trust_class: TrustClass
    freshness_class: FreshnessClass
    token_estimate: int
    permitted_use: tuple[str, ...]
    expiry: str | None
    source_hash: str
    supersedes: tuple[str, ...] = ()
    superseded_by: str | None = None
    precedence_class: str = "project_canon"
    content: str = ""  # fixture text only
    opaque_pointer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "pack_id": self.pack_id,
            "title": self.title,
            "trust_class": self.trust_class.value,
            "freshness_class": self.freshness_class.value,
            "token_estimate": self.token_estimate,
            "permitted_use": list(self.permitted_use),
            "expiry": self.expiry,
            "source_hash": self.source_hash,
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
            "precedence_class": self.precedence_class,
            "content": self.content,
            "opaque_pointer": self.opaque_pointer,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "ContextSource":
        return ContextSource(
            schema_version=str(d["schema_version"]),
            source_id=str(d["source_id"]),
            pack_id=str(d["pack_id"]),
            title=str(d["title"]),
            trust_class=TrustClass(d["trust_class"]),
            freshness_class=FreshnessClass(d["freshness_class"]),
            token_estimate=int(d["token_estimate"]),
            permitted_use=tuple(d.get("permitted_use") or ()),
            expiry=d.get("expiry"),
            source_hash=str(d["source_hash"]),
            supersedes=tuple(d.get("supersedes") or ()),
            superseded_by=d.get("superseded_by"),
            precedence_class=str(d.get("precedence_class") or "project_canon"),
            content=str(d.get("content") or ""),
            opaque_pointer=str(d.get("opaque_pointer") or ""),
        )


@dataclass
class ContextPack:
    schema_version: str
    pack_id: str
    sources: list[ContextSource]
    token_budget: int
    conflicts: list[ConflictRecord] = field(default_factory=list)
    selected_source_ids: list[str] = field(default_factory=list)
    total_tokens: int = 0
    stale_sources: list[str] = field(default_factory=list)
    excluded_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "sources": [s.to_dict() for s in self.sources],
            "token_budget": self.token_budget,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "selected_source_ids": list(self.selected_source_ids),
            "total_tokens": self.total_tokens,
            "stale_sources": list(self.stale_sources),
            "excluded_sources": list(self.excluded_sources),
        }
