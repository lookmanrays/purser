"""Reconcile the scripted participant's public observations and measured exit."""

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from purser.kernel.projector import apply_event, empty_projection
from purser.kernel.store import _batches
from purser.kernel.wire import KernelError, digest as kernel_digest
from purser.kernel.workview import WorkView

from .artifacts import MANIFEST, encoded, sha
from .capture import durable_decision, returned_decision
from .executor import ORDER, PARENT
from .measurement import same
from .outcomes import CLOSED


WORK = (*ORDER, PARENT)
EXITS = {"done": "complete", "blocked": "arm_incomplete", "idle": "arm_incomplete",
         "step_limit": "arm_incomplete", "time_limit": "arm_incomplete",
         "harness_failure": "infrastructure_invalid"}
ACTIONS = {"plan": ("planner",), "context": ("executor",), "edit": ("executor",),
           "check": ("executor", "plan_reviewer"), "submit": ("executor",),
           "observe": ("independent_observer",), "decide": ("independent_verifier", "closure")}


def validity_for(reason):
    if reason not in EXITS:
        raise ValueError("unknown scripted exit")
    return EXITS[reason]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value, length):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def utc_time(value):
    if type(value) is not str:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.utcoffset() == timedelta(0) else None


def check_exit(raw, journal, platform, modern, issue, *, duration=None):
    """Keep the raw identities for diagnostics. Only a reconciled clock has a comparable cost."""
    endpoint_ok = True
    def check(condition, message):
        nonlocal endpoint_ok
        if not condition:
            endpoint_ok = False
            issue(message)
        return condition

    reason = raw.get("terminal") if type(raw.get("terminal")) is str else None
    basis = platform.get("cost_basis")
    cost = raw.get("cost") if type(raw.get("cost")) is dict else {}
    ends = [row for row in journal if row["kind"] == "terminal"]
    single = len(ends) == 1 and ends[0] is journal[-1]
    check(single, "expected one final terminal event")
    terminal = ends[-1]["payload"] if ends else {}
    check(reason in EXITS, "exit is unavailable in scripted mode")
    check(reason in EXITS and EXITS[reason] == raw.get("validity"), "validity disagrees with the scripted exit")
    check(reason == terminal.get("end_reason") == platform.get("budget_reason"), "terminal and budget reasons disagree")
    check("human_action" in terminal and terminal["human_action"] is None, "unexpected or missing manual action evidence")
    missing = terminal.get("missing")
    if reason == "harness_failure":
        check(type(missing) is str and bool(missing.strip()) and missing == platform.get("failure"),
              "harness failure lacks matching missing-evidence detail")
    else:
        check("missing" in terminal and missing is None and "failure" in platform and platform["failure"] is None
              and not platform.get("recovery_issues"), "participant exit carries failed or missing instrumentation")
    actions = [row for row in journal if row["kind"] == "action"]
    steps_ok = check(type(cost.get("steps")) is int and len(actions) == cost["steps"], "charged steps differ from the journal")
    expected_last = (len(journal) - 1 or None) if modern or reason != "harness_failure" else len(actions)
    check("last_observation" in terminal and same(terminal["last_observation"], expected_last),
          "terminal last observation disagrees with journal")
    start, end = (platform.get(k) for k in ("start_monotonic_ns", "end_monotonic_ns"))
    started, ended = (platform.get(k) for k in ("start_utc", "end_utc"))
    span = cost.get("elapsed_ns")
    released = start is not None
    clock_ok = check(basis in ("exact", "before_release", "observed_lower_bound"), "unknown clock basis")
    if modern:
        stopped = platform.get("clock_stopped")
        expected_basis = "before_release" if not released else "exact" if stopped is True else "observed_lower_bound"
        clock_ok &= check(type(stopped) is bool and (released or not stopped)
                          and basis == expected_basis == terminal.get("cost_basis"), "clock provenance disagrees with basis")
    if basis == "before_release":
        clock_ok &= check((start, end, started, ended) == (None,) * 4 and same(span, 0)
                          and not [row for row in journal if row["kind"] != "terminal"]
                          and reason == "harness_failure", "before-release clock has participant activity")
    else:
        times = utc_time(started), utc_time(ended)
        clock_ok &= check(type(start) is int and type(end) is int and 0 <= start <= end
                          and same(end - start if duration is None else duration(start, end, platform), span) and all(times) and times[0] <= times[1],
                          "invalid clock interval or elapsed cost")
        if clock_ok:
            live = [row for row in journal if row["kind"] in ("action", "fault") or
                    row["kind"] == "public_state" and not row["payload"].get("recovery_read_utc")]
            clock_ok &= check(all(row["offset_ns"] <= span for row in live), "live participant event after its clock")
            offline = [row for row in journal if row["kind"] in ("terminal", "oracle")
                       or row["kind"] == "public_state" and row["payload"].get("recovery_read_utc")]
            clock_ok &= check(all(row["offset_ns"] >= span for row in offline), "offline observation inside the participant clock")
            for row in offline:
                if row["kind"] == "public_state":
                    clock_ok &= check(utc_time(row["payload"]["recovery_read_utc"]) is not None
                                      and "transition_offset_ns" in row["payload"] and row["payload"]["transition_offset_ns"] is None,
                                      "recovered state claims a participant transition time")
        if basis == "observed_lower_bound":
            clock_ok &= check(reason == "harness_failure", "participant exit requires a stopped clock")
    if modern or reason != "harness_failure":
        clock_ok &= check("end_monotonic_ns" in terminal and "end_utc" in terminal
                          and same(terminal["end_monotonic_ns"], end) and terminal["end_utc"] == ended,
                          "terminal endpoint disagrees with platform clock")
    budget = MANIFEST["budgets"]["scripted"]
    observed = platform.get("participant_exit")
    participant_reason = observed.get("reason") if type(observed) is dict else None
    budget_reason = participant_reason if reason == "harness_failure" and modern else reason
    if budget_reason == "time_limit":
        clock_ok &= check(type(span) is int and span >= budget["max_elapsed_s"] * 10**9, "time cutoff precedes deadline")
    elif budget_reason in ("done", "idle", "blocked", "step_limit"):
        clock_ok &= check(type(span) is int and span <= budget["max_elapsed_s"] * 10**9, "participant exceeded its deadline")
    if budget_reason == "step_limit":
        check(len(actions) == budget["max_steps"], "step cutoff precedes the registered step budget")
    if modern:
        if observed is not None:
            check(type(participant_reason) is str and participant_reason in EXITS and participant_reason != "harness_failure" and basis == "exact"
                  and same(observed.get("end_monotonic_ns"), end) and observed.get("end_utc") == ended
                  and (reason == "harness_failure" or observed.get("reason") == reason),
                  "observed participant exit disagrees with terminal or clock")
        else:
            check(reason == "harness_failure", "participant exit observation missing")
    return (basis if clock_ok and steps_ok else "unavailable"), endpoint_ok


