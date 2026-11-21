"""The operational verifier shared by the two verifier-equipped arms. Public fixed probes."""

from .artifacts import FIXTURE, changes
from .measurement import probe, same


def inspect(repo, oid, root, label, *, run_probe=probe):
    channel, ids = "local", ["left"]
    test = {"channel": channel, "ids": ids, "disabled": [], "attempts": 1, "base": 3, "times": [0, 2, 3, 6]}
    if label == "legacy/compat":
        actual = run_probe(root, legacy=True)
        answer = {"tests": 28, "errors": [], "failures": [], "skipped": []}
    elif label == "scope/paths":
        delta = changes(repo, oid)
        actual = [e["path"] for e in delta if e["path"] not in FIXTURE["public_paths"]
                  or (e["old_mode"], e["new_mode"]) != ("100644", "100644")]
        answer = []
    else:
        if label in ("W2/retry", "queue/composition"):
            test["attempts"] = 3
        if label in ("W3/delivery", "queue/composition"):
            test["ids"] = ["left", "right", "left"]
            ids = ["left", "right"]
        if label == "W1/preferences":
            test["disabled"], ids = [channel], []
        actual = run_probe(root, cases=[test])
        expected = [[[i, channel, t * 1_000_000] for i in ids] if t in range(0, test["attempts"] * 3, 3)
                    else [] for t in test["times"]]
        answer = [{"enqueued": test["attempts"] * len(ids), "samples": expected, "pending": 0}]
    return {"criterion": label, "version": 1, "candidate": oid,
            "input": None if label in ("scope/paths", "legacy/compat") else test,
            "observed": actual, "expected": answer}


def verdict(report, label, oid):
    if (report["candidate"], report["criterion"], report["version"]) != (oid, label, 1):
        raise ValueError("public observation is for a different candidate or criterion")
    return "pass" if same(report["observed"], report["expected"]) else "fail"
