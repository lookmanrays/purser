"""A single measured attempt. The outcome scorer runs after the clock has stopped."""

import json
import platform
import signal
import sqlite3
import sys
import tempfile
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from . import observer, scorer
from .arms import Plan, self_review
from .artifacts import FIXTURE, MANIFEST, START, Candidates, changes, encoded, extract_tree, git, read_artifact, sha
from .executor import LEAVES, ORDER, PARENT, PARENT_CHECKS, TASKS, fault_for, implement, inject, local_check
from .purser_arm import Treatment


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Cutoff(Exception):
    pass


class Journal:
    def __init__(self, arm, *, progress=None, released=True):
        self.arm, self.rows = arm, []
        self.progress, self.released = progress, released
        self.candidates, self.platform, self.initial_states = {}, {}, {}
        self.start, self.started = time.monotonic_ns(), utc()
        self.end = self.ended = None

    def release(self):
        self.start, self.started = time.monotonic_ns(), utc()
        self.released = True
        self.persist()

    def stop(self):
        if self.end is None:
            self.end, self.ended = (time.monotonic_ns(), utc()) if self.released else (self.start, self.started)

    def checkpoint(self):
        last, last_utc = self.end, self.ended
        if last is None and self.released:
            last, last_utc = time.monotonic_ns(), utc()
        basis = "before_release" if not self.released else "observed_lower_bound" if self.end is None else "exact"
        data = {"rows": self.rows, "candidates": self.candidates, "initial_states": self.initial_states,
                "platform": self.platform, "start_monotonic_ns": self.start if self.released else None,
                "start_utc": self.started if self.released else None,
                "last_monotonic_ns": last if self.released else None,
                "last_utc": last_utc if self.released else None, "cost_basis": basis}
        if self.end is not None and self.released:
            data.update(end_monotonic_ns=self.end, end_utc=self.ended)
        return data

    def persist(self):
        if self.progress is not None:
            self.progress(deepcopy(self.checkpoint()))

    def event(self, kind, payload):
        self.rows.append({"seq": len(self.rows) + 1, "offset_ns": time.monotonic_ns() - self.start,
                          "kind": kind, "payload": payload})
        self.persist()

    @property
    def steps(self):
        return sum(event["kind"] == "action" for event in self.rows)

    def action(self, kind, work, actor, oid, inputs, run):
        limits = MANIFEST["budgets"]["scripted"]
        number = self.steps + 1
        if number > limits["max_steps"]:
            raise Cutoff("step_limit")
        if time.monotonic_ns() - self.start >= limits["max_elapsed_s"] * 10**9:
            raise Cutoff("time_limit")
        info = {"action_id": f"action-{number}", "attempt": number, "action": kind, "subject": work,
                "candidate": oid, "actor": actor, "arm": self.arm, "input": inputs}
        self.event("action", info)
        try:
            answer = run()
        except Exception as error:
            info["return"] = {"failure": str(error)}
            self.persist()
            raise
        info["return"] = answer
        self.persist()
        return answer


def operational_definitions():
    here = Path(__file__).parent
    sources = {p: sha((here / p).read_bytes()) for p in ("observer.py", "probe.py", "measurement.py")}
    defs = {name: encoded({"criterion": name, "version": 1, "public_verifier": sources,
                           "fixture": FIXTURE["tree_sha1"]}) for name in scorer.LABELS}
    env = encoded({"python": sys.version, "platform": platform.platform(), "git": git(here, "--version").decode().strip()})
    return defs, env


def _record_states(rows, checkpoint, previous, emit, *, work=None, decision=None, recovered=False):
    for key, row in sorted(rows.items()):
        state = row["state"]
        if previous.get(key) == state and not (decision is not None and key == work):
            continue
        subject = row["candidate"]
        evidence = decision if key == work else None
        event = {"subject": key, "from": previous.get(key), "to": state,
                 "candidate": subject["digest"] if isinstance(subject, dict) else subject,
                 "checkpoint": checkpoint, "diagnosis": evidence["diagnosis"] if evidence else [],
                 "evidence": evidence, "effect_disposition": None}
        if recovered:
            event["recovery_read_utc"], event["transition_offset_ns"] = utc(), None
        emit("public_state", event)
        previous[key] = state


def _published(rows, initial):
    result = dict(initial)
    for entry in rows:
        if entry["kind"] == "public_state":
            public = entry["payload"]
            result[public["subject"]] = public["to"]
    return result


