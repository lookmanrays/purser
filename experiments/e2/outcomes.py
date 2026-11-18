"""Derive E2 fields from timed participant endpoints and independent candidate measurements."""

from experiments.e1.executor import LEAVES, PARENT
from experiments.e1.outcomes import CLOSED, LABELS, entered_closure

from . import measure

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


def assess(journal, track, checkpoint, verdicts, *, delivery, qualification, submitted, unchanged, final_oid,
           endpoint_known, history_complete, action_sequence_complete):
    def passed(oid, label, version):
        verdict = criteria(oid, verdicts, version)[label]
        return verdict == "pass" if verdict in ("pass", "fail") else None

    phase = measure.OLD if track == "clean" else measure.NEW
    final_grades = criteria(final_oid, verdicts, phase)
    facts = dict.fromkeys(FIELDS)
    facts.update(final_pass=all_pass(final_grades), final_candidate=final_oid, final_criteria=final_grades,
                 rework_observed=None, stale_at_delivery=None, new_bad_closure=None, closure_observations=[],
                 reopened_subjects=[], rework_actions=[], checkpoint_candidate=None, checkpoint_work=None,
                 observed_retention=None, observed_reopened=None, observed_invalid_closure=None)
    faults = [e for e in journal if e["kind"] == "fault"]
    received = [e for e in faults if e["payload"].get("publicly_delivered") is True]
    if not received:
        if track != "clean" and delivery is False:
            facts["intervention_reached"] = False
        return facts

    # A witnessed change and a qualifying registered change have distinct evidence.
    fault = received[0]
    p = fault["payload"]
    before = checkpoint(p.get("before")) or {}
    oid = p.get("candidate")
    facts.update(checkpoint_candidate=oid, checkpoint_work=before)
    reached = checkpoint_eligibility(before, oid, track, lambda item,label: passed(item,label,measure.OLD),
                                      unchanged, submitted)
    if track != "clean":
        facts["intervention_reached"] = conjunction([delivery, reached, qualification])

    previous = before
    reopened = set()
    uncertain_sibling = uncertain_closure = False
    stale, bad_closes, bad_states = [], [], []
    for e in journal:
        payload = e["payload"]
        if (e["kind"] == "action" and payload.get("after_intervention") is True
                and payload.get("action") in REWORK and payload.get("subject") in WORK):
            facts["rework_actions"].append(payload["attempt"])
        if e["seq"] < fault["seq"] or e["kind"] not in ("fault", "public_state"):
            continue
        at_delivery = e is fault
        ck = payload.get("checkpoint") if e["kind"] == "public_state" else payload.get("after")
        states = checkpoint(ck)
        if states is None:
            uncertain_closure = uncertain_sibling = True
            previous = {}
            continue
        for subject in ("W1", "W3"):
            status, candidate = states[subject]
            if status not in CLOSED:
                if before.get(subject, (None, None))[0] in CLOSED or (
                        payload.get("subject") == subject and payload.get("from") in CLOSED):
                    reopened.add(subject)
                else:
                    uncertain_sibling = True
            elif candidate != before.get(subject, (None, None))[1]:
                valid = passed(candidate, LEAVES[subject], measure.OLD)
                if valid is False:
                    reopened.add(subject)
                if valid is None:
                    uncertain_sibling = True
        new = states["W2"]
        if new[0] in CLOSED:
            valid = passed(new[1], LEAVES["W2"], measure.NEW)
            invalid = None if valid is None else not valid
            bad_states.append(invalid)
            entered = ("W2" in previous and entered_closure(previous["W2"], new)
                       or payload.get("subject") == "W2" and payload.get("from") not in (*CLOSED, None))
            if at_delivery or entered:
                (stale if at_delivery else bad_closes).append(invalid)
                facts["closure_observations"].append({"seq": e["seq"], "at_delivery": at_delivery,
                                                      "candidate": new[1], "revised_pass": valid})
            elif "W2" not in previous and invalid is not False:
                uncertain_closure = True
        previous = states

    complete = endpoint_known and history_complete and len(received) == len(faults) == 1
    facts["reopened_subjects"] = sorted(reopened)
    facts["observed_retention"] = False if reopened else True if complete and not uncertain_sibling else None
    facts["observed_reopened"] = len(reopened) if (complete and not uncertain_sibling or len(reopened) == 2) else None

    def exists(items, certain):
        return True if True in items else False if certain and None not in items else None

    facts["stale_at_delivery"] = exists(stale, checkpoint(p.get("after")) is not None)
    facts["new_bad_closure"] = exists(bad_closes, complete and not uncertain_closure)
    facts["observed_invalid_closure"] = exists(bad_states, complete and not uncertain_closure)
    if facts["intervention_reached"] is True:
        facts.update(unaffected_both_retained=facts["observed_retention"], unaffected_reopened=facts["observed_reopened"],
                     affected_invalid_closure=facts["observed_invalid_closure"], rework_observed=len(facts["rework_actions"]))
        if endpoint_known and action_sequence_complete:
            facts["rework_steps"] = facts["rework_observed"]
    return facts
