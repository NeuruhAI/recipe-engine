"""Who recorded an authority decision.

A decision's ``decided_by`` is derived from the credential the caller actually
authenticated with, never from a constant. The operator token identifies the
operator (the scheduler, agents, probes); only the founder credential identifies
the founder, and only a founder decider may record an ALLOW or cause FR to sign
one (G-1, 2026-09-22).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OPERATOR_IDENTITY = "operator"

# A founder identity is a plain lowercase name. No ":" so it can never be read
# as a "governance:<authorization_id>" resolver decision, and never "operator".
_IDENTITY_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


def valid_founder_identity(identity: str) -> bool:
    return bool(_IDENTITY_RE.fullmatch(identity or "")) and identity != OPERATOR_IDENTITY


@dataclass(frozen=True)
class Decider:
    """The authenticated party behind one decision."""

    identity: str
    founder: bool


OPERATOR_DECIDER = Decider(identity=OPERATOR_IDENTITY, founder=False)
