"""Admission rules for commitments' premises, decisions and dependency declarations."""
from .decisionevents import decision_fields, text
from .rules import ID, Refusal, matches
from .workview import WorkView

OPS = {
    "assumption.create": "id statement observer",
    "decision.record": "id choice alternatives rationale support triggers supersedes",
    "decision.opine": "id decision opinion",
    "assumption.observe": "id assumption finding",
    "dependency.add": "id support",
    "dependency.remove": "id support",
}


def bound(op, binding):
    name = op["op"]
    role = binding["role"]
    if name == "assumption.observe":
        return role == "observer" and binding["assignment"] == op["assumption"] and "premise" in binding
    if name == "decision.opine" and role == "executor":
        return binding["assignment"] is not None
    return role in ("principal", "planner") and binding["assignment"] is None


def validate(op, binding):
    name = op["op"]
    if op.keys() != {"op", *OPS[name].split()} or not matches(ID, op["id"]):
        raise Refusal("invalid_op")
    refs = {op["id"]}
    if name == "decision.record":
        if not decision_fields(op):
            raise Refusal("invalid_op")
        refs.update(op["support"])
        if op["supersedes"]:
            refs.add(op["supersedes"])
    elif name == "assumption.create":
        if not text(op["statement"]) or not text(op["observer"]):
            raise Refusal("invalid_op")
        if op["observer"] == binding["actor_id"]:
            raise Refusal("forbidden")
    else:
        field = {"decision.opine": "decision", "assumption.observe": "assumption"}.get(name, "support")
        if (not matches(ID, op[field]) or name == "decision.opine" and not text(op["opinion"])
                or name == "assumption.observe" and op["finding"] not in ("confirms", "contradicts")):
            raise Refusal("invalid_op")
        refs.add(op[field])
    if not bound(op, binding):
        raise Refusal("forbidden")
    return refs


def executor_access(view, op, binding):
    assignment = view.records.get(binding["assignment"])
    return bool(assignment and assignment["kind"] == "Assignment" and assignment["commitment"] == binding["commitment"]
                and assignment["fields"]["executor"] == binding["actor_id"] and assignment["fields"]["state"] == "current"
                and op["decision"] in view.graph.supports(assignment["fields"]["work"]))


def prior_access(store, op, binding, revision):
    return bound(op, binding) and (binding["role"] != "executor" or executor_access(
        WorkView(store.snapshot(revision=revision)["projection"]), op, binding))


def reads(view, op, binding, keys):
    if binding["role"] == "executor":
        if not executor_access(view, op, binding):
            raise Refusal("forbidden")
        work = view.fields(binding["assignment"])["work"]
        keys.update({binding["assignment"], work, *view.graph.supports(work)})
    if op["op"] == "assumption.observe":
        for item in view.graph.affected(op["assumption"]):
            assignment = view.fields(item).get("assignment")
            if assignment:
                keys.add(assignment)
    if op["op"] == "decision.record" and op["supersedes"]:
        # Incoming membership is rebound, and this declaration is about the triggered decision.
        keys.update(view.graph.incoming[op["supersedes"]])
    return keys


def incoming_reads(view, op):
    if op["op"] == "decision.record" and op["supersedes"]:
        return {op["supersedes"]}
    if op["op"] == "assumption.observe":
        return {op["assumption"], *view.graph.affected(op["assumption"])}
    return set()


def incoming_conflicts(changes, supported_ids):
    # A dropped edge has no current row. Even insertion followed by deletion since the
    # base changed the collection. Its original admissions survive in the verified log.
    changed = {}
    for event in changes:
        fields = event["data"]
        if event["type"] in ("DependencyAdded", "DependencyRemoved"):
            targets = {fields["support"]}
        elif event["type"] == "DecisionRecorded":
            targets = {*fields["support"], fields["id"], fields["supersedes"]}
        else:
            continue
        for key in targets & supported_ids:
            changed["depends_on:incoming:" + key] = event["revision"]
    return [{"key": key, "changed": revision} for key, revision in changed.items()]


def apply(admission, op):
    name, key = op["op"], op["id"]
    view, actor = admission.view, admission.actor
    fields = {k: v for k, v in op.items() if k != "op"}
    if name not in ("dependency.add", "dependency.remove") and key in view.records:
        raise Refusal("id_exists")
    if name == "assumption.create":
        event = "AssumptionCreated"
    elif name == "decision.record":
        for aid in op["support"]:
            assumption = admission.get(aid, "Assumption")
            if assumption["state"] != "held":
                raise Refusal("invalidated_support")
            if assumption["observer"] == actor:
                raise Refusal("forbidden")
        if op["supersedes"] and admission.get(op["supersedes"], "Decision")["state"] != "reconsidering":
            raise Refusal("invalid_transition")
        fields.update(support=sorted(op["support"]), triggers=sorted(op["triggers"]))
        event = "DecisionRecorded"
    elif name == "decision.opine":
        admission.get(op["decision"], "Decision")
        if admission.binding["role"] == "executor" and not executor_access(view, op, admission.binding):
            raise Refusal("forbidden")
        fields["author"] = actor
        event = "DecisionOpinionRecorded"
    elif name == "assumption.observe":
        premise = admission.get(op["assumption"], "Assumption")
        if premise["observer"] != actor:
            raise Refusal("forbidden")
        for affected in view.graph.affected(op["assumption"]):
            assignment = view.fields(affected).get("assignment")
            if assignment and view.fields(assignment)["executor"] == actor:
                raise Refusal("forbidden")
        fields.update(observer=actor, artifact_sha256=admission.batch.artifacts.put(admission.binding["premise"]["artifact"]))
        event = "PremiseObserved"
    else:
        admission.get(key, "WorkItem")
        support = view.records.get(op["support"])
        if (not support or support["kind"] not in ("Decision", "WorkItem", "Assumption")
                or support["commitment"] != admission.scope):
            raise Refusal("not_found")
        exists = op["support"] in view.graph.outgoing[key]
        if name == "dependency.add" and (exists or support["fields"]["state"] == "superseded"):
            raise Refusal("invalid_transition")
        if name == "dependency.remove" and not exists:
            raise Refusal("not_found")
        event = "DependencyAdded" if name == "dependency.add" else "DependencyRemoved"
    admission.emit(event, fields, commitment=admission.scope)
    admission.reopen_changed()
    return "admitted", "ok", {}
