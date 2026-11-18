import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from purser.kernel.artifacts import Artifacts
from purser.kernel.gate import admit
from purser.kernel.store import Store
from purser.kernel.wire import KernelError, digest
from purser.kernel.workview import WorkView
from tests.test_gate import OWNER, TIMES, WIRE

TRACE = json.loads((Path(__file__).resolve().parents[1] / "docs/contracts/lifecycle-v1.json").read_bytes())
C = "C-notify"
DEFINITION = b"Independent notification scheduling check v1\n"
ENV = b"Python 3.11; local test fixture; no remote actions\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def role(name, actor=None, assignment=None, **extra):
    return {**OWNER, "actor_id": actor or f"{name}:test", "role": name,
            "commitment": C, "assignment": assignment, **extra}


class WorkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.store = Store(self.directory / "project.db")
        self.store.initialize()
        for i, proposal in enumerate(WIRE["proposals"]):
            admit(self.store, OWNER, proposal, clock=lambda i=i: TIMES[i])
        self.n = 0
        self.planner = role("planner")
        self.closer = role("closure")
        self.candidate = TRACE["subjects"]["O"]
        self.base = self.store.snapshot()["revision"]

    def send(self, op, binding=None, base=None, allow=False):
        self.n += 1
        p = {"v": 1, "proposal_id": f"p-{self.n}", "commitment": C,
             "base_revision": self.base if base is None else base, "ops": op if type(op) is list else [op],
             "justification": []}
        receipt = admit(self.store, binding or self.planner, p, clock=lambda: TIMES[2])
        self.base = receipt["revision"]
        if not allow:
            self.assertEqual(receipt["disposition"], "admitted", (op, receipt))
        return receipt

    def view(self):
        return WorkView(self.store.snapshot()["projection"])

    def child(self, key, parent="W-root"):
        self.send({"op": "work.create", "id": key, "parent": parent, "title": f"Implement {key}"})

    def coverage(self, key="W-root", kind="all", k=None):
        return self.send({"op": "work.coverage", "id": key, "kind": kind, "k": k})

    def criterion(self, work, oid, version=1, **extra):
        return {"op": "obligation.set", "id": oid, "work": work, "version": version, "title": "Queue behavior",
                "observer": "observer:test", "verifier": "verifier:test", "definition_sha256": sha(DEFINITION),
                "environment_sha256": sha(ENV), **extra}

    def submit(self, work, label, candidate=None, producer="executor:test"):
        assignment, result = "AS-" + label, "Result-" + label
        self.send({"op": "work.assign", "id": assignment, "work": work, "executor": producer})
        self.send({"op": "result.submit", "id": result, "assignment": assignment, "candidate": candidate or self.candidate},
                  role("executor", producer, assignment))
        return result

    def run_verification(self, oid, result, label, candidate=None, verdict="pass", report=b"observed"):
        run, obs, evidence = "V-" + label, "Obs-" + label, "E-" + label
        self.send({"op": "run.open", "id": run, "obligation": oid, "result": result})
        self.observe(run, obs, candidate, report)
        self.evidence(run, obs, evidence, verdict)
        return run, obs, evidence

    def observe(self, run, obs, candidate=None, report=b"observed"):
        proof = {"candidate": candidate or self.candidate, "definition": DEFINITION, "environment": ENV, "artifact": report}
        return self.send({"op": "observation.record", "id": obs, "run": run},
                         role("observer", assignment=run, witness=proof))

    def evidence(self, run, obs, key, verdict):
        return self.send({"op": "evidence.record", "id": key, "run": run, "observation": obs, "verdict": verdict},
                         role("verifier", assignment=run))

    def close(self, work, *, allowed=True, base=None):
        return self.send({"op": "work.close", "id": work}, self.closer, base, allow=not allowed)

    def test_frozen_leaf_outcome_on_real_git_bytes_and_persistent_human_root(self):
        self.child("W1")
        self.coverage()
        self.send(self.criterion("W1", "O-prefs"), OWNER)
        result = self.submit("W1", "prefs")
        self.assertEqual(self.close("W1", allowed=False)["code"], "closure_blocked")
        self.run_verification("O-prefs", result, "prefs")
        self.assertEqual(self.view().basis("W1"), TRACE["outcome_vectors"][0]["basis"])
        self.assertEqual(self.view().outcome("W1"), TRACE["outcome_vectors"][0]["digest"])
        self.close("W1")
        root = self.view().outcome("W-root")
        self.assertEqual(self.view().reasons("W-root"), [{"code": "awaiting_acceptance", "refs": ["W-root"]}])
        self.assertEqual(self.close("W-root", allowed=False)["code"], "closure_blocked")
        self.assertEqual(self.send({"op": "work.accept", "id": "W-root", "outcome": "0"*64},
                                   OWNER, allow=True)["code"], "outcome_mismatch")
        self.send({"op": "work.accept", "id": "W-root", "outcome": root}, OWNER)
        view = WorkView(Store(self.store.path).replay()["projection"])
        self.assertEqual((view.fields(C)["state"], view.fields("W-root")["accepted_outcome"]), ("satisfied", root))
        self.assertEqual(view.answers(), self.view().answers())
        self.assertTrue(view.closed("W-root"))
        self.send(self.criterion("W1", "O-prefs", version=2), OWNER)
        after = self.view()
        self.assertEqual((after.fields(C)["state"], after.fields("W-root")["state"]), ("active", "open"))
        self.assertEqual(after.fields("W1")["state"], "submitted")
        self.assertIsNone(after.fields("W-root")["accepted_outcome"])

    def test_measured_git_subject_real_artifact_bytes_and_changed_subject(self):
        git = self.directory / "fixture"
        subprocess.run(["git", "init", "-q", str(git)], check=True)
        (git / "notify.py").write_text("def retry(): return 3\n")
        subprocess.run(["git", "-C", str(git), "add", "notify.py"], check=True)
        subprocess.run(["git", "-C", str(git), "-c", "user.name=Fixture", "-c", "user.email=fixture@local",
                        "commit", "-qm", "Add retry"], check=True)
        oid = subprocess.check_output(["git", "-C", str(git), "rev-parse", "HEAD"], text=True).strip()
        git_id = {"kind": "git.commit", "algorithm": "sha1", "digest": oid}
        raw = subprocess.check_output(["git", "-C", str(git), "show", f"{oid}:notify.py"])
        self.send(self.criterion("W-root", "O-git"), OWNER)
        result = self.submit("W-root", "git", git_id)
        self.send({"op": "run.open", "id": "V-git", "obligation": "O-git", "result": result})
        op = {"op": "observation.record", "id": "Obs-git", "run": "V-git"}
        good = {"candidate": git_id, "definition": DEFINITION, "environment": ENV, "artifact": raw}
        for field, wrong in (("candidate", self.candidate), ("definition", b"changed"), ("environment", b"changed")):
            receipt = self.send(op, role("observer", assignment="V-git", witness={**good, field: wrong}), allow=True)
            self.assertEqual(receipt["code"], "witness_mismatch")
        self.send(op, role("observer", assignment="V-git", witness=good))
        self.evidence("V-git", "Obs-git", "E-git", "pass")
        artifacts = Artifacts(self.store.path)
        self.assertEqual(artifacts.read(sha(raw)), raw)
        operation = {"op": "work.accept", "id": "W-root", "outcome": self.view().outcome("W-root")}
        path = artifacts.directory / sha(raw)
        path.write_bytes(b"corrupt")
        with self.assertRaises(KernelError) as error:
            self.send(operation, OWNER)
        self.assertEqual(error.exception.code, "integrity_error")
        self.assertEqual(self.store.snapshot()["revision"], self.base)
        path.unlink()
        with self.assertRaises(KernelError) as error:
            self.send(operation, OWNER)
        self.assertEqual(error.exception.code, "unavailable")
        artifacts.put(raw)
        self.send(operation, OWNER)
