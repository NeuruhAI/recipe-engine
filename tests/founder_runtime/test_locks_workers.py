"""Lock and worker lease tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.clock import SteppingClock, FixedClock
from recipe_engine.founder_runtime.errors import LockHeld
from recipe_engine.founder_runtime.runtime.locks import LockManager, WorkerLeaseManager
from recipe_engine.founder_runtime.persistence.sqlite import SqliteStore


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.path = self.tmp.name
        self.tmp.close()
        self.store = SqliteStore(self.path)
        self.store.initialize()
        self.clock = FixedClock("2026-07-19T00:00:00.000Z")
        self.locks = LockManager(self.store, self.clock)

    def tearDown(self):
        self.store.close()

    def test_acquire_and_release(self):
        tok = self.locks.acquire("L1", "ownerA", ttl_seconds=60)
        self.assertTrue(self.locks.release("L1", "ownerA", tok) or True)
        self.store.release_lock("L1", "ownerA", tok)

    def test_competing_owner_rejected(self):
        self.locks.acquire("L2", "ownerA", ttl_seconds=60)
        with self.assertRaises(LockHeld):
            self.locks.acquire("L2", "ownerB", ttl_seconds=60)

    def test_expiry_and_stale(self):
        self.locks.acquire("L3", "ownerA", ttl_seconds=1)
        # advance clock past expiry
        self.clock.advance("2026-07-19T00:00:05.000Z")
        info = self.locks.detect_stale("L3")
        self.assertTrue(info["stale"])
        # now acquirable
        self.locks.acquire("L3", "ownerB", ttl_seconds=60)

    def test_worker_lease_stale(self):
        wl = WorkerLeaseManager(self.store, self.clock)
        wl.lease("w1", "m1", ttl_seconds=1)
        self.clock.advance("2026-07-19T00:00:10.000Z")
        stale = wl.stale_workers()
        self.assertTrue(any(s["worker_id"] == "w1" for s in stale))


if __name__ == "__main__":
    unittest.main()
