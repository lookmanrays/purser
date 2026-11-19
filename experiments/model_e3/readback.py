"""Associate the sealed provider response with its actual public effects on a cold read."""

from experiments.e1.artifacts import MANIFEST, encoded, sha
from experiments.e1.report import require

from .participant import declaration, participant_history, prompt_for
from .turns import LIMIT, TRACE_KIND, USAGE, check_record, failure_terminal


def original_actions(objects, evidence):
    calls = []
    for pair in evidence.get("actions", []):
        call = dict(objects.item(pair["request"]))
        require(call["action_id"] == f"action-{len(calls) + 1}", "missing original public caller release")
        response = objects.item(pair["response"]) if pair.get("response") else None
        if response:
            require(response["action_id"] == call["action_id"], "original public reply belongs to another action")
            call.update(response)
        calls.append(call)
    return calls


class Application:
    """Read only the admitted public request/reply stream, independently of the participant's loop."""

    def __init__(self, actions, arm):
        self.actions, self.arm = actions, arm
        self.position, self.releases, self.previous = 0, 0, "ready"
        self.intent = self.terminal = None

    def take(self, kind, actor, operands):
        if self.position == len(self.actions):
            raise EOFError("public action not reached")
        call = self.actions[self.position]
        require((call["action"], call["actor"]) == (kind, actor) and encoded(call["input"]) == encoded(operands),
                "model choice differs from the public action or its actual operands")
        self.position += 1
        if "return" not in call:
            raise EOFError("public action was interrupted")
        return call["return"]

    def apply(self, choice):
        action, args = choice["action"], choice["arguments"]
        effect = self.intent["effect"]
        if action == "inspect":
            self.take("reconcile", "executor", {"effect": effect, "attempt": args["attempt"], "delivery": "explicit"})
            return
        if action == "dispatch":
            require((args["effect"], args["target"]) == (effect, self.intent["target"]["digest"]),
                    "model selected a different Git subject")
            if self.take("submit", "executor", {"intent": self.intent}).get("disposition") != "admitted":
                self.terminal = "blocked"
                return
        else:
            expected = declaration(effect, self.previous, action, args)
            actual = self.take("decide", "executor", expected)
            require(encoded({k: actual[k] for k in expected}) == encoded(expected),
                    "public declaration differs from the returned decision")
            if action != "retry":
                self.terminal = "done" if action == "complete" else "blocked"
                return
            self.previous = action
        self.releases += 1
        attempt = f"T{self.releases}"
        self.take("effect", "broker", {"effect": effect, "attempt": attempt})
        if self.arm != "loop":
            self.take("reconcile", "independent_observer", {"effect": effect, "attempt": attempt, "delivery": "automatic"})


