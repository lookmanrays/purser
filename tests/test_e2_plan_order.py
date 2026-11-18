"""Local adjacency, physical publication order and the measured latest submission."""

import tempfile
import unittest
from pathlib import Path

from experiments.e1.artifacts import Artifacts
from experiments.e1.executor import PARENT
from experiments.e1.recovery import FileArchive
from experiments.e1.report import Objects
from experiments.e1.run import Journal
from experiments.e2 import evidence, policy
from experiments.e2.driver import Driver
from experiments.e2.plan_order import PlanOrder
from experiments.e2.public_history import read_participant


class PublicationChronology(unittest.TestCase):
    def test_nonoverlap_orders_detached_native_submissions_without_extending_state_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = Artifacts(FileArchive(root))
            plan = policy.PlanArm(root)
            journal = Journal("loop")
            Driver(None, plan, journal, assets, 2001, "clean")
            first_oid, last_oid = "a" * 40, "b" * 40
            plan.submit("W1", first_oid)
            plan.assign_again("W2")
            plan.submit(PARENT, last_oid)
            issued = {e["payload"]["attempt"]: e["payload"] for e in journal.rows if e["kind"] == "action"}
            first, gap, last = [issued[i]["port_after"] for i in (1, 2, 3)]
            (root / gap["view"]["path"]).unlink()
            rows = [e for e in journal.rows if e["payload"].get("attempt") != 2
                    and e["payload"].get("source_action") != 2]
            objects = Objects(assets.archive)
            participant = read_participant("loop", rows, journal.platform, objects.item, lambda _: None,
                                           objects=objects, seed=2001, track="clean")
            self.assertIsNone(participant.source.plan_edge(last))
            self.assertEqual(participant.source.through(first), first)
            self.assertEqual(participant.source.compare(first, last), -1)
            facts = evidence.score(participant, "clean", {}, journal.platform, release=None,
                                   qualification=None, submitted=None, endpoint_known=False, step_boundaries=False,
                                   final_subject=False, known_candidates={first_oid}, available_candidates={first_oid})
            self.assertEqual((facts["last_observed_subject"], facts["last_observed_attempt"],
                              facts["last_observed_candidate"]), (PARENT, 3, last_oid))
            self.assertIsNone(facts["last_observed_pass"])
            self.assertIsNone(facts["final_pass"])

    def test_incomparable_siblings_and_later_witness_have_different_proof(self):
        stamps = {"a": {"number": 3, "previous": "root"},
                  "b": {"number": 3, "previous": "root"},
                  "c": {"number": 5, "previous": "gap"}}
        order = PlanOrder(stamps, {"a": (1, 5), "b": (2, 5), "c": (6, 10)}, self.fail)
        self.assertIsNone(order.compare("a", "b"))
        self.assertEqual(order.compare("a", "c"), -1)
        self.assertEqual(order.compare("b", "c"), -1)
        issues = []
        stamps["b"] = {"number": 4, "previous": "a"}
        conflict = PlanOrder(stamps, {"a": (3, 4), "b": (1, 2), "c": (6, 10)}, issues.append)
        self.assertTrue(issues)
        self.assertIsNone(conflict.compare("a", "b"))
        self.assertEqual(conflict.compare("a", "c"), -1)
        self.assertEqual(conflict.compare("b", "c"), -1)
