"""Transaction helpers for sqlite store."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Any


@contextmanager
def immediate_transaction(conn: Any) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
