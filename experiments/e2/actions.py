"""Charge public work at issue and retain publications attributed to each invocation."""
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from time import monotonic_ns

from experiments.e1 import capture


@dataclass
class Frame:
    action: dict
    method: str | None
    claimed: bool = False


def expected_method(kind, actor, inputs):
    if "adopt" in inputs:
        return "revise"
    if "assign" in inputs:
        return "assign_again"
    if "open_run" in inputs:
        return "opening"
    if kind == "submit":
        return "submit"
    if actor == "independent_observer":
        return "observe"
    if actor == "independent_verifier":
        return "verify"
    if actor == "closure":
        return "decide"
    return None


class PublicActions:
    def __init__(self, driver):
        self.driver = driver
        self.frame = ContextVar("public_work_frame", default=None)
        self.admission = RLock()

    @property
    def current(self):
        frame = self.frame.get()
        return frame.action if frame is not None else None

    def record(self, request):
        action = self.current
        if action is None:
            return False
        action.setdefault("requests", []).append(request)
        if "proposal" not in action:
            action["proposal"] = request
        self.driver.j.persist()
        return True

    def snapshot(self):
        rows, identity = self.driver.adapter.public_view()
        return rows, capture.checkpoint(self.driver.assets, rows, identity)

    def states(self, action, decision=None):
        rows, checkpoint = self.snapshot()
        capture.states(rows, checkpoint, self.driver.previous, self.driver.j.event, action=action, decision=decision)
        return checkpoint

    def call(self, kind, work, actor, inputs, operation, oid=None):
        """A second semantic operation within a callback obtains a distinct budget slot."""
        journal = self.driver.j
        parent = self.current
        with self.admission:
            _, before = self.snapshot()
            action = journal.begin_action(kind, work, actor, oid, inputs, before=before,
                    metadata={"parent_attempt": parent["attempt"] if parent is not None else None,
                              "issued_monotonic_ns": monotonic_ns()})
        frame = Frame(action, expected_method(kind, actor, inputs))
        token = self.frame.set(frame)
        try:
            return journal.run_action(action, operation, self.driver.assets)
        finally:
            self.frame.reset(token)
            action["finished_monotonic_ns"] = monotonic_ns()
            action["after"] = self.states(action, capture.returned_decision(action))
            journal.persist()

    def port(self, method, kind, work, actor, inputs, operation, oid=None):
        frame = self.frame.get()
        if (frame is None or frame.claimed or frame.method != method
                or (frame.action["subject"], frame.action["actor"]) != (work, actor)):
            return self.call(kind, work, actor, inputs,
                             lambda: self.port(method, kind, work, actor, inputs, operation, oid), oid)
        frame.claimed = True
        action = frame.action
        action["port_method"] = method
        _, action["port_before"] = self.snapshot()
        self.driver.j.persist()
        try:
            answer = operation()
            action["port_return"] = answer
            return answer
        finally:
            action["port_after"] = self.states(action)
            self.driver.j.persist()


class WorkPorts:
    """Both participant types expose the same public work, including through copied ports."""
    work_actions = None

    def public_operation(self, method, kind, work, actor, inputs, operation, oid=None):
        if self.work_actions is None:
            return operation()
        if oid is None and method in ("opening", "observe", "verify", "decide"):
            rows, _ = self.public_view()
            oid = capture.position(rows[work])[1]
        return self.work_actions.port(method, kind, work, actor, inputs, operation, oid)

    def assign_again(self, work):
        operation = super().assign_again
        return self.public_operation("assign_again", "plan", work, "planner", {"assign": work, "work": work},
                                     lambda: operation(work))

    def submit(self, work, oid):
        operation = super().submit
        return self.public_operation("submit", "submit", work, "executor", {"candidate": oid},
                                     lambda: operation(work, oid), oid)

    def opening(self, work, label):
        operation = super().opening
        return self.public_operation("opening", "plan", work, "planner", {"open_run": label},
                                     lambda: operation(work, label))

    def observe(self, work, label, observed):
        operation = super().observe
        return self.public_operation("observe", "observe", work, "independent_observer", {"criterion": label},
                                     lambda: operation(work, label, observed), observed["candidate"])

    def verify(self, work, label, observed):
        operation = super().verify
        return self.public_operation("verify", "decide", work, "independent_verifier", {"criterion": label},
                                     lambda: operation(work, label, observed), observed["candidate"])

    def decide(self, work, audit):
        operation = super().decide
        return self.public_operation("decide", "decide", work, "closure", {"request": "close", "public_review": audit},
                                     lambda: operation(work, audit))
