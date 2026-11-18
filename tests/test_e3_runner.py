"""E3's registered participant through real guarded Git and cold Purser admissions."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from experiments.e1.artifacts import Artifacts, FIXTURE, MANIFEST, ROOT, read_artifact, sha
from experiments.e1.recovery import FileArchive
from experiments.e3 import suite, trial
from experiments.e3.fixture import E3, TARGET, intent_for, read
from experiments.e3.witness import transitions


def pilot_pin():
    # These tests are private runner probes; only the committed suite can produce an official registration.
    test = sha(Path(__file__).read_bytes())
    return {"fixture_tree": FIXTURE["tree_sha1"], "fixture_commit": FIXTURE["commit_sha1"],
            **{key: test for key in ("manifest_sha256", "protocol_sha256", "raw_schema_sha256", "scorer_sha256",
                                     "fault_driver_sha256", "executor_sha256")},
            "treatments": dict.fromkeys(MANIFEST["units"]["arm_order"], test), "source_commit": "0" * 40}


class Runner(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.assets = Artifacts(FileArchive(self.base))
        self.pin = pilot_pin()

    def item(self, ref):
        return json.loads(read_artifact(self.assets.archive, ref))

    def run_case(self, arm, track, rank):
        directory = self.base / f"{arm}-{track}-{rank}"
        directory.mkdir()
        raw = trial.run_participant(directory, self.assets, self.pin, arm, track, 3000 + rank, rank)
        platform = self.item(raw["artifacts"]["platform"])
        self.assertNotEqual(raw["terminal"], "harness_failure", platform.get("failure"))
        return raw, platform, platform["e3"]

    def test_schedule_has_the_registered_rotation_and_populations(self):
        units = list(suite.schedule())
        self.assertEqual(units[:3], [("primary", 3001, 1, arm) for arm in ("loop_equal_verifier", "loop", "purser")])
        self.assertEqual(units[3:6], [("primary", 3002, 2, arm) for arm in ("loop", "purser", "loop_equal_verifier")])
        self.assertEqual(len(set(units)), 66)
        self.assertEqual({u[1] for u in units if u[0] == "primary"}, set(E3["seeds"]))

    def test_first_disposition_precedes_fresh_release_and_complete_native_transition(self):
        for arm in ("purser", "loop", "loop_equal_verifier"):
            with self.subTest(arm=arm):
                raw, platform, case = self.run_case(arm, "primary", 2)
                self.assertEqual((raw["terminal"], raw["cost"]["steps"]), ("done", 7 if arm == "loop" else 8))
                self.assertEqual(case["intent"], intent_for(3002))
                self.assertEqual(self.item(raw["artifacts"]["candidates"])[TARGET]["parent"], FIXTURE["commit_sha1"])
                calls = [(self.item(p["request"]), self.item(p["response"])) for p in case["actions"]]
                for (req, reply), (next_req, _) in zip(calls, calls[1:]):
                    self.assertLessEqual(req["issued_monotonic_ns"], reply["finished_monotonic_ns"])
                    self.assertLessEqual(reply["finished_monotonic_ns"], next_req["issued_monotonic_ns"])
                self.assertEqual(calls[2][1]["return"], {"status": "timeout"})
                self.assertEqual(calls[3][0]["input"]["delivery"], "explicit" if arm == "loop" else "automatic")
                self.assertEqual(calls[3][1]["return"]["report"]["state"], "confirmed_absent")
                original, final = [self.item(ref) for ref in case["declarations"]]
                self.assertEqual((original["report"], original["choice"], original["basis"]), ("unlanded", "retry", "action-4"))
                self.assertLess(original["published_ns"], calls[5][0]["issued_monotonic_ns"])
                self.assertEqual((final["report"], final["original_report"]), ("landed", "unlanded"))
                self.assertEqual(transitions(self.item(case["native_snapshots"]["after-T1"])), [])
                update, = transitions(self.item(case["final_native"]))
                self.assertEqual((update["old"], update["new"]), (FIXTURE["commit_sha1"], TARGET))
                self.assertTrue(update["message"].endswith(" T2"))
                self.assertEqual(sorted(case["writers"]), ["T1", "T2"])
                first = self.item(case["writers"]["T1"])
                events = {e["event"]: e for e in first["events"]}
                self.assertEqual(events["reaped"]["returncode"], 76)
                self.assertNotIn("written", events)
                self.assertLess(events["reaped"]["at_ns"], calls[3][0]["issued_monotonic_ns"])
                if arm == "purser":
                    trace = self.item(case["kernel"])
                    names = [json.loads(value)["type"] for value in trace["original_events"]]
                    self.assertEqual(names.count("EffectDispatchAuthorized"), 2)
                    self.assertEqual(names.count("GitEffectObserved"), 2)
                    self.assertEqual(len(trace["kernel_artifacts"]), 2)
                else:
                    self.assertEqual(events["permission_checked"]["preparation"]["changed_paths"], sorted(FIXTURE["public_paths"]))
                    self.assertIsNone(case["kernel"])

    def test_landed_timeout_and_clean_ack_use_their_actual_public_evidence(self):
        for arm, track, expected in (("loop", "primary", 5), ("loop", "clean", 4), ("purser", "clean", 5)):
            with self.subTest(arm=arm, track=track):
                raw, platform, case = self.run_case(arm, track, 1)
                self.assertEqual((raw["terminal"], raw["cost"]["steps"]), ("done", expected))
                call = self.item(case["actions"][2]["response"])["return"]
                self.assertEqual(call["status"], "acknowledged" if track == "clean" else "timeout")
                original, = [self.item(ref) for ref in case["declarations"]]
                self.assertEqual((original["original_report"], original["choice"]), ("landed", "complete"))
                self.assertEqual(len(transitions(self.item(case["final_native"]))), 1)
                self.assertEqual(list(case["writers"]), ["T1"])
                self.assertEqual(platform["end_monotonic_ns"] - platform["start_monotonic_ns"], raw["cost"]["elapsed_ns"])

    def test_stress_observes_a_live_writer_and_ends_before_its_teardown(self):
        raw, platform, case = self.run_case("purser", E3["stress"]["id"], 1)
        self.assertEqual((raw["terminal"], raw["cost"]["steps"]), ("blocked", 5))
        original = self.item(case["declarations"][0])
        self.assertEqual((original["original_report"], original["choice"], original["reason"]),
                         ("unknown", "pending", "writer_may_still_apply"))
        self.assertTrue(original["next_action"])
        observation = self.item(case["actions"][3]["response"])["return"]["report"]
        self.assertFalse(observation["writer_quiescent"])
        self.assertFalse(observation["history_intact"])
        events = {e["event"]: e for e in self.item(case["writers"]["T1"])["events"]}
        self.assertIsNone(events["exit_check"]["returncode"])
        self.assertLess(original["published_ns"], platform["participant_exit"]["end_monotonic_ns"])
        self.assertLess(platform["end_monotonic_ns"], self.item(case["final_native"])["started_ns"])
        self.assertLess(self.item(case["final_native"])["finished_ns"], events["teardown_reaped"]["at_ns"])
        self.assertLess(events["teardown_reaped"]["at_ns"], self.item(case["teardown_native"])["started_ns"])
        self.assertNotIn("written", events)
        self.assertEqual(transitions(self.item(case["final_native"])), [])

    def test_step_and_elapsed_cutoffs_keep_attempted_work(self):
        budget = MANIFEST["budgets"]["scripted"]
        with patch.dict(budget, max_steps=2):
            raw, platform, case = self.run_case("loop", "primary", 1)
        self.assertEqual((raw["terminal"], raw["validity"], raw["cost"]["steps"]), ("step_limit", "arm_incomplete", 2))
        self.assertEqual(case["writers"], {})
        with patch.dict(budget, max_elapsed_s=0.15), patch("experiments.e3.actions.Tools.context",
                lambda tools: tools.j.invoke("context", "executor", {"assignment": "AS-notify"},
                                             lambda _: time.sleep(1))):
            raw, platform, case = self.run_case("loop", "primary", 2)
        self.assertEqual((raw["terminal"], raw["validity"], raw["cost"]["steps"]), ("time_limit", "arm_incomplete", 1))
        self.assertGreaterEqual(raw["cost"]["elapsed_ns"], 150_000_000)
        self.assertIn("time_limit", self.item(case["actions"][0]["response"])["return"]["failure"])

    def test_collector_loss_keeps_original_decision_without_rerunning_a_writer(self):
        directory = self.base / "stopped"
        directory.mkdir()
        source = """from pathlib import Path
