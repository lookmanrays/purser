"""Model-selected coding and plan actions against a real disposable arm."""

import time
from itertools import combinations

from purser.adapters.model_protocol import ActionSpec, ModelIncomplete, ModelPrompt, OperandSpec

from experiments.e1 import capture, observer
from experiments.e1.artifacts import FIXTURE, changes, encoded, git
from experiments.e1.executor import LEAVES, ORDER, PARENT, PARENT_CHECKS, PATHS, TASKS, fault_for, inject
from experiments.e1.outcomes import LABELS, is_closed
from experiments.e1.purser_arm import Treatment
from experiments.e1.run import Cutoff
from experiments.e2 import policy
from experiments.model_budget import LIMITS

from .public import PublicRun, WorkRefused

WORK = (*ORDER, PARENT)
BY_PATH = {path: work for work, path in PATHS.items()}
DIAGNOSES = ("none", *(",".join(group) for count in range(1, len(LABELS) + 1)
                       for group in combinations(LABELS, count)))
FILE_PATHS = tuple(row["path"] for row in FIXTURE["files"])
TASK = ("Implement the three notification policies in the current assignment. Work on preferences (W1) and "
        "ordered delivery (W3) before completing retries (W2). Integrate the result as W-notify. "
        "The public queue, current policy source, Git changes, task context and checks are available. "
        "Reconsider the relevant work when its requirement changes. You have at most eight decisions. "
        "Choose the source, work, and any closure or diagnosis yourself. An edit with request_close asks "
        "for closure after submission and public review. A close on W-notify submits the current tree "
        "for integration review and requests parent closure.")


def menu(paths, outside, revised):
    work = OperandSpec(choices=WORK)
    declaration = OperandSpec(choices=("submit", "request_close"))
    source = OperandSpec(max_bytes=4096)
    labels = OperandSpec(choices=DIAGNOSES)
    actions = {
        "edit": ActionSpec("Replace one policy module, check it and submit for this work.",
                           {"path": OperandSpec(choices=tuple(paths)), "source": source, "disposition": declaration}),
        "close": ActionSpec("Declare a work item complete on the current candidate.", {"work": work}),
        "reopen": ActionSpec("Reopen and reassign one work item.", {"work": work}),
        "inspect": ActionSpec("Read the selected work's current public context.", {"work": work}),
        "read": ActionSpec("Read a page of a public source or test file (8 KiB pages).",
                           {"path": OperandSpec(choices=FILE_PATHS), "page": OperandSpec(choices=tuple(map(str, range(12))))}),
        "diagnose": ActionSpec("Publish the missing obligations for the current result, then continue.",
                               {"work": work, "obligations": labels}),
        "stop": ActionSpec("Leave the task pending and give the public reason.",
                           {"reason": OperandSpec(max_bytes=512)}),
    }
    if revised:
        actions["revise_retry"] = ActionSpec("Adopt the received retry decision, then submit your replacement module.",
                                             {"path": OperandSpec(choices=(PATHS["W2"],)), "source": source,
                                              "disposition": declaration})
    if outside:
        actions["remove"] = ActionSpec("Remove a listed out-of-scope addition and submit the parent.",
                                       {"path": OperandSpec(choices=tuple(outside)), "disposition": declaration})
    return actions


def prompt_bytes(prompt):
    return {"task": prompt.task, "observation": prompt.observation,
            "actions": {name: spec.describe() for name, spec in prompt.actions.items()}}


def workspace_view(experiment, rows, repo, oid, notice, history, turn):
    work = {w: {k: v for k, v in row.items() if not k.startswith("_")} for w, row in rows.items()}
    previous = []
    for event in history:
        if event["kind"] == "action":
            p = event["payload"]
            if (turn and p.get("model_turn") == turn and "return" in p
                    and p["input"].get("tool") != "public plan and working Git tree"):
                previous.append({"id": p["action_id"], "action": p["action"], "actor": p["actor"],
                                 "work": p["subject"], "candidate": p["candidate"], "result": p["return"]})
    initial = {e["payload"]["subject"]: e["payload"]["return"] for e in history if e["kind"] == "action"
               and e["payload"]["input"].get("release") == "initial assignment" and "return" in e["payload"]}
    return {"work": work, "assignment_contexts": initial if not turn else {}, "candidate": oid, "git_changes": changes(repo, oid),
            "policy_files": {p: git(repo, "show", f"{oid}:{p}").decode() for p in PATHS.values()},
            "notice": notice, "last_public_actions": previous,
            "tasks": policy.TASK_TITLES if experiment == "E2" else TASKS,
            "retry_requirement": policy.NEW if notice else policy.OLD}


