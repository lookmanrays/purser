"""Admission of scoped Git authority, immutable intent, and independent settlement."""
from . import effectpolicy as policy
from .rules import HASH, ID, Refusal, matches
from .wire import canonical, digest
from .workview import WorkView

OPS = {
    "effect.require": "id work slot repository repository_instance resource paths",
    "grant.request": "id work slot repository resource paths assignment generation grantee expires_at",
    "grant.revoke": "id",
    "effect.request": "id intent",
    "effect.authorize": "id effect",
    "effect.reconcile": "id effect attempt",
}


def bound(op, binding):
    name, role, actor, assignment = op["op"], binding["role"], binding["actor_id"], binding["assignment"]
    if name == "effect.authorize":
        return role == "broker" and actor == policy.BROKER and assignment == op["effect"]
    if name == "effect.reconcile":
        return role == "observer" and actor == policy.OBSERVER and assignment == op["attempt"]
    if name == "effect.request":
        return role == "executor" and assignment == op["intent"]["assignment"]
    if name == "grant.request" and role == "executor":
        return assignment == op["assignment"] and actor == op["grantee"]
    return assignment is None and (role == "principal" or role == "planner" and name != "grant.revoke")


def validate(op, binding):
    name = op["op"]
    if op.keys() != {"op", *OPS[name].split()}:
        raise Refusal("invalid_op")
    refs = set()
    for k in {"id", "work", "assignment", "effect", "attempt", "slot", "repository"} & op.keys():
        if not matches(ID, op[k]):
            raise Refusal("invalid_op")
        if k not in ("slot", "repository"):
            refs.add(op[k])
    if ("paths" in op and not policy.allowed_paths(op["paths"]) or "resource" in op and not policy.branch(op["resource"])
            or "repository_instance" in op and not matches(HASH, op["repository_instance"])):
        raise Refusal("invalid_op")
    if name == "grant.request" and (not policy.deadline(op["expires_at"]) or type(op["generation"]) is not int
            or op["generation"] < 1 or type(op["grantee"]) is not str or not op["grantee"].strip()
            or op["id"] == op["assignment"] or op["grantee"] in (policy.BROKER, policy.OBSERVER, binding["principal_id"])):
        raise Refusal("invalid_op")
    if name == "effect.request":
        intent = op["intent"]
        if not policy.intent_valid(intent) or op["id"] != intent["effect"]:
            raise Refusal("invalid_op")
        refs.update(intent[k] for k in ("commitment", "work", "assignment", "grant"))
    required_proof = {"effect.authorize": "git_preparation", "effect.reconcile": "git_observation"}.get(name)
    if not bound(op, binding) or required_proof and required_proof not in binding:
        raise Refusal("forbidden")
    return refs


def executor_access(view, binding):
    row = view.records.get(binding["assignment"])
    return bool(row and row["kind"] == "Assignment" and row["commitment"] == binding["commitment"]
                and row["fields"]["executor"] == binding["actor_id"])


def prior_access(store, op, binding, revision):
    return bound(op, binding) and (binding["role"] != "executor" or executor_access(
        WorkView(store.snapshot(revision=revision)["projection"]), binding))


def same_intent(view, op, binding):
    prior = view.records.get(op["id"])
    return bool(prior and prior["kind"] == "Effect" and prior["fields"].get("intent") == op["intent"]
                and prior["commitment"] == binding["commitment"] and executor_access(view, binding))


def reads(view, op, binding, keys):
    if binding["role"] == "executor" and not executor_access(view, binding):
        raise Refusal("forbidden")
    name = op["op"]
    effect = view.records.get(op.get("effect"))
    intent = op.get("intent") if name == "effect.request" else None
    if name == "effect.authorize" and effect and effect["kind"] == "Effect":
        intent = effect["fields"].get("intent")
    work = intent["work"] if intent else op.get("work")
    if intent:
        keys.update(intent[k] for k in ("work", "assignment", "grant"))
    if work and view.records.get(work, {}).get("kind") == "WorkItem":
        fields = view.fields(work)
        slot = fields.get("effect_slots", {}).get(intent["slot"] if intent else op["slot"])
        if slot and intent:
            keys.add(slot["effect"])
            prior = view.fields(slot["effect"]).get("intent")
            if prior and slot["effect"] != intent["effect"]:
                keys.update((prior["grant"], prior["assignment"]))
        if intent:
            keys.update(view.graph.supports(work))
            if fields.get("result"):
                keys.add(fields["result"])
    return keys


def ref_conflict(view, op, base):
    row = view.records.get(op["effect"])
    if not row or row["kind"] != "Effect" or "intent" not in row["fields"]:
        return []
    fields = row["fields"]
    changed = max((r["changed"] for r in policy.ref_attempts(view, fields)), default=0)
    return ([{"key": "git_ref:" + fields["repository_instance"] + ":" + fields["intent"]["resource"],
              "changed": changed}] if changed > base else [])


