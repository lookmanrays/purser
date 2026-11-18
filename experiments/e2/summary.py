"""Present per-measure availability, paired comparisons and both frozen E2 predicates."""

from math import inf

from experiments.e1.artifacts import MANIFEST
from experiments.e1.suite import ARMS

from .outcomes import conjunction

TRIALS = MANIFEST["experiments"]["E2"]
N = len(TRIALS["seeds"])


def value(case, key):
    if case is None:
        return None
    if (key in ("intervention_reached", "unaffected_both_retained", "unaffected_reopened", "affected_invalid_closure",
                "rework_steps", "final_pass", "steps", "elapsed_ns") and not case.facts.get("registration_confirmed", True)):
        return None
    if key in ("steps", "elapsed_ns", "final_pass") and case.facts.get("cohort_eligible", True) is not True:
        return None
    if key == "steps":
        return case.facts.get("charged_attempts_observed") if case.comparable_steps else None
    if key == "elapsed_ns":
        return case.facts.get("witnessed_elapsed_ns") if case.comparable_elapsed else None
    return case.facts.get(key)


def shown(v):
    if v is None:
        return "?"
    if type(v) is bool:
        return "yes" if v else "no"
    return str(v)


def aggregate(cases, key):
    known = [v for c in cases if (v := value(c, key)) is not None]
    return sum(known), len(known)


def verdict(v):
    return "inconclusive" if v is None else "met" if v else "not met"


def predicates(purser, comparison):
    if len(purser) != N or len(comparison) != N:
        raise ValueError(f"E2 predicates require exactly {N} registered slots per arm")
    retained, nr = aggregate(purser, "unaffected_both_retained")
    p, np = aggregate(purser, "rework_steps")
    b, nb = aggregate(comparison, "rework_steps")
    fp, nfp = aggregate(purser, "final_pass")
    fb, nfb = aggregate(comparison, "final_pass")
    def at_least(low_a, high_a, low_b, high_b):
        return True if low_a >= high_b else False if high_a < low_b else None
    def bounds(group):
        low = high = 0
        for case in group:
            exact = value(case, "rework_steps")
            if exact is not None:
                low += exact
                high += exact
            elif value(case, "intervention_reached") is True:
                lower = value(case, "rework_observed")
                low += lower if lower is not None else 0
                upper = value(case, "rework_upper")
                if upper is None:
                    upper = MANIFEST["budgets"]["scripted"]["max_steps"] if value(case, "admission_ceiling_verified") else inf
                high += upper
            else:
                # Rework is undefined when the required intervention is never reached.
                return None
        return low, high
    p_bounds, b_bounds = bounds(purser), bounds(comparison)
    half = lower = None
    if p_bounds is not None and b_bounds is not None:
        pl, ph = p_bounds
        bl, bh = b_bounds
        half = at_least(bl, bh, 2*pl, 2*ph)
        lower = at_least(bl, bh, pl+1, ph+1)
    final = at_least(fp, fp + N-nfp, fb, fb + N-nfb)
    retention = at_least(retained, retained + N-nr, 18, 18)
    both = conjunction([lower, final])
    ratio = ("inconclusive" if np != N or nb != N else "undefined (zero denominator)" if b == 0 else f"{p/b:.3f}")
    return dict(retention=retention, half=half, final=final, passed=conjunction([retention, half, final]),
                no_advantage=None if both is None else not both, ratio=ratio)


