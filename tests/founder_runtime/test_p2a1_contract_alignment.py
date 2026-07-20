"""P2-A.1 Wave-0 contract alignment tests — pure contracts, no live I/O."""

from __future__ import annotations

import ast
import copy
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.adapters.base import (
    AdapterMode,
    OperationalWriteRef,
    WriteClass,
)
from recipe_engine.founder_runtime.adapters.config import AdapterBinding, AdapterConfig
from recipe_engine.founder_runtime.adapters.contract_validation import (
    AEGIS_VERDICTS,
    AXON_CONTAINMENT_CODES,
    IAR_HEALTH_KEYS,
    QDRANT_COLLECTION,
    QDRANT_MANIFEST_ID,
    QDRANT_POINT_BASELINE,
    QDRANT_SCROLL_REQUEST,
    validate_aegis_world,
    validate_axon_governed_evidence,
    validate_axon_governed_read,
    validate_axon_identity,
    validate_axon_tools,
    validate_axon_traversal,
    validate_governance_decision,
    validate_governance_health,
    validate_iar_health,
    validate_iar_provenance,
    validate_mother_equivalence_receipt,
    validate_qdrant_verification,
)
from recipe_engine.founder_runtime.adapters.envelope import (
    AdapterResultStatus,
    SCHEMA_ADAPTER_RESULT,
    SCHEMA_ADAPTER_RESULT_V1,
    make_result,
    parse_adapter_result,
    validate_payload_hash,
)
from recipe_engine.founder_runtime.adapters.fixture_aegis import FixtureAegis
from recipe_engine.founder_runtime.adapters.fixture_cockpit import FixtureCockpit
from recipe_engine.founder_runtime.adapters.fixture_mother import FixtureMother
from recipe_engine.founder_runtime.adapters.identity import (
    DivergenceState,
    EvidenceClass,
    VAULT_RUNTIME_CHECKOUT,
    VAULT_SOURCE_REPO,
    normalize_vault_dual_roots,
    vault_provenance,
)
from recipe_engine.founder_runtime.adapters.protocols import (
    AdapterRequestMeta,
    AegisEvidenceAdapter,
    AxonReadonlyAdapter,
    CockpitPort,
    GovernanceAction,
    GovernanceDecisionAdapter,
    IARInferenceAdapter,
    MotherHealthAdapter,
    QdrantRetrievalAdapter,
    VaultDocumentAdapter,
)
from recipe_engine.founder_runtime.adapters.registry import AdapterRegistry
from recipe_engine.founder_runtime.adapters.timeouts import (
    OPERATION_POLICIES,
    operation_policy,
    resolve_operation_policy,
    retry_permitted,
)
from recipe_engine.founder_runtime.errors import (
    ConfigurationMissing,
    ErrorCode,
    LiveFallbackForbidden,
    MalformedResponse,
    ProvenanceInvalid,
    QdrantCountChanged,
    UnsafeCapability,
)


SHA = "a" * 64
OLD_FIELDS = {
    "schema_version", "adapter_name", "adapter_version", "adapter_mode", "operation",
    "status", "observed_at", "duration_ms", "correlation_id", "causation_id",
    "mission_id", "plan_id", "step_id", "authority_class", "mutation_attempted",
    "dependency_identity", "source_identity", "source_version_or_sha", "provenance",
    "evidence_refs", "payload_hash", "error_code", "retryable", "fallback_used", "payload",
}
NEW_FIELDS = {
    "declared_timeout_ms", "effective_timeout_ms", "retry_policy", "write_class",
    "upstream_writes",
}


def result(**overrides):
    values = dict(
        adapter_name="github",
        adapter_version="fixture",
        adapter_mode=AdapterMode.FIXTURE,
        operation="repo_state",
        status=AdapterResultStatus.SUCCESS,
        observed_at="2026-07-20T00:00:00Z",
        correlation_id="corr",
        authority_class="A0",
        dependency_identity={"service": "fixture"},
        source_identity={"mode": "fixture"},
        source_version_or_sha="fixture",
        provenance={"evidence_class": "fixture"},
        payload={"ok": True},
    )
    values.update(overrides)
    return make_result(**values)