def check_grant(admission, op):
    work = admission.get(op["work"], "WorkItem")
    slot = work.get("effect_slots", {}).get(op["slot"])
    if (not slot or any(op[k] != slot[k] for k in ("repository", "resource"))
            or not set(op["paths"]) <= set(slot["paths"])):
        raise Refusal("scope_mismatch")
    row = admission.view.records.get(op["assignment"])
    if row:
        assignment = admission.get(op["assignment"], "Assignment")
        if (assignment["executor"] != op["grantee"] or assignment["work"] != op["work"]
                or work.get("assignment") != op["assignment"] or assignment["generation"] != op["generation"]):
            raise Refusal("scope_mismatch")
    elif (admission.binding["role"] == "executor"
          or op["generation"] != work["generation"] + int(bool(work.get("assignment")))):
        raise Refusal("scope_mismatch")
    if admission.commitment["state"] != "active":
        raise Refusal("invalid_transition")


def bind_intent(a, op):
    intent, key = op["intent"], op["id"]
    if intent["commitment"] != a.scope:
        raise Refusal("forbidden")
    assignment = a.get(intent["assignment"], "Assignment")
    if assignment["executor"] != a.actor:
        raise Refusal("forbidden")
    work = a.get(intent["work"], "WorkItem")
    prior = a.view.records.get(key)
    if prior and prior["kind"] != "Effect":
        raise Refusal("id_exists")
    if prior and "intent" in prior["fields"]:
        if prior["fields"]["intent"] != intent:
            raise Refusal("intent_conflict")
        return {"effect": key, "state": prior["fields"]["state"]}
    proof = a.binding.get("git_preparation")
    if not proof or proof["intent_digest"] != digest("effect", intent):
        raise Refusal("witness_mismatch")
    issue = policy.live_authority(a.view, intent, proof, a.batch.at)
    if issue:
        raise Refusal(issue)
    slot = work["effect_slots"][intent["slot"]]
    former = a.get(slot["effect"], "Effect")
    if slot["effect"] != key:
        if prior:
            raise Refusal("id_exists")
        old = former.get("intent")
        if not old or former["state"] in policy.UNRESOLVED or {**old, "effect": key} == intent:
            raise Refusal("slot_occupied")
        if (former["state"] == "confirmed_happened" and old["generation"] == intent["generation"]
                or former["state"] == "ready" and not policy.live_authority(a.view, old, former, a.batch.at)):
            raise Refusal("slot_occupied")
    a.emit("EffectIntentBound", {"id": key, "commitment": a.scope, "intent": intent,
           "repository_instance": proof["repository_instance"], "changed_paths": proof["changed_paths"]})
    return {"effect": key, "state": "ready"}


def apply(a, op):
    name, key = op["op"], op["id"]
    fields = {k: v for k, v in op.items() if k != "op"}
    if name in ("effect.require", "grant.request", "effect.authorize", "effect.reconcile") and key in a.view.records:
        raise Refusal("id_exists")
    details = {}
    if name == "effect.require":
        if op["slot"] in a.get(op["work"], "WorkItem").get("effect_slots", {}):
            raise Refusal("slot_occupied")
        a.emit("EffectRequired", fields, commitment=a.scope)
        a.reopen_changed()
    elif name == "grant.request":
        check_grant(a, op)
        keys = reads(a.view, op, a.binding, {a.scope, key, op["work"], op["assignment"]})
        return a.batch.hold(op["expires_at"], keys)
    elif name == "grant.revoke":
        grant = a.get(key, "AuthorityGrant")
        if grant["state"] == "revoked":
            raise Refusal("already_decided")
        attempts = sorted(r["id"] for r in a.view.records.values() if r["kind"] == "EffectAttempt"
                          and r["fields"]["grant"] == key and r["fields"]["state"] in policy.UNRESOLVED)
        a.emit("GrantRevoked", fields, commitment=a.scope, principal=a.actor, in_flight=attempts)
    elif name == "effect.request":
        details = bind_intent(a, op)
        a.reopen_changed()
    elif name == "effect.authorize":
        effect = a.get(op["effect"], "Effect")
        issue = policy.dispatchable(a.view, effect, a.batch.at)
        if issue:
            raise Refusal(issue)
        proof = a.binding["git_preparation"]
        if any(proof[k] != effect[k] for k in ("intent_digest", "repository_instance", "changed_paths")):
            raise Refusal("witness_mismatch")
        a.emit("EffectDispatchAuthorized", fields, commitment=a.scope, broker=a.actor, authorized_at=a.batch.at)
        details = {"effect": op["effect"], "attempt": key, "intent_digest": effect["intent_digest"], "state": "dispatching"}
    else:
        effect = a.get(op["effect"], "Effect")
        attempt = a.get(op["attempt"], "EffectAttempt")
        if attempt["effect"] != op["effect"] or effect.get("attempt") != op["attempt"] or attempt["state"] not in policy.UNRESOLVED:
            raise Refusal("invalid_transition")
        report = a.binding["git_observation"]
        if not policy.observes(report, effect, op["attempt"]):
            raise Refusal("witness_mismatch")
        sha = a.batch.artifacts.put(canonical(report))
        a.emit("GitEffectObserved", fields, commitment=a.scope, observer=a.actor, state=report["state"],
               reason=report["reason"], artifact_sha256=sha)
        details = {"effect": op["effect"], "attempt": op["attempt"], "state": report["state"]}
        a.reopen_changed()
    return "admitted", "ok", details
