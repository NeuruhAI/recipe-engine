"""Local schema initialization only. No production migrations."""

from __future__ import annotations

from .schema import SCHEMA_VERSION, SCHEMA_SQL


def local_init_sql() -> str:
    return SCHEMA_SQL


def current_schema_version() -> str:
    return SCHEMA_VERSION
