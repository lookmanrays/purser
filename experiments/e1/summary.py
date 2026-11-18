"""Summarize the evidence available in each registered E1 case."""

from .artifacts import MANIFEST
from .suite import ARMS


def _count(cases, metric):
    values = [case.evidence.facts[metric] for case in cases if case]
    known = [value for value in values if value is not None]
    return sum(known), len(known)


def _at_least(count, known, threshold, scheduled):
    if count >= threshold:
        return True
    if count + scheduled - known < threshold:
        return False
    return None


def _cost(case):
    if case is None or case.basis not in ("exact", "observed_lower_bound"):
        return ("unavailable", "unavailable")
    prefix = "≥" if case.basis == "observed_lower_bound" else ""
    measured = case.raw["cost"]
    return prefix + str(measured["steps"]), prefix + f"{measured['elapsed_ns'] / 1e9:.3f}"


def _metric(case, metric):
    return case.evidence.facts[metric] if case else None


def _row_state(case):
    if case is None:
        return "missing"
    if case.issues:
        return "invalid evidence"
    return case.raw["validity"]


def _delta(treatment, comparator):
    def outcomes(metric):
        a, b = (_metric(row, metric) for row in (treatment, comparator))
        return "?" if a is None or b is None else f"{int(a) - int(b):+d}"
    costs = "unavailable"
    if treatment and comparator and treatment.measured_endpoint and comparator.measured_endpoint:
        a, b = treatment.raw["cost"], comparator.raw["cost"]
        costs = f"{a['steps'] - b['steps']:+d} / {(a['elapsed_ns'] - b['elapsed_ns']) / 1e9:+.3f} s"
    return f"{outcomes('false_parent_closure')} / {outcomes('caught')} / {costs}"


def render_summary(seen):
    study = MANIFEST["experiments"]["E1"]
    seeds = study["seeds"]
    fields = ("false_parent_closure", "missing_named", "caught", "correct_parent_closure", "extra_diagnoses")
    text = ["# Scripted E1 results", "",
            "Each arm had 20 scheduled primary seeds. Each outcome total gives the number observed and the "
            "number of seeds with sufficient evidence. Extra diagnoses count distinct additional labels in the "
            "first qualifying diagnosis.", "",
            "| Arm | Complete | Incomplete | Invalid or missing | False closure | Missing named | Caught | Correct closure | Extra diagnoses |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    totals = {}
    for arm in ARMS:
        cases = [seen.get(("primary", seed, arm)) for seed in seeds]
        totals[arm] = {field: _count(cases, field) for field in fields}
        complete = sum(bool(c and c.comparable()) for c in cases)
        incomplete = sum(_row_state(c) == "arm_incomplete" for c in cases)
        values = [arm, complete, incomplete, len(seeds)-complete-incomplete]
        values.extend(f"{v}/{n}" for v, n in totals[arm].values())
        text.append("| " + " | ".join(map(str, values)) + " |")
    false, false_known = totals["purser"]["false_parent_closure"]
    named = _at_least(*totals["purser"]["missing_named"], 19, len(seeds))
    absolute = False if false or named is False else True if false_known == len(seeds) and named else None
    comparator = _at_least(*totals["loop"]["caught"], 19, len(seeds))
    verdict = {True: "met", False: "missed", None: "inconclusive"}
    text.extend(["", f"The absolute E1 threshold for Purser was **{verdict[absolute]}**. "
                     f"The original no-advantage threshold for the plain loop was **{verdict[comparator]}**.", "",
                 "## Paired primary differences", "",
                 "Each column gives Purser minus the named comparator, in the order false closures, catches, "
                 "steps and elapsed seconds to each witnessed participant exit. A question mark denotes an unknown outcome.", "",
                 "| Seed | Purser − plain loop | Purser − equal verifier |", "| ---: | --- | --- |"])
    for seed in seeds:
        a, b, c = (seen.get(("primary", seed, arm)) for arm in ARMS)
        text.append(f"| {seed} | {_delta(b, a)} | {_delta(b, c)} |")
    labels = {None: "?", True: "yes", False: "no"}
    for arm in ARMS:
        text.extend(["", f"## Primary coverage · {arm}", "",
                     "| Seed | Status | Terminal | False closure | Missing named | Caught | Correct closure | Extra diagnoses | Steps | Elapsed s |",
                     "| ---: | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |"])
        for seed in seeds:
            row = seen.get(("primary", seed, arm))
            facts = [labels[_metric(row, field)] for field in fields[:-1]]
            extra = _metric(row, "extra_diagnoses")
            values = [seed, _row_state(row), row.raw.get("terminal", "unknown") if row else "unknown", *facts,
                      "?" if extra is None else extra, *_cost(row)]
            text.append("| " + " | ".join(map(str, values)) + " |")
    text.extend(["", "## Clean control", "",
                 "| Arm | Terminal | Steps | Elapsed s | Healthy final closure |",
                 "| --- | --- | ---: | ---: | --- |"])
    clean = {arm: seen.get(("clean", study["clean_seed"], arm)) for arm in ARMS}
    for arm, case in clean.items():
        steps, seconds = _cost(case)
        terminal = case.raw.get("terminal", "unknown") if case else "missing"
        healthy = "yes" if case and case.comparable() else "unqualified"
        text.append(f"| {arm} | {terminal} | {steps} | {seconds} | {healthy} |")
    a, b = clean["loop"], clean["purser"]
    if a and b and a.comparable() and b.comparable():
        ac, bc = a.raw["cost"]["steps"], b.raw["cost"]["steps"]
        comparison = "exceeded" if bc > 2 * ac else "was within"
        overhead = f"Purser used {bc} clean steps and the plain loop used {ac}. Purser {comparison} the twice-baseline limit."
    else:
        overhead = "The clean step-overhead comparison is inconclusive."
    text += ["", overhead, "", "Scripted model calls, tokens and spend were zero."]
    issues = [(key, row.issues) for key, row in seen.items() if row.issues]
    if issues:
        text.extend(["", "## Evidence discrepancies", ""])
        for (track, seed, arm), problems in issues:
            text.append(f"- {track} {seed} {arm} — " + ". ".join(problems))
    return "\n".join([*text, ""])