def _durable_diagnosis(rows, events, records=()):
    """An interrupted verdict is public only when the participant retained it for this subject."""
    action = next((e["payload"] for e in reversed(events) if e["kind"] == "action"), None)
    if not action or action["actor"] != "independent_verifier":
        return None, None
    number, work, oid = action["attempt"], action["subject"], action["candidate"]
    label, requested = action["input"].get("criterion"), action["input"].get("verdict")
    if requested not in ("fail", "inconclusive") or work not in rows or any(
        e["kind"] == "public_state" and (e["payload"].get("evidence") or {}).get("source_action") == number
        for e in events
    ):
        return None, None
    subject = rows[work]["candidate"]
    if (subject.get("digest") if isinstance(subject, dict) else subject) != oid:
        return None, None
    if records:
        runs = {r["fields"]["current_run"] for r in records if r["kind"] == "Obligation"
                and r["fields"]["work"] == work and r["fields"]["title"] == label}
        approved = any(r["kind"] == "Evidence" and r["fields"]["run"] in runs
                       and r["fields"]["verdict"] == requested for r in records)
    else:
        approved = rows[work].get("verified", {}).get(label) == requested
    return (work, {"diagnosis": [label], "source_action": number}) if approved else (None, None)


def _measure_oracles(repo, oid, checkout, seed, rank, labels, assets, refs, emit, issue):
    # Each independent result survives a later unavailable probe or artifact write.
    for label in labels:
        try:
            report = scorer.measure(repo, oid, checkout, seed, rank, labels=(label,))[label]
            ref = assets.put(report, "json")
            refs.append(ref)
            emit("oracle", {"candidate": oid, "criterion": label, "version": 1,
                            "expected": "all specified cases pass", "observed": report["verdict"],
                            "verdict": report["verdict"], "evidence": ref})
        except Exception as exc:
            issue(f"protected observation {oid} {label}", exc)


class Driver:
    def __init__(self, candidates, adapter, journal, store, seed, track):
        self.candidates, self.adapter, self.j, self.assets = candidates, adapter, journal, store
        self.seed, self.fault = seed, fault_for(seed, track)
        self.used_fault, self.previous = False, adapter.states()
        self.j.initial_states = dict(self.previous)
        self.seen = set()

    def call(self, action, work, actor, inputs, operation, oid=None):
        answer = self.j.action(action, work, actor, oid, inputs, operation)
        decision = None
        if actor == "closure":
            decision = answer
        elif actor == "plan_reviewer" and answer.get("missing"):
            decision = {"diagnosis": answer["missing"], "source_action": self.j.steps}
        elif actor == "independent_verifier" and answer["diagnosis"]:
            decision = {"diagnosis": answer["diagnosis"], "source_action": self.j.steps}
        if action in ("plan", "submit", "observe", "decide") or decision:
            self.capture(work, decision)
        return answer

    def capture(self, work, decision=None):
        rows, checkpoint = self.adapter.public_view()
        _record_states(rows, checkpoint, self.previous, self.j.event, work=work, decision=decision)

    def reconcile(self):
        self.previous = _published(self.j.rows, self.j.initial_states)
        rows, checkpoint = self.adapter.public_view()
        records = self.adapter.store.snapshot()["projection"]["records"] if isinstance(self.adapter, Treatment) else ()
        work, decision = _durable_diagnosis(rows, self.j.rows, records)
        _record_states(rows, checkpoint, self.previous, self.j.event, work=work, decision=decision, recovered=True)

    def one(self, work, repair):
        candidates, adapter = self.candidates, self.adapter
        root = candidates.work
        if repair:
            self.call("plan", work, "planner", {"repair": repair}, lambda: adapter.assign_again(work))
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
                        self.j.platform.setdefault("referenced_files", []).append(ref)
                        self.j.persist()
                        response = adapter.observe(work, label, report)
                        return {"witness": ref, "response": response}
                    observed = self.call("observe", work, "independent_observer", {"criterion": label}, observe, oid)
                    ref = observed["witness"]
                    report = read_artifact(self.assets.archive, ref)
                    measured = json.loads(report)
                    verdict = dict(measured, verdict=observer.verdict(measured, label, oid))
                    def verify():
                        reply = adapter.verify(work, label, verdict)
                        diagnosis = [label] if verdict["verdict"] in ("fail", "inconclusive") else []
                        return {**reply, "diagnosis": diagnosis}
                    self.call("decide", work, "independent_verifier",
                              {"criterion": label, "witness": ref, "verdict": verdict["verdict"]}, verify, oid)
        decision = self.call("decide", work, "closure", {"request": "close", "public_review": audit},
                             lambda: adapter.decide(work, audit), oid)
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