def journal_prefix(journal, arm, issue, *, allowed_actions=ACTIONS):
    """Read every structurally identifiable observation, flagging gaps in chronology and action identity."""
    if type(journal) is not list:
        issue("unavailable observation journal")
        return [], False
    result = []
    ordered = True
    previous = sequence = 0
    attempt = 0
    for seq, event in enumerate(journal, 1):
        gap = {"kind": "public_gap", "payload": {}, "offset_ns": previous}
        if (type(event) is not dict or type(event.get("payload")) is not dict
                or event.get("kind") not in ("action", "public_state", "fault", "oracle", "terminal")):
            issue(f"invalid journal event {seq}")
            result.append(gap)
            ordered = False
            continue
        offset = event.get("offset_ns")
        number = event.get("seq")
        if not same(number, sequence + 1) or type(offset) is not int or offset < previous:
            issue(f"invalid journal sequence or offset at {seq}")
            result.append(gap)
            ordered = False
        sequence = max(sequence, number) if type(number) is int and number > 0 else sequence + 1
        if type(offset) is int and offset >= 0:
            previous = offset
        else:
            event = dict(event, offset_ns=previous)
        p = event["payload"]
        if event["kind"] == "action":
            number = p.get("attempt")
            repeated = type(number) is int and number <= attempt
            if not same(number, attempt + 1):
                issue(f"non-contiguous charged action at event {seq}")
                if not result or result[-1] is not gap:
                    result.append(gap)
                ordered = False
            attempt = max(attempt, number) if type(number) is int and number > 0 else attempt + 1
            action, actor = p.get("action"), p.get("actor")
            if (repeated or type(action) is not str or actor not in allowed_actions.get(action, ()) or p.get("arm") != arm
                    or arm == "loop" and actor in ("independent_verifier", "independent_observer")
                    or not same(p.get("attempt"), attempt) or p.get("action_id") != f"action-{attempt}"
                    or p.get("subject") not in WORK or type(p.get("input")) is not dict
                    or "candidate" not in p or p["candidate"] is not None and not digest(p["candidate"], 40)
                    or "return" in p and type(p["return"]) is not dict
                    or action == "submit" and (not digest(p["candidate"], 40) or p["input"].get("candidate") != p["candidate"])
                    or "work" in p["input"] and p["input"]["work"] != p["subject"]):
                issue(f"invalid charged action {attempt}")
                if not result or result[-1] is not gap:
                    result.append(gap)
                ordered = False
                # Its invocation still costs one step. Its alleged result cannot establish an outcome.
                p = {"attempt": attempt, "action": "", "actor": "", "subject": None, "input": {}, "candidate": None}
                event = dict(event, payload=p)
        result.append(event)
    return result, ordered


