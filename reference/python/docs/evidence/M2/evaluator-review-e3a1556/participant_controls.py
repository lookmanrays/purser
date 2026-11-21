"""Private controlled participant releases on the exact pre-registration evaluator."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
from experiments.e1 import run, suite, report
from experiments.e1.artifacts import encoded, sha

directory = Path(sys.argv[1])
pin = {**suite.source_pin(), "replicate": 81, "replication_reason": "Private evaluator qualification"}
assert pin["source_commit"] == "e3a155633058cd20274ce5e9cfaca224b41f5b5b"
units = [("primary", 1002, 2, "loop"), ("primary", 1002, 2, "loop_equal_verifier"),
         ("primary", 1003, 3, "purser"), ("clean", 1001, 1, "loop"),
         ("clean", 1001, 1, "purser"), ("clean", 1001, 1, "loop_equal_verifier")]
registration = encoded(pin)
(directory / "freeze.json").write_bytes(registration)
original_review = run.self_review
original_decide = run.Plan.decide
original_unlink = Path.unlink

def controlled_attempt(workdir, assets, freeze, arm, track, seed, rank, *, progress):
    wrong_released = False
    parent_refused = False
    def review(repo, oid, root, work):
        nonlocal wrong_released
        actual = original_review(repo, oid, root, work)
        if (arm, seed, work) == ("loop", 1002, "W3") and not wrong_released:
            wrong_released = True
            return dict(actual, missing=["W3/delivery"])
        return actual
    def unlink(path, *args, **kwargs):
        if arm == "loop_equal_verifier" and seed == 1002 and path == workdir / "worker/notes/seed-1002.txt":
            return
        return original_unlink(path, *args, **kwargs)
    def decide(plan, work, audit):
        nonlocal parent_refused
        if (arm, seed, work) == ("loop_equal_verifier", 1002, "W-notify"):
            if parent_refused:
                plan.update(work, state="done", diagnosis=audit["missing"])
                return {"diagnosis": audit["missing"], "claim": plan.context(work)}
            parent_refused = True
        return original_decide(plan, work, audit)
    with patch.object(run, "self_review", review), patch.object(run.Plan, "decide", decide), patch.object(Path, "unlink", unlink):
        return run.attempt(workdir, assets, freeze, arm, track, seed, rank, progress=progress)

suite.collect_cases(directory, pin, registration, units, controlled_attempt)
series = report.inspect_series(directory)
wrong = series["primary",1002,"loop"]
late = series["primary",1002,"loop_equal_verifier"]
assert wrong.comparable() and wrong.evidence.facts == {
    "false_parent_closure":False,"missing_named":False,"caught":False,"correct_parent_closure":True,"extra_diagnoses":1}
assert late.comparable() and late.evidence.facts == {
    "false_parent_closure":True,"missing_named":True,"caught":False,"correct_parent_closure":False,"extra_diagnoses":0}
assert [series["clean",1001,a].raw["cost"]["steps"] for a in suite.ARMS] == [24,42,42]
text = report.render(directory)
(directory / "report.md").write_text(text)
checked = report.verify_candidates(directory)
summary = {"candidate":pin["source_commit"],"wrong_first":wrong.evidence.facts,"failed_repair":late.evidence.facts,
           "reexecuted":checked,"raw_sha256":sha((directory/"raw.zip").read_bytes()),"report_sha256":sha(text.encode())}
(directory / "qualification.json").write_bytes(encoded(summary))
print(json.dumps(summary, indent=2))