def reconcile_interrupted(directory, assets, arm, progress, *, seed=None, rank=None):
    """Read the stopped participant and independently score surviving Git objects. No worker is started."""
    if not progress or progress["cost_basis"] == "before_release":
        return progress
    saved = deepcopy(progress)
    journal = saved["rows"]
    facts = saved.setdefault("platform", {})
    refs = facts.setdefault("referenced_files", [])

    def emit(kind, payload):
        offset = max(journal[-1]["offset_ns"] if journal else 0, time.monotonic_ns() - saved["start_monotonic_ns"])
        journal.append({"seq": len(journal) + 1, "offset_ns": offset, "kind": kind, "payload": payload})

    def issue(stage, error):
        item = {"stage": stage, "error": f"{type(error).__name__}: {error}"}
        failures = facts.setdefault("recovery_issues", [])
        if item not in failures:
            failures.append(item)

    try:
        records = ()
        if arm == "purser":
            from purser.kernel.store import Store
            from purser.kernel.workview import WorkView
            snapshot = Store(Path(directory) / "kernel.sqlite").snapshot()
            records = snapshot["projection"]["records"]
            rows = {r["work"]: r for r in WorkView(snapshot["projection"]).answers()["q02"]
                    if r["work"] in (*ORDER, PARENT)}
            checkpoint = {"revision": snapshot["revision"], "head_hash": snapshot["head_hash"]}
            trace = {"snapshot": snapshot, "source": "interrupted_public_read"}
        else:
            raw = (Path(directory) / "plan.json").read_bytes()
            rows = json.loads(raw)
            checkpoint, trace = {"plan_sha256": sha(raw)}, {"plan": rows}
        previous = _published(journal, saved.get("initial_states", {}))
        work, decision = _durable_diagnosis(rows, journal, records)
        _record_states(rows, checkpoint, previous, emit, work=work, decision=decision, recovered=True)
        if not facts.get("treatment_trace"):
            facts["treatment_trace"] = assets.put(trace, "json")
            refs.append(facts["treatment_trace"])
    except Exception as exc:
        issue("public readback", exc)

    if seed is None or rank is None:
        return saved
    targets = set()
    for event in journal:
        p = event["payload"]
        if event["kind"] == "fault" or (event["kind"] == "public_state" and p["subject"] == PARENT
                                       and p["to"] in ("closed", "done")):
            oid = p.get("candidate")
            if isinstance(oid, str) and len(oid) == 40 and all(c in "0123456789abcdef" for c in oid):
                targets.add(oid)
            else:
                issue(f"event {event['seq']}", ValueError("missing immutable Git candidate"))
    measured = {(e["payload"]["candidate"], e["payload"]["criterion"]) for e in journal if e["kind"] == "oracle"}
    for oid in sorted(targets):
        missing = [label for label in scorer.LABELS if (oid, label) not in measured]
        if not missing:
            continue
        try:
            entry = saved["candidates"][oid]
            pack = read_artifact(assets.archive, entry["pack"])
            with tempfile.TemporaryDirectory(prefix="purser-e1-readback-") as tmp:
                repo = Path(tmp)
                git(repo, "init", "-q", "--bare", "--object-format=sha1")
                git(repo, "index-pack", "--stdin", data=pack)
                git(repo, "fsck", "--full", "--no-dangling", oid)
                git(repo, "merge-base", "--is-ancestor", START, oid)
                parents = git(repo, "rev-list", "--parents", "-n", "1", oid).decode().split()
                if (entry["start"] != START or git(repo, "rev-parse", oid+"^{tree}").decode().strip() != entry["tree"]
                        or parents != [oid] + ([entry["parent"]] if entry["parent"] else [])):
                    raise ValueError("retained Git identity disagrees with candidate map")
                with extract_tree(repo, oid) as tree:
                    _measure_oracles(repo, oid, Path(tree), seed, rank, missing, assets, refs, emit, issue)
        except Exception as exc:
            issue(oid, exc)
    return saved


