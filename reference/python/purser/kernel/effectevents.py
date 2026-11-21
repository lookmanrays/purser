"""Version-one projection of required work slots, human grants and physical attempts."""
from .effectpolicy import BROKER, CAPABILITY, OBSERVER, UNRESOLVED, allowed_paths, branch, deadline, git_paths, intent_valid
from .rules import HASH, ID, matches
from .wire import KernelError, digest

EVENTS = {
    "EffectRequired": "work slot repository repository_instance resource paths",
    "GrantIssued": "work slot repository resource paths grantee assignment generation expires_at principal",
    "GrantRevoked": "principal in_flight",
    "EffectIntentBound": "intent repository_instance changed_paths",
    "EffectDispatchAuthorized": "effect broker authorized_at",
    "GitEffectObserved": "effect attempt observer state reason artifact_sha256",
}


def project(projection, name, data, revision):
    error = KernelError("integrity_error", {"revision": revision})
    if type(data) is not dict or data.keys() != {"id", "commitment", *EVENTS[name].split()}:
        raise error
    for k in ("id", "commitment", "work", "slot", "repository", "assignment", "effect", "attempt"):
        if k in data and not matches(ID, data[k]):
            raise error
    for k in ("repository_instance", "artifact_sha256"):
        if k in data and not matches(HASH, data[k]):
            raise error
    if ("paths" in data and not allowed_paths(data["paths"])
            or "changed_paths" in data and not git_paths(data["changed_paths"])):
        raise error
    if "resource" in data and not branch(data["resource"]):
        raise error
    rows = {r["id"]: r for r in projection["records"]}
    key, scope = data["id"], data["commitment"]

    def get(identity, kind):
        row = rows.get(identity)
        if not row or row["kind"] != kind or row["commitment"] != scope:
            raise error
        return row["fields"]

    def put(identity, kind, values, *, new=False):
        if new and identity in rows:
            raise error
        fields = {} if new else get(identity, kind)
        rows[identity] = {"id": identity, "kind": kind, "commitment": scope, "changed": revision,
                          "fields": {**fields, **values}}

    def set_slot(work, slot, values):
        slots = get(work, "WorkItem").get("effect_slots", {})
        put(work, "WorkItem", {"effect_slots": {**slots, slot: values}})

    principal = get(scope, "Commitment")["principal"]
    attrs = {k: data[k] for k in EVENTS[name].split()}
    if name == "EffectRequired":
        work, slot = data["work"], data["slot"]
        if slot in get(work, "WorkItem").get("effect_slots", {}):
            raise error
        put(key, "Effect", {"work": work, "slot": slot, "state": "required"}, new=True)
        set_slot(work, slot, {"effect": key, **{k: data[k] for k in ("repository", "repository_instance", "resource", "paths")}})
    elif name == "GrantIssued":
        slot = get(data["work"], "WorkItem").get("effect_slots", {}).get(data["slot"])
        if (not slot or any(slot[k] != data[k] for k in ("repository", "resource"))
                or not set(data["paths"]) <= set(slot["paths"]) or data["principal"] != principal
                or type(data["grantee"]) is not str or not data["grantee"].strip() or not deadline(data["expires_at"])
                or type(data["generation"]) is not int or data["generation"] < 1):
            raise error
        put(key, "AuthorityGrant", dict(attrs, state="active", capability=CAPABILITY,
                                       repository_instance=slot["repository_instance"]), new=True)
    elif name == "GrantRevoked":
        grant = get(key, "AuthorityGrant")
        expected = sorted(r["id"] for r in rows.values() if r["kind"] == "EffectAttempt"
                          and r["fields"]["grant"] == key and r["fields"]["state"] in UNRESOLVED)
        if grant["state"] != "active" or data["principal"] != principal or data["in_flight"] != expected:
            raise error
        put(key, "AuthorityGrant", {"state": "revoked", "in_flight_at_revocation": expected})
    elif name == "EffectIntentBound":
        intent = data["intent"]
        if not intent_valid(intent) or intent["effect"] != key or intent["commitment"] != scope:
            raise error
        work, slot = intent["work"], intent["slot"]
        policy = get(work, "WorkItem").get("effect_slots", {}).get(slot)
        if (not policy or policy["repository_instance"] != data["repository_instance"]
                or any(policy[k] != intent[k] for k in ("repository", "resource"))
                or not set(data["changed_paths"]) <= set(get(intent["grant"], "AuthorityGrant")["paths"])):
            raise error
        former = policy["effect"]
        old = get(former, "Effect")
        if former == key and old["state"] != "required" or former != key and old["state"] in (*UNRESOLVED, "required"):
            raise error
        if former != key:
            if old["state"] == "ready":
                put(former, "Effect", {"state": "superseded", "superseded_by": key})
            set_slot(work, slot, {**policy, "effect": key})
        put(key, "Effect", dict(attrs, work=work, slot=slot, state="ready",
                                intent_digest=digest("effect", intent)), new=key != former)
    elif name == "EffectDispatchAuthorized":
        effect = get(data["effect"], "Effect")
        if effect["state"] not in ("ready", "confirmed_absent") or data["broker"] != BROKER or not deadline(data["authorized_at"]):
            raise error
        intent = effect["intent"]
        put(key, "EffectAttempt", {**attrs, **{k: intent[k] for k in ("grant", "repository", "resource", "work")},
            "repository_instance": effect["repository_instance"], "intent_digest": effect["intent_digest"],
            "state": "dispatching"}, new=True)
        put(data["effect"], "Effect", {"state": "dispatching", "attempt": key, "observation": None})
    else:
        effect, attempt = get(data["effect"], "Effect"), get(data["attempt"], "EffectAttempt")
        if (attempt["effect"] != data["effect"] or effect.get("attempt") != data["attempt"]
                or attempt["state"] not in UNRESOLVED or data["state"] not in ("pending", "confirmed_happened", "confirmed_absent")
                or data["observer"] != OBSERVER or type(data["reason"]) is not str or not data["reason"].strip()):
            raise error
        put(key, "Observation", dict(attrs, work=effect["work"]), new=True)
        values = {"state": data["state"], "observation": key, "reason": data["reason"]}
        put(data["attempt"], "EffectAttempt", values)
        put(data["effect"], "Effect", values)
    return {**projection, "records": [rows[k] for k in sorted(rows)]}
