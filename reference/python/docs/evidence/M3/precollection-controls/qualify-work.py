"""Private source qualification. Run inside the candidate clone with a committed replicate > 1."""
from contextlib import ExitStack
from copy import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

from experiments.e1.artifacts import encoded, sha
from experiments.e1.executor import PARENT
from experiments.e2 import policy, suite
from experiments.e2.report import inspect_series


def child(code, value, *args):
    return subprocess.run([sys.executable, "-I", "-B", *code, *args], input=encoded(value),
                          capture_output=True, check=True, timeout=10).stdout


def run(folder, mode):
    freeze = json.loads((folder / "freeze.json").read_bytes())
    assert freeze["replicate"] > 1 and suite.validate_freeze(freeze) == freeze["replicate"]
    observations = {}
    with zipfile.ZipFile(HERE / "independent-context-handoff.zip") as archive:
        worker_source = archive.read("scripts/context_worker.py")
    worker = folder / "original-context-worker.py"
    worker.write_bytes(worker_source)

    def extras(arm, message):
        driver = arm.work_actions.driver
        alias = copy(arm)
        seen = observations.setdefault(driver.j.arm, [])
        before = driver.j.steps
        original_head = arm.public_view()[1]
        prior = {work: alias.candidate_id(work) for work in ("W1", "W3")}
        assert driver.j.steps == before

        def decision(packet, witness):
            subject = packet["work"]["id"]
            request = {"role": "executor", "subject": subject, "ordinal": len(seen) + 1}
            answer = json.loads(child([str(worker)], {"request": request, "context": packet, "notice": message}))
            assert answer["request"] == request and answer["subject"] == subject
            assert answer["response_sha256"] == sha(encoded(packet)) and answer["prior_candidate"] == prior[subject]
            assert answer["work_disposition"] == "retain-submission"
            witness.received(subject, packet)
            seen.append({"attempt": witness.attempt, "source": arm.public_view()[1], "receiver": answer})
            return answer

        if mode in ("bound", "controller"):
            for subject in ("W1", "W3", "W1"):
                if mode == "bound":
                    alias.context(subject, receiver=decision)
                else:
                    driver.call("context", subject, "executor", {"work": subject},
                                lambda: alias.context(subject, receiver=decision))
        elif mode == "handoff":
            def closed(packet, witness):
                read, write = os.pipe()
                os.close(read)
                try:
                    # The first atomic byte cannot transfer to a closed reader.
                    os.write(write, b"x")
                except BrokenPipeError:
                    witness.not_delivered()
                    seen.append({"attempt": witness.attempt, "outcome": "EPIPE before transfer"})
                    return {"disposition": "failed-before-write"}
                finally:
                    os.close(write)
                raise AssertionError("closed reader unexpectedly accepted bytes")

            def elsewhere(packet, witness):
                # An independent fixture selects real W3 bytes at this same original head.
                actual = arm._context_from_publication("W3")[2]
                source = ("import hashlib,json,sys; data=sys.stdin.buffer.read(); "
                          "w=json.loads(data)['work']['id']; "
                          "print(json.dumps([w,hashlib.sha256(data).hexdigest(),w==sys.argv[1]]))")
                subject, digest, accepted = json.loads(child(["-c", source], actual, "W1"))
                assert not accepted and subject == "W3" and digest == sha(encoded(actual))
                witness.received(subject, actual)
                seen.append({"attempt": witness.attempt, "outcome": "mismatched packet rejected",
                             "received_subject": subject, "received_sha256": digest})
                return {"accepted": accepted}

            def unacknowledged(packet, witness):
                answer = child(["-c", "import json,sys; json.load(sys.stdin)"], packet)
                assert answer == b""
                seen.append({"attempt": witness.attempt, "outcome": "EOF without receipt"})
                return {"acknowledgement": None}

            for receive in (decision, closed, elsewhere, unacknowledged):
                alias.context("W1", receiver=receive)
        elif mode == "tools":
            tools = copy(driver.tools)
            results = []
            real_check = policy.worker_check
            def capture_check(*args):
                result = real_check(*args)
                results.append(result["legacy"])
                return result
            with patch.object(policy, "worker_check", capture_check):
                assert tools.check("W1", policy.LINEAR, candidate=prior["W1"], pass_only=True) is True
            assert len(results) == 1 and results[0]["tests"] == 28
            seen.append({"attempt": driver.j.steps, "tool": "worker_check", "subject": "W1",
                         "candidate": prior["W1"], "legacy": results[0], "returned": True})
            queries = [("plan", "W1"), ("git", "W1"), ("git_history", "W1"), ("git_diff", "W3")]
            if isinstance(arm, policy.KernelArm):
                queries += [("status", PARENT), ("why", "W1"), ("frontier", PARENT), ("replay", PARENT)]
            for tool, subject in queries:
                answer = tools.inspect(tool, subject)
                assert answer is not None
                seen.append({"attempt": driver.j.steps, "tool": tool, "subject": subject,
                             "received_sha256": sha(encoded(answer))})
        else:
            raise ValueError(mode)
        assert driver.j.steps - before == len(seen) and original_head == arm.public_view()[1]

    def extend(implementation):
        def revise(arm, message):
            response = implementation(arm, message)
            extras(arm, message)
            return response
        return revise

    units = [u for u in suite.schedule() if u[0] == "primary" and u[2] == 1]
    if mode == "handoff":
        units = [u for u in units if u[3] == "loop"]
    with ExitStack() as patches:
        for cls in (policy.PlanArm, policy.KernelArm):
            patches.enter_context(patch.object(cls, "revise", extend(cls.revise)))
        suite.collect_units(folder, units)

    cases, stats = inspect_series(folder, strict=True, rerun=True)
    output = {"source": freeze["source_commit"], "replicate": freeze["replicate"],
              "worker_sha256": sha(worker_source), "mode": mode, "stats": stats, "cases": {}}
    for (track, seed, arm), case in cases.items():
        seen = observations[arm]
        ordinary, rework = (30, 13) if arm == "loop" else (48, 25)
        assert case.raw["terminal"] == "done" and case.facts["final_pass"] is True and not case.issues
        assert case.raw["cost"]["steps"] == ordinary + len(seen)
        assert case.facts["rework_steps"] == rework + len(seen)
        assert case.facts["unaffected_both_retained"] is True
        with zipfile.ZipFile(folder / "raw.zip") as archive:
            journal = json.loads(archive.read(case.raw["artifacts"]["journal"]["path"]))
        actions = {e["payload"]["attempt"]: e["payload"] for e in journal if e["kind"] == "action"}
        selected = [actions[s["attempt"]] for s in seen]
        assert all(actions[a["parent_attempt"]]["subject"] == "W2" for a in selected)
        reads = {row["attempt"]: row for row in case.facts["received_contexts"]}
        receipts = [reads[a["attempt"]] for a in selected if a["attempt"] in reads]
        if mode in ("bound", "controller"):
            assert [r["received_subject"] for r in receipts] == ["W1", "W3", "W1"]
            assert all(r["intended_received"] is True for r in receipts)
            assert len({encoded(a["context_source"]) for a in selected}) == 1
            assert selected[0]["prepared_context"] == selected[2]["prepared_context"]
        if mode == "handoff":
            assert len({a["prepared_context"]["sha256"] for a in selected}) == 1
            assert [r["received"] for r in receipts] == [True, False, True, None]
            assert [r["intended_received"] for r in receipts] == [True, False, False, None]
            assert receipts[2]["received_subject"] == "W3" and case.facts["complete_read_exposure"] is False
        output["cases"][arm] = {"steps": case.raw["cost"]["steps"], "rework": case.facts["rework_steps"],
                                "final_pass": case.facts["final_pass"], "work": seen, "receipts": receipts}
    output["raw_sha256"] = sha((folder / "raw.zip").read_bytes())
    (folder / "work-evidence.json").write_text(json.dumps(output, sort_keys=True, indent=2) + "\n")
    print(json.dumps({arm: [e["steps"], e["rework"]] for arm, e in output["cases"].items()}), flush=True)


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve(), sys.argv[2])
