"""Version-one work events interpreted by the sole deterministic projector."""
from .wire import KernelError

EVENTS = {
    "WorkCreated": "commitment parent title", "CoverageSet": "coverage",
    "WorkAssigned": "commitment work executor generation",
    "ResultSubmitted": "commitment work assignment generation producer candidate",
    "ObligationSet": "commitment work version title observer verifier definition_sha256 environment_sha256",
    "RunOpened": ("commitment work generation assignment result producer candidate obligation criterion_version "
                  "observer verifier definition_sha256 environment_sha256"),
    "ObservationRecorded": "commitment run observer artifact_sha256",
    "EvidenceRecorded": "commitment run observation verifier verdict",
    "ObligationWaived": "commitment obligation version work generation result candidate reason principal",
    "WorkAccepted": "outcome principal", "WorkClosed": "outcome", "WorkReopened": "state",
    "CommitmentSatisfied": "", "CommitmentReopened": "",
}
_INTS = {"generation", "criterion_version", "version"}
_OBJECTS = {"candidate", "coverage"}


def project(projection, name, data, revision):
    error = KernelError("integrity_error", {"revision": revision})
    if type(data) is not dict or data.keys() != {"id", *EVENTS[name].split()}:
        raise error
    for key, value in data.items():
        expected = int if key in _INTS else dict if key in _OBJECTS else str
        if type(value) is not expected:
            raise error
    records = {r["id"]: r for r in projection["records"]}
    identity = data["id"]
    fields = {k: v for k, v in data.items() if k not in ("id", "commitment")}
    edges = projection["edges"]

    def create(kind, values):
        if identity in records:
            raise error
        records[identity] = {"id": identity, "kind": kind, "commitment": data["commitment"],
                             "changed": revision, "fields": values}

    def update(key, kind, **changes):
        record = records.get(key)
        if record is None or record["kind"] != kind:
            raise error
        records[key] = {**record, "changed": revision, "fields": {**record["fields"], **changes}}

    if name == "WorkCreated":
        create("WorkItem", dict(fields, generation=1, state="open", candidate=None))
        edge = {"kind": "child_of", "from_id": identity, "to_id": data["parent"], "changed": revision}
        edges = sorted([*edges, edge], key=lambda e: (e["kind"], e["from_id"], e["to_id"]))
    elif name == "CoverageSet":
        update(identity, "WorkItem", coverage=data["coverage"])
    elif name == "WorkAssigned":
        work = data["work"]
        prior = records.get(work, {}).get("fields", {}).get("assignment")
        if prior:
            update(prior, "Assignment", state="superseded")
        create("Assignment", dict(fields, state="current"))
        update(work, "WorkItem", state="assigned", generation=data["generation"], assignment=identity,
               candidate=None, result=None, accepted_outcome=None, closed_outcome=None)
    elif name == "ResultSubmitted":
        create("Result", fields)
        update(data["work"], "WorkItem", candidate=data["candidate"], state="submitted", result=identity)
    elif name == "ObligationSet":
        values = dict(fields, current_run=None, waiver=None)
        if identity in records:
            update(identity, "Obligation", **values)
        else:
            create("Obligation", values)
    elif name == "RunOpened":
        create("VerificationRun", fields)
        update(data["obligation"], "Obligation", current_run=identity)
    elif name == "ObservationRecorded":
        create("Observation", fields)
    elif name == "EvidenceRecorded":
        create("Evidence", fields)
    elif name == "ObligationWaived":
        create("Waiver", fields)
        update(data["obligation"], "Obligation", waiver=identity)
    elif name == "WorkAccepted":
        update(identity, "WorkItem", accepted_outcome=data["outcome"])
    elif name == "WorkClosed":
        update(identity, "WorkItem", state="closed", closed_outcome=data["outcome"])
    elif name == "WorkReopened":
        update(identity, "WorkItem", state=data["state"], closed_outcome=None, accepted_outcome=None)
    else:
        update(identity, "Commitment", state="satisfied" if name == "CommitmentSatisfied" else "active")
    return {**projection, "records": [records[k] for k in sorted(records)], "edges": edges}