import os, sys
from experiments.e1.artifacts import Artifacts
from experiments.e1.recovery import FileArchive
from experiments.e3.fixture import write
from experiments.e3.trial import run_participant
from tests.test_e3_runner import pilot_pin
root = Path(sys.argv[1])
def checkpoint(record):
    write(root / 'progress.json', record)
    if record['rows'] and record['rows'][-1]['kind'] == 'public_state':
        os._exit(73)
run_participant(root, Artifacts(FileArchive(root.parent)), pilot_pin(), 'loop', 'primary', 3002, 2, progress=checkpoint)
"""
        proc = subprocess.run([sys.executable, "-B", "-c", source, str(directory)], cwd=ROOT, capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 73, proc.stderr)
        saved = trial.recover(directory, self.assets, "loop", read(directory / "progress.json"), seed=3002, rank=2)
        failed = trial.failure(self.assets, self.pin, "loop", "primary", 3002, 2, reason="collector exited", progress=saved)
        self.assertEqual((failed["terminal"], failed["validity"], failed["cost"]["steps"]),
                         ("harness_failure", "infrastructure_invalid", 5))
        platform = self.item(failed["artifacts"]["platform"])
        case = platform["e3"]
        self.assertIsNone(case["actions"][-1]["response"])
        self.assertEqual(self.item(case["declarations"][0])["original_report"], "unlanded")
        self.assertEqual(list(case["writers"]), ["T1"])
        self.assertIsNone(case["final_native"])
        self.assertEqual(transitions(self.item(case["recovery_native"])), [])
        self.assertIsNone(platform["participant_exit"])
        self.assertEqual(platform["cost_basis"], "observed_lower_bound")