def render_summary(cases, *, stats=None):
    seeds = TRIALS["seeds"]
    primary = {arm: [cases.get(("primary", s, arm)) for s in seeds] for arm in ARMS}
    registration = []
    if stats:
        kind = "Official replicate 1" if stats["replicate"] == 1 else f"Private replicate {stats['replicate']}"
        registration = [f"{kind} has {stats['records']} of {stats['registered']} registered records, "
                        f"{stats['missing_registered']} are missing.", ""]
    out = ["# Scripted E2 results", "", *registration,
           "Twenty matched primary ranks were registered. Count columns show the total and the number of seeds "
           "with an observed value. Missing binary outcomes can range from zero to one. Thresholds use those bounds. "
           "A question mark marks an unavailable measurement.", "",
           "| Arm | Complete | Incomplete | Invalid or missing | Checkpoint reached | Both retained | Invalid affected closure | Rework steps / measured | Final pass | Total steps / measured |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    def total(group, key):
        s, n = aggregate(group, key)
        return f"{s}/{n}"
    for arm, group in primary.items():
        validities = [case.raw.get("validity") if case and not case.issues else None for case in group]
        complete = validities.count("complete")
        incomplete = validities.count("arm_incomplete")
        vals = [total(group, k) for k in ("intervention_reached", "unaffected_both_retained",
                 "affected_invalid_closure", "rework_steps", "final_pass", "steps")]
        out.append(f"| {arm} | {complete} | {incomplete} | {N-complete-incomplete} | " + " | ".join(vals) + " |")

    out.extend(["", "## Frozen thresholds", "",
                "Each comparison uses the original rules. The half-cost test compares summed rework across all 20 "
                "ranks. The no-advantage predicate is true when Purser rework is equal or greater, or its final pass count is lower.", "",
                "| Purser compared with | Both retained ≥18 | 2 × Purser rework ≤ comparator | Final passes ≥ comparator | E2 passes | No advantage | Rework ratio |",
                "| --- | --- | --- | --- | --- | --- | ---: |"])
    comparators = ("loop", "loop_equal_verifier")
    for other in comparators:
        tests = predicates(primary["purser"], primary[other])
        out.append(f"| {other} | " + " | ".join(verdict(tests[k]) for k in ("retention", "half", "final", "passed", "no_advantage"))
                   + f" | {tests['ratio']} |")
    out.extend(["", "## Paired primary results", "",
                "Each signed difference is Purser minus its comparator. Columns within each cell give retention, "
                "bad affected closure, rework steps, final pass, total steps and elapsed seconds. Zero is a tie.", "",
                "| Seed | Purser − plain loop | Purser − equal verifier |", "| ---: | --- | --- |"])
    def delta(a, b, key):
        x, y = value(a, key), value(b, key)
        if x is None or y is None:
            return "?"
        return f"{(x-y)/1e9:+.3f} s" if key == "elapsed_ns" else f"{x-y:+d}"
    for i, seed in enumerate(seeds):
        compar = [" / ".join(delta(primary["purser"][i], primary[b][i], key) for key in
                  ("unaffected_both_retained", "affected_invalid_closure", "rework_steps", "final_pass", "steps", "elapsed_ns"))
                  for b in comparators]
        out.append(f"| {seed} | " + " | ".join(compar) + " |")
    def cost(case, k):
        v = value(case, k)
        if v is not None:
            return f"{v/1e9:.3f}" if k == "elapsed_ns" else str(v)
        if case and case.basis == "before_release":
            return "0 before release"
        if k == "elapsed_ns" and case and case.basis == "observed_lower_bound":
            low = case.facts.get("active_lower_ns")
            if low is not None:
                return f"≥{low/1e9:.3f}"
        if k == "steps" and case and value(case, "charged_attempts_observed") is not None:
            return f"{case.facts['charged_attempts_observed']} observed (total ?)"
        return "?"
    def work(case):
        v = value(case, "rework_steps")
        if v is None and value(case, "rework_observed") is not None:
            lower, upper = case.facts["rework_observed"], value(case, "rework_upper")
            return f"{lower}–{upper}" if upper is not None else f"≥{lower} (total ?)"
        return shown(v)
    for arm in ARMS:
        out.extend(["", f"## Primary coverage · {arm}", "",
                    "| Seed | Validity / exit | Reached | Both retained | Reopen transitions | Invalid affected closure | Rework | Final pass | Steps | Elapsed s |",
                    "| ---: | --- | --- | --- | ---: | --- | ---: | --- | ---: | ---: |"])
        for seed, c in zip(seeds, primary[arm], strict=True):
            status = f"{c.raw.get('validity','unavailable')} / {c.raw.get('terminal','unavailable')}" if c else "missing"
            if c and c.issues:
                status += " (disputed)"
            fields = [shown(value(c,k)) for k in ("intervention_reached", "unaffected_both_retained", "unaffected_reopened",
                                                  "affected_invalid_closure")]
            out.append(f"| {seed} | {status} | " + " | ".join([*fields, work(c), shown(value(c,"final_pass")),
                                                             cost(c,"steps"), cost(c,"elapsed_ns")]) + " |")
    out.extend(["", "## Clean control · A1 retained", "",
                "| Arm | Exit | Clean exposure | Final pass | Steps | Elapsed s |", "| --- | --- | --- | --- | ---: | ---: |"])
    clean = {arm: cases.get(("clean", TRIALS["clean_seed"], arm)) for arm in ARMS}
    for arm,c in clean.items():
        out.append(f"| {arm} | {c.raw.get('terminal','unavailable') if c else 'missing'} | {shown(value(c,'clean_exposure'))} | {shown(value(c,'final_pass'))} | {cost(c,'steps')} | {cost(c,'elapsed_ns')} |")
    p, b = value(clean["purser"], "steps"), value(clean["loop"], "steps")
    clean_measured = all(c is not None and c.healthy_clean for c in (clean["purser"], clean["loop"]))
    overhead = p > 2*b if clean_measured and p is not None and b is not None else None
    out.extend(["", "The twice-baseline clean overhead flag is **" + shown(overhead) + "**.",
                "", "## Already-closed affected stress control", "",
                "At delivery the affected W2 was already closed under the old schedule. The two bad-closure columns "
                "distinguish a stale closure still visible at delivery from a new bad closure asserted later.", "",
                "| Arm | Exit | Old W2 candidate at delivery | Both siblings retained | Stale at delivery | New bad closure | Rework | Final pass | Steps | Elapsed s |",
                "| --- | --- | --- | --- | --- | --- | ---: | --- | ---: | ---: |"])
    for arm in ARMS:
        c = cases.get(("closed-affected",TRIALS["stress"]["seed"],arm))
        fields = [shown(value(c,k)) for k in ("checkpoint_candidate", "unaffected_both_retained","stale_at_delivery","new_bad_closure")]
        out.append(f"| {arm} | {c.raw.get('terminal','unavailable') if c else 'missing'} | " + " | ".join([*fields, work(c), shown(value(c,"final_pass")),
                                                                                        cost(c,"steps"), cost(c,"elapsed_ns")]) + " |")
    reopening_counts, witnesses = [], []
    for (track, seed, arm), case in sorted(cases.items()):
        name = f"{track} / {seed} / {arm}"
        count = case.facts.get("observed_reopened")
        if count is not None:
            siblings = ", ".join(case.facts["reopened_subjects"]) or "—"
            exact = shown(value(case, "unaffected_reopened"))
            reopening_counts.append(f"| {name} | {count} | {siblings} | {exact} |")
        for field in ("reopen_observations", "closure_observations"):
            for item in case.facts.get(field, []):
                head = item["publication"]
                publication = f"{head[0]} / `{head[1]}`" if isinstance(head, tuple) else f"`{head}`"
                if field == "reopen_observations":
                    event, grade = "reopened", "—"
                else:
                    event = "closed at delivery" if item["at_delivery"] else "closed"
                    grade = shown(item["revised_pass"])
                witnesses.append(f"| {name} | {publication} | {shown(item.get('source_action'))} | {item['subject']} | {event} "
                                 f"| `{item['candidate']}` | {grade} |")
    if reopening_counts:
        out.extend(["", "## Observed sibling reopenings", "",
                    "An incomplete history gives a lower bound on the number of transitions.", "",
                    "| Case | Transitions observed | Distinct siblings observed reopening | Exact eligible total |",
                    "| --- | ---: | --- | ---: |", *reopening_counts])
    if witnesses:
        out.extend(["", "## Public reopening and closure evidence", "",
                    "Each reopening identifies the previously closed candidate. Each closure grades revised retry "
                    "on the candidate declared by that subject.", "",
                    "| Case | Plan digest or kernel revision / hash | Public action | Subject | Public observation | Git candidate | Revised retry passes |",
                    "| --- | --- | --- | --- | --- | --- | --- |", *witnesses])
    diagnosed = [(key, c) for key,c in cases.items() if c.issues or c.unavailable or c.raw.get("validity") != "complete"]
    if diagnosed:
        out.extend(["", "## Unavailable or disputed observations", ""])
        for key,c in diagnosed:
            reasons = [*c.issues, *c.unavailable] or [str(c.raw.get("terminal", "unavailable"))]
            out.append(f"- {' / '.join(str(x) for x in key)} — " + " · ".join(reasons))
            facts = c.facts
            observed = [("old W2 submission", "old_submission_observed"), ("old checkpoint qualified", "checkpoint_qualified"),
                        ("premise delivered", "premise_delivered"), ("prior changed exposure", "prior_changed_exposure"),
                        ("scoring interval quiescent", "clock_pause_quiescent"),
                        ("scoring exclusion accounted", "exclusion_accounted"),
                        ("witnessed participant exit", "physical_exit"), ("clean exposure", "clean_exposure"),
                        ("both siblings continuously retained", "observed_retention"),
                        ("invalid affected closure", "observed_invalid_closure"), ("stale closure at receipt", "stale_at_delivery"),
                        ("new invalid affected closure", "new_bad_closure"), ("final Git criteria pass", "final_pass")]
            out.append("  Observed facts — " + ", ".join(f"{label} {shown(facts.get(field))}" for label,field in observed) + ".")
            interval = facts.get("observed_interval")
            if interval:
                out.append(f"  From delivery through `{interval['through']}`, "
                           f"both retained {shown(interval['both_retained'])}, reopenings {interval['reopened']}, "
                           f"invalid affected closure {shown(interval['invalid_closure'])}.")
            kernel = facts.get("kernel_recovery")
            if kernel:
                out.append(f"  Canonical SQLite prefix through revision {kernel['original_prefix_revision']}. "
                           f"Authenticated history through revision {kernel['recovered_through_revision']}.")
            for transmission in facts.get("kernel_transmissions", ()):
                head = transmission["observed_head"]
                stamp = f"r{head['revision']} `{head['head_hash']}`" if head else "unknown"
                out.append(f"  Captured port return for attempt {transmission['attempt']}, "
                           f"{transmission['operation']} on {transmission['subject']}, candidate "
                           f"`{transmission['candidate'] or '?'}`. Receipt {transmission['disposition'] or '?'} "
                           f"at revision {shown(transmission['receipt_revision'])}, observed head {stamp}.")
            candidate = value(c, "last_observed_candidate")
            if candidate:
                where = value(c, "last_observed_utc")
                publication = value(c, "last_observed_publication")
                if where is None and publication is not None:
                    where = f"{facts['last_observed_subject']} submission {facts['last_observed_attempt']}, publication {publication}"
                where = f", {where}" if where else ""
                grades = facts.get("last_observed_criteria", {})
                out.append(f"  Last measured candidate `{candidate}` ({value(c,'last_observed_source')}{where}), "
                           f"registered criteria pass {shown(value(c,'last_observed_pass'))}. "
                           + ", ".join(f"{label} {grade or '?'}" for label, grade in grades.items()) + ".")
            elif facts.get("sealed_candidate_criteria"):
                for oid, grades in sorted(facts["sealed_candidate_criteria"].items()):
                    out.append(f"  Surviving sealed candidate `{oid}` — "
                               + ", ".join(f"{label} {grade or '?'}" for label, grade in grades.items()) + ".")
            if facts.get("exit_git_dirty") is True:
                out.append(f"  The stopped working tree was dirty at `{facts['exit_git_head']}`.")
    observations, historical = [], []
    for (track, seed, arm), case in sorted(cases.items()):
        for claim in case.facts.get("historical_protected_observations", ()):
            for record in claim["recordings"]:
                historical.append(f"| `{claim['candidate']}` | {track} / {seed} / {arm} | {claim['criterion']} "
                                  f"| {claim['version']} | {record['verdict']} | {record['source']} "
                                  f"| `{record['sha256']}` | {claim['journal']} |")
        for oid, slots in sorted(case.facts.get("protected_slots", {}).items()):
            for slot in slots:
                grade = slot["verdict"] or "?"
                history = slot["historical_verdict"] or "?"
                source = slot["source"] or "unavailable"
                observations.append(f"| {track} / {seed} / {arm} | `{oid}` | {slot['criterion']} "
                                    f"| {slot['version']} | {history} | {grade} | {source} "
                                    f"| {slot['original']} | {slot['replay']} |")
    if historical:
        out.extend(["", "## Historical protected observations without established case membership", "",
                    "These originals record judgments on the named Git subjects. Membership and independent replay "
                    "for these subjects remain unestablished.", "",
                    "| Report subject | Case | Criterion | Version | Original verdict | Origin | Report SHA-256 | Journal row |",
                    "| --- | --- | --- | ---: | --- | --- | --- | --- |", *historical])
    if observations:
        out.extend(["", "## Protected Git observations", "",
                    "| Case | Candidate | Criterion | Version | Retained verdict | Available grade "
                    "| Grade source | Original journal row | Independent replay |",
                    "| --- | --- | --- | ---: | --- | --- | --- | --- | --- |", *observations])
    out.extend(["", "The scripted protocol permits zero model calls, tokens and spend.", ""])
    return "\n".join(out)
