"""Durable caller release, response and decision sources for the public E3 tool boundary."""

import time

from experiments.e1.run import Journal, utc
from purser.kernel.store import Store

from . import treatment
from .fixture import ASSIGNMENT, TARGET, WORK, read, write
from .witness import native


class Actions(Journal):
    def __init__(self, directory, assets, arm, progress, *, mode="scripted"):
        super().__init__(arm, progress=progress, released=False, mode=mode)
        self.directory, self.assets = directory, assets

    def release(self):
        self.start, self.started, self.released = time.monotonic_ns(), utc(), True
        write(self.directory / "release-clock.json", {"at_ns": self.start, "utc": self.started})
        self.persist()

    def stop(self):
        running = self.end is None and self.released
        super().stop()
        if running:
            write(self.directory / "end-clock.json", {"at_ns": self.end, "utc": self.ended})

    def event(self, kind, payload):
        if kind == "action":
            source = {**payload, "issued_monotonic_ns": time.monotonic_ns(), "issued_utc": utc()}
            write(self.directory / "calls" / f"{payload['attempt']:03d}-request.json", source)
            payload.update(issued_monotonic_ns=source["issued_monotonic_ns"], issued_utc=source["issued_utc"],
                           request=self.assets.put(source, "json"))
        super().event(kind, payload)

    def invoke(self, kind, actor, inputs, operation):
        call = self.begin_action(kind, WORK, actor, TARGET, inputs)

        def complete(answer):
            source = {"action_id": call["action_id"], "return": answer, "finished_monotonic_ns": time.monotonic_ns(),
                      "finished_utc": utc()}
            write(self.directory / "calls" / f"{call['attempt']:03d}-response.json", source)
            call.update(source, response=self.assets.put(source, "json"))
            self.persist()

        try:
            result = operation(call["action_id"])
        except Exception as error:
            complete({"failure": f"{type(error).__name__}: {error}"})
            raise
        complete(result)
        return call["action_id"], result


class Tools:
    def __init__(self, directory, arm, repository, journal, supervisor):
        self.directory, self.repository, self.j, self.supervisor = directory, repository, journal, supervisor
        self.store = Store(directory / "kernel.sqlite") if arm == "purser" else None
        self.automatic = arm != "loop"

    def context(self):
        def release(_):
            packet = read(self.directory / "task.json")
            return {"packet": packet, "context": treatment.context(self.store) if self.store else None}
        return self.j.invoke("context", "executor", {"assignment": ASSIGNMENT}, release)

    def submit(self, intent):
        def submit(action):
            if self.store:
                return treatment.bind(self.store, self.repository, intent, action)
            with self.repository.broker() as broker:
                measured = broker.prepare(intent)
            packet = read(self.directory / "task.json")
            if intent != packet["intent"] or not set(measured["changed_paths"]) <= set(packet["grant"]["paths"]):
                raise ValueError("plain plan proposed an intent outside its available permission")
            write(self.directory / "bound.json", intent)
            return {"disposition": "admitted", "details": {"effect": intent["effect"], "state": "ready"}}
        return self.j.invoke("submit", "executor", {"intent": intent}, submit)

    def effect(self, intent, attempt):
        basis, ack = self.j.invoke("effect", "broker", {"effect": intent["effect"], "attempt": attempt},
                                   lambda action: self.supervisor.dispatch(intent, attempt, action))
        observation = None
        if self.automatic:
            basis, observation = self._observation(intent, attempt, "automatic")
        return basis, ack, observation

    def inspect(self, intent, attempt):
        return self._observation(intent, attempt, "explicit")

    def _observation(self, intent, attempt, delivery):
        def observe(action):
            if self.store:
                result = treatment.observe(self.store, self.repository, intent, attempt, action)
            else:
                result = {"report": self.repository.observe(intent, attempt), "receipt": None}
            # The protected witness samples Git separately while this invocation is still open.
            # Only the product/explicit observer's own original report is delivered to the worker.
            native(self.directory, self.repository.binding, read(self.directory / "task.json")["receipt_ref"],
                   "observation-" + action)
            return result
        actor = "independent_observer" if delivery == "automatic" else "executor"
        return self.j.invoke("reconcile", actor, {"effect": intent["effect"], "attempt": attempt, "delivery": delivery},
                             observe)

    def decide(self, decision):
        def publish(action):
            declaration = {**decision, "action_id": action, "published_ns": time.monotonic_ns(), "published_utc": utc()}
            write(self.directory / "declarations" / (action + ".json"), declaration)
            self.j.event("public_state", {"candidate": TARGET, "subject": WORK, "from": decision["previous"],
                         "to": decision["choice"], "diagnosis": [], "original_report": decision["original_report"],
                         "action_id": action, "declaration": self.j.assets.put(declaration, "json")})
            return declaration
        return self.j.invoke("decide", "executor", decision, publish)
