"""Adversarial observations and incomplete archives at the E1 public boundaries."""

import json
import subprocess
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from experiments.e1 import measurement, observer, report, scorer
from experiments.e1.artifacts import ROOT, START, Artifacts, Candidates, encoded
from experiments.e1.executor import implement


def event(kind, **payload):
    return {"kind": kind, "payload": payload}


def truth(oid, failures=()):
    return [event("oracle", candidate=oid, criterion=label, verdict="fail" if label in failures else "pass")
            for label in scorer.LABELS]


def closed(oid):
    return event("public_state", subject="W-notify", candidate=oid, diagnosis=[], **{"from":"open","to":"closed"})


def fault(oid):
    return event("fault", candidate=oid, target="W2/retry", seed=1001, intervention="skipped_retry_check")


def submitted(oid):
    return event("action", action="submit", candidate=oid, input={}, **{"return":{"state":"submitted"}})


class MeasurementIntegrity(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        z = zipfile.ZipFile(self.root / "unit.zip", "w")
        self.addCleanup(z.close)
        self.candidates = Candidates(self.root, Artifacts(z))
        for work in ("W1", "W2", "W3"):
            implement(self.candidates.work, work)

    def test_fractional_positive_and_negative_offsets_are_exact_in_real_git_candidate(self):
        c = self.candidates
        retry = c.work / "notify/retry.py"
        retry.write_text('def delays(attempts, base):\n'
                         '    return (-0.000001,) + tuple(k*base+0.000001 for k in range(1,attempts))\n')
        oid = c.commit("Exact-time measurement component fixture")
        with c.export(oid) as loc:
            location = Path(loc)
            spec = {"channel":"inbox","ids":["01","1"],"disabled":[],"attempts":2,"base":2,"times":[0,2,3]}
            data = measurement.probe(location, cases=[spec])[0]
            self.assertEqual(data["samples"], [[["01","inbox",-1],["1","inbox",-1]],[],
                                               [["01","inbox",2000001],["1","inbox",2000001]]])
            checked = scorer.measure(c.archive, oid, location, 912345, 4)
            self.assertEqual(checked["W2/retry"]["verdict"], "fail")
            observed = observer.inspect(c.archive, oid, location, "W2/retry")
            self.assertEqual(observer.verdict(observed,"W2/retry",oid), "fail")

    def test_half_second_early_first_attempt_fails_even_when_all_sample_counts_match(self):
        c = self.candidates
        (c.work / "notify/retry.py").write_text(
            "def delays(attempts, base):\n    return (-0.5,) + tuple(k*base for k in range(1,attempts))\n")
        oid = c.commit("Early delivery component fixture")
        with c.export(oid) as loc:
            measured = scorer.measure(c.archive, oid, Path(loc), 912345, 4)["W2/retry"]
        self.assertEqual(measured["verdict"], "fail")
        for case in measured["cases"]:
            expected, actual = case["expected"], case["observed"]
            self.assertEqual((expected["enqueued"], expected["pending"]), (actual["enqueued"], actual["pending"]))
            self.assertEqual([len(batch) for batch in expected["samples"]], [len(batch) for batch in actual["samples"]])
            self.assertEqual(actual["samples"][0][0][2], -500000)

    def test_wrong_typed_values_do_not_compare_equal_to_correct_python_values(self):
        c = self.candidates
        service = c.work / "notify/service.py"
        service.write_text(service.read_text().replace("return count", "return bool(count)")
                           .replace("Delivery(event_id, channel, due_at)", "Delivery(True, 1, due_at)"))
        oid = c.commit("Type measurement component fixture")
        with c.export(oid) as loc:
            actual = measurement.probe(Path(loc), cases=[{"channel":"1","ids":["1"],"disabled":[],
                                                          "attempts":1,"base":1,"times":[0]}])[0]
            self.assertEqual(actual["enqueued"]["invalid_type"], "bool")
            self.assertEqual([v["invalid_type"] for v in actual["samples"][0][0][:2]], ["bool","int"])
        self.assertFalse(measurement.same(True, 1))
        self.assertFalse(measurement.same(1.0, 1))

    def test_broken_candidate_is_observed_and_missing_instrumentation_is_unavailable(self):
        c = self.candidates
        (c.work / "notify/retry.py").write_bytes(b"invalid<<python?\n")
        oid = c.commit("Unimportable component fixture")
        with c.export(oid) as loc:
            location = Path(loc)
            obs = measurement.probe(location, cases=[{}])
            self.assertEqual(obs[0]["candidate_exception"], "SyntaxError")
            with patch.object(measurement, "REGISTRATION", self.root/"missing"):
                with self.assertRaises(measurement.MeasurementUnavailable):
                    measurement.probe(location, legacy=True)
            with patch.object(measurement.sys, "executable", str(self.root/"not-an-interpreter")):
                with self.assertRaises(measurement.MeasurementUnavailable):
                    measurement.probe(location, cases=[{}])
            fake = subprocess.CompletedProcess([], 0, b'{"measured":[]}', b"")
            with patch.object(measurement.subprocess, "run", return_value=fake):
                with self.assertRaises(measurement.MeasurementUnavailable):
                    measurement.probe(location, cases=[{}])

    def test_first_public_diagnosis_wins_and_pre_repair_false_closure_stays(self):
        original = [fault("A"), submitted("A"),
                    event("action", action="check", input={}, candidate="A",
                          **{"return":{"missing":["scope/paths","unrelated"]}}),
                    event("public_state", candidate="A", subject="W2", diagnosis=["W2/retry"],
                          **{"from":"submitted","to":"submitted"}), closed("A"),
                    event("action", action="edit", input={"repair":["W2/retry"]}, **{"return":{"edited":"W2"}}),
                    closed("B"), *truth("A", ["W2/retry"]), *truth("B")]
        scored = scorer.score(original)
        self.assertTrue(scored["false_parent_closure"] and scored["correct_parent_closure"])
        self.assertFalse(scored["missing_named"] or scored["caught"])
        self.assertEqual(scored["extra_diagnoses"], 2)
        first = original[2]["payload"]["return"]
        first["missing"] = ["W2/retry","scope/paths"]
        scored = scorer.score(original)
        self.assertTrue(scored["missing_named"])
        self.assertFalse(scored["caught"])
        self.assertEqual(scored["extra_diagnoses"], 1)

    def test_unexposed_primary_and_partial_evidence_cannot_earn_an_observed_pass(self):
        self.assertIsNone(scorer.score([fault("A"), *truth("A", ["W2/retry"])]))
        rows = [fault("A"), submitted("A"), closed("A"), *truth("A", ["W2/retry"])[:-1]]
        self.assertIsNone(scorer.score(rows))
        self.assertEqual(scorer.assess(rows).false_candidates, ["A"])
        self.assertFalse(scorer.assess(rows).correct_candidates)
        rows = [fault("A"), submitted("A"), closed("A"), *truth("A")[:-1]]
        self.assertFalse(scorer.assess(rows).correct_candidates)
        self.assertIsNone(scorer.score(rows))
        with self.assertRaises(ValueError):
            scorer.assess([*truth("A"), *truth("A")])

    def test_protected_report_cannot_replace_its_expected_result_or_remove_cases(self):
        c = self.candidates
        oid = c.commit("Protocol expectation component fixture")
        with c.export(oid) as loc:
            r = scorer.measure(c.archive, oid, Path(loc), 912345, 4)["W2/retry"]
        self.assertEqual(scorer.verify_report(r,oid,"W2/retry",912345,4),"pass")
        r["cases"][0]["expected"]["enqueued"] = 77
        with self.assertRaises(ValueError):
            scorer.verify_report(r,oid,"W2/retry",912345,4)
        r["cases"] = []
        with self.assertRaises(ValueError):
            scorer.verify_report(r,oid,"W2/retry",912345,4)


class ReportIntegrity(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        original = ROOT/"experiments/results/e1-scripted-v1"
        self.freeze = json.loads((original/"freeze.json").read_bytes())
        with zipfile.ZipFile(original/"raw.zip") as archive:
            self.raw = json.loads(archive.read("runs/primary/1001/loop.json"))
        self.raw.update(score=None,terminal="harness_failure",validity="infrastructure_invalid")
        self.raw["cost"].update(steps=0,elapsed_ns=0)
        self.raw["replicate"] = self.freeze["replicate"] = 2
        self.freeze["replication_reason"] = "component qualification only"

    def example(self, *, duplicate=False):
        with zipfile.ZipFile(self.root/"raw.zip","w") as archive:
            archive.writestr("freeze.json",encoded(self.freeze))
            a = Artifacts(archive)
            self.raw["artifacts"] = {
                "journal":a.put([], "json"), "candidates":a.put({}, "json"), "model":None,
                "platform":a.put({"referenced_files":[],"definition_artifacts":[],"environment_artifact":None,
                                  "treatment_trace":None,"start_monotonic_ns":None,"end_monotonic_ns":None,
                                  "cost_basis":"before_release"}, "json")}
            key = "primary/1001/loop.json"
            archive.writestr("started/"+key, encoded({"track":"primary","seed":1001,"rank":1,"arm":"loop"}))
            archive.writestr("runs/"+key, encoded(self.raw))
            if duplicate:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr("runs/"+key, encoded(self.raw))

    def test_archive_alone_represents_unreleased_case_and_verifies_identity(self):
        self.example()
        result = report.inspect_series(self.root)
        self.assertFalse(result[("primary",1001,"loop")].comparable())
        for field, bad in (("experiment","E3"),("suite","another"),("replicate",3)):
            with self.subTest(field=field):
                original = self.raw[field]
                self.raw[field] = bad
                self.example()
                with self.assertRaises(ValueError):
                    report.inspect_series(self.root)
                self.raw[field] = original
        self.raw["inputs"]["scorer_sha256"] = "0"*64
        self.example()
        with self.assertRaises(ValueError):
            report.inspect_series(self.root)

    def test_duplicate_raw_and_changed_frozen_source_rejected(self):
        self.example(duplicate=True)
        with self.assertRaisesRegex(ValueError,"duplicate ZIP"):
            report.inspect_series(self.root)
        self.freeze["source_files"]["experiments/e1/scorer.py"] = "0"*64
        self.example()
        with self.assertRaisesRegex(ValueError,"source files"):
            report.inspect_series(self.root)

    def test_interrupted_false_closure_visible_and_invalid_pair_has_no_cost_conclusion(self):
        known = scorer.assess([fault("A"), submitted("A"), closed("A"), *truth("A", ["W2/retry"])])
        interrupted = report.Case(dict(self.raw, validity="arm_incomplete", terminal="time_limit"),known,"exact")
        clean_good = report.Case(dict(self.raw, track="clean",validity="complete",terminal="done"),
                                 scorer.assess([closed("B"),*truth("B")]),"exact")
        clean_bad = report.Case(dict(self.raw,track="clean"),scorer.assess([]),"before_release")
        cases = {("primary",1001,"purser"):interrupted,("clean",1001,"purser"):clean_good,
                 ("clean",1001,"loop"):clean_bad}
        with patch.object(report,"inspect_series",return_value=cases):
            text = report.render(self.root)
        self.assertIn("| purser | 0 | 1 | 19 | 1 |",text)
        self.assertIn("false close, arm_incomplete",text)
        self.assertIn("Purser was **missed**",text)
        self.assertIn("clean step-overhead comparison is inconclusive",text)
