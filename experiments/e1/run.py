"""A single measured attempt. The outcome scorer runs after the clock has stopped."""

import json
import platform
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import observer, scorer
from .arms import Plan, self_review
from .artifacts import FIXTURE, MANIFEST, START, Candidates, changes, encoded, git, sha
from .executor import LEAVES, ORDER, PARENT, PARENT_CHECKS, TASKS, fault_for, implement, inject, local_check
from .purser_arm import Treatment


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Cutoff(Exception):
    pass


class Journal:
    def __init__(self, arm):
        self.arm, self.rows, self.steps = arm, [], 0
        self.start = time.monotonic_ns()
        self.started = utc()

    def event(self, kind, payload):
        self.rows.append({"seq": len(self.rows) + 1, "offset_ns": time.monotonic_ns() - self.start,
                          "kind": kind, "payload": payload})

    def action(self, kind, work, actor, oid, inputs, run):
        limits = MANIFEST["budgets"]["scripted"]
        if self.steps >= limits["max_steps"]:
            raise Cutoff("step_limit")
        if time.monotonic_ns() - self.start >= limits["max_elapsed_s"] * 10**9:
            raise Cutoff("time_limit")
        self.steps += 1
        info = {"action_id": f"action-{self.steps}", "attempt": self.steps, "action": kind, "subject": work,
                "candidate": oid, "actor": actor, "arm": self.arm, "input": inputs}
        self.event("action", info)
        try:
            answer = run()
        except Exception as error:
            info["return"] = {"failure": str(error)}
            raise
        info["return"] = answer
        return answer


def operational_definitions():
    here = Path(__file__).parent
    sources = {p: sha((here / p).read_bytes()) for p in ("observer.py", "probe.py", "measurement.py")}
    defs = {name: encoded({"criterion": name, "version": 1, "public_verifier": sources,
                           "fixture": FIXTURE["tree_sha1"]}) for name in scorer.LABELS}
    env = encoded({"python": sys.version, "platform": platform.platform(), "git": git(here, "--version").decode().strip()})
    return defs, env


class Driver:
    def __init__(self, candidates, adapter, journal, store, seed, track):
        self.candidates, self.adapter, self.j, self.assets = candidates, adapter, journal, store
        self.seed, self.fault = seed, fault_for(seed, track)
        self.used_fault, self.previous = False, adapter.states()
        self.witnesses = []
        self.seen = set()

    def call(self, action, work, actor, inputs, operation, oid=None):
        return self.j.action(action, work, actor, oid, inputs, operation)

    def capture(self, work, oid, decision=None):
        rows, checkpoint = self.adapter.public_view()
        states = {key: row["state"] for key, row in rows.items()}
        for key, state in states.items():
            if self.previous.get(key) != state or (decision is not None and key == work):
                self.j.event("public_state", {
                    "subject": key, "from": self.previous.get(key), "to": state,
                    "candidate": rows[key]["candidate"]["digest"] if isinstance(rows[key]["candidate"], dict)
                                 else rows[key]["candidate"], "checkpoint": checkpoint,
                    "diagnosis": decision["diagnosis"] if key == work and decision else [],
                    "evidence": decision if key == work and decision else None, "effect_disposition": None,
                })
        self.previous = states

    def one(self, work, repair):
        candidates, adapter = self.candidates, self.adapter
        root = candidates.work
        if repair:
            self.call("plan", work, "planner", {"repair": repair}, lambda: adapter.assign_again(work))
            self.capture(work, candidates.head)
        context = self.call("context", work, "executor", {"work": work}, lambda: adapter.context(work))
        released = context["work"]
        if released["id"] != work or released["title"] != TASKS[work]:
            raise ValueError("released assignment does not match the common task")
        fault = self.fault if not self.used_fault and self.fault and self.fault[1] == work else None
        if work in LEAVES:
            def edit():
                implement(root, released["id"])
                return {"edited": work}
            self.call("edit", work, "executor", {"repair": repair, "work": work}, edit)
        elif repair:
            def tidy():
                paths = [e["path"] for e in changes(candidates.archive, candidates.head)
                         if e["path"] not in FIXTURE["public_paths"]]
                for path in paths:
                    (root / path).unlink()
                return {"removed": paths}
            self.call("edit", work, "executor", {"repair": repair, "work": work}, tidy)
        skip = bool(fault and fault[0] == "skipped_retry_check")
        checked = self.call("check", work, "executor", {"check": "public task checks"},
                            lambda: local_check(root, work, skip=skip))
        claim = "done, tests pass" if fault else checked["worker_claim"]
        if fault:
            inject(root, fault, self.seed)
            self.used_fault = True
        oid = candidates.commit(f"Notification work {work}")
        self.seen.add(oid)
        if fault:
            self.j.event("fault", {"seed": self.seed, "intervention": fault[0], "target": fault[2],
                                   "candidate": oid, "trigger": f"first submission of {work}",
                                   "work": work, "released": claim})
        self.call("submit", work, "executor", {"claim": claim, "candidate": oid},
                  lambda: adapter.submit(work, oid), oid)
        self.capture(work, oid)
        with candidates.export(oid) as archive_dir:
            checkout = Path(archive_dir)
            audit = self.call("check", work, "plan_reviewer", {"check": "post-submit public self-review"},
                              lambda: self_review(candidates.archive, oid, checkout, work), oid)
            if self.j.arm != "loop":
                for label in (LEAVES[work],) if work in LEAVES else PARENT_CHECKS:
                    self.call("plan", work, "planner", {"open_run": label}, lambda: adapter.opening(work, label), oid)
                    def observe():
                        report = observer.inspect(candidates.archive, oid, checkout, label)
                        ref = self.assets.put(report, "json")
                        self.witnesses.append(ref)
                        response = adapter.observe(work, label, report)
                        return {"witness": ref, "response": response}
                    self.call("observe", work, "independent_observer", {"criterion": label}, observe, oid)
                    report = self.assets.archive.read(self.witnesses[-1]["path"])
                    measured = json.loads(report)
                    verdict = dict(measured, verdict=observer.verdict(measured, label, oid))
                    self.call("decide", work, "independent_verifier", {"criterion": label, "witness": self.witnesses[-1]},
                              lambda: adapter.verify(work, label, verdict), oid)
                    self.capture(work, oid)
        decision = self.call("decide", work, "closure", {"request": "close", "public_review": audit},
                             lambda: adapter.decide(work, audit), oid)
        self.capture(work, oid, decision)
        return self.previous[work] == "closed", decision["diagnosis"]

    def execute(self):
        self.call("plan", PARENT, "planner", {"schedule": list(ORDER)}, lambda: {"work_order": list(ORDER)})
        for work in (*ORDER, PARENT):
            done, diagnosis = self.one(work, [])
            if not done and diagnosis:
                done, _ = self.one(work, diagnosis)
            if not done:
                return "blocked"
        return "done"


