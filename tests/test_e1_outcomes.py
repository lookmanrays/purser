"""The E1 protocol distinguishes public releases, immutable candidates and independently observed outcomes."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from experiments.e1 import scorer
from experiments.e1.arms import self_review
from experiments.e1.artifacts import Artifacts, Candidates
from experiments.e1.executor import PATHS, implement, inject


PARENT, RETRY, SCOPE = "W-notify", "W2/retry", "scope/paths"


def event(kind, **payload):
    return {"kind": kind, "payload": payload}


def action(kind, actor, work, oid, reply=None, *, attempt=None, inputs=None, **extra):
    p = dict(action=kind, actor=actor, subject=work, candidate=oid, input=inputs or {}, **extra)
    if attempt is not None:
        p["attempt"] = attempt
    if reply is not None:
        p["return"] = reply
    return event("action", **p)


def submit(work="W2", oid="A"):
    return action("submit", "executor", work, oid, {"state": "submitted"})


def review(work, oid, *labels):
    return action("check", "plan_reviewer", work, oid, {"missing": list(labels)})


def state(work, oid, before, after, *, evidence=None):
    labels = evidence["diagnosis"] if evidence else []
    return event("public_state", subject=work, candidate=oid, diagnosis=labels, evidence=evidence,
                 **{"from": before, "to": after})


def fault(work="W2", target=RETRY, oid="A"):
    return event("fault", work=work, candidate=oid, target=target)


def truth(oid, failures=(), *, omit=()):
    return [event("oracle", candidate=oid, criterion=label, version=1,
                  verdict="fail" if label in failures else "pass") for label in scorer.LABELS if label not in omit]


class PublicOutcomes(unittest.TestCase):
    def test_unobserved_public_interval_can_only_change_a_later_first_diagnosis(self):
        first = review("W2", "A", RETRY)
        gap = event("public_gap")
        prefix, measurements = [fault(), submit()], truth("A", [RETRY])
        earlier = scorer.assess(prefix + [gap, first] + measurements, history_complete=False)
        later = scorer.assess(prefix + [first, gap] + measurements, history_complete=False)
        self.assertIsNone(earlier.named)
        self.assertIs(later.named, True)

    def test_alias_reread_replacement_reopen_and_final_state_use_the_declared_candidate(self):
        journal = [state(PARENT, "A", "submitted", "done"), state(PARENT, "A", "done", "closed"),
                   state(PARENT, "A", "closed", "closed"), state(PARENT, "B", "closed", "closed"),
                   state(PARENT, "B", "closed", "submitted"), state(PARENT, "B", "submitted", "done"),
                   state(PARENT, None, "done", "open"), *truth("A", [SCOPE]), *truth("B")]
        assessment = scorer.assess(journal)
        self.assertEqual(assessment.false_candidates, ["A"])
        self.assertEqual(assessment.correct_candidates, ["B", "B"])
        self.assertEqual(assessment.final_parent, ("open", None))
        self.assertTrue(assessment.facts["false_parent_closure"] and assessment.facts["correct_parent_closure"])
        self.assertIsNone(assessment.facts["missing_named"])
        self.assertIsNone(assessment.score(True))

    def test_private_reused_oid_wrong_work_pending_or_declined_submission_do_not_expose(self):
        earlier = [submit("W3"), state("W3", "A", "open", "closed"), fault(),
                   state("W3", "A", "closed", "done"), review("W3", "A", RETRY),
                   state("W2", "A", "assigned", "assigned"), *truth("A", [RETRY])]
        for reply in (None, {"failure": "lost"}, {"disposition": "rejected", "diagnosis": [RETRY]},
                      {"ok": True}, {"state": "open"}):
            journal = [*earlier, action("submit", "executor", "W2", "A", reply), review("W2", "A", RETRY)]
            with self.subTest(reply=reply):
                outcome = scorer.assess(journal)
                self.assertFalse(outcome.fault_measured)
                self.assertIsNone(outcome.facts["missing_named"])
                self.assertIsNone(outcome.facts["extra_diagnoses"])
                self.assertIsNone(outcome.score(True))
        acknowledged = scorer.assess([*earlier, action("submit", "executor", "W2", "A", {"disposition": "admitted"}),
                                      review("W2", "A", RETRY)])
        recovered = scorer.assess([*earlier, action("submit", "executor", "W2", "A"),
                                  state("W2", "A", "assigned", "submitted"), review("W2", "A", RETRY)])
        for outcome in (acknowledged, recovered):
            self.assertTrue(outcome.fault_measured and outcome.facts["caught"])
        # The same immutable bytes can be returned for different submissions. A
        # subsequent reread of an already released W3 does not readmit its result.
        stale = [submit("W3"), state("W3", "A", "submitted", "closed"), fault(), submit(),
                 state("W3", "A", "closed", "done"), review("W3", "A", SCOPE),
                 review("W2", "A", RETRY), *truth("A", [RETRY])]
        self.assertTrue(scorer.score(stale)["caught"])
        stale = [submit(), state("W2", "A", "submitted", "closed"), fault(),
                 state("W2", "A", "closed", "done"), *truth("A", [RETRY])]
        self.assertFalse(scorer.assess(stale).fault_measured)

    def test_first_sealed_self_review_of_a_submitted_sibling_inherits_the_outside_scope_fault(self):
        with tempfile.TemporaryDirectory() as temp, zipfile.ZipFile(Path(temp) / "component.zip", "w") as z:
            git = Candidates(Path(temp), Artifacts(z))
            implement(git.work, "W1")
            inject(git.work, ("outside_scope", "W1", SCOPE), 9003)
            origin = git.commit("First outside-scope work")
            path = git.work / PATHS["W3"]
            path.write_text(path.read_text() + "\n# Implementer left the original behavior.\n")
            descendant = git.commit("Incomplete delivery work carrying the extra file")
            with git.export(descendant) as checkout:
                returned = self_review(git.archive, descendant, Path(checkout), "W3")
                parent = self_review(git.archive, descendant, Path(checkout), PARENT)
            self.assertEqual(returned["missing"], ["W3/delivery"])
            self.assertIn(SCOPE, parent["missing"])
            journal = [fault("W1", SCOPE, origin), submit("W1", origin), submit("W3", descendant),
                       action("check", "plan_reviewer", "W3", descendant, returned),
                       submit(PARENT, descendant), action("check", "plan_reviewer", PARENT, descendant, parent),
                       *truth(origin, [SCOPE])]
            result = scorer.score(journal, candidates=git.entries)
        self.assertEqual((result["missing_named"], result["extra_diagnoses"], result["caught"]), (False, 1, False))

    def test_an_unsubmitted_subject_an_unrelated_branch_and_stale_results_cannot_supply_first_labels(self):
        journal = [fault(), submit(), review("W3", "A", SCOPE), submit("W3", "other"),
                   review("W3", "other", SCOPE), submit("W3", "B"), review("W3", "other", "legacy/compat"),
                   action("observe", "independent_observer", "W3", "B", {"diagnosis": [SCOPE]}),
                   action("decide", "independent_verifier", "W3", "B",
                          {"disposition": "rejected", "diagnosis": [SCOPE]}, inputs={"verdict": "fail"}),
                   review("W3", "B", RETRY, "legacy/compat"), *truth("A", [RETRY])]
        result = scorer.score(journal, candidates={"root": None, "A": "root", "B": "A", "other": "root"})
        self.assertEqual((result["missing_named"], result["extra_diagnoses"], result["caught"]), (True, 1, True))

    def test_context_releases_adverse_evidence_and_excludes_pending_requirements_and_private_inputs(self):
        reasons = [{"code": "missing_evidence", "refs": ["O-W2-retry"]}]
        for context in (
            {"work": {"id": "W2"}, "state": "submitted", "candidate": "A", "verified": {RETRY: "inconclusive"}},
            {"work": {"id": "W2", "state": "submitted", "candidate": {"digest": "A"}},
             "obligations": [{"state": "failed", "title": RETRY}], "reasons": reasons},
        ):
            pending = {"work": {"id": "W2", "state": "submitted", "candidate": {"digest": "A"}},
                       "obligations": [{"state": "open", "title": SCOPE}], "reasons": reasons}
            journal = [fault(), action("context", "executor", "W2", None, context), submit(),
                       action("context", "executor", "W2", None, pending),
                       action("plan", "planner", "W2", "A", {"criterion": SCOPE}, inputs={"repair": [SCOPE]}),
                       action("context", "executor", "W2", None, context), *truth("A", [RETRY])]
            with self.subTest(context=context):
                self.assertTrue(scorer.score(journal)["caught"])
                self.assertEqual(scorer.score(journal)["extra_diagnoses"], 0)

    def test_unacknowledged_repair_bounds_first_diagnosis_using_observed_worktree_or_completed_edit(self):
        for reply, annotations, expected in (
            (None, {}, (None, None, 0)), ({"failure": "cutoff"}, {"repair_applied": True}, (False, False, 0)),
            ({"failure": "before write"}, {"repair_applied": False}, (True, True, 0)),
            ({"edited": "W2"}, {}, (False, False, 0)),
        ):
            repair = action("edit", "executor", "W2", None, reply, inputs={"repair": [RETRY]}, **annotations)
            journal = [fault(), submit(), repair, review("W2", "A", RETRY), *truth("A", [RETRY])]
            result = scorer.assess(journal).facts
            with self.subTest(reply=reply, annotations=annotations):
                self.assertEqual((result["missing_named"], result["caught"], result["extra_diagnoses"]), expected)

    def test_no_op_parent_tidy_and_unrelated_sibling_edit_leave_the_response_window_open(self):
        journal = [fault(), submit(),
                   action("edit", "executor", "W3", None, {"edited": "W3"}, inputs={"repair": ["W3/delivery"]}),
                   action("edit", "executor", PARENT, None, {"removed": []}, inputs={"repair": [SCOPE]}),
                   review("W2", "A", RETRY), *truth("A", [RETRY])]
        self.assertTrue(scorer.score(journal)["missing_named"])

    def test_every_faulty_parent_including_a_post_repair_descendant_defeats_a_catch(self):
        before = [fault(), submit(), review("W2", "A", RETRY),
                  action("edit", "executor", "W2", None, {"edited": "W2"}, inputs={"repair": [RETRY]})]
        parents = {"root": None, "A": "root", "B": "A", "C": "B", "unrelated": "root"}
        for oid, failures, caught in (("A", [RETRY], False), ("C", [RETRY], False),
                                      ("C", [SCOPE], True), ("unrelated", [RETRY], True)):
            checks = truth("A", [RETRY]) + (truth(oid, failures) if oid != "A" else [])
            result = scorer.score([*before, submit(PARENT, oid), state(PARENT, oid, "submitted", "done"), *checks],
                                  candidates=parents)
            with self.subTest(oid=oid, failures=failures):
                self.assertTrue(result["missing_named"] and result["false_parent_closure"])
                self.assertEqual(result["caught"], caught)

    def test_partial_closure_measurements_keep_independent_positive_and_negative_facts(self):
        journal = [fault(), submit(), review("W2", "A", RETRY), state(PARENT, "B", "submitted", "closed"),
                   *truth("A", [RETRY], omit=["legacy/compat"]), *truth("B", [SCOPE], omit=[RETRY])]
        assessment = scorer.assess(journal, candidates={"A": None, "B": "A"})
        self.assertEqual(assessment.facts, {"false_parent_closure": True, "missing_named": True, "caught": None,
                                          "correct_parent_closure": False, "extra_diagnoses": 0})
        self.assertEqual(assessment.missing_candidates, {"B"})
        self.assertIsNone(assessment.score())
        incomplete = scorer.assess([*journal, event("oracle", candidate="B", criterion=RETRY, verdict="fail")],
                                   history_complete=False, candidates={"A": None, "B": "A"})
        self.assertFalse(incomplete.facts["caught"])
        self.assertIsNone(incomplete.facts["correct_parent_closure"])
        self.assertIsNone(scorer.assess([], history_complete=False).facts["false_parent_closure"])

    def test_an_interrupted_read_cannot_be_replaced_by_a_subsequent_verdict_or_a_derived_summary(self):
        journal = [fault(), submit(), action("check", "plan_reviewer", "W2", "A", attempt=2),
                   state("W2", "A", "submitted", "submitted", evidence={"source_action": 2, "diagnosis": [RETRY]}),
                   review("W2", "A", RETRY), *truth("A", [RETRY])]
        self.assertIsNone(scorer.assess(journal).facts["missing_named"])
        journal[2] = action("decide", "independent_verifier", "W2", "A", attempt=2,
                            inputs={"criterion": RETRY, "verdict": "fail"})
        outcome = scorer.assess(journal)
        self.assertIsNone(outcome.facts["missing_named"])
        self.assertIsNone(outcome.score())

    def test_recovered_decisions_bind_a_durable_result_to_its_exact_invocation_and_candidate(self):
        def run(publication, *, evidence_work="W2", evidence_oid="A", source=2):
            recovery = {"source_action": source, "diagnosis": [RETRY], "publication": publication}
            check = action("decide", "independent_verifier", "W2", "A", attempt=2, inputs={"criterion": RETRY})
            return scorer.assess([fault(), submit(), check, state(evidence_work, evidence_oid, "submitted",
                                 "submitted", evidence=recovery), review("W2", "A", SCOPE), *truth("A", [RETRY])])

        binding = {"attempt": 2, "actor": "independent_verifier", "subject": "W2",
                   "candidate": "A", "diagnosis": [RETRY]}
        self.assertTrue(run(binding).facts["caught"])
        for changed in ({"attempt": 1}, {"actor": "closure"}, {"subject": "W3"}, {"candidate": "other"}):
            self.assertIsNone(run(dict(binding, **changed)).facts["missing_named"])
        self.assertIsNone(run(binding, evidence_work="W3").facts["missing_named"])
        self.assertIsNone(run(binding, evidence_oid="B").facts["missing_named"])
        self.assertIsNone(run(binding, source=1).facts["missing_named"])

    def test_rejected_kernel_closure_receipt_names_its_public_reasons_before_the_later_diagnosis(self):
        p = {"proposal_id": "P-20", "actor_id": "closure:study", "digest": "exact proposal"}
        receipt = {**p, "code": "closure_blocked", "disposition": "rejected",
                   "details": {"work": "W2", "reasons": [{"code": "missing_evidence", "refs": ["O-W2-retry"]}]}}
        closure = action("decide", "closure", "W2", "A", attempt=2, proposal=p)
        recovery = {"source_action": 2, "diagnosis": [RETRY], "receipt": receipt}
        for response in (closure, action("decide", "closure", "W2", "A", {"receipt": receipt}, attempt=2, proposal=p)):
            result = scorer.assess([fault(), submit(), response,
                                  state("W2", "A", "submitted", "submitted", evidence=recovery),
                                  review("W2", "A", SCOPE), *truth("A", [RETRY])])
            self.assertTrue(result.facts["caught"])
            self.assertEqual(result.facts["extra_diagnoses"], 0)
