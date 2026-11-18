"""Admission of authenticated proposals into the single local journal."""
import re
from datetime import datetime, timezone

from .projector import apply_event
from .wire import KernelError, canonical, decode, digest, parse_time

PRINCIPAL = "principal:local"
ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,95}")
HASH = re.compile(r"[0-9a-f]{64}")
APPROVAL = re.compile(r"a:[0-9a-f]{64}")
FIELDS = {
    "commitment.create": {"op", "id", "root", "statement"},
    "commitment.edit": {"op", "id", "statement"},
    "commitment.request_acceptance": {"op", "id", "expires_at"},
    "approval.answer": {"op", "id", "request_digest", "decision"},
}


class Refusal(KernelError):
    """A valid first delivery whose semantic rejection must survive restart."""


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def authenticate(binding):
    keys = {"actor_id", "role", "principal_id", "commitment", "assignment"}
    if (not isinstance(binding, dict) or binding.keys() != keys
            or any(not isinstance(binding[k], str) or not binding[k] for k in ("actor_id", "role"))
            or binding["principal_id"] != PRINCIPAL
            or (binding["role"] == "principal") != (binding["actor_id"] == PRINCIPAL)
            or any(binding[k] is not None and not matches(ID, binding[k]) for k in ("commitment", "assignment"))):
        raise KernelError("unauthenticated")


def envelope(value):
    proposal = decode(canonical(value))  # Freeze caller-owned values before deriving the request identity.
    keys = {"v", "proposal_id", "commitment", "base_revision", "ops", "justification"}
    if (not isinstance(proposal, dict) or proposal.keys() != keys
            or type(proposal["v"]) is not int or proposal["v"] != 1
            or not all(matches(ID, proposal[k]) for k in ("proposal_id", "commitment"))
            or type(proposal["base_revision"]) is not int
            or not isinstance(proposal["ops"], list) or not proposal["ops"]
            or not isinstance(proposal["justification"], list)
            or not all(isinstance(ref, str) and ref for ref in proposal["justification"])):
        raise KernelError("invalid_input")
    return proposal


def validate_op(op, proposal, binding):
    if not isinstance(op, dict) or not isinstance(op.get("op"), str):
        raise Refusal("invalid_op")
    name = op["op"]
    if name not in FIELDS:
        raise Refusal("unknown_op")
    if op.keys() != FIELDS[name]:
        raise Refusal("invalid_op")
    answer = name == "approval.answer"
    if not matches(APPROVAL if answer else ID, op["id"]):
        raise Refusal("invalid_op")
    if "root" in op and (not matches(ID, op["root"]) or op["root"] == op["id"]):
        raise Refusal("invalid_op")
    if "statement" in op and (not isinstance(op["statement"], str) or not op["statement"].strip()):
        raise Refusal("invalid_op")
    if "expires_at" in op:
        try:
            parse_time(op["expires_at"])
        except KernelError as exc:
            raise Refusal("invalid_op") from exc
    if answer and (not matches(HASH, op["request_digest"]) or op["decision"] not in ("approve", "deny")):
        raise Refusal("invalid_op")
    if (answer or "expires_at" in op) and len(proposal["ops"]) != 1:
        raise Refusal("invalid_op")
    if binding["role"] != "principal" and (binding["role"] != "intake" or answer):
        raise Refusal("forbidden")
    if not answer and op["id"] != proposal["commitment"]:
        raise Refusal("forbidden")
    return [op["id"], op["root"]] if "root" in op else [op["id"]]


def receipt(actor, proposal_id, request_digest, disposition, code, details):
    return {"actor_id": actor, "proposal_id": proposal_id, "digest": request_digest,
            "disposition": disposition, "code": code, "details": details}


def known_receipt(projection, actor, proposal_id):
    return next((r for r in projection["receipts"]
                 if r["actor_id"] == actor and r["proposal_id"] == proposal_id), None)


def conflicts(records, versions):
    return [{"key": key, "changed": records[key]["changed"]} for key, version in sorted(versions.items())
            if key in records and records[key]["changed"] > version]


