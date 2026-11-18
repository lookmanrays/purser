"""Read-only operator answers from one verified snapshot."""

from .kernel.wire import KernelError, digest


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
        "answers": {"q01": commitments},
    }


def why(snapshot, as_of, record_id):
    records = {row["id"]: row for row in snapshot["projection"]["records"]}
    if record_id not in records:
        raise KernelError("not_found")
    record = records[record_id]
    commitment = records[record["commitment"]]
    if commitment["fields"]["state"] == "active":
        reason = {"code": "active_commitment", "refs": [commitment["fields"]["root"]]}
    else:
        approvals = [
            row["id"] for row in records.values()
            if row["kind"] == "Approval" and row["commitment"] == commitment["id"]
            and row["fields"]["state"] == "pending"
        ]
        reason = {"code": "awaiting_activation", "refs": approvals or [commitment["id"]]}
    return {
        "v": 1, "revision": snapshot["revision"], "as_of": as_of,
        "record": record, "reasons": [reason],
    }
