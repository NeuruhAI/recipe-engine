"""Deterministic hashing for receipts and IDs. Pure; no I/O."""

from __future__ import annotations

import hashlib
from typing import Any

from .serialization import canonical_json


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def deterministic_id(prefix: str, *parts: Any) -> str:
    """Stable ID: prefix + first 16 hex of content hash of parts."""
    body = content_hash(list(parts))
    return f"{prefix}_{body[:16]}"
