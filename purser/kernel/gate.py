"""Admission of authenticated proposals into the single local journal."""
import re
from datetime import datetime, timezone

from . import decisionops, effectops, effectpolicy
from .artifacts import MAX_WITNESS_BYTES, Artifacts
from .projector import apply_event
from .rules import HASH, ID, Refusal, candidate, matches
from .workops import OPS, WorkAdmission, prior_work_access, validate_work, work_reads
from .workview import WorkView
from .wire import KernelError, canonical, decode, digest, parse_time

PRINCIPAL = "principal:local"
APPROVAL = re.compile(r"a:[0-9a-f]{64}")
FIELDS = {
    "commitment.create": {"op", "id", "root", "statement"},
    "commitment.edit": {"op", "id", "statement"},
    "commitment.request_acceptance": {"op", "id", "expires_at"},
    "approval.answer": {"op", "id", "request_digest", "decision"},
}


# Admission, read-set derivation and receipt re-delivery share family membership.
_SCOPED = {name: (validate_work, work_reads, prior_work_access) for name in OPS}
for domain in (decisionops, effectops):
    _SCOPED.update({name: (domain.validate, domain.reads, domain.prior_access) for name in domain.OPS})


def scoped_ops(op):
    if isinstance(op, dict) and isinstance(op.get("op"), str):
        return _SCOPED.get(op["op"])
    return None


def authenticate(binding):
    keys = {"actor_id", "role", "principal_id", "commitment", "assignment"}
    if (not isinstance(binding, dict) or not keys <= binding.keys()
            or any(not isinstance(binding[k], str) or not binding[k] for k in ("actor_id", "role"))
            or binding["principal_id"] != PRINCIPAL
            or (binding["role"] == "principal") != (binding["actor_id"] == PRINCIPAL)
            or any(binding[k] is not None and not matches(ID, binding[k]) for k in ("commitment", "assignment"))):
        raise KernelError("unauthenticated")
    extra = binding.keys() - keys
    if extra in ({"git_preparation"}, {"git_observation"}):
        name = next(iter(extra))
        roles = ("executor", "broker") if name == "git_preparation" else ("observer",)
        if binding["role"] not in roles or not effectpolicy.proof_valid(name, binding[name]):
            raise KernelError("unauthenticated")
    elif extra == {"premise"} and binding["role"] == "observer":
        proof = binding["premise"]
        if (type(proof) is not dict or proof.keys() != {"artifact"} or type(proof["artifact"]) is not bytes
                or not 0 < len(proof["artifact"]) <= MAX_WITNESS_BYTES):
            raise KernelError("unauthenticated")
    elif extra:
        name = {"observer": "witness", "verifier": "criterion"}.get(binding["role"])
        if extra != {name} or type(binding[name]) is not dict:
            raise KernelError("unauthenticated")
        proof = binding[name]
        byte_fields = ("definition", "environment", "artifact") if name == "witness" else ("definition", "environment")
        other = {"candidate"} if name == "witness" else {"work", "observer"}
        if (proof.keys() != {*byte_fields, *other}
                or any(type(proof[k]) is not bytes or len(proof[k]) > MAX_WITNESS_BYTES for k in byte_fields)
                or name == "witness" and not candidate(proof["candidate"])
                or name == "criterion" and (not matches(ID, proof["work"]) or type(proof["observer"]) is not str)):
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
    if answer and binding["assignment"] is not None:
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


def in_scope(binding, scope):
    return binding["commitment"] == scope or binding["role"] == "principal" and binding["commitment"] is None


def _redelivery(store, snapshot, binding, proposal, request_digest, scoped_here):
    previous = known_receipt(snapshot["projection"], binding["actor_id"], proposal["proposal_id"])
    if previous:
        if previous["digest"] != request_digest:
            raise KernelError("id_conflict")
        if not scoped_here and previous["code"] != "forbidden":
            raise KernelError("unauthenticated")
        if previous["code"] not in ("bad_revision", "invalid_op", "unknown_op", "forbidden", "not_found"):
            for op in proposal["ops"]:
                family = scoped_ops(op)
                if family and not family[2](store, op, binding, previous["revision"]):
                    raise KernelError("unauthenticated")
    return previous


def redelivery(store, binding, proposal):
    """Read an exact transport receipt, including a finalized approval, before opening Git."""
    authenticate(binding)
    proposal = envelope(proposal)
    checksum = digest("proposal", {"actor_id": binding["actor_id"], "proposal": proposal})
    return _redelivery(store, store.snapshot(), binding, proposal, checksum, in_scope(binding, proposal["commitment"]))


class Admission:
    def __init__(self, snapshot, at, binding, proposal, request_digest, artifacts):
        self.start = snapshot["revision"]
        self.projection = snapshot["projection"]
        self.at = at
        self.proposal = proposal
        self.actor = binding["actor_id"]
        self.binding = binding
        self.artifacts = artifacts
        self.request_digest = request_digest
        self.events = []

    def emit(self, kind, data):
        revision = self.start + len(self.events) + 1
        self.projection = apply_event(self.projection, kind, data, revision, revision)
        self.events.append({"type": kind, "data": data})

    def records(self):
        return {r["id"]: r for r in self.projection["records"]}

    def hold(self, expires_at, reads):
        if self.at >= expires_at:
            raise Refusal("approval_expired")
        key = "a:" + self.request_digest
        records = self.records()
        versions = [{"key": k, "changed": records[k]["changed"] if k in records else 0} for k in sorted(reads)]
        self.emit("ApprovalRequested", {"id": key, "requester": self.actor, "request": self.proposal,
                  "request_digest": self.request_digest, "read_versions": versions, "expires_at": expires_at})
        return "pending", "needs_principal", {"approval_id": key}

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
            requested = fields["request"]["ops"][0]
            if requested["op"] == "grant.request":
                effectops.check_grant(WorkAdmission(self), requested)
                self.emit("GrantIssued", {**{k: v for k, v in requested.items() if k != "op"},
                                          "commitment": approval["commitment"], "principal": self.actor})
            else:
                self.emit("CommitmentActivated", {"id": approval["commitment"]})
        self.emit("ProposalReceipted", receipt(fields["requester"], fields["request"]["proposal_id"],
                  fields["request_digest"], "admitted" if state == "approved" else "rejected", code, details))
        return "admitted", "ok", {"approval_id": op["id"], "state": state}

    def apply(self, op):
        name, key = op["op"], op["id"]
        if name in effectops.OPS:
            return effectops.apply(WorkAdmission(self), op)
        if name in decisionops.OPS:
            return decisionops.apply(WorkAdmission(self), op)
        if name in OPS:
            return WorkAdmission(self).apply(op)
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
                return self.hold(op["expires_at"], {key})
        return "admitted", "ok", {}


