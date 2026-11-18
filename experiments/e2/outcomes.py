"""Derive E2 fields from timed participant endpoints and independent candidate measurements."""

from experiments.e1.executor import LEAVES, PARENT
from experiments.e1.outcomes import CLOSED, LABELS, entered_closure

from . import measure
from .public_sources import identity

FIELDS = ("intervention_reached", "unaffected_both_retained", "unaffected_reopened",
          "affected_invalid_closure", "rework_steps", "final_pass")
WORK = {*LEAVES, PARENT}
REWORK = {"plan", "context", "edit", "check", "submit", "observe", "decide"}


def conjunction(values):
    values = list(values)
    return False if False in values else True if all(v is True for v in values) else None


def checkpoint_eligibility(before, oid, track, passed, unchanged, submitted):
    required = [submitted]
    for subject in ("W1", "W3", "W2"):
        state, candidate = before.get(subject, (None, None))
        if state is None:
            required.append(None)
        else:
            needs_closed = subject != "W2" or track == "closed-affected"
            required.append((state in CLOSED) == needs_closed)
        required.append(passed(oid, LEAVES[subject]))
        if subject == "W2":
            required.append(candidate == oid if subject in before and oid else None)
        else:
            required.extend([passed(candidate, LEAVES[subject]), unchanged.get(subject)])
    return conjunction(required)


def raw_score(facts):
    # Frozen wire fields, other than rework, cannot express unknown. The independent
    # reporter retains every individually observed fact even when this object is null.
    return {k: facts[k] for k in FIELDS} if all(facts[k] is not None for k in FIELDS if k != "rework_steps") else None


def criteria(oid, verdicts, phase):
    """Grade each obligation independently, on its registered version and exact Git commit."""
    return {label: verdicts.get((oid, label, phase if label in measure.REVISED else measure.OLD))
            for label in LABELS}


def all_pass(grades):
    return conjunction(value == "pass" if value in ("pass", "fail") else None for value in grades.values())


def assess(history, track, verdicts, *, delivery, qualification, submitted, unchanged, final_oid,
           endpoint_known, action_sequence_complete):
    def passed(oid, label, version):
        verdict = criteria(oid, verdicts, version)[label]
        return verdict == "pass" if verdict in ("pass", "fail") else None

    phase = measure.OLD if track == "clean" else measure.NEW
    final_grades = criteria(final_oid, verdicts, phase)
    facts = dict.fromkeys(FIELDS)
    facts.update(final_pass=all_pass(final_grades), final_candidate=final_oid, final_criteria=final_grades,
                 rework_observed=None, stale_at_delivery=None, new_bad_closure=None, closure_observations=[],
                 reopened_subjects=[], reopen_observations=[], rework_actions=[], rework_undetermined=[],
                 observed_interval=None, checkpoint_candidate=None, checkpoint_work=None,
                 observed_retention=None, observed_reopened=None, observed_invalid_closure=None)
    faults = [e for e in history.journal if e["kind"] == "fault"]
    received = [e for e in faults if e["payload"].get("publicly_delivered") is True]
    if not received:
        if track != "clean" and delivery is False:
            facts["intervention_reached"] = False
        return facts

    # A witnessed change and a qualifying registered change have distinct evidence.
    fault = received[0] if len(received) == 1 else next(
        (row for row in received if row["payload"] is history.first_delivered), None)
    if fault is None:
        return facts
    p = fault["payload"]
    before = history.checkpoint(p.get("before")) or {}
    oid = p.get("candidate")
    facts.update(checkpoint_candidate=oid, checkpoint_work=before)
    reached = checkpoint_eligibility(before, oid, track, lambda item,label: passed(item,label,measure.OLD),
                                      unchanged, submitted)
    if track != "clean":
        facts["intervention_reached"] = conjunction([delivery, reached, qualification])

    for e in history.journal:
        payload = e["payload"]
        if e["kind"] == "action":
            eligible = payload.get("rework_eligible", payload.get("action") in REWORK and payload.get("subject") in WORK)
            counted = conjunction([payload.get("after_intervention"), eligible])
            if counted is True:
                facts["rework_actions"].append(payload["attempt"])
            elif counted is None:
                facts["rework_undetermined"].append(payload["attempt"])

    retained = []
    saw_delivery = False
    through = history.source.through(p.get("after"))
    contiguous = {}
    for publication, seq, prior, states, at_delivery in history.public_states_after(fault):
        saw_delivery |= at_delivery
        for subject in ("W1", "W3"):
            status, candidate = states[subject]
            if status not in CLOSED:
                retained.append(False)
                if prior is not None and prior[subject][0] in CLOSED:
                    facts["reopen_observations"].append({"subject": subject, "publication": publication,
                                                         "candidate": prior[subject][1], "at_delivery": at_delivery})
            elif candidate != before.get(subject, (None, None))[1]:
                retained.append(passed(candidate, LEAVES[subject], measure.OLD))
        for subject in ("W2", PARENT):
            new = states[subject]
            if new[0] in CLOSED and (at_delivery or prior is not None and entered_closure(prior[subject], new)):
                facts["closure_observations"].append({"seq": seq, "subject": subject, "publication": publication,
                                                      "at_delivery": at_delivery, "candidate": new[1],
                                                      "revised_pass": passed(new[1], LEAVES["W2"], measure.NEW)})
        if through is not None and publication == identity(history.arm, through):
            contiguous = {"through": publication, "from": identity(history.arm, p.get("after")),
                          "both_retained": conjunction(retained), "reopened": len(facts["reopen_observations"]),
                          "declarations": list(facts["closure_observations"])}

    one_change = len(received) == 1 and all(e["payload"].get("publicly_delivered") is not None for e in faults)
    complete = saw_delivery and endpoint_known and history.public_complete and one_change
    reopens = facts["reopen_observations"]
    facts["reopened_subjects"] = sorted({item["subject"] for item in reopens})
    facts["observed_reopened"] = len(reopens)
    facts["observed_retention"] = conjunction([*retained, True if complete else None])

    def invalid(declarations, covered):
        passed = [item["revised_pass"] for item in declarations]
        return True if False in passed else False if covered and None not in passed else None

    declarations = facts["closure_observations"]
    if saw_delivery and contiguous:
        contiguous["invalid_closure"] = invalid(contiguous.pop("declarations"), True)
        facts["observed_interval"] = contiguous
    facts["stale_at_delivery"] = invalid((item for item in declarations if item["at_delivery"]), saw_delivery)
    facts["new_bad_closure"] = invalid((item for item in declarations if not item["at_delivery"]), complete)
    facts["observed_invalid_closure"] = invalid(declarations, complete)
    if facts["intervention_reached"] is True:
        facts.update(unaffected_both_retained=facts["observed_retention"], unaffected_reopened=len(reopens) if complete else None,
                     affected_invalid_closure=facts["observed_invalid_closure"], rework_observed=len(facts["rework_actions"]))
        if endpoint_known and action_sequence_complete:
            facts["rework_upper"] = facts["rework_observed"] + len(facts["rework_undetermined"])
            if not facts["rework_undetermined"]:
                facts["rework_steps"] = facts["rework_observed"]
    return facts