def _run_arm(driver, journal):
    def expire(signum, frame):
        raise Cutoff("time_limit")
    previous = signal.signal(signal.SIGALRM, expire)
    try:
        journal.release()
        remaining = MANIFEST["budgets"]["scripted"]["max_elapsed_s"] - (time.monotonic_ns() - journal.start) / 1e9
        if remaining <= 0:
            return "time_limit"
        signal.setitimer(signal.ITIMER_REAL, remaining)
        try:
            return driver.execute()
        except Cutoff as exc:
            return str(exc)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        journal.stop()


def attempt(directory, assets, freeze, arm, track, seed, rank, *, progress=None):
    from .recovery import failure_record, input_identity
    journal = Journal(arm, progress=progress, released=False)
    metadata = journal.platform
    metadata.update({"definition_artifacts": [], "environment_artifact": None,
                     "referenced_files": [], "treatment_trace": None})
    adapter = driver = None
    errors = []
    terminal = None

    def issue(stage, error):
        errors.append(f"{stage}: {type(error).__name__}: {error}")

    try:
        journal.persist()
        candidates = Candidates(directory, assets)
        journal.candidates = candidates.entries
        journal.persist()
        definitions, environment = operational_definitions()
        for value in definitions.values():
            metadata["definition_artifacts"].append(assets.put(value, "bin"))
        metadata["environment_artifact"] = assets.put(environment, "bin")
        metadata.update({"os": platform.platform(), "python": sys.version, "sqlite": sqlite3.sqlite_version,
                         "git": git(directory, "--version").decode().strip()})
        adapter = Treatment(directory, definitions, environment) if arm == "purser" else Plan(directory)
        driver = Driver(candidates, adapter, journal, assets, seed, track)
        terminal = _run_arm(driver, journal)
    except Exception as exc:
        issue("trial", exc)
    finally:
        journal.stop()
    if terminal in ("done", "blocked", "idle") and journal.end - journal.start > MANIFEST["budgets"]["scripted"]["max_elapsed_s"] * 10**9:
        terminal = "time_limit"

    # Once the alarm is disarmed, a durable write can still be visible after its acknowledgment was lost.
    try:
        if driver is not None:
            driver.reconcile()
    except Exception as exc:
        issue("public readback", exc)
    try:
        if adapter is not None:
            metadata["treatment_trace"] = assets.put(adapter.retained(), "json")
            metadata["referenced_files"].append(metadata["treatment_trace"])
    except Exception as exc:
        issue("treatment retention", exc)
    try:
        metadata["referenced_files"].extend(e["pack"] for e in journal.candidates.values())
        if driver is not None:
            for oid in sorted(driver.seen):
                try:
                    with candidates.export(oid) as exported:
                        _measure_oracles(candidates.archive, oid, Path(exported), seed, rank, scorer.LABELS,
                                         assets, metadata["referenced_files"], journal.event, issue)
                except Exception as exc:
                    issue(f"protected observation {oid}", exc)
        if not errors:
            summary = scorer.score(journal.rows) if track == "primary" else None
            if terminal == "done" and track == "primary" and summary is None:
                raise ValueError("completed primary case lacks intervention or independent evidence")
            journal.event("terminal", {"end_reason": terminal, "last_observation": len(journal.rows) or None,
                                       "end_monotonic_ns": journal.end, "end_utc": journal.ended,
                                       "missing": None, "human_action": None})
            metadata.update({"start_utc": journal.started, "end_utc": journal.ended, "start_monotonic_ns": journal.start,
                             "end_monotonic_ns": journal.end, "budget_reason": terminal, "failure": None,
                             "cost_basis": "exact"})
            return {"schema_version": 1, "suite": MANIFEST["id"], "experiment": "E1", "track": track,
                    "seed": seed, "arm": arm, "mode": "scripted", "replicate": freeze.get("replicate", 1),
                    "inputs": input_identity(freeze, arm),
                    "artifacts": {"journal": assets.put(journal.rows, "json"), "candidates": assets.put(journal.candidates, "json"),
                                  "platform": assets.put(metadata, "json"), "model": None},
                    "terminal": terminal, "validity": "complete" if terminal == "done" else "arm_incomplete",
                    "cost": {"steps": journal.steps, "elapsed_ns": journal.end-journal.start,
                             "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "spend_usd_micros": 0}, "score": summary}
    except Exception as exc:
        issue("measurement or finalization", exc)
    return failure_record(assets, freeze, arm, track, seed, rank, reason=" | ".join(errors), progress=journal.checkpoint())
