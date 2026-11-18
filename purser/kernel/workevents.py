"""Version-one work events interpreted by the sole deterministic projector."""
from .rules import HASH, candidate, matches
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
_LINKS = {"work": "WorkItem", "parent": "WorkItem", "assignment": "Assignment", "result": "Result",
          "obligation": "Obligation", "run": "VerificationRun", "observation": "Observation"}


def project(projection, name, data, revision):
    error = KernelError("integrity_error", {"revision": revision})
    if type(data) is not dict or data.keys() != {"id", *EVENTS[name].split()}:
        raise error
    for key, value in data.items():
        expected = int if key in _INTS else dict if key in _OBJECTS else str
        if type(value) is not expected or key in _INTS and value < 1:
            raise error
        if (key.endswith("_sha256") or key == "outcome") and not matches(HASH, value):
            raise error
    if "candidate" in data and not candidate(data["candidate"]):
        raise error
    if "verdict" in data and data["verdict"] not in ("pass", "fail", "inconclusive"):
        raise error
    if name == "WorkReopened" and data["state"] not in ("open", "assigned", "submitted"):
        raise error
    if "coverage" in data:
        c = data["coverage"]
        if c.keys() != {"kind", "k"} or not (c["kind"] in ("all", "any") and c["k"] is None
                or c["kind"] == "k_of_n" and type(c["k"]) is int and c["k"] > 0):
            raise error
    records = {r["id"]: r for r in projection["records"]}
    for field, kind in _LINKS.items():
        if field in data:
            target = records.get(data[field])
            if not target or target["kind"] != kind or target["commitment"] != data["commitment"]:
                raise error
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
        recovered = {"causes": []} if "causes" in records[work]["fields"] else {}
        update(work, "WorkItem", state="assigned", generation=data["generation"], assignment=identity, **recovered,
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
