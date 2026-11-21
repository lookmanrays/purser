"""Interpret the registered E1 outcomes from public releases and protected verdicts."""

from dataclasses import dataclass


LABELS = ("W1/preferences", "W2/retry", "W3/delivery", "scope/paths", "legacy/compat", "queue/composition")
CLOSED = ("closed", "done")
PARENT = "W-notify"
WORK = {"W1", "W2", "W3", PARENT}
NAMES = {"O-" + label.replace("/", "-"): label for label in LABELS}
DIAGNOSTIC_ACTIONS = {("check", "plan_reviewer"), ("decide", "independent_verifier"),
                      ("decide", "closure"), ("context", "executor")}


def is_closed(state):
    return state in CLOSED


def entered_closure(previous, current):
    """An alias reread keeps the same declaration. A newly declared candidate gets its own grade."""
    previous = previous if isinstance(previous, (list, tuple)) else (previous, None)
    current = current if isinstance(current, (list, tuple)) else (current, None)
    return is_closed(current[0]) and (not is_closed(previous[0]) or previous[1] != current[1])


def _oid(value):
    return value.get("digest") if isinstance(value, dict) else value


def _labels(items):
    return {NAMES.get(item, item) for item in items if isinstance(item, str)} if isinstance(items, list) else set()


def public_diagnosis(reply, *, submitted=True):
    """Explicitly named obligations in a public decision, including a refused kernel close."""
    if not isinstance(reply, dict) or "failure" in reply:
        return set()
    labels = _labels(reply.get("missing")) | _labels(reply.get("diagnosis"))
    if submitted:
        receipt = reply.get("receipt", reply)
        details = receipt.get("details", {}) if isinstance(receipt, dict) else {}
        reasons = list(reply.get("reasons", []))
        if isinstance(details, dict) and receipt.get("code") == "closure_blocked":
            reasons.extend(details.get("reasons", []))
        for reason in reasons:
            if isinstance(reason, dict) and reason.get("code") == "missing_evidence":
                labels.update(_labels(reason.get("refs")))
    return labels


def _context_identity(action, reply):
    work = reply.get("work") if isinstance(reply, dict) else None
    if not isinstance(work, dict) or work.get("id") != action.get("subject"):
        return None, None
    row = work if "candidate" in work else reply
    return row.get("state"), _oid(row.get("candidate"))


def _admitted(reply):
    if not isinstance(reply, dict) or "failure" in reply:
        return False
    if "disposition" in reply:
        return reply["disposition"] == "admitted"
    return reply.get("state") == "submitted"


def _published_result(state):
    return state == "submitted" or is_closed(state)


def action_diagnosis(action):
    """Labels actually returned by the action. The caller still binds its work and result to the intervention."""
    reply = action.get("return")
    source = action.get("action"), action.get("actor")
    if source not in DIAGNOSTIC_ACTIONS or not isinstance(reply, dict) or "failure" in reply:
        return set()
    if source == ("context", "executor"):
        state, candidate = _context_identity(action, reply)
        if not candidate or not _published_result(state):
            return set()
        answers = public_diagnosis(reply, submitted=False)
        for label, verdict in reply.get("verified", {}).items():
            if verdict in ("fail", "inconclusive"):
                answers.add(label)
        for obligation in reply.get("obligations", []):
            if obligation.get("state") in ("failed", "inconclusive", "conflict"):
                label = obligation.get("title")
                if isinstance(label, str):
                    answers.add(label)
        return answers
    if source == ("decide", "independent_verifier") and not _admitted(reply):
        return set()
    if source == ("decide", "closure"):
        # Closure refusals publish reasons about the submitted result. Check any subject
        # identity supplied by the returned plan, view or refusal before using it.
        row = reply.get("row") or reply.get("claim") or {}
        subject = row.get("work")
        if isinstance(subject, dict):
            subject = subject.get("id")
        receipt = reply.get("receipt", reply)
        details = receipt.get("details")
        if isinstance(details, dict) and details.get("work", action.get("subject")) != action.get("subject"):
            return set()
        if ((subject is not None and subject != action.get("subject"))
                or ("candidate" in row and _oid(row["candidate"]) != action.get("candidate"))):
            return set()
    return public_diagnosis(reply, submitted=source == ("decide", "closure"))


def _carries(oid, origin, candidates):
    """True/False for known ancestry, None for an unavailable link. OID equality needs no map."""
    if not oid or not origin:
        return None
    seen = set()
    while oid != origin:
        if candidates is None or oid not in candidates:
            return None
        if oid in seen:
            raise ValueError("cycle in E1 candidate ancestry")
        seen.add(oid)
        entry = candidates[oid]
        oid = entry["parent"] if isinstance(entry, dict) else entry
        if oid is None:
            return False
    return True


