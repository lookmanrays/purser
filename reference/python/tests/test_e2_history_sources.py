"""Case custody, independent public heads and the exact extent of a proven interval."""

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from experiments.e1.artifacts import Artifacts, sha
from experiments.e1.capture import checkpoint
from experiments.e1.executor import PARENT
from experiments.e1.recovery import FileArchive
from experiments.e1.report import Objects
from experiments.e2 import plan_versions, policy
from experiments.e2.public_sources import CHECKPOINT_FIELDS, identity
from experiments.e2.trial import definitions, participant
from experiments.e2.views import CorroboratedViews


def observed(**fields):
    return {"kind": "action", "payload": fields}


class PlanSources(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.assets = Artifacts(FileArchive(self.root))
        self.plan = policy.PlanArm(self.root)
        self.initial = self.head()
        self.issues, self.unavailable = [], []

    def head(self):
        return checkpoint(self.assets, self.plan.read(), {"plan_sha256": sha(self.plan.file.read_bytes())})

    def write(self, work, state, candidate):
        rows = self.plan.read()
        rows[work].update(state=state, candidate=candidate)
        self.plan.publish(rows)
        return self.head()

    def trace(self):
        refs = plan_versions.retain(self.root, self.assets, self.plan.file.read_bytes())
        return {"plan": self.plan.read(), "plan_versions": refs}

    def read(self, platform, journal=()):
        views = CorroboratedViews("loop", Objects(self.assets.archive), journal, dict(initial_public=self.initial, **platform),
                                  self.issues.append, unavailable=self.unavailable.append)
        return views.originals

    def test_every_checkpoint_operand_can_extend_a_verified_prefix(self):
        old = self.write("W2", "submitted", "a" * 40)
        source = self.assets.put(self.trace(), "json")
        middle = self.write("W1", "closed", "b" * 40)
        last = self.write(PARENT, "submitted", "c" * 40)
        orphan = self.write(PARENT, "submitted", "d" * 40)
        platform = {"checkpoint_measurement": {"checkpoint": old, "submission_source": source}}
        for field in CHECKPOINT_FIELDS:
            with self.subTest(field=field):
                # Only the later digest survived on this operand. It names the
                # exact bytes and then the preceding generation, including middle.
                journal = [observed(**{field: {"plan_sha256": last["plan_sha256"]}}, input={"other": orphan}),
                           {"kind": "oracle", "payload": {"checkpoint": orphan}}]
                history = self.read(platform, journal)
                self.assertEqual([p.checkpoint for p in history.publications][-3:], [old, middle, last])
                self.assertEqual(history.through(old), last)
                self.assertEqual(history.compare(middle, last), -1)
                self.assertEqual(history.candidate_oids(), {c * 40 for c in "abc"})
                self.assertFalse(history.complete)
        self.assertEqual(self.issues, [])
        self.assertTrue(self.unavailable)

    def test_damaged_list_cannot_enroll_a_foreign_replacement_or_hide_the_sound_terminal(self):
        actual = self.write("W2", "submitted", "a" * 40)
        terminal = self.trace()
        foreign = deepcopy(terminal["plan"])
        foreign["W2"]["candidate"] = "b" * 40
        alternative = self.assets.put(foreign, "json")
        damaged = dict(terminal, plan_versions=[*terminal["plan_versions"][:-1], alternative])
        source = self.read({"final_public": actual, "treatment_trace": self.assets.put(terminal, "json"),
                            "checkpoint_measurement": {"checkpoint": actual,
                                                       "submission_source": self.assets.put(damaged, "json")}})
        self.assertTrue(source.complete)
        self.assertEqual(source.candidate_oids(), {"a" * 40})
        self.assertNotIn(alternative["sha256"], source.plan_heads)
        self.assertTrue(any("does not terminate" in issue for issue in self.issues))

    def test_disconnected_suffix_proves_its_own_changes_and_bounds(self):
        old = self.write("W2", "submitted", "a" * 40)
        pause = self.assets.put(self.trace(), "json")
        absent = self.write("W3", "closed", "a" * 40)
        restored = self.write("W1", "closed", "b" * 40)
        reopened = self.write("W1", "open", None)
        (self.root / absent["view"]["path"]).unlink()
        source = self.read({"checkpoint_measurement": {"checkpoint": old, "submission_source": pause},
                            "final_public": reopened})
        self.assertEqual(source.through(old), old)
        self.assertEqual(source.through(restored), reopened)
        self.assertIsNone(source.compare(old, reopened))
        self.assertEqual(source.compare(reopened, restored), 1)
        self.assertIsNone(source.plan_edge(restored))
        self.assertEqual(source.plan_edge(reopened)[0].checkpoint, restored)
        report = {identity("loop", ck): i for i, ck in enumerate((old, restored, reopened))}
        observations = list(source.states_after(old, report))
        self.assertEqual([row[0] for row in observations], list(report))
        self.assertIsNone(observations[1][2])
        self.assertEqual(observations[2][2]["W1"], ("closed", "b" * 40))
        self.assertEqual(self.issues, [])
        self.assertFalse(source.complete)

    def test_fork_has_no_shared_latest_but_each_witness_remains_addressable(self):
        old = self.write("W2", "submitted", "a" * 40)
        pause = self.assets.put(self.trace(), "json")
        left = self.write("W1", "submitted", "b" * 40)
        rows = self.plan.read()
        rows["W1"]["candidate"] = "c" * 40
        other = self.assets.put(rows, "json")
        right = {"plan_sha256": other["sha256"], "view": other}
        source = self.read({"checkpoint_measurement": {"checkpoint": old, "submission_source": pause},
                            "final_public": left}, [observed(after=right)])
        self.assertEqual(source.through(old), old)
        self.assertIsNone(source.compare(left, right))
        self.assertEqual(source.compare(old, right), -1)
        for ck in (left, right):
            self.assertEqual(source.plan_edge(ck)[0].checkpoint, old)
            self.assertIsNotNone(source.lookup(ck))
        self.assertFalse(source.complete)
        self.assertTrue(any("conflicting" in issue for issue in self.issues))

    def test_release_to_final_head_coverage_is_independent_of_lost_pre_release_bytes(self):
        end = self.write("W2", "submitted", "a" * 40)
        trace = self.trace()
        (self.root / trace["plan_versions"][0]["path"]).unlink()
        source = self.read({"treatment_trace": self.assets.put(trace, "json"), "final_public": end})
        self.assertTrue(source.complete)
        self.assertTrue(source.initial_linear)
        self.assertEqual(source.through(self.initial), end)
        self.assertTrue(self.unavailable)
        self.assertEqual(self.issues, [])


class TransmittedKernelView(unittest.TestCase):
    def test_later_return_attests_its_rows_without_creating_an_original_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            assets = Artifacts(FileArchive(directory))
            adapter = policy.KernelArm(directory, *definitions())
            rows, head, trace = participant(directory, assets, "purser")
            initial = checkpoint(assets, rows, head)
            prefix = assets.put(trace, "json")
            candidate = "a" * 40
            adapter.submit("W2", candidate)
            rows, head, _ = participant(directory, assets, "purser")
            later = checkpoint(assets, rows, head)
            platform = {"initial_public": initial,
                        "checkpoint_measurement": {"checkpoint": initial, "submission_source": prefix}}
            issues, missing = [], []
            source = CorroboratedViews("purser", Objects(assets.archive), [observed(port_after=later)],
                                       platform, issues.append, unavailable=missing.append).originals
            self.assertEqual(source.publications[-1].checkpoint, initial)
            self.assertEqual(source.lookup(later)["W2"], ("submitted", candidate))
            self.assertIsNone(source.position(later))
            self.assertIsNone(source.through(later))
            self.assertEqual(source.candidate_oids(), {candidate})
            self.assertFalse(source.complete)
            self.assertEqual(issues, [])
            self.assertTrue(missing)
