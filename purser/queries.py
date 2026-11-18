"""Read-only operator answers from one verified snapshot."""

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
    return {
        "v": 1, "revision": snapshot["revision"], "as_of": as_of,
        "record": record, "reasons": reasons,
    }
