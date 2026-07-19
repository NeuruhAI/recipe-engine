"""Runtime locks and worker leases."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from recipe_engine.founder_runtime.errors import LockHeld, LockStale
from recipe_engine.founder_runtime.ids import new_uuid


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
        now = self.clock.now_iso()
        # peek without advancing if stepping — use now then compute expiry from same
        token = new_uuid("lck")
        # For stepping clock, now_iso advances; use a second call for expiry base is fine
        expires = _add_seconds(now, ttl_seconds)
        ok = self.store.acquire_lock(lock_name, owner, token, now, expires, mission_id)
        if not ok:
            raise LockHeld("lock held by another owner", {"lock": lock_name, "owner": owner})
        return token

    def release(self, lock_name: str, owner: str, token: str) -> None:
        self.store.release_lock(lock_name, owner, token)

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
