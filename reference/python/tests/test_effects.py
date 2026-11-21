"""M4-A through real WAL admission, independent OS processes and cold model-free reads."""
import json
import multiprocessing
import subprocess
import sys
import unittest
from datetime import datetime
from itertools import count
from pathlib import Path

from purser.kernel.artifacts import Artifacts
from purser.kernel.gate import admit, authorize_attempt, redelivery
from purser.kernel.store import Store
from purser.kernel.wire import KernelError, digest
from tests import test_decisions as decisions
from tests import test_work as work

M0 = work.TRACE["immutable_effect"]
INTENT = M0["intent"]
INSTANCE = "7" * 64
AT = "2026-11-18T13:59:59.000000Z"
EXPIRY = "2026-11-18T14:00:00.000000Z"
FILES = ["notify/retry.py"]
SLOT = {"op": "effect.require", "id": INTENT["effect"], "work": "W2", "slot": "publish",
        "repository": INTENT["repository"], "repository_instance": INSTANCE, "resource": INTENT["resource"],
        "paths": FILES}
GRANT = {"op": "grant.request", "id": INTENT["grant"], "work": "W2", "slot": "publish",
         "repository": INTENT["repository"], "resource": INTENT["resource"], "paths": FILES,
         "assignment": "AS-B", "generation": 2, "grantee": "executor:beta", "expires_at": EXPIRY}
SOURCE = Path(__file__).resolve().parents[1]


def prepare(intent=INTENT, paths=FILES, instance=INSTANCE):
    return {"intent_digest": digest("effect", intent), "repository_instance": instance, "changed_paths": paths}


def report(attempt="T1", state="confirmed_happened", intent=INTENT, **changed):
    kind, reason, matches = {"confirmed_happened": (intent["target"]["digest"], "protected_receipt_verified", 1),
                            "confirmed_absent": (None, "quiescent_no_application", 0),
                            "pending": (None, "writer_active", None)}[state]
    return {**{k: intent[k] for k in ("repository", "resource")}, "attempt": attempt, "state": state,
            "reason": reason, "repository_instance": INSTANCE, "intent_digest": digest("effect", intent),
            "target": intent["target"]["digest"], "expected_old": intent["expected_old"]["digest"],
            "branch_tip": intent["target" if state == "confirmed_happened" else "expected_old"]["digest"],
            "receipt_ref": "refs/purser/receipts/" + digest("effect", intent), "receipt_target": kind,
            "writer_quiescent": state != "pending", "history_intact": state != "pending",
            "matching_transitions": matches, **changed}


def cutoff_role(intent=INTENT, **overrides):
    return work.role("broker", "broker:git", intent["effect"], git_preparation={**prepare(intent), **overrides})


def frame(key, base, op, scope=work.C):
    return {"v": 1, "proposal_id": key, "commitment": scope, "base_revision": base, "ops": [op], "justification": []}


def race_writer(path, binding, proposal, acquired, resume, started, results):
    started.set()

    def clock():
        if acquired is not None:
            acquired.set()  # Store owns BEGIN IMMEDIATE at this exact point.
            if not resume.wait(15):
                raise RuntimeError("second writer failed to start")
        return datetime.fromisoformat(AT)

    if proposal["ops"][0]["op"] == "effect.authorize":
        value = authorize_attempt(Store(path), binding, proposal, clock=clock)
    else:
        value = (admit(Store(path), binding, proposal, clock=clock), False)
    results.put((proposal["proposal_id"], value))


