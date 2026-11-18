import tempfile
import unittest
import zipfile
from pathlib import Path

from experiments.e1 import scorer
from experiments.e1.artifacts import Artifacts, Candidates, START
from experiments.e1.arms import Plan
from experiments.e1.executor import implement, inject
from experiments.e1.purser_arm import Treatment
from experiments.e1.run import Driver, Journal, operational_definitions
from experiments.e1.suite import schedule


class ExperimentComponents(unittest.TestCase):
    def test_seed_schedule_and_separate_control(self):
        plan = list(schedule())
        self.assertEqual(len(plan), 63)
        self.assertEqual(len(set(plan)), 63)
        self.assertEqual([arm for _, _, _, arm in plan[:3]], ["loop", "purser", "loop_equal_verifier"])
        self.assertEqual([arm for _, _, _, arm in plan[3:6]], ["purser", "loop_equal_verifier", "loop"])
        self.assertEqual([(t, seed) for t, seed, _, _ in plan[-3:]], [("clean", 1001)] * 3)

    def test_independent_queue_measurement_reaches_changed_and_faulty_git_bytes(self):
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(Path(tmp)/"unit.zip", "w") as z:
            c = Candidates(Path(tmp), Artifacts(z))
            self.assertEqual(c.head, START)
            for w in ("W1", "W2", "W3"):
                implement(c.work, w)
            oid = c.commit("Unit fixture only")
            with c.export(oid) as ex:
                good = scorer.measure(c.archive, oid, Path(ex), 912345, 4)
            self.assertTrue(all(r["verdict"] == "pass" for r in good.values()))
            inject(c.work, ("skipped_retry_check","W2","W2/retry"), 912345)
            oid = c.commit("Unit fault only")
            with c.export(oid) as ex:
                bad = scorer.measure(c.archive, oid, Path(ex), 912345, 4)
            self.assertEqual({k for k, r in bad.items() if r["verdict"] == "fail"},
                             {"W2/retry","queue/composition"})

    def test_public_action_counts_against_hand_worked_nonstudy_control(self):
        for arm, expected in [("loop", 24), ("loop_equal_verifier", 42)]:
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)
                with zipfile.ZipFile(path/"unit.zip", "w") as z:
                    assets = Artifacts(z)
                    candidates = Candidates(path, assets)
                    journal = Journal(arm)
                    driver = Driver(candidates, Plan(path), journal, assets, 912345, "clean")
                    self.assertEqual(driver.execute(), "done")
                    self.assertEqual(expected, journal.steps)
                    self.assertEqual(sum(r["kind"] == "action" for r in journal.rows), expected)

    def test_kernel_diagnosis_is_read_from_real_scoped_public_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm = Treatment(Path(tmp), *operational_definitions())
            label = "W1/preferences"
            arm.submit("W1", START)
            arm.opening("W1", label)
            observation = {"criterion": label, "candidate": START, "observed": ["synthetic unit"], "verdict": "fail"}
            arm.observe("W1", label, observation)
            arm.verify("W1", label, observation)
            public = arm.decide("W1", {"missing": ["misleading self-review"]})
            self.assertEqual(public["receipt"]["code"], "closure_blocked")
            self.assertEqual(public["diagnosis"], [label])
            self.assertEqual(arm.states()["W1"], "submitted")

    def test_failed_parent_closure_survives_later_correct_publication(self):
        j = Journal("loop")
        j.event("fault", {"target": "W2/retry", "candidate": "bad"})
        for oid in ("bad","good"):
            j.event("public_state", {"candidate": oid, "subject":"W-notify","from":"open","to":"closed","diagnosis":[]})
            for label in scorer.LABELS:
                j.event("oracle", {"candidate":oid,"criterion":label,
                                   "verdict":"fail" if (oid,label)==("bad","W2/retry") else "pass"})
        s = scorer.score(j.rows)
        self.assertTrue(s["false_parent_closure"] and s["correct_parent_closure"])
        self.assertFalse(s["missing_named"] or s["caught"])