def public_rows(rows, arm):
    require(type(rows) is dict and set(rows) == set(WORK), "participant work identities differ from the registered set")
    result = {}
    for work, row in rows.items():
        state, oid = row["state"], row["candidate"]
        identity = row["work"] if arm == "purser" else row["work"]["id"]
        require(identity == work and type(state) is str and bool(state), "invalid public subject or state")
        if arm == "purser" and oid is not None:
            require(type(oid) is dict and oid.get("kind") == "git.commit" and oid.get("algorithm") == "sha1",
                    "invalid kernel candidate identity")
            oid = oid["digest"]
        require(oid is None or digest(oid, 40), "invalid public Git candidate")
        require(state not in (*CLOSED, "submitted") or oid is not None, "public submission or completion lacks a candidate")
        result[work] = (state, oid)
    return result


def kernel_rows(projection):
    return {row["work"]: row for row in WorkView(projection).answers()["q02"] if row["work"] in WORK}


def kernel_history(trace, revisions):
    """Reuse the kernel's canonical batch reader and projector for exact retained public revisions."""
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE events (revision INTEGER PRIMARY KEY, body BLOB NOT NULL)")
        db.executemany("INSERT INTO events VALUES (?,?)", ((i, body.encode()) for i, body in enumerate(trace["original_events"], 1)))
        projection = empty_projection()
        revision = 0
        history = {}
        for batch in _batches(db):
            revision, head = batch[-1]["revision"], batch[-1]["hash"]
            for event in batch:
                require(event["v"] == 1, "unknown kernel event version")
                projection = apply_event(projection, event["type"], event["data"], event["revision"], revision,
                                         event_version=event["event_version"])
            if revision in revisions:
                history[revision] = head, kernel_rows(projection), {"projection": projection}
        require(revision and same(trace["snapshot"], {"revision": revision, "head_hash": head, "projection": projection}),
                "retained kernel snapshot disagrees with original events")
        return history


READ_ERRORS = (KeyError, ValueError, TypeError, AttributeError, IndexError, KernelError, sqlite3.Error)