class K1TimeoutPolicyTests(unittest.TestCase):
    def test_table_is_keyed_by_adapter_and_operation(self):
        self.assertIn(("github", "repo_state"), OPERATION_POLICIES)
        self.assertIn(("axon_readonly", "governed_read"), OPERATION_POLICIES)

    def test_required_defaults(self):
        self.assertEqual(operation_policy("github", "repo_state").default_timeout_ms, 10_000)
        self.assertEqual(operation_policy("axon_readonly", "governed_read").default_timeout_ms, 30_000)
        self.assertEqual(operation_policy("iar", "health").default_timeout_ms, 6_000)
        self.assertEqual(operation_policy("iar", "health_subrequest").default_timeout_ms, 3_000)

    def test_meta_resolves_operation_default(self):
        meta = AdapterRequestMeta(correlation_id="c")
        self.assertEqual(meta.policy_for("aegis", "validate_world").effective_timeout_ms, 10_000)

    def test_override_above_ceiling_fails_closed(self):
        with self.assertRaises(UnsafeCapability):
            resolve_operation_policy("axon_readonly", "governed_read", 30_001)

    def test_iar_declared_range_is_bounded(self):
        self.assertEqual(
            resolve_operation_policy("iar", "interpret_local_deferred", 300_000).effective_timeout_ms,
            300_000,
        )
        with self.assertRaises(UnsafeCapability):
            resolve_operation_policy("iar", "interpret_local_deferred", 300_001)

    def test_retry_is_explicit_and_classified(self):
        q = resolve_operation_policy("qdrant", "manifest_scroll")
        self.assertEqual(q.policy.max_attempts, 2)
        self.assertTrue(q.policy.retry_requires_fresh_before_count)
        self.assertTrue(retry_permitted(q, "connect"))
        self.assertFalse(retry_permitted(q, ErrorCode.AUTHENTICATION_FAILED.value))
        ax = resolve_operation_policy("axon_readonly", "governed_read")
        self.assertEqual(ax.policy.timeout_classification, ErrorCode.SIDE_EFFECT_UNCERTAIN.value)
        self.assertFalse(retry_permitted(ax, "connect"))

    def test_unknown_operation_fails_closed(self):
        with self.assertRaises(ConfigurationMissing):
            operation_policy("github", "arbitrary")

    def test_envelope_carries_declared_and_effective(self):
        envelope = result()
        self.assertEqual(envelope.declared_timeout_ms, 10_000)
        self.assertEqual(envelope.effective_timeout_ms, 10_000)
        self.assertEqual(envelope.retry_policy["max_attempts"], 1)

    def test_meta_override_reaches_fixture_envelope_and_ceiling(self):
        adapter = FixtureMother()
        allowed = AdapterRequestMeta(correlation_id="c", observed_at="t", timeout_ms=4_000)
        envelope = adapter.health_result(observed_at="t", meta=allowed)
        self.assertEqual(envelope.declared_timeout_ms, 4_000)
        self.assertEqual(envelope.effective_timeout_ms, 4_000)
        denied = AdapterRequestMeta(correlation_id="c", observed_at="t", timeout_ms=5_001)
        with self.assertRaises(UnsafeCapability):
            adapter.health_result(observed_at="t", meta=denied)


class K2EnvelopeHashTests(unittest.TestCase):
    def test_validation_is_pure(self):
        envelope = result(payload={"nested": {"values": [1, 2]}})
        before = copy.deepcopy(envelope.to_dict())
        self.assertTrue(validate_payload_hash(envelope))
        self.assertEqual(envelope.to_dict(), before)

    def test_missing_hash_fails_without_backfill(self):
        envelope = result()
        envelope.payload_hash = ""
        before = copy.deepcopy(envelope.to_dict())
        self.assertFalse(validate_payload_hash(envelope))
        self.assertEqual(envelope.to_dict(), before)

    def test_mismatch_fails_without_mutation(self):
        envelope = result()
        envelope.payload["ok"] = False
        before = copy.deepcopy(envelope.to_dict())
        self.assertFalse(validate_payload_hash(envelope))
        self.assertEqual(envelope.to_dict(), before)

    def test_recompute_compatibility_helper_is_nonmutating(self):
        envelope = result()
        stored = envelope.payload_hash
        envelope.payload["new"] = True
        computed = envelope.recompute_payload_hash()
        self.assertNotEqual(computed, stored)
        self.assertEqual(envelope.payload_hash, stored)