def attempt(directory, assets, freeze, arm, track, seed, rank):
    candidates = Candidates(directory, assets)
    definitions, environment = operational_definitions()
    adapter = None
    journal = None
    driver = None
    failure = None
    try:
        adapter = (Treatment(directory, definitions, environment) if arm == "purser" else Plan(directory))
        journal = Journal(arm)
        driver = Driver(candidates, adapter, journal, assets, seed, track)
        def expire(signum, frame):
            raise Cutoff("time_limit")
        previous_handler = signal.signal(signal.SIGALRM, expire)
        try:
            remaining = MANIFEST["budgets"]["scripted"]["max_elapsed_s"] - (time.monotonic_ns() - journal.start) / 1e9
            if remaining <= 0:
                raise Cutoff("time_limit")
            signal.setitimer(signal.ITIMER_REAL, remaining)
            terminal = driver.execute()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
    except Cutoff as error:
        terminal = str(error)
    except Exception as error:
        terminal, failure = "harness_failure", f"{type(error).__name__}: {error}"
    if journal is None:
        journal = Journal(arm)
    end, ended = time.monotonic_ns(), utc()
    if terminal == "done" and end - journal.start > MANIFEST["budgets"]["scripted"]["max_elapsed_s"] * 10**9:
        terminal = "time_limit"
    journal.event("terminal", {"end_reason": terminal, "last_observation": journal.steps,
                               "missing": failure, "human_action": None})
    witness = []
    try:
        for oid in sorted(driver.seen if driver else []):
            with candidates.export(oid) as exported:
                values = scorer.measure(candidates.archive, oid, Path(exported), seed, rank)
            for label, report in values.items():
                ref = assets.put(report, "json")
                witness.append(ref)
                journal.event("oracle", {"candidate": oid, "criterion": label, "version": 1,
                              "expected": "all specified cases pass", "observed": report["verdict"],
                              "verdict": report["verdict"], "evidence": ref})
        summary = scorer.score(journal.rows) if terminal != "harness_failure" and track == "primary" else None
    except Exception as error:
        terminal, failure, summary = "harness_failure", str(error), None
        journal.event("terminal", {"end_reason": terminal, "last_observation": journal.steps,
                                   "missing": failure, "human_action": None})
    retained = assets.put(adapter.retained(), "json") if adapter is not None else None
    public = (driver.witnesses if driver else [])
    platform_record = {
        "os": platform.platform(), "python": sys.version, "sqlite": sqlite3.sqlite_version,
        "git": git(directory, "--version").decode().strip(), "start_utc": journal.started, "end_utc": ended,
        "start_monotonic_ns": journal.start, "end_monotonic_ns": end, "budget_reason": terminal, "failure": failure,
        "treatment_trace": retained, "definition_artifacts": [assets.put(v, "bin") for v in definitions.values()],
        "environment_artifact": assets.put(environment, "bin"),
        "referenced_files": witness + public + [item["pack"] for item in candidates.entries.values()]
                            + ([retained] if retained else []),
    }
    inputs = {key: freeze[key] for key in ("fixture_tree","fixture_commit","manifest_sha256","protocol_sha256",
                                           "raw_schema_sha256","scorer_sha256","fault_driver_sha256","executor_sha256")}
    return {"schema_version": 1, "suite": MANIFEST["id"], "experiment": "E1", "track": track,
            "seed": seed, "arm": arm, "mode": "scripted", "replicate": 1,
            "inputs": dict(inputs, treatment_sha256=freeze["treatments"][arm], worktree_head=freeze["source_commit"]),
            "artifacts": {"journal": assets.put(journal.rows, "json"), "candidates": assets.put(candidates.entries, "json"),
                          "platform": assets.put(platform_record, "json"), "model": None},
            "terminal": terminal, "validity": "infrastructure_invalid" if failure else
                                         ("complete" if terminal == "done" else "arm_incomplete"),
            "cost": {"steps": journal.steps,"elapsed_ns":end-journal.start,"model_calls":0,"input_tokens":0,
                     "output_tokens":0,"spend_usd_micros":0}, "score": summary}
