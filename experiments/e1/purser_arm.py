"""The study treatment issues normal bound gate proposals and reads their durable projection."""

from datetime import datetime, timezone

from purser.kernel.gate import admit
from purser.kernel.store import Store
from purser.kernel.wire import digest

from .artifacts import encoded, sha
from .executor import LEAVES, ORDER, PARENT, PARENT_CHECKS, TASKS

SCOPE = "C-notify"
PRINCIPAL = "principal:local"
OBSERVER = "observer:queue"
VERIFIER = "verifier:queue"
EXECUTOR = "executor:script"
IDS = {label: "O-" + label.replace("/", "-") for label in (*LEAVES.values(), *PARENT_CHECKS)}


class Treatment:
    def __init__(self, directory, definitions, environment, *, before_assignments=None, tasks=TASKS):
        self.store = Store(directory / "kernel.sqlite")
        self.store.initialize()
        self.definitions, self.environment = definitions, environment
        self.count, self.assignments, self.results, self.runs, self.observations = 0, {}, {}, {}, {}
        self.receipts = []
        self.on_proposal = None
        self.send("principal", [{"op": "commitment.create", "id": SCOPE, "root": "W-root",
                                 "statement": "Implement local notification preferences, retries and delivery."}])
        hold = self.send("principal", [{"op": "commitment.request_acceptance", "id": SCOPE,
                                       "expires_at": "2030-01-01T00:00:00.000000Z"}])
        self.send("principal", [{"op": "approval.answer", "id": hold["details"]["approval_id"],
                                 "request_digest": hold["digest"], "decision": "approve"}])
        create = [{"op": "work.create", "id": PARENT, "parent": "W-root", "title": tasks[PARENT]}]
        create += [{"op": "work.create", "id": w, "parent": PARENT, "title": tasks[w]} for w in ORDER]
        create += [{"op": "work.coverage", "id": w, "kind": "all", "k": None} for w in ("W-root", PARENT)]
        self.send("planner", create)
        obligations = []
        for work in (*ORDER, PARENT):
            labels = (LEAVES[work],) if work in LEAVES else PARENT_CHECKS
            for label in labels:
                obligations.append({"op": "obligation.set", "id": IDS[label], "work": work, "version": 1,
                                    "title": label, "observer": OBSERVER, "verifier": VERIFIER,
                                    "definition_sha256": sha(definitions[label]),
                                    "environment_sha256": sha(environment)})
        self.send("principal", obligations)
        if before_assignments is not None:
            before_assignments(self)
        for w in (*ORDER, PARENT):
            self.assign_again(w)

    def send(self, role, ops, *, assignment=None, witness=None, premise=None, allow_rejected=False):
        actor = {"principal": PRINCIPAL, "planner": "planner:study", "closure": "closure:study",
                 "executor": EXECUTOR, "observer": OBSERVER, "verifier": VERIFIER}[role]
        binding = {"role": role, "actor_id": actor, "principal_id": PRINCIPAL,
                   "commitment": None if role == "principal" else SCOPE, "assignment": assignment}
        if witness is not None:
            binding["witness"] = witness
        if premise is not None:
            binding["premise"] = premise
        self.count += 1
        proposal = {"v": 1, "proposal_id": f"P-{self.count}", "commitment": SCOPE,
                    "base_revision": self.store.snapshot()["revision"], "justification": [], "ops": ops}
        if self.on_proposal is not None:
            self.on_proposal({"actor_id": actor, "proposal_id": proposal["proposal_id"], "body": proposal,
                              "digest": digest("proposal", {"actor_id": actor, "proposal": proposal})})
        receipt = admit(self.store, binding, proposal, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.receipts.append({"role": role, "proposal": proposal, "receipt": receipt})
        if receipt["disposition"] == "rejected" and not allow_rejected:
            raise RuntimeError(f"{ops[0]['op']}: {receipt}")
        return receipt

    def view(self):
        from purser.kernel.workview import WorkView
        return WorkView(self.store.snapshot()["projection"])

    def rows(self):
        return {r["work"]: r for r in self.view().answers()["q02"]}

    def public_view(self):
        from purser.kernel.workview import WorkView
        snapshot = self.store.snapshot()
        rows = {r["work"]: r for r in WorkView(snapshot["projection"]).answers()["q02"]
                if r["work"] in (*ORDER, PARENT)}
        return rows, {"revision": snapshot["revision"], "head_hash": snapshot["head_hash"]}

    def states(self):
        return {k: r["state"] for k, r in self.rows().items() if k in (*ORDER, PARENT)}

    def context(self, work):
        from purser.driver import context_pack
        return context_pack(self.store.snapshot(), self.assignments[work])

    def assign_again(self, work):
        identity = f"AS-{work}-{self.count}"
        receipt = self.send("planner", [{"op": "work.assign", "id": identity, "work": work, "executor": EXECUTOR}])
        self.assignments[work] = identity
        return receipt

    def submit(self, work, oid):
        aid = self.assignments[work]
        rid = f"R-{self.count}"
        candidate = {"kind": "git.commit", "algorithm": "sha1", "digest": oid}
        receipt = self.send("executor", [{"op": "result.submit", "id": rid, "assignment": aid,
                                         "candidate": candidate}], assignment=aid)
        self.results[work] = (rid, candidate)
        return receipt

    def opening(self, work, label):
        run = f"V-{self.count}"
        receipt = self.send("planner", [{"op": "run.open", "id": run, "obligation": IDS[label],
                                        "result": self.results[work][0]}])
        self.runs[label] = run
        return receipt

    def observe(self, work, label, observed):
        run = self.runs[label]
        obs = f"Obs-{self.count}"
        payload = encoded(observed)
        receipt = self.send("observer", [{"op": "observation.record", "id": obs, "run": run}],
                            assignment=run, witness={"candidate": self.results[work][1],
                            "definition": self.definitions[label], "environment": self.environment, "artifact": payload})
        self.observations[label] = obs
        return receipt

    def verify(self, work, label, observed):
        run = self.runs[label]
        return self.send("verifier", [{"op": "evidence.record", "id": f"E-{self.count}", "run": run,
                                      "observation": self.observations[label], "verdict": observed["verdict"]}],
                         assignment=run)

    def decide(self, work, audit):
        receipt = self.send("closure", [{"op": "work.close", "id": work}], allow_rejected=True)
        reasons = self.view().reasons(work)
        refs = {ref for reason in reasons if reason["code"] == "missing_evidence" for ref in reason["refs"]}
        missing = [label for label, identity in IDS.items() if identity in refs]
        return {"diagnosis": sorted(missing), "reasons": reasons, "receipt": receipt, "row": self.rows()[work]}

    def retained(self):
        return {**retained_kernel(self.store, self.store.snapshot()), "receipts": self.receipts}


def retained_kernel(store, snapshot):
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True)) as db:
        events = [body.decode() for body, in db.execute("SELECT body FROM events ORDER BY revision")]
    return {"original_events": events, "snapshot": snapshot}
