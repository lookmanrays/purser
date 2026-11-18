"""Source-bound public decisions across the participant commit/acknowledgment boundary."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from experiments.e1 import run
from experiments.e1.artifacts import Artifacts, read_artifact


class PublicDecisionCapture(unittest.TestCase):
    def attempt(self, directory, assets, arm, track):
        pin = {k: "0"*64 for k in ("manifest_sha256", "protocol_sha256", "raw_schema_sha256",
                                    "scorer_sha256", "fault_driver_sha256", "executor_sha256",
                                    "fixture_tree", "fixture_commit", "source_commit")}
        pin["treatments"] = {arm: "0"*64}
        return run.attempt(directory, assets, pin, arm, track, 1001, 1)

    def test_done_is_a_work_and_parent_completion_through_the_live_loop(self):
        original = run.Plan.update
        def done(plan, work, **fields):
            if fields.get("state") == "closed":
                fields["state"] = "done"
            return original(plan, work, **fields)
        with tempfile.TemporaryDirectory() as tmp, patch.object(run.Plan, "update", done):
            directory = Path(tmp)
            with zipfile.ZipFile(directory / "control.zip", "w") as archive:
                raw = self.attempt(directory, Artifacts(archive), "loop", "clean")
                journal = json.loads(read_artifact(archive, raw["artifacts"]["journal"]))
                platform = json.loads(read_artifact(archive, raw["artifacts"]["platform"]))
                trace = json.loads(read_artifact(archive, platform["treatment_trace"]))
        self.assertEqual((raw["terminal"], raw["cost"]["steps"]), ("done", 24))
        self.assertEqual({row["state"] for row in trace["plan"].values()}, {"done"})
        facts = run.scorer.assess(journal)
        self.assertEqual((facts.facts["correct_parent_closure"], facts.facts["false_parent_closure"]), (True, False))
        self.assertEqual(facts.final_parent, ("done", trace["plan"]["W-notify"]["candidate"]))

    def test_kernel_closure_refusal_is_public_even_when_its_reply_is_lost(self):
        import signal
        send = run.Treatment.send
        def interrupt(treatment, role, ops, **kwargs):
            receipt = send(treatment, role, ops, **kwargs)
            if role == "closure" and receipt["code"] == "closure_blocked":
                signal.raise_signal(signal.SIGALRM)
            return receipt
        with tempfile.TemporaryDirectory() as tmp, patch.object(run.Treatment, "send", interrupt):
            directory = Path(tmp)
            with zipfile.ZipFile(directory / "control.zip", "w") as archive:
                raw = self.attempt(directory, Artifacts(archive), "purser", "primary")
                journal = json.loads(read_artifact(archive, raw["artifacts"]["journal"]))
                platform = json.loads(read_artifact(archive, raw["artifacts"]["platform"]))
                trace = json.loads(read_artifact(archive, platform["treatment_trace"]))
        action = [e["payload"] for e in journal if e["kind"] == "action"][-1]
        recovered = [e["payload"] for e in journal if e["kind"] == "public_state"
                     and e["payload"].get("source_action") == action["attempt"]]
        self.assertEqual(raw["terminal"], "time_limit")
        self.assertEqual(action["return"], {"failure": "time_limit"})
        self.assertEqual(len(recovered), 1)
        evidence = recovered[0]["evidence"]
        self.assertEqual(evidence["diagnosis"], ["W2/retry"])
        self.assertEqual(evidence["receipt"]["digest"], action["proposal"]["digest"])
        self.assertIn(evidence["receipt"], trace["snapshot"]["projection"]["receipts"])
        self.assertTrue(platform["public_history_complete"])
