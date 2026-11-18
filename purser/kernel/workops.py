"""Work, verification and closure operators admitted by the local gate."""
import hashlib
import re

from .rules import HASH, ID, Refusal, matches
from .workview import WorkView

OPS = {
    "work.create": ("id parent title", ("principal", "planner", "executor")),
    "work.coverage": ("id kind k", ("principal", "planner")),
    "work.assign": ("id work executor", ("principal", "planner")),
    "obligation.set": ("id work version title observer verifier definition_sha256 environment_sha256",
                       ("principal", "verifier")),
    "result.submit": ("id assignment candidate", ("executor",)),
    "run.open": ("id obligation result", ("principal", "planner")),
    "observation.record": ("id run", ("observer",)),
    "evidence.record": ("id run observation verdict", ("verifier",)),
    "obligation.waive": ("id obligation version result reason", ("principal",)),
    "work.close": ("id", ("closure",)),
    "work.accept": ("id outcome", ("principal",)),
}
_REFS = {"id", "parent", "work", "assignment", "obligation", "result", "run", "observation"}


def candidate(value):
    if type(value) is not dict or value.keys() != {"kind", "algorithm", "digest"}:
        return False
    size = {("git.commit", "sha1"): 40, ("git.commit", "sha256"): 64, ("artifact.bytes", "sha256"): 64}
    return (all(type(v) is str for v in value.values())
            and re.fullmatch(r"[0-9a-f]+", value["digest"]) is not None
            and len(value["digest"]) == size.get((value["kind"], value["algorithm"])))


def bound(op, binding):
    name, role = op["op"], binding["role"]
    if role not in OPS[name][1]:
        return False
    if role in ("principal", "planner", "closure"):
        return binding["assignment"] is None
    if name == "work.create":
        return bool(binding["assignment"])
    if name == "obligation.set":
        return binding["assignment"] == op["id"] and binding["actor_id"] == op["verifier"] and "criterion" in binding
    return binding["assignment"] == op.get("run", op.get("assignment"))


def validate_work(op, binding):
    name = op["op"]
    if op.keys() != {"op", *OPS[name][0].split()}:
        raise Refusal("invalid_op")
    for key, val in op.items():
        if key in _REFS and not matches(ID, val):
            raise Refusal("invalid_op")
        if key in ("title", "reason", "executor", "observer", "verifier") and (type(val) is not str or not val.strip()):
            raise Refusal("invalid_op")
        if (key.endswith("_sha256") or key == "outcome") and not matches(HASH, val):
            raise Refusal("invalid_op")
    if "version" in op and (type(op["version"]) is not int or op["version"] < 1):
        raise Refusal("invalid_op")
    if "candidate" in op and not candidate(op["candidate"]):
        raise Refusal("invalid_op")
    if "verdict" in op and op["verdict"] not in ("pass", "fail", "inconclusive"):
        raise Refusal("invalid_op")
    if "k" in op and not (
        op["kind"] in ("all", "any") and op["k"] is None
        or op["kind"] == "k_of_n" and type(op["k"]) is int and op["k"] > 0
    ):
        raise Refusal("invalid_op")
    if not bound(op, binding):
        raise Refusal("forbidden")
    return {op[k] for k in _REFS & op.keys()}


def work_reads(view, op, binding):
    name = op["op"]
    keys = validate_work(op, binding)
    if binding["assignment"]:
        keys.add(binding["assignment"])
    if binding["role"] == "executor":
        assignment = view.records.get(binding["assignment"])
        if (not assignment or assignment["kind"] != "Assignment" or assignment["commitment"] != binding["commitment"]
                or assignment["fields"]["executor"] != binding["actor_id"] or assignment["fields"]["state"] != "current"
                or name == "work.create" and op["parent"] != assignment["fields"]["work"]):
            raise Refusal("forbidden")
    for ref in tuple(keys):
        row = view.records.get(ref)
        if row:
            keys.update(row["fields"][k] for k in ("work", "assignment", "result")
                        if isinstance(row["fields"].get(k), str) and name not in ("observation.record", "evidence.record"))
    target = view.records.get(op["id"])
    if target and target["kind"] == "WorkItem":
        if name in ("work.close", "work.accept"):
            keys.update(view.reads(op["id"]))
        elif name == "work.coverage":
            keys.update(view.children[op["id"]])
    return keys


