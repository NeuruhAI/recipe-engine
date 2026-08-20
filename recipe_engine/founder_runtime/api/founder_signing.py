"""The founder's signing key, and the boundary that owns it.

This module is the ONLY place in the runtime that looks for a founder signing
secret, and it lives in the API package on purpose: the API process IS the
external decision channel.  It is where a human's approve/reject arrives, and it
is the one party entitled to turn that decision into an artifact Governance will
verify.

The mission process is on the other side of that boundary.  It asks for
permission; it must not be able to grant it.  That property is not a convention
here — ``live_adapters.composition.build_live_organs`` refuses to build a live
organ set in a process that can see this secret, so a mission cannot even start
in an environment where it could sign.  Same host, same user, so this is a
process-role boundary rather than an OS one: it does not claim that a compromised
mission process could not read the file, only that the mission role never holds
the key and fails closed instead of proceeding if the environment says otherwise.

The secret is read once at construction, never returned, never logged, never
placed in a response, an event payload, a receipt or an error detail.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Mapping

from recipe_engine.founder_runtime.adapters.governance_authorization import (
    sign_founder_approval,
    unsigned_scope_once_approval,
)

# The name Governance's own launcher uses for this key. Reusing it is
# deliberate: one secret, one name, one meaning across both processes.
ENV_FOUNDER_SECRET = "GOVERNANCE_FOUNDER_SECRET"
ENV_FOUNDER_SECRET_FILE = "GOVERNANCE_FOUNDER_SECRET_FILE"

# The approver identity in the Governance trust configuration. Governance
# refuses an approval whose approver_id it does not know, and refuses one whose
# approver_id is the lane or component being approved.
FOUNDER_APPROVER_ID = "founder"

# How long a founder's decision stays spendable. Governance caps this at
# approval_max_ttl_seconds (900) and refuses anything longer, so this is the
# runtime's own, shorter statement of the same intent: a decision made now is
# about now.
APPROVAL_TTL_SECONDS = 600


def secret_is_present(env: Mapping[str, str] | None = None) -> bool:
    """Whether a founder signing secret is reachable from this process.

    Used by the mission-side composition root to REFUSE to build a live organ
    set here. It answers only yes or no and never exposes the value.
    """
    source = os.environ if env is None else env
    if source.get(ENV_FOUNDER_SECRET, "").strip():
        return True
    return bool(source.get(ENV_FOUNDER_SECRET_FILE, "").strip())


def _load_secret() -> str | None:
    value = os.environ.get(ENV_FOUNDER_SECRET, "").strip()
    if value:
        return value
    path = os.environ.get(ENV_FOUNDER_SECRET_FILE, "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return None


class FounderApprovalSigner:
    """Turns a founder's decision into the artifact Governance verifies.

    Fail closed in both directions. With no secret configured, ``configured`` is
    False and ``sign`` raises: the channel records the decision but cannot
    produce a Governance-verifiable artifact, and a live mission consequently
    parks rather than proceeding on an unsigned record. With a secret that is
    too short to be a real key, it refuses rather than signing weakly.
    """

    MIN_SECRET_LENGTH = 16

    def __init__(self, secret: str | None = None):
        supplied = secret if secret is not None else _load_secret()
        if supplied and len(supplied) >= self.MIN_SECRET_LENGTH:
            self._secret: str | None = supplied
        else:
            self._secret = None

    @property
    def configured(self) -> bool:
        return self._secret is not None

    def sign_scope_once(
        self,
        *,
        scope_key: str,
        source_sha: str,
        issued_at: str,
        expires_at: str,
        approval_id: str | None = None,
        risk_tier: str = "R0",
    ) -> dict[str, Any]:
        """The signed, content/scope/version-bound, single-use founder approval."""
        if self._secret is None:
            raise RuntimeError(
                "no founder signing secret is configured; this channel cannot mint a "
                "Governance-verifiable approval"
            )
        unsigned = unsigned_scope_once_approval(
            approval_id=approval_id or ("fappr_" + secrets.token_hex(8)),
            approver_id=FOUNDER_APPROVER_ID,
            scope_key=scope_key,
            source_sha=source_sha,
            # A fresh nonce per decision is what makes the artifact single-use:
            # Governance spends it on its own consumption ledger and refuses it
            # for ever afterwards, across restarts.
            nonce="fnonce_" + secrets.token_hex(12),
            issued_at=issued_at,
            expires_at=expires_at,
            risk_tier=risk_tier,
        )
        return sign_founder_approval(unsigned, self._secret)