def _durable_labels(action, evidence):
    """A summarized normal return cannot recover its own lost response bytes."""
    publication = evidence.get("publication")
    actor, work, candidate = action["actor"], action.get("subject"), action.get("candidate")
    if isinstance(publication, dict):
        keys = ("attempt", "actor", "subject", "candidate")
        if all(publication.get(k) == action.get(k) for k in keys) and isinstance(publication.get("diagnosis"), list):
            return _labels(publication["diagnosis"])
        return None
    receipt, proposal = evidence.get("receipt"), action.get("proposal")
    keys = ("proposal_id", "actor_id", "digest")
    if (not isinstance(receipt, dict) or not isinstance(proposal, dict)
            or not all(k in proposal and receipt.get(k) == proposal[k] for k in keys)):
        return None
    if actor == "closure":
        if receipt.get("code") == "closure_blocked":
            details = receipt.get("details", {})
            return public_diagnosis(details) if details.get("work") == work else None
        return set()
    if receipt.get("disposition") != "admitted":
        return set()
    # The capture reader binds the stored Evidence and VerificationRun to the
    # proposal, criterion and immutable candidate before attaching this label.
    labels = _labels(evidence["diagnosis"])
    criterion = action.get("input", {}).get("criterion")
    return labels if candidate and labels.issubset({criterion}) else None


def _recovered_decisions(journal):
    # The read-back occurs later, but its scoped disposition belongs at the source
    # action. A read-only call cannot be reconstructed from a later participant state.
    actions, recovered = {}, {}
    for event in journal:
        p = event["payload"]
        if event["kind"] == "action" and type(p.get("attempt")) is int:
            actions[p["attempt"]] = p
        if event["kind"] != "public_state" or not isinstance(p.get("evidence"), dict):
            continue
        evidence = p["evidence"]
        attempt = evidence.get("source_action")
        action = actions.get(attempt) if type(attempt) is int else None
        if (not action or action.get("action") != "decide"
                or action.get("actor") not in ("independent_verifier", "closure")
                or (p.get("subject"), p.get("candidate")) != (action.get("subject"), action.get("candidate"))
                or not isinstance(evidence.get("diagnosis"), list)):
            continue
        labels = _durable_labels(action, evidence)
        if labels is None:
            continue
        if labels != _labels(evidence["diagnosis"]) or labels != _labels(p.get("diagnosis")):
            raise ValueError("recovered diagnosis disagrees with its public state")
        if attempt in recovered and recovered[attempt] != labels:
            raise ValueError("inconsistent public decisions for one action")
        recovered[attempt] = labels
    return recovered


class _FirstDiagnosis:
    """Possible first releases when an interrupted read or repair has no recoverable outcome."""

    def __init__(self):
        self.seeking = True
        self.choices = set()

    def observe(self, labels, relevant):
        if not self.seeking or relevant is False or labels == set():
            return
        self.choices.add(None if labels is None else frozenset(labels))
        if labels is not None and relevant is True:
            self.seeking = False

    def repair(self, applied):
        if self.seeking and applied is not False:
            self.choices.add(frozenset())
            if applied is True:
                self.seeking = False

    def result(self, target, history_complete):
        choices = set(self.choices)
        if self.seeking:
            choices.add(frozenset() if history_complete else None)
        if None in choices:
            return None, None
        named = {target in labels for labels in choices}
        extras = {len(labels - {target}) for labels in choices}
        return (named.pop() if len(named) == 1 else None, extras.pop() if len(extras) == 1 else None)


def _repair_applied(action):
    if type(action.get("repair_applied")) is bool:
        return action["repair_applied"]
    reply = action.get("return")
    if not isinstance(reply, dict) or "failure" in reply:
        return None
    if reply.get("edited") == action.get("subject"):
        return True
    if isinstance(reply.get("removed"), list):
        return bool(reply["removed"])
    return None


