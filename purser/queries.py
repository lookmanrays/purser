"""Read-only operator answers from one verified snapshot."""

from .kernel.support import SupportGraph
from .kernel.wire import KernelError, digest
from .kernel.workview import WorkView


def status(snapshot, as_of):
    projection = snapshot["projection"]
    commitments = [
        {"commitment": row["id"], "principal": row["fields"]["principal"]}
        for row in projection["records"]
        if row["kind"] == "Commitment" and row["fields"]["state"] == "active"
    ]
    return {
        "v": 1,
        "revision": snapshot["revision"],
        "head_hash": snapshot["head_hash"],
        "projection_hash": digest("projection", projection),
        "as_of": as_of,
        "answers": {"q01": commitments, **WorkView(projection).answers()},
    }


def why(snapshot, as_of, record_id):
    view = WorkView(snapshot["projection"])
    records = view.records
    if record_id not in records:
        raise KernelError("not_found")
    record = records[record_id]
    commitment = records[record["commitment"]]
    if record["kind"] == "Commitment" and commitment["fields"]["state"] in ("active", "satisfied"):
        active = commitment["fields"]["state"] == "active"
        reasons = [{"code": "active_commitment" if active else "accepted_outcome",
                    "refs": [commitment["fields"]["root"]]}]
    else:
        reasons = view.reasons(record_id)
    result = {"v": 1, "revision": snapshot["revision"], "as_of": as_of, "record": record, "reasons": reasons}
    if record["kind"] == "WorkItem" and any(
        r["commitment"] == record["commitment"] and r["kind"] in ("Assumption", "Decision") for r in records.values()
    ):
        causes = []
        for ref in record["fields"].get("causes", []):
            observation = records[ref]
            if observation["kind"] == "Observation" and "assumption" in observation["fields"]:
                premise = observation["fields"]["assumption"]
                at_change = SupportGraph(records, view.fields(premise)["impact_edges"])
                causes.append({"observation": ref, "assumption": premise, "revision": observation["changed"],
                               "edges": at_change.edges_for(record_id)})
        result["support"] = {"edges": view.graph.edges_for(record_id), "causes": causes,
                             "basis": view.basis(record_id), "outcome": view.outcome(record_id)}
    return result
