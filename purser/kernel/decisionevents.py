"""Decision facts folded into the existing records and typed edges."""
from .rules import HASH, ID, matches
from .support import SupportGraph
from .wire import KernelError

EVENTS = {
    "AssumptionCreated": "statement observer",
    "DecisionRecorded": "choice alternatives rationale support triggers supersedes",
    "DecisionOpinionRecorded": "decision opinion author",
    "PremiseObserved": "assumption finding observer artifact_sha256",
    "DependencyAdded": "support",
    "DependencyRemoved": "support",
}


def text(value):
    return type(value) is str and bool(value.strip())


def decision_fields(data):
    for name in ("support", "triggers", "alternatives"):
        items = data[name]
        if type(items) is not list or not all(text(v) for v in items) or len(items) != len(set(items)):
            return False
    return (text(data["choice"]) and data["choice"] in data["alternatives"] and text(data["rationale"])
            and all(matches(ID, key) for key in data["support"])
            and set(data["triggers"]) <= set(data["support"])
            and (data["supersedes"] is None or matches(ID, data["supersedes"])))


def project(projection, name, data, revision):
    error = KernelError("integrity_error", {"revision": revision})
    if (type(data) is not dict or data.keys() != {"id", "commitment", *EVENTS[name].split()}
            or not matches(ID, data["id"]) or not matches(ID, data["commitment"])):
        raise error
    key, scope = data["id"], data["commitment"]
    records = {r["id"]: r for r in projection["records"]}
    edges = {(e["kind"], e["from_id"], e["to_id"]): e for e in projection["edges"]}
    graph = SupportGraph(records, edges.values())

    def require(item, *kinds):
        if not matches(ID, item):
            raise error
        row = records.get(item)
        if not row or row["kind"] not in kinds or row["commitment"] != scope:
            raise error
        return row["fields"]

    def create(kind, fields):
        if key in records:
            raise error
        records[key] = {"id": key, "kind": kind, "commitment": scope, "changed": revision, "fields": fields}

    def update(item, **changes):
        row = records[item]
        records[item] = {**row, "changed": revision, "fields": {**row["fields"], **changes}}

    def add(dependent, support):
        edges["depends_on", dependent, support] = {"kind": "depends_on", "from_id": dependent,
                                                  "to_id": support, "changed": revision}

    def stale(item, cause):
        f = records[item]["fields"]
        update(item, state="stale", causes=sorted({*f.get("causes", []), cause}),
               closed_outcome=None, accepted_outcome=None)

    require(scope, "Commitment")
    if name == "AssumptionCreated":
        if not text(data["statement"]) or not text(data["observer"]):
            raise error
        create("Assumption", {"statement": data["statement"], "observer": data["observer"],
                              "state": "held", "causes": [], "affected": []})
    elif name == "DecisionRecorded":
        if not decision_fields(data):
            raise error
        for support in data["support"]:
            if require(support, "Assumption")["state"] != "held":
                raise error
        previous = data["supersedes"]
        if previous is not None and require(previous, "Decision")["state"] != "reconsidering":
            raise error
        create("Decision", {**{k: data[k] for k in EVENTS[name].split()}, "state": "effective",
                            "effective_revision": revision, "causes": []})
        for support in data["support"]:
            add(key, support)
        if previous:
            update(previous, state="superseded", superseded_by=key)
            for dependent in graph.incoming[previous]:
                del edges["depends_on", dependent, previous]
                add(dependent, key)
                update(dependent)
    elif name == "DecisionOpinionRecorded":
        require(data["decision"], "Decision")
        if not text(data["opinion"]) or not text(data["author"]):
            raise error
        create("DecisionOpinion", {k: data[k] for k in EVENTS[name].split()})
    elif name == "PremiseObserved":
        premise = data["assumption"]
        fields = require(premise, "Assumption")
        if (data["finding"] not in ("confirms", "contradicts") or data["observer"] != fields["observer"]
                or not matches(HASH, data["artifact_sha256"])):
            raise error
        create("Observation", {k: data[k] for k in EVENTS[name].split()})
        if data["finding"] == "contradicts" and fields["state"] == "held":
            affected = graph.affected(premise)
            # Preserve the exact causal graph even when a later decision redirects live edges.
            reached = affected | {premise}
            causal = [{"kind": "depends_on", "from_id": item, "to_id": support}
                      for item in sorted(affected) for support in sorted(graph.outgoing[item] & reached)]
            update(premise, state="invalidated", causes=[key], affected=sorted(affected), impact_edges=causal)
            for item in sorted(affected):
                row = records[item]
                if row["kind"] == "Decision":
                    update(item, state="reconsidering", causes=sorted({*row["fields"]["causes"], key}))
                else:
                    stale(item, key)
    else:
        support = data["support"]
        require(key, "WorkItem")
        target = require(support, "WorkItem", "Decision", "Assumption")
        edge = ("depends_on", key, support)
        if name == "DependencyAdded":
            if edge in edges or target.get("state") == "superseded":
                raise error
            add(key, support)
        else:
            if edge not in edges:
                raise error
            del edges[edge]
        update(key)  # Version outgoing membership, including deletions, in the dependent's read set.
        for item in sorted({key} | graph.affected(key)):
            f = records[item]["fields"]
            if f.get("assignment") or f.get("state") == "closed":
                stale(item, support)
    return {**projection, "records": [records[k] for k in sorted(records)],
            "edges": [edges[k] for k in sorted(edges)]}
