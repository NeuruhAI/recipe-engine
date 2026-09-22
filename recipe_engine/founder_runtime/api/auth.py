"""Local operator authentication for the Founder Runtime API.

Fail closed. The operator key is supplied only via environment variable or an
operator-owned file; it is never stored in source, never logged, and never
returned in any response. Comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

ENV_TOKEN = "FR_API_OPERATOR_TOKEN"
ENV_TOKEN_FILE = "FR_API_OPERATOR_TOKEN_FILE"


class AuthError(Exception):
    def __init__(self, message: str = "unauthorized"):
        self.message = message
        super().__init__(message)


def generate_token() -> str:
    """Generate a fresh operator token (operator stores it out-of-band)."""
    return "frk_" + secrets.token_urlsafe(32)


def _load_token() -> str | None:
    tok = os.environ.get(ENV_TOKEN, "").strip()
    if tok:
        return tok
    path = os.environ.get(ENV_TOKEN_FILE, "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return None


class OperatorAuth:
    """Holds the expected token digest; never the cleartext after init."""

    def __init__(self, token: str | None = None):
        supplied = token if token is not None else _load_token()
        if not supplied:
            # Fail closed: no token configured means every request is denied.
            self._digest = None
        else:
            self._digest = hashlib.sha256(supplied.encode("utf-8")).digest()

    @property
    def configured(self) -> bool:
        return self._digest is not None

    def verify(self, presented: str | None) -> bool:
        if self._digest is None or not presented:
            return False
        cand = hashlib.sha256(presented.encode("utf-8")).digest()
        return hmac.compare_digest(cand, self._digest)

    def require(self, presented: str | None) -> None:
        if not self.verify(presented):
            raise AuthError("missing or invalid operator token")

    def matches_digest(self, digest: bytes) -> bool:
        """True when ``digest`` is this operator token's sha256 (constant-time)."""
        return self._digest is not None and hmac.compare_digest(digest, self._digest)


# ---- the founder credential (G-1) -------------------------------------------
# The two approval routes accept ONLY this credential. FR holds nothing but the
# sha256 of the founder token and the identity it stands for; the token itself
# lives in the founder's login Keychain and is read at call time by
# ``neuruh-approve``. A raw founder token in this process's environment is a
# misconfiguration FR refuses to start with, because an env file, plist or
# process listing is exactly where the raw token must never be.
ENV_FOUNDER_TOKEN_SHA256 = "FR_FOUNDER_TOKEN_SHA256"
ENV_FOUNDER_IDENTITY = "FR_FOUNDER_IDENTITY"
FORBIDDEN_RAW_FOUNDER_TOKEN_ENV = ("FR_FOUNDER_TOKEN", "FR_FOUNDER_TOKEN_FILE", "NEURUH_FOUNDER_TOKEN")

_HEX64 = frozenset("0123456789abcdef")


class FounderAuthConfigError(Exception):
    """The founder credential is configured in a way FR must not run with."""


def founder_token_sha256(token: str) -> str:
    """The hex digest FR stores for a founder token (used when provisioning)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FounderAuth:
    """Holds the founder token's sha256 and identity; never the token."""

    def __init__(self, token_sha256: str | None = None, identity: str | None = None, *,
                 operator: OperatorAuth | None = None, environ=None):
        from recipe_engine.founder_runtime.api.identity import valid_founder_identity

        env = os.environ if environ is None else environ
        for name in FORBIDDEN_RAW_FOUNDER_TOKEN_ENV:
            if str(env.get(name, "")).strip():
                raise FounderAuthConfigError(
                    f"{name} is set: a raw founder token must never be in FR's environment; "
                    f"configure only {ENV_FOUNDER_TOKEN_SHA256}")
        hexd = (token_sha256 if token_sha256 is not None
                else str(env.get(ENV_FOUNDER_TOKEN_SHA256, ""))).strip().lower()
        ident = (identity if identity is not None else str(env.get(ENV_FOUNDER_IDENTITY, ""))).strip()
        self._digest: bytes | None = None
        self.identity: str | None = None
        if not hexd:
            # Unconfigured: every founder route is refused (FOUNDER_AUTH_NOT_CONFIGURED).
            return
        if len(hexd) != 64 or not set(hexd) <= _HEX64:
            raise FounderAuthConfigError(f"{ENV_FOUNDER_TOKEN_SHA256} must be a 64-char sha256 hex digest")
        if not valid_founder_identity(ident):
            raise FounderAuthConfigError(
                f"{ENV_FOUNDER_IDENTITY} must be a lowercase name (no ':'), not 'operator'")
        digest = bytes.fromhex(hexd)
        if operator is not None and operator.matches_digest(digest):
            raise FounderAuthConfigError("the founder token must differ from the operator token")
        self._digest = digest
        self.identity = ident

    @property
    def configured(self) -> bool:
        return self._digest is not None

    def verify(self, presented: str | None) -> bool:
        if self._digest is None or not presented:
            return False
        cand = hashlib.sha256(presented.encode("utf-8")).digest()
        return hmac.compare_digest(cand, self._digest)
