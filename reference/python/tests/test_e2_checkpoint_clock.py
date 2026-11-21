"""Private controls for original W2 admission, physical clock edges and sealed Git recovery."""

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

from experiments.e1 import capture
from experiments.e1.artifacts import FIXTURE, START, Artifacts, Candidates, encoded, sha
from experiments.e1.measurement import MeasurementUnavailable
from experiments.e1.recovery import FileArchive
from experiments.e1.report import GitPacks, Objects
from experiments.e1.suite import collect_cases
from experiments.e2 import checkpoint, clock, measure, policy, trial
from experiments.e2.driver import Driver


class ClockEdges(unittest.TestCase):
    def test_work_and_ingress_on_either_side_of_scoring(self):
        for kind in ("context", "notice"):
            for when in ("before", "during", "after"):
                with self.subTest(kind=kind, when=when), patch.object(signal, "setitimer"):
                    journal = clock.ParticipantClock("loop")
                    def issue():
                        if kind == "notice":
                            payload = {}
                            clock.begin_notice(journal, payload)
                            return payload
                        return journal.begin_action(kind, "W1", "executor", None, {"work": "W1"})

                    pause = {}
                    if when == "before":
                        payload = issue()
                    with journal.measuring_checkpoint(pause):
                        if when == "during":
                            payload = issue()
                    if when == "after":
                        payload = issue()
                    clock.finish_activity(journal, payload)
                    journal.stop()
                    bounds = dict(journal.platform, start_monotonic_ns=journal.start, end_monotonic_ns=journal.end)
                    self.assertIs(clock.quiescence(bounds, [payload], True), when == "after")
                    self.assertTrue(clock.exclusion_accounted(bounds, [payload], True))
                    expected = {"before": pause["start_monotonic_ns"], "during": payload["issued_monotonic_ns"],
                                "after": None}[when]
                    self.assertEqual(pause["first_public_activity_ns"], expected)
                    if when != "after":
                        pause["first_public_activity_ns"] = None
                        self.assertFalse(clock.exclusion_accounted(bounds, [payload], True))
                    with self.assertRaises(RuntimeError):
                        issue()