class EffectAuthority(unittest.TestCase):
    def setUp(self):
        self.h = work.WorkTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.store = self.h.store
        self.serial = count(1)
        self.b = work.role("executor", "executor:beta", "AS-B")

    def send(self, op, binding, *, base=None, at=AT, code="ok", scope=work.C):
        request = frame("m4-" + str(next(self.serial)), self.h.base if base is None else base, op, scope)
        receipt = admit(Store(self.store.path), binding, request, clock=lambda: datetime.fromisoformat(at))
        self.h.base = receipt["revision"]
        self.assertEqual(receipt["code"], code, (op, receipt))
        return request, receipt

    def declare(self, *, slot=SLOT):
        self.h.child("W2")
        self.h.send({"op": "work.assign", "id": "AS-A", "work": "W2", "executor": "executor:alpha"})
        self.h.send(slot)

    def issue(self, grant=GRANT, actor=None):
        p, held = self.send(grant, actor or self.h.planner, code="needs_principal")
        answer = {"op": "approval.answer", "id": held["details"]["approval_id"],
                  "request_digest": held["digest"], "decision": "approve"}
        response, receipt = self.send(answer, work.OWNER)
        self.assertEqual(receipt["details"]["state"], "approved")
        self.assertEqual(redelivery(Store(self.store.path), work.OWNER, response), receipt)
        self.assertEqual(redelivery(Store(self.store.path), actor or self.h.planner, p)["disposition"], "admitted")
        return p, response

    def ready(self, *, slot=SLOT, grant=GRANT):
        self.declare(slot=slot)
        self.issue(grant)
        self.h.submit("W2", "B", INTENT["target"], "executor:beta")

    def bind(self, intent=INTENT, *, binding=None, code="ok", proof=None):
        actor = binding if binding is not None else {**self.b, "git_preparation": proof or prepare(intent)}
        return self.send({"op": "effect.request", "id": intent["effect"], "intent": intent}, actor, code=code)

    def authorize(self, attempt="T1", *, intent=INTENT, at=AT, binding=None, code="ok"):
        proposal = frame("dispatch-" + str(next(self.serial)), self.h.base,
                         {"op": "effect.authorize", "id": attempt, "effect": intent["effect"]})
        actor = binding or cutoff_role(intent)
        receipt, fresh = authorize_attempt(self.store, actor, proposal, clock=lambda: datetime.fromisoformat(at))
        self.h.base = receipt["revision"]
        self.assertEqual((receipt["code"], fresh), (code, code == "ok"), receipt)
        return proposal, receipt

    def observe(self, observation, attempt="T1", state="confirmed_happened", *, intent=INTENT, code="ok", **changes):
        proof = report(attempt, state, intent, **changes)
        actor = work.role("observer", "observer:git", attempt, git_observation=proof)
        return self.send({"op": "effect.reconcile", "id": observation, "attempt": attempt, "effect": intent["effect"]},
                         actor, code=code)

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-B", "-m", "purser", "--db", str(self.store.path), *args],
                                cwd=SOURCE, capture_output=True, check=True, timeout=20)
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def test_exact_m0_intent_and_all_five_outcomes_through_cold_cli(self):
        h = self.h
        h.child("W-notify")
        h.child("W2", "W-notify")
        h.send([decisions.premise("A1"), decisions.decision("D-original"), decisions.link("W2", "D-original")])
        h.send(SLOT)
        h.send({"op": "work.assign", "id": "AS-A", "work": "W2", "executor": "executor:alpha"})
        self.issue()
        h.send(h.criterion("W2", "O-retries"), work.OWNER)
        h.send({"op": "result.submit", "id": "Result-A", "assignment": "AS-A",
                "candidate": work.TRACE["subjects"]["A"]}, work.role("executor", "executor:alpha", "AS-A"))
        h.send(decisions.observation(), decisions.observer())
        h.child("W1", "W-notify")
        h.child("W3", "W-notify")
        h.coverage("W-notify")
        h.coverage("W-root")
        while h.base < 40:  # The frozen vector declares revision 41 for its replacement decision.
            self.send({"op": "grant.revoke", "id": "absent"}, work.OWNER, code="not_found")
        self.assertEqual(h.base, 40)
        h.send(decisions.decision("D-revised", support=(), triggers=(), supersedes="D-original"))
        result = h.submit("W2", "B", INTENT["target"], "executor:beta")
        h.run_verification("O-retries", result, "B", INTENT["target"])
        self.assertEqual(h.view().reasons("W2"), [{"code": "effect_unresolved", "refs": ["F-publish"]}])
        for work_id, label in (("W1", "prefs"), ("W3", "delivery")):
            h.send(h.criterion(work_id, "O-" + label), work.OWNER)
            result = h.submit(work_id, label)
            h.run_verification("O-" + label, result, label)
            h.close(work_id)
        self.bind()
        self.assertEqual(h.view().fields("F-publish")["intent_digest"], M0["intent_digest"])
        _, dispatched = self.authorize()
        cut = dispatched["revision"]
        cold = self.cli("replay", "--as-of", AT)["answers"]
        self.assertEqual(set(cold), {f"q{i:02d}" for i in range(1, 11)})
        self.assertEqual(cold["q06"], [{"effect": "F-publish", "attempt": "T1", "state": "dispatching"}])
        self.assertIn({"action": "reconcile", "subject": "F-publish"}, cold["q10"])
        self.assertNotIn({"action": "authorize_dispatch", "subject": "F-publish"}, cold["q10"])
        self.observe("Obs-Git-B", branch_tip=work.TRACE["subjects"]["L"]["digest"])
        self.assertEqual(self.cli("status", "--revision", str(cut), "--as-of", AT)["answers"], cold)
        self.assertEqual(self.cli("frontier", "--as-of", AT)["actions"], h.view().answers(AT)["q10"])
        h.close("W2")
        h.close("W-notify")
        last = self.cli("why", "W2", "--as-of", AT)["support"]
        self.assertEqual(last["basis"]["effects"], [{"effect": "F-publish", "observation": "Obs-Git-B"}])
        view = h.view()
        for vector in work.TRACE["outcome_vectors"]:
            key = vector["basis"]["work"]
            self.assertEqual((view.basis(key), view.outcome(key)), (vector["basis"], vector["digest"]))
        h.send({"op": "work.accept", "id": "W-root", "outcome": view.outcome("W-root")}, work.OWNER)
        self.assertTrue(h.view().closed("W-root"))
        self.assertEqual(self.cli("replay", "--as-of", AT), self.cli("status", "--as-of", AT))

    def test_scope_attenuation_future_grantee_and_exact_physical_subject(self):
        self.ready(slot=dict(SLOT, paths=["notify/delivery.py", *FILES]))
        self.assertEqual(self.cli("context", "AS-B", "--as-of", AT)["capabilities"][0]["paths"], FILES)
        self.assertEqual(self.cli("context", "AS-B", "--as-of", EXPIRY)["capabilities"], [])
        self.bind(code="scope_mismatch", proof=prepare(paths=["notify/delivery.py", *FILES]))
        self.bind(code="scope_mismatch", proof=prepare(instance="9" * 64))
        for changed in ({"resource": "refs/heads/other"}, {"repository": "R-other"}, {"generation": 1},
                        {"grant": "G-unknown"}, {"target": INTENT["expected_old"], "expected_old": INTENT["target"]}):
            intent = {**INTENT, **changed}
            self.bind(intent, code="inactive_assignment" if "target" in changed else "scope_mismatch")
        self.bind(binding={**self.b, "actor_id": "executor:other"}, code="forbidden")
        self.bind(binding=self.b, code="witness_mismatch")
        self.send({"op": "effect.request", "id": "F-publish", "intent": INTENT, "changed_paths": FILES},
                  self.b, code="invalid_op")
        self.bind()
        self.authorize(binding=cutoff_role(changed_paths=["notify/delivery.py"]), code="witness_mismatch")
        self.authorize(at=EXPIRY, code="grant_expired")
        for at, state in ((AT, "active"), (EXPIRY, "expired")):
            answers = self.cli("status", "--as-of", at)["answers"]
            self.assertEqual(answers["q05"][0]["state"], state)
            self.assertEqual({"action": "authorize_dispatch", "subject": "F-publish"} in answers["q10"], state == "active")

    def test_native_distinct_same_tree_commits_have_an_authorizable_empty_path_measurement(self):
        repo = self.h.directory / "repo"
        repo.mkdir()
        def git(*args):
            return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=fixture@example.test",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args])
        git("init", "--quiet", "-b", "purser-demo")
        git("commit", "--allow-empty", "-qm", "first")
        old = git("rev-parse", "HEAD").decode().strip()
        git("commit", "--allow-empty", "-qm", "second")
        target = git("rev-parse", "HEAD").decode().strip()
        self.assertNotEqual(old, target)
        measured = [path.decode() for path in git("diff", "--name-only", "-z", old, target).split(b"\0") if path]
        self.assertEqual(measured, [])
        intent = {**INTENT, "expected_old": {**INTENT["expected_old"], "digest": old},
                  "target": {**INTENT["target"], "digest": target}}
        self.declare()
        self.send(dict(SLOT, id="invalid", paths=[]), self.h.planner, code="invalid_op")
        self.send(dict(GRANT, paths=[]), self.h.planner, code="invalid_op")
        self.issue()
        self.h.submit("W2", "B", intent["target"], "executor:beta")
        self.bind(intent, proof=prepare(intent, paths=measured))
        self.authorize(intent=intent, binding=cutoff_role(intent, changed_paths=measured))
        self.assertEqual(self.cli("replay", "--as-of", AT)["answers"]["q06"][0]["attempt"], "T1")

    def test_other_commitment_on_same_ref_stales_without_disclosing_its_attempt_and_blocks_overlap(self):
        self.ready()
        self.bind()
        other = "C-other"
        owner = work.OWNER
        def send(op, actor=owner, **options):
            return self.send(op, actor, scope=other, **options)
        send({"op": "commitment.create", "id": other, "root": "W-other", "statement": "Independent commitment"})
        _, held = send({"op": "commitment.request_acceptance", "id": other, "expires_at": EXPIRY}, code="needs_principal")
        send({"op": "approval.answer", "id": held["details"]["approval_id"], "request_digest": held["digest"],
              "decision": "approve"})
        send({**SLOT, "id": "F-other", "work": "W-other"})
        send({"op": "work.assign", "id": "AS-other", "work": "W-other", "executor": "executor:elsewhere"})
        executor = {**work.role("executor", "executor:elsewhere", "AS-other"), "commitment": other}
        send({"op": "result.submit", "id": "Result-other", "assignment": "AS-other", "candidate": INTENT["target"]}, executor)
        _, held = send({**GRANT, "id": "G-other", "work": "W-other", "assignment": "AS-other", "generation": 1,
                        "grantee": "executor:elsewhere"}, code="needs_principal")
        send({"op": "approval.answer", "id": held["details"]["approval_id"], "request_digest": held["digest"],
              "decision": "approve"})
        intent = {**INTENT, "commitment": other, "work": "W-other", "assignment": "AS-other", "generation": 1,
                  "grant": "G-other", "effect": "F-other"}
        send({"op": "effect.request", "id": "F-other", "intent": intent}, {**executor, "git_preparation": prepare(intent)})
        op = {"op": "effect.authorize", "id": "T-other-private", "effect": "F-other"}
        actor = {**cutoff_role(intent), "commitment": other}
        before = self.h.base
        send(op, actor)
        ours = {"op": "effect.authorize", "id": "T1", "effect": INTENT["effect"]}
        _, conflict = self.send(ours, cutoff_role(), base=before, code="stale_base")
        self.assertEqual([row["key"] for row in conflict["details"]["conflicts"]],
                         ["git_ref:" + INSTANCE + ":" + INTENT["resource"]])
        self.authorize(code="ref_unresolved")
        self.assertNotIn({"action": "authorize_dispatch", "subject": INTENT["effect"]}, self.h.view().answers(AT)["q10"])
        proof = report("T-other-private", "confirmed_absent", intent)
        observer = {**work.role("observer", "observer:git", "T-other-private", git_observation=proof), "commitment": other}
        send({"op": "effect.reconcile", "id": "Reported-other", "attempt": "T-other-private", "effect": "F-other"}, observer)
        self.send(ours, cutoff_role(), base=before, code="stale_base")
        self.authorize()

    def test_approval_expiry_supersession_and_redelivery_never_mint_another_grant(self):
        self.declare()
        origin, held = self.send(GRANT, self.h.planner, code="needs_principal")
        answer = {"op": "approval.answer", "id": held["details"]["approval_id"], "request_digest": held["digest"],
                  "decision": "approve"}
        self.send(answer, self.h.planner, code="forbidden")
        self.h.submit("W2", "B", INTENT["target"], "executor:beta")
        _, stale = self.send(answer, work.OWNER)
        self.assertEqual(stale["details"]["state"], "stale")
        self.assertIsNone(self.h.view().records.get("G-publish"))
        self.assertEqual(redelivery(self.store, self.h.planner, origin)["code"], "stale_base")
        _, held = self.send(GRANT, self.b, code="needs_principal")
        _, expired = self.send(dict(answer, id=held["details"]["approval_id"], request_digest=held["digest"]),
                               work.OWNER, at=EXPIRY)
        self.assertEqual(expired["details"]["state"], "expired")
        _, approved = self.issue(actor=self.b)
        self.send(approved["ops"][0], work.OWNER, code="already_decided")
        self.assertEqual(len(self.h.view().answers(AT)["q05"]), 1)
        with self.assertRaises(KernelError) as error:
            redelivery(self.store, work.OWNER, {**approved, "justification": ["different"]})
        self.assertEqual(error.exception.code, "id_conflict")
        self.send({"op": "grant.revoke", "id": "G-publish"}, self.b, code="forbidden")
        self.assertEqual(self.h.view().answers(AT)["q05"][0]["state"], "active")

    def test_unresolved_retry_absence_and_consumption_bind_all_public_paths(self):
        self.ready()
        self.bind()
        request, accepted = self.authorize()
        actor = work.role("broker", "broker:git", "F-publish")
        self.assertEqual(redelivery(Store(self.store.path), actor, request), accepted)
        self.assertEqual(authorize_attempt(Store(self.store.path), actor, request), (accepted, False))
        self.assertEqual(accepted["details"]["intent_digest"], M0["intent_digest"])
        self.authorize("T-other", code="effect_unavailable")
        self.bind(dict(INTENT, effect="F-other"), code="slot_occupied")
        self.bind(dict(INTENT, purpose="Different purpose"), code="intent_conflict")
        self.observe("Obs-pending", state="pending")
        self.assertEqual(self.h.view().answers(AT)["q06"], [{"effect": "F-publish", "attempt": "T1", "state": "pending"}])
        self.assertIn("writer_active", json.dumps(self.cli("why", "F-publish", "--as-of", AT)))
        for change in ({"writer_quiescent": False}, {"history_intact": False}, {"branch_tip": INTENT["target"]["digest"]},
                       {"receipt_target": INTENT["target"]["digest"]}, {"repository_instance": "6" * 64},
                       {"matching_transitions": 1}, {"receipt_ref": "refs/purser/receipts/" + "4" * 64}):
            self.observe("Bad", state="confirmed_absent", code="witness_mismatch", **change)
        self.observe("Good-absent", state="confirmed_absent")
        self.authorize("T1", code="id_exists")
        self.assertEqual(authorize_attempt(self.store, actor, request), (accepted, False))
        self.authorize("T2")
        self.observe("Late-T1", code="invalid_transition")
        self.observe("Mismatched-target", attempt="T2", receipt_target=INTENT["expected_old"]["digest"], code="witness_mismatch")
        self.observe("Good-landed", attempt="T2")
        self.assertEqual(self.h.view().answers(AT)["q06"], [])
        self.authorize("T3", code="effect_unavailable")
        _, current = self.bind(binding=self.b)
        self.assertEqual(current["details"]["state"], "confirmed_happened")
        self.bind(dict(INTENT, effect="F-other", purpose="second"), code="slot_occupied")
        self.assertEqual(self.h.view().fields("T1")["state"], "confirmed_absent")
        self.assertEqual(self.h.view().fields("T2")["state"], "confirmed_happened")

    def test_replacing_a_proven_absent_or_undispatched_intent_reads_the_original_grant(self):
        self.ready()
        request, _ = self.bind()
        self.issue(dict(GRANT, id="G-new"), actor=self.b)
        replacement = dict(INTENT, effect="F-new", grant="G-new")
        self.bind(replacement, code="slot_occupied")
        before = self.h.base
        self.send({"op": "grant.revoke", "id": "G-publish"}, work.OWNER)
        op = {"op": "effect.request", "id": "F-new", "intent": replacement}
        _, stale = self.send(op, {**self.b, "git_preparation": prepare(replacement)}, base=before, code="stale_base")
        self.assertIn("G-publish", [c["key"] for c in stale["details"]["conflicts"]])
        self.bind(replacement)
        self.assertEqual(self.h.view().fields("F-publish")["state"], "superseded")
        _, redelivered = self.send(request["ops"][0], self.b, base=request["base_revision"])
        self.assertEqual(redelivered["details"], {"effect": "F-publish", "state": "superseded"})
        self.authorize("T-new", intent=replacement)
        self.issue(dict(GRANT, id="G-final"), actor=self.b)
        final = dict(INTENT, effect="F-final", grant="G-final")
        self.bind(final, code="slot_occupied")
        self.observe("New-absent", "T-new", state="confirmed_absent", intent=replacement)
        self.bind(final)
        self.authorize("Old-slot", intent=replacement, code="effect_unavailable")
        self.authorize("Final-attempt", intent=final)

    def test_observer_independence_retained_report_and_closure_rechecks_bytes(self):
        self.ready()
        self.h.send(self.h.criterion("W2", "O"), work.OWNER)
        self.h.run_verification("O", "Result-B", "B", INTENT["target"])
        self.bind()
        self.authorize()
        op = {"op": "effect.reconcile", "id": "O-Git", "effect": "F-publish", "attempt": "T1"}
        self.send(op, self.b, code="forbidden")
        self.send(op, work.role("observer", "executor:beta", "T1", git_observation=report()), code="forbidden")
        self.send(op, work.role("observer", "broker:git", "T1", git_observation=report()), code="forbidden")
        self.observe("O-Git")
        sha = self.h.view().fields("O-Git")["artifact_sha256"]
        artifacts = Artifacts(self.store.path)
        self.assertEqual(json.loads(artifacts.read(sha)), report())
        (artifacts.directory / sha).write_bytes(b"altered disposable witness")
        with self.assertRaises(KernelError) as err:
            self.h.close("W2")
        self.assertEqual(err.exception.code, "integrity_error")
        self.assertFalse(self.h.view().closed("W2"))

    def test_invalidation_and_reassignment_fence_old_cutoff_but_preserve_observation(self):
        self.declare()
        self.h.send([decisions.premise("A1"), decisions.decision(), decisions.link("W2", "D-old")])
        self.issue()
        self.h.submit("W2", "B", INTENT["target"], "executor:beta")
        self.bind()
        before = self.h.base
        self.h.send(decisions.observation(), decisions.observer())
        op = {"op": "effect.authorize", "id": "Old-attempt", "effect": "F-publish"}
        self.send(op, cutoff_role(), base=before, code="stale_base")
        self.authorize(code="invalidated_support")
        self.h.send(decisions.decision("D-revised", support=(), triggers=(), supersedes="D-old"))
        self.h.submit("W2", "C", INTENT["target"], "executor:gamma")
        self.authorize(code="inactive_assignment")
        fresh = dict(INTENT, effect="F-revised", assignment="AS-C", generation=3, grant="G-revised")
        self.issue(dict(GRANT, id="G-revised", assignment="AS-C", generation=3, grantee="executor:gamma"))
        self.bind(fresh, binding=work.role("executor", "executor:gamma", "AS-C", git_preparation=prepare(fresh)))
        self.authorize("T-current", intent=fresh)
        self.h.submit("W2", "D", INTENT["target"], "executor:new")
        self.send({"op": "grant.revoke", "id": "G-revised"}, work.OWNER)
        self.assertIn({"action": "reconcile", "subject": "F-revised"}, self.h.view().answers(EXPIRY)["q10"])
        self.observe("Obs-historical", "T-current", intent=fresh)
        self.assertIn({"code": "effect_unresolved", "refs": ["F-revised"]}, self.h.view().reasons("W2"))
        self.assertEqual(self.h.view().basis("W2")["effects"], [])

    def exercise_cutoff_order(self, authorize_first):
        self.ready()
        self.bind()
        release = {"op": "effect.authorize", "id": "T1", "effect": INTENT["effect"]}
        revoke = {"op": "grant.revoke", "id": INTENT["grant"]}
        calls = [(cutoff_role(), frame("race-dispatch", self.h.base, release)),
                 (work.OWNER, frame("race-revoke", self.h.base, revoke))]
        if not authorize_first:
            calls.reverse()
        ctx = multiprocessing.get_context("spawn")
        acquired, resume, results = ctx.Event(), ctx.Event(), ctx.Queue()
        writers = []
        try:
            for index, (binding, proposal) in enumerate(calls):
                started = ctx.Event()
                writer = ctx.Process(target=race_writer, args=(self.store.path, binding, proposal,
                                     acquired if index == 0 else None, resume, started, results))
                writer.start()
                writers.append(writer)
                self.assertTrue(started.wait(15))
                if index == 0:
                    self.assertTrue(acquired.wait(15))
            resume.set()
            replies = dict(results.get(timeout=15) for _ in writers)
        finally:
            resume.set()
            for writer in writers:
                writer.join(15)
                if writer.is_alive():
                    writer.kill()
                    writer.join()
            results.close()
        self.assertEqual([w.exitcode for w in writers], [0, 0])
        dispatched, fresh = replies["race-dispatch"]
        revoked, _ = replies["race-revoke"]
        self.assertEqual(revoked["disposition"], "admitted")
        self.assertEqual((dispatched["disposition"], fresh), ("admitted" if authorize_first else "rejected", authorize_first))
        self.h.base = max(dispatched["revision"], revoked["revision"])
        self.assertEqual(self.h.view().fields("G-publish")["in_flight_at_revocation"], ["T1"] if authorize_first else [])
        if authorize_first:
            self.observe("Obs-after-revoke")
        self.assertEqual(len([r for r in self.h.view().records.values() if r["kind"] == "EffectAttempt"]), int(authorize_first))
        self.assertEqual(self.cli("status", "--as-of", AT)["answers"]["q05"][0]["state"], "revoked")

    def test_revoke_wins_reserved_sqlite_cutoff(self):
        self.exercise_cutoff_order(False)

    def test_dispatch_wins_reserved_sqlite_cutoff(self):
        self.exercise_cutoff_order(True)


if __name__ == "__main__":
    unittest.main()
