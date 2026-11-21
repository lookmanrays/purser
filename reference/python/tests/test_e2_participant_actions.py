"""Exercise E2 operation identity and the published plan through actual file-backed ports."""
from copy import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.e1.artifacts import START, Artifacts, read_artifact
from experiments.e1.executor import PARENT
from experiments.e1.recovery import FileArchive
from experiments.e1.run import Journal
from experiments.e2 import plan_versions
from experiments.e2.driver import Driver
from experiments.e2.policy import PlanArm


class ParticipantPorts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.assets = Artifacts(FileArchive(directory))
        self.arm = PlanArm(directory)
        self.arm.submit("W2", START)

    def versions(self):
        path = self.arm.file
        refs = plan_versions.retain(path.parent, self.assets, path.read_bytes())
        return [json.loads(read_artifact(self.assets.archive, ref)) for ref in refs]

    def test_other_operand_and_intermediate_changes_are_distinct_real_attempts(self):
        journal = Journal("loop")
        driver = Driver(None, self.arm, journal, self.assets, 2001, "primary")
        other = copy(self.arm)

        def compound():
            other.opening("W2", "queue/composition")
            expected = self.arm.opening("W2", "W2/retry")
            other.assign_again("W2")
            other.submit("W2", START)
            return expected

        answer = driver.call("plan", "W2", "planner", {"open_run": "W2/retry"}, compound, START)
        self.assertEqual(answer, {"criterion": "W2/retry", "candidate": START})
        actions = [row["payload"] for row in journal.rows if row["kind"] == "action"]
        self.assertEqual([(a["attempt"], a["parent_attempt"], a["port_method"]) for a in actions],
                         [(1, None, "opening"), (2, 1, "opening"), (3, 1, "assign_again"), (4, 1, "submit")])
        self.assertEqual([a["port_input"]["open_run"] for a in actions[:2]], ["W2/retry", "queue/composition"])
        self.assertTrue(all(a["issued_monotonic_ns"] <= a["finished_monotonic_ns"] for a in actions))
        versions = self.versions()[-2:]
        self.assertEqual([(r[PARENT][plan_versions.KEY]["attempt"], r["W2"]["state"]) for r in versions],
                         [(3, "open"), (4, "submitted")])
        self.assertNotIn(plan_versions.KEY, self.arm.context(PARENT))

    def test_orphaned_publication_and_outdated_predecessor_leave_the_head_intact(self):
        before = self.versions()
        write_atomic = plan_versions.write_atomic

        def interrupted(path, contents):
            if path == self.arm.file:
                raise InterruptedError("stopped before the new plan head became visible")
            write_atomic(path, contents)

        with patch.object(plan_versions, "write_atomic", interrupted), self.assertRaises(InterruptedError):
            self.arm.assign_again("W2")
        self.assertEqual(self.versions(), before)
        with plan_versions.expect_head("0" * 64), self.assertRaises(plan_versions.HeadMoved):
            self.arm.assign_again("W2")
        self.assertEqual(self.versions(), before)