class EnvelopeCompatibilityTests(unittest.TestCase):
    def test_v11_retains_original_25_fields(self):
        fields = set(result().to_dict())
        self.assertEqual(OLD_FIELDS | NEW_FIELDS, fields)
        self.assertEqual(SCHEMA_ADAPTER_RESULT, "fr.adapter_result.v1.1")

    def test_v11_round_trip(self):
        original = result()
        parsed = parse_adapter_result(original.to_dict())
        self.assertEqual(parsed.to_dict(), original.to_dict())

    def test_v1_requires_explicit_compatibility(self):
        raw = result().to_dict()
        raw["schema_version"] = SCHEMA_ADAPTER_RESULT_V1
        for key in NEW_FIELDS:
            raw.pop(key)
        with self.assertRaises(MalformedResponse):
            parse_adapter_result(raw)
        parsed = parse_adapter_result(raw, allow_v1_compatibility=True)
        self.assertIsNone(parsed.write_class)
        self.assertEqual(parsed.upstream_writes, [])

    def test_v11_missing_security_field_rejected(self):
        raw = result().to_dict()
        raw.pop("write_class")
        with self.assertRaises(MalformedResponse):
            parse_adapter_result(raw)

    def test_v1_bad_hash_not_backfilled(self):
        raw = result().to_dict()
        raw["schema_version"] = SCHEMA_ADAPTER_RESULT_V1
        for key in NEW_FIELDS:
            raw.pop(key)
        raw["payload_hash"] = ""
        with self.assertRaises(MalformedResponse):
            parse_adapter_result(raw, allow_v1_compatibility=True)
        self.assertEqual(raw["payload_hash"], "")

    def test_legacy_live_result_is_not_accepted_as_compatible(self):
        raw = result().to_dict()
        raw["schema_version"] = SCHEMA_ADAPTER_RESULT_V1
        raw["adapter_mode"] = "live"
        for key in NEW_FIELDS:
            raw.pop(key)
        with self.assertRaises(MalformedResponse):
            parse_adapter_result(raw, allow_v1_compatibility=True)


class K3WriteClassificationTests(unittest.TestCase):
    def test_exact_vocabulary(self):
        self.assertEqual(
            {item.value for item in WriteClass},
            {"mission_truth", "operational_metadata", "external_mutation", "prohibited"},
        )

    def test_pure_read_is_explicitly_no_write(self):
        envelope = result()
        self.assertIsNone(envelope.write_class)
        self.assertEqual(envelope.upstream_writes, [])

    def test_unknown_classification_fails(self):
        with self.assertRaises(UnsafeCapability):
            result(write_class="mystery")

    def test_mission_truth_external_and_prohibited_writes_fail(self):
        for write_class in (
            WriteClass.MISSION_TRUTH,
            WriteClass.EXTERNAL_MUTATION,
            WriteClass.PROHIBITED,
        ):
            with self.subTest(write_class=write_class):
                with self.assertRaises(UnsafeCapability):
                    result(write_class=write_class)

    def test_fixture_cannot_claim_operational_write(self):
        ref = OperationalWriteRef("ledger", "fixture")
        with self.assertRaises(LiveFallbackForbidden):
            result(write_class=WriteClass.OPERATIONAL_METADATA, upstream_writes=[ref])

    def test_live_operational_write_requires_bounded_reference(self):
        envelope = result(
            adapter_name="axon_readonly",
            adapter_mode=AdapterMode.LIVE,
            operation="governed_read",
            source_identity={"mode": "live"},
            write_class=WriteClass.OPERATIONAL_METADATA,
            upstream_writes=[OperationalWriteRef("axon_gateway_receipt", "safe_receipt_dir")],
        )
        self.assertEqual(envelope.write_class, "operational_metadata")
        self.assertTrue(envelope.upstream_writes[0]["bounded"])

    def test_metadata_write_not_used_by_completion_verifier(self):
        source = (ROOT / "recipe_engine/founder_runtime/runtime/completion.py").read_text()
        self.assertNotIn("upstream_writes", source)
        self.assertNotIn("write_class", source)


