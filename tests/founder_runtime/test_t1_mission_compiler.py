"""T1 Phase-4: mission contract v2, extraction, strategy generation, plan custody.

Covers the gaps the Phase-4 audit found: a Mission Contract missing five of its
thirteen required fields, constraints that were a fixed tuple no founder could
influence, a dead validation branch that discarded every schema error on a
blocked mission, no strategy generator at all, and plans that were neither
persisted, versioned, nor bound to the mission that compiled them.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe_engine.founder_runtime.clock import SteppingClock
from recipe_engine.founder_runtime.contracts.mission import (
    Mission,
    SCHEMA_MISSION,
    SCHEMA_MISSION_V1,
)
from recipe_engine.founder_runtime.errors import MissionSchemaInvalid, PersistenceConflict
from recipe_engine.founder_runtime.intent.compiler import compile_intent_to_mission
from recipe_engine.founder_runtime.intent.constraints import (
    AuthorityEnvelope,
    extract_time_budget,
    extract_with_provenance,
)
from recipe_engine.founder_runtime.runtime.mission_runtime import MissionRuntime
from recipe_engine.founder_runtime.state.states import MissionState
from recipe_engine.founder_runtime.strategy import generate_candidate_strategies

NOW = "2026-08-16T00:00:00.000Z"
READ_ONLY_OBJECTIVE = (
    "Determine the three strongest technically evidenced improvements to DeedSonar "
    "candidate ranking without changing production or contacting anyone."
)


def _tmp_db() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    path = tmp.name
    tmp.close()
    return path


def _compile(text=READ_ONLY_OBJECTIVE, envelope=AuthorityEnvelope.READ_ONLY_LIVE):
    mission, _ = compile_intent_to_mission(text, clock_now=NOW, envelope=envelope)
    return mission


# ============================================================ contract v2
class MissionContractV2Tests(unittest.TestCase):
    """All thirteen Phase-4 contract fields, present and populated."""

    def test_every_required_field_is_present(self):
        m = _compile()
        body = m.to_dict()
        required = [
            "mission_id", "objective", "verification_criteria", "non_goals",
            "constraints", "authority_requirements", "time_budget", "resource_budget",
            "required_evidence", "context_requirements", "world", "domain",
            "risk_class", "approval_gates",
        ]
        for f in required:
            self.assertIn(f, body, f"missing contract field: {f}")
            self.assertIsNotNone(body[f], f"contract field is None: {f}")

    def test_budgets_are_bounded_not_absent(self):
        m = _compile()
        # "no stated budget" must never mean "unbounded".
        self.assertTrue(m.time_budget)
        self.assertGreater(m.resource_budget["max_steps"], 0)
        self.assertGreater(m.resource_budget["max_adapter_calls"], 0)
        self.assertGreater(m.resource_budget["max_replans"], 0)

    def test_world_and_domain_are_bound(self):
        m = _compile()
        self.assertEqual(m.world, "neuruh")
        self.assertTrue(m.domain)

    def test_approval_gates_record_the_missions_own_gates(self):
        m = _compile()
        self.assertTrue(m.approval_gates)
        # Approval never confers execution — recorded on every contract.
        self.assertIn("approval_confers_no_execution", m.approval_gates)

    def test_required_evidence_rolls_up_deliverable_requirements(self):
        m = _compile()
        self.assertIn("execution_receipts", m.required_evidence)
        for d in m.deliverables:
            for ev in d.evidence_requirements:
                self.assertIn(ev, m.required_evidence)

    def test_v1_rows_still_load(self):
        """A mission persisted under v1 must survive the schema bump."""
        body = _compile().to_dict()
        body["schema_version"] = SCHEMA_MISSION_V1
        for f in ("non_goals", "world", "domain", "time_budget",
                  "resource_budget", "required_evidence", "approval_gates"):
            body.pop(f)
        restored = Mission.from_dict(body)
        self.assertEqual(restored.schema_version, SCHEMA_MISSION_V1)
        self.assertEqual(restored.non_goals, [])
        self.assertEqual(restored.resource_budget, {})
        self.assertIsNone(restored.time_budget)

    def test_identity_covers_the_new_fields(self):
        """Two missions differing only in what they refuse must not collide."""
        a = _compile()
        b = _compile()
        self.assertEqual(a.recompute_input_hash(), b.recompute_input_hash())
        b.non_goals = list(b.non_goals) + ["something_else_entirely"]
        self.assertNotEqual(a.input_hash, b.recompute_input_hash())


# ============================================================ extraction
class ExtractionTests(unittest.TestCase):
    def test_founder_stated_non_goals_reach_the_contract(self):
        m = _compile()
        self.assertIn("changing_production_state", m.non_goals)
        self.assertIn("contacting_third_parties", m.non_goals)

    def test_envelope_decides_the_live_adapter_constraint(self):
        fixture = _compile(envelope=AuthorityEnvelope.READ_ONLY_FIXTURE)
        live = _compile(envelope=AuthorityEnvelope.READ_ONLY_LIVE)
        self.assertIn("fixture_mode_only", fixture.constraints)
        self.assertIn("no_live_adapters", fixture.constraints)
        # A deliberately live read-only mission must not assert fixture_mode_only.
        self.assertNotIn("fixture_mode_only", live.constraints)
        self.assertIn("live_read_only_adapters_permitted", live.constraints)

    def test_both_envelopes_forbid_mutation(self):
        for env in (AuthorityEnvelope.READ_ONLY_FIXTURE, AuthorityEnvelope.READ_ONLY_LIVE):
            self.assertIn("no_production_mutation", _compile(envelope=env).constraints)
            self.assertIn("no_secret_access", _compile(envelope=env).constraints)

    def test_every_extracted_item_carries_its_evidence(self):
        result = extract_with_provenance(READ_ONLY_OBJECTIVE,
                                         envelope=AuthorityEnvelope.READ_ONLY_LIVE)
        items = {p["item"] for p in result.provenance}
        for c in result.constraints:
            self.assertIn(c, items)
        for n in result.non_goals:
            self.assertIn(n, items)
        phrase_sourced = [p for p in result.provenance if p["source"].startswith("phrase:")]
        self.assertTrue(phrase_sourced, "no constraint was traced to founder wording")

    def test_extraction_is_deterministic(self):
        a = extract_with_provenance(READ_ONLY_OBJECTIVE)
        b = extract_with_provenance(READ_ONLY_OBJECTIVE)
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_stated_deadline_is_honoured(self):
        self.assertEqual(extract_time_budget("do it within an hour"), "PT1H")
        self.assertNotEqual(extract_time_budget("no stated bound"), "")


class BlockedInputValidationTests(unittest.TestCase):
    """The dead branch discarded every schema error on a blocked mission."""

    def test_blocked_mission_is_not_exempt_from_schema_validation(self):
        import recipe_engine.founder_runtime.intent.compiler as compiler_mod

        original = compiler_mod.validate_mission_dict
        compiler_mod.validate_mission_dict = lambda d: ["invalid autonomy_level: bogus"]
        try:
            with self.assertRaises(MissionSchemaInvalid):
                compile_intent_to_mission("???", clock_now=NOW)
        finally:
            compiler_mod.validate_mission_dict = original

    def test_a_genuinely_blocked_mission_still_compiles(self):
        mission, _ = compile_intent_to_mission("???", clock_now=NOW)
        self.assertEqual(mission.current_state, MissionState.BLOCKED_INPUT)
        self.assertTrue(mission.extensions.get("blocked_question"))


# ============================================================ generation
class StrategyGenerationTests(unittest.TestCase):
    def test_strategies_are_derived_from_observed_evidence(self):
        m = _compile()
        rich = generate_candidate_strategies(
            m, claim_ids=["claim_overall_health", "claim_blocked", "claim_source_identity"],
            available_sources=["mother", "github", "vault"])
        poor = generate_candidate_strategies(m, claim_ids=[], available_sources=[])
        rich_best = rich.candidates[0].expected_outcomes[0].value
        poor_best = poor.candidates[0].expected_outcomes[0].value
        # Same code, different evidence, different result: that is what makes
        # this generation rather than a second hardcoding.
        self.assertGreater(rich_best, poor_best)

    def test_missing_sources_become_declared_evidence_gaps(self):
        m = _compile()
        report = generate_candidate_strategies(
            m, claim_ids=["claim_overall_health"], available_sources=["mother"])
        needs_vault = [c for c in report.candidates if "vault" in c.dependencies]
        self.assertTrue(needs_vault)
        for c in needs_vault:
            self.assertIn("source:vault", c.evidence_gaps)
            self.assertIn("vault", c.missing_dependencies)

    def test_non_goals_eliminate_action_classes(self):
        m = _compile()
        report = generate_candidate_strategies(
            m, claim_ids=["claim_overall_health"], available_sources=["mother"])
        for c in report.candidates:
            for ac in c.action_classes:
                self.assertIn(ac, report.admissible_action_classes)
                self.assertNotIn(ac, ("mutate", "deploy", "execute", "write"))

    def test_read_only_mission_cannot_generate_a_mutating_strategy(self):
        m = _compile()
        report = generate_candidate_strategies(
            m, claim_ids=["claim_overall_health"], available_sources=["mother"])
        self.assertNotIn("mutate", report.admissible_action_classes)
        self.assertNotIn("deploy", report.admissible_action_classes)

    def test_candidate_count_is_capped(self):
        m = _compile()
        report = generate_candidate_strategies(
            m, claim_ids=["claim_overall_health"], available_sources=["mother"],
            max_candidates=2)
        self.assertLessEqual(len(report.candidates), 2)
        self.assertTrue(any(r["reason"] == "exceeded_candidate_cap" for r in report.rejected))

    def test_generation_is_deterministic(self):
        m = _compile()
        args = dict(claim_ids=["claim_overall_health", "claim_blocked"],
                    available_sources=["mother", "github"])
        self.assertEqual(
            generate_candidate_strategies(m, **args).to_dict(),
            generate_candidate_strategies(m, **args).to_dict(),
        )

    def test_every_candidate_is_traceable_to_its_mission(self):
        m = _compile()
        report = generate_candidate_strategies(
            m, claim_ids=["claim_overall_health"], available_sources=["mother"])
        for c in report.candidates:
            self.assertEqual(c.extensions["generated_from_mission"], m.mission_id)
            self.assertTrue(c.feasibility_notes)


# ============================================================ plan custody
class PlanCustodyTests(unittest.TestCase):
    def setUp(self):
        self.rt = MissionRuntime(_tmp_db(), clock=SteppingClock(), adapter_mode="fixture")
        m = self.rt.create_mission_from_text("status organism", creation_idempotency_key="t1-plan")
        self.mid = m.mission_id

    def tearDown(self):
        self.rt.close()

    def _commit(self, plan_id: str, *, supersedes=None, strategy="strat_a"):
        return self.rt.commit_plan(
            self.mid,
            plan={"plan_id": plan_id, "plan_receipt": f"receipt_{plan_id}"},
            packet={"packet_id": f"pkt_{plan_id}", "receipt_hash": "h"},
            strategy_id=strategy,
            authority_binding={"authority_class": "A0", "approval_confers_execution": False},
            evidence_refs=["ev_1"],
            supersedes=supersedes,
        )

    def test_plan_is_persisted_and_retrievable(self):
        self._commit("plan_1")
        stored = self.rt.store.get_plan("plan_1")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["mission_id"], self.mid)

    def test_plan_is_bound_to_the_mission_that_compiled_it(self):
        self._commit("plan_1")
        # The mission's plan_id used to be an independently minted string that no
        # compiled plan ever had.
        self.assertEqual(self.rt.get_mission(self.mid).plan_id, "plan_1")

    def test_replan_supersedes_rather_than_overwrites(self):
        self._commit("plan_1")
        self._commit("plan_2", supersedes="plan_1", strategy="strat_b")
        lineage = self.rt.store.list_plans(self.mid)
        self.assertEqual([p["plan_version"] for p in lineage], [1, 2])
        self.assertEqual(lineage[1]["supersedes"], "plan_1")
        # The superseded plan is still retrievable.
        self.assertIsNotNone(self.rt.store.get_plan("plan_1"))
        self.assertEqual(self.rt.store.latest_plan(self.mid)["plan_id"], "plan_2")

    def test_plan_carries_authority_and_evidence_binding(self):
        record = self._commit("plan_1")
        self.assertFalse(record["authority_binding"]["approval_confers_execution"])
        self.assertEqual(record["evidence_refs"], ["ev_1"])
        self.assertEqual(record["packet_id"], "pkt_plan_1")

    def test_packet_is_resolvable_from_the_plan(self):
        record = self._commit("plan_1")
        packet = self.rt.store.get_decision_packet(record["packet_id"])
        self.assertIsNotNone(packet, "a plan's packet_id must resolve to a stored packet")

    def test_plan_requires_a_logged_event(self):
        """A plan cannot be persisted without its canonical event."""
        with self.assertRaises(PersistenceConflict):
            self.rt.store.save_plan({
                "plan_id": "plan_unlogged", "mission_id": self.mid, "plan_version": 99,
                "strategy_id": "x", "plan_receipt": "r", "created_at": "2026-08-16T00:00:00.000Z",
            })

    def test_commit_is_idempotent(self):
        first = self._commit("plan_1")
        again = self._commit("plan_1")
        self.assertEqual(first["plan_id"], again["plan_id"])
        self.assertEqual(len(self.rt.store.list_plans(self.mid)), 1)


if __name__ == "__main__":
    unittest.main()