class Admission:
    def __init__(self, snapshot, at, binding, proposal, request_digest):
        self.start = snapshot["revision"]
        self.projection = snapshot["projection"]
        self.at = at
        self.proposal = proposal
        self.actor = binding["actor_id"]
        self.request_digest = request_digest
        self.events = []

    def emit(self, kind, data):
        revision = self.start + len(self.events) + 1
        self.projection = apply_event(self.projection, kind, data, revision, revision)
        self.events.append({"type": kind, "data": data})

    def records(self):
        return {r["id"]: r for r in self.projection["records"]}

    def resolve(self, op, approval, records):
        fields = approval["fields"]
        if fields["request_digest"] != op["request_digest"]:
            raise Refusal("digest_mismatch")
        if fields["state"] != "pending":
            raise Refusal("already_decided")
        details = {"approval_id": op["id"]}
        versions = {v["key"]: v["changed"] for v in fields["read_versions"]}
        stale = conflicts(records, versions)
        if self.at >= fields["expires_at"]:
            state, code = "expired", "approval_expired"
        elif stale:
            state, code, details = "stale", "stale_base", {"conflicts": stale}
        elif op["decision"] == "deny":
            state, code = "denied", "approval_denied"
        else:
            state, code = "approved", "ok"
        self.emit("ApprovalResolved", {"id": op["id"], "state": state})
        if state == "approved":
            self.emit("CommitmentActivated", {"id": approval["commitment"]})
        self.emit("ProposalReceipted", receipt(fields["requester"], fields["request"]["proposal_id"],
                  fields["request_digest"], "admitted" if state == "approved" else "rejected", code, details))
        return "admitted", "ok", {"approval_id": op["id"], "state": state}

    def apply(self, op):
        name, key = op["op"], op["id"]
        records = self.records()
        if name == "commitment.create":
            if key in records or op["root"] in records:
                raise Refusal("id_exists")
            self.emit("CommitmentCreated", {"id": key, "root": op["root"], "statement": op["statement"],
                                           "principal": PRINCIPAL})
        else:
            record = records.get(key)
            if record is None or record["commitment"] != self.proposal["commitment"]:
                raise Refusal("not_found")
            if name == "approval.answer":
                return self.resolve(op, record, records)
            if record["kind"] != "Commitment" or record["fields"]["state"] != "proposed":
                raise Refusal("invalid_transition")
            if name == "commitment.edit":
                self.emit("CommitmentEdited", {"id": key, "statement": op["statement"]})
            elif name == "commitment.request_acceptance":
                if self.at >= op["expires_at"]:
                    raise Refusal("approval_expired")
                approval_id = "a:" + self.request_digest
                self.emit("ApprovalRequested", {"id": approval_id, "requester": self.actor,
                          "request": self.proposal, "request_digest": self.request_digest,
                          "read_versions": [{"key": key, "changed": record["changed"]}],
                          "expires_at": op["expires_at"]})
                return "pending", "needs_principal", {"approval_id": approval_id}
        return "admitted", "ok", {}


def admit(store, binding, proposal, *, clock=lambda: datetime.now(timezone.utc)):
    authenticate(binding)
    proposal = envelope(proposal)
    actor, scope, base = binding["actor_id"], proposal["commitment"], proposal["base_revision"]
    proposal_id = proposal["proposal_id"]
    request_digest = digest("proposal", {"actor_id": actor, "proposal": proposal})
    scoped_here = binding["commitment"] == scope or (
        binding["role"] == "principal" and binding["commitment"] is None
    )

    def decide(snapshot, at):
        previous = known_receipt(snapshot["projection"], actor, proposal_id)
        if previous:
            if previous["digest"] != request_digest:
                raise KernelError("id_conflict")
            if not scoped_here and previous["code"] != "forbidden":
                raise KernelError("unauthenticated")
            return [], previous
        batch = Admission(snapshot, at, binding, proposal, request_digest)
        try:
            if not 0 <= base <= snapshot["revision"]:
                raise Refusal("bad_revision")
            if base not in (0, snapshot["revision"]):
                try:
                    store.snapshot(revision=base)
                except KernelError as exc:
                    if exc.code != "bad_revision":
                        raise
                    raise Refusal("bad_revision") from exc
            if not scoped_here:
                raise Refusal("forbidden")
            keys = {key for op in proposal["ops"] for key in validate_op(op, proposal, binding)}
            records = batch.records()
            # A scoped lookup cannot expose another commitment's records or change revision.
            for key in keys:
                if key in records and records[key]["commitment"] != scope:
                    if binding["role"] != "principal" or any(op["op"] == "approval.answer" for op in proposal["ops"]):
                        raise Refusal("not_found")
            stale = conflicts(records, dict.fromkeys(keys, base))
            if stale:
                raise Refusal("stale_base", {"conflicts": stale})
            for op in proposal["ops"]:
                disposition, code, details = batch.apply(op)
        except Refusal as exc:
            batch.events.clear()
            disposition, code, details = "rejected", exc.code, exc.details
        result = receipt(actor, proposal_id, request_digest, disposition, code, details)
        batch.events.append({"type": "ProposalReceipted", "data": result})
        return batch.events, dict(result, revision=snapshot["revision"] + len(batch.events))

    _, result = store.transact(actor, proposal_id, clock, decide)
    return result
