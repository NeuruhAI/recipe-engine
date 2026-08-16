"""GATE B — the Qdrant pin binds to retrieval identity, not to a name.

The retired pin (neuruh_vault_canon / 3049 points / a manifest_id the current
corpus does not carry) named a collection that no longer exists, and the adapter
correctly failed closed rather than substituting a fixture. Replacing it with a
new name and count would repeat the same mistake one collection later.

These tests hold the replacement to the standard the retired pin failed: the
verification must reproduce the geometry the vectors live in, the model that
built them and the tree they came from, all read from the corpus itself. A
collection answering to the right name with differently-embedded vectors must be
refused.

They also hold the two gates apart. Host recovery and canon identity are
different questions, and driving the host-recovery gate from the canon pin would
demand a host-recovery audit for a collection no host recovery ever covered.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.contract_validation import (
    QDRANT_ATTESTATION_IDENTITY_HASH,
    QDRANT_COLLECTION,
    QDRANT_DISTANCE,
    QDRANT_EMBEDDING_MODEL,
    QDRANT_POINT_BASELINE,
    QDRANT_SOURCE_TREE,
    QDRANT_VECTOR_SIZE,
    validate_qdrant_verification,
)
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.errors import (
    QdrantCollectionMismatch,
    QdrantCountChanged,
    QdrantCountMismatch,
    QdrantPayloadProvenanceMismatch,
)
from recipe_engine.live_adapters.host_recovery_gate import (
    REQUIRED_COLLECTION as HOST_RECOVERY_COLLECTION,
    REQUIRED_COUNT as HOST_RECOVERY_COUNT,
)
from recipe_engine.live_adapters.live_qdrant import LiveQdrantAdapter
from recipe_engine.live_adapters.settings import LiveAdapterSettings

from tests.founder_runtime import p2b_harness as H

SHA = "a" * 64
META = AdapterRequestMeta(correlation_id="t1-gateb", observed_at="2026-08-16T00:00:00.000Z")


def _verification(**over):
    payload = {
        "collection": QDRANT_COLLECTION,
        "endpoint_class": "loopback",
        "server_identity": "1.19.0",
        "container": "neuruh-qdrant",
        "image": "qdrant/qdrant:v1.19.0",
        "count_before": QDRANT_POINT_BASELINE,
        "count_after": QDRANT_POINT_BASELINE,
        "point_id_hash": SHA,
        "vector_size": QDRANT_VECTOR_SIZE,
        "distance": QDRANT_DISTANCE,
        "embedding_model": QDRANT_EMBEDDING_MODEL,
        "embedding_dim": QDRANT_VECTOR_SIZE,
        "source_tree": QDRANT_SOURCE_TREE,
        "attestation_identity_hash": QDRANT_ATTESTATION_IDENTITY_HASH,
        "request_hashes": [SHA],
        "response_hashes": [SHA],
        "mutation_attempted": False,
    }
    payload.update(over)
    return payload


class RatifiedIdentityTests(unittest.TestCase):
    def test_the_pin_is_the_current_canon(self):
        self.assertEqual(QDRANT_COLLECTION, "neuruh_canon_v20260816")
        self.assertEqual(QDRANT_POINT_BASELINE, 11756)
        self.assertEqual(QDRANT_VECTOR_SIZE, 768)
        self.assertEqual(QDRANT_EMBEDDING_MODEL, "nomic-embed-text")
        self.assertEqual(QDRANT_SOURCE_TREE, "neuruh-vault")

    def test_the_retired_july_pin_is_gone(self):
        self.assertNotEqual(QDRANT_COLLECTION, "neuruh_vault_canon")
        self.assertNotEqual(QDRANT_POINT_BASELINE, 3049)

    def test_attestation_hash_is_a_sha256(self):
        self.assertRegex(QDRANT_ATTESTATION_IDENTITY_HASH, r"^[0-9a-f]{64}$")

    def test_a_well_formed_verification_validates(self):
        self.assertEqual(
            validate_qdrant_verification(_verification())["count_after"],
            QDRANT_POINT_BASELINE)


class BindsToEvidenceNotJustNameTests(unittest.TestCase):
    """Each of these would pass a name-and-count check and must still fail."""

    def test_wrong_vector_dimension_is_refused(self):
        with self.assertRaises(QdrantPayloadProvenanceMismatch):
            validate_qdrant_verification(_verification(vector_size=3072))

    def test_wrong_distance_metric_is_refused(self):
        with self.assertRaises(QdrantPayloadProvenanceMismatch):
            validate_qdrant_verification(_verification(distance="Euclid"))

    def test_wrong_embedding_model_is_refused(self):
        with self.assertRaises(QdrantPayloadProvenanceMismatch):
            validate_qdrant_verification(_verification(embedding_model="text-embedding-3-large"))

    def test_payload_dimension_disagreeing_with_collection_is_refused(self):
        with self.assertRaises(QdrantPayloadProvenanceMismatch):
            validate_qdrant_verification(_verification(embedding_dim=1536))

    def test_wrong_source_tree_is_refused(self):
        with self.assertRaises(QdrantPayloadProvenanceMismatch):
            validate_qdrant_verification(_verification(source_tree="some-other-corpus"))

    def test_wrong_attestation_hash_is_refused(self):
        with self.assertRaises(QdrantPayloadProvenanceMismatch):
            validate_qdrant_verification(_verification(attestation_identity_hash="b" * 64))

    def test_wrong_collection_is_still_refused(self):
        with self.assertRaises(QdrantCollectionMismatch):
            validate_qdrant_verification(_verification(collection="neuruh_vault_canon"))

    def test_count_drift_is_still_refused(self):
        with self.assertRaises(QdrantCountMismatch):
            validate_qdrant_verification(_verification(count_before=3049, count_after=3049))

    def test_count_changing_during_a_read_is_still_refused(self):
        with self.assertRaises(QdrantCountChanged):
            validate_qdrant_verification(
                _verification(count_after=QDRANT_POINT_BASELINE + 1))


class AdapterObservesTheEvidenceTests(unittest.TestCase):
    """The adapter must READ the provenance, not restate the constants."""

    def _run(self, td, **routes):
        H.write_sealed_audit(Path(td))
        with H.mock_service(H.qdrant_routes(**routes)) as (port, _srv):
            ep = H.endpoint("qdrant", port)
            settings = H.settings_with(endpoints=(ep,), host_recovery_audit_base=str(td))
            return LiveQdrantAdapter(settings, http=H.http_client(ep)).verify_collection(META)

    def test_live_verification_reports_observed_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            res = self._run(td)
        self.assertEqual(res.status, "success", res.payload)
        self.assertEqual(res.payload["embedding_model"], QDRANT_EMBEDDING_MODEL)
        self.assertEqual(res.payload["embedding_dim"], QDRANT_VECTOR_SIZE)
        self.assertEqual(res.payload["vector_size"], QDRANT_VECTOR_SIZE)
        self.assertEqual(res.payload["distance"], QDRANT_DISTANCE)
        self.assertEqual(res.payload["source_tree"], QDRANT_SOURCE_TREE)

    def test_a_service_serving_a_different_model_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            res = self._run(td, embedding_model="text-embedding-3-large")
        self.assertEqual(res.status, "malformed")
        self.assertFalse(res.fallback_used, "a provenance mismatch must never fall back")

    def test_a_service_serving_a_different_dimension_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            res = self._run(td, vector_size=3072, embedding_dim=3072)
        self.assertEqual(res.status, "malformed")
        self.assertFalse(res.fallback_used)

    def test_a_service_serving_a_different_corpus_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            res = self._run(td, source_tree="not-the-vault")
        self.assertEqual(res.status, "malformed")
        self.assertFalse(res.fallback_used)

    def test_the_reported_image_is_observed_not_a_stale_literal(self):
        with tempfile.TemporaryDirectory() as td:
            res = self._run(td)
        # The retired code stamped qdrant/qdrant:v1.13.2 whatever answered.
        self.assertIn("1.19.0", res.payload["image"])
        self.assertNotIn("1.13.2", res.payload["image"])


class GatesStayApartTests(unittest.TestCase):
    """Host recovery and canon identity are different questions."""

    def test_host_recovery_assertion_is_not_the_canon_pin(self):
        settings = LiveAdapterSettings()
        self.assertEqual(settings.qdrant_collection, QDRANT_COLLECTION)
        self.assertEqual(settings.qdrant_point_baseline, QDRANT_POINT_BASELINE)
        # The sealed host-recovery audit describes the collection extant at the
        # time of that recovery. It is historical and must not track the canon.
        self.assertEqual(settings.host_recovery_collection, HOST_RECOVERY_COLLECTION)
        self.assertEqual(settings.host_recovery_point_count, HOST_RECOVERY_COUNT)
        self.assertNotEqual(settings.host_recovery_collection, settings.qdrant_collection)

    def test_gate_still_seals_without_a_green_audit(self):
        with tempfile.TemporaryDirectory() as td:
            with H.mock_service(H.qdrant_routes()) as (port, _srv):
                ep = H.endpoint("qdrant", port)
                settings = H.settings_with(endpoints=(ep,), host_recovery_audit_base=str(td))
                adapter = LiveQdrantAdapter(settings, http=H.http_client(ep))
                self.assertFalse(adapter.gate_decision().open)
                res = adapter.verify_collection(META)
        self.assertEqual(res.status, "denied")
        self.assertEqual(res.error_code, "DEPENDENCY_UNHEALTHY")
        self.assertFalse(res.payload["live_contacted"], "a sealed gate must not contact Qdrant")
        self.assertFalse(res.payload["fixture_substituted"])


if __name__ == "__main__":
    unittest.main()
