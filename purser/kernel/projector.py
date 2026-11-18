"""Deterministic interpretation of admitted domain events. No I/O or clock."""
from .wire import KernelError, parse_time
from . import workevents


def empty_projection():
    return {"records": [], "receipts": [], "edges": []}


# The schema closes each original event payload before its meaning is applied.
_FIELDS = {
    "CommitmentCreated": {"id": str, "root": str, "principal": str, "statement": str},
    "CommitmentEdited": {"id": str, "statement": str},
    "ApprovalRequested": {"id": str, "requester": str, "request": dict,
                          "request_digest": str, "read_versions": list, "expires_at": str},
    "ApprovalResolved": {"id": str, "state": str},
    "CommitmentActivated": {"id": str},
    "ProposalReceipted": {"actor_id": str, "proposal_id": str, "digest": str,
                          "disposition": str, "code": str, "details": dict},
}


def _matches(data, fields):
    return (type(data) is dict and data.keys() == fields.keys()
            and all(type(data[key]) is kind for key, kind in fields.items()))


def _receipt(projection, data, revision, end):
    identity = (data["actor_id"], data["proposal_id"])
    records = {(r["actor_id"], r["proposal_id"]): r for r in projection["receipts"]}
    prior = records.get(identity)
    if (data["disposition"] not in ("pending", "admitted", "rejected")
            or prior is not None and (prior["digest"] != data["digest"] or prior["disposition"] != "pending"
                                      or data["disposition"] == "pending")):
        raise KernelError("integrity_error", {"revision": revision})
    records[identity] = dict(data, revision=end)
    return {**projection, "receipts": [records[k] for k in sorted(records)]}


def apply_event(projection, event_type, data, revision, batch_end, *, event_version=1):
    location = {"revision": revision}
    if event_version == 1 and event_type in workevents.EVENTS:
        return workevents.project(projection, event_type, data, revision)
    if event_type not in _FIELDS or not (
        event_version == 1 or event_version == 0 and event_type == "CommitmentCreated"
    ):
        raise KernelError("unsupported_version", location)
    if event_version == 0:
        old = {"id": str, "root": str, "principal": str, "text": str}
        if not _matches(data, old):
            raise KernelError("integrity_error", location)
        data = dict(data)
        data["statement"] = data.pop("text")
    if not _matches(data, _FIELDS[event_type]):
        raise KernelError("integrity_error", location)
    if event_type == "ProposalReceipted":
        return _receipt(projection, data, revision, batch_end)

    records = {r["id"]: r for r in projection["records"]}
    identifier = data["id"]

    def create(key, kind, commitment, fields):
        if key in records:
            raise KernelError("integrity_error", location)
        records[key] = {"id": key, "kind": kind, "commitment": commitment,
                        "changed": revision, "fields": fields}

    def update(kind, **changes):
        prior = records.get(identifier)
        if prior is None or prior["kind"] != kind:
            raise KernelError("integrity_error", location)
        records[identifier] = {**prior, "changed": revision, "fields": {**prior["fields"], **changes}}

    if event_type == "CommitmentCreated":
        create(identifier, "Commitment", identifier,
               {"state": "proposed", "root": data["root"], "statement": data["statement"],
                "principal": data["principal"]})
        create(data["root"], "WorkItem", identifier,
               {"state": "open", "generation": 1, "parent": None, "candidate": None})
    elif event_type == "CommitmentEdited":
        update("Commitment", statement=data["statement"])
    elif event_type == "CommitmentActivated":
        update("Commitment", state="active")
    elif event_type == "ApprovalRequested":
        commitment = data["request"].get("commitment")
        if type(commitment) is not str or records.get(commitment, {}).get("kind") != "Commitment":
            raise KernelError("integrity_error", location)
        if any(not _matches(v, {"key": str, "changed": int}) for v in data["read_versions"]):
            raise KernelError("integrity_error", location)
        try:
            parse_time(data["expires_at"])
        except KernelError as exc:
            raise KernelError("integrity_error", location) from exc
        create(identifier, "Approval", commitment,
               {**{k: v for k, v in data.items() if k != "id"}, "state": "pending"})
    elif event_type == "ApprovalResolved":
        if data["state"] not in ("approved", "denied", "expired", "stale"):
            raise KernelError("integrity_error", location)
        update("Approval", state=data["state"])
    return {**projection, "records": [records[k] for k in sorted(records)]}