def read_trace(identity, trace, objects, platform, *, service_sha256=None, authority_sha256=None, interrupted=False):
    require(all(trace.get(k) == value for k, value in identity.items()) and trace.get("kind") == TRACE_KIND,
            "model trace belongs to another population, registration or case")
    entries = trace["turns"]
    require(type(entries) is list and len(entries) <= LIMIT, "model turn limit exceeded")
    public = original_actions(objects, platform.get("e3", {}))
    plan = Application(public, identity["unit"][3])
    complete = True
    if public:
        try:
            reply = plan.take("context", "executor", {"assignment": "AS-notify"})
            plan.intent = reply["packet"]["intent"]
        except EOFError:
            if not interrupted:
                raise ValueError("public context release was interrupted")
            complete = False
    require(not entries or plan.intent is not None, "model action before the task was released")
    last_model_ns = None
    records, decisions, costs = [], [], []
    for ordinal, entry in enumerate(entries, 1):
        require(type(entry.get("ordinal")) is int and entry["ordinal"] == ordinal
                and plan.terminal is None and type(entry.get("before_actions")) is int and entry["before_actions"] == plan.position,
                "model prompt prefix differs from the public action sequence")
        before = plan.position
        require(type(entry["issued_ns"]) is int and public[before - 1]["finished_monotonic_ns"] <= entry["issued_ns"],
                "model turn overlapped an undelivered public action")
        last_model_ns = entry["issued_ns"]
        prompt = prompt_for(participant_history(public[:before]))
        result_ref = entry.get("result") or entry.get("recovered_result")
        outcome = entry.get("outcome")
        record = None
        if result_ref:
            record = check_record(objects.blob(result_ref), result_ref["sha256"], identity, ordinal, prompt)
            require(entry["issued_ns"] <= record["started_monotonic_ns"]
                    and (outcome is None or (outcome == record["outcome"]
                         and entry.get("request_sha256") == record["request_sha256"]
                         and record["returned_monotonic_ns"] <= entry["returned_ns"])),
                    "sealed provider record disagrees with its original caller")
            require((service_sha256 is None or service_sha256 == sha(encoded(record["service"])))
                    and (authority_sha256 is None or authority_sha256 == record["authority_sha256"]),
                    "service or spending authority differs from registration")
            records.append(record)
            last_model_ns = record["returned_monotonic_ns"]
            costs.append({"model_calls": 1, **record["usage"]})
            if entry.get("recovered_result") and outcome is None:
                complete = False
                continue
        elif outcome in ("disabled", "budget_refused", "deadline"):
            costs.append(dict.fromkeys(("model_calls", *USAGE), 0))
        else:
            # The coordinator may die between reservation, I/O and the caller's sealed return.
            costs.append({})
            complete = False
        if outcome is None:
            require(interrupted and ordinal == len(entries), "unfinished model release in a completed case")
            complete = False
            break
        returned = entry["returned_ns"]
        require(type(returned) is int and entry["issued_ns"] <= returned
                and (before == len(public) or returned <= public[before]["issued_monotonic_ns"]),
                "public effect predates its model response")
        last_model_ns = returned
        try:
            if outcome == "decision":
                require(record is not None, "public choice lacks original model evidence")
                plan.apply(record["proposal"])
                decisions.append({"ordinal": ordinal, "action": record["proposal"]["action"],
                                  "source": result_ref, "first_public_action": before + 1,
                                  "last_public_action": plan.position})
            else:
                failure = plan.take("decide", "executor", {"request": "choose", "turn": ordinal})
                require(failure == {"failure": outcome} and ordinal == len(entries),
                        "unusable model reply is not reflected in the public decision")
                plan.terminal = failure_terminal(outcome)
        except EOFError:
            if not interrupted or ordinal != len(entries):
                raise ValueError("model decision lacks its corresponding public action")
            complete = False
    require(plan.position == len(public), "public work is unaccounted for by original model decisions")
    if not interrupted:
        expected_exit = "model_limit" if plan.terminal is None and len(entries) == LIMIT else plan.terminal
        require(expected_exit is not None and expected_exit == platform["participant_exit"]["reason"],
                "participant exit disagrees with the original model choices")
    if records:
        conditions = {(sha(encoded(r["service"])), r["authority_sha256"]) for r in records}
        require(len(conditions) == 1, "model service or authority changed within a case")
    limits = MANIFEST["budgets"]["model"]
    retained_use = {"input_tokens": 0, "output_tokens": 0}
    for record in records:
        for field, retained in retained_use.items():
            reserved = record["limits"][field]
            require(retained + reserved <= limits["max_" + field], "model release exceeded the remaining token budget")
            actual = record["usage"][field]
            retained_use[field] += reserved if actual is None else actual
    exact, lower_bounds = {}, {}
    for field in ("model_calls", *USAGE):
        values = [cost.get(field) for cost in costs]
        known = [v for v in values if v is not None]
        if len(known) == len(values):
            exact[field] = sum(known)
        elif known:
            lower_bounds[field] = sum(known)
    exceeded = (exact.get("input_tokens", 0) > limits["max_input_tokens"]
                or exact.get("output_tokens", 0) > limits["max_output_tokens"])
    require(not exceeded or (entries and entries[-1].get("outcome") == "usage_overrun"),
            "model case exceeded its registered token budget")
    first = next((r for r in records if r["transport"]["wire_state"] == "sent"), None)
    exposure = {"ordinal": first["turn"]["ordinal"], "request_sha256": first["request_sha256"],
                "send_interval_ns": [first["started_monotonic_ns"], first["returned_monotonic_ns"]],
                "http_status": first["transport"]["http_status"]} if first else None
    return {"model_trace_complete": complete, "model_decisions": decisions, "model_receipts": len(records),
            "first_model_exposure": exposure,
            "last_model_ns": last_model_ns, "cost_exact": exact, "cost_lower_bounds": lower_bounds}
