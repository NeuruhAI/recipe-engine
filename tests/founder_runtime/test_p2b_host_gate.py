"""P2-B host-recovery gate compatibility-repair regression suite.

Covers every case the repair charter enumerates: legacy/final/mixed evidence
aliases, wrong root, path traversal, symlink escape, checksum failure, missing
evidence, contradictory counts, unhealthy Redis, missing volume, continuing EIO,
generation ordering (older-HOLD→newer-GREEN, newer-HOLD→older-GREEN, two
conflicting GREEN), and the real authoritative audit 20260721T121408Z.  Plus
no-fixture-fallback and no-mutation invariants through the Qdrant adapter.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.live_adapters.host_recovery_gate import (
    ALLOWLISTED_ROOT_NAMES,
    DEFAULT_AUDIT_BASE,
    HostRecoveryGate,
)
from recipe_engine.live_adapters.live_qdrant import LiveQdrantAdapter

from tests.founder_runtime import p2b_harness as H

REAL_BASE = os.path.expanduser("~/neuruh-workspace/audit-output")
REAL_GREEN_TS = "20260721T121408Z"


def _gate(base) -> HostRecoveryGate:
    return HostRecoveryGate(str(base))


class AcceptedAliasTests(unittest.TestCase):
    def test_legacy_root_and_filenames_open(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), root_name=H.ROOT_LEGACY, aliases="legacy", ts="20260721T010000Z")
            d = _gate(td).evaluate()
            self.assertTrue(d.open, d.reason)

    def test_final_root_and_filenames_open(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), root_name=H.ROOT_FINAL, aliases="final", ts="20260721T121408Z")
            d = _gate(td).evaluate()
            self.assertTrue(d.open, d.reason)

    def test_mixed_accepted_aliases_open(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), root_name=H.ROOT_FINAL, aliases="mixed", ts="20260721T020000Z")
            d = _gate(td).evaluate()
            self.assertTrue(d.open, d.reason)


class RootAndPathTests(unittest.TestCase):
    def test_wrong_root_not_discovered(self):
        with tempfile.TemporaryDirectory() as td:
            # audit placed under a NON-allowlisted root name
            H.write_sealed_audit(Path(td), root_name="host-colima-integrity-recovery-bogus", ts="20260721T030000Z")
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("no sealed host-recovery audit", d.reason)

    def test_allowlist_is_exactly_two_roots(self):
        self.assertEqual(set(ALLOWLISTED_ROOT_NAMES), {"host-colima-integrity-recovery", "host-colima-integrity-recovery-final"})
        self.assertIn("neuruh-workspace/audit-output", DEFAULT_AUDIT_BASE)

    def test_checksum_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T040000Z", manifest_extra_line=("0" * 64) + "  ../escape.md")
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("checksum", d.reason.lower())

    def test_symlinked_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T050000Z", symlink_evidence=True)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)  # symlinked qdrant evidence is not read

    def test_symlinked_audit_dir_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            # a real GREEN audit OUTSIDE the allowlisted root
            outside = base / "outside"
            H.write_sealed_audit(outside, root_name="x", ts="20260721T060000Z")
            real_audit = outside / "x" / "20260721T060000Z"
            # symlink it INTO the allowlisted root
            root = base / H.ROOT_FINAL
            root.mkdir(parents=True, exist_ok=True)
            os.symlink(real_audit, root / "20260721T060000Z")
            d = _gate(td).evaluate()
            self.assertFalse(d.open)  # escaping symlink dir is not contained


class SealAndContentTests(unittest.TestCase):
    def test_checksum_failure_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T070000Z", corrupt_checksum=True)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("checksum", d.reason.lower())

    def test_missing_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T075000Z", write_manifest=False)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)

    def test_missing_qdrant_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T080000Z", omit_qdrant=True)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("Qdrant", d.reason)

    def test_contradictory_counts_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T090000Z", count_before=3049, count_after=3050)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("count", d.reason.lower())

    def test_redis_not_healthy_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T100000Z", redis_pong=False)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("PONG", d.reason)

    def test_missing_volume_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T110000Z", volumes=4)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("volume", d.reason.lower())

    def test_recreated_volume_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T115000Z", recreated_volume=True)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("volume", d.reason.lower())

    def test_continuing_eio_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T120000Z", io_clear=False)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("I/O", d.reason)

    def test_wrong_collection_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T123000Z")
            d = HostRecoveryGate(str(td), collection="some_other_collection").evaluate()
            self.assertFalse(d.open)
            self.assertIn("collection", d.reason.lower())


class GenerationOrderingTests(unittest.TestCase):
    def test_older_hold_then_newer_green_opens(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), root_name=H.ROOT_LEGACY, aliases="legacy", ts="20260721T002614Z", verdict="HOLD", io_clear=False)
            H.write_sealed_audit(Path(td), root_name=H.ROOT_FINAL, aliases="final", ts="20260721T121408Z", verdict="GREEN")
            d = _gate(td).evaluate()
            self.assertTrue(d.open, d.reason)
            self.assertIn("121408", d.sealed_audit_path or "")

    def test_newer_hold_after_older_green_seals(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), root_name=H.ROOT_FINAL, aliases="final", ts="20260721T010000Z", verdict="GREEN")
            H.write_sealed_audit(Path(td), root_name=H.ROOT_LEGACY, aliases="legacy", ts="20260722T010000Z", verdict="HOLD", io_clear=False)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("HOLD", d.reason)

    def test_two_conflicting_green_seals(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), root_name=H.ROOT_FINAL, aliases="final", ts="20260721T010000Z", verdict="GREEN", count_before=3049, count_after=3049)
            H.write_sealed_audit(Path(td), root_name=H.ROOT_LEGACY, aliases="legacy", ts="20260721T020000Z", verdict="GREEN", count_before=3050, count_after=3050)
            d = _gate(td).evaluate()
            self.assertFalse(d.open)
            self.assertIn("conflicting", d.reason.lower())

    def test_two_agreeing_green_open(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), root_name=H.ROOT_LEGACY, aliases="legacy", ts="20260721T010000Z", verdict="GREEN")
            H.write_sealed_audit(Path(td), root_name=H.ROOT_FINAL, aliases="final", ts="20260721T020000Z", verdict="GREEN")
            d = _gate(td).evaluate()
            self.assertTrue(d.open, d.reason)


class RealAuthoritativeAuditTests(unittest.TestCase):
    @unittest.skipUnless(
        os.path.isdir(os.path.join(REAL_BASE, "host-colima-integrity-recovery-final", REAL_GREEN_TS)),
        "authoritative host audit not present",
    )
    def test_real_authoritative_audit_opens_green(self):
        d = HostRecoveryGate().evaluate()  # pinned default base = real workspace
        self.assertTrue(d.open, d.reason)
        self.assertEqual(d.reason, "sealed GREEN host recovery verified")
        self.assertIn(REAL_GREEN_TS, d.sealed_audit_path or "")
        self.assertEqual(d.evidence.get("qdrant_counts"), [3049])
        self.assertIn("5 volumes preserved", d.evidence.get("volumes", ""))


class GateNoFallbackNoMutationTests(unittest.TestCase):
    def test_sealed_gate_denies_qdrant_no_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), verdict="HOLD", aliases="legacy", root_name=H.ROOT_LEGACY, ts="20260721T130000Z", io_clear=False)
            ep = H.endpoint("qdrant", 1)
            s = H.settings_with(endpoints=(ep,), host_recovery_audit_base=str(td))
            res = LiveQdrantAdapter(s, http=H.http_client(ep)).verify_collection(
                AdapterRequestMeta(correlation_id="c", observed_at="t")
            )
            self.assertEqual(res.status, "denied")
            self.assertFalse(res.payload["live_contacted"])
            self.assertFalse(res.payload["fixture_substituted"])
            self.assertFalse(res.fallback_used)
            self.assertFalse(res.mutation_attempted)

    def test_open_gate_verify_is_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            H.write_sealed_audit(Path(td), ts="20260721T140000Z")
            with H.mock_service(H.qdrant_routes()) as (port, _srv):
                ep = H.endpoint("qdrant", port)
                s = H.settings_with(endpoints=(ep,), host_recovery_audit_base=str(td))
                res = LiveQdrantAdapter(s, http=H.http_client(ep)).verify_collection(
                    AdapterRequestMeta(correlation_id="c", observed_at="t")
                )
                self.assertEqual(res.status, "success")
                self.assertFalse(res.payload["mutation_attempted"])
                self.assertFalse(res.mutation_attempted)
                self.assertFalse(res.fallback_used)


if __name__ == "__main__":
    unittest.main()
