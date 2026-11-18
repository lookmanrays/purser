"""Per-case measurement and interruption boundaries, outside any collected series."""
import io
import json
import signal
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from experiments.e1 import measurement, run
from experiments.e1.artifacts import Artifacts, MANIFEST


class TrialControllerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        archive = zipfile.ZipFile(io.BytesIO(), "w")
        self.addCleanup(archive.close)
        self.assets = Artifacts(archive)
        zero = "0" * 64
        self.freeze = {k: zero for k in ("manifest_sha256", "protocol_sha256", "raw_schema_sha256",
                                         "scorer_sha256", "fault_driver_sha256", "executor_sha256")}
        self.freeze.update(fixture_tree=MANIFEST["fixture"]["tree_sha1"], fixture_commit=MANIFEST["fixture"]["commit_sha1"],
                           treatments={arm: zero for arm in ("loop", "loop_equal_verifier", "purser")},
                           source_commit="1"*40, replicate=13)

    def attempt(self, *, arm="loop", track="primary", progress=None):
        # A nonstudy seed with the first fault family exercises a real queue and a real Git archive.
        return run.attempt(self.directory, self.assets, self.freeze, arm, track, 9002, 1, progress=progress)

    def load(self, raw, artifact):
        return json.loads(self.assets.archive.read(raw["artifacts"][artifact]["path"]))

    def test_setup_failure_is_a_retained_unreleased_case(self):
        checkpoints = []
        with patch.object(run, "Candidates", side_effect=OSError("fixture storage unavailable")):
            raw = self.attempt(progress=checkpoints.append)
        self.assertEqual((raw["terminal"], raw["validity"], raw["replicate"], raw["score"]),
                         ("harness_failure", "infrastructure_invalid", 13, None))
        self.assertEqual((raw["cost"]["steps"], raw["cost"]["elapsed_ns"]), (0, 0))
        self.assertEqual(self.load(raw, "candidates"), {})
        platform = self.load(raw, "platform")
        self.assertEqual(platform["cost_basis"], "before_release")
        self.assertIsNone(platform["start_monotonic_ns"])
        self.assertIsNone(platform["end_monotonic_ns"])
        self.assertIn("fixture storage unavailable", platform["failure"])
        self.assertEqual(checkpoints[0]["cost_basis"], "before_release")
        self.assertEqual([e["kind"] for e in self.load(raw, "journal")], ["terminal"])

    def test_real_signal_after_persisted_parent_close_retains_its_outcome(self):
        original = run.Plan.decide
        def decide(plan, work, audit):
            reply = original(plan, work, audit)
            if work == "W-notify" and plan.read()[work]["state"] == "closed":
                signal.raise_signal(signal.SIGALRM)
            return reply
        with patch.object(run.Plan, "decide", decide):
            raw = self.attempt()
        journal = self.load(raw, "journal")
        closes = [e["payload"] for e in journal if e["kind"] == "public_state"
                  and e["payload"]["subject"] == "W-notify" and e["payload"]["to"] == "closed"]
        self.assertEqual(len(closes), 1)
        trace = self.load(raw, "platform")["treatment_trace"]
        real = json.loads(self.assets.archive.read(trace["path"]))["plan"]["W-notify"]
        self.assertEqual((real["state"], real["candidate"]), ("closed", closes[0]["candidate"]))
        self.assertEqual((raw["terminal"], raw["validity"]), ("time_limit", "arm_incomplete"))
        self.assertTrue(raw["score"]["correct_parent_closure"])
        self.assertEqual(self.load(raw, "platform")["cost_basis"], "exact")
        self.assertEqual(sum(e["kind"] == "terminal" for e in journal), 1)
        self.assertEqual(journal[-1]["payload"]["end_reason"], "time_limit")
        self.assertGreaterEqual(next(e["offset_ns"] for e in journal if e["kind"] == "oracle"), raw["cost"]["elapsed_ns"])

    def test_preexposure_cutoff_cannot_produce_a_primary_score(self):
        original = run.Journal.event
        def stop_before_intent_is_journaled(journal, kind, payload):
            if kind == "action":
                signal.raise_signal(signal.SIGALRM)
            return original(journal, kind, payload)
        with patch.object(run.Journal, "event", stop_before_intent_is_journaled):
            raw = self.attempt()
        self.assertEqual((raw["terminal"], raw["cost"]["steps"], raw["score"]), ("time_limit", 0, None))
        self.assertFalse(any(e["kind"] == "action" for e in self.load(raw, "journal")))
        self.assertEqual(self.load(raw, "platform")["cost_basis"], "exact")

    def test_measured_private_fault_without_public_submission_has_no_primary_score(self):
        original = run.Plan.submit
        def stop_before_fault_submission(plan, work, oid):
            if work == "W2":
                signal.raise_signal(signal.SIGALRM)
            return original(plan, work, oid)
        with patch.object(run.Plan, "submit", stop_before_fault_submission):
            raw = self.attempt()
        journal = self.load(raw, "journal")
        fault = next(e["payload"] for e in journal if e["kind"] == "fault")
        self.assertTrue(any(e["kind"] == "oracle" and e["payload"]["candidate"] == fault["candidate"]
                            and e["payload"]["criterion"] == fault["target"] and e["payload"]["verdict"] == "fail"
                            for e in journal))
        self.assertEqual((raw["terminal"], raw["score"]), ("time_limit", None))

    def test_late_retention_failure_keeps_known_activity_and_measurements(self):
        with patch.object(run.Plan, "retained", side_effect=OSError("trace retention unavailable")):
            raw = self.attempt(track="clean")
        journal = self.load(raw, "journal")
        platform = self.load(raw, "platform")
        self.assertEqual((raw["validity"], raw["terminal"], raw["score"]), ("infrastructure_invalid", "harness_failure", None))
        self.assertEqual((raw["cost"]["steps"], platform["cost_basis"]), (24, "exact"))
        self.assertTrue(any(e["kind"] == "public_state" and e["payload"]["subject"] == "W-notify"
                            and e["payload"]["to"] == "closed" for e in journal))
        self.assertTrue(any(e["kind"] == "oracle" for e in journal))
        self.assertIn("trace retention unavailable", platform["failure"])
        self.assertEqual(sum(e["kind"] == "terminal" for e in journal), 1)

    def test_progress_precedes_invocation_and_surviving_plan_can_be_reconciled(self):
        seen = {}
        def save(progress):
            if not progress["rows"]:
                return
            last = progress["rows"][-1]
            p = last["payload"]
            if last["kind"] == "action" and p["actor"] == "closure" and p["subject"] == "W-notify":
                seen["returned" if "return" in p else "intent"] = progress
        raw = self.attempt(track="clean", progress=save)
        self.assertEqual(raw["terminal"], "done")
        self.assertEqual(seen.keys(), {"intent", "returned"})
        saved = seen["intent"]
        self.assertEqual(saved["cost_basis"], "observed_lower_bound")
        self.assertNotIn("return", saved["rows"][-1]["payload"])
        self.assertNotIn("end_monotonic_ns", saved)
        self.assertTrue(saved["candidates"])
        restored = run.reconcile_interrupted(self.directory, self.assets, "loop", saved)
        self.assertEqual((restored["last_monotonic_ns"], restored["cost_basis"]),
                         (saved["last_monotonic_ns"], "observed_lower_bound"))
        after = restored["rows"][-1]["payload"]
        self.assertEqual((after["subject"], after["from"], after["to"]), ("W-notify", "submitted", "closed"))
        self.assertIsNone(after["transition_offset_ns"])
        repeated = run.reconcile_interrupted(self.directory, self.assets, "loop", restored)
        self.assertEqual(repeated["rows"], restored["rows"])

    def test_interrupted_wrong_parent_keeps_available_oracles_from_the_staged_git_pack(self):
        oid_source = run.Candidates(self.directory, self.assets)
        for work in run.ORDER:
            run.implement(oid_source.work, work)
        seed, rank = 9003, 1
        fault = run.fault_for(seed, "primary")
        run.inject(oid_source.work, fault, seed)
        oid = oid_source.commit("Implement notification behavior with an extra out-of-scope file")
        plan = run.Plan(self.directory)
        for work in run.ORDER:
            plan.submit(work, oid)
            plan.decide(work, {"missing": []})
        plan.submit(run.PARENT, oid)
        saved = []
        def remember(progress):
            event = progress["rows"][-1]
            if event["kind"] == "action" and "return" not in event["payload"]:
                saved.append(progress)
        journal = run.Journal("loop", progress=remember)
        journal.initial_states, journal.candidates = plan.states(), oid_source.entries
        journal.event("fault", {"candidate": oid, "target": fault[2], "work": fault[1]})
        journal.action("decide", run.PARENT, "closure", oid, {}, lambda: plan.decide(run.PARENT, {"missing": []}))
        original = plan.file.read_bytes()
        oid_source.work.rename(self.directory / "unavailable-worker")
        oid_source.archive.rename(self.directory / "unavailable-private-repository")
        # Only the frozen legacy test directory is unavailable. All retained candidate bytes survive.
        with patch.object(measurement, "REGISTRATION", self.directory / "missing-frozen-tests"):
            partial = run.reconcile_interrupted(self.directory, self.assets, "loop", saved[0], seed=seed, rank=rank)
        measured = {e["payload"]["criterion"]: e["payload"]["verdict"] for e in partial["rows"] if e["kind"] == "oracle"}
        self.assertEqual(measured, {label: "fail" if label == "scope/paths" else "pass"
                                    for label in run.scorer.LABELS if label != "legacy/compat"})
        self.assertEqual([(e["payload"]["subject"], e["payload"]["to"], e["payload"]["candidate"])
                          for e in partial["rows"] if e["kind"] == "public_state"], [(run.PARENT, "closed", oid)])
        self.assertEqual(run.scorer.assess(partial["rows"]).false_candidates, [oid])
        self.assertIsNone(run.scorer.score(partial["rows"]))
        self.assertIn("legacy/compat", partial["platform"]["recovery_issues"][0]["stage"])
        self.assertEqual(partial["last_monotonic_ns"], saved[0]["last_monotonic_ns"])
        self.assertEqual(partial["cost_basis"], "observed_lower_bound")
        complete = run.reconcile_interrupted(self.directory, self.assets, "loop", partial, seed=seed, rank=rank)
        self.assertEqual(complete["rows"][:-1], partial["rows"])
        self.assertEqual(complete["rows"][-1]["payload"]["criterion"], "legacy/compat")
        observed = run.scorer.assess(complete["rows"], candidates=complete["candidates"])
        self.assertEqual(observed.false_candidates, [oid])
        # This fixture journal starts after the leaf submissions. Only the parent was read back.
        self.assertIsNone(observed.score(True))
        self.assertEqual(plan.file.read_bytes(), original)

    def test_interrupted_admitted_verifier_failure_preserves_its_public_diagnosis(self):
        original = run.Plan.verify
        def verify(plan, work, label, report):
            response = original(plan, work, label, report)
            if label == "W2/retry" and report["verdict"] == "fail":
                signal.raise_signal(signal.SIGALRM)
            return response
        with patch.object(run.Plan, "verify", verify):
            raw = self.attempt(arm="loop_equal_verifier")
        events = self.load(raw, "journal")
        intent = next(e["payload"] for e in events if e["kind"] == "action"
                      and e["payload"]["actor"] == "independent_verifier" and e["payload"]["subject"] == "W2")
        self.assertIn("failure", intent["return"])
        public = [e["payload"] for e in events if e["kind"] == "public_state"
                  and (e["payload"]["evidence"] or {}).get("source_action") == intent["attempt"]]
        self.assertEqual([(p["candidate"], p["diagnosis"]) for p in public], [(intent["candidate"], ["W2/retry"])])
        self.assertEqual(raw["terminal"], "time_limit")

    def test_transient_final_archive_failure_emits_one_consistent_record(self):
        original = self.assets.put
        def reject_final_platform(value, suffix):
            if isinstance(value, dict) and value.get("budget_reason") == "done" and value.get("cost_basis") == "exact":
                raise OSError("platform artifact temporarily unavailable")
            return original(value, suffix)
        with patch.object(self.assets, "put", side_effect=reject_final_platform):
            raw = self.attempt(track="clean")
        platform = self.load(raw, "platform")
        journal = self.load(raw, "journal")
        self.assertEqual(raw["terminal"], "harness_failure")
        self.assertEqual((platform["cost_basis"], raw["cost"]["steps"]), ("exact", 24))
        self.assertIn("platform artifact temporarily unavailable", platform["failure"])
        self.assertEqual([e["payload"]["end_reason"] for e in journal if e["kind"] == "terminal"], ["harness_failure"])
        self.assertTrue(platform["treatment_trace"])