class K4VaultTests(unittest.TestCase):
    def test_alias_normalizes_to_dual_root(self):
        config = normalize_vault_dual_roots({"NEURUH_VAULT_ROOT": VAULT_SOURCE_REPO})
        self.assertEqual(config.source_root, VAULT_SOURCE_REPO)
        self.assertEqual(config.runtime_root, VAULT_RUNTIME_CHECKOUT)
        self.assertEqual(config.selected_role, "source")

    def test_runtime_alias_normalizes_without_sync_claim(self):
        config = normalize_vault_dual_roots({"VAULT_ROOT": VAULT_RUNTIME_CHECKOUT})
        self.assertEqual(config.selected_role, "runtime")

    def test_ambiguous_aliases_fail(self):
        with self.assertRaises(ProvenanceInvalid):
            normalize_vault_dual_roots(
                {"NEURUH_VAULT_ROOT": VAULT_SOURCE_REPO, "VAULT_ROOT": VAULT_RUNTIME_CHECKOUT}
            )

    def test_missing_alias_fails(self):
        with self.assertRaises(ConfigurationMissing):
            normalize_vault_dual_roots({})

    def test_rejected_clone_sha_fails(self):
        with self.assertRaises(ProvenanceInvalid):
            normalize_vault_dual_roots({"VAULT_ROOT": VAULT_SOURCE_REPO}, source_sha="d970ceb123")

    def test_runtime_ahead_dirty_state(self):
        provenance = vault_provenance(
            source_sha="a", runtime_sha="b", retrieval_timestamp="t",
            evidence_class=EvidenceClass.LIVE_OBSERVED,
            source_dirty=False, runtime_dirty_count=2, ahead=331, behind=0,
            ancestry="runtime_ahead", manifest_allowlist_version="v1",
        )
        self.assertEqual(provenance.divergence_state, DivergenceState.DIVERGED_RUNTIME_AHEAD_DIRTY)
        with self.assertRaises(ProvenanceInvalid):
            provenance.assert_for_sync_claim()

    def test_unknown_cannot_support_sync(self):
        provenance = vault_provenance(
            source_sha="", runtime_sha="", retrieval_timestamp="t",
            evidence_class=EvidenceClass.UNAVAILABLE,
        )
        with self.assertRaises(ProvenanceInvalid):
            provenance.assert_for_sync_claim()


