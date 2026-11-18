"""Protected seeded measurements and journal-based E1 scoring. No treatment imports."""

from dataclasses import dataclass

from .artifacts import FIXTURE, changes
from .measurement import probe, same, valid_legacy, valid_sample

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
        samples.append([[name, channel, seconds * 1_000_000] for name, channel, seconds in events
                        if last < seconds <= time])
        last = time
    return {"enqueued": len(events), "samples": samples, "pending": 0}


def measure(repo, oid, root, seed, rank, *, labels=None):
    selected = set(LABELS if labels is None else labels)
    if not selected.issubset(LABELS):
        raise ValueError("unknown protected criterion")
    checks = {label: [] for label in LABELS if label in selected}
    cases = [(label, case) for label, case in inputs(seed, rank) if label in selected]
    if cases:
        actual = probe(root, cases=[case for _, case in cases])
        for (label, case), observed in zip(cases, actual, strict=True):
            checks[label].append({"input": case, "expected": expected(case), "observed": observed})
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
    return {label: {"criterion": label, "version": 1, "candidate": oid, "cases": rows,
                    "verdict": "pass" if all(same(r["observed"], r["expected"]) for r in rows) else "fail"}
            for label, rows in checks.items()}


def public_diagnosis(reply, *, submitted=True):
    """Read explicit labels released to the arm. Requested inputs and oracle facts are private."""
    if type(reply) is not dict:
        return set()
    labels = set()
    for key in ("missing", "diagnosis"):
        listed = reply.get(key, [])
        if isinstance(listed, list):
            labels.update(value for value in listed if isinstance(value, str))
    names = {"O-" + label.replace("/", "-"): label for label in LABELS}
    for reason in reply.get("reasons", []) if submitted else []:
        if isinstance(reason, dict) and reason.get("code") == "missing_evidence":
            labels.update(names.get(ref, ref) for ref in reason["refs"])
    return labels


@dataclass
class Assessment:
    false_candidates: list
    correct_candidates: list
    missing_candidates: set
    fault_measured: bool
    named: bool
    caught: bool
    extra: int

    def score(self, require_fault):
        if self.missing_candidates or (require_fault and not self.fault_measured):
            return None
        return {"false_parent_closure": bool(self.false_candidates), "missing_named": self.named,
                "caught": self.caught, "correct_parent_closure": bool(self.correct_candidates),
                "extra_diagnoses": self.extra}


def assess(journal):
    truth, closes = {}, []
    fault = first = None
    repaired = exposed = False
    for row in journal:
        p = row["payload"]
        labels = set()
        if row["kind"] == "oracle":
            judgments = truth.setdefault(p["candidate"], {})
            if p["criterion"] in judgments or p["criterion"] not in LABELS:
                raise ValueError("duplicate or unknown protected criterion")
            judgments[p["criterion"]] = p["verdict"]
        elif row["kind"] == "fault":
            if fault is not None:
                raise ValueError("multiple E1 interventions")
            fault = p
        elif row["kind"] == "action":
            if p["action"] == "edit" and p["input"].get("repair"):
                repaired = True
            reply = p.get("return")
            if (fault and p["action"] == "submit" and p.get("candidate") == fault.get("candidate")
                    and isinstance(reply, dict) and "failure" not in reply):
                exposed = True
            labels = public_diagnosis(reply, submitted=p.get("candidate") is not None)
        elif row["kind"] == "public_state":
            if fault and p.get("candidate") is not None and p.get("candidate") == fault.get("candidate"):
                exposed = True
            labels = public_diagnosis(p)
            if p["subject"] == "W-notify" and p["to"] == "closed" and p["from"] != "closed":
                closes.append((p["candidate"], fault is not None and not repaired))
        if fault and not repaired and first is None and labels:
            first = labels
    false, correct, missing = [], [], set()
    target = fault["target"] if fault else None
    faulty_parent_closed = False
    for oid, during_fault in closes:
        judgments = truth.get(oid, {})
        if set(judgments) != set(LABELS) or any(v not in ("pass", "fail") for v in judgments.values()):
            missing.add(oid)
        if "fail" in judgments.values():
            false.append(oid)
            if during_fault and judgments.get(target) == "fail":
                faulty_parent_closed = True
        elif oid not in missing:
            correct.append(oid)
    at_fault = truth.get(fault.get("candidate"), {}) if fault else {}
    measured = (exposed and fault is not None and set(at_fault) == set(LABELS)
                and all(v in ("pass", "fail") for v in at_fault.values()) and at_fault.get(target) == "fail")
    named = target in (first or ())
    return Assessment(false, correct, missing, measured, named, named and not faulty_parent_closed,
                      len((first or set()) - {target}))


def score(journal, *, require_fault=True):
    return assess(journal).score(require_fault)


def verify_report(report, oid, label, seed, rank):
    """Compare the retained test input/expectation with this seeded protocol, then grade its observation."""
    if (report.get("candidate"), report.get("criterion"), report.get("version")) != (oid, label, 1):
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
        specs = [spec for name, spec in inputs(seed, rank) if name == label]
        if not specs or not same([r.get("input") for r in cases], specs):
            raise ValueError("protected inputs disagree with preregistered seed")
        if not all(valid_sample(r["observed"], spec) for r, spec in zip(cases, specs, strict=True)):
            raise ValueError("invalid protected queue observation")
        expectations = [expected(spec) for spec in specs]
    if not same([r.get("expected") for r in cases], expectations):
        raise ValueError("protected expectations disagree with protocol")
    return "pass" if all(same(row["observed"], exp) for row, exp in zip(cases, expectations, strict=True)) else "fail"
