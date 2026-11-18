"""Falsifiers for protected observations across the case stage and independent cold reader."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.e1.artifacts import Artifacts, Candidates, START
from experiments.e1.measurement import MeasurementUnavailable
from experiments.e1.recovery import FileArchive
from experiments.e1.report import Objects
from experiments.e2 import measure
from experiments.e2.protected_slots import read_slots


class ProtectedSlots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.assets = Artifacts(FileArchive(root))
        cls.repo = Candidates(root, cls.assets).archive
        cls.events = []
        def collect(event, data):
            cls.events.append({"kind": event, "payload": data})
        problems = []
        measure.collect(cls.repo, [START], 2001, 1, cls.assets, [], [], collect, lambda stage, error: problems.append(stage))
        assert not problems and len(cls.events) == 8

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def read(self, journal, *, platform=None, git=True, candidates=(START,), rerun=False, cache=None, seed=2001, rank=1):
        issues = []
        verdicts, witnesses, details = read_slots(
            journal, platform or {}, Objects(self.assets.archive), candidates, {START: self.repo} if git else {},
            seed, rank, rerun=rerun, measurements=cache, issue=issues.append)
        slots = {(row["criterion"], row["version"]): row for row in details.get(START, [])}
        return issues, verdicts, witnesses, slots

    def test_missing_all_rows_executes_every_registered_slot(self):
        cache = {}
        issues, verdicts, witnesses, slots = self.read([], rerun=True, cache=cache)
        self.assertEqual(set(cache), {(START, 2001, 1, 1), (START, 2001, 1, 2)})
        self.assertEqual(len(issues), 8)
        self.assertEqual(len(verdicts), len(witnesses))
        self.assertEqual({k for k, r in slots.items() if r["verdict"] == "pass"}, {("scope/paths", 1), ("legacy/compat", 1)})
        self.assertEqual(len(verdicts), 8)
        self.assertTrue(all((r["original"], r["replay"], r["source"]) == ("missing", "measured", "replay")
                            for r in slots.values()))
        with patch.object(measure, "check", side_effect=AssertionError("unexpected cache reuse across unit keys")) as check:
            self.assertEqual(self.read([], rerun=True, cache=cache)[1], verdicts)
            check.assert_not_called()
            self.read([], rerun=True, cache=cache, seed=2002, rank=2)
            self.assertGreater(check.call_count, 0)

    def test_one_failed_replay_preserves_historical_and_all_seven_peers(self):
        check = measure.check
        def unavailable(repo, oid, root, seed, rank, version, labels):
            if "legacy/compat" in labels:
                raise MeasurementUnavailable("legacy child timed out")
            return check(repo, oid, root, seed, rank, version, labels)
        with patch.object(measure, "check", unavailable):
            issues, verdicts, witnesses, slots = self.read(self.events, rerun=True)
        self.assertEqual(len(issues), 1)
        self.assertEqual(len(verdicts), len(witnesses))
        self.assertEqual(len(verdicts), 7)
        legacy = slots["legacy/compat", 1]
        self.assertEqual((legacy["original"], legacy["historical_verdict"], legacy["replay"]), ("recorded", "pass", "unavailable"))
        self.assertIsNone(legacy["verdict"])
        self.assertTrue(all(r["replay"] == "matched" for k, r in slots.items() if k != ("legacy/compat", 1)))

    def test_conflicting_duplicate_requires_independent_original_git_observation(self):
        row = next(e for e in self.events if e["payload"]["criterion"] == "W1/preferences")
        forged = copy.deepcopy(Objects(self.assets.archive).item(row["payload"]["evidence"]))
        self.assertEqual(forged["verdict"], "fail")
        for case in forged["cases"]:
            case["observed"] = case["expected"]
        forged["verdict"] = "pass"
        ref = self.assets.put(forged, "json")
        duplicate = {"kind": "oracle", "payload": dict(row["payload"], evidence=ref, observed="pass", verdict="pass")}
        issues, verdicts, _, slots = self.read([*self.events, duplicate])
        slot = slots["W1/preferences", 1]
        self.assertTrue(issues)
        self.assertEqual((slot["original"], slot["replay"], slot["source"]), ("disputed", "different", "replay"))
        self.assertEqual({r["verdict"] for r in slot["recordings"]}, {"fail", "pass"})
        self.assertIsNone(slot["historical_verdict"])
        self.assertEqual(verdicts[START, "W1/preferences", 1], "fail")
        self.assertEqual(len(verdicts), 8)

    def test_checkpoint_and_stage_preserve_history_without_fabricating_a_replay(self):
        retry = next(e["payload"] for e in self.events if e["payload"]["criterion"] == "W2/retry" and
                     e["payload"]["version"] == 1)
        legacy = next(e["payload"] for e in self.events if e["payload"]["criterion"] == "legacy/compat")
        platform = {"checkpoint_measurement": {"checks": [{"candidate": START, "criterion": "W2/retry",
                                                          "evidence": retry["evidence"]}]},
                    "referenced_files": [legacy["evidence"]]}
        issues, verdicts, witnesses, slots = self.read([], platform=platform, git=False)
        self.assertTrue(issues)
        self.assertEqual((verdicts, witnesses), ({}, {}))
        self.assertEqual((slots["W2/retry", 1]["source"], slots["W2/retry", 1]["historical_verdict"]),
                         ("checkpoint", "fail"))
        self.assertEqual((slots["legacy/compat", 1]["source"], slots["legacy/compat", 1]["historical_verdict"]),
                         ("case_stage", "pass"))
        self.assertTrue(all(r["verdict"] is None and r["replay"] == "unavailable" for r in slots.values()))
        _, verdicts, witnesses, slots = self.read([], platform=platform)
        self.assertEqual(len(witnesses), len(verdicts))
        self.assertEqual((slots["W2/retry", 1]["original"], slots["W2/retry", 1]["replay"]), ("missing", "matched"))
        self.assertEqual(self.read([], candidates=())[3], {})

    def test_collector_recovers_durable_report_before_its_journal_row(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Artifacts(FileArchive(Path(directory)))
            original = next(e["payload"] for e in self.events if e["payload"]["criterion"] == "legacy/compat")
            report = Objects(self.assets.archive).item(original["evidence"])
            ref = assets.put(report, "json")
            # This is the object the scorer had durably published when the journal append was interrupted.
            wrong = dict(original, evidence=ref, observed="fail", verdict="fail")
            recorded, problems = [], []
            sources = measure.stage_reports(assets, 2001, 1)
            check = measure.check
            def stopped(repo, oid, path, seed, rank, version, labels):
                self.assertNotIn("legacy/compat", labels)
                return check(repo, oid, path, seed, rank, version, labels)
            with patch.object(measure, "check", stopped):
                measure.collect(self.repo, [START], 2001, 1, assets, [], [{"kind": "oracle", "payload": wrong}],
                                lambda _, p: recorded.append(p), lambda stage, error: problems.append(stage),
                                stage_reports=sources)
            self.assertIn("retained protected observation", problems)
            self.assertEqual(len(recorded), 8)
            legacy = next(row for row in recorded if row["criterion"] == "legacy/compat")
            self.assertEqual((legacy["verdict"], legacy["source"], legacy["evidence"]), ("pass", "case_stage", ref))
            self.assertEqual(sum("source" in row for row in recorded), 1)

    def test_detached_stage_report_survives_loss_of_all_git_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Artifacts(FileArchive(Path(directory)))
            prior = self.events[0]["payload"]
            report = Objects(self.assets.archive).item(prior["evidence"])
            ref = assets.put(report, "json")
            rows, refs = [], []
            sources = measure.stage_reports(assets, 2001, 1)
            with patch.object(measure, "check", side_effect=AssertionError("missing Git must not run")) as check:
                measure.collect(None, [START], 2001, 1, assets, refs, [], lambda _, p: rows.append(p),
                                self.fail, stage_reports=sources)
                check.assert_not_called()
            self.assertEqual(refs, [ref])
            self.assertEqual([(r["source"], r["verdict"]) for r in rows], [("case_stage", report["verdict"])])

    def test_scope_can_be_measured_when_a_git_export_is_unavailable(self):
        with patch.object(measure, "extract_tree", side_effect=OSError("unavailable export")):
            issues, verdicts, _, slots = self.read([], rerun=True)
        self.assertTrue(issues)
        self.assertEqual(verdicts, {(START, "scope/paths", 1): "pass"})
        self.assertEqual(sum(r["replay"] == "unavailable" for r in slots.values()), 7)
