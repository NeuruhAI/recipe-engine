"""Adapter interface. Adapters may depend on domain types; autonomy core must not."""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from recipe_engine.autonomy.models import CandidateStrategy, OpportunityEnvelope


class DomainAdapter(Protocol):
    """Normalize domain inputs into autonomy contracts. No I/O required for P0."""

    domain: str

    def to_opportunity(self, raw: dict[str, Any]) -> OpportunityEnvelope:
        ...

    def candidate_strategies(
        self, opportunity: OpportunityEnvelope, raw: dict[str, Any] | None = None
    ) -> Sequence[CandidateStrategy]:
        ...