class ActualSubmission(unittest.TestCase):
    def test_original_submission_on_each_arm(self):
        for arm in ("loop", "purser", "loop_equal_verifier"):
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                assets = Artifacts(FileArchive(root))
                candidates = Candidates(root, assets)
                adapter = policy.KernelArm(root, *trial.definitions()) if arm == "purser" else policy.PlanArm(root)
                journal = clock.ParticipantClock(arm)
                driver = Driver(candidates, adapter, journal, assets, 2001, "primary", 1)
                def original_state():
                    rows, identity = adapter.public_view()
                    head = capture.checkpoint(assets, rows, identity)
                    original, _ = checkpoint.participant_at(driver, rows, head, Objects(assets.archive))
                    return original, head
                def proof():
                    original, head = original_state()
                    submitted, number = checkpoint.accepted_submission(original, head, START)
                    self.assertTrue(checkpoint.old_assignment(original, head, number))
                    return submitted, number
                self.assertIsNot(proof()[0], True)
                adapter.submit("W2", START)
                self.assertEqual(proof(), (True, 1))
                # Losing the attributed invocation cannot turn the public candidate name into an observed submit.
                journal.rows[:] = [row for row in journal.rows if row["kind"] != "action"]
                self.assertIsNot(proof()[0], True)
                driver.track = "clean"
                adapter.receive(policy.notice(2001))
                self.assertFalse(checkpoint.old_assignment(*original_state()))

    def test_one_unavailable_probe_preserves_the_other_seven_at_start(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = Artifacts(FileArchive(root))
            candidates = Candidates(root, assets)
            saved, problems = [], []
            measured = measure.check
            def partial(repo, oid, path, seed, rank, version, labels):
                if version == measure.OLD and "legacy/compat" in labels:
                    raise MeasurementUnavailable("private legacy worker unavailable")
                return measured(repo, oid, path, seed, rank, version, labels)
            with patch.object(measure, "check", partial):
                measure.collect(candidates.archive, [START], 2001, 1, assets, [], [],
                                lambda _, row: saved.append(row), lambda stage, _: problems.append(stage))
            self.assertEqual(len(saved), 7)
            self.assertEqual(len(problems), 1)
            self.assertTrue(all(row["candidate"] == START for row in saved))
            self.assertNotIn(("legacy/compat", 1), {(row["criterion"], row["version"]) for row in saved})


UNIT = [("primary", 2001, 1, "loop")]


def private_collect(directory, run):
    pin = {key: "0" * 64 for key in ("manifest_sha256", "protocol_sha256", "raw_schema_sha256",
                                       "scorer_sha256", "fault_driver_sha256", "executor_sha256")}
    pin.update(fixture_tree=FIXTURE["tree_sha1"], fixture_commit=START, source_commit="0" * 40,
               treatments={"loop": "0" * 64}, replicate=95)
    collect_cases(directory, pin, encoded(pin), UNIT, run, recover=trial.recover, failure=trial.failure)


def stop_after_closure(directory):
    decide = policy.PlanArm.decide
    def interrupted(arm, work, audit):
        reply = decide(arm, work, audit)
        if work == "W-notify" and arm.read()[work]["state"] == "closed":
            os.kill(os.getpid(), signal.SIGKILL)
        return reply
    with patch.object(policy.PlanArm, "decide", interrupted):
        private_collect(directory, trial.attempt)


class StageRecovery(unittest.TestCase):
    def test_original_database_before_and_after_the_first_materialized_witness(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = Artifacts(FileArchive(root))
            adapter = policy.KernelArm(root, *trial.definitions())
            journal = clock.ParticipantClock("purser")
            driver = Driver(None, adapter, journal, assets, 2001, "clean", 1)
            driver.tools.context("W1")
            witnesses = trial.KernelArtifacts(adapter.store.path).directory
            self.assertFalse(witnesses.exists())
            _, _, early = trial.participant(root, assets, "purser")
            self.assertTrue(early["original_events"])
            self.assertEqual(early["kernel_artifacts"], {})
            self.assertNotIn("missing_kernel_artifacts", early)

            message = policy.notice(2001)
            adapter.receive(message)
            digest = sha(encoded(message))
            _, _, retained = trial.participant(root, assets, "purser")
            self.assertIn(digest, retained["kernel_artifacts"])
            (witnesses / digest).rename(root / "quarantined-witness")
            _, _, partial = trial.participant(root, assets, "purser")
            self.assertEqual(partial["original_events"], retained["original_events"])
            self.assertEqual(partial["snapshot"], retained["snapshot"])
            self.assertIn(digest, partial["missing_kernel_artifacts"])
            self.assertNotIn(digest, partial["kernel_artifacts"])

    def test_original_packs_when_entire_disposable_trial_is_gone(self):
        self.check_recovery("trial")

    def test_original_packs_with_only_the_redundant_bare_store_missing(self):
        self.check_recovery("trial/private-objects")

    def check_recovery(self, missing):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            child = subprocess.run([sys.executable, "-B", "-m", __name__, str(directory)], capture_output=True, timeout=95)
            self.assertEqual(child.returncode, -signal.SIGKILL, (child.stdout, child.stderr))
            stage = directory / ".attempt-primary-2001-loop"
            (stage / missing).rename(directory / "quarantined")
            calls = []
            for _ in range(2):
                private_collect(directory, lambda *args, **kwargs: calls.append(args))
            self.assertEqual(calls, [])
            with zipfile.ZipFile(directory / "raw.zip") as archive:
                starts = [name for name in archive.namelist() if name.startswith("started/")]
                self.assertEqual(starts, ["started/primary/2001/loop.json"])
                raw = json.loads(archive.read("runs/primary/2001/loop.json"))
                self.assertEqual(raw["terminal"], "harness_failure")
                objects = Objects(archive)
                platform = objects.item(raw["artifacts"]["platform"])
                self.assertIsNone(platform["participant_exit"])
                if missing == "trial":
                    self.assertNotIn("exit_candidate", platform)
                else:
                    self.assertEqual(platform["exit_candidate"]["source"], "stopped_worker_readback")
                    self.assertFalse(platform["exit_candidate"]["dirty"])
                candidates = objects.item(raw["artifacts"]["candidates"])
                rows = objects.item(raw["artifacts"]["journal"])
                oracles = [e["payload"] for e in rows if e["kind"] == "oracle"]
                self.assertEqual(len(candidates), 5)
                self.assertEqual(len(oracles), 40)
                packs = GitPacks(directory)
                for oid, entry in candidates.items():
                    packs.restore(oid, entry, objects)
                    self.assertEqual(len([o for o in oracles if o["candidate"] == oid]), 8)
                final, = set(candidates) - {entry["parent"] for entry in candidates.values()}
                grades = [o["verdict"] for o in oracles if o["candidate"] == final and
                          (o["version"] == 2 if o["criterion"] in measure.REVISED else o["version"] == 1)]
                self.assertEqual(grades, ["pass"] * 6)


if __name__ == "__main__":
    stop_after_closure(Path(sys.argv[1]))
