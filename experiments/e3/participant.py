"""Common scripted executor. Its inputs are the public tool results and the assigned observation delivery mode."""

from itertools import count


def disposition(ack, observation, intent, attempt):
    if observation is not None:
        report = observation["report"]
        if report is None:
            return "unknown", "observation_refused"
        if (report["attempt"] != attempt or report["target"] != intent["target"]["digest"]
                or report["resource"] != intent["resource"] or report["expected_old"] != intent["expected_old"]["digest"]):
            return "unknown", "observation_identity_mismatch"
        states = {"confirmed_happened": "landed", "confirmed_absent": "unlanded", "pending": "unknown"}
        return states[report["state"]], report["reason"]
    if (ack["status"] == "acknowledged" and ack["effect"] == intent["effect"] and ack["attempt"] == attempt
            and ack["target"] == intent["target"]["digest"]):
        return "landed", "guarded_write_acknowledged"
    return "unknown", "dispatch_unconfirmed"


class Participant:
    def __init__(self, tools):
        self.tools = tools

    def execute(self):
        _, task = self.tools.context()
        intent = task["packet"]["intent"]
        _, submission = self.tools.submit(intent)
        if submission["disposition"] != "admitted":
            return "blocked"

        previous, original = "ready", None
        for number in count(1):
            attempt = f"T{number}"
            basis, ack, observation = self.tools.effect(intent, attempt)
            if observation is None and ack["status"] == "timeout":
                basis, observation = self.tools.inspect(intent, attempt)
            report, reason = disposition(ack, observation, intent, attempt)
            if original is None:
                original = report
            choice = {"landed": "complete", "unlanded": "retry", "unknown": "pending"}[report]
            next_action = {"complete": None, "retry": "authorize a new guarded attempt",
                           "pending": "reconcile this attempt when the writer and its receipt can be observed"}[choice]
            self.tools.decide({"effect": intent["effect"], "attempt": attempt, "report": report,
                               "original_report": original, "reason": reason, "basis": basis,
                               "previous": previous, "choice": choice, "next_action": next_action})
            if choice != "retry":
                return "done" if choice == "complete" else "blocked"
            previous = choice
