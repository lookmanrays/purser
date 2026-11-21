"""Read a private participant's measurements through the normal E2 consumer."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.e1.artifacts import Artifacts, FIXTURE, MANIFEST, REGISTRATION
from experiments.e1.executor import PARENT
from experiments.e1.recovery import FileArchive
from experiments.e1.report import GitPacks, Objects
from experiments.e2 import outcomes, report, summary, trial
from experiments.e2.driver import Driver
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

    def test_later_disputed_work_prices_only_a_genuinely_ambiguous_operand(self):
        original = Objects(self.assets.archive).item(self.raw["artifacts"]["journal"])
        for request_subject, exact, lower in (("W3", 13, 13), ("unregistered", None, 12)):
            with self.subTest(request_subject=request_subject):
                journal = copy.deepcopy(original)
                review = next(e["payload"] for e in journal if e["kind"] == "action" and e["payload"]["attempt"] == 29)
                self.assertEqual(review["work_started"]["subject"], PARENT)
                review["work_request"]["subject"] = request_subject
                raw = dict(self.raw, artifacts=dict(self.raw["artifacts"], journal=self.assets.put(journal, "json")))
                case = self.read(raw)
                self.assertTrue(any("attempt 29 work identity" in item and request_subject in item for item in case.issues))
                expected = (True, True, 0, False, exact, True)
                self.assertEqual(tuple(summary.value(case, k) for k in outcomes.FIELDS), expected)
                self.assertEqual((case.facts["rework_observed"], case.facts["rework_upper"]), (lower, 13))
                self.assertEqual(case.facts["rework_undetermined"], [] if exact else [29])
                self.assertIs(case.facts["prior_changed_exposure"], False)
                self.assertIs(case.facts["clock_pause_quiescent"], True)
                self.assertEqual(summary.value(case, "steps"), 30)
                self.assertEqual(summary.value(case, "elapsed_ns"), self.raw["cost"]["elapsed_ns"])

    def test_real_step_cutoff_with_lost_exiting_checkout(self):
        run = Driver.execute
        retain = trial.retain_participant
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case_dir = root / "trial"
            case_dir.mkdir()
            stage = root / "staged"
            stage.mkdir()
            assets = Artifacts(FileArchive(stage))

            def use_remaining_budget(driver):
                self.assertEqual(run(driver), "done")
                while True:
                    driver.tools.context(PARENT)

            def lose_checkout(directory, *args):
                (directory / "worker").rename(root / "lost-checkout")
                return retain(directory, *args)

            with patch.object(Driver, "execute", use_remaining_budget), patch.object(trial, "retain_participant", lose_checkout):
                raw = trial.attempt(case_dir, assets, self.pin, "loop", "primary", 2001, 1)
            objects = Objects(assets.archive)
            platform = objects.item(raw["artifacts"]["platform"])
            self.assertEqual(platform["participant_exit"]["reason"], "step_limit")
            self.assertNotIn("exit_candidate", platform)
            self.assertEqual(raw["terminal"], "harness_failure")
            case = report.inspect_case(raw, SCHEMA, objects, GitPacks(root), 1, rerun=False, measurements={})
            limit = MANIFEST["budgets"]["scripted"]["max_steps"]
            self.assertEqual(summary.value(case, "steps"), limit)
            self.assertEqual(summary.value(case, "rework_steps"), limit - 17)
            self.assertEqual(summary.value(case, "elapsed_ns"), raw["cost"]["elapsed_ns"])
            self.assertIs(case.facts["unaffected_both_retained"], True)
            self.assertIsNone(summary.value(case, "final_pass"))
            self.assertEqual(case.facts["last_observed_subject"], PARENT)

    def test_undated_receiver_before_and_after_the_registered_notice(self):
        original = Objects(self.assets.archive).item(self.raw["artifacts"]["journal"])
        for number, exposed in ((2, None), (26, False)):
            with self.subTest(attempt=number):
                journal = copy.deepcopy(original)
                action = next(e["payload"] for e in journal if e["kind"] == "action" and e["payload"]["attempt"] == number)
                self.assertEqual(action["action"], "context")
                del action["delivery"]
                raw = dict(self.raw, artifacts=dict(self.raw["artifacts"], journal=self.assets.put(journal, "json")))
                case = self.read(raw)
                self.assertIs(case.facts["prior_changed_exposure"], exposed)
                self.assertIs(case.facts["intervention_reached"], None if exposed is None else True)
                self.assertIs(case.facts["checkpoint_qualified"], True)
                self.assertTrue(case.comparable_elapsed)

    def test_later_withheld_ingress_preserves_the_first_delivery(self):
        def repeat_after_run(driver):
            result = run(driver)
            self.assertIsNone(driver.receive(driver.candidates.head))
            return result

        run = Driver.execute
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "stage"
            stage.mkdir()
            assets = Artifacts(FileArchive(stage))
            with patch.object(Driver, "execute", repeat_after_run):
                raw = trial.attempt(root, assets, self.pin, "loop", "primary", 2001, 1)
            case = report.inspect_case(raw, SCHEMA, Objects(assets.archive), GitPacks(root), 1, rerun=False, measurements={})
            self.assertIn("unregistered or duplicate intervention", case.issues)
            self.assertTrue(case.facts["intervention_reached"])
            self.assertIs(case.facts["prior_changed_exposure"], False)
            self.assertIs(case.facts["unaffected_both_retained"], True)
            self.assertEqual(summary.value(case, "rework_steps"), 13)

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