class WorkAdmission:
    def __init__(self, batch):
        self.batch = batch
        self.view = WorkView(batch.projection)
        self.scope = batch.proposal["commitment"]
        self.binding = batch.binding
        self.actor = batch.actor
        self.commitment = self.get(self.scope, "Commitment")
        if self.commitment["state"] not in ("active", "satisfied"):
            raise Refusal("invalid_transition")

    def get(self, key, kind):
        record = self.view.records.get(key)
        if record is None or record["kind"] != kind or record["commitment"] != self.scope:
            raise Refusal("not_found")
        return record["fields"]

    def emit(self, name, fields, **extra):
        self.batch.emit(name, {**fields, **extra})

    def apply(self, op):
        name, key = op["op"], op["id"]
        fields = {k: v for k, v in op.items() if k != "op"}
        if name not in ("work.coverage", "work.close", "work.accept", "obligation.set") and key in self.view.records:
            raise Refusal("id_exists")
        if name == "work.create":
            self.get(op["parent"], "WorkItem")
            self.emit("WorkCreated", fields, commitment=self.scope)
        elif name == "work.coverage":
            self.get(key, "WorkItem")
            if op["kind"] == "k_of_n" and op["k"] > len(self.view.children[key]):
                raise Refusal("invalid_op")
            self.emit("CoverageSet", {"id": key, "coverage": {"kind": op["kind"], "k": op["k"]}})
        elif name == "work.assign":
            work = self.get(op["work"], "WorkItem")
            generation = work["generation"] + (1 if work.get("assignment") else 0)
            self.emit("WorkAssigned", fields, commitment=self.scope, generation=generation)
        elif name == "result.submit":
            assignment = self.get(op["assignment"], "Assignment")
            work = self.get(assignment["work"], "WorkItem")
            if (assignment["state"] != "current" or assignment["executor"] != self.actor
                    or work.get("assignment") != op["assignment"] or work.get("result")):
                raise Refusal("invalid_transition")
            if op["candidate"]["kind"] == "artifact.bytes":
                self.batch.artifacts.read(op["candidate"]["digest"])
            self.emit("ResultSubmitted", fields, commitment=self.scope, work=assignment["work"],
                      generation=assignment["generation"], producer=self.actor)
        elif name == "obligation.set":
            self.get(op["work"], "WorkItem")
            prior = self.view.records.get(key)
            if prior and prior["kind"] != "Obligation":
                raise Refusal("id_exists")
            if self.binding["role"] == "verifier":
                pinned = self.binding["criterion"]
                if (prior or op["version"] != 1 or pinned["work"] != op["work"] or pinned["observer"] != op["observer"]
                        or any(hashlib.sha256(pinned[k]).hexdigest() != op[k+"_sha256"] for k in ("definition", "environment"))):
                    raise Refusal("forbidden")
                for k in ("definition", "environment"):
                    self.batch.artifacts.put(pinned[k])
            if (prior and self.get(key, "Obligation")["work"] != op["work"]
                    or op["version"] != (prior["fields"]["version"] + 1 if prior else 1)):
                raise Refusal("invalid_transition")
            self.emit("ObligationSet", fields, commitment=self.scope)
        elif name == "run.open":
            criterion = self.get(op["obligation"], "Obligation")
            result = self.get(op["result"], "Result")
            work = self.get(criterion["work"], "WorkItem")
            if result["work"] != criterion["work"] or op["result"] != work.get("result"):
                raise Refusal("invalid_transition")
            if result["producer"] in (criterion["observer"], criterion["verifier"]):
                raise Refusal("forbidden")
            frozen = {k: criterion[k] for k in ("observer", "verifier", "definition_sha256", "environment_sha256")}
            self.emit("RunOpened", fields, commitment=self.scope, criterion_version=criterion["version"], **result, **frozen)
        elif name in ("observation.record", "evidence.record"):
            run = self.get(op["run"], "VerificationRun")
            role = self.binding["role"]
            if self.actor != run[role] or self.actor == run["producer"]:
                raise Refusal("forbidden")
            if name == "observation.record":
                proof = self.binding.get("witness")
                if not proof or proof["candidate"] != run["candidate"] or any(
                    hashlib.sha256(proof[k]).hexdigest() != run[k+"_sha256"] for k in ("definition", "environment")
                ):
                    raise Refusal("witness_mismatch")
                for k in ("definition", "environment"):
                    self.batch.artifacts.put(proof[k])
                sha = self.batch.artifacts.put(proof["artifact"])
                self.emit("ObservationRecorded", fields, commitment=self.scope, observer=self.actor, artifact_sha256=sha)
            else:
                observation = self.get(op["observation"], "Observation")
                if observation["run"] != op["run"] or observation["observer"] != run["observer"]:
                    raise Refusal("witness_mismatch")
                for sha in (observation["artifact_sha256"], run["definition_sha256"], run["environment_sha256"]):
                    self.batch.artifacts.read(sha)
                self.emit("EvidenceRecorded", fields, commitment=self.scope, verifier=self.actor)
        elif name == "obligation.waive":
            criterion = self.get(op["obligation"], "Obligation")
            result = self.get(op["result"], "Result")
            work = self.get(criterion["work"], "WorkItem")
            if (op["version"] != criterion["version"] or result["work"] != criterion["work"]
                    or work.get("result") != op["result"]):
                raise Refusal("invalid_transition")
            self.emit("ObligationWaived", fields, commitment=self.scope, work=criterion["work"],
                      generation=work["generation"], candidate=work["candidate"], principal=self.actor)
        else:
            self.get(key, "WorkItem")
            root = self.commitment["root"] == key
            if name == "work.accept" and not root:
                raise Refusal("forbidden")
            outcome = self.view.outcome(key)
            reasons = self.view.reasons(key)
            if not outcome or name == "work.close" and root:
                raise Refusal("closure_blocked", {"work": key, "reasons": reasons})
            if self.view.closed(key):
                raise Refusal("already_decided")
            if name == "work.accept":
                if outcome != op["outcome"]:
                    raise Refusal("outcome_mismatch")
                self.emit("WorkAccepted", {"id": key, "outcome": outcome, "principal": self.actor})
            for sha in self.view.artifacts(key):
                self.batch.artifacts.read(sha)
            self.emit("WorkClosed", {"id": key, "outcome": outcome})
            if root:
                self.emit("CommitmentSatisfied", {"id": self.scope})
        self.reopen_changed()
        return "admitted", "ok", {}

    def reopen_changed(self):
        view = WorkView(self.batch.projection)
        for key, row in view.records.items():
            f = row["fields"]
            if (row["commitment"] == self.scope and row["kind"] == "WorkItem" and f["state"] == "closed"
                    and not view.closed(key)):
                state = "submitted" if f["candidate"] else "assigned" if f.get("assignment") else "open"
                self.emit("WorkReopened", {"id": key, "state": state})
        c = view.fields(self.scope)
        if c["state"] == "satisfied" and not view.closed(c["root"]):
            self.emit("CommitmentReopened", {"id": self.scope})
