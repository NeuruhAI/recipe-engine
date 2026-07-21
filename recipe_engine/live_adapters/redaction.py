"""Credential + sensitive-payload redaction for live evidence.

Nothing that could leak a secret ever reaches a receipt, an AdapterResult, or an
error detail.  Endpoints are disclosed only as *logical classes*; URLs with
embedded credentials are scrubbed; auth headers are masked; secret-looking
tokens in free text are blanked.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# userinfo in a URL:  scheme://user:pass@host  ->  scheme://***@host
_URL_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")
# ?token=...&key=...  query secrets
_QUERY_SECRET = re.compile(r"(?i)([?&](?:token|key|secret|password|sig|signature|access[_-]?key)=)[^&#\s]+")
# long opaque tokens (github pat, bearer, hex/b64 >= 24)
_OPAQUE_TOKEN = re.compile(r"(?i)\b(gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{20,}|[A-Fa-f0-9]{40,})\b")

_SENSITIVE_HEADER = re.compile(r"(?i)(authorization|x-axon-secret|x-api-key|cookie|set-cookie|token|secret)")


def redact_url(url: str) -> str:
    """Return a URL safe to record: credentials and query secrets removed."""
    if not isinstance(url, str) or not url:
        return ""
    scrubbed = _URL_USERINFO.sub(lambda m: m.group("scheme") + "***@", url)
    scrubbed = _QUERY_SECRET.sub(lambda m: m.group(1) + "***", scrubbed)
    scrubbed = _OPAQUE_TOKEN.sub("***", scrubbed)
    return scrubbed


def redact_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    scrubbed = redact_url(text)
    scrubbed = _OPAQUE_TOKEN.sub("***", scrubbed)
    return scrubbed


def redact_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if _SENSITIVE_HEADER.search(str(key)):
            out[str(key)] = "***"
        else:
            out[str(key)] = redact_text(str(value))
    return out


def redact_git_remote(url: str) -> str:
    """A git origin URL, credential-free.  SSH form host:path is left intact
    (it carries no secret); https userinfo/token forms are scrubbed."""
    return redact_url(url)
