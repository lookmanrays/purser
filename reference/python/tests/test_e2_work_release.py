"""Regression controls for the public worker's admission, delivery and transport identity."""
from copy import copy
import os
from pathlib import Path
import tempfile
import unittest

from experiments.e1.artifacts import Artifacts
from experiments.e1.recovery import FileArchive
from experiments.e1.run import Cutoff, Journal
from experiments.e2.actions import local_receiver
from experiments.e2.driver import Driver
from experiments.e2.policy import PlanArm


class WorkRelease(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        assets = Artifacts(FileArchive(directory))
        self.arm = PlanArm(directory)
        self.alias = copy(self.arm)
        self.journal = Journal("loop")
        self.driver = Driver(None, self.arm, self.journal, assets, 2001, "clean")

    def actions(self):
        return [row["payload"] for row in self.journal.rows if row["kind"] == "action"]

    def test_explicit_duplicate_keeps_scalar_return_and_distinct_alias_invocation_is_charged(self):
        receipts = []
        def receive(packet, delivery):
            delivery.received("W1", packet)
            first = dict(delivery.action["delivery"])
            delivery.received("W1", packet)
            self.assertEqual(first, delivery.action["delivery"])
            receipts.append(delivery.attempt)
            return True

        def execute():
            def forward():
                return self.driver.actions.forward(self.alias, "context", "W1", receiver=receive)
            self.assertIs(forward(), True)
            self.assertIs(forward(), True)
            self.assertIs(self.alias.context("W1", receiver=receive), True)
            return {"accepted": True}

        self.driver.call("context", "W1", "executor", {"work": "W1"}, execute)
        self.assertEqual(receipts, [1, 2])
        self.assertEqual([a["parent_attempt"] for a in self.actions()], [None, 1])

    def test_failure_and_absent_acknowledgement_preserve_charge_and_separate_delivery(self):
        def broken_pipe(packet, delivery):
            read_end, write_end = os.pipe()
            os.close(read_end)
            try:
                os.write(write_end, b"x")
            except BrokenPipeError:
                delivery.not_delivered()
                raise
            finally:
                os.close(write_end)
        with self.assertRaises(BrokenPipeError):
            self.alias.context("W1", receiver=broken_pipe)
        self.alias.context("W1")
        self.driver.tools.context("W1")
        failed, pending, obtained = self.actions()
        self.assertEqual(failed["delivery"]["state"], "not_delivered")
        self.assertNotIn("delivery", pending)
        self.assertEqual(obtained["delivery"]["state"], "received")
        self.assertEqual(len({a["prepared_context"]["sha256"] for a in self.actions()}), 1)

    def test_last_read_is_admitted_before_worker_and_next_read_never_reaches_it(self):
        reached = []
        def receive(packet, delivery):
            reached.append((delivery.attempt, self.journal.steps))
            return local_receiver(packet, delivery)
        # Genuine repeated independent invocations share one unchanging original plan.
        for _ in range(95):
            self.alias.context("W1", receiver=receive)
        self.driver.call("context", "W1", "executor", {"work": "W1"},
                         lambda: self.alias.context("W1", receiver=receive))
        with self.assertRaisesRegex(Cutoff, "step_limit"):
            self.alias.context("W3", receiver=receive)
        self.assertEqual(reached, [(i, i) for i in range(1, 97)])
        self.assertEqual([a["attempt"] for a in self.actions()], list(range(1, 97)))


if __name__ == "__main__":
    unittest.main()
