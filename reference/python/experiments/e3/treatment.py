"""Public Purser enrollment, executor, broker and observer for the fixed E3 intent."""

from itertools import count

from purser.driver import context_pack
from purser.kernel.artifacts import Artifacts
from purser.kernel.gate import admit
from purser.kernel.store import Store
from purser.kernel.wire import decode

from experiments.e1.purser_arm import retained_kernel
from .fixture import ASSIGNMENT, EXECUTOR, EXPIRES, PRINCIPAL, SCOPE, TASK, WORK

ACTORS = {"principal": PRINCIPAL, "planner": "planner:study", "executor": EXECUTOR}


def bound(role):
    return {"role": role, "actor_id": ACTORS[role], "principal_id": PRINCIPAL,
            "commitment": None if role == "principal" else SCOPE,
            "assignment": ASSIGNMENT if role == "executor" else None}


def frame(store, identity, operation):
    return {"v": 1, "proposal_id": identity, "commitment": SCOPE, "base_revision": store.snapshot()["revision"],
            "justification": [], "ops": [operation]}


def enroll(directory, packet, repository):
    store = Store(directory / "kernel.sqlite")
    store.initialize()
    intent = packet["intent"]
    serial = count(1)

    def send(role, op, *, expected="ok"):
        response = admit(store, bound(role), frame(store, f"setup-{next(serial)}", op))
        if response["code"] != expected:
            raise ValueError(f"fixture admission failed: {op['op']} {response}")
        return response

    def approve(receipt):
        send("principal", {"op": "approval.answer", "id": receipt["details"]["approval_id"],
                           "request_digest": receipt["digest"], "decision": "approve"})

    send("principal", {"op": "commitment.create", "id": SCOPE, "root": "W-root", "statement": TASK})
    approve(send("principal", {"op": "commitment.request_acceptance", "id": SCOPE, "expires_at": EXPIRES},
                 expected="needs_principal"))
    send("planner", {"op": "work.create", "id": WORK, "parent": "W-root", "title": TASK})
    send("planner", {"op": "effect.require", "id": intent["effect"], "work": WORK, "slot": intent["slot"],
                     "repository": intent["repository"], "repository_instance": repository.binding["repository_instance"],
                     "resource": intent["resource"], "paths": packet["grant"]["paths"]})
    send("planner", {"op": "work.assign", "id": ASSIGNMENT, "work": WORK, "executor": EXECUTOR})
    approve(send("planner", {"op": "grant.request", **packet["grant"]}, expected="needs_principal"))
    send("executor", {"op": "result.submit", "id": "R-prepared", "assignment": ASSIGNMENT, "candidate": intent["target"]})


def context(store):
    return context_pack(store.snapshot(), ASSIGNMENT)


def bind(store, repository, intent, action):
    from purser.effects.foreground import request_effect
    proposal = frame(store, action, {"op": "effect.request", "id": intent["effect"], "intent": intent})
    return request_effect(store, repository, bound("executor"), proposal)


def dispatch(store, repository, intent, attempt, action, **cutpoints):
    from purser.effects.foreground import dispatch_effect
    proposal = frame(store, action, {"op": "effect.authorize", "id": attempt, "effect": intent["effect"]})
    return dispatch_effect(store, repository, proposal, **cutpoints)


def observe(store, repository, intent, attempt, action):
    from purser.effects.foreground import reconcile_effect
    oid = "Obs-" + attempt
    proposal = frame(store, action, {"op": "effect.reconcile", "id": oid, "effect": intent["effect"], "attempt": attempt})
    receipt = reconcile_effect(store, repository, proposal)
    report = None
    if receipt["disposition"] == "admitted":
        records = store.snapshot(revision=receipt["revision"])["projection"]["records"]
        fields = next(row["fields"] for row in records if row["id"] == oid and row["kind"] == "Observation")
        report = decode(Artifacts(store.path).read(fields["artifact_sha256"]))
    return {"report": report, "receipt": receipt}


def evidence(directory, assets):
    store = Store(directory / "kernel.sqlite")
    snapshot = store.snapshot()
    trace = retained_kernel(store, snapshot)
    artifacts = Artifacts(store.path)
    trace["kernel_artifacts"] = {fields["artifact_sha256"]: assets.put(artifacts.read(fields["artifact_sha256"]), "bin")
                                 for row in snapshot["projection"]["records"] if row["kind"] == "Observation"
                                 for fields in [row["fields"]] if "artifact_sha256" in fields}
    return trace
