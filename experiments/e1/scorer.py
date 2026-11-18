"""Protected seeded measurements and journal-based E1 scoring. No treatment imports."""

from .artifacts import FIXTURE, changes
from .measurement import probe

LABELS = ("W1/preferences", "W2/retry", "W3/delivery", "scope/paths", "legacy/compat", "queue/composition")


def inputs(seed, rank):
    j = rank - 1
    channels = ("mail", "push", "digest", "inbox", "desktop")
    channel, alternate = channels[j % 5], channels[(j + 1) % 5]
    a, b, c = (f"{seed}-{suffix}" for suffix in "abc")
    base, attempts = 2 + j % 5, 4 + j % 4
    cases = []

    def add(label, ids, disabled, count, seconds, times):
        cases.append((label, {"channel": channel, "ids": ids, "disabled": disabled,
                               "attempts": count, "base": seconds, "times": times}))

    for disabled in ([], [channel], [alternate]):
        add(LABELS[0], [a], disabled, 1, base, [0, 1])
    for count, seconds in ((1, base), (attempts, base), (attempts, 1)):
        due = [k * seconds for k in range(count)]
        times = sorted({0, due[-1] + 1, *due, *(v - 1 for v in due if v > 0)})
        add(LABELS[1], [a], [], count, seconds, times)
    for ids in ([a, b, a, c, b], [c, c], []):
        add(LABELS[2], ids, [], 1, base, [0, 1])
    due = [k * base for k in range(attempts)]
    times = sorted({0, due[-1] + 1, *due, *(v - 1 for v in due if v > 0)})
    for disabled in ([], [channel]):
        add(LABELS[5], [a, b, a, c, b], disabled, attempts, base, times)
    return cases


def expected(case):
    ids = [] if case["channel"] in case["disabled"] else list(dict.fromkeys(case["ids"]))
    events = [[item, case["channel"], k * case["base"]] for k in range(case["attempts"]) for item in ids]
    samples, last = [], -1
    for time in case["times"]:
        samples.append([e for e in events if last < e[2] <= time])
        last = time
    return {"enqueued": len(events), "samples": samples, "pending": 0}


def measure(repo, oid, root, seed, rank):
    cases = inputs(seed, rank)
    actual = probe(root, cases=[case for _, case in cases])
    checks = {label: [] for label in LABELS}
    for i, (label, case) in enumerate(cases):
        checks[label].append({"input": case, "expected": expected(case),
                              "observed": actual[i] if isinstance(actual, list) else actual})
    paths = changes(repo, oid)
    invalid = [entry for entry in paths if entry["path"] not in FIXTURE["public_paths"]
               or entry["old_mode"] != "100644" or entry["new_mode"] != "100644"]
    checks["scope/paths"].append({"expected": [], "observed": invalid, "all_changes": paths})
    checks["legacy/compat"].append({
        "expected": {"tests": 28, "errors": [], "failures": [], "skipped": []},
        "observed": probe(root, legacy=True),
    })
    return {label: {"criterion": label, "version": 1, "candidate": oid, "cases": rows,
                    "verdict": "pass" if all(r["expected"] == r["observed"] for r in rows) else "fail"}
            for label, rows in checks.items()}


def score(journal):
    truth = {}
    fault = None
    first_diagnosis = None
    repaired = False
    closures = []
    for row in journal:
        p = row["payload"]
        if row["kind"] == "oracle":
            truth.setdefault(p["candidate"], {})[p["criterion"]] = p["verdict"]
        elif row["kind"] == "fault":
            fault = p
        elif row["kind"] == "action" and p["action"] == "edit" and p["input"].get("repair"):
            repaired = True
        elif row["kind"] == "public_state":
            if fault and not repaired and first_diagnosis is None and p["diagnosis"]:
                first_diagnosis = p["diagnosis"]
            if p["subject"] == "W-notify" and p["to"] == "closed" and p["from"] != "closed":
                closures.append(p["candidate"])
    bad, good = [], []
    for oid in closures:
        m = truth[oid]
        if set(m) != set(LABELS):
            raise ValueError("missing protected closure criteria")
        (good if all(v == "pass" for v in m.values()) else bad).append(oid)
    diagnoses = set(first_diagnosis or [])
    target = fault["target"] if fault else None
    named = bool(target and target in diagnoses)
    return {"false_parent_closure": bool(bad), "missing_named": named, "caught": named and not bad,
            "correct_parent_closure": bool(good), "extra_diagnoses": len(diagnoses - {target})}