def admit(store, binding, proposal, *, clock=lambda: datetime.now(timezone.utc)):
    return _admit(store, binding, proposal, clock, cutoff=False)[0]


def authorize_attempt(store, binding, proposal, *, clock=lambda: datetime.now(timezone.utc)):
    """Only the caller committing a new dispatch may release its guarded Git writer."""
    return _admit(store, binding, proposal, clock, cutoff=True)


def _admit(store, binding, proposal, clock, cutoff):
    authenticate(binding)
    binding = dict(binding)
    for name in ("git_preparation", "git_observation"):
        if name in binding:
            binding[name] = decode(canonical(binding[name]))
            if not effectpolicy.proof_valid(name, binding[name]):
                raise KernelError("unauthenticated")
    for proof in ("witness", "criterion", "premise"):
        if proof in binding:
            binding[proof] = dict(binding[proof])
    if "witness" in binding:
        binding["witness"]["candidate"] = dict(binding["witness"]["candidate"])
    proposal = envelope(proposal)
    if cutoff and not (len(proposal["ops"]) == 1 and type(proposal["ops"][0]) is dict
                       and proposal["ops"][0].get("op") == "effect.authorize"):
        raise KernelError("invalid_input")
    actor, scope, base = binding["actor_id"], proposal["commitment"], proposal["base_revision"]
    proposal_id = proposal["proposal_id"]
    request_digest = digest("proposal", {"actor_id": actor, "proposal": proposal})
    scoped_here = in_scope(binding, scope)

    def decide(snapshot, at):
        previous = _redelivery(store, snapshot, binding, proposal, request_digest, scoped_here)
        if previous:
            return [], (previous, False)
        batch = Admission(snapshot, at, binding, proposal, request_digest, Artifacts(store.path))
        try:
            if not 0 <= base <= snapshot["revision"]:
                raise Refusal("bad_revision")
            base_projection = None
            if base not in (0, snapshot["revision"]):
                try:
                    base_projection = store.snapshot(revision=base)["projection"]
                except KernelError as exc:
                    if exc.code != "bad_revision":
                        raise
                    raise Refusal("bad_revision") from exc
            if not scoped_here:
                raise Refusal("forbidden")
            keys, incoming, ref_changes = set(), set(), []
            records = batch.records()
            view = WorkView(batch.projection)
            for op in proposal["ops"]:
                family = scoped_ops(op)
                if family:
                    if op["op"] in ("grant.request", "effect.authorize") and len(proposal["ops"]) != 1:
                        raise Refusal("invalid_op")
                    validate, read_set, _ = family
                    references = validate(op, binding)
                    if any(k in records and records[k]["commitment"] != scope for k in references):
                        raise Refusal("not_found")
                    # An exact logical redelivery reads its current disposition. It acquires no new authority.
                    if (op["op"] == "effect.request" and len(proposal["ops"]) == 1
                            and effectops.same_intent(view, op, binding)):
                        continue
                    keys.update(read_set(view, op, binding, references))
                    if base != snapshot["revision"] and op["op"] == "effect.authorize":
                        ref_changes.extend(effectops.ref_conflict(view, op, base))
                    if base != snapshot["revision"] and op["op"] in decisionops.OPS:
                        incoming.update(decisionops.incoming_reads(view, op))
                        if base_projection is not None:
                            incoming.update(decisionops.incoming_reads(WorkView(base_projection), op))
                    keys.add(scope)
                else:
                    keys.update(validate_op(op, proposal, binding))
            # A scoped lookup cannot expose another commitment's records or change revision.
            for key in keys:
                if key in records and records[key]["commitment"] != scope:
                    if binding["role"] != "principal" or any(op["op"] == "approval.answer" for op in proposal["ops"]):
                        raise Refusal("not_found")
            stale = conflicts(records, dict.fromkeys(keys, base))
            stale.extend(ref_changes)
            if incoming:
                stale.extend(decisionops.incoming_conflicts(store.verified_changes_since(base), incoming))
            if stale:
                stale.sort(key=lambda row: row["key"])
                raise Refusal("stale_base", {"conflicts": stale})
            for op in proposal["ops"]:
                disposition, code, details = batch.apply(op)
        except Refusal as exc:
            batch.events.clear()
            disposition, code, details = "rejected", exc.code, exc.details
        result = receipt(actor, proposal_id, request_digest, disposition, code, details)
        batch.events.append({"type": "ProposalReceipted", "data": result})
        durable = dict(result, revision=snapshot["revision"] + len(batch.events))
        return batch.events, (durable, cutoff and disposition == "admitted")

    _, result = store.transact(actor, proposal_id, clock, decide)
    return result
