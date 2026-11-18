"""One foreground turn over durable state. Adapters are supplied by trusted launchers."""
from uuid import uuid4

from .kernel.gate import admit
from .kernel.wire import KernelError
from .kernel.workview import WorkView


def context_pack(snapshot, assignment_id):
    view = WorkView(snapshot["projection"])
    row = view.records.get(assignment_id)
    if not row or row["kind"] != "Assignment":
        raise KernelError("not_found")
    assignment = row["fields"]
    work_id, scope = assignment["work"], row["commitment"]
    work, commitment = view.fields(work_id), view.fields(scope)
    if (commitment["state"] != "active" or assignment["state"] != "current"
            or work.get("assignment") != assignment_id or assignment["generation"] != work["generation"]
            or view.graph.issues(work_id)):
        raise KernelError("inactive_assignment")
    obligations = []
    for oid in view.related[work_id, "Obligation"]:
        criterion = view.fields(oid)
        state, witness = view.qualification(oid)
        obligations.append({"id": oid, "version": criterion["version"], "title": criterion["title"],
                            "state": state, "verifier": criterion["verifier"],
                            "definition_sha256": criterion["definition_sha256"],
                            "environment_sha256": criterion["environment_sha256"], "witness": witness})
    observations = []
    for run in view.related[work_id, "VerificationRun"]:
        if view.fields(run)["assignment"] == assignment_id:
            for oid in view.observations[run]:
                observations.append({"id": oid, **view.fields(oid)})
    return {
        "v": 1, "revision": snapshot["revision"], "head_hash": snapshot["head_hash"],
        "commitment": {"id": scope, "statement": commitment["statement"]},
        "assignment": {"id": assignment_id, "executor": assignment["executor"]},
        "work": {"id": work_id, "generation": work["generation"], "state": work["state"],
                 "title": work.get("title", commitment["statement"]), "candidate": work["candidate"],
                 "children": list(view.children[work_id]), "coverage": work.get("coverage")},
        "acceptance": "principal" if work_id == commitment["root"] else "automatic",
        "limits": {"results_per_assignment": 1, "shared_effects": 0},
        "obligations": obligations, "observations": observations, "reasons": view.reasons(work_id),
        "decisions": view.graph.decisions(work_id), "capabilities": [],
    }


def tick(store, executors=None, *, clock=None):
    """Dispatch at most one eligible local action. An executor returns its candidate identity."""
    snapshot = store.snapshot()
    view = WorkView(snapshot["projection"])
    actions = view.answers()["q10"]
    executors = {} if executors is None else executors
    for action in actions:
        name, work_id = action["action"], action["subject"]
        if name not in ("execute", "evaluate_close"):
            continue
        work = view.fields(work_id)
        scope = view.records[work_id]["commitment"]
        # A snapshot can select the same opportunity in two processes. An invocation has
        # its own receipt identities so transport replay cannot release a different callback.
        token = uuid4().hex

        def propose(actor, role, assignment, operation, prefix):
            binding = {"actor_id": actor, "role": role, "principal_id": "principal:local",
                       "commitment": scope, "assignment": assignment}
            proposal = {"v": 1, "proposal_id": prefix + token, "commitment": scope,
                        "base_revision": snapshot["revision"], "ops": [operation], "justification": [work_id]}
            options = {} if clock is None else {"clock": clock}
            return admit(store, binding, proposal, **options)

        if name == "execute":
            assignment_id = work["assignment"]
            actor = view.fields(assignment_id)["executor"]
            if actor not in executors:
                continue
            release = propose(actor, "executor", assignment_id,
                              {"op": "assignment.dispatch", "id": assignment_id}, "p-release-")
            if release["disposition"] != "admitted":
                return {"v": 1, "state": "refused", "revision": release["revision"], "action": action, "receipt": release}
            candidate = executors[actor](context_pack(snapshot, assignment_id))
            op = {"op": "result.submit", "id": "R-" + token, "assignment": assignment_id, "candidate": candidate}
            role = "executor"
        else:
            actor, role, assignment_id = "closure:local-driver", "closure", None
            op = {"op": "work.close", "id": work_id}
        result = propose(actor, role, assignment_id, op, "p-driver-")
        return {"v": 1, "state": "dispatched", "revision": result["revision"],
                "action": action, "receipt": result}
    return {"v": 1, "state": "idle", "revision": snapshot["revision"], "frontier": actions}
