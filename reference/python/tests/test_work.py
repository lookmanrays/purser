import hashlib
import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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

    def test_frozen_leaf_outcome_and_persistent_human_root(self):
        self.child("W1")
        self.coverage()
        self.send(self.criterion("W1", "O-prefs"), OWNER)
        result = self.submit("W1", "prefs")
        self.assertEqual(self.close("W1", allowed=False)["code"], "closure_blocked")
        self.run_verification("O-prefs", result, "prefs")
        self.assertEqual(self.view().basis("W1"), TRACE["outcome_vectors"][0]["basis"])
        self.assertEqual(self.view().outcome("W1"), TRACE["outcome_vectors"][0]["digest"])
        self.close("W1")
        retired = self.send({"op": "work.create", "id": "W-late", "parent": "W1", "title": "New scope"},
                            role("executor", assignment="AS-prefs"), allow=True)
        self.assertEqual(retired["code"], "forbidden")
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
        algorithm = subprocess.check_output(["git", "-C", str(git), "rev-parse", "--show-object-format"], text=True).strip()
        git_id = {"kind": "git.commit", "algorithm": algorithm, "digest": oid}
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

    def test_historical_late_evidence_conflicts_and_explicit_recheck(self):
        self.child("W2")
        self.send(self.criterion("W2", "O-retries"), OWNER)
        original = self.submit("W2", "A", TRACE["subjects"]["A"], "executor:alpha")
        self.send({"op": "run.open", "id": "V-A", "obligation": "O-retries", "result": original})
        newer = self.submit("W2", "B", TRACE["subjects"]["B"], "executor:beta")
        after = self.view()
        self.assertEqual((after.fields("W2")["generation"], after.fields("AS-A")["state"]), (2, "superseded"))
        old_actor = role("executor", "executor:alpha", "AS-A")
        denied = self.send({"op": "result.submit", "id": "R-late", "assignment": "AS-A",
                            "candidate": TRACE["subjects"]["A"]}, old_actor, allow=True)
        self.assertEqual(denied["code"], "forbidden")
        self.observe("V-A", "Obs-A", TRACE["subjects"]["A"])
        self.evidence("V-A", "Obs-A", "E-A", "pass")
        self.assertIn("E-A", self.view().records)
        self.assertEqual(self.view().qualification("O-retries")[0], "open")
        run, obs, _ = self.run_verification("O-retries", newer, "B", TRACE["subjects"]["B"], "fail")
        self.assertEqual(self.view().qualification("O-retries")[0], "failed")
        self.evidence(run, obs, "E-B-favorable", "pass")
        self.assertEqual(self.view().qualification("O-retries")[0], "conflict")
        self.assertEqual(self.close("W2", allowed=False)["code"], "closure_blocked")
        run2, obs2, _ = self.run_verification("O-retries", newer, "B2", TRACE["subjects"]["B"])
        self.close("W2")
        self.evidence(run, obs, "E-B-still-old", "inconclusive")
        self.assertTrue(self.view().closed("W2"))
        self.evidence(run2, obs2, "E-B2-negative", "inconclusive")
        self.assertFalse(self.view().closed("W2"))
        self.assertEqual(self.view().fields("W2")["state"], "submitted")

    def test_waiver_is_principal_bound_to_exact_result_and_criterion(self):
        self.child("W1")
        self.send(self.criterion("W1", "O-missing"), OWNER)
        result = self.submit("W1", "first")
        waive = {"op": "obligation.waive", "id": "X-waive", "obligation": "O-missing",
                 "version": 1, "result": result, "reason": "Owner inspected the excluded behavior."}
        self.assertEqual(self.send(waive, self.planner, allow=True)["code"], "forbidden")
        self.assertEqual(self.send(dict(waive, version=2), OWNER, allow=True)["code"], "invalid_transition")
        self.send(waive, OWNER)
        self.assertEqual(self.view().qualification("O-missing")[0], "waived")
        self.close("W1")
        self.submit("W1", "next")
        self.assertEqual(self.view().qualification("O-missing")[0], "open")
        self.assertFalse(self.view().closed("W1"))
        self.assertEqual(self.send(dict(waive, id="X-old"), OWNER, allow=True)["code"], "invalid_transition")
        self.assertIn("X-waive", self.view().records)

    def test_immutable_verifier_enrollment_and_separate_producer_scope(self):
        op = self.criterion("W-root", "O-independent")
        proof = {"work": "W-root", "observer": "observer:test", "definition": DEFINITION, "environment": ENV}
        trusted = role("verifier", assignment=op["id"], criterion=proof)
        for actor in (self.planner, role("executor", assignment="AS-test"), role("verifier", assignment=op["id"]),
                      role("verifier", "verifier:other", op["id"], criterion=proof),
                      role("verifier", assignment="O-other", criterion=proof),
                      role("verifier", assignment=op["id"], criterion=dict(proof, environment=b"other")),
                      role("verifier", assignment=op["id"], criterion=dict(proof, work="W-other"))):
            self.assertEqual(self.send(op, actor, allow=True)["code"], "forbidden")
        self.send(op, trusted)
        self.assertEqual(self.send(dict(op, version=2), trusted, allow=True)["code"], "forbidden")
        self.assertEqual(self.view().fields("O-independent")["version"], 1)
        result = self.submit("W-root", "one", producer="observer:test")
        opened = {"op": "run.open", "id": "V-self", "obligation": op["id"], "result": result}
        self.assertEqual(self.send(opened, self.planner, allow=True)["code"], "forbidden")
        self.send(dict(op, version=2, observer="observer:independent", verifier="observer:test"), OWNER)
        self.assertEqual(self.send(opened, self.planner, allow=True)["code"], "forbidden")
        self.assertNotIn("V-self", self.view().records)

    def test_frontier_from_restart_and_assignment_attenuation(self):
        self.assertIn({"action": "assign", "subject": "W-root"}, self.view().answers()["q10"])
        self.send({"op": "work.assign", "id": "AS-first", "work": "W-root", "executor": "executor:alpha"})
        first = role("executor", "executor:alpha", "AS-first")
        self.assertIn({"action": "execute", "subject": "W-root"},
                      WorkView(self.store.replay()["projection"]).answers()["q10"])
        self.send({"op": "work.create", "id": "W-child", "parent": "W-root", "title": "Child"}, first)
        foreign = self.send({"op": "work.create", "id": "W-grand", "parent": "W-child", "title": "Grandchild"},
                            first, allow=True)
        self.assertEqual(foreign["code"], "forbidden")
        self.send({"op": "result.submit", "id": "R-first", "assignment": "AS-first", "candidate": self.candidate},
                  first)
        self.assertNotIn({"action": "execute", "subject": "W-root"}, self.view().answers()["q10"])
        self.send({"op": "work.assign", "id": "AS-second", "work": "W-root", "executor": "executor:beta"})
        self.assertIn({"action": "execute", "subject": "W-root"}, self.view().answers()["q10"])
        self.assertEqual(self.send({"op": "result.submit", "id": "R-other", "assignment": "AS-second",
                                    "candidate": self.candidate}, first, allow=True)["code"], "forbidden")
        unactivated = Store(self.directory / "proposed.db")
        unactivated.initialize()
        admit(unactivated, OWNER, WIRE["proposals"][0], clock=lambda: TIMES[0])
        self.assertEqual(WorkView(unactivated.snapshot()["projection"]).answers()["q10"], [])

    def test_all_any_k_coverage_witness_order_sets_and_unrelated_siblings(self):
        self.child("W-parent")
        for key in ("W-Z", "W-B", "W-A"):
            self.child(key, "W-parent")
        self.coverage("W-parent", "any")
        self.assertEqual(self.close("W-parent", allowed=False)["code"], "closure_blocked")
        self.assertEqual(self.close("W-Z", allowed=False)["code"], "closure_blocked")
        for work, label in (("W-B", "Beta"), ("W-A", "Alpha")):
            oid = "O-" + label
            self.send(self.criterion(work, oid), OWNER)
            result = self.submit(work, label)
            self.run_verification(oid, result, label)
            self.close(work)
        one = self.view().basis("W-parent")
        self.assertEqual([v["work"] for v in one["witnesses"]], ["W-A"])
        old = self.base
        self.child("W-unrelated")
        self.close("W-parent", base=old)
        self.coverage("W-parent", "k_of_n", 2)
        self.assertFalse(self.view().closed("W-parent"))
        basis = self.view().basis("W-parent")
        self.assertEqual([v["work"] for v in basis["witnesses"]], ["W-A", "W-B"])
        self.assertEqual(basis["children"], ["W-A", "W-B", "W-Z"])
        self.close("W-parent")
        for invalid in (0, True, 4):
            receipt = self.send({"op": "work.coverage", "id": "W-parent", "kind": "k_of_n", "k": invalid},
                                allow=True)
            self.assertEqual(receipt["code"], "invalid_op")
        self.coverage("W-parent")
        self.assertEqual(self.view().reasons("W-parent"), [{"code": "missing_coverage", "refs": ["W-Z"]}])
        self.assertTrue(self.view().closed("W-A"))
        self.assertTrue(self.view().closed("W-B"))

    def test_new_children_criteria_and_witness_sets_stale_closure(self):
        self.child("W-A")
        self.coverage()
        self.send(self.criterion("W-A", "O-first"), OWNER)
        result = self.submit("W-A", "one")
        run, observation, _ = self.run_verification("O-first", result, "one")
        old = self.base
        self.observe(run, "Obs-unused")
        self.close("W-A", base=old)
        old = self.base
        expected = self.view().outcome("W-root")
        self.child("W-added")
        held = self.send({"op": "work.accept", "id": "W-root", "outcome": expected}, OWNER, old, allow=True)
        self.assertEqual(held["code"], "stale_base")
        self.assertIn("W-added", [item["key"] for item in held["details"]["conflicts"]])
        old = self.base
        self.send(self.criterion("W-A", "O-added"), OWNER)
        stale = self.close("W-A", base=old, allowed=False)
        self.assertEqual(stale["code"], "stale_base")
        self.assertIn("O-added", [item["key"] for item in stale["details"]["conflicts"]])
        old = self.base
        self.evidence(run, observation, "E-negative", "fail")
        stale = self.close("W-A", base=old, allowed=False)
        self.assertIn("E-negative", [item["key"] for item in stale["details"]["conflicts"]])
        self.assertFalse(self.view().closed("W-A"))

    def test_rejected_batch_retains_no_tentative_work_and_old_queries_are_pure(self):
        old = self.base
        ops = [{"op": "work.create", "id": "W-new", "parent": "W-root", "title": "First"},
               {"op": "work.assign", "id": "AS-missing", "work": "W-does-not-exist", "executor": "executor:x"}]
        rejected = self.send(ops, allow=True)
        self.assertEqual(rejected["code"], "not_found")
        self.assertNotIn("W-new", self.view().records)
        self.assertEqual(self.base, old + 1)
        before = self.store.snapshot(revision=old)
        self.assertEqual(WorkView(before["projection"]).answers(), self.view().answers())

    def test_simultaneous_conflicting_verdict_and_close_observe_one_order(self):
        self.child("W1")
        self.send(self.criterion("W1", "O-first"), OWNER)
        result = self.submit("W1", "one")
        run, obs, _ = self.run_verification("O-first", result, "one")
        at = self.base
        barrier = Barrier(2)

        def send(name, action, actor):
            proposal = {"v": 1, "proposal_id": name, "commitment": C, "base_revision": at,
                        "ops": [action], "justification": []}
            barrier.wait(timeout=5)
            return admit(Store(self.store.path), actor, proposal, clock=lambda: TIMES[2])

        with ThreadPoolExecutor(max_workers=2) as pool:
            close = pool.submit(send, "race-close", {"op": "work.close", "id": "W1"}, self.closer)
            fail = pool.submit(send, "race-fail", {"op": "evidence.record", "id": "E-conflicting", "run": run,
                               "observation": obs, "verdict": "fail"}, role("verifier", assignment=run))
            first, negative = close.result(timeout=12), fail.result(timeout=12)
        self.assertEqual(negative["disposition"], "admitted")
        if first["revision"] < negative["revision"]:
            self.assertEqual(first["disposition"], "admitted")
            self.assertTrue(WorkView(self.store.snapshot(revision=first["revision"])["projection"]).closed("W1"))
        else:
            self.assertEqual(first["code"], "stale_base")
        recovered = WorkView(self.store.replay()["projection"])
        self.assertFalse(recovered.closed("W1"))
        self.assertEqual(recovered.qualification("O-first")[0], "conflict")

    def test_private_executor_retry_is_bound_to_historical_assignment(self):
        self.child("W2")
        for assignment, work in (("AS-root", "W-root"), ("AS-sibling", "W2")):
            self.send({"op": "work.assign", "id": assignment, "work": work, "executor": "executor:test"})
        original = {"v": 1, "proposal_id": "p-private-create", "commitment": C, "base_revision": self.base,
                    "ops": [{"op": "work.create", "id": "W-allowed", "parent": "W-root", "title": "Child"}],
                    "justification": []}
        binding = role("executor", assignment="AS-root")
        accepted = admit(self.store, binding, original, clock=lambda: TIMES[2])
        self.base = accepted["revision"]
        self.send({"op": "work.assign", "id": "AS-new", "work": "W-root", "executor": "executor:another"})
        self.assertEqual(admit(self.store, binding, original), accepted)
        with self.assertRaises(KernelError) as err:
            admit(self.store, role("executor", assignment="AS-sibling"), original)
        self.assertEqual(err.exception.code, "unauthenticated")
