"""Publish one work-scoped action and observe the plan or kernel at its boundary."""

import time
from contextlib import contextmanager
from copy import deepcopy

from experiments.e1 import capture
from experiments.e1.arms import Plan
from experiments.e1.artifacts import encoded
from experiments.e1.executor import PARENT
from experiments.e1.purser_arm import Treatment
from experiments.e1.run import Cutoff, utc
from experiments.e2 import head, plan_versions, policy
from experiments.model_budget import LIMITS


class ModelPlan(Plan):
    """Keep the plan's original publications, including an unfounded declaration."""

    def publish(self, rows):
        actor = self.publication()
        plan_versions.publish(self.file, rows, actor["attempt"] if actor else None)

    def _plan_row(self, work):
        row = super()._plan_row(work)
        row.pop(plan_versions.KEY, None)
        return row


class PremisePlan(policy.PlanArm):
    def publication(self):
        return super().publication() or self.invocation


class PremiseKernel(policy.KernelArm):
    # Model actions are received by PublicRun, including their original gate proposal.
    record_request = Treatment.record_request


def make_arm(directory, arm, experiment, definitions, environment):
    if arm == "purser":
        kind = PremiseKernel if experiment == "E2" else Treatment
        return kind(directory, definitions, environment)
    return (PremisePlan if experiment == "E2" else ModelPlan)(directory)


class WorkRefused(Exception):
    def __init__(self, receipt):
        self.receipt = receipt


class PublicRun:
    def __init__(self, journal, adapter, assets):
        self.j, self.arm, self.assets = journal, adapter, assets
        rows, initial = self.point()
        self.previous = {work: capture.position(row) for work, row in rows.items()}
        self.j.initial_states = dict(self.previous)
        self.j.platform["initial_public"] = initial
        self.pending = None
        self.turn = 0

    def point(self):
        rows, identity = self.arm.public_view()
        return rows, capture.checkpoint(self.assets, rows, identity)

    def choice(self, prompt_ref):
        self.turn += 1
        if self.turn > LIMITS["max_model_calls"]:
            raise Cutoff("model_limit")
        _, before = self.point()
        self.pending = self.j.begin_action("decide", PARENT, "executor", None, {"model_choice": self.turn},
                                          before=before, metadata={"model_turn": self.turn, "prompt": prompt_ref,
                                          "issued_monotonic_ns": time.monotonic_ns()})
        return self.pending

    def call(self, kind, work, actor, inputs, operation, oid=None):
        rows, before = self.point()
        if self.pending is not None:
            action, self.pending = self.pending, None
            action.update(action=kind, subject=work, actor=actor, input=deepcopy(inputs), candidate=oid)
            if action["before"] != before:
                raise ValueError("public state changed while awaiting model choice")
        else:
            action = self.j.begin_action(kind, work, actor, oid, deepcopy(inputs), before=before,
                                         metadata={"model_turn": self.turn or None,
                                                   "issued_monotonic_ns": time.monotonic_ns()})
        action["work_started_ns"] = time.monotonic_ns()
        self.j.persist()
        def proposal(value):
            action.setdefault("proposals", []).append(value)
            self.j.persist()
        if isinstance(self.arm, Treatment):
            self.arm.on_proposal = proposal
        else:
            self.arm.invocation = {"attempt": action["attempt"], "actor": actor}
        refused = []
        def invoke():
            try:
                return operation()
            except RuntimeError:
                proposals = action.get("proposals", [])
                last = self.arm.receipts[-1]["receipt"] if isinstance(self.arm, Treatment) and self.arm.receipts else {}
                if not proposals or last.get("proposal_id") != proposals[-1]["proposal_id"] or last.get("disposition") != "rejected":
                    raise
                refused.append(last)
                return last

        try:
            reply = self.j.run_action(action, invoke, self.assets)
            if refused:
                raise WorkRefused(reply)
            return reply
        finally:
            self.arm.on_proposal = None
            self.arm.invocation = None
            action["finished_monotonic_ns"] = time.monotonic_ns()
            rows, after = self.point()
            action["after"] = after
            capture.states(rows, after, self.previous, self.j.event, action=action,
                           decision=capture.returned_decision(action))
            self.j.persist()

    def failed_choice(self, reason):
        if self.pending is not None:
            self.call("decide", PARENT, "executor", {"model_choice": self.turn},
                      lambda: {"failure": reason})

    def deliver(self, message, oid):
        rows, before = self.point()
        event = {"seed": message["seed"], "intervention": "linear_to_exponential", "candidate": oid,
                 "work": "W2", "attempt": message["id"], "subject": "W2", "trigger": before,
                 "released": message, "before": before, "issued_monotonic_ns": time.monotonic_ns()}
        self.j.event("fault", event)
        with head.expect(self.arm, before):
            event["return"] = self.arm.receive(message)
        rows, event["after"] = self.point()
        event["finished_monotonic_ns"] = time.monotonic_ns()
        capture.states(rows, event["after"], self.previous, self.j.event, action=event)
        self.j.persist()

    def retained(self):
        if isinstance(self.arm, Treatment):
            from experiments.e2.trial import participant
            return participant(self.arm.store.path.parent, self.assets, "purser")[2]
        rows = self.arm.file.read_bytes()
        return {"plan": self.arm.read(), "plan_versions": plan_versions.retain(self.arm.file.parent, self.assets, rows)}