class K5NoFallbackTests(unittest.TestCase):
    def test_missing_binding_fails_closed(self):
        registry = AdapterRegistry(AdapterConfig(bindings=()))
        with self.assertRaises(ConfigurationMissing):
            registry.resolve("mother")

    def test_caller_cannot_downgrade_live_binding_to_fixture(self):
        registry = AdapterRegistry(
            AdapterConfig(
                allow_live=True,
                bindings=(AdapterBinding("mother", AdapterMode.LIVE, "live"),),
            )
        )
        with self.assertRaises(LiveFallbackForbidden):
            registry.resolve("mother", mode=AdapterMode.FIXTURE)

    def test_live_factory_exception_is_not_replaced_by_fixture(self):
        registry = AdapterRegistry(
            AdapterConfig(
                allow_live=True,
                bindings=(AdapterBinding("mother", AdapterMode.LIVE, "live"),),
            )
        )
        registry.register("mother", "live", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
        with self.assertRaisesRegex(RuntimeError, "offline"):
            registry.resolve("mother")

    def test_live_factory_cannot_return_fixture_identity(self):
        registry = AdapterRegistry(
            AdapterConfig(
                allow_live=True,
                bindings=(AdapterBinding("mother", AdapterMode.LIVE, "live"),),
            )
        )
        registry.register("mother", "live", FixtureMother)
        with self.assertRaises(LiveFallbackForbidden):
            registry.resolve("mother")


class K6ToK12ContractTests(unittest.TestCase):
    def test_protocol_methods_exist(self):
        expected = {
            MotherHealthAdapter: {"read_equivalence_receipt"},
            VaultDocumentAdapter: {"provenance_state"},
            QdrantRetrievalAdapter: {"verify_collection"},
            IARInferenceAdapter: {"health", "verify_provenance"},
            AegisEvidenceAdapter: {"validate_world"},
            GovernanceDecisionAdapter: {"health", "default_policy", "decide"},
            AxonReadonlyAdapter: {"identity", "tools_census", "governed_read", "traversal_probe"},
        }
        for protocol, methods in expected.items():
            with self.subTest(protocol=protocol.__name__):
                self.assertTrue(methods.issubset(set(protocol.__dict__)))

    def test_cockpit_protocol_matches_fixture(self):
        self.assertIsInstance(FixtureCockpit(), CockpitPort)

    def test_governance_action_cannot_carry_policy(self):
        action = GovernanceAction("a", "actor", "tool", "domain", "summary")
        self.assertNotIn("policy", action.to_dict())
        self.assertNotIn("policy", inspect.signature(GovernanceAction).parameters)

    def test_aegis_fixture_uses_new_signature(self):
        self.assertIn("world_id", inspect.signature(FixtureAegis.validate_world).parameters)
        self.assertNotIn("assess_risk", AegisEvidenceAdapter.__dict__)

    def test_aegis_valid_and_contradictory_are_successful_opinions(self):
        meta = AdapterRequestMeta(correlation_id="c", observed_at="t")
        adapter = FixtureAegis()
        for world in ("fixture_world", "fixture_contradictory_world"):
            envelope = adapter.validate_world(world, meta)
            self.assertEqual(envelope.status, "success")
            self.assertIn(envelope.payload["verdict"], AEGIS_VERDICTS)
            validate_aegis_world(envelope.payload)

    def test_unknown_aegis_world_is_unsafe(self):
        envelope = FixtureAegis().validate_world(
            "../secret", AdapterRequestMeta(correlation_id="c", observed_at="t")
        )
        self.assertEqual(envelope.status, "unsafe")


class PureResponseValidatorTests(unittest.TestCase):
    def test_mother_bound_receipt(self):
        receipt = {
            "services": [{
                "port": 8848, "status": 200, "body_sha256": SHA,
                "pid": 10, "command": "governance", "cwd": "/canonical", "listener": "127.0.0.1",
            }],
            "mutation_path": "MUTATION_PATH_NOT_RATIFIED",
            "cockpit_sha": "b" * 40,
            "cycle_id": "cycle-1",
            "timestamp": "2026-07-20T00:00:00Z",
            "seal": "P1.1 READ-ONLY RUNTIME RATIFIED",
        }
        self.assertEqual(validate_mother_equivalence_receipt(receipt)["binding"]["cycle_id"], "cycle-1")
        bad = copy.deepcopy(receipt)
        bad["mutation_path"] = "RATIFIED"
        with self.assertRaises(UnsafeCapability):
            validate_mother_equivalence_receipt(bad)

    def test_qdrant_contract_and_exact_scroll_body(self):
        self.assertEqual(
            QDRANT_SCROLL_REQUEST,
            {"limit": 1, "with_payload": True, "with_vector": False},
        )
        payload = {
            "collection": QDRANT_COLLECTION,
            "endpoint_class": "loopback",
            "server_identity": "qdrant",
            "container": "neuruh-qdrant",
            "image": "qdrant/qdrant:v1.13.2",
            "count_before": QDRANT_POINT_BASELINE,
            "count_after": QDRANT_POINT_BASELINE,
            "point_id_hash": SHA,
            "manifest_id": QDRANT_MANIFEST_ID,
            "manifest_hash": SHA,
            "request_hashes": [SHA],
            "response_hashes": [SHA],
            "mutation_attempted": False,
        }
        self.assertEqual(validate_qdrant_verification(payload)["count_after"], 3049)
        payload["count_after"] += 1
        with self.assertRaises(QdrantCountChanged):
            validate_qdrant_verification(payload)

    def test_iar_exact_health_and_provenance(self):
        health = {
            "enabled": True,
            "active_backend": "local",
            "active_model": "model",
            "backends": {"local": True},
            "degraded_reason": None,
        }
        self.assertEqual(set(validate_iar_health(health)), IAR_HEALTH_KEYS)
        with self.assertRaises(MalformedResponse):
            validate_iar_health({**health, "extra": True})
        binding = {
            "pid": 1, "command": "iar", "cwd": "/repo", "repo_sha": "c" * 40,
            "ledger_path": "/repo/ledger.jsonl", "ledger_line_count": 143,
            "ledger_sha256_prefix": "fae0db89", "genesis_continuity": True,
        }
        self.assertTrue(validate_iar_provenance({"provenance_intact": True}, binding)["provenance_intact"])

    def test_governance_contracts(self):
        self.assertTrue(validate_governance_health({"ok": True, "service": "autonomous-governance-core"})["ok"])
        decision = {
            "action_id": "a", "decision": "escalate", "risk_score": 0.8,
            "thresholds": {}, "reasons": [], "required_next_steps": [], "audit_id": "id",
            "action": {"action_id": "a"}, "created_at": "t",
        }
        self.assertEqual(validate_governance_decision(decision)["decision"], "escalate")
        decision["action"]["policy"] = {}
        with self.assertRaises(UnsafeCapability):
            validate_governance_decision(decision)

    def test_axon_identity_tools_and_traversal(self):
        identity = {
            "status": "healthy", "version": "2.4.0-p1.1",
            "inference": {"authority": "iar", "provider": "local", "model": "m", "health": "ok"},
            "execution_available": True, "degraded_reason": None,
        }
        self.assertEqual(validate_axon_identity(identity)["version"], "2.4.0-p1.1")
        self.assertEqual(validate_axon_tools({"tools": ["file_read"]})["tools"], ["file_read"])
        for code in AXON_CONTAINMENT_CODES:
            self.assertEqual(validate_axon_traversal({"ok": False, "error_codes": [code]})["error_code"], code)
        with self.assertRaises(UnsafeCapability):
            validate_axon_tools({"tools": ["file_read", "run_command"]})
        with self.assertRaises(UnsafeCapability):
            validate_axon_traversal({"ok": True, "error_codes": []})

    def test_axon_governed_read_requires_receipted_metadata_write(self):
        payload = {
            "taskId": "task", "status": "completed", "governance": {"decision": "allow"},
            "subtasks": [{"tool": "file_read", "status": "succeeded", "path": "safe_read"}],
            "path": "safe_read", "output_hash": SHA, "mutation": False,
        }
        validate_axon_governed_read(payload, expected_path="safe_read")
        envelope = result(
            adapter_name="axon_readonly", adapter_mode=AdapterMode.LIVE,
            operation="governed_read", payload=payload,
            source_identity={"mode": "live"}, mission_id="m", plan_id="p", step_id="s",
            evidence_refs=["receipt:hash"], write_class=WriteClass.OPERATIONAL_METADATA,
            upstream_writes=[OperationalWriteRef("axon_gateway_receipt", "safe_receipt_dir")],
        )
        self.assertEqual(validate_axon_governed_evidence(envelope, expected_path="safe_read")["taskId"], "task")
        missing = copy.deepcopy(envelope)
        missing.evidence_refs.clear()
        with self.assertRaises(ProvenanceInvalid):
            validate_axon_governed_evidence(missing, expected_path="safe_read")


class SafetyRegressionTests(unittest.TestCase):
    def test_domain_error_codes_present(self):
        for code in (
            "QDRANT_COUNT_MISMATCH", "QDRANT_COUNT_CHANGED",
            "QDRANT_COLLECTION_MISMATCH", "QDRANT_PAYLOAD_PROVENANCE_MISMATCH",
        ):
            self.assertEqual(getattr(ErrorCode, code).value, code)

    def test_axon_mutation_hard_stop_gates_unchanged(self):
        from recipe_engine.founder_runtime.adapters.axon_mutation import MUTATION_GATES, mutation_authorized
        self.assertFalse(mutation_authorized())
        self.assertFalse(any(MUTATION_GATES.values()))

    def test_contract_modules_have_no_live_io_imports(self):
        adapter_root = ROOT / "recipe_engine/founder_runtime/adapters"
        forbidden = {"socket", "subprocess", "requests", "http", "urllib"}
        violations = []
        for path in adapter_root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    violations.extend((path, alias.name) for alias in node.names if alias.name.split(".")[0] in forbidden)
                elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in forbidden:
                    violations.append((path, node.module))
        self.assertEqual(violations, [])

    def test_no_environment_or_fallback_switches(self):
        adapter_root = ROOT / "recipe_engine/founder_runtime/adapters"
        text = "\n".join(path.read_text() for path in adapter_root.rglob("*.py"))
        self.assertNotIn("os.environ", text)
        self.assertNotIn("getenv(", text)
        self.assertNotIn("fallback_used=True", text)


if __name__ == "__main__":
    unittest.main()
