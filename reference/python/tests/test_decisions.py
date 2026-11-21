"""Exercise M3 on real gates, independent SQLite connections and cold CLI processes."""
import json
import shutil
import sqlite3
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier, Event

from purser.driver import context_pack, tick
from purser.kernel.artifacts import Artifacts
from purser.kernel.gate import admit
from purser.kernel.store import Store
from purser.kernel.wire import KernelError, canonical, digest
from purser.kernel.workview import WorkView
from tests import test_work as work

SOURCE = Path(__file__).resolve().parents[1]
ARTIFACT = b"Observed the exponential retry requirement via the approved fixture input."


def premise(key, observer="observer:premise"):
    return {"op": "assumption.create", "id": key, "statement": "A held scheduling premise", "observer": observer}


def decision(identity="D-old", support=("A1",), triggers=("A1",), supersedes=None):
    choice = "exponential" if supersedes else "linear"
    return {"op": "decision.record", "id": identity, "choice": choice, "alternatives": ["linear", "exponential"],
            "rationale": "Apply the admitted scheduling rule", "support": list(support),
            "triggers": list(triggers), "supersedes": supersedes}


def link(child, parent):
    return {"op": "dependency.add", "id": child, "support": parent}


def observation(identity="Obs-change", subject="A1", finding="contradicts"):
    return {"op": "assumption.observe", "id": identity, "assumption": subject, "finding": finding}


def observer(subject="A1", actor="observer:premise"):
    return work.role("observer", actor, subject, premise={"artifact": ARTIFACT})


def request(identity, base, op):
    return {"v": 1, "proposal_id": identity, "commitment": work.C, "base_revision": base,
            "ops": [op], "justification": []}


