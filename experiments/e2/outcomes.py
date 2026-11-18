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


def assess(journal, track, checkpoint, verdicts, *, delivery, qualification, unchanged, final_oid,
           endpoint_known, history_complete, action_sequence_complete):
    def passed(oid, label, version):
        actual_version = version if label in measure.REVISED else measure.OLD
        verdict = verdicts.get((oid, label, actual_version))
        return None if verdict is None else verdict == "pass"

    phase = measure.OLD if track == "clean" else measure.NEW
    final_pass = conjunction(passed(final_oid, label, phase) for label in LABELS) if final_oid else None
    facts = dict.fromkeys(FIELDS)
    facts.update(final_pass=final_pass, final_candidate=final_oid, rework_observed=None,
                 stale_at_delivery=None, new_bad_closure=None, closure_observations=[], reopened_subjects=[],
                 rework_actions=[], checkpoint_candidate=None, checkpoint_work=None)
    faults = [e for e in journal if e["kind"] == "fault"]
    if track == "clean":
        return facts
    if not faults:
        facts["intervention_reached"] = False if delivery is False or endpoint_known and history_complete else None
        return facts
    fault = faults[0]
    p = fault["payload"]
    before = checkpoint(p.get("before")) or {}
    oid = p.get("candidate")
    facts.update(checkpoint_candidate=oid, checkpoint_work=before)
    if len(faults) != 1 or delivery is not True:
        if len(faults) == 1 and delivery is False:
            facts["intervention_reached"] = False
        return facts
    submitted = any(e["kind"] == "action" and e["payload"].get("action") == "submit"
                    and e["payload"].get("subject") == "W2" and e["payload"].get("candidate") == oid
                    and (checkpoint(e["payload"].get("after")) or {}).get("W2", (None, None))[1] == oid
                    for e in journal if e.get("seq", 0) < fault["seq"])
    submission = True if submitted else False if history_complete else None
    reached = checkpoint_eligibility(before, oid, track, lambda item,label: passed(item,label,measure.OLD),
                                      unchanged, submission)
    reached = conjunction([reached, qualification])
    facts["intervention_reached"] = reached

    previous = before
    reopened = set()
    uncertain_sibling = uncertain_closure = False
    stales, bad_closes = [], []
    for e in journal:
        if e.get("seq", 0) < fault["seq"]:
            continue
        payload = e["payload"]
        if e["kind"] == "action" and payload.get("action") in REWORK and payload.get("subject") in WORK:
            facts["rework_actions"].append(payload["attempt"])
        # Nested calls can return to an older outer checkpoint. Only a physical
        # publication or the incoming delivery advances visible participant state.
        if e["kind"] not in ("fault", "public_state"):
            continue
        ck = payload.get("checkpoint") if e["kind"] == "public_state" else payload.get("after")
        states = checkpoint(ck)
        if states is None:
            uncertain_closure = uncertain_sibling = True
            continue
        for subject in ("W1", "W3"):
            status, candidate = states[subject]
            if status not in CLOSED:
                reopened.add(subject)
            elif candidate != before.get(subject, (None, None))[1]:
                valid = passed(candidate, LEAVES[subject], measure.OLD)
                if valid is False:
                    reopened.add(subject)
                if valid is None:
                    uncertain_sibling = True
        new = states["W2"]
        at_delivery = e is fault
        if new[0] in CLOSED and (at_delivery or entered_closure(previous.get("W2"), new)):
            valid = passed(new[1], LEAVES["W2"], measure.NEW)
            (stales if at_delivery else bad_closes).append(None if valid is None else not valid)
            facts["closure_observations"].append({"seq": e.get("seq"), "at_delivery": at_delivery,
                                                  "candidate": new[1], "revised_pass": valid})
        previous = states
    complete = endpoint_known and history_complete
    facts["reopened_subjects"] = sorted(reopened)
    facts["unaffected_both_retained"] = False if reopened else True if complete and not uncertain_sibling else None
    facts["unaffected_reopened"] = len(reopened) if (complete and not uncertain_sibling or len(reopened) == 2) else None
    def exists(items, certain):
        return True if True in items else False if certain and None not in items else None
    delivery_after = checkpoint(p.get("after")) is not None
    facts["stale_at_delivery"] = exists(stales, delivery_after)
    facts["new_bad_closure"] = exists(bad_closes, complete and not uncertain_closure)
    facts["affected_invalid_closure"] = exists(stales + bad_closes, complete and not uncertain_closure)
    if reached is not True:
        facts.update(unaffected_both_retained=None, unaffected_reopened=None, rework_observed=None)
        return facts
    facts["rework_observed"] = len(facts["rework_actions"])
    if endpoint_known and action_sequence_complete:
        facts["rework_steps"] = facts["rework_observed"]
    return facts
