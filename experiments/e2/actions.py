"""Charge public work at issue and retain publications attributed to each invocation."""
from dataclasses import dataclass

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
        self.frames = []

    @property
    def current(self):
        return self.frames[-1].action if self.frames else None

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
        frame = None
        _, before = self.snapshot()
        parent = self.current

        def run():
            nonlocal frame
            action = journal.rows[-1]["payload"]
            action["parent_attempt"] = parent["attempt"] if parent is not None else None
            frame = Frame(action, expected_method(kind, actor, inputs))
            self.frames.append(frame)
            journal.persist()
            try:
                return operation()
            finally:
                self.frames.pop()

        try:
            return journal.action(kind, work, actor, oid, inputs, run, before=before, assets=self.driver.assets)
        finally:
            if frame is not None:
                action = frame.action
                action["after"] = self.states(action)
                journal.persist()

    def port(self, method, kind, work, actor, inputs, operation, oid=None):
        frame = self.frames[-1] if self.frames else None
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
            return operation()
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
