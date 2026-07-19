"""B5 — unique worker ownership and lock safety."""
from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.runtime.locks import LockManager, default_worker_id
from recipe_engine.founder_runtime.clock import FixedClock
from recipe_engine.founder_runtime.persistence.sqlite import SqliteStore
from recipe_engine.founder_runtime.errors import LockHeld


class B5LockTests(unittest.TestCase):
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

    def test_unique_default_owners(self):
        a = set(default_worker_id() for _ in range(20))
        self.assertEqual(len(a), 20)
        rt1 = MissionRuntime(self.path + ".1", clock=self.clock, adapter_mode="fixture")
        rt2 = MissionRuntime(self.path + ".2", clock=self.clock, adapter_mode="fixture")
        self.assertNotEqual(rt1.worker_id, rt2.worker_id)
        self.assertNotEqual(rt1.worker_id, "worker_local")
        rt1.close(); rt2.close()

    def test_same_name_different_instance_cannot_steal(self):
        tok1 = self.locks.acquire("L", "same_owner", ttl_seconds=60)
        # second acquisition with same owner string but new token must fail while unexpired
        with self.assertRaises(LockHeld):
            self.locks.acquire("L", "same_owner", ttl_seconds=60)
        # original still held
        lock = self.store.get_lock("L")
        self.assertEqual(lock["token"], tok1)

    def test_exact_owner_renewal(self):
        tok = self.locks.acquire("L2", "ownerA", ttl_seconds=60)
        self.locks.renew("L2", "ownerA", tok, ttl_seconds=120)
        lock = self.store.get_lock("L2")
        self.assertEqual(lock["token"], tok)

    def test_wrong_token(self):
        self.locks.acquire("L3", "ownerA", ttl_seconds=60)
        with self.assertRaises(LockHeld):
            self.locks.renew("L3", "ownerA", "wrong_token", ttl_seconds=60)

    def test_unexpired_conflict(self):
        self.locks.acquire("L4", "ownerA", ttl_seconds=60)
        with self.assertRaises(LockHeld):
            self.locks.acquire("L4", "ownerB", ttl_seconds=60)

    def test_expired_takeover(self):
        tok1 = self.locks.acquire("L5", "ownerA", ttl_seconds=1)
        self.clock.advance("2026-07-19T00:00:05.000Z")
        tok2 = self.locks.acquire("L5", "ownerB", ttl_seconds=60)
        self.assertNotEqual(tok1, tok2)
        lock = self.store.get_lock("L5")
        self.assertEqual(lock["owner"], "ownerB")

    def test_release_by_wrong_owner(self):
        tok = self.locks.acquire("L6", "ownerA", ttl_seconds=60)
        ok = self.locks.release("L6", "ownerB", tok)
        self.assertFalse(ok)
        self.assertIsNotNone(self.store.get_lock("L6"))
        ok2 = self.locks.release("L6", "ownerA", tok)
        self.assertTrue(ok2)

    def test_stale_worker(self):
        from recipe_engine.founder_runtime.runtime.locks import WorkerLeaseManager
        wl = WorkerLeaseManager(self.store, self.clock)
        wl.lease("w1", "m1", ttl_seconds=1)
        self.clock.advance("2026-07-19T00:00:10.000Z")
        stale = wl.stale_workers()
        self.assertTrue(any(s["worker_id"] == "w1" for s in stale))

    def test_process_restart_lock_expiry(self):
        self.locks.acquire("L7", "ownerA", ttl_seconds=1)
        self.store.close()
        store2 = SqliteStore(self.path)
        store2.initialize()
        clock2 = FixedClock("2026-07-19T00:00:10.000Z")
        locks2 = LockManager(store2, clock2)
        # expired — takeover ok
        tok = locks2.acquire("L7", "ownerB", ttl_seconds=60)
        self.assertTrue(tok)
        store2.close()

    def test_concurrent_acquisition(self):
        tok = self.locks.acquire("L8", "o1", ttl_seconds=60)
        failures = 0
        for i in range(5):
            try:
                self.locks.acquire("L8", f"o{i}", ttl_seconds=60)
            except LockHeld:
                failures += 1
        self.assertEqual(failures, 5)
        self.assertEqual(self.store.get_lock("L8")["token"], tok)


if __name__ == "__main__":
    unittest.main()
