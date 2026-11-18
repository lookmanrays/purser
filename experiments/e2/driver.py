"""Run the common participant policy; the observer never sees the protected grader."""

import json
from contextlib import nullcontext
from pathlib import Path

from experiments.e1 import capture, observer
from experiments.e1.artifacts import encoded, read_artifact, sha
from experiments.e1.executor import LEAVES, PARENT, PARENT_CHECKS
from experiments.e1.outcomes import is_closed
from experiments.e1.run import Driver as ActionDriver

from . import checkpoint, clock, head, policy
from .actions import PublicActions
from .operations import PORTS, method_for
from .worker_tools import WorkerTools


class Driver(ActionDriver):
    def __init__(self, candidates, adapter, journal, assets, seed, track, rank=1):
        super().__init__(candidates, adapter, journal, assets, seed, "clean")
        self.track, self.rank = track, rank
        self.phase = policy.LINEAR
        self.actions = PublicActions(self)
        self.adapter.work_actions = self.actions
        self.tools = WorkerTools(self.actions, adapter, candidates)
        self.j.platform["work_admission_version"] = 2
        self._notice_claimed = False

    def call(self, action, work, actor, inputs, operation, oid=None):
        method = method_for(action, actor, inputs)
        def dispatch():
            if method in PORTS or method == "context":
                return self.actions.controller_transport(self.adapter, method, operation)
            self.actions.start_work()
            return operation()
        return self.actions.call(action, work, actor, inputs, dispatch, oid)

    def submit_work(self, work):
        adapter = self.adapter
        context = self.tools.context(work)
        if context["work"]["id"] != work:
            raise ValueError("assignment does not identify the requested work")
        if work == "W2":
            self.phase = adapter.choice(context)
            if self.phase not in policy.CHOICES:
                raise ValueError("unknown public W2 retry choice")
        if work in LEAVES:
            self.tools.edit(work, self.phase)
        checks = self.tools.check(work, self.phase)
        oid = self.candidates.commit(f"Notification work {work}")
        self.seen.add(oid)
        self.call("submit", work, "executor", {"candidate": oid, "claim": checks["worker_claim"]},
                  lambda: adapter.submit(work, oid), oid)
        return oid

    def finish(self, work, oid):
        candidates, adapter = self.candidates, self.adapter
        with candidates.export(oid) as exported:
            root = Path(exported)
            audit = self.tools.review(work, oid, self.phase, root)
            if self.j.arm != "loop":
                for label in (LEAVES[work],) if work in LEAVES else PARENT_CHECKS:
                    self.call("plan", work, "planner", {"open_run": label}, lambda: adapter.opening(work, label), oid)
                    def observe():
                        self.actions.start_work()
                        report = policy.inspect(candidates.archive, oid, root, label, self.phase)
                        ref = self.assets.put(report, "json")
                        self.j.platform["referenced_files"].append(ref)
                        reply = self.actions.forward(adapter, "observe", work, label, report)
                        return {"witness": ref, "response": reply}
                    observed = self.call("observe", work, "independent_observer",
                                         {"criterion": label, "schedule": self.phase}, observe, oid)
                    report = json.loads(read_artifact(self.assets.archive, observed["witness"]))
                    graded = dict(report, verdict=observer.verdict(report, label, oid))
                    def verify():
                        self.actions.start_work()
                        reply = self.actions.forward(adapter, "verify", work, label, graded)
                        return {**reply, "diagnosis": [] if graded["verdict"] == "pass" else [label]}
                    self.call("decide", work, "independent_verifier", {"criterion": label, "witness": observed["witness"],
                              "verdict": graded["verdict"]}, verify, oid)
        self.call("decide", work, "closure", {"request": "close", "public_review": audit},
                  lambda: adapter.decide(work, audit), oid)
        return is_closed(self.previous[work][0])

    def receive(self, oid):
        message = policy.notice(self.seed)
        from .plan_versions import HeadMoved
        try:
            self.adapter.receive(message)
        except HeadMoved:
            return None
        return message

    def physical_receive(self, adapter, message, operation):
        """Observe the exposed incoming port, including a copied port with no driver call."""
        from .plan_versions import HeadMoved
        rows, identity = adapter.public_view()
        qualification = self.j.platform.get("checkpoint_measurement") or {}
        if isinstance(adapter, policy.KernelArm):
            same_source = isinstance(self.adapter, policy.KernelArm) and adapter.store.path == self.adapter.store.path
        else:
            same_source = isinstance(self.adapter, policy.PlanArm) and adapter.file == self.adapter.file
        with self.actions.admission:
            expected = None
            if (self.track != "clean" and qualification.get("qualified") is True and not self._notice_claimed
                    and same_source and adapter._binding is self.adapter._binding and message == policy.notice(self.seed)
                    and capture.position(rows["W2"])[1] == qualification["candidate"]):
                expected = qualification["checkpoint"]
                self._notice_claimed = True
        event = {"required_before": expected, "intervention": "linear_to_exponential",
                 "observation_id": message["id"], "seed": self.seed, "trigger": self.track,
                 "released": message, "released_sha256": sha(encoded(message)),
                 "before": capture.checkpoint(self.assets, rows, identity), "candidate": capture.position(rows["W2"])[1],
                 "subject": "W2", "actor": "fixture_observer", "attempt": message["id"]}
        clock.begin_notice(self.j, event)
        if isinstance(adapter, policy.KernelArm):
            previous = adapter.on_proposal
            def record(proposal):
                event["proposal"] = proposal
                self.j.persist()
            adapter.on_proposal = record
        try:
            if self.track != "clean" and expected is None:
                raise HeadMoved("no available qualified predecessor for this notice")
            with head.expect(adapter, expected) if expected else nullcontext():
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
            clock.finish_activity(self.j, event)
            if isinstance(adapter, policy.KernelArm):
                adapter.on_proposal = previous
            event["response_evidence"] = self.assets.put(capture.response_evidence(event), "json")
            self.j.persist()
            rows, identity = adapter.public_view()
            event["after"] = capture.checkpoint(self.assets, rows, identity)
            self.j.persist()
            capture.states(rows, event["after"], self.previous, self.j.event, action=event)

    def execute(self):
        self.tools.schedule()
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
