"""Case-local accounting and participant evidence at the scripted E1 archive boundary."""

import io
import json
import shutil
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path

from experiments.e1 import report, run, scorer
from experiments.e1.accounting import PublicHistory, check_exit, journal_prefix
from experiments.e1.artifacts import ROOT, Artifacts, encoded, sha


CONTROLS = ROOT / "docs/evidence/M2/evaluator-review-3785d19"


class ArchiveAccounting(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def write(self, source, change=None):
        dest = self.directory / "raw.zip"
        if change is None:
            shutil.copyfile(CONTROLS / source, dest)
            return
        with zipfile.ZipFile(CONTROLS / source) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        def put(obj):
            raw = encoded(obj)
            ref = {"bytes": len(raw), "sha256": sha(raw), "path": f"objects/{sha(raw)}.json"}
            members[ref["path"]] = raw
            return ref
        change(members, put)
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)

    def test_preserved_cutoff_has_no_pair_and_healthy_neighbors_stay_measured(self):
        self.write("contradictory-cutoff.zip")
        rows = report.inspect_series(self.directory, strict=False)
        clean = rows["clean", 1001, "loop"]
        self.assertFalse(clean.comparable())
        self.assertFalse(clean.measured_endpoint)
        self.assertTrue(clean.evidence.facts["correct_parent_closure"])
        self.assertIsNone(clean.evidence.facts["false_parent_closure"])
        self.assertTrue(rows["clean", 1001, "purser"].comparable())
        self.assertTrue(rows["clean", 1001, "purser"].measured_endpoint)
        self.assertTrue(rows["primary", 1003, "loop"].comparable())
        self.assertIn("inconclusive", report.render(self.directory))
        with self.assertRaisesRegex(ValueError, "terminal"):
            report.verify_candidates(self.directory)

    def test_recovered_failed_done_survives_independent_reexecution(self):
        self.write("done-false-close.zip")
        rows = report.inspect_series(self.directory)
        case = rows["primary", 1002, "loop"]
        self.assertEqual(case.basis, "observed_lower_bound")
        self.assertEqual((case.evidence.facts["false_parent_closure"], case.evidence.facts["caught"]), (True, False))
        self.assertTrue(rows["primary", 1003, "loop"].comparable())
        self.assertEqual(report.verify_candidates(self.directory)["runs"], 2)

    def test_complete_primary_may_end_on_a_measured_failed_candidate(self):
        self.write("done-false-close.zip")
        with zipfile.ZipFile(self.directory / "raw.zip") as archive:
            raw = json.loads(archive.read("runs/primary/1002/loop.json"))
            objects = report.Objects(archive)
            journal = objects.item(raw["artifacts"]["journal"])
            candidates = objects.item(raw["artifacts"]["candidates"])
            evidence = scorer.assess(journal, candidates=candidates, history_complete=True)
        self.assertTrue(evidence.score(True)["false_parent_closure"])
        # Eligibility of an observed primary completion can include either measured result.
        complete = dict(raw, validity="complete", terminal="done")
        self.assertTrue(report.Case(complete, evidence, "exact", final_parent=evidence.final_parent).comparable())
        self.assertFalse(report.Case(dict(complete, track="clean"), evidence, "exact", final_parent=evidence.final_parent).comparable())

    def test_clean_control_requires_the_last_measured_candidate_still_closed(self):
        self.write("contradictory-cutoff.zip")
        case = report.inspect_series(self.directory, strict=False)["clean", 1001, "purser"]
        earlier = case.final_parent[1]
        self.assertTrue(case.comparable())
        for final in (("open", None), ("done", "f" * 40)):
            example = report.Case(case.raw, case.evidence, "exact", final_parent=final)
            self.assertFalse(example.comparable())
        alias = report.Case(case.raw, case.evidence, "exact", final_parent=("done", earlier))
        self.assertTrue(alias.comparable())

    def test_lost_or_corrupt_criterion_only_invalidates_its_consumers(self):
        for shared, missing in ((False, True), (True, False)):
            with self.subTest(shared=shared):
                def damage(members, put):
                    key = "runs/clean/1001/loop.json" if shared else "runs/primary/1002/loop.json"
                    raw = json.loads(members[key])
                    journal = json.loads(members[raw["artifacts"]["journal"]["path"]])
                    parent = next(e["payload"]["candidate"] for e in reversed(journal) if e["kind"] == "public_state"
                                  and e["payload"]["subject"] == "W-notify")
                    label = "queue/composition" if shared else "legacy/compat"
                    oracle = next(e["payload"] for e in journal if e["kind"] == "oracle" and
                                  (e["payload"]["candidate"], e["payload"]["criterion"]) == (parent, label))
                    path = oracle["evidence"]["path"]
                    if missing:
                        del members[path]
                    else:
                        members[path] = b"invalid recorded bytes"
                self.write("contradictory-cutoff.zip" if shared else "done-false-close.zip", damage)
                rows = report.inspect_series(self.directory, strict=False)
                self.assertTrue(rows["primary", 1003, "loop"].comparable())
                if shared:
                    self.assertTrue(rows["clean", 1001, "purser"].measured_endpoint)
                    for arm in ("loop", "purser"):
                        row = rows["clean", 1001, arm]
                        self.assertIsNone(row.evidence.facts["correct_parent_closure"])
                        self.assertFalse(row.comparable())
                else:
                    row = rows["primary", 1002, "loop"]
                    self.assertTrue(row.evidence.facts["false_parent_closure"])
                    self.assertTrue(row.issues)
                with self.assertRaises(ValueError):
                    report.verify_candidates(self.directory)

    def test_missing_duplicate_or_displaced_terminal_is_local_to_its_run(self):
        for variant in ("absent", "duplicate", "displaced"):
            with self.subTest(variant=variant):
                def damage(members, put):
                    key = "runs/primary/1003/loop.json"
                    raw = json.loads(members[key])
                    journal = json.loads(members[raw["artifacts"]["journal"]["path"]])
                    terminal = journal.pop()
                    if variant == "duplicate":
                        journal.extend((deepcopy(terminal), terminal))
                    if variant == "displaced":
                        journal.insert(-1, terminal)
                    for i, row in enumerate(journal, 1):
                        row["seq"] = i
                    raw["artifacts"]["journal"] = put(journal)
                    members[key] = encoded(raw)
                self.write("done-false-close.zip", damage)
                rows = report.inspect_series(self.directory, strict=False)
                self.assertTrue(rows["primary", 1002, "loop"].evidence.facts["false_parent_closure"])
                row = rows["primary", 1003, "loop"]
                self.assertTrue(row.evidence.facts["correct_parent_closure"])
                self.assertIsNone(row.evidence.facts["false_parent_closure"])
                self.assertFalse(row.comparable())
                with self.assertRaisesRegex(ValueError, "final terminal"):
                    report.verify_candidates(self.directory)


