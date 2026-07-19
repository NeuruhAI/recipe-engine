"""Runtime locks and worker leases — unique ownership, token-bound renew."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from recipe_engine.founder_runtime.errors import LockHeld, LockStale
from recipe_engine.founder_runtime.ids import new_uuid


def default_worker_id() -> str:
    """Unique per process/worker instance — never a shared constant."""
    return f"worker_{os.getpid()}_{uuid.uuid4().hex[:16]}"


def _add_seconds(iso: str, seconds: int) -> str:
    s = iso.replace("Z", "+00:00")
    if "." not in s:
        s = s.replace("+00:00", ".000+00:00")
    dt = datetime.fromisoformat(s)
    return (dt + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int((dt + timedelta(seconds=seconds)).microsecond/1000):03d}Z"


class LockManager:
    def __init__(self, store, clock):
        self.store = store
        self.clock = clock

    def acquire(self, lock_name: str, owner: str, *, ttl_seconds: int = 60, mission_id: str | None = None) -> str:
        """Acquire a new lease; returns unique token. Cannot steal unexpired locks."""
        now = self.clock.now_iso()
        token = new_uuid("lck")
        expires = _add_seconds(now, ttl_seconds)
        ok = self.store.acquire_lock(lock_name, owner, token, now, expires, mission_id)
        if not ok:
            raise LockHeld("lock held (unexpired) by another owner or token", {"lock": lock_name, "owner": owner})
        return token

    def renew(self, lock_name: str, owner: str, token: str, *, ttl_seconds: int = 60) -> None:
        now = self.clock.now_iso()
        expires = _add_seconds(now, ttl_seconds)
        ok = self.store.renew_lock(lock_name, owner, token, now, expires)
        if not ok:
            raise LockHeld("cannot renew: wrong owner/token or lock missing/expired", {
                "lock": lock_name, "owner": owner,
            })

    def release(self, lock_name: str, owner: str, token: str) -> bool:
        return bool(self.store.release_lock(lock_name, owner, token))

    def detect_stale(self, lock_name: str) -> dict[str, Any] | None:
        now = self.clock.now_iso()
        lock = self.store.get_lock(lock_name)
        if not lock:
            return None
        if lock["expires_at"] <= now:
            self.store.force_expire_lock(lock_name, now)
            return {"stale": True, "lock": lock}
        return {"stale": False, "lock": lock}


class WorkerLeaseManager:
    def __init__(self, store, clock):
        self.store = store
        self.clock = clock

    def lease(self, worker_id: str, mission_id: str | None, *, ttl_seconds: int = 120) -> None:
        now = self.clock.now_iso()
        expires = _add_seconds(now, ttl_seconds)
        self.store.upsert_worker_lease(worker_id, mission_id, now, expires, now, "active")

    def stale_workers(self) -> list[dict[str, Any]]:
        now = self.clock.now_iso()
        return self.store.list_stale_workers(now)
