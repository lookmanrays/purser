"""Read a private participant's measurements through the normal E2 consumer."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from experiments.e1.artifacts import Artifacts, FIXTURE, REGISTRATION
from experiments.e1.recovery import FileArchive
from experiments.e1.report import GitPacks, Objects
from experiments.e2 import outcomes, report, summary, trial
SCHEMA = json.loads((REGISTRATION / "raw.schema.json").read_bytes())


class IndependentMeasurements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="e2-comparison-fixture-")
        cls.root = Path(cls.workspace.name)
        cls.packs = GitPacks(cls.root)
        working = cls.root / "participant"
        working.mkdir()
        stage = cls.root / "stage"
        stage.mkdir()
        cls.assets = Artifacts(FileArchive(stage))
        cls.pin = {key: "0" * 64 for key in ("manifest_sha256", "protocol_sha256", "raw_schema_sha256",
                                               "scorer_sha256", "fault_driver_sha256", "executor_sha256")}
        cls.pin.update(fixture_tree=FIXTURE["tree_sha1"], fixture_commit=FIXTURE["commit_sha1"],
                       source_commit="0" * 40, treatments={"loop": "0" * 64}, replicate=91)
        cls.raw = trial.attempt(working, cls.assets, cls.pin, "loop", "primary", 2001, 1)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def read(self, raw):
        return report.inspect_case(raw, SCHEMA, Objects(self.assets.archive), self.packs,
                                   1, rerun=False, measurements={})

    def test_damage_to_one_raw_cost_preserves_independent_cost_and_git_measurements(self):
        for absent, available, expected in (("steps", "elapsed_ns", self.raw["cost"]["elapsed_ns"]),
                                            ("elapsed_ns", "steps", self.raw["cost"]["steps"])):
            with self.subTest(absent=absent):
                raw = dict(self.raw, cost=dict(self.raw["cost"]))
                del raw["cost"][absent]
                case = self.read(raw)
                self.assertTrue(case.issues)
                self.assertIsNone(summary.value(case, absent))
                self.assertEqual(summary.value(case, available), expected)
                self.assertTrue(case.facts["final_pass"])
                self.assertTrue(case.facts["intervention_reached"])

    def test_unrelated_malformed_oracle_preserves_individually_observed_results(self):
        objects = Objects(self.assets.archive)
        original = objects.item(self.raw["artifacts"]["journal"])
        journal = copy.deepcopy(original)
        event = next(e for e in journal if e["kind"] == "oracle" and e["payload"]["candidate"] !=
                     objects.item(self.raw["artifacts"]["platform"])["exit_candidate"]["head"]
                     and e["payload"]["criterion"] == "legacy/compat")
        event["payload"]["version"] = ["unavailable"]
        raw = dict(self.raw, artifacts=dict(self.raw["artifacts"], journal=self.assets.put(journal, "json")))
        case = self.read(raw)
        self.assertIn("malformed protected observation identity", case.issues)
        self.assertTrue(case.facts["final_pass"])
        self.assertTrue(case.facts["unaffected_both_retained"])
        self.assertEqual(case.facts["rework_steps"], 13)

    def test_wrong_registration_trigger_keeps_observed_history_and_git_grade(self):
        journal = copy.deepcopy(Objects(self.assets.archive).item(self.raw["artifacts"]["journal"]))
        next(e["payload"] for e in journal if e["kind"] == "fault")["trigger"] = "closed-affected"
        raw = dict(self.raw, artifacts=dict(self.raw["artifacts"], journal=self.assets.put(journal, "json")))
        case = self.read(raw)
        self.assertTrue(case.issues)
        self.assertIsNot(case.facts["intervention_reached"], True)
        self.assertTrue(case.facts["observed_retention"])
        self.assertTrue(case.facts["final_pass"])
        self.assertIsNone(summary.value(case, "final_pass"))
        self.assertIsNone(summary.value(case, "rework_steps"))

    def test_missing_raw_score_keeps_openly_verifiable_facts(self):
        raw = dict(self.raw)
        del raw["score"]
        case = self.read(raw)
        self.assertIn("raw record violates frozen schema", case.issues)
        self.assertTrue(case.facts["intervention_reached"])
        self.assertTrue(case.facts["final_pass"])
        self.assertEqual(case.facts["rework_steps"], 13)

    def test_setup_failure_has_a_witnessed_zero_and_no_exiting_code_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = Artifacts(FileArchive(Path(tmp)))
            raw = trial.failure(resources, self.pin, "loop", "primary", 2001, 1, reason="fixture unavailable before release")
            case = report.inspect_case(raw, SCHEMA, Objects(resources.archive), self.packs,
                                       1, rerun=False, measurements={})
        self.assertEqual(case.issues, ())
        self.assertEqual(case.basis, "before_release")
        self.assertEqual(case.facts["charged_attempts_observed"], 0)
        self.assertEqual(case.facts["active_lower_ns"], 0)
        self.assertFalse(case.facts["intervention_reached"])
        self.assertIsNone(case.facts["final_pass"])
        self.assertIsNone(case.facts["rework_steps"])


class Bounds(unittest.TestCase):
    @staticmethod
    def item(steps, *, cap=None, observed=0):
        return report.Case({}, dict(rework_steps=steps, rework_observed=observed, final_pass=True,
                                    unaffected_both_retained=True, intervention_reached=True,
                                    admission_ceiling_verified=cap), "exact", ())

    def test_finite_rework_upper_bound_requires_verified_admission(self):
        n = summary.N
        exact, other = self.item(0), self.item(12)
        certain = summary.predicates([exact] * (n-1) + [self.item(None, cap=True)], [other] * n)
        self.assertTrue(certain["half"])
        unbounded = summary.predicates([exact] * (n-1) + [self.item(None, cap=False)], [other] * n)
        self.assertIsNone(unbounded["half"])
        self.assertEqual(certain["ratio"], "inconclusive")
        with self.assertRaisesRegex(ValueError, "exactly 20"):
            summary.predicates([exact], [other])

    def test_one_failed_criterion_survives_missing_unrelated_measurements(self):
        verdicts = {("candidate", "W2/retry", 2): "fail"}
        grades = outcomes.criteria("candidate", verdicts, 2)
        self.assertEqual(len(grades), 6)
        self.assertFalse(outcomes.all_pass(grades))
        self.assertIsNone(outcomes.all_pass(outcomes.criteria("other", verdicts, 2)))
