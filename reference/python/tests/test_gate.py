import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier

from purser.kernel.gate import admit
from purser.kernel.store import Store
from purser.kernel.wire import KernelError

WIRE = json.loads((Path(__file__).resolve().parents[1] / "docs/contracts/wire-v1.json").read_bytes())
CREATE, HOLD, ANSWER = WIRE["proposals"]
OWNER = WIRE["binding"]
TIMES = [datetime.fromisoformat(at) for at in WIRE["clock_by_batch"]]


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "purser.sqlite"
        self.store = Store(self.path)
        self.store.initialize()

    def submit(self, proposal, binding=OWNER, at=TIMES[0]):
        return admit(Store(self.path), binding, proposal, clock=lambda: at)

    def test_exact_original_bytes_survive_pending_and_final_retries(self):
        for i, proposal in enumerate(WIRE["proposals"]):
            result = self.submit(proposal, at=TIMES[i])
            self.assertEqual(result, WIRE["expected"]["admission_replies"][i]["stdout"]["receipt"])
            self.assertEqual(self.submit(proposal, at=TIMES[i]), result)
            self.assertEqual(self.store.replay(), self.store.snapshot())
        self.assertEqual(self.store.snapshot(), WIRE["expected"]["snapshot_at_8"])
        latest = self.submit(HOLD)
        self.assertEqual((latest["revision"], latest["disposition"]), (8, "admitted"))
        with self.assertRaises(KernelError) as failure:
            self.submit(dict(CREATE, justification=["changed"]))
        self.assertEqual(failure.exception.code, "id_conflict")
        self.assertEqual(self.store.snapshot()["revision"], 8)

    def test_derived_conflicts_and_unrelated_updates(self):
        self.submit(CREATE)
        branch = WIRE["independent_case_branches"][3]
        for proposal, expected in zip(branch["ordered_proposals"], branch["expect"]):
            received = self.submit(proposal)
            self.assertEqual({key: received[key] for key in expected}, expected)

    def test_ordered_batch_and_rejected_suffix(self):
        change = {"op": "commitment.edit", "id": CREATE["commitment"], "statement": "Second statement"}
        both = dict(CREATE, ops=[*CREATE["ops"], change])
        self.assertEqual(self.submit(both)["revision"], 3)
        before = self.store.snapshot()["projection"]["records"]
        self.assertEqual(before[0]["fields"]["statement"], change["statement"])
        proposal = WIRE["independent_case_branches"][4]["ordered_proposals"][0]
        rejected = self.submit(dict(proposal, base_revision=3))
        self.assertEqual((rejected["code"], rejected["revision"]), ("id_exists", 4))
        self.assertEqual(self.store.replay()["projection"]["records"], before)
        middle = self.submit(dict(proposal, proposal_id="p-middle", base_revision=1))
        self.assertEqual((middle["code"], middle["revision"]), ("bad_revision", 5))

    def test_reading_foreign_ids_does_not_expose_their_stale_revision(self):
        self.submit(CREATE)
        scoped = dict(OWNER, actor_id="intake:two", role="intake", commitment="C-other")
        other = dict(CREATE, proposal_id="p-other", commitment="C-other")
        for name, subject in (("p-present", CREATE["commitment"]), ("p-absent", "C-hidden")):
            edit = dict(other, proposal_id=name, ops=[{"op": "commitment.edit", "id": subject,
                                                       "statement": "Out of scope"}])
            denied = self.submit(edit, binding=scoped)
            self.assertEqual((denied["code"], denied["details"]), ("forbidden", {}))
        actor = dict(scoped, commitment=CREATE["commitment"])
        held = self.submit(HOLD, binding=actor)
        self.assertEqual(held["disposition"], "pending")
        with self.assertRaises(KernelError) as unbound:
            self.submit(HOLD, binding=scoped)
        self.assertEqual(unbound.exception.code, "unauthenticated")

    def test_matching_answer_rechecks_origin_and_current_authority(self):
        self.submit(CREATE)
        pending = self.submit(HOLD)
        answer_op = ANSWER["ops"][0]
        executor = dict(OWNER, role="executor", actor_id="agent:one", commitment=HOLD["commitment"])
        self.assertEqual(self.submit(ANSWER, executor)["code"], "forbidden")
        wrong = dict(ANSWER, proposal_id="p-wrong", ops=[dict(answer_op, request_digest="a" * 64)])
        self.assertEqual(self.submit(wrong)["code"], "digest_mismatch")
        self.assertEqual(self.submit(HOLD), pending)
        change = WIRE["independent_case_branches"][3]["ordered_proposals"][1]
        self.submit(change)
        self.assertEqual(self.submit(ANSWER)["details"]["state"], "stale")
        finalized = self.submit(HOLD)
        self.assertEqual((finalized["code"], finalized["details"]),
                         ("stale_base", {"conflicts": [{"key": HOLD["commitment"], "changed": 7}]}))
        snapshot = self.store.replay()
        self.assertEqual(snapshot["projection"]["records"][0]["fields"]["state"], "proposed")
        twice = dict(ANSWER, proposal_id="p-again", base_revision=snapshot["revision"])
        self.assertEqual(self.submit(twice)["code"], "already_decided")

    def test_deadline_is_exclusive_and_denial_is_durable(self):
        for decision, at, state in (("deny", TIMES[2], "denied"),
                                    ("approve", datetime.fromisoformat(HOLD["ops"][0]["expires_at"]), "expired")):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as d:
                s = Store(Path(d) / "independent.db")
                s.initialize()
                for p in (CREATE, HOLD):
                    admit(s, OWNER, p, clock=lambda: TIMES[0])
                answer = dict(ANSWER, ops=[dict(ANSWER["ops"][0], decision=decision)])
                self.assertEqual(admit(s, OWNER, answer, clock=lambda: at)["details"]["state"], state)
                self.assertEqual(s.replay()["projection"]["records"][0]["fields"]["state"], "proposed")
                original = admit(s, OWNER, HOLD, clock=lambda: at)
                self.assertEqual(original["code"], "approval_" + state)

    def test_concurrent_writers_decide_against_the_committed_start(self):
        self.submit(CREATE)
        def race(payloads):
            barrier = Barrier(len(payloads))
            def deliver(proposal):
                barrier.wait(timeout=5)
                return self.submit(proposal)
            with ThreadPoolExecutor(len(payloads)) as pool:
                return list(pool.map(deliver, payloads))
        edit = WIRE["independent_case_branches"][3]["ordered_proposals"][1]
        self.assertEqual(sorted(r["code"] for r in race([edit, dict(edit, proposal_id="p-peer")])),
                         ["ok", "stale_base"])
        same = dict(edit, proposal_id="p-next", base_revision=self.store.snapshot()["revision"])
        left, right = race([same, same])
        self.assertEqual(left, right)
        self.assertEqual(self.store.replay()["revision"], 7)


if __name__ == "__main__":
    unittest.main()
