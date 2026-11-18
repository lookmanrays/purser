"""Run the common participant policy; the observer never sees the protected grader."""

import json
import time
from contextlib import nullcontext
from pathlib import Path

from experiments.e1 import capture, observer
from experiments.e1.artifacts import encoded, read_artifact, sha
from experiments.e1.executor import LEAVES, ORDER, PARENT, PARENT_CHECKS
from experiments.e1.outcomes import is_closed
from experiments.e1.run import Driver as ActionDriver

from . import checkpoint, head, policy
from .actions import PublicActions


class Driver(ActionDriver):
    def __init__(self, candidates, adapter, journal, assets, seed, track, rank):
        super().__init__(candidates, adapter, journal, assets, seed, "clean")
        self.track, self.rank = track, rank
        self.phase = policy.LINEAR
        self.actions = PublicActions(self)
        self.adapter.work_actions = self.actions

    def call(self, action, work, actor, inputs, operation, oid=None):
        return self.actions.call(action, work, actor, inputs, operation, oid)

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
        qualification = self.j.platform.get("checkpoint_measurement")
        expected = qualification["checkpoint"] if qualification and qualification.get("qualified") is True else None
        from .plan_versions import HeadMoved
        try:
            with head.expect(self.adapter, expected) if expected else nullcontext():
                self.adapter.receive(message)
        except HeadMoved:
            return None
        return message

    def physical_receive(self, adapter, message, operation):
        """Observe the exposed incoming port, including a copied port with no driver call."""
        from .plan_versions import HeadMoved
        rows, identity = adapter.public_view()
        issued = time.monotonic_ns()
        self.j.public_activity(issued)
        event = {"required_before": head.required(), "intervention": "linear_to_exponential",
                 "observation_id": message["id"], "seed": self.seed, "trigger": self.track,
                 "released": message, "released_sha256": sha(encoded(message)),
                 "before": capture.checkpoint(self.assets, rows, identity), "candidate": capture.position(rows["W2"])[1],
                 "subject": "W2", "actor": "fixture_observer", "attempt": message["id"], "issued_monotonic_ns": issued}
        self.j.event("fault", event)
        if isinstance(adapter, policy.KernelArm):
            previous = adapter.on_proposal
            def record(proposal):
                event["proposal"] = proposal
                self.j.persist()
            adapter.on_proposal = record
        try:
            reply = operation()
            event["return"] = reply
            return reply
        except HeadMoved:
            event.update(disposition="head_moved", **{"return": {"disposition":"withheld", "reason":"qualified checkpoint changed"}})
            raise
        except Exception as error:
            event["return"] = {"failure": str(error)}
            raise
        finally:
            event["finished_monotonic_ns"] = time.monotonic_ns()
            if isinstance(adapter, policy.KernelArm):
                adapter.on_proposal = previous
            event["response_evidence"] = self.assets.put(capture.response_evidence(event), "json")
            self.j.persist()
            rows, identity = adapter.public_view()
            event["after"] = capture.checkpoint(self.assets, rows, identity)
            self.j.persist()
            capture.states(rows, event["after"], self.previous, self.j.event, action=event)

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
            if not checkpoint.qualify(self, old):
                return "blocked"
            message = self.receive(old)
            if message is None:
                return "blocked"
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
