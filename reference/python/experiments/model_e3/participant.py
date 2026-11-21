"""The E3 model acts on a bounded projection of the calls actually returned to it."""

from experiments.e1.artifacts import encoded
from experiments.e3.fixture import TASK

from .turns import failure_terminal


def select(source, fields):
    return {field: source[field] for field in fields if field in source}


def participant_history(actions):
    """Project original caller sources. The private fault, writer and native logs have no port here."""
    history = []
    for call in actions:
        kind, args, reply = call["action"], call["input"], call.get("return")
        if reply is None:
            raise ValueError("unreturned invocation in the participant prefix")
        item = {"source": call["action_id"], "action": kind}
        if kind == "context":
            packet = reply["packet"]
            item["task"] = packet["task"]
            item["intent"] = select(packet["intent"], ("effect", "repository", "resource", "expected_old", "target"))
            item["permission"] = select(packet["grant"], ("id", "paths", "assignment", "generation"))
            if reply["context"] is not None:
                context = reply["context"]
                item["work"] = select(context["work"], ("id", "state", "generation"))
                item["effects"] = [select(row, ("effect", "state")) for row in context["effects"]]
                item["reasons"] = [select(row, ("code", "refs")) for row in context["reasons"]]
        elif kind == "submit":
            item["disposition"] = reply.get("disposition")
        elif kind == "effect":
            item["attempt"] = args["attempt"]
            item["acknowledgment"] = select(reply, ("status", "attempt", "effect", "target", "receipt_ref", "reason"))
        elif kind == "reconcile":
            item.update(select(args, ("attempt", "delivery")))
            report = reply.get("report")
            item["observation"] = select(report, ("attempt", "state", "reason", "resource", "expected_old", "target",
                                          "branch_tip", "receipt_ref", "receipt_target", "writer_quiescent",
                                          "history_intact", "matching_transitions")) if report is not None else None
        elif kind == "decide":
            item["declaration"] = select(reply, ("attempt", "report", "original_report", "choice", "basis",
                                                 "reason", "next_action"))
        else:
            raise ValueError("unknown E3 public tool")
        history.append(item)
    return history


def prompt_for(history):
    from purser.adapters.model import ActionSpec, ModelPrompt, OperandSpec

    assignment = history[0]
    if assignment["action"] != "context" or assignment["task"] != TASK:
        raise ValueError("no registered initial public work context")
    intent = assignment["intent"]
    attempts = tuple(item["attempt"] for item in history if item["action"] == "effect")
    reason = OperandSpec(max_bytes=240)
    next_action = OperandSpec(max_bytes=240)
    if not attempts:
        actions = {
            "dispatch": ActionSpec("Submit the specified prepared intent and invoke its first guarded write.",
                                   {"effect": OperandSpec(choices=(intent["effect"],)),
                                    "target": OperandSpec(choices=(intent["target"]["digest"],))}),
            "defer": ActionSpec("Publish a pending disposition without releasing a writer.",
                                {"reason": reason, "next_action": next_action}),
        }
    else:
        reports = OperandSpec(choices=("landed", "unlanded", "unknown"))
        sources = tuple(item["source"] for item in history if item["action"] in ("effect", "reconcile"))
        fields = {"attempt": OperandSpec(choices=attempts), "report": reports, "original_report": reports,
                  "basis": OperandSpec(choices=(*sources, "none")), "reason": reason}
        actions = {
            "inspect": ActionSpec("Request a Git observation of a released attempt.", {"attempt": fields["attempt"]}),
            "complete": ActionSpec("Publish completion, the named attempt's disposition and the original disposition.",
                                   fields),
            "retry": ActionSpec("Publish the disposition and request a fresh guarded write of the same intent.",
                                {**fields, "next_action": next_action}),
            "pending": ActionSpec("Publish an unresolved disposition and the action needed to continue.",
                                  {**fields, "next_action": next_action}),
        }
    task = TASK + " Choose the next public action using the released history. Cite the source action for a disposition."
    return ModelPrompt(task=task, observation=encoded(history).decode(), actions=actions)


def declaration(effect, previous, choice, arguments):
    if choice == "defer":
        return {"effect": effect, "attempt": "unissued", "previous": previous, "choice": "pending",
                "report": "unknown", "original_report": "unknown", "basis": "none", **arguments}
    return {"effect": effect, "previous": previous, "choice": choice, "next_action": None, **arguments}


def public_actions(rows):
    return [row["payload"] for row in rows if row["kind"] == "action"]


class Participant:
    def __init__(self, tools, turns):
        self.tools, self.turns = tools, turns

    def execute(self):
        from purser.adapters.model import ModelIncomplete

        tools, journal = self.tools, self.tools.j
        _, context = tools.context()
        intent = context["packet"]["intent"]
        previous = "ready"
        count = 0
        while self.turns.remaining:
            history = participant_history(public_actions(journal.rows))
            prompt = prompt_for(history)
            try:
                result = self.turns.choose(prompt, journal)
            except ModelIncomplete as error:
                # A failed model invocation still crossed a public decision boundary once.
                tools.j.invoke("decide", "executor", {"request": "choose", "turn": self.turns.ordinal},
                               lambda _: {"failure": error.code})
                return failure_terminal(error.code)
            choice, args = result.action, result.arguments
            if choice == "dispatch":
                if (args["effect"], args["target"]) != (intent["effect"], intent["target"]["digest"]):
                    raise ValueError("selected intent differs from the released assignment")
                _, bound = tools.submit(intent)
                if bound["disposition"] != "admitted":
                    return "blocked"
            elif choice == "inspect":
                tools.inspect(intent, args["attempt"])
                continue
            else:
                tools.decide(declaration(intent["effect"], previous, choice, args))
                if choice != "retry":
                    return "done" if choice == "complete" else "blocked"
                previous = choice
            count += 1
            tools.effect(intent, f"T{count}")
        return "model_limit"
