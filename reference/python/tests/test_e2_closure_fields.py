"""Read compound transitions and disconnected observations from physical E2 plan versions."""

import tempfile
import unittest
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from experiments.e1.artifacts import Artifacts, sha
from experiments.e1.recovery import FileArchive
from experiments.e1.report import Objects
from experiments.e2 import outcomes, plan_versions, policy
from experiments.e2.public_history import Participant
from experiments.e2.public_sources import Originals


class ClosureFields(unittest.TestCase):
    linear, revised, other = (str(i) * 40 for i in (1, 2, 3))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.assets = Artifacts(FileArchive(self.root))
        self.plan = policy.PlanArm(self.root)
        self.initial = self.checkpoint()
        self.before = self.publish(W1=("closed", self.linear), W3=("closed", self.linear),
                                   W2=("submitted", self.linear))
        self.verdicts = {(self.linear, label, 1): "pass" for label in ("W1/preferences", "W2/retry", "W3/delivery")}
        self.verdicts.update({(self.linear, "W2/retry", 2): "fail", (self.revised, "W2/retry", 2): "pass"})

    def checkpoint(self):
        return {"plan_sha256": sha(self.plan.file.read_bytes()), "view": self.assets.put(self.plan.read(), "json")}

    def publish(self, **states):
        rows = self.plan.read()
        for work, (state, oid) in states.items():
            rows[work].update(state=state, candidate=oid)
        self.plan.publish(rows)
        return self.checkpoint()

    def assess(self, receipt, views, *, prefix=None, endpoint=True):
        head = self.plan.file.read_bytes()
        refs = plan_versions.retain(self.root, self.assets, head)
        trace = {"plan": self.plan.read(), "plan_versions": refs[:prefix]}
        platform = {"treatment_trace": self.assets.put(trace, "json"), "initial_public": self.initial,
                    "final_public": self.checkpoint()}
        issues = []
        source = Originals("loop", platform, Objects(self.assets.archive).item, issues.append)
        self.assertEqual(source.complete, prefix is None)
        self.assertEqual(bool(issues), prefix is not None)
        for view in [self.before, receipt, *views]:
            self.assertIsNotNone(source.lookup(view))
        # Stipulate the independently qualified receipt and project duplicate records
        # of the supplied public heads. Assess only the publication/criterion join.
        fault = {"kind": "fault", "seq": 1, "payload": {"before": self.before, "after": receipt,
                 "candidate": self.linear, "publicly_delivered": True}}
        journal = [fault] + [{"kind": "public_state", "seq": i, "payload": {"checkpoint": ck}}
                             for i, ck in enumerate(views, 2)]
        history = SimpleNamespace(arm="loop", journal=journal, source=source, public_complete=source.complete,
                                  checkpoint=source.lookup)
        history.public_states_after = partial(Participant.public_states_after, history)
        return outcomes.assess(history, "primary", self.verdicts, delivery=True, qualification=True, submitted=True,
                               unchanged={"W1": True, "W3": True}, final_oid=self.revised, endpoint_known=endpoint,
                               action_sequence_complete=True)

    def test_each_subject_at_one_publication_owns_its_transition_and_candidate(self):
        receipt = self.publish(W1=("open", self.linear), W3=("open", self.linear))
        two_closes = self.publish(W2=("closed", self.revised), **{"W-notify": ("done", self.linear)})
        replacement = self.publish(W2=("done", self.revised), **{"W-notify": ("closed", self.revised)})
        repeated_views = [receipt, receipt, two_closes, two_closes, replacement, replacement]
        facts = self.assess(receipt, repeated_views)
        self.assertFalse(facts["unaffected_both_retained"])
        self.assertEqual(facts["unaffected_reopened"], 2)
        self.assertEqual(facts["reopened_subjects"], ["W1", "W3"])
        self.assertEqual([(r["subject"], r["publication"], r["at_delivery"]) for r in facts["reopen_observations"]],
                         [(w, receipt["plan_sha256"], True) for w in ("W1", "W3")])
        self.assertTrue(facts["affected_invalid_closure"])
        self.assertTrue(facts["new_bad_closure"])
        self.assertFalse(facts["stale_at_delivery"])
        self.assertEqual([(r["subject"], r["publication"], r["candidate"], r["revised_pass"])
                          for r in facts["closure_observations"]],
                         [("W2", two_closes["plan_sha256"], self.revised, True),
                          ("W-notify", two_closes["plan_sha256"], self.linear, False),
                          ("W-notify", replacement["plan_sha256"], self.revised, True)])
        stopped = self.assess(receipt, repeated_views, endpoint=False)
        self.assertIsNone(stopped["unaffected_reopened"])
        self.assertEqual(stopped["observed_reopened"], 2)
        self.assertTrue(stopped["affected_invalid_closure"])

    def test_missing_predecessor_proves_interruption_and_subsequent_linked_edges(self):
        receipt = self.publish()
        self.publish(W3=("open", None))
        open_view = self.publish(W2=("submitted", self.revised))
        reclosed = self.publish(W3=("closed", self.linear))
        reopened = self.publish(W3=("open", None))
        facts = self.assess(receipt, [open_view, open_view, reclosed, reopened, reopened], prefix=4)
        self.assertFalse(facts["unaffected_both_retained"])
        self.assertIsNone(facts["unaffected_reopened"])
        self.assertEqual(facts["observed_reopened"], 1)
        self.assertEqual(facts["reopened_subjects"], ["W3"])
        self.assertEqual([r["publication"] for r in facts["reopen_observations"]], [reopened["plan_sha256"]])
        self.assertIsNone(facts["affected_invalid_closure"])

    def test_failing_closed_sibling_and_ungraded_parent_have_distinct_effects(self):
        receipt = self.publish()
        self.verdicts[self.other, "W1/preferences", 1] = "fail"
        changed = self.publish(W1=("closed", self.other), **{"W-notify": ("closed", self.other)})
        facts = self.assess(receipt, [changed])
        self.assertFalse(facts["unaffected_both_retained"])
        self.assertEqual(facts["unaffected_reopened"], 0)
        self.assertEqual(facts["reopened_subjects"], [])
        self.assertIsNone(facts["affected_invalid_closure"])
        declaration, = facts["closure_observations"]
        self.assertEqual(declaration["subject"], "W-notify")
        self.assertIsNone(declaration["revised_pass"])