class ExitAccounting(unittest.TestCase):
    def boundary(self, reason, *, steps, span, basis="exact"):
        invalid = reason == "harness_failure"
        validity = "infrastructure_invalid" if invalid else "complete" if reason == "done" else "arm_incomplete"
        raw = {"terminal": reason, "validity": validity, "cost": {"steps": steps, "elapsed_ns": span}}
        pre = basis == "before_release"
        t = None if pre else "2026-11-18T00:00:00+00:00"
        end = None if pre else span + 23
        journal = [{"kind": "action", "offset_ns": i, "payload": {}} for i in range(steps)]
        payload = {"end_reason": reason, "missing": "controlled loss" if invalid else None, "human_action": None,
                   "last_observation": steps or None, "end_monotonic_ns": end, "end_utc": t, "cost_basis": basis}
        journal.append({"kind": "terminal", "offset_ns": span, "payload": payload})
        platform = {"budget_reason": reason, "start_monotonic_ns": None if pre else 23, "end_monotonic_ns": end,
                    "start_utc": t, "end_utc": t, "cost_basis": basis, "clock_stopped": basis == "exact",
                    "failure": payload["missing"], "public_history_complete": not pre,
                    "participant_exit": None if invalid else {"reason": reason, "end_monotonic_ns": end, "end_utc": t}}
        return raw, journal, platform

    def test_normal_cutoffs_and_three_failure_clock_bases(self):
        cases = [("done", 24, 99, "exact"), ("blocked", 10, 99, "exact"), ("idle", 1, 99, "exact"),
                 ("step_limit", 96, 999, "exact"), ("time_limit", 10, 60_000_000_000, "exact"),
                 ("harness_failure", 0, 0, "before_release"), ("harness_failure", 6, 99, "observed_lower_bound"),
                 ("harness_failure", 4, 99, "exact")]
        for reason, steps, span, basis in cases:
            with self.subTest(reason=reason, basis=basis):
                inputs = self.boundary(reason, steps=steps, span=span, basis=basis)
                issues = []
                self.assertEqual(check_exit(*inputs, True, issues.append), (basis, True))
                self.assertEqual(issues, [])

    def test_header_cannot_overrule_clock_marker_limits_or_manual_action(self):
        for value in ("step", "early deadline", "validity", "stop", "manual", "missing", "participant", "timed event"):
            with self.subTest(value=value):
                reason = {"step": "step_limit", "early deadline": "time_limit"}.get(value, "done")
                raw, journal, platform = self.boundary(reason, steps=4, span=22)
                if value == "validity":
                    raw["validity"] = "arm_incomplete"
                if value == "stop":
                    platform["clock_stopped"] = False
                if value == "manual":
                    journal[-1]["payload"]["human_action"] = "intervention"
                if value == "missing":
                    journal[-1]["payload"]["missing"] = "unavailable"
                if value == "participant":
                    platform["participant_exit"]["reason"] = "idle"
                if value == "timed event":
                    journal[-2]["offset_ns"] = 23
                issues = []
                self.assertFalse(check_exit(raw, journal, platform, True, issues.append)[1])
                self.assertTrue(issues)