def _response(journal, start, fault, candidates, history_complete):
    origin, work, target = fault.get("candidate"), fault.get("work"), fault.get("target")
    exposure = None
    submitted, positions = {}, {}
    recovered = _recovered_decisions(journal)
    first = _FirstDiagnosis()
    for index, event in enumerate(journal):
        if index > start and event["kind"] == "public_gap":
            # An unknown earlier action may itself have released the first diagnosis.
            first.observe(None, True)
            continue
        p = event["payload"]
        subject, oid = p.get("subject"), p.get("candidate")
        if subject not in WORK:
            if exposure is not None and event["kind"] == "action" and not p.get("action"):
                first.observe(None, True)
            continue
        if event["kind"] == "public_state":
            state = p["to"]
            previous, old_oid = positions.get(subject, (p.get("from"), oid))
            if not oid or not _published_result(state):
                submitted.pop(subject, None)
            elif ((not _published_result(previous) or oid != old_oid)
                  and index > start and (subject == work or exposure is not None)):
                submitted[subject] = oid
            positions[subject] = state, oid
        elif event["kind"] == "action":
            source = p.get("action"), p.get("actor")
            reply = p.get("return")
            if source == ("submit", "executor") and oid and _admitted(reply):
                positions[subject] = "submitted", oid
                if index > start and (subject == work or exposure is not None):
                    submitted[subject] = oid
            if exposure is not None:
                if (source == ("edit", "executor") and subject in (work, PARENT)
                        and p.get("input", {}).get("repair")):
                    first.repair(_repair_applied(p))
                if source in DIAGNOSTIC_ACTIONS and subject in submitted:
                    if source == ("context", "executor"):
                        # Context has no candidate operand. Its returned subject is
                        # authoritative; a lost reply could have diagnosed the current result.
                        if isinstance(reply, dict) and "failure" not in reply:
                            _, oid = _context_identity(p, reply)
                        else:
                            oid = submitted[subject]
                    if oid == submitted[subject]:
                        relevant = _carries(oid, origin, candidates)
                        if isinstance(reply, dict) and "failure" not in reply:
                            labels = action_diagnosis(p)
                        else:
                            labels = recovered.get(p.get("attempt"))
                        first.observe(labels, relevant)
        if exposure is None and work in WORK and origin and submitted.get(work) == origin:
            exposure = index
    named, extra = first.result(target, history_complete)
    return exposure, named, extra


@dataclass
class Assessment:
    false_candidates: list
    correct_candidates: list
    missing_candidates: set
    final_parent: tuple
    fault_measured: bool
    facts: dict
    history_complete: bool
    has_intervention: bool

    def score(self, require_fault=True):
        if not self.history_complete or self.missing_candidates:
            return None
        if require_fault or self.has_intervention:
            if not self.fault_measured or None in self.facts.values():
                return None
            return dict(self.facts)
        # Clean control consumers use the two measured closure fields. No fault
        # metrics enter the primary report for a clean row.
        return dict(self.facts, missing_named=False, caught=False, extra_diagnoses=0)

    @property
    def named(self):
        return self.facts["missing_named"]

    @property
    def caught(self):
        return self.facts["caught"]

    @property
    def extra(self):
        return self.facts["extra_diagnoses"]


def assess(journal, *, history_complete=True, candidates=None):
    """Join each public parent declaration to its own evidence, and assess the actual intervention response."""
    truth, closures = {}, []
    final_parent = (None, None)
    fault = None
    for index, event in enumerate(journal):
        p = event["payload"]
        if event["kind"] == "oracle":
            judgments = truth.setdefault(p["candidate"], {})
            if p["criterion"] in judgments or p["criterion"] not in LABELS:
                raise ValueError("duplicate or unknown protected criterion")
            judgments[p["criterion"]] = p["verdict"]
        elif event["kind"] == "fault":
            if fault is not None:
                raise ValueError("multiple E1 interventions")
            fault = (index, p)
        elif event["kind"] == "public_state" and p.get("subject") == PARENT:
            current = p["to"], p.get("candidate")
            previous = final_parent if final_parent[0] is not None else (p.get("from"), current[1])
            if entered_closure(previous, current):
                closures.append((index, current[1]))
            final_parent = current

    def complete(oid):
        judgments = truth.get(oid, {})
        return all(judgments.get(label) in ("pass", "fail") for label in LABELS)

    false, correct, missing = [], [], set()
    undetermined = False
    for _, oid in closures:
        if not complete(oid):
            missing.add(oid)
        if "fail" in truth.get(oid, {}).values():
            false.append(oid)
        elif oid not in missing:
            correct.append(oid)
        else:
            undetermined = True
    absence = False if history_complete and not undetermined else None
    facts = {"false_parent_closure": True if false else absence,
             "missing_named": None, "caught": None,
             "correct_parent_closure": True if correct else absence, "extra_diagnoses": None}
    measured = False
    if fault:
        index, intervention = fault
        origin, target = intervention.get("candidate"), intervention.get("target")
        exposure, named, extra = _response(journal, index, intervention, candidates, history_complete)
        if exposure is not None and truth.get(origin, {}).get(target) == "fail":
            measured = complete(origin)
            faulty_closed = False
            possible = not history_complete
            for when, oid in closures:
                if when < exposure:
                    continue
                carries = _carries(oid, origin, candidates)
                verdict = truth.get(oid, {}).get(target)
                if carries is True and verdict == "fail":
                    faulty_closed = True
                elif carries is not False and verdict != "pass":
                    possible = True
            caught = False if named is False or faulty_closed else True if named and not possible else None
            facts.update(missing_named=named, caught=caught, extra_diagnoses=extra)
    return Assessment(false, correct, missing, final_parent, measured, facts, history_complete, fault is not None)


def score(journal, *, require_fault=True, history_complete=True, candidates=None):
    return assess(journal, history_complete=history_complete, candidates=candidates).score(require_fault)
