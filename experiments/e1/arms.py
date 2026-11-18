"""The plain plan and the additional verifier-equipped plan use identical decision logic."""

import json

from .artifacts import FIXTURE, changes, encoded, sha
from .executor import LEAVES, ORDER, PARENT, PARENT_CHECKS, PATHS, TASKS, local_check
from .measurement import probe, same
from .outcomes import is_closed
from .recovery import write_atomic


def self_review(repo, oid, root, work):
    delta = changes(repo, oid)
    if work in LEAVES:
        check = local_check(root, work)
        touched = any(e["path"] == PATHS[work] and e["status"] == "M" for e in delta)
        missing = [] if check["pass"] and touched else [LEAVES[work]]
        return {"missing": missing, "check": check, "changes": delta}
    allowed = set(FIXTURE["public_paths"])
    outside = [e["path"] for e in delta if e["path"] not in allowed
               or (e["old_mode"], e["new_mode"]) != ("100644", "100644")]
    legacy = local_check(root, PARENT)
    case = {"channel": "inbox", "ids": ["n", "n"], "disabled": [], "attempts": 2,
            "base": 1, "times": [0, 1]}
    actual = probe(root, cases=[case])
    expected = [{"enqueued": 2, "samples": [[["n", "inbox", 0]], [["n", "inbox", 1_000_000]]], "pending": 0}]
    missing = [label for label, ok in zip(PARENT_CHECKS, (not outside, legacy["pass"], same(actual, expected)))
               if not ok]
    return {"missing": missing, "changes": delta, "legacy": legacy, "smoke": actual}


class Plan:
    def __init__(self, directory, *, tasks=TASKS):
        self.file = directory / "plan.json"
        self.invocation = None
        self.publish({work: {"state": "open", "candidate": None, "verified": {},
                             "work": {"id": work, "title": tasks[work]}} for work in (*ORDER, PARENT)})

    def publish(self, plans):
        write_atomic(self.file, encoded(plans))

    def read(self):
        return json.loads(self.file.read_bytes())

    def publication(self):
        return self.invocation

    def update(self, work, *, diagnosis=(), **fields):
        plans = self.read()
        row = plans[work]
        row.update(fields)
        invocation = self.publication()
        if invocation is not None:
            row["_publication"] = {**invocation, "subject": work, "candidate": row["candidate"],
                                   "diagnosis": list(diagnosis)}
        self.publish(plans)
        return {"state": row["state"]}

    def _plan_row(self, work):
        row = self.read()[work]
        # The controller's receipt identifies a write on readback. It is outside the released context.
        row.pop("_publication", None)
        return row

    def context(self, work):
        return self._plan_row(work)

    def assign_again(self, work):
        return self.update(work, state="open", candidate=None, verified={})

    def submit(self, work, oid):
        return self.update(work, state="submitted", candidate=oid, verified={})

    def opening(self, work, label):
        return {"criterion": label, "candidate": self._plan_row(work)["candidate"]}

    def observe(self, work, label, observed):
        return {"criterion": label, "candidate": observed["candidate"], "observed": observed["observed"]}

    def verify(self, work, label, observed):
        checked = self._plan_row(work)["verified"]
        checked[label] = observed["verdict"]
        diagnosis = [label] if observed["verdict"] in ("fail", "inconclusive") else []
        return self.update(work, verified=checked, diagnosis=diagnosis)

    def decide(self, work, audit):
        plans = self.read()
        missing = set(audit["missing"])
        missing.update(label for label, result in plans[work]["verified"].items() if result != "pass")
        if work == PARENT:
            missing.update(LEAVES[k] for k in ORDER if not is_closed(plans[k]["state"]))
        self.update(work, state=plans[work]["state"] if missing else "closed", diagnosis=sorted(missing))
        return {"diagnosis": sorted(missing), "claim": self._plan_row(work)}

    def public_view(self):
        original = self.file.read_bytes()
        rows = json.loads(original)
        return rows, {"plan_sha256": sha(original)}

    def states(self):
        return {k: row["state"] for k, row in self.read().items()}

    def retained(self):
        return {"plan": self.read()}
