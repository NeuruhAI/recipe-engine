"""P2-B IAR append-only ledger baseline re-ratification — adversarial battery.

The continuity validator replaces the frozen 143-record total with a
baseline-anchored append-only contract: the ratified baseline stays
byte-anchored by its FULL sha256, the whole chain must link from the literal
GENESIS root, and post-baseline growth must satisfy the reviewed local-only
provider policy.  The current total and head hash are observed evidence only —
legitimate local growth validates without a code change; every tamper class
fails closed.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import tests.founder_runtime.p2b_harness as H
from recipe_engine.founder_runtime.adapters.contract_validation import (
    IAR_LEDGER_BASELINE_COUNT,
    IAR_LEDGER_BASELINE_SHA256,
    validate_iar_ledger_continuity,
    validate_iar_provenance,
)
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.founder_runtime.errors import MalformedResponse, ProvenanceInvalid
from recipe_engine.live_adapters.canonical_topology import (
    CANONICAL_IAR_LEDGER_BASELINE_COUNT,
    CANONICAL_IAR_LEDGER_BASELINE_SHA256,
    TopologyError,
    verify_canonical_topology,
)
from recipe_engine.live_adapters.live_iar import LiveIARAdapter

META = AdapterRequestMeta(
    correlation_id="iar-ledger", plan_id="p", step_id="iar_provenance",
    observed_at="1970-01-01T00:00:00.000Z", timeout_ms=4000,
)

BASELINE = 5  # small synthetic baseline exercises the same machinery


def _ledger(count: int, tmp: Path, **kw) -> dict:
    return H.build_iar_ledger(count, tmp / "ledger.jsonl", baseline_count=kw.pop("baseline_count", BASELINE), **kw)


def _validate(led: dict, **kw) -> dict:
    return validate_iar_ledger_continuity(
        led["path"].read_bytes(),
        baseline_count=kw.pop("baseline_count", led["baseline_count"]),
        baseline_sha256=kw.pop("baseline_sha256", led["baseline_sha256"]),
    )


def _rewrite(led: dict, lines: list[str]) -> None:
    led["path"].write_bytes("".join(line + "\n" for line in lines).encode())


def _rechain(records: list[dict]) -> list[str]:
    """Re-derive a fully consistent chain from record bodies (attacker with
    full write access but NOT the ratified baseline anchor)."""
    import hashlib

    prev = "GENESIS"
    lines = []
    for record in records:
        body = {k: v for k, v in record.items() if k not in ("prev_hash", "entry_hash")}
        rec = {**body, "prev_hash": prev}
        blob = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        rec["entry_hash"] = hashlib.sha256((prev + blob).encode()).hexdigest()
        prev = rec["entry_hash"]
        lines.append(json.dumps(rec, sort_keys=True))
    return lines


class RatifiedAnchorConstants(unittest.TestCase):
    """The shipped anchors are exactly the sealed ratified values."""

    def test_anchor_values(self):
        self.assertEqual(IAR_LEDGER_BASELINE_COUNT, 143)
        self.assertEqual(
            IAR_LEDGER_BASELINE_SHA256,
            "fae0db894e7af106c265671953134fe76d707710e5be0cd9f8e7479fedaec660",
        )
        self.assertEqual(CANONICAL_IAR_LEDGER_BASELINE_COUNT, IAR_LEDGER_BASELINE_COUNT)
        self.assertEqual(CANONICAL_IAR_LEDGER_BASELINE_SHA256, IAR_LEDGER_BASELINE_SHA256)

    def test_topology_pins_and_rejects_wrong_anchor(self):
        import dataclasses

        from recipe_engine.live_adapters.canonical_topology import build_canonical_topology

        base = build_canonical_topology(repo_roots=(), mother_port=8077, aegis_port=8055)
        self.assertEqual(base.iar_ledger_baseline_count, CANONICAL_IAR_LEDGER_BASELINE_COUNT)
        self.assertEqual(base.iar_ledger_baseline_sha256, CANONICAL_IAR_LEDGER_BASELINE_SHA256)
        for field, value in (
            ("iar_ledger_baseline_count", 161),   # a re-pin of the current total is NOT the contract
            ("iar_ledger_baseline_sha256", "a" * 64),
        ):
            with self.assertRaises(TopologyError):
                verify_canonical_topology(dataclasses.replace(base, **{field: value}))


class AppendOnlyContinuity(unittest.TestCase):
    def test_baseline_exactly_validates(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE, Path(tmp))
            out = _validate(led)
            self.assertEqual(out["record_count"], BASELINE)
            self.assertEqual(out["head_entry_hash"], led["head_entry_hash"])
            self.assertEqual(out["ledger_sha256"], led["ledger_sha256"])

    def test_grown_ledger_validates(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 18, Path(tmp))
            out = _validate(led)
            self.assertEqual(out["record_count"], BASELINE + 18)

    def test_synthetic_valid_local_append_validates(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 1, Path(tmp))
            record = H.iar_ledger_record(
                led["head_entry_hash"], BASELINE + 2,
                backend="ollama-llama3.2", model="llama3.2:3b",
            )
            _rewrite(led, led["lines"] + [json.dumps(record, sort_keys=True)])
            out = _validate(led)
            self.assertEqual(out["record_count"], BASELINE + 2)
            self.assertEqual(out["head_entry_hash"], record["entry_hash"])

    def test_future_growth_needs_no_new_anchor(self):
        """Repeated legitimate local appends keep validating with the SAME anchor."""
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE, Path(tmp))
            lines, prev = list(led["lines"]), led["head_entry_hash"]
            for step in range(1, 26):
                record = H.iar_ledger_record(prev, BASELINE + step,
                                             backend="gemma-4-e4b", model="gemma-4-e4b")
                prev = record["entry_hash"]
                lines.append(json.dumps(record, sort_keys=True))
                _rewrite(led, lines)
                out = _validate(led)
                self.assertEqual(out["record_count"], BASELINE + step)

    def test_current_head_is_not_a_ceiling(self):
        """The returned head/count are evidence; no hard-coded current value exists."""
        with TemporaryDirectory() as tmp:
            out = _validate(_ledger(BASELINE + 3, Path(tmp)))
            self.assertEqual(out["record_count"], BASELINE + 3)


class TamperFailsClosed(unittest.TestCase):
    def test_baseline_mutation(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 2, Path(tmp))
            records = [json.loads(line) for line in led["lines"]]
            records[1]["decision_reason"] = "tampered"
            _rewrite(led, _rechain(records))  # consistent chain, altered baseline
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_baseline_deletion(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 2, Path(tmp))
            records = [json.loads(line) for line in led["lines"]]
            del records[2]
            _rewrite(led, _rechain(records))
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_post_baseline_mutation(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 2, Path(tmp))
            records = [json.loads(line) for line in led["lines"]]
            records[-1]["output_sha"] = "0" * 64  # bare edit: breaks its entry hash
            _rewrite(led, [json.dumps(r, sort_keys=True) for r in records])
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_historical_insertion(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 2, Path(tmp))
            records = [json.loads(line) for line in led["lines"]]
            forged = H.iar_ledger_record("GENESIS", 99)
            records.insert(2, forged)
            _rewrite(led, _rechain(records))
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_reordering(self):
        with TemporaryDirectory() as tmp:
            # bare reorder anywhere breaks the chain linkage …
            led = _ledger(BASELINE + 3, Path(tmp))
            records = [json.loads(line) for line in led["lines"]]
            records[-1], records[-2] = records[-2], records[-1]
            _rewrite(led, [json.dumps(r, sort_keys=True) for r in records])
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)
            # … and a fully RECHAINED reorder of historical (baseline) records
            # still fails: the anchored baseline bytes change.
            led2 = _ledger(BASELINE + 3, Path(tmp))
            records = [json.loads(line) for line in led2["lines"]]
            records[1], records[2] = records[2], records[1]
            _rewrite(led2, _rechain(records))
            with self.assertRaises(ProvenanceInvalid):
                _validate(led2)

    def test_broken_prev_hash(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 2, Path(tmp))
            records = [json.loads(line) for line in led["lines"]]
            records[-1]["prev_hash"] = "f" * 64
            _rewrite(led, [json.dumps(r, sort_keys=True) for r in records])
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_invalid_entry_hash(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 2, Path(tmp))
            records = [json.loads(line) for line in led["lines"]]
            records[-1]["entry_hash"] = "e" * 64
            _rewrite(led, [json.dumps(r, sort_keys=True) for r in records])
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_truncation_below_baseline(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 2, Path(tmp))
            _rewrite(led, led["lines"][: BASELINE - 1])
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_truncated_final_record(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 1, Path(tmp))
            raw = led["path"].read_bytes()
            led["path"].write_bytes(raw[:-10])  # cut inside the final record
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_replayed_request_identity(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 1, Path(tmp))
            dup = H.iar_ledger_record(
                led["head_entry_hash"], BASELINE + 2, request_id=f"req-{1:05d}",
                backend="ollama-llama3.2", model="llama3.2:3b",
            )
            _rewrite(led, led["lines"] + [json.dumps(dup, sort_keys=True)])
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_unknown_record_field_and_malformed_json(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE + 1, Path(tmp))
            # chain-valid record carrying an unreviewed extra field still fails
            record = H.iar_ledger_record(led["head_entry_hash"], BASELINE + 2, unknown_grant=True)
            self.assertIn("unknown_grant", record)
            _rewrite(led, led["lines"] + [json.dumps(record, sort_keys=True)])
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)
            _rewrite(led, led["lines"] + ['{"not json'])
            with self.assertRaises(MalformedResponse):
                _validate(led)


class LocalOnlyPolicy(unittest.TestCase):
    def _grown(self, tmp: Path, **kw) -> dict:
        led = _ledger(BASELINE, tmp)
        record = H.iar_ledger_record(led["head_entry_hash"], BASELINE + 1, **kw)
        _rewrite(led, led["lines"] + [json.dumps(record, sort_keys=True)])
        return led

    def test_unknown_backend_fails(self):
        with TemporaryDirectory() as tmp:
            led = self._grown(Path(tmp), backend="mystery-llm", model="mystery")
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_openrouter_record_fails(self):
        with TemporaryDirectory() as tmp:
            led = self._grown(Path(tmp), backend="openrouter-gpt", model="gpt-x")
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_cloud_provider_record_fails(self):
        with TemporaryDirectory() as tmp:
            led = self._grown(Path(tmp), backend="claude-cloud", model="claude", kind="cloud")
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_cloud_kind_on_allowlisted_backend_fails(self):
        with TemporaryDirectory() as tmp:
            led = self._grown(Path(tmp), backend="ollama-llama3.2", model="llama3.2:3b", kind="cloud")
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_nonzero_cost_fails(self):
        with TemporaryDirectory() as tmp:
            led = self._grown(Path(tmp), backend="ollama-llama3.2", model="llama3.2:3b",
                              est_cost_usd=0.004)
            with self.assertRaises(ProvenanceInvalid):
                _validate(led)

    def test_malformed_local_provider_record_fails(self):
        with TemporaryDirectory() as tmp:
            led = self._grown(Path(tmp), backend="ollama-llama3.2", model="")
            with self.assertRaises(MalformedResponse):
                _validate(led)

    def test_malformed_usage_evidence_fails(self):
        with TemporaryDirectory() as tmp:
            led = self._grown(Path(tmp), backend="ollama-llama3.2", model="llama3.2:3b",
                              input_sha="not-a-digest")
            with self.assertRaises(MalformedResponse):
                _validate(led)

    def test_baseline_records_are_anchored_not_policed(self):
        """Baseline content is ratified by its hash; policy governs growth."""
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE, Path(tmp))
            _validate(led)  # bonsai-8b baseline records validate under their anchor


class AnchorMisconfigurationFailsClosed(unittest.TestCase):
    def test_unset_anchor_fails(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE, Path(tmp))
            for count, sha in ((0, led["baseline_sha256"]), (BASELINE, ""), (BASELINE, "zz")):
                with self.assertRaises(ProvenanceInvalid):
                    _validate(led, baseline_count=count, baseline_sha256=sha or "x")

    def test_wrong_anchor_hash_fails(self):
        with TemporaryDirectory() as tmp:
            led = _ledger(BASELINE, Path(tmp))
            with self.assertRaises(ProvenanceInvalid):
                _validate(led, baseline_sha256="0" * 64)


class LiveAdapterCrossCheck(unittest.TestCase):
    """The adapter independently re-validates the file behind the binding."""

    def _adapter_with(self, body: dict, **settings_kw):
        led = H.iar_fixture_ledger()
        from contextlib import ExitStack

        stack = ExitStack()
        port, _srv = stack.enter_context(H.mock_service({("GET", "/provenance"): (200, body)}))
        ep = H.endpoint("iar", port)
        adapter = LiveIARAdapter(H.settings_with(endpoints=(ep,), **settings_kw), http=H.http_client(ep))
        return stack, adapter, led

    def test_success_with_consistent_receipt(self):
        stack, adapter, led = self._adapter_with(H.iar_provenance_body())
        with stack:
            res = adapter.verify_provenance(META)
        self.assertEqual(res.status, "success")
        self.assertEqual(res.payload["continuity"]["record_count"], led["count"])
        self.assertIn(f"iar:ledger_head:{led['head_entry_hash'][:16]}", res.evidence_refs)

    def test_grown_live_ledger_succeeds_without_code_change(self):
        with TemporaryDirectory() as tmp:
            grown = H.build_iar_ledger(H.IAR_FIXTURE_BASELINE_COUNT + 18, Path(tmp) / "l.jsonl")
            body = H.iar_provenance_body()
            body["binding"].update(
                ledger_path=str(grown["path"]), ledger_line_count=grown["count"],
                ledger_sha256_prefix=grown["ledger_sha256"][:16],
            )
            stack, adapter, _ = self._adapter_with(
                body,
                iar_ledger_baseline_count=grown["baseline_count"],
                iar_ledger_baseline_sha256=grown["baseline_sha256"],
            )
            with stack:
                res = adapter.verify_provenance(META)
            self.assertEqual(res.status, "success")
            self.assertEqual(res.payload["continuity"]["record_count"], grown["count"])

    def test_incorrect_head_receipt_fails(self):
        body = H.iar_provenance_body()
        body["binding"]["ledger_line_count"] = body["binding"]["ledger_line_count"] + 1
        stack, adapter, _ = self._adapter_with(body)
        with stack:
            res = adapter.verify_provenance(META)
        self.assertEqual(res.status, "malformed")
        self.assertEqual(res.error_code, "PROVENANCE_INVALID")

    def test_incorrect_head_hash_receipt_fails(self):
        body = H.iar_provenance_body()
        body["binding"]["ledger_sha256_prefix"] = "0123456789abcdef"
        stack, adapter, _ = self._adapter_with(body)
        with stack:
            res = adapter.verify_provenance(META)
        self.assertEqual(res.status, "malformed")
        self.assertEqual(res.error_code, "PROVENANCE_INVALID")

    def test_unreadable_ledger_path_fails(self):
        body = H.iar_provenance_body()
        body["binding"]["ledger_path"] = "/nonexistent/iar.jsonl"
        stack, adapter, _ = self._adapter_with(body)
        with stack:
            res = adapter.verify_provenance(META)
        self.assertEqual(res.status, "malformed")
        self.assertEqual(res.error_code, "PROVENANCE_INVALID")

    def test_unconfigured_anchor_fails_closed(self):
        stack, adapter, _ = self._adapter_with(
            H.iar_provenance_body(), iar_ledger_baseline_count=0, iar_ledger_baseline_sha256="",
        )
        with stack:
            res = adapter.verify_provenance(META)
        self.assertEqual(res.status, "malformed")
        self.assertEqual(res.error_code, "PROVENANCE_INVALID")

    def test_ledger_below_baseline_fails_at_wire(self):
        body = H.iar_provenance_body()
        body["binding"]["ledger_line_count"] = 1
        stack, adapter, _ = self._adapter_with(body)
        with stack:
            res = adapter.verify_provenance(META)
        self.assertEqual(res.status, "malformed")
        self.assertEqual(res.error_code, "PROVENANCE_INVALID")


class WireBindingContract(unittest.TestCase):
    def test_wire_rules(self):
        base = {
            "pid": 1, "command": "iar", "cwd": "/x", "repo_sha": "c" * 40,
            "ledger_path": "/abs/l.jsonl", "ledger_line_count": 143,
            "ledger_sha256_prefix": "fae0db894e7af106", "genesis_continuity": True,
        }
        ok = validate_iar_provenance({"provenance_intact": True}, base)
        self.assertTrue(ok["provenance_intact"])
        cases = [
            {"ledger_line_count": 142},           # below ratified baseline
            {"ledger_line_count": True},          # boolean masquerading as int
            {"ledger_sha256_prefix": "fae0db89"},  # 8 chars — not a 16-hex head receipt
            {"ledger_sha256_prefix": "FAE0DB894E7AF106"},  # uppercase
            {"genesis_continuity": False},
            {"ledger_path": "relative.jsonl"},
        ]
        for tamper in cases:
            with self.assertRaises(ProvenanceInvalid):
                validate_iar_provenance({"provenance_intact": True}, {**base, **tamper})
        with self.assertRaises(ProvenanceInvalid):
            validate_iar_provenance({"provenance_intact": False}, base)


if __name__ == "__main__":
    unittest.main()