class PublicHistory:
    """Read actual participant bytes once for each checkpoint, then reconcile the recorded transitions."""

    def __init__(self, arm, modern, read, issue):
        self.arm, self.modern, self.read, self.issue = arm, modern, read, issue
        self.checkpoints, self.kernel = {}, None
        self.final = None
        self.complete = True

    def gap(self, message):
        self.complete = False
        self.issue(message)

    def checkpoint(self, checkpoint):
        if type(checkpoint) is not dict:
            return None
        key = encoded(checkpoint)
        if key in self.checkpoints:
            return self.checkpoints[key]
        states = None
        try:
            rows = self.read(checkpoint["view"]) if "view" in checkpoint else None
            if self.arm == "purser":
                rev, head = checkpoint["revision"], checkpoint["head_hash"]
                require(type(rev) is int and rev > 0 and digest(head, 64), "invalid kernel revision")
                if self.kernel is not None:
                    real_head, real_rows, _ = self.kernel[rev]
                    require(head == real_head and (rows is None or same(real_rows, rows)), "kernel checkpoint differs from replay")
                    rows = real_rows
            else:
                plan = checkpoint["plan_sha256"]
                require(digest(plan, 64) and (rows is None or sha(encoded(rows)) == plan), "plan checkpoint digest differs")
            require(not self.modern or "view" in checkpoint, "immutable checkpoint view missing")
            if rows is not None:
                states = public_rows(rows, self.arm)
        except READ_ERRORS as exc:
            self.gap(f"invalid public checkpoint: {exc}")
        self.checkpoints[key] = states
        return states

    def decision(self, action, checkpoint, subject):
        if action["subject"] != subject:
            return None
        returned = returned_decision(action)
        if returned is not None:
            return returned
        rows = self.read(checkpoint["view"])
        snapshot = None
        if self.arm == "purser":
            if self.kernel is None:
                return None
            snapshot = self.kernel[checkpoint["revision"]][2]
        return durable_decision(rows, action, snapshot)

    def retained(self, platform, journal):
        ref = platform.get("treatment_trace")
        if ref is None:
            return None
        try:
            trace = self.read(ref)
            if self.arm != "purser":
                self.final = public_rows(trace["plan"], self.arm)
                return {"plan_sha256": sha(encoded(trace["plan"]))}
            snap = trace["snapshot"]
            if "original_events" in trace:
                checkpoints = [platform.get("initial_public"), platform.get("final_public")]
                for row in journal:
                    p = row["payload"]
                    checkpoints.extend(p.get(k) for k in ("checkpoint", "before", "after"))
                revisions = {c["revision"] for c in checkpoints if type(c) is dict and type(c.get("revision")) is int}
                self.kernel = kernel_history(trace, revisions)
            else:
                require(not self.modern, "original kernel event history missing")
            self.final = public_rows(kernel_rows(snap["projection"]), self.arm)
            return {"revision": snap["revision"], "head_hash": snap["head_hash"]}
        except READ_ERRORS as exc:
            self.gap(f"invalid final participant trace: {exc}")
            return None

    def reconcile(self, journal, platform, *, incomplete):
        retained = self.retained(platform, journal)
        initial = {w: ("assigned" if self.arm == "purser" else "open", None) for w in WORK}
        if self.modern and platform.get("initial_public"):
            observed = self.checkpoint(platform["initial_public"])
            if observed is not None:
                if observed != initial:
                    self.gap("release checkpoint differs from the registered starting work")
                initial = observed
        elif self.modern and (journal and journal[0]["kind"] != "terminal" or platform.get("start_monotonic_ns") is not None):
            self.gap("released participant has no initial checkpoint")
        current = dict(initial)
        rows = []
        last_action = None
        last_public = None
        releases = set()
        after = None
        boundary = None
        last_boundary = platform.get("initial_public")

        def close_action():
            if after is None:
                return
            for work in WORK:
                changed = current[work] != after[work]
                decision = None
                if self.modern and work == last_action["subject"] and work not in releases:
                    try:
                        decision = self.decision(last_action, boundary, work)
                    except READ_ERRORS as exc:
                        self.gap(f"unavailable decision for action {last_action['attempt']}: {exc}")
                if changed or decision is not None:
                    self.gap(f"missing public transition or decision at action {last_action['attempt']} for {work}")
                    # The immutable endpoint and the actual response or durable receipt still witness this release.
                    old, (state, oid) = current[work][0], after[work]
                    rows.append({"kind": "public_state", "payload": {"subject": work, "from": old, "to": state,
                                 "candidate": oid, "diagnosis": decision["diagnosis"] if decision else [],
                                 "evidence": decision, "source_action": last_action["attempt"]}})
                    current[work] = after[work]

        for event in journal:
            kind, p = event["kind"], event["payload"]
            if kind == "action":
                close_action()
                releases = set()
                after = boundary = None
                last_action = p
                if self.modern:
                    if last_boundary is not None and not same(p.get("before"), last_boundary):
                        self.gap(f"checkpoint discontinuity before action {p['attempt']}")
                        rows.append({"kind": "public_gap", "payload": {}})
                    before = self.checkpoint(p.get("before"))
                    after = self.checkpoint(p.get("after"))
                    boundary = last_boundary = p.get("after")
                    if before is None or before != current:
                        self.gap(f"public history gap before action {p['attempt']}")
                        if before is not None:
                            current = dict(before)
                    if after is None:
                        self.complete = False
                        if not incomplete or platform.get("public_history_complete") is True:
                            self.gap(f"missing after checkpoint on action {p['attempt']}")
                    if self.arm == "purser" and p.get("proposal"):
                        try:
                            proposal = p["proposal"]
                            body, actor = proposal["body"], proposal["actor_id"]
                            require(proposal["proposal_id"] == body["proposal_id"]
                                    and same(body["base_revision"], p["before"]["revision"])
                                    and kernel_digest("proposal", {"actor_id": actor, "proposal": body}) == proposal["digest"],
                                    "proposal does not belong to this invocation")
                        except READ_ERRORS as exc:
                            self.gap(f"action {p['attempt']} proposal: {exc}")
                            p = {k: v for k, v in p.items() if k != "proposal"}
                    if "response_evidence" in p:
                        try:
                            witness = self.read(p["response_evidence"])
                            expected = {k: p[k] for k in ("attempt", "actor", "subject", "candidate")}
                            require(type(witness) is dict and set(witness) == set(expected) | {"return"}
                                    and same(expected, {k: witness[k] for k in expected})
                                    and type(witness["return"]) is dict, "response belongs to a different invocation")
                            if not same(witness["return"], p.get("return")):
                                self.gap(f"returned response differs at action {p['attempt']}")
                            p = dict(p, **{"return": witness["return"]})
                        except READ_ERRORS as exc:
                            self.gap(f"action {p['attempt']} response evidence: {exc}")
                            p = {k: v for k, v in p.items() if k != "return"}
                    elif "return" in p and "failure" not in p["return"] or "return" not in p and not incomplete:
                        self.gap(f"missing response evidence at action {p['attempt']}")
                        p = {k: v for k, v in p.items() if k != "return"}
                    event = dict(event, payload=p)
                    last_action = p
                rows.append(event)
                continue
            if kind != "public_state":
                if kind in ("oracle", "terminal"):
                    close_action()
                    after = None
                rows.append(event)
                continue
            try:
                work, state, oid = p["subject"], p["to"], p["candidate"]
                require(work in WORK and type(state) is str and (oid is None or digest(oid, 40))
                        and (state not in (*CLOSED, "submitted") or oid is not None), "invalid transition subject")
                require(type(p.get("diagnosis")) is list and all(type(x) is str for x in p["diagnosis"]),
                        "invalid public diagnosis")
                ck = p["checkpoint"]
                mapped = self.checkpoint(ck)
                if self.modern:
                    require(last_action is not None and same(p.get("source_action"), last_action["attempt"])
                            and mapped is not None and (boundary is None or same(ck, boundary)), "transition invocation mismatch")
                if mapped is not None:
                    require(mapped[work] == (state, oid), "transition disagrees with participant checkpoint")
                require(p["from"] == current[work][0], "previous public state mismatch")
                if self.modern:
                    decision = self.decision(last_action, ck, work)
                    if decision is not None:
                        releases.add(work)
                    labels = decision["diagnosis"] if decision else []
                    if not same(p.get("evidence"), decision) or not same(p["diagnosis"], labels):
                        self.gap(f"decision evidence differs from its release at action {last_action['attempt']}")
                        event = dict(event, payload=dict(p, diagnosis=labels, evidence=decision))
                current[work] = state, oid
                last_public = ck
                rows.append(event)
            except READ_ERRORS as exc:
                self.gap(f"unverified public transition at event {event['seq']}: {exc}")

        close_action()
        final = self.checkpoint(platform.get("final_public")) if self.modern else None
        if final is not None:
            if last_boundary is not None and not same(platform["final_public"], last_boundary):
                self.gap("final checkpoint differs from last action endpoint")
            if self.final is not None and self.final != final:
                self.gap("final checkpoint differs from participant trace")
            if retained is not None and any(platform["final_public"].get(k) != v for k, v in retained.items()):
                self.gap("final checkpoint identity differs from participant trace")
        if self.final is None:
            self.final = final
        if self.final is not None and self.final != current:
            self.gap("final work state or candidate differs from the recorded public history")
        if (not self.modern and last_public is not None and retained is not None and self.arm != "purser"
                and last_public.get("plan_sha256") != retained["plan_sha256"]):
            self.gap("final plan identity differs from last public checkpoint")
        if self.modern and retained is not None and final is None and not incomplete:
            self.gap("final public checkpoint missing")
        return rows
