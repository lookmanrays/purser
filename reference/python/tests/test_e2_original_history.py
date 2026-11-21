"""Exercise physical worker receipts and lost acknowledgments against original participant publications."""

from copy import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.e1 import capture
from experiments.e1.artifacts import Artifacts
from experiments.e1.recovery import FileArchive
from experiments.e1.report import Objects
from experiments.e1.run import Journal
from experiments.e2 import policy
from experiments.e2.driver import Driver
from experiments.e2.public_history import read_participant
from experiments.e2.trial import definitions, participant


class OriginalParticipantEvidence(unittest.TestCase):
    def trial(self, arm):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        assets = Artifacts(FileArchive(root))
        adapter = policy.KernelArm(root, *definitions()) if arm == "purser" else policy.PlanArm(root)
        journal = Journal(arm)
        driver = Driver(None, adapter, journal, assets, 2001, "clean")

        def read(events=None):
            rows, identity, trace = participant(root, assets, arm)
            platform = dict(journal.platform, final_public=capture.checkpoint(assets, rows, identity),
                            treatment_trace=assets.put(trace, "json"))
            issues = []
            history = read_participant(arm, journal.rows if events is None else events, platform,
                                       Objects(assets.archive).item, issues.append, seed=2001, track="clean")
            return history, issues

        return driver, copy(adapter), read

    def test_actual_handoff_preparation_failure_and_unknown_are_distinct(self):
        for arm in ("loop", "purser", "loop_equal_verifier"):
            with self.subTest(arm=arm):
                driver, alias, read = self.trial(arm)
                self.assertEqual(driver.tools.context("W1")["work"]["id"], "W1")

                def closed_pipe(packet, receipt):
                    receipt.not_delivered()
                    raise BrokenPipeError("closed before transfer")

                with self.assertRaises(BrokenPipeError):
                    alias.context("W1", receiver=closed_pipe)
                history, issues = read()
                self.assertEqual(issues, [])
                self.assertTrue(history.clean_exposure)
                self.assertEqual([r["intended_received"] for r in history.received_contexts], [True, False])

                alias.context("W1")
                history, issues = read()
                self.assertEqual(issues, [])
                self.assertIsNone(history.clean_exposure)
                self.assertEqual(history.action_lower_bound, 3)
                self.assertTrue(history.issued_coverage)

                wrong = alias._context_from_publication("W3")[2]
                actual = dict(wrong, premise=policy.notice(2001))

                def received_wrong(packet, receipt):
                    self.assertEqual(packet["work"]["id"], "W1")
                    receipt.received(actual["work"]["id"], actual)
                    return False

                self.assertFalse(alias.context("W1", receiver=received_wrong))
                history, issues = read()
                self.assertEqual(issues, [])
                self.assertFalse(history.clean_exposure)
                self.assertEqual(history.received_contexts[-1]["received_subject"], "W3")
                self.assertFalse(history.received_contexts[-1]["intended_received"])
                self.assertEqual(history.action_lower_bound, 4)

    def test_original_notice_survives_loss_of_redundant_ingress_rows(self):
        for arm in ("loop", "purser", "loop_equal_verifier"):
            with self.subTest(arm=arm):
                _, alias, read = self.trial(arm)
                alias.receive(policy.notice(2001))
                history, issues = read(events=[])
                self.assertEqual(issues, [])
                self.assertTrue(history.fault_receipt)
                self.assertFalse(history.clean_exposure)
                self.assertEqual(history.action_lower_bound, 0)
                faults = [r["payload"] for r in history.journal if r["kind"] == "fault"]
                self.assertEqual(len(faults), 1)
                self.assertTrue(faults[0]["inferred_from_original"])
                self.assertNotEqual(faults[0]["before"], faults[0]["after"])

    def test_overlapping_issue_and_received_changed_context_have_independent_order(self):
        for arm in ("loop", "purser", "loop_equal_verifier"):
            for before_notice in (True, False):
                with self.subTest(arm=arm, before_notice=before_notice):
                    driver, alias, read = self.trial(arm)
                    message = policy.notice(2001)

                    def receiver(packet, receipt):
                        altered = dict(packet, premise=message)
                        if before_notice:
                            receipt.received("W1", altered)
                        alias.receive(message)
                        if not before_notice:
                            receipt.received("W1", altered)
                        return {"observed_work": "W1"}

                    driver.tools.context("W1", receiver=receiver)
                    history, issues = read()
                    self.assertEqual(issues, [])
                    self.assertFalse(history.numbered[1]["after_intervention"])
                    self.assertFalse(history.clean_exposure)
                    # A later acknowledgement alone cannot date first delivery in
                    # an already-running request.
                    self.assertIs(history.prior_changed_exposure, True if before_notice else None)

    def test_request_after_original_publication_inside_its_open_ack_interval(self):
        for arm in ("loop", "purser", "loop_equal_verifier"):
            with self.subTest(arm=arm):
                driver, alias, read = self.trial(arm)
                mutation = "send" if arm == "purser" else "update"
                original = getattr(alias, mutation)

                def before_ack(*args, **kwargs):
                    reply = original(*args, **kwargs)
                    driver.tools.context("W1")
                    return reply

                with patch.object(alias, mutation, before_ack):
                    alias.receive(policy.notice(2001))
                history, issues = read()
                self.assertEqual(issues, [])
                self.assertTrue(history.numbered[1]["after_intervention"])
                self.assertFalse(history.prior_changed_exposure)
