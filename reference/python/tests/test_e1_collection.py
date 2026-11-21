import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from experiments.e1 import run, suite
from experiments.e1.artifacts import ROOT, encoded, read_artifact
from experiments.e1.recovery import failure_record


class CollectionRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        original = ROOT / "experiments/results/e1-scripted-v1/freeze.json"
        self.pin = {**json.loads(original.read_bytes()), "replicate": 2,
                    "replication_reason": "Controlled recovery check"}
        self.registration = encoded(self.pin)
        (self.directory / "freeze.json").write_bytes(self.registration)

    def collect(self, units, runner):
        suite.collect_cases(self.directory, self.pin, self.registration, units, runner)

    def test_two_collectors_cannot_release_the_same_registered_case(self):
        program = """
import json, os, sys, time
from pathlib import Path
from experiments.e1 import suite
from experiments.e1.recovery import failure_record
directory = Path(sys.argv[1])
registration = (directory / "freeze.json").read_bytes()
suite.committed_registration = lambda path: (json.loads(registration), registration)
suite.schedule = lambda: [("clean", 1001, 1, "loop")]
def single_release(work, assets, pin, arm, track, seed, rank, *, progress):
    with (directory / "releases").open("a") as log:
        log.write(str(os.getpid()) + chr(10))
    time.sleep(0.25)
    return failure_record(assets, pin, arm, track, seed, rank, reason="controlled concurrent release",
                          progress={"cost_basis": "before_release"})
suite.run.attempt = single_release
suite.collect(directory)
"""
        command = [sys.executable, "-B", "-c", program, str(self.directory)]
        children = [subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for _ in range(2)]
        for child in children:
            _, err = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, err.decode())
        self.assertEqual(len((self.directory / "releases").read_text().splitlines()), 1)

    def test_interruption_during_publication_preserves_last_archive_and_does_not_repeat_a_case(self):
        units = list(suite.schedule())[:3]
        attempts = []

        def setup_failure(directory, assets, pin, arm, track, seed, rank, *, progress):
            attempts.append(arm)
            return failure_record(assets, pin, arm, track, seed, rank, reason="test setup failure",
                                  progress={"cost_basis": "before_release"})

        self.collect(units[:1], setup_failure)
        path = self.directory / "raw.zip"
        first = path.read_bytes()
        replace = os.replace

        def interrupt_publication(source, dest):
            if dest == path:
                raise InterruptedError("publication interrupted before rename")
            replace(source, dest)

        with patch.object(suite.os, "replace", side_effect=interrupt_publication):
            with self.assertRaises(InterruptedError):
                self.collect(units, setup_failure)
        self.assertEqual(path.read_bytes(), first)
        self.collect(units, setup_failure)
        self.assertEqual(attempts, [unit[3] for unit in units])
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual(len([name for name in names if name.startswith("runs/")]), 3)
            self.assertEqual(len([name for name in names if name.startswith("started/")]), 3)

    def test_sigkill_after_real_durable_close_preserves_prefix_and_collects_next_case(self):
        units = [("clean", 1001, 1, "loop"), ("clean", 1001, 1, "loop_equal_verifier")]
        program = """
import json, os, signal, sys
from pathlib import Path
from experiments.e1 import run, suite
from experiments.e1.arms import Plan
from experiments.e1.executor import PARENT
original = Plan.decide
def die_after_close(self, work, audit):
    result = original(self, work, audit)
    if work == PARENT and self.states()[PARENT] == "closed":
        os.kill(os.getpid(), signal.SIGKILL)
    return result
Plan.decide = die_after_close
directory = Path(sys.argv[1])
registration = (directory / "freeze.json").read_bytes()
suite.collect_cases(directory, json.loads(registration), registration, json.loads(sys.argv[2]), run.attempt)
"""
        child = subprocess.run([sys.executable, "-B", "-c", program, str(self.directory),
                                json.dumps(units)], cwd=ROOT, capture_output=True, timeout=35)
        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr.decode())
        invoked = []

        def resumed(*args, **kwargs):
            invoked.append(args[3])
            return run.attempt(*args, **kwargs)

        self.collect(units, resumed)
        self.collect(units, resumed)
        self.assertEqual(invoked, ["loop_equal_verifier"])
        with zipfile.ZipFile(self.directory / "raw.zip") as archive:
            raw = json.loads(archive.read("runs/clean/1001/loop.json"))
            self.assertEqual((raw["validity"], raw["score"], raw["replicate"]), ("infrastructure_invalid", None, 2))
            self.assertEqual(raw["cost"]["steps"], 24)
            history = json.loads(read_artifact(archive, raw["artifacts"]["journal"]))
            closed = [r for r in history if r["kind"] == "public_state" and
                      r["payload"]["subject"] == "W-notify" and r["payload"]["to"] == "closed"]
            self.assertEqual(len(closed), 1)
            candidates = json.loads(read_artifact(archive, raw["artifacts"]["candidates"]))
            final_oid = closed[0]["payload"]["candidate"]
            read_artifact(archive, candidates[final_oid]["pack"])
            measured = {r["payload"]["criterion"]: r["payload"]["verdict"] for r in history
                        if r["kind"] == "oracle" and r["payload"]["candidate"] == final_oid}
            self.assertEqual(measured, {criterion: "pass" for criterion in run.scorer.LABELS})
            facts = json.loads(read_artifact(archive, raw["artifacts"]["platform"]))
            self.assertEqual(facts["cost_basis"], "observed_lower_bound")
            self.assertGreater(raw["cost"]["elapsed_ns"], 0)
            self.assertEqual(history[-1]["payload"]["end_reason"], "harness_failure")
            following = json.loads(archive.read("runs/clean/1001/loop_equal_verifier.json"))
            self.assertEqual((following["validity"], following["cost"]["steps"]), ("complete", 42))
