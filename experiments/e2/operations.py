"""Public E2 operations and their scoped kernel counterparts, frozen with the participant."""
from experiments.e1.accounting import ACTIONS as E1_ACTIONS
from experiments.e1.purser_arm import EXECUTOR, OBSERVER, VERIFIER

ACTIONS = {**E1_ACTIONS, "context": ("executor", "planner")}

# Public kind, actor, decisive input, kernel operation, kernel identity and committed event.
PORTS = {
    "assign_again": ("plan", "planner", "assign", "work.assign", "planner:study", "WorkAssigned"),
    "submit": ("submit", "executor", "candidate", "result.submit", EXECUTOR, "ResultSubmitted"),
    "opening": ("plan", "planner", "open_run", "run.open", "planner:study", "RunOpened"),
    "observe": ("observe", "independent_observer", "criterion", "observation.record", OBSERVER, "ObservationRecorded"),
    "verify": ("decide", "independent_verifier", "criterion", "evidence.record", VERIFIER, "EvidenceRecorded"),
    "decide": ("decide", "closure", "request", "work.close", "closure:study", "WorkClosed"),
    "revise": ("plan", "planner", "adopt", "decision.record", "planner:study", "DecisionRecorded"),
    "receive": ("fault", "fixture_observer", None, "assumption.observe", OBSERVER, "PremiseObserved"),
}


def method_for(kind, actor, inputs):
    if actor not in ACTIONS.get(kind, ()):
        return "receive" if (kind, actor) == ("fault", "fixture_observer") else None
    if kind == "context":
        return "inspect" if "tool" in inputs else "context"
    if kind == "edit":
        return "edit"
    if kind == "check":
        return "review" if actor == "plan_reviewer" else "worker_check"
    if kind == "plan" and "schedule" in inputs:
        return "schedule"
    for name, (registered, role, operand, *_) in PORTS.items():
        if (kind, actor) == (registered, role) and operand in inputs:
            return name
    return None
