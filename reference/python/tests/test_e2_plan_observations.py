"""Publicly witnessed plan operations, missing whole heads and scoped row evidence."""
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from experiments.e1.artifacts import Artifacts, sha
from experiments.e1.capture import checkpoint
from experiments.e1.executor import PARENT
from experiments.e1.recovery import FileArchive
from experiments.e1.report import Objects
from experiments.e1.run import Journal
from experiments.e2 import plan_versions, policy
from experiments.e2.checkpoint import old_assignment
from experiments.e2.driver import Driver
from experiments.e2.views import CorroboratedViews


class PlanObservations(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.assets = Artifacts(FileArchive(self.root))
        self.arm = policy.PlanArm(self.root)
        self.journal = Journal("loop_equal_verifier")
        self.driver = Driver(None, self.arm, self.journal, self.assets, 2001, "clean")
        self.initial = self.checkpoint()
        self.issues = []

    def checkpoint(self):
        rows, identity = self.arm.public_view()
        return checkpoint(self.assets, rows, identity)

    def source(self, events, final=None):
        platform = dict(initial_public=self.initial, **({"final_public": final} if final else {}))
        return CorroboratedViews(self.journal.arm, Objects(self.assets.archive), events, platform,
                                 self.issues.append, unavailable=lambda _: None).originals

    def test_each_public_write_restores_its_exact_canonical_bytes(self):
        candidate = "a" * 40
        notice = policy.notice(2001)
        self.arm.submit("W2", candidate)
        self.arm.receive(notice)
        self.arm.revise(notice)
        self.arm.assign_again("W2")
        self.arm.submit("W2", candidate)
        self.arm.verify("W2", "W2/retry", {"candidate": candidate, "verdict": "inconclusive"})
        self.arm.decide("W2", {"missing": []})
        self.arm.submit(PARENT, candidate)
        self.arm.decide(PARENT, {"missing": []})
        final = self.checkpoint()
        refs = plan_versions.retain(self.root, self.assets, self.arm.file.read_bytes())
        self.assertEqual(len(refs), 11)
        for ref in refs:
            path = self.root / ref["path"]
            expected = path.read_bytes()
            path.unlink()
            try:
                source = self.source(self.journal.rows, final=final)
                digest = ref["sha256"]
                self.assertIn(digest, source.plan_heads)
                self.assertEqual(source._plan_point(digest).checkpoint["view"], ref)
                self.assertEqual(source.through(self.initial), final)
                self.assertEqual(sha(expected), digest)
            finally:
                path.write_bytes(expected)
        self.assertEqual(self.issues, [])

    def test_sender_row_survives_an_unreconstructible_version_and_transformed_inspection(self):
        candidate = "b" * 40
        self.arm.submit("W2", candidate)
        middle = self.checkpoint()

        def receiver(row, receipt):
            foreign = dict(row, candidate="c" * 40)
            receipt.received("W2", foreign)
            return {"accepted_work": "W2"}

        self.assertEqual(self.driver.tools.inspect("plan", "W2", receiver=receiver), {"accepted_work": "W2"})
        self.driver.tools.context("W2")
        events = [r for r in self.journal.rows if r["kind"] == "action" and r["payload"]["action"] == "context"]
        (self.root / middle["view"]["path"]).unlink()
        for e in events:
            source = self.source([e])
            self.assertNotIn(middle["plan_sha256"], source.plan_heads)
            self.assertIsNone(source.lookup(middle))
            self.assertEqual(source.plan_observations[middle["plan_sha256"]], {"W2": ("submitted", candidate)})
            self.assertEqual(source.context(middle, "W2")["candidate"], candidate)
            self.assertTrue(old_assignment(SimpleNamespace(arm=self.journal.arm, source=source), middle))
            self.assertNotIn("c" * 40, source.candidate_oids())
            with self.assertRaises(KeyError):
                source.context(middle, "W3")
            stable = deepcopy(e)
            del stable["payload"]["context_source"]
            self.assertEqual(self.source([stable]).context(middle, "W2")["candidate"], candidate)
        legacy = deepcopy(events[0])
        for key in ("prepared_context", "context_source"):
            del legacy["payload"][key]
        # A callback may send a different row. It supplies a digest hypothesis
        # for whole-byte authentication, never an independent source assignment.
        source = self.source([legacy])
        self.assertNotIn(middle["plan_sha256"], source.plan_observations)
        self.assertEqual(self.issues, [])

    def test_following_digest_can_authenticate_a_write_with_a_lost_entry_view(self):
        self.arm.submit("W2", "a" * 40)
        before = self.checkpoint()
        self.arm.assign_again("W2")
        target = self.checkpoint()
        self.arm.submit("W3", "b" * 40)
        final = self.checkpoint()
        events = deepcopy(self.journal.rows)
        action = next(r["payload"] for r in events if r["kind"] == "action" and r["payload"]["attempt"] == 2)
        del action["before"], action["port_before"]
        (self.root / target["view"]["path"]).unlink()
        source = self.source(events)
        self.assertEqual(source.plan_edge(target)[0].checkpoint, before)
        self.assertEqual(source.through(self.initial), final)
        self.assertEqual(self.issues, [])

    def test_adjacent_successor_and_original_closure_reply_recover_only_the_named_object(self):
        candidate = "a" * 40
        self.arm.submit("W2", candidate)
        before = self.checkpoint()
        self.arm.decide("W2", {"missing": ["W2/retry"]})
        target = self.checkpoint()
        self.arm.submit("W2", "b" * 40)
        after = self.checkpoint()
        # Neither the first invocation nor its resulting file survived. The
        # next writer retained the other three rows. Of the closure replies,
        # only its independently content-addressed original remains.
        events = deepcopy([r for r in self.journal.rows if r["kind"] == "action" and r["payload"]["attempt"] > 1])
        close = events[0]["payload"]
        for key in ("port_input", "port_return", "return", "work_request", "work_started"):
            close.pop(key, None)
        del close["input"]["public_review"]
        for ck in (before, target):
            (self.root / ck["view"]["path"]).unlink()
        source = self.source(events)
        self.assertNotIn(before["plan_sha256"], source.plan_heads)
        self.assertEqual(source.plan_edge(after)[0].checkpoint, target)
        self.assertEqual(source.context(target, "W2")["candidate"], candidate)
        self.assertIsNone(source.plan_edge(target))
        self.assertEqual(self.issues, [])

    def test_partial_state_conflict_is_local_and_a_whole_head_can_resolve_it(self):
        self.arm.submit("W2", "a" * 40)
        ck = self.checkpoint()
        self.driver.tools.context("W2")
        events = [r for r in self.journal.rows if r["kind"] == "action" and r["payload"]["action"] == "context"]
        def state(work, status, oid):
            return {"kind": "public_state", "payload": {"checkpoint": ck, "subject": work, "to": status, "candidate": oid}}
        events.extend([state("W2", "submitted", "b" * 40), state("W3", "open", None)])
        path = self.root / ck["view"]["path"]
        data = path.read_bytes()
        path.unlink()
        source = self.source(events)
        self.assertEqual(source.plan_observations[ck["plan_sha256"]], {"W3": ("open", None)})
        self.assertNotIn("b" * 40, source.candidate_oids())
        self.assertTrue(any("conflicting" in error for error in self.issues))
        path.write_bytes(data)
        source = self.source(events)
        self.assertEqual(source.context(ck, "W2")["candidate"], "a" * 40)
        self.assertNotIn("b" * 40, source.candidate_oids())
        self.assertTrue(any("differs from authenticated" in error for error in self.issues))

    def test_conflicting_context_origin_and_claimed_write_cannot_supply_missing_state(self):
        self.arm.submit("W2", "a" * 40)
        left = self.checkpoint()
        self.driver.tools.context("W2")
        self.arm.submit("W2", "b" * 40)
        right = self.checkpoint()
        events = [deepcopy(r) for r in self.journal.rows if r["kind"] == "action"]
        context, submit = events[1], events[2]
        p = context["payload"]
        for field in ("before", "after", "port_before", "port_after"):
            p[field] = self.initial
        (self.root / left["view"]["path"]).unlink()
        source = self.source([context])
        self.assertNotIn(left["plan_sha256"], source.plan_observations)
        self.assertNotIn("a" * 40, source.candidate_oids())
        self.assertTrue(any("conflicting source" in error for error in self.issues))
        self.assertIsNotNone(source.lookup(self.initial))

        # The following bytes still authenticate their own submission. Without
        # the actual port a request with a copied matching identity cannot
        # prove which other rows the operation left alone.
        for field in ("port_method", "port_input", "port_return", "work_started"):
            del submit["payload"][field]
        source = self.source([submit])
        self.assertIn(right["plan_sha256"], source.plan_heads)
        self.assertNotIn(left["plan_sha256"], source.plan_observations)
