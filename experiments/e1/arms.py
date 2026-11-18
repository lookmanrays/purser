"""The plain plan and the additional verifier-equipped plan use identical decision logic."""

import json

from .artifacts import FIXTURE, changes, encoded, sha
from .executor import LEAVES, ORDER, PARENT, PARENT_CHECKS, PATHS, TASKS, local_check
from .measurement import probe


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
    expected = [{"enqueued": 2, "samples": [[["n", "inbox", 0]], [["n", "inbox", 1]]], "pending": 0}]
    missing = [label for label, ok in zip(PARENT_CHECKS, (not outside, legacy["pass"], actual == expected))
               if not ok]
    return {"missing": missing, "changes": delta, "legacy": legacy, "smoke": actual}


class Plan:
    def __init__(self, directory):
        self.file = directory / "plan.json"
        self.file.write_bytes(encoded({work: {"state": "open", "candidate": None, "verified": {}, "work": {"id": work, "title": TASKS[work]}}
                                       for work in (*ORDER, PARENT)}))

    def read(self):
        return json.loads(self.file.read_bytes())

    def update(self, work, **fields):
        plans = self.read()
        plans[work].update(fields)
        self.file.write_bytes(encoded(plans))
        return {"state": plans[work]["state"]}

    def context(self, work):
        return self.read()[work]

    def assign_again(self, work):
        return self.update(work, state="open", candidate=None, verified={})

    def submit(self, work, oid):
        return self.update(work, state="submitted", candidate=oid, verified={})

    def opening(self, work, label):
        return {"criterion": label, "candidate": self.context(work)["candidate"]}

    def observe(self, work, label, observed):
        return {"criterion": label, "candidate": observed["candidate"], "observed": observed["observed"]}

    def verify(self, work, label, observed):
        checked = self.context(work)["verified"]
        checked[label] = observed["verdict"]
        return self.update(work, verified=checked)

    def decide(self, work, audit):
        plans = self.read()
        missing = set(audit["missing"])
        missing.update(label for label, result in plans[work]["verified"].items() if result != "pass")
        if work == PARENT:
            missing.update(LEAVES[k] for k in ORDER if plans[k]["state"] != "closed")
        if not missing:
            self.update(work, state="closed")
        return {"diagnosis": sorted(missing), "claim": self.context(work)}

    def public_view(self):
        original = self.file.read_bytes()
        rows = json.loads(original)
        return rows, {"plan_sha256": sha(original)}

    def states(self):
        return {k: row["state"] for k, row in self.read().items()}

    def retained(self):
        return {"plan": self.read()}
