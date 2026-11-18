"""Issue each public work request once and observe the value at its receiving boundary."""
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from sqlite3 import Error as SQLiteError
from threading import RLock
from time import monotonic_ns

from experiments.e1 import capture
from experiments.e1.artifacts import encoded, sha

from . import clock
from .operations import method_for

PENDING = object()


def wire(value):
    # The frozen action envelope is an object. A worker may legitimately return a scalar.
    return value if isinstance(value, dict) else {"value": value}


@dataclass
class Frame:
    action: dict
    forwarded: tuple | None = None
    forwarded_value: object = PENDING


@dataclass
class Transport:
    participant: object
    method: str
    frame: Frame
    consumed: bool = False
    bound_aliases: bool = False

    def matches(self, participant, method, frame, kind, work, actor, inputs, oid):
        if self.consumed or self.method != method or self.frame is not frame:
            return False
        if self.bound_aliases and frame.forwarded is not None:
            return False
        if self.participant is not participant and not (
            self.bound_aliases and self.participant._binding is participant._binding
        ):
            return False
        declared = frame.action["work_request"]
        if (kind, work, actor) != (declared["action"], declared["subject"], declared["actor"]):
            return False
        if oid != declared["candidate"] and not (oid is None and method in ("opening", "decide")):
            return False
        shared = inputs.keys() & declared["input"].keys()
        return all(inputs[key] == declared["input"][key] for key in shared)


class Delivery:
    """A trusted receiving continuation can attest the bytes it actually observed."""
    def __init__(self, actions, action):
        self.actions, self.action = actions, action
        self.attempt = action["attempt"]

    def received(self, subject, value):
        self._save("received", subject, wire(value))

    def not_delivered(self):
        """Use only for a physically established failure before transfer."""
        self._save("not_delivered")

    def _save(self, state, subject=None, value=None):
        with self.actions.admission:
            if self.actions.current is not self.action:
                raise ValueError("delivery does not belong to the active invocation")
            previous = self.action.get("delivery")
            observed = previous["observed_monotonic_ns"] if previous is not None else monotonic_ns()
            witness = {"attempt": self.attempt, "state": state, "observed_monotonic_ns": observed}
            if state == "received":
                ref = self.actions.driver.assets.put(value, "json")
                witness.update(subject=subject, payload=ref, sha256=ref["sha256"])
            if previous is not None and previous != witness:
                raise ValueError("contradictory delivery observations")
            self.action["delivery"] = witness
            self.actions.driver.j.persist()


def local_receiver(packet, witness):
    """Entry of the in-process participant which actually accepts the scoped packet."""
    witness.received(packet["work"]["id"], packet)
    return packet


