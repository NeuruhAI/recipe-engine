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