class ParticipantAccounting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        directory = Path(temp.name)
        archive = zipfile.ZipFile(io.BytesIO(), "w")
        cls.addClassCleanup(archive.close)
        # The registered-input ZIPs exercise report/verify above. This off-study trial exercises the live writer.
        pin = json.loads((ROOT / "experiments/results/e1-scripted-v1/freeze.json").read_bytes())
        raw = run.attempt(directory, Artifacts(archive), pin, "loop", "clean", 9002, 1)
        cls.objects = report.Objects(archive)
        cls.journal = cls.objects.item(raw["artifacts"]["journal"])
        cls.platform = cls.objects.item(raw["artifacts"]["platform"])
        if raw["terminal"] != "done":
            raise AssertionError(cls.platform.get("failure", raw["terminal"]))

    def test_missing_charged_action_has_a_position_in_the_folded_history(self):
        journal = deepcopy(self.journal)
        del journal[1]
        issues = []
        rows, complete = journal_prefix(journal, "loop", issues.append)
        self.assertFalse(complete)
        self.assertEqual(rows[0]["kind"], "action")
        self.assertEqual(rows[1]["kind"], "public_gap")
        self.assertTrue(issues)

    def test_action_endpoints_witness_an_omitted_transition_and_authenticate_the_response(self):
        journal = deepcopy(self.journal)
        final = next(e for e in journal if e["kind"] == "public_state"
                     and (e["payload"]["subject"], e["payload"]["to"]) == ("W-notify", "closed"))
        journal.remove(final)
        action = next(e["payload"] for e in journal if e["kind"] == "action" and e["payload"]["actor"] == "plan_reviewer")
        real = deepcopy(action["return"])
        action["return"]["missing"] = ["invented"]
        issues = []
        history = PublicHistory("loop", True, self.objects.item, issues.append)
        observed = history.reconcile(journal, self.platform, incomplete=False)
        recovered = [e["payload"] for e in observed if e["kind"] == "public_state" and e["payload"]["subject"] == "W-notify"]
        self.assertEqual(recovered[-1]["candidate"], final["payload"]["candidate"])
        self.assertEqual(recovered[-1]["to"], "closed")
        authenticated = next(e["payload"] for e in observed if e["kind"] == "action" and e["payload"]["attempt"] == action["attempt"])
        self.assertEqual(authenticated["return"], real)
        self.assertFalse(history.complete)
        self.assertTrue(any("missing public transition" in s for s in issues))
        self.assertTrue(any("response differs" in s for s in issues))

    def test_real_clean_plan_history_agrees_at_every_action_and_final_parent(self):
        issues = []
        history = PublicHistory("loop", True, self.objects.item, issues.append)
        history.reconcile(self.journal, self.platform, incomplete=False)
        self.assertEqual(issues, [])
        self.assertTrue(history.complete)
        self.assertEqual(history.final["W-notify"][0], "closed")
