import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from docs.evidence.M2.prepare_continuity import C, CHILD, PRINCIPAL, ROOT, SOURCE, Fixture, binding
from purser.driver import context_pack, tick
from purser.kernel.artifacts import Artifacts
from purser.kernel.wire import KernelError
from purser.kernel.workview import WorkView

class ForegroundDriver(unittest.TestCase):
    def setUp(self):
        parent = tempfile.TemporaryDirectory()
        self.addCleanup(parent.cleanup)
        self.fixture = Fixture(Path(parent.name) / "fixture")
        self.store = self.fixture.store
        self.candidate = {"kind": "artifact.bytes", "algorithm": "sha256",
                          "digest": Artifacts(self.fixture.path).put(b"actual controlled candidate")}
        self.planner = binding("planner:controlled", "planner")
        owner = binding(PRINCIPAL, "principal")
        send = self.fixture.submit
        send(owner, {"op": "commitment.create", "id": C, "root": ROOT, "statement": "Exercise a fresh driver"})
        hold = send(owner, {"op": "commitment.request_acceptance", "id": C,
                           "expires_at": "2026-11-19T12:00:00.000000Z"}, expected="pending")
        send(owner, {"op": "approval.answer", "id": hold["details"]["approval_id"],
                     "request_digest": hold["digest"], "decision": "approve"})
        send(self.planner, {"op": "work.create", "id": CHILD, "parent": ROOT, "title": "First child"})
        send(self.planner, {"op": "work.create", "id": "W-sibling", "parent": ROOT, "title": "Unrelated sibling"})
        self.assign("AS-first", "executor:first")

    def assign(self, identity, actor):
        return self.fixture.submit(self.planner, {"op": "work.assign", "id": identity, "work": CHILD,
                                                 "executor": actor})

    def cli(self, *args, expected=0):
        process = subprocess.run([sys.executable, "-B", "-m", "purser", "--db", str(self.fixture.path), *args],
                                 cwd=SOURCE, env={"PATH": "/usr/bin:/bin", "TZ": "UTC", "LC_ALL": "C"},
                                 capture_output=True, timeout=15)
        self.assertEqual(process.returncode, expected, process.stdout)
        self.assertFalse(process.stderr)
        return json.loads(process.stdout)

    def test_scoped_pack_restart_and_adapter_replacement(self):
        initial = self.store.snapshot()
        original = self.cli("context", "AS-first")
        self.assertEqual(original, context_pack(initial, "AS-first"))
        self.assertNotIn("W-sibling", json.dumps(original))
        self.assertEqual(original["limits"]["results_per_assignment"], 1)
        self.assertEqual(self.cli("run")["state"], "idle")
        self.assertEqual(self.cli("frontier")["actions"], WorkView(initial["projection"]).answers()["q10"])
        self.assertEqual(self.store.snapshot(), initial)
        self.assign("AS-second", "executor:replacement")
        current = self.store.snapshot()
        self.assertEqual(self.cli("context", "AS-first", expected=4)["error"]["code"], "inactive_assignment")
        self.assertEqual(self.cli("context", "AS-first", "--revision", str(initial["revision"])), original)
        called = []
        def adapter(pack):
            called.append((pack["assignment"]["id"], pack["work"]["generation"]))
            return self.candidate
        self.assertEqual(tick(self.store, {"executor:first": adapter})["state"], "idle")
        result = tick(self.store, {"executor:replacement": adapter})
        self.assertEqual((result["action"]["subject"], result["receipt"]["disposition"]), (CHILD, "admitted"))
        second = tick(self.store, {"executor:replacement": adapter})
        self.assertEqual(second["state"], "idle")
        self.assertEqual(called, [("AS-second", 2)])
        self.assertEqual(WorkView(self.store.replay()["projection"]).fields(CHILD)["candidate"], self.candidate)
        self.assertEqual(self.store.snapshot(revision=current["revision"]), current)

    def test_reassigned_during_callback_cannot_submit_for_its_successor(self):
        def slow_adapter(pack):
            self.assign("AS-new", "executor:new")
            return self.candidate
        result = tick(self.store, {"executor:first": slow_adapter})
        self.assertEqual(result["receipt"]["disposition"], "rejected")
        self.assertIsNone(WorkView(self.store.snapshot()["projection"]).fields(CHILD)["candidate"])
        with self.assertRaises(KernelError):
            context_pack(self.store.snapshot(), "AS-first")


if __name__ == "__main__":
    unittest.main()
