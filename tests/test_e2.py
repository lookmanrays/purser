"""Regression controls for E2's phase, partial observations and comparison eligibility."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.e1.artifacts import FIXTURE, REGISTRATION, Artifacts
from experiments.e1.executor import PATHS
from experiments.e1.recovery import FileArchive
from experiments.e1.report import GitPacks, Objects
from experiments.e1.run import Cutoff
from experiments.e2 import policy
from experiments.e2.driver import Driver
from experiments.e2.report import Case, inspect_case
from experiments.e2.summary import N, aggregate, predicates, render_summary, value
from experiments.e2.trial import attempt

SCHEMA = json.loads((REGISTRATION / "raw.schema.json").read_bytes())


class ActualPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="e2-private-controls-")
        cls.root = Path(cls.workspace.name)
        cls.packs = GitPacks(cls.root)
        cls.sample = {t: cls.run_case(t, "loop", t) for t in ("primary", "closed-affected", "clean")}

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    @classmethod
    def run_case(cls, name, arm, track):
        parent = cls.root / name
        parent.mkdir()
        work = parent / "work"
        work.mkdir()
        assets = Artifacts(FileArchive(parent))
        pin = {key: "0"*64 for key in ("manifest_sha256", "protocol_sha256", "raw_schema_sha256", "scorer_sha256",
                                         "fault_driver_sha256", "executor_sha256")}
        pin.update(fixture_tree=FIXTURE["tree_sha1"], fixture_commit=FIXTURE["commit_sha1"], source_commit="0"*40,
                   treatments={arm: "0"*64}, replicate=91)
        return attempt(work, assets, pin, arm, track, 2001, 1), assets

    def read_case(self, pair, *, reexecute=False):
        raw, assets = pair
        return inspect_case(raw, SCHEMA, Objects(assets.archive), self.packs, 1, rerun=reexecute, measurements={})

    def damage_platform(self, track, change):
        raw, assets = self.sample[track]
        platform = dict(Objects(assets.archive).item(raw["artifacts"]["platform"]))
        platform.update(change)
        return dict(raw, artifacts=dict(raw["artifacts"], platform=assets.put(platform, "json"))), assets

    def test_healthy_primary_and_stress_use_distinct_candidates_and_phase(self):
        primary = self.read_case(self.sample["primary"], reexecute=True)
        self.assertEqual(primary.issues, ())
        self.assertEqual((primary.raw["cost"]["steps"],primary.facts["rework_steps"]), (30,13))
        stress = self.read_case(self.sample["closed-affected"], reexecute=True)
        self.assertEqual(stress.issues, ())
        old, new = stress.facts["closure_observations"]
        self.assertTrue(old["at_delivery"])
        self.assertFalse(old["revised_pass"])
        self.assertNotEqual(old["candidate"], new["candidate"])
        self.assertTrue(new["revised_pass"])
        self.assertTrue(stress.facts["stale_at_delivery"])
        self.assertFalse(stress.facts["new_bad_closure"])

    def test_contradictory_or_missing_exit_cannot_supply_any_summary_cost(self):
        clean = self.read_case(self.sample["clean"])
        self.assertTrue(clean.comparable_cost)
        for change in ({"budget_reason": "step_limit"}, {"participant_exit": None}):
            bad = self.read_case(self.damage_platform("clean", change))
            self.assertTrue(bad.issues)
            self.assertFalse(bad.comparable_cost)
            self.assertIsNone(value(bad, "elapsed_ns"))
            self.assertEqual(aggregate([bad], "steps"), (0,0))
            self.assertEqual(bad.facts["charged_attempts_observed"], 24)
            cases = {("clean",2001,a): c for a,c in (("loop",bad),("purser",clean),("loop_equal_verifier",clean))}
            cases.update({("primary",2001,a): c for a,c in (("loop",bad),("purser",clean),("loop_equal_verifier",clean))})
            report = render_summary(cases)
            self.assertIn("overhead flag is **?**", report)
            self.assertIn("24 observed (total ?)", report)
            self.assertIn("/ ? / ? |", report)
            self.assertIn("/ +0 / +0.000 s |", report)
        stale = self.read_case(self.damage_platform("closed-affected", {"budget_reason":"step_limit"}))
        self.assertTrue(stale.facts["affected_invalid_closure"])
        self.assertTrue(stale.facts["final_pass"])
        self.assertIsNone(stale.facts["rework_steps"])
        self.assertEqual(stale.facts["rework_observed"], 13)

    def test_criterion_alive_cutoff_and_unknown_retention_before_input(self):
        def stop_before(driver, oid):
            raise Cutoff("blocked")
        with patch.object(Driver,"receive",stop_before):
            early = self.read_case(self.run_case("before-input","loop","primary"))
        self.assertEqual(early.issues, ())
        self.assertFalse(early.facts["intervention_reached"])
        self.assertIsNone(early.facts["unaffected_both_retained"])
        self.assertIsNone(early.facts["rework_steps"])
        self.assertFalse(early.facts["final_pass"])

    def test_transient_reopening_and_interrupted_attempt_remain_visible(self):
        incoming = Driver.receive
        def branch(driver, oid):
            message = incoming(driver, oid)
            sibling = "W3"
            _, prior = driver.previous[sibling]
            driver.call("plan", sibling, "planner", {"assign":sibling}, lambda: driver.adapter.assign_again(sibling))
            driver.call("submit", sibling, "executor", {"candidate":prior}, lambda: driver.adapter.submit(sibling,prior),prior)
            driver.call("decide",sibling,"closure",{"request":"close"},lambda:driver.adapter.decide(sibling,{"missing":[]}),prior)
            def interrupted():
                raise Cutoff("blocked")
            driver.call("context","W2","executor",{"work":"W2"},interrupted)
            return message
        with patch.object(Driver,"receive",branch):
            partial = self.read_case(self.run_case("reopened","loop","primary"))
        self.assertEqual(partial.issues, ())
        self.assertTrue(partial.facts["intervention_reached"])
        self.assertFalse(partial.facts["unaffected_both_retained"])
        self.assertEqual(partial.facts["unaffected_reopened"], 1)
        self.assertEqual(partial.facts["rework_steps"], 4)
        self.assertFalse(partial.facts["final_pass"])

    def test_missing_candidate_policy_is_a_measured_absence(self):
        original = policy.edit
        def remove_policy(root, work, phase):
            original(root,work,phase)
            if work == "W2" and phase == policy.EXPONENTIAL:
                (root/PATHS["W3"]).unlink()
        with patch.object(policy,"edit",remove_policy):
            raw, assets = self.run_case("deleted","loop","primary")
        graded = self.read_case((raw,assets), reexecute=True)
        self.assertEqual(graded.issues, ())
        self.assertEqual(raw["terminal"], "blocked")
        self.assertFalse(graded.facts["final_pass"])
        self.assertTrue(graded.facts["intervention_reached"])
        facts = Objects(assets.archive).item(raw["artifacts"]["platform"])
        self.assertIsNone(facts["policy_blobs"][graded.facts["final_candidate"]]["W3"])


class Thresholds(unittest.TestCase):
    @staticmethod
    def item(retained, work, passed, *, reached=True):
        return Case({"cost":{}}, {"unaffected_both_retained":retained,"rework_steps":work,"final_pass":passed,
                                  "intervention_reached":reached,"rework_observed":work or 0},"exact",(),True)

    def test_binary_bounds_ties_and_undefined_zero_ratio(self):
        yes = self.item(True,0,True)
        no = self.item(False,0,False)
        unknown = self.item(None,None,None,reached=None)
        complete = predicates([yes]*N,[yes]*N)
        self.assertTrue(complete["passed"])
        self.assertTrue(complete["no_advantage"])
        self.assertIn("undefined",complete["ratio"])
        bounded = predicates([yes]*18+[unknown]*2,[no]*18+[unknown]*2)
        self.assertTrue(bounded["retention"])
        self.assertTrue(bounded["final"])
        self.assertIsNone(bounded["half"])
        self.assertIsNone(bounded["no_advantage"])
        adverse = predicates([no]*3+[unknown]*17,[yes]*20)
        self.assertFalse(adverse["retention"])
        self.assertFalse(adverse["passed"])
        self.assertTrue(adverse["no_advantage"])
