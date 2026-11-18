"""Protected seeded measurements and journal-based E1 scoring. No treatment imports."""

from .artifacts import FIXTURE, changes
from .measurement import probe, same, valid_legacy, valid_sample

from .outcomes import Assessment, LABELS, action_diagnosis, assess, entered_closure, is_closed, public_diagnosis, score


def linear_offsets(count, base):
    return [k * base for k in range(count)]


def inputs(seed, rank, *, offsets=linear_offsets):
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
        due = offsets(count, seconds)
        times = sorted({0, due[-1] + 1, *due, *(v - 1 for v in due if v > 0)})
        add(LABELS[1], [a], [], count, seconds, times)
    for ids in ([a, b, a, c, b], [c, c], []):
        add(LABELS[2], ids, [], 1, base, [0, 1])
    due = offsets(attempts, base)
    times = sorted({0, due[-1] + 1, *due, *(v - 1 for v in due if v > 0)})
    for disabled in ([], [channel]):
        add(LABELS[5], [a, b, a, c, b], disabled, attempts, base, times)
    return cases


def expected(case, *, offsets=linear_offsets):
    ids = [] if case["channel"] in case["disabled"] else list(dict.fromkeys(case["ids"]))
    events = [[item, case["channel"], due] for due in offsets(case["attempts"], case["base"]) for item in ids]
    samples, last = [], -1
    for time in case["times"]:
        samples.append([[name, channel, seconds * 1_000_000] for name, channel, seconds in events
                        if last < seconds <= time])
        last = time
    return {"enqueued": len(events), "samples": samples, "pending": 0}


def measure(repo, oid, root, seed, rank, *, labels=None, offsets=linear_offsets, version=1):
    selected = set(LABELS if labels is None else labels)
    if not selected.issubset(LABELS):
        raise ValueError("unknown protected criterion")
    checks = {label: [] for label in LABELS if label in selected}
    cases = [(label, case) for label, case in inputs(seed, rank, offsets=offsets) if label in selected]
    if cases:
        actual = probe(root, cases=[case for _, case in cases])
        for (label, case), observed in zip(cases, actual, strict=True):
            checks[label].append({"input": case, "expected": expected(case, offsets=offsets), "observed": observed})
    if "scope/paths" in selected:
        paths = changes(repo, oid)
        invalid = [entry for entry in paths if entry["path"] not in FIXTURE["public_paths"]
                   or entry["old_mode"] != "100644" or entry["new_mode"] != "100644"]
        checks["scope/paths"].append({"expected": [], "observed": invalid, "all_changes": paths})
    if "legacy/compat" in selected:
        checks["legacy/compat"].append({
            "expected": {"tests": 28, "errors": [], "failures": [], "skipped": []},
            "observed": probe(root, legacy=True),
        })
    return {label: {"criterion": label, "version": version, "candidate": oid, "cases": rows,
                    "verdict": "pass" if all(same(r["observed"], r["expected"]) for r in rows) else "fail"}
            for label, rows in checks.items()}


def verify_report(report, oid, label, seed, rank, *, offsets=linear_offsets, version=1):
    """Compare the retained test input/expectation with this seeded protocol, then grade its observation."""
    if (report.get("candidate"), report.get("criterion"), report.get("version")) != (oid, label, version):
        raise ValueError("protected report identity mismatch")
    cases = report.get("cases")
    if type(cases) is not list or not cases or any(type(r) is not dict or "observed" not in r for r in cases):
        raise ValueError("missing protected case observations")
    if label == "legacy/compat":
        if not valid_legacy(cases[0]["observed"]):
            raise ValueError("invalid protected test observation")
        expectations = [{"tests": 28, "errors": [], "failures": [], "skipped": []}]
    elif label == "scope/paths":
        entries = cases[0]["all_changes"]
        if type(entries) is not list:
            raise ValueError("missing scope path audit")
        outside = [e for e in entries if e["path"] not in FIXTURE["public_paths"]
                   or (e["old_mode"], e["new_mode"]) != ("100644", "100644")]
        if not same(cases[0]["observed"], outside):
            raise ValueError("scope finding disagrees with its retained path audit")
        expectations = [[]]
    else:
        specs = [spec for name, spec in inputs(seed, rank, offsets=offsets) if name == label]
        if not specs or not same([r.get("input") for r in cases], specs):
            raise ValueError("protected inputs disagree with preregistered seed")
        if not all(valid_sample(r["observed"], spec) for r, spec in zip(cases, specs, strict=True)):
            raise ValueError("invalid protected queue observation")
        expectations = [expected(spec, offsets=offsets) for spec in specs]
    if not same([r.get("expected") for r in cases], expectations):
        raise ValueError("protected expectations disagree with protocol")
    return "pass" if all(same(row["observed"], exp) for row, exp in zip(cases, expectations, strict=True)) else "fail"
