"""Deterministic ID helpers for founder runtime."""

from __future__ import annotations

import uuid
from typing import Any

from recipe_engine.autonomy.hashing import content_hash, deterministic_id, sha256_hex


def new_uuid(prefix: str = "id") -> str:
    """Non-deterministic UUID for entity IDs that must be unique under concurrency."""
    return f"{prefix}_{uuid.uuid4().hex}"


def stable_id(prefix: str, *parts: Any) -> str:
    return deterministic_id(prefix, *parts)


def hash_payload(obj: Any) -> str:
    return content_hash(obj)


def digest(data: str | bytes) -> str:
    return sha256_hex(data)


__all__ = ["new_uuid", "stable_id", "hash_payload", "digest", "content_hash", "sha256_hex"]
