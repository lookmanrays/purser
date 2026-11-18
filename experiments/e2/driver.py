"""Run the common participant policy; the observer never sees the protected grader."""

import json
from pathlib import Path

from experiments.e1 import capture, observer
from experiments.e1.artifacts import encoded, read_artifact, sha
from experiments.e1.executor import LEAVES, ORDER, PARENT, PARENT_CHECKS
from experiments.e1.outcomes import is_closed
from experiments.e1.run import Driver as ActionDriver

from . import policy


class Driver(ActionDriver):
    def __init__(self, candidates, adapter, journal, assets, seed, track):
        super().__init__(candidates, adapter, journal, assets, seed, "clean")
        self.track = track
        self.phase = policy.LINEAR

    def submit_work(self, work):
        adapter = self.adapter
        context = self.call("context", work, "executor", {"work": work}, lambda: adapter.context(work))
        if context["work"]["id"] != work:
            raise ValueError("assignment does not identify the requested work")
        if work == "W2":
            self.phase = adapter.choice(context)
            if self.phase not in policy.CHOICES:
                raise ValueError("unknown public W2 retry choice")
        if work in LEAVES:
            def edit():
                policy.edit(self.candidates.work, work, self.phase)
                return {"edited": work}
            self.call("edit", work, "executor", {"work": work, "schedule": self.phase}, edit)
        checks = self.call("check", work, "executor", {"check": "public task checks", "schedule": self.phase},
                           lambda: policy.worker_check(self.candidates.work, work, self.phase))
        oid = self.candidates.commit(f"Notification work {work}")
        self.seen.add(oid)
        self.call("submit", work, "executor", {"candidate": oid, "claim": checks["worker_claim"]},
                  lambda: adapter.submit(work, oid), oid)
        return oid

    def finish(self, work, oid):
        candidates, adapter = self.candidates, self.adapter
        with candidates.export(oid) as exported:
            root = Path(exported)
            audit = self.call("check", work, "plan_reviewer", {"check": "review submitted candidate", "schedule": self.phase},
                              lambda: policy.review(candidates.archive, oid, root, work, self.phase), oid)
            if self.j.arm != "loop":
                for label in (LEAVES[work],) if work in LEAVES else PARENT_CHECKS:
                    self.call("plan", work, "planner", {"open_run": label}, lambda: adapter.opening(work, label), oid)
                    def observe():
                        report = policy.inspect(candidates.archive, oid, root, label, self.phase)
                        ref = self.assets.put(report, "json")
                        self.j.platform["referenced_files"].append(ref)
                        reply = adapter.observe(work, label, report)
                        return {"witness": ref, "response": reply}
                    observed = self.call("observe", work, "independent_observer",
                                         {"criterion": label, "schedule": self.phase}, observe, oid)
                    report = json.loads(read_artifact(self.assets.archive, observed["witness"]))
                    graded = dict(report, verdict=observer.verdict(report, label, oid))
                    def verify():
                        reply = adapter.verify(work, label, graded)
                        return {**reply, "diagnosis": [] if graded["verdict"] == "pass" else [label]}
                    self.call("decide", work, "independent_verifier", {"criterion": label, "witness": observed["witness"],
                              "verdict": graded["verdict"]}, verify, oid)
        self.call("decide", work, "closure", {"request": "close", "public_review": audit},
                  lambda: adapter.decide(work, audit), oid)
        return is_closed(self.previous[work][0])

    def receive(self, oid):
        message = policy.notice(self.seed)
        rows, identity = self.adapter.public_view()
        event = {"intervention": "linear_to_exponential", "observation_id": message["id"], "seed": self.seed,
                 "trigger": self.track, "released": message, "released_sha256": sha(encoded(message)),
                 "before": capture.checkpoint(self.assets, rows, identity), "candidate": oid,
                 "subject": "W2", "actor": "fixture_observer", "attempt": message["id"]}
        self.j.event("fault", event)
        if isinstance(self.adapter, policy.KernelArm):
            def record(proposal):
                event["proposal"] = proposal
                self.j.persist()
            self.adapter.on_proposal = record
        try:
            reply = self.adapter.receive(message)
        finally:
            if isinstance(self.adapter, policy.KernelArm):
                self.adapter.on_proposal = None
        event["return"] = reply
        event["response_evidence"] = self.assets.put(capture.response_evidence(event), "json")
        self.j.persist()
        rows, identity = self.adapter.public_view()
        event["after"] = capture.checkpoint(self.assets, rows, identity)
        self.j.persist()
        capture.states(rows, event["after"], self.previous, self.j.event, action=event)
        return message

    def execute(self):
        self.call("plan", PARENT, "planner", {"schedule": list(ORDER)}, lambda: {"work_order": list(ORDER)})
        for work in ("W1", "W3"):
            if not self.finish(work, self.submit_work(work)):
                return "blocked"
        old = self.submit_work("W2")
        if self.track in ("clean", "closed-affected"):
            if not self.finish("W2", old):
                return "blocked"
        if self.track != "clean":
            message = self.receive(old)
            # The plan names the affected criterion. Both loops can act only on W2.
            work = next(w for w, criterion in LEAVES.items() if criterion == message["criterion"])
            self.call("plan", work, "planner", {"adopt": message["id"], "work": work},
                      lambda: self.adapter.revise(message))
            self.call("plan", work, "planner", {"assign": work, "work": work}, lambda: self.adapter.assign_again(work))
            if not self.finish(work, self.submit_work(work)):
                return "blocked"
        if not self.finish(PARENT, self.submit_work(PARENT)):
            return "blocked"
        return "done"