class DecisionKernel(unittest.TestCase):
    def setUp(self):
        self.h = work.WorkTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.store = self.h.store

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-B", "-m", "purser", "--db", str(self.store.path), *args],
                                cwd=SOURCE, env={"PATH": "/usr/bin:/bin", "TZ": "UTC", "LC_ALL": "C"},
                                capture_output=True, check=True, timeout=20)
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def setup_w2(self, *, submitted=True):
        h = self.h
        h.child("W2")
        h.send([premise("A1"), decision(), link("W2", "D-old")])
        h.send(h.criterion("W2", "O-retry"), work.OWNER)
        if submitted:
            result = h.submit("W2", "a")
            h.run_verification("O-retry", result, "a")
        else:
            h.send({"op": "work.assign", "id": "AS-A", "work": "W2", "executor": "executor:test"})

    def test_mixed_graph_trigger_opinion_and_replacement_after_restart(self):
        h = self.h
        for key in ("W1", "W2", "W3", "W-B", "W-C", "W-cross"):
            h.child(key)
        h.child("W-tree", "W-B")
        h.send([premise("A1"), premise("A-other"), decision(support=("A1", "A-other")),
                link("W2", "D-old"), link("W-B", "W2"), link("W-C", "W-B"), link("W-B", "W-C"),
                link("W-cross", "W-tree")])
        for key in ("W1", "W2", "W3"):
            h.send(h.criterion(key, "O-"+key), work.OWNER)
            result = h.submit(key, key)
            h.run_verification("O-"+key, result, key)
            h.close(key)
        initial = h.view()
        positive = initial.basis("W2")["decisions"]
        self.assertEqual(positive, [{"id": "D-old", "revision": initial.records["D-old"]["changed"]}])
        retained = {k: (initial.records[k], initial.basis(k), initial.outcome(k)) for k in ("W1", "W3")}
        opinion = {"op": "decision.opine", "id": "Opinion", "decision": "D-old", "opinion": "Exponential may be better"}
        h.send(opinion, work.role("executor", "executor:test", "AS-W2"))
        h.send(observation("Obs-confirm", finding="confirms"), observer())
        h.send(observation("Obs-other", "A-other"), observer("A-other"))
        self.assertEqual(h.view().records["D-old"], initial.records["D-old"])
        self.assertTrue(h.view().closed("W2"))
        self.assertEqual(h.view().fields("A-other")["affected"], [])
        before = h.base
        changed = h.send(observation(), observer())
        current = WorkView(self.store.replay()["projection"])
        self.assertEqual(current.answers()["q04"], [
            {"assumption": "A-other", "affected": []},
            {"assumption": "A1", "affected": ["D-old", "W-B", "W-C", "W2"]}])
        revision = current.records["Obs-change"]["changed"]
        self.assertEqual({current.records[k]["changed"] for k in ("W2", "W-B", "W-C", "D-old", "A1")}, {revision})
        self.assertEqual(Artifacts(self.store.path).read(current.fields("Obs-change")["artifact_sha256"]), ARTIFACT)
        for key in ("W1", "W3"):
            self.assertEqual((current.records[key], current.basis(key), current.outcome(key)), retained[key])
            self.assertTrue(current.closed(key))
        self.assertEqual(current.fields("W-tree")["state"], "open")
        self.assertEqual(current.fields("W-cross")["state"], "open")
        self.assertEqual(current.qualification("O-W2")[0], "open")
        self.assertIn({"action": "reconsider", "subject": "D-old"}, current.answers()["q10"])
        self.assertIn({"action": "rework", "subject": "W2"}, current.answers()["q10"])
        self.assertEqual(h.close("W2", allowed=False, base=before)["code"], "stale_base")
        self.assertEqual(h.close("W2", allowed=False)["code"], "closure_blocked")
        before_answers = self.cli("status", "--revision", str(before))["answers"]
        self.assertEqual(before_answers["q03"][0]["state"], "effective")
        self.assertNotIn("A1", [row["assumption"] for row in before_answers["q04"]])
        self.assertTrue(self.cli("why", "W2", "--revision", str(before))["support"]["outcome"])
        why = self.cli("why", "W2", "--revision", str(changed["revision"]))
        self.assertIsNone(why["support"]["outcome"])
        self.assertIn({"code": "invalidated_support", "refs": ["A1", "D-old", "Obs-change", "W2"]}, why["reasons"])
        self.assertEqual(self.cli("why", "W1")["support"]["basis"], retained["W1"][1])
        self.assertEqual(self.cli("replay", "--as-of", "2026-11-18T12:00:00.000000Z"),
                         self.cli("status", "--as-of", "2026-11-18T12:00:00.000000Z"))
        self.assertEqual(h.send({"op": "work.assign", "id": "AS-too-early", "work": "W2", "executor": "executor:b"},
                                allow=True)["code"], "invalidated_support")
        h.send(decision("D-new", (), (), "D-old"))
        self.assertEqual(h.view().answers()["q03"], [{"decision": "D-new", "state": "effective", "support": []}])
        self.assertIsNone(h.view().outcome("W2"))
        replaced = self.cli("why", "W2")["support"]
        self.assertEqual(replaced["edges"], [{"kind":"depends_on", "from_id":"W2", "to_id":"D-new"}])
        self.assertEqual(replaced["causes"][0]["edges"], [
            {"kind":"depends_on", "from_id":"D-old", "to_id":"A1"},
            {"kind":"depends_on", "from_id":"W2", "to_id":"D-old"}])
        result = h.submit("W2", "b", work.TRACE["subjects"]["B"], producer="executor:b")
        h.evidence("V-W2", "Obs-W2", "Late-evidence", "pass")
        v = h.view()
        self.assertEqual(v.qualification("O-W2")[0], "open")
        self.assertEqual(v.fields("W2")["generation"], 2)
        self.assertEqual(self.cli("context", "AS-b")["decisions"],
                         [{"id": "D-new", "revision": v.records["D-new"]["changed"], "choice": "exponential", "support": []}])
        h.run_verification("O-W2", result, "b", candidate=work.TRACE["subjects"]["B"])
        h.close("W2")
        self.assertTrue(h.view().closed("W2"))
        self.assertTrue(h.view().closed("W3"))

    def test_principal_accepted_any_keeps_independent_witness(self):
        h = self.h
        self.setup_w2()
        h.child("W1")
        h.coverage(kind="any")
        h.send(h.criterion("W1", "O-prefs"), work.OWNER)
        result = h.submit("W1", "w1")
        h.run_verification("O-prefs", result, "w1")
        h.close("W2")
        h.close("W1")
        outcome = h.view().outcome("W-root")
        h.send({"op": "work.accept", "id": "W-root", "outcome": outcome}, work.OWNER)
        h.send(observation(), observer())
        view = h.view()
        self.assertEqual(view.fields("W2")["state"], "stale")
        self.assertTrue(view.closed("W-root"))
        self.assertEqual(view.fields(work.C)["state"], "satisfied")
        self.assertEqual(view.outcome("W-root"), outcome)

    def test_trigger_identity_scope_and_state_reads(self):
        h = self.h
        self.setup_w2()
        self.assertEqual(h.send(observation("Spoof"), observer(actor="observer:other"), allow=True)["code"], "forbidden")
        self.assertEqual(h.send(observation("Spoof2"), work.role("executor", "executor:test", "AS-a"),
                                allow=True)["code"], "forbidden")
        self.assertEqual(h.send(decision("D-early", (), (), "D-old"), allow=True)["code"], "invalid_transition")
        base = h.base
        h.send({"op": "decision.opine", "id": "Free-opinion", "decision": "D-old", "opinion": "Discuss"})
        self.assertEqual(h.close("W2", base=base)["code"], "ok")
        h.send(observation(), observer())
        self.assertEqual(h.view().fields("W2")["state"], "stale")
        h.send({"op": "dependency.remove", "id": "W2", "support": "D-old"})
        base = h.base
        h.send([premise("A-new"), decision("D-new", ("A-new",), ("A-new",), "D-old")])
        h.send(link("W2", "D-new"))
        rework = {"op": "work.assign", "id": "AS-new", "work": "W2", "executor": "executor:new"}
        self.assertEqual(h.send(rework, base=base, allow=True)["code"], "stale_base")
        self.assertEqual(h.send(rework)["code"], "ok")

    def test_premise_cannot_certify_a_run_even_in_original_history(self):
        h = self.h
        self.setup_w2(submitted=False)
        h.send({"op": "result.submit", "id": "R-main", "assignment": "AS-A", "candidate": h.candidate},
               work.role("executor", "executor:test", "AS-A"))
        h.send({"op": "run.open", "id": "V-main", "obligation": "O-retry", "result": "R-main"})
        h.send(observation("Obs-confirm", finding="confirms"), observer())
        op = {"op": "evidence.record", "id": "E-wrong", "run": "V-main", "observation": "Obs-confirm", "verdict": "pass"}
        proposal = request("wrong-witness", h.base, op)
        verifier = work.role("verifier", assignment="V-main")
        refused = admit(self.store, verifier, proposal)
        self.assertEqual((refused["disposition"], refused["code"], refused["revision"]),
                         ("rejected", "witness_mismatch", h.base + 1))
        self.assertEqual(admit(Store(self.store.path), verifier, proposal), refused)
        self.assertEqual(h.view().qualification("O-retry")[0], "open")
        self.assertNotIn("Obs-confirm", [o["id"] for o in self.cli("context", "AS-A")["observations"]])
        self.assertEqual(self.cli("why", "Obs-confirm")["record"]["fields"]["assumption"], "A1")
        prior = self.store.snapshot()
        seq = prior["revision"] + 1
        forged = {"v": 1, "event_version": 1, "revision": seq, "type": "EvidenceRecorded",
                  "batch_start": seq, "batch_size": 1, "proposal_id": "forged", "actor_id": verifier["actor_id"],
                  "at": "2026-11-18T12:00:00.000000Z", "prev_hash": prior["head_hash"],
                  "data": {k: v for k, v in op.items() if k != "op"} | {"commitment":work.C, "verifier":verifier["actor_id"]}}
        forged["hash"] = digest("event", forged)
        with closing(sqlite3.connect(self.store.path)) as conn, conn:
            conn.execute("INSERT INTO events VALUES (?,?)", (seq, canonical(forged)))
        with self.assertRaises(KernelError) as invalid:
            self.store.replay()
        self.assertEqual((invalid.exception.code, invalid.exception.details["revision"]), ("integrity_error", seq))
        self.assertEqual(self.store.snapshot(revision=prior["revision"]), prior)

    def test_removed_and_transient_incoming_membership_stale_supersession(self):
        h = self.h
        h.child("W2")
        h.child("W3")
        h.send([premise("A1"), decision(), link("W2", "D-old")])
        h.send(observation(), observer())
        original = h.base
        h.send({"op": "dependency.remove", "id": "W2", "support": "D-old"})
        replacement = decision("D-new", (), (), "D-old")
        refused = h.send(replacement, base=original, allow=True)
        self.assertEqual(refused["code"], "stale_base")
        self.assertIn("depends_on:incoming:D-old", [r["key"] for r in refused["details"]["conflicts"]])
        base = h.base
        h.send(link("W3", "D-old"))
        h.send({"op": "dependency.remove", "id": "W3", "support": "D-old"})
        self.assertEqual(h.send(replacement, base=base, allow=True)["code"], "stale_base")
        self.assertEqual(h.send(replacement)["disposition"], "admitted")

    def test_both_writer_orders_close_against_independent_observation(self):
        h = self.h
        self.setup_w2()
        base = h.base
        close = (h.closer, request("race-close", base, {"op": "work.close", "id": "W2"}))
        change = (observer(), request("race-change", base, observation()))
        for first, second in ((close, change), (change, close)):
            with self.subTest(first=first[1]["proposal_id"]):
                target = Store(h.directory / (first[1]["proposal_id"] + ".db"))
                target.initialize()
                with closing(sqlite3.connect(self.store.path)) as source, closing(sqlite3.connect(target.path)) as dest:
                    source.backup(dest)
                shutil.copytree(Artifacts(self.store.path).directory, Artifacts(target.path).directory)
                reserved, waiting, finish = Event(), Event(), Event()

                class First(Store):
                    def transact(self, actor, proposal, clock, decide):
                        def hold(snap, time):
                            reserved.set()
                            if not finish.wait(5):
                                raise AssertionError("second connection never reached its write attempt")
                            return decide(snap, time)
                        return super().transact(actor, proposal, clock, hold)

                class Second(Store):
                    def transact(self, *args):
                        waiting.set()
                        return super().transact(*args)

                with ThreadPoolExecutor(2) as pool:
                    a = pool.submit(admit, First(target.path), *first)
                    self.assertTrue(reserved.wait(5))
                    b = pool.submit(admit, Second(target.path), *second)
                    self.assertTrue(waiting.wait(5))
                    self.assertFalse(b.done())
                    finish.set()
                    prior, later = a.result(10), b.result(10)
                self.assertEqual(prior["disposition"], "admitted")
                historical = WorkView(target.snapshot(revision=prior["revision"])["projection"])
                if first == close:
                    self.assertTrue(historical.closed("W2"))
                    self.assertEqual(later["disposition"], "admitted")
                else:
                    self.assertEqual(historical.fields("W2")["state"], "stale")
                    self.assertEqual(later["code"], "stale_base")
                self.assertEqual(WorkView(target.replay()["projection"]).fields("W2")["state"], "stale")

    def test_two_ticks_same_snapshot_one_release_then_invalidation(self):
        h = self.h
        self.setup_w2(submitted=False)
        selected, released, resume, continue_second = Barrier(2), Event(), Event(), Event()
        calls = []

        class SameStart(Store):
            def __init__(self, path, later):
                super().__init__(path)
                self.later = later

            def snapshot(self, **kw):
                snap = super().snapshot(**kw)
                if not kw:
                    selected.wait(5)
                    if self.later and not continue_second.wait(5):
                        raise AssertionError("uncut second tick")
                return snap

        def adapter(pack):
            calls.append(pack)
            released.set()
            if not resume.wait(5):
                raise AssertionError("in-flight worker timeout")
            return h.candidate

        adapters = {"executor:test": adapter}
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(tick, SameStart(self.store.path, False), adapters)
            second = pool.submit(tick, SameStart(self.store.path, True), adapters)
            try:
                self.assertTrue(released.wait(5))
                h.send(observation(), observer())
                continue_second.set()
                refusal = second.result(5)
                self.assertEqual(refusal["state"], "refused")
                self.assertEqual(refusal["receipt"]["code"], "stale_base")
            finally:
                continue_second.set()
                resume.set()
            self.assertEqual(first.result(5)["receipt"]["code"], "stale_base")
        self.assertEqual(len(calls), 1)
        self.assertEqual(h.view().fields("W2")["state"], "stale")
        self.assertIsNone(h.view().fields("W2").get("result"))
        with self.assertRaises(KernelError):
            context_pack(self.store.snapshot(), "AS-A")
        self.assertEqual(tick(self.store, adapters)["state"], "idle")

    def test_separate_driver_release_survives_only_unrelated_receipts(self):
        h = self.h
        self.setup_w2(submitted=False)
        binding = work.role("executor", "executor:test", "AS-A")
        release = request("recover-lost-reply", h.base, {"op": "assignment.dispatch", "id": "AS-A"})
        ack = admit(self.store, binding, release)
        garbage = request("unrelated-rejected", h.base, {"op": "unknown", "id": "Q"})
        self.assertEqual(admit(self.store, h.planner, garbage)["disposition"], "rejected")
        self.assertEqual(admit(self.store, binding, release), ack)
        called = []
        def adapter(pack):
            called.append(pack["decisions"])
            return h.candidate
        self.assertEqual(tick(self.store, {"executor:test": adapter})["receipt"]["disposition"], "admitted")
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0][0]["id"], "D-old")

    def test_acquired_direct_outcomes_follow_selected_coverage_through_a_structural_chain(self):
        for rule in ("all", "any", "k_of_n"):
            for selected in (False, True):
                with self.subTest(rule=rule, selected=selected):
                    self.check_support_chain(rule, selected)

    def check_support_chain(self, rule, selected):
        h = work.WorkTests()
        h.setUp()
        self.addCleanup(h.doCleanups)
        h.child("Aggregate")
        h.child("Structural")
        h.child("Child-S", "Structural")
        h.child("Direct")
        h.child("Consumer")
        h.child("Untouched")
        items = ["Child-A", "Child-B", "Child-Z"]
        for key in items:
            h.child(key, "Aggregate")
        h.coverage("Aggregate", rule, 2 if rule == "k_of_n" else None)
        h.coverage("Structural")
        changed = "Child-A" if selected else "Child-Z"
        if rule == "all" and not selected:
            changed = "Outside"
            h.child(changed)
        h.send([premise("A1"), decision(), link(changed, "D-old"), link("Structural", "Aggregate"),
                link("Direct", "Aggregate"), link("Consumer", "Structural")])

        def qualify(key, label, payload):
            subject = {"kind": "artifact.bytes", "algorithm": "sha256", "digest": Artifacts(h.store.path).put(payload)}
            result = h.submit(key, label, subject)
            h.run_verification("O-"+key, result, label, candidate=subject, report=b"Observed " + payload)
            h.close(key)

        for key in [*items, "Child-S", "Direct", "Consumer", "Untouched"] + (["Outside"] if changed == "Outside" else []):
            h.send(h.criterion(key, "O-"+key), work.OWNER)
        for key in [*items, "Child-S", "Untouched"] + (["Outside"] if changed == "Outside" else []):
            qualify(key, key, key.encode())
        h.close("Aggregate")
        h.close("Structural")
        for key in ("Direct", "Consumer"):
            qualify(key, key, key.encode())
        old = h.view()
        old_revision = h.base
        self.assertEqual(old.graph.acquired("Consumer"), {"Structural": old.outcome("Structural")})
        self.assertEqual(old.graph.acquired("Structural"), {"Aggregate": old.outcome("Aggregate")})
        self.assertEqual(old.graph.acquired("Direct"), {"Aggregate": old.outcome("Aggregate")})
        h.send(observation(), observer())
        affected = h.view().answers()["q04"][0]["affected"]
        self.assertEqual(affected, sorted([changed, "D-old"]))
        h.send(decision("D-new", (), (), "D-old"))
        qualify(changed, "Changed", b"new supported candidate")
        if not h.view().closed("Aggregate"):
            h.close("Aggregate")
        if not h.view().closed("Structural"):
            h.close("Structural")
        updated = WorkView(Store(h.store.path).replay()["projection"])
        self.assertEqual(updated.records["Untouched"], old.records["Untouched"])
        for key in ("Aggregate", "Structural"):
            self.assertTrue(updated.closed(key))
            self.assertEqual(updated.outcome(key) != old.outcome(key), selected)
        self.assertEqual(updated.fields("Structural")["generation"] > old.fields("Structural")["generation"], selected)
        for key, support in (("Direct", "Aggregate"), ("Consumer", "Structural")):
            self.assertEqual(updated.graph.acquired(key), {support: old.outcome(support)})
            if selected:
                self.assertEqual(updated.fields(key)["state"], "stale")
                self.assertEqual(updated.qualification("O-"+key)[0], "open")
                self.assertEqual(h.close(key, allowed=False)["code"], "closure_blocked")
                self.assertIn({"action":"rework", "subject":key}, updated.answers()["q10"])
                clarify = subprocess.check_output([sys.executable, "-B", "-m", "purser", "--db", str(h.store.path),
                                                   "why", key], cwd=SOURCE)
                self.assertEqual(json.loads(clarify)["support"]["work_outcomes"], {
                    "acquired":{support:old.outcome(support)}, "current":{support:updated.outcome(support)}})
                qualify(key, "Revised-"+key, b"Reworked under new support " + key.encode())
                self.assertGreater(h.view().fields(key)["generation"], old.fields(key)["generation"])
                self.assertTrue(h.view().closed(key))
            else:
                self.assertEqual(updated.records[key], old.records[key])
                self.assertEqual(updated.outcome(key), old.outcome(key))
                self.assertTrue(updated.closed(key))
        past = WorkView(h.store.snapshot(revision=old_revision)["projection"])
        self.assertTrue(past.closed("Consumer"))
        self.assertEqual(past.outcome("Consumer"), old.outcome("Consumer"))

    def test_work_dependency_cycle_with_coverage_stays_blocked_and_local(self):
        h = self.h
        h.child("Aggregate")
        h.child("Child", "Aggregate")
        h.coverage("Aggregate")
        h.send([premise("A1"), decision(), link("Child", "D-old"), link("Child", "Aggregate")])
        self.assertEqual(h.close("Aggregate", allowed=False)["code"], "closure_blocked")
        self.assertEqual(h.send({"op":"work.assign", "id":"AS-cycle", "work":"Child", "executor":"executor:test"},
                                allow=True)["code"], "invalidated_support")
        h.send(observation(), observer())
        view = WorkView(self.store.replay()["projection"])
        self.assertEqual(view.answers()["q04"], [{"assumption":"A1", "affected":["Child","D-old"]}])
        self.assertFalse(view.closed("Aggregate"))

    def test_removing_and_restoring_direct_support_before_and_after_structural_closure(self):
        h = self.h
        for parent in ("Automatic", "Peer"):
            h.child(parent)
            leaf = "Leaf-"+parent
            h.child(leaf, parent)
            h.coverage(parent)
            h.send(h.criterion(leaf, "O-"+leaf), work.OWNER)
            result = h.submit(leaf, leaf)
            h.run_verification("O-"+leaf, result, leaf)
            h.close(leaf)
        h.close("Peer")
        unbound = h.view().outcome("Automatic")
        h.send(link("Automatic", "Peer"))
        bound = h.view().outcome("Automatic")
        self.assertIsNotNone(bound)
        self.assertNotEqual(bound, unbound)
        h.close("Automatic")
        h.send({"op":"dependency.remove", "id":"Automatic", "support":"Peer"})
        h.send(link("Automatic", "Peer"))
        self.assertNotEqual(h.view().outcome("Automatic"), bound)
        h.close("Automatic")
        self.assertTrue(h.view().closed("Automatic"))