class PublicActions:
    def __init__(self, driver):
        self.driver = driver
        self.frame = ContextVar("public_work_frame", default=None)
        self.transport = ContextVar("public_work_transport", default=None)
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
        action.setdefault("proposal", request)
        self.driver.j.persist()
        return True

    def snapshot(self):
        rows, identity = self.driver.adapter.public_view()
        return rows, capture.checkpoint(self.driver.assets, rows, identity)

    def states(self, action, decision=None):
        with self.admission:
            rows, checkpoint = self.snapshot()
            capture.states(rows, checkpoint, self.driver.previous, self.driver.j.event, action=action, decision=decision)
        return checkpoint

    def call(self, kind, work, actor, inputs, operation, oid=None):
        journal = self.driver.j
        parent = self.current
        method = method_for(kind, actor, inputs)
        if method is None:
            raise ValueError("unknown public work operation")
        request = {"method": method, "action": kind, "actor": actor, "subject": work,
                   "candidate": oid, "input": deepcopy(inputs)}
        with self.admission:
            metadata = {"parent_attempt": parent["attempt"] if parent else None, "work_request": request}
            if any(e["kind"] == "fault" and "finished_monotonic_ns" not in e["payload"] for e in journal.rows):
                # During ingress alone we need a sample on both sides of issue. A head
                # already containing the premise proves the next issue is post-input.
                try:
                    _, metadata["pre_issue_head"] = self.snapshot()
                except (OSError, SQLiteError, ValueError, KeyError):
                    pass
            metadata["issued_monotonic_ns"] = monotonic_ns()
            action = journal.begin_action(kind, work, actor, oid, inputs, metadata=metadata)
        token = self.frame.set(Frame(action))
        value = None
        def perform():
            nonlocal value
            with self.admission:
                _, action["before"] = self.snapshot()
                journal.persist()
            value = operation()
            return wire(value)
        try:
            journal.run_action(action, perform, self.driver.assets)
            return value
        finally:
            self.frame.reset(token)
            clock.finish_activity(journal, action)
            decision = None if "port_after" in action else capture.returned_decision(action)
            action["after"] = self.states(action, decision)
            journal.persist()

    def start_work(self):
        """Called at the local actor's entry, after the public request has arrived."""
        action = self.current
        if "work_started" not in action:
            action["work_started"] = deepcopy(action["work_request"])
            self.driver.j.persist()

    def forward(self, participant, method, *args, **kwargs):
        """One explicit transport of the current request onto its bound participant port."""
        frame = self.frame.get()
        if frame is None or frame.action["work_request"]["method"] != method:
            raise ValueError("forwarding requires the matching admitted public request")
        token = self.transport.set(Transport(participant, method, frame))
        try:
            return getattr(participant, method)(*args, **kwargs)
        finally:
            self.transport.reset(token)

    def controller_transport(self, participant, method, operation):
        """The controller callback forwards one issue to the trial's bound participants."""
        token = self.transport.set(Transport(participant, method, self.frame.get(), bound_aliases=True))
        try:
            return operation()
        finally:
            self.transport.reset(token)

    def port(self, participant, method, kind, work, actor, inputs, operation, oid=None):
        target = self.transport.get()
        frame = self.frame.get()
        if target and target.matches(participant, method, frame, kind, work, actor, inputs, oid):
            target.consumed = True
            identity = (method, work, actor, oid, sha(encoded(inputs)))
            if frame.forwarded is not None:
                if frame.forwarded != identity or frame.forwarded_value is PENDING:
                    raise ValueError("forwarded request already outstanding or disagrees with original")
                return deepcopy(frame.forwarded_value)
            frame.forwarded = identity
            token = self.transport.set(None)
            try:
                answer = self._run_port(method, inputs, operation)
                frame.forwarded_value = deepcopy(answer)
                return answer
            finally:
                self.transport.reset(token)
        return self.call(kind, work, actor, inputs, lambda: self._run_port(method, inputs, operation), oid)

    def _run_port(self, method, inputs, operation):
        action = self.current
        action.update(port_method=method, port_input=deepcopy(inputs))
        rows, action["port_before"] = self.snapshot()
        if method in ("opening", "decide"):
            oid = capture.position(rows[action["subject"]])[1]
            if action["candidate"] is not None and action["candidate"] != oid:
                raise ValueError("the public work candidate changed before participant entry")
            action["candidate"] = action["work_request"]["candidate"] = oid
        self.start_work()
        try:
            answer = operation()
            action["port_return"] = deepcopy(wire(answer))
            return answer
        finally:
            reply = dict(action, **{"return": action.get("port_return")})
            decision = capture.returned_decision(reply)
            if decision and method == "verify" and inputs["observation"]["verdict"] != "pass":
                decision["diagnosis"] = [inputs["criterion"]]
            action["port_after"] = self.states(action, decision)
            self.driver.j.persist()

    def context(self, participant, work, actor, receiver):
        def release():
            rows, identity, packet = participant._context_from_publication(work)
            action = self.current
            action["context_source"] = capture.checkpoint(self.driver.assets, rows, identity)
            action["prepared_context"] = self.driver.assets.put(packet, "json")
            self.driver.j.persist()
            if receiver is None:
                # The public accessor returns bytes to its caller. Further delivery to an
                # unspecified recipient cannot be certified from a prepared response.
                return packet
            return receiver(packet, Delivery(self, action))
        return self.port(participant, "context", "context", work, actor, {"work": work}, release)


class Binding:
    def __init__(self):
        self.actions = None


class WorkPorts:
    """Public operations on both treatments; aliases share the trial's admission owner."""
    def __init__(self, *args, **kwargs):
        self._binding = Binding()
        super().__init__(*args, **kwargs)

    @property
    def work_actions(self):
        return self._binding.actions

    @work_actions.setter
    def work_actions(self, actions):
        if self._binding.actions is not None and self._binding.actions is not actions:
            raise ValueError("participant already bound to another run")
        self._binding.actions = actions

    def context(self, work, *, actor="executor", receiver=None):
        if self.work_actions is None:
            return self._context_from_publication(work)[2]
        return self.work_actions.context(self, work, actor, receiver)

    def candidate_id(self, work):
        """The planner's narrow input for an existing submission."""
        rows, _ = self.public_view()
        return capture.position(rows[work])[1]

    def public_operation(self, method, kind, work, actor, inputs, operation, oid=None):
        if self.work_actions is None:
            return operation()
        return self.work_actions.port(self, method, kind, work, actor, inputs, operation, oid)

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
        return self.public_operation("observe", "observe", work, "independent_observer",
                                     {"criterion": label, "observation": observed},
                                     lambda: operation(work, label, observed), observed["candidate"])

    def verify(self, work, label, observed):
        operation = super().verify
        return self.public_operation("verify", "decide", work, "independent_verifier",
                                     {"criterion": label, "observation": observed},
                                     lambda: operation(work, label, observed), observed["candidate"])

    def decide(self, work, audit):
        operation = super().decide
        return self.public_operation("decide", "decide", work, "closure", {"request": "close", "public_review": audit},
                                     lambda: operation(work, audit))