class Participant:
    def __init__(self, experiment, track, seed, candidates, adapter, journal, checks, decide):
        self.experiment, self.track, self.seed = experiment, track, seed
        self.candidates, self.arm, self.j = candidates, adapter, journal
        self.checks, self.decide = checks, decide
        self.public = PublicRun(journal, adapter, checks.assets)
        self.call = self.public.call
        self.fault = fault_for(seed, track) if experiment == "E1" else None
        self.injected = False
        self.phase = policy.LINEAR
        self.notice = None
        self.adopted = False
        self.worked = set()

    def view(self):
        rows, _ = self.arm.public_view()
        return workspace_view(self.experiment, rows, self.candidates.archive, self.candidates.head,
                              self.notice, self.j.rows, self.public.turn)

    def execute(self):
        for work in WORK:
            self.call("context", work, "executor", {"release": "initial assignment", "work": work},
                      lambda: self.arm.context(work))
        while self.public.turn < LIMITS["max_model_calls"]:
            view = self.call("context", PARENT, "executor", {"tool": "public plan and working Git tree"}, self.view)
            paths = [p for w, p in PATHS.items() if w != "W2" or {"W1", "W3"} <= self.worked]
            outside = [row["path"] for row in view["git_changes"] if row["status"] == "A" and row["path"] not in BY_PATH]
            prompt = ModelPrompt(TASK, encoded(view).decode(), menu(paths, outside, self.notice and not self.adopted))
            start = self.public.choice(self.checks.assets.put(prompt_bytes(prompt), "json"))
            try:
                decision = self.decide(prompt, deadline=time.monotonic() + max(0, self.j.remaining_seconds()))
            except ModelIncomplete as error:
                self.public.failed_choice(error.code)
                if error.code in ("budget_refused", "model_limit", "token_limit"):
                    return "model_limit"
                if error.code in ("invalid_proposal", "unfinished_response", "unusable_response"):
                    return "blocked"
                return "provider_unavailable"
            original = {"action": decision.action, "arguments": decision.arguments}
            prompt.proposal(encoded(original))
            start["choice"] = dict(original, request_sha256=decision.request_sha256, result_sha256=decision.result_sha256)
            self.j.persist()
            try:
                exit_reason = self.apply(decision.action, decision.arguments)
            except WorkRefused:
                continue
            if exit_reason:
                return exit_reason
        return "model_limit"

    def state(self, work):
        return self.public.previous[work]

    def assign(self, work):
        return self.call("plan", work, "planner", {"assign": work}, lambda: self.arm.assign_again(work))

    def apply(self, action, args):
        oid = self.candidates.head
        if action == "stop":
            self.call("decide", PARENT, "executor", args, lambda: {"pending": args["reason"]}, oid)
            return "blocked"
        if action == "read":
            path, page = args["path"], int(args["page"])
            self.call("context", PARENT, "executor", args, lambda: {
                "path": path, "page": page, "source": git(self.candidates.archive, "show", f"{oid}:{path}")
                [page * 8192:(page + 1) * 8192].decode(errors="replace")}, oid)
            return None
        if action in ("edit", "revise_retry", "remove"):
            work = PARENT if action == "remove" else BY_PATH[args["path"]]
            if action == "revise_retry":
                self.call("plan", work, "planner", {"adopt": self.notice["id"]}, lambda: self.arm.revise(self.notice))
                self.adopted = True
            _, prior_oid = self.state(work)
            if prior_oid is not None or (action == "revise_retry" and isinstance(self.arm, Treatment)):
                self.assign(work)
            def write():
                path = self.candidates.work / args["path"]
                if action == "remove":
                    path.unlink()
                else:
                    path.write_bytes(args["source"].encode("utf-8"))
                commit = self.candidates.commit("Model edit of " + args["path"])
                return {"edited": work, "candidate": commit, "path": args["path"]}
            edited = self.call("edit", work, "executor", {**args, "repair": bool(prior_oid) or action == "remove"},
                               write, oid)
            self.worked.add(work)
            submitted, changed = self.submit(work, edited["candidate"])
            if changed:
                return None
            if args["disposition"] == "request_close":
                return self.close(work, submitted)
            return None
        work = args["work"]
        if action == "reopen":
            self.assign(work)
        elif action == "inspect":
            def inspect_work():
                from purser.kernel.wire import KernelError
                try:
                    return self.arm.context(work)
                except KernelError as error:
                    return {"disposition": "refused", "code": str(error)}
            self.call("context", work, "executor", {"work": work}, inspect_work, self.state(work)[1])
        elif action == "diagnose":
            labels = [] if args["obligations"] == "none" else args["obligations"].split(",")
            current = self.state(work)[1]
            self.call("check", work, "plan_reviewer", args, lambda: {"missing": labels}, current)
        elif action == "close":
            if self.state(work)[1] != oid:
                if self.state(work)[1] is not None:
                    self.assign(work)
                oid, changed = self.submit(work, oid)
                if changed:
                    return None
            return self.close(work, oid)
        else:
            raise ValueError("unhandled typed action")
        return None

    def submit(self, work, oid):
        c = self.candidates
        fault = self.fault if self.fault and not self.injected and self.fault[1] == work else None
        checked = self.call("check", work, "executor", {"check": "public task checks"},
                            lambda: self.checks.public_check("worker", self.experiment, oid, {"work": work, "phase": self.phase,
                                      "skip": bool(fault and fault[0] == "skipped_retry_check")}), oid)
        claim = "done, tests pass" if fault else checked["worker_claim"]
        if fault:
            before = oid
            inject(c.work, fault, self.seed)
            oid = c.commit("Registered first submission overlay")
            self.injected = True
            self.j.event("fault", {"seed": self.seed, "intervention": fault[0], "candidate": oid,
                        "source_candidate": before, "work": work, "target": fault[2],
                        "trigger": "first submission of " + work, "released": claim})
        self.call("submit", work, "executor", {"candidate": oid, "claim": claim},
                              lambda: self.arm.submit(work, oid), oid)
        if self.check_premise(work, oid):
            return oid, True
        self.call("check", work, "plan_reviewer", {"check": "post-submit public self-review"},
                          lambda: self.checks.public_check("review", self.experiment, oid, {"work": work, "phase": self.phase}), oid)
        if self.j.arm != "loop":
            for label in (LEAVES[work],) if work in LEAVES else PARENT_CHECKS:
                self.call("plan", work, "planner", {"open_run": label}, lambda: self.arm.opening(work, label), oid)
                def observe():
                    result = self.checks.public_check("verifier", self.experiment, oid, {"criterion": label, "phase": self.phase})
                    return {"report": result, "receipt": self.arm.observe(work, label, result)}
                result = self.call("observe", work, "independent_observer", {"criterion": label}, observe, oid)["report"]
                verdict = observer.verdict(result, label, oid)
                def verify():
                    reply = self.arm.verify(work, label, dict(result, verdict=verdict))
                    return dict(reply, diagnosis=[] if verdict == "pass" else [label])
                self.call("decide", work, "independent_verifier", {"criterion": label, "verdict": verdict}, verify, oid)
        return oid, False

    def close(self, work, oid):
        def declare():
            if isinstance(self.arm, Treatment):
                return self.arm.decide(work, {})
            self.arm.update(work, state="closed")
            return {"claim": self.arm._plan_row(work), "diagnosis": []}
        self.call("decide", work, "closure", {"request": "close"}, declare, oid)
        self.check_premise(work, oid)
        return "done" if work == PARENT and is_closed(self.state(PARENT)[0]) else None

    def check_premise(self, work, oid):
        if (self.experiment != "E2" or self.track == "clean" or self.injected or work != "W2"
                or self.state("W2")[1] != oid or not all(is_closed(self.state(w)[0]) for w in ("W1", "W3"))
                or is_closed(self.state("W2")[0]) != (self.track == "closed-affected")):
            return False
        rows, checkpoint = self.public.point()
        states = {w: capture.position(row) for w, row in rows.items()}
        with self.j.private_measurement(oid, checkpoint):
            qualified = self.checks.checkpoint(states, oid, self.track, self.j)
        if not qualified:
            return False
        self.public.deliver(policy.notice(self.seed), oid)
        self.notice = policy.notice(self.seed)
        self.injected = True
        self.phase = policy.EXPONENTIAL
        return True
