"""Use genuine worker inspections to distinguish Git selection, received values, and callback returns."""
from copy import deepcopy
from itertools import product
from pathlib import Path
import tempfile
import unittest

from experiments.e1.artifacts import Artifacts, Candidates, START
from experiments.e1.capture import response_evidence
from experiments.e1.executor import PARENT, PATHS
from experiments.e1.recovery import FileArchive
from experiments.e1.report import GitPacks, Objects
from experiments.e1.run import Journal
from experiments.e2 import git_reads
from experiments.e2.candidate_evidence import Catalog, inspected_oids
from experiments.e2.driver import Driver
from experiments.e2.policy import PlanArm


class ScopedGitObservations(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.assets = Artifacts(FileArchive(root))
        self.candidates = Candidates(root, self.assets)
        self.journal = Journal("loop")
        self.driver = Driver(self.candidates, PlanArm(root), self.journal, self.assets, 2001, "primary")
        readback = root / "reader"
        readback.mkdir()
        self.objects = Objects(self.assets.archive)
        self.catalog = Catalog(self.objects, GitPacks(readback))
        path = self.candidates.work / PATHS["W2"]
        with path.open("a") as f:
            f.write("\n# First independent change.\n")
        self.middle = self.candidates.commit("first fixture change")
        with path.open("a") as f:
            f.write("\n# Next independent change.\n")
        self.tip = self.candidates.commit("second fixture change")

    def read(self, tool="git_history", subject=PARENT, **kwargs):
        self.driver.tools.inspect(tool, subject, **kwargs)
        return self.last()

    def last(self):
        return next(row["payload"] for row in reversed(self.journal.rows) if row["kind"] == "action")

    def selection(self, action, *, modern=True, lookup=True):
        issues = []
        found = inspected_oids([{"kind": "action", "payload": action}], "loop", self.objects, issues.append,
                               lookup=self.catalog.repository_for if lookup else None, seed=self.driver.seed,
                               start=self.journal.start, native_producer=modern)
        return found, issues

    def set_received(self, action, value):
        ref = self.assets.put({"value": value}, "json")
        action["delivery"].update(payload=ref, sha256=ref["sha256"])

    def set_reply(self, action, value):
        action["return"] = {"value": value}
        action["response_evidence"] = self.assets.put(response_evidence(action), "json")

    def legacy(self, *args, **kwargs):
        action = deepcopy(self.read(*args, **kwargs))
        del action["git_read"]
        return action

    def test_typed_reads_and_explicit_historical_operands_use_the_real_command_subject(self):
        for tool, work, explicit in product(git_reads.TOOLS, (PARENT, *PATHS), (None, START)):
            with self.subTest(tool=tool, work=work, requested=explicit):
                action = self.read(tool, work, candidate=explicit)
                actual = explicit or self.tip
                source = self.objects.item(action["git_read"])
                self.assertEqual(source["selected"], actual)
                self.assertEqual({"value": source["value"]}, self.objects.item(action["delivery"]["payload"]))
                self.assertEqual(self.selection(action), ({actual}, []))
                if tool == "git_history" and work == "W1":
                    self.assertEqual(source["value"], [START])
                if tool == "git_diff" and work == "W1":
                    self.assertEqual(source["value"], "")

    def test_transformed_or_failed_callback_preserves_the_original_git_operation(self):
        def transformed(value, witness):
            native = self.objects.item(self.last()["git_read"])
            self.assertEqual(native["selected"], self.tip)
            self.assertEqual(native["value"], value)
            witness.received(PARENT, value)
            return [START]
        action = self.read(receiver=transformed)
        self.assertEqual(action["return"], {"value": [START]})
        self.assertEqual(self.selection(action), ({self.tip}, []))

        def stopped(value, witness):
            witness.received(PARENT, value)
            raise OSError("acknowledgment lost")
        with self.assertRaises(OSError):
            self.read(receiver=stopped)
        self.assertNotIn("response_evidence", self.last())
        self.assertEqual(self.selection(self.last()), ({self.tip}, []))
        with self.assertRaisesRegex(ValueError, "unknown study candidate"):
            self.read(candidate="b" * 40)
        self.assertIn("work_started", self.last())
        self.assertNotIn("git_read", self.last())
        self.assertEqual(self.selection(self.last()), (set(), []))

    def test_legacy_implicit_conflicting_full_history_never_adds_the_alternate_tip(self):
        ordinary = self.legacy(candidate=None)
        self.set_received(ordinary, [START])
        for lookup in (False, True):
            with self.subTest(bytes=lookup):
                found, issues = self.selection(ordinary, modern=False, lookup=lookup)
                self.assertEqual(found, set())
                self.assertIn("conflict", " ".join(issues))
        # An addressed return alone can be an arbitrary callback acknowledgement.
        del ordinary["delivery"]
        self.assertEqual(self.selection(ordinary, modern=False), (set(), []))

    def test_authenticated_history_checks_all_ancestry_and_path_filtering(self):
        action = self.legacy()
        self.set_received(action, [self.tip, START])
        self.set_reply(action, [self.tip, START])
        found, issues = self.selection(action, modern=False)
        self.assertFalse(found)
        self.assertIn("ancestry", " ".join(issues))
        path_read = self.legacy("git_history", "W1", candidate=self.tip)
        self.assertEqual(path_read["return"], {"value": [START]})
        self.assertEqual(self.selection(path_read, modern=False), ({self.tip}, []))
        implicit = self.legacy("git_history", "W1")
        self.assertEqual(self.selection(implicit, modern=False), (set(), []))

    def test_bad_receiver_and_inline_return_cannot_replace_the_authenticated_original(self):
        action = self.legacy(candidate=START)
        self.set_received(action, [self.tip, self.middle, START])
        action["return"] = {"value": [self.tip, self.middle, START]}
        found, issues = self.selection(action, modern=False)
        self.assertEqual(found, {START})
        self.assertTrue(any("inline" in msg for msg in issues))
        self.assertTrue(any("receiver" in msg for msg in issues))
        self.assertEqual(self.objects.item(action["response_evidence"])["return"], {"value": [START]})

    def test_case_or_call_mismatched_native_pointer_cannot_name_an_implicit_scoped_read(self):
        action = self.read("git", "W1")
        source = self.objects.item(action["git_read"])
        for update in ({"seed": 2002}, {"arm": "purser"}, {"attempt": 2},
                       {"issued_monotonic_ns": source["issued_monotonic_ns"] + 1}):
            with self.subTest(changed=list(update)):
                copy = dict(action, git_read=self.assets.put(dict(source, **update), "json"))
                found, issues = self.selection(copy)
                self.assertEqual(found, set())
                self.assertIn("different case or invocation", " ".join(issues))
