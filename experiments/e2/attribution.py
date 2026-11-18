"""Bind a public invocation to its exact participant operation and return."""

from experiments.e1.accounting import require
from experiments.e1.artifacts import encoded, sha
from experiments.e1.executor import PARENT
from experiments.e1.measurement import same
from experiments.e1.purser_arm import EXECUTOR, IDS, OBSERVER, VERIFIER
from purser.kernel.wire import digest

from . import policy
from .public_sources import PLAN_VERSION


# The public roles separate authority to run, observe, grade and close the same candidate.
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
PLAN_WRITES = {"assign_again", "submit", "verify", "decide", "revise", "receive"}


def expected(action):
    for method, (kind, actor, operand, *_) in PORTS.items():
        if (action.get("action", "fault"), action.get("actor")) == (kind, actor):
            if operand is None or operand in action.get("input", {}):
                return method
    return None


def request_key(request):
    body, actor = request["body"], request["actor_id"]
    key = actor, body["proposal_id"], digest("proposal", {"actor_id": actor, "proposal": body})
    require(key == (actor, request["proposal_id"], request["digest"]), "issued request digest or id differs")
    require(body["v"] == 1 and body["commitment"] == "C-notify", "request outside participant commitment")
    return key


def receipt_key(receipt):
    return receipt["actor_id"], receipt["proposal_id"], receipt["digest"]


def operation(action, method, request, before, pub):
    """Check the actual bound request against the declared port, including rejected proposals."""
    body = request["body"]
    op, = body["ops"]
    _, _, _, opcode, actor, event_kind = PORTS[method]
    require(op["op"] == opcode and request["actor_id"] == actor, "wrong operation or split-authority actor")
    work, oid = action["subject"], action["candidate"]
    inputs = action.get("port_input", action.get("input", {}))
    records = pub.records
    if method == "assign_again":
        require(op["work"] == work == inputs["assign"] and op["executor"] == EXECUTOR, "wrong assigned work")
    elif method == "submit":
        require(records[op["assignment"]]["work"] == work and
                same(op["candidate"], {"kind": "git.commit", "algorithm": "sha1", "digest": oid}) and
                inputs["candidate"] == oid, "wrong submitted assignment or candidate")
    elif method == "decide":
        require(op["id"] == work and before.states[work][1] == oid and inputs["request"] == "close",
                "wrong closure subject, candidate or request")
    elif method in ("opening", "observe", "verify"):
        label = inputs["open_run" if method == "opening" else "criterion"]
        record = records[op["result"] if method == "opening" else op["run"]]
        obligation = op["obligation"] if method == "opening" else record["obligation"]
        require(record["work"] == work and record["candidate"]["digest"] == oid and obligation == IDS[label],
                "wrong criterion, work or candidate")
        if method == "verify":
            observation = records[op["observation"]]
            require(observation.get("run") == op["run"], "verifier used a different observation")
            graded = inputs.get("observation", {})
            require(op["verdict"] == graded.get("verdict", action["input"].get("verdict")),
                    "verifier verdict differs from candidate input")
    elif method == "revise":
        choice = inputs.get("notice", {}).get("retry_schedule", policy.EXPONENTIAL)
        require(work == "W2" and inputs["adopt"] == policy.OBSERVATION and
                same(op, policy.decision(policy.REPLACEMENT, choice, [], policy.ORIGINAL)),
                "wrong adopted decision or affected work")
    else:
        msg = action["released"]
        require(work == "W2" and same(op, {"op": opcode, "id": msg["id"], "assumption": msg["assumption"],
                                           "finding": msg["finding"]}), "wrong observed premise")
    if pub.receipt["disposition"] == "admitted":
        events = [e for e in pub.events if e["type"] == event_kind and e["event_version"] == 1]
        # Closing an already current certificate legitimately admits only its receipt.
        if method == "decide" and before.states[work][0] in ("closed", "succeeded") and not events:
            return
        require(len(events) == 1 and events[0]["data"]["id"] == op["id"], "missing matching committed effect")
        if method == "observe" and "observation" in inputs:
            require(events[0]["data"]["artifact_sha256"] == sha(encoded(inputs["observation"])),
                    "observed bytes differ from the retained invocation")
        if method == "receive":
            require(events[0]["data"].get("artifact_sha256") == sha(encoded(action["released"])),
                    "admitted premise differs from the released bytes")


def response_receipt(method, reply, *, outer):
    if method == "decide":
        return reply["receipt"]
    if method == "observe" and outer and "response" in reply:
        return reply["response"]
    if method == "verify" and outer and "diagnosis" in reply:
        return {k: v for k, v in reply.items() if k != "diagnosis"}
    return reply


def receipt_response(action, method, receipt):
    for field in ("port_return", "return"):
        if field in action and "failure" not in action[field]:
            actual = response_receipt(method, action[field], outer=field == "return")
            require(same(actual, receipt), f"{field} receipt differs from the original bound proposal")


def plan_row(row):
    return {k: v for k, v in row.items() if k not in (PLAN_VERSION, "_publication")}


def plan_operation(action, method, before, pub):
    work = action["subject"]
    require(method in PLAN_WRITES, "read-only invocation claimed a plan write")
    row = pub.rows[work]
    previous = plan_row(before.rows[work])
    actual = plan_row(row)
    marker = row["_publication"]
    require(pub.attempt == action["attempt"] and
            all(marker[k] == v for k, v in {"attempt": action["attempt"], "actor": action["actor"],
                "subject": work, "candidate": row["candidate"]}.items()), "wrong plan publication actor or subject")
    for other, fields in before.rows.items():
        if other != work:
            require(same({k: v for k, v in fields.items() if k != PLAN_VERSION},
                         {k: v for k, v in pub.rows[other].items() if k != PLAN_VERSION}),
                    "plan response concealed a different subject's publication")
    inputs = action.get("port_input", action.get("input", {}))
    expected = dict(previous)
    diagnosis = []
    if method == "receive":
        require(work == "W2", "wrong premise recipient")
        expected["premise"] = action["released"]
    elif method == "revise":
        message = previous["premise"]
        require(work == "W2" and inputs["adopt"] == message["id"] and previous["criterion"] == message["criterion"],
                "wrong plan decision or affected criterion")
        if "notice" in inputs:
            require(same(inputs["notice"], message), "revised plan operand differs from received notice")
        expected["retry_choice"] = message["retry_schedule"]
    elif method == "assign_again":
        require(inputs["assign"] == work, "wrong assigned plan work")
        expected.update(state="open", candidate=None, verified={})
    elif method == "submit":
        require(inputs["candidate"] == action["candidate"], "wrong submitted plan candidate")
        expected.update(state="submitted", candidate=action["candidate"], verified={})
    elif method == "verify":
        require(previous["candidate"] == action["candidate"], "wrong verified plan candidate")
        label = inputs["criterion"]
        verdict = inputs.get("observation", {}).get("verdict", action["input"].get("verdict"))
        require(verdict in ("pass", "fail", "inconclusive"), "missing plan verification verdict")
        expected["verified"] = {**previous["verified"], label: verdict}
        diagnosis = [label] if verdict != "pass" else []
    else:
        require(previous["candidate"] == action["candidate"], "wrong closure plan candidate")
        diagnosis = set(inputs["public_review"]["missing"])
        diagnosis.update(label for label, verdict in previous["verified"].items() if verdict != "pass")
        if work == PARENT:
            from experiments.e1.executor import LEAVES
            from experiments.e1.outcomes import is_closed
            diagnosis.update(label for child, label in LEAVES.items() if not is_closed(before.rows[child]["state"]))
        diagnosis = sorted(diagnosis)
        if not diagnosis:
            expected["state"] = "closed"
    require(same(actual, expected) and same(marker["diagnosis"], diagnosis), "plan effect does not match invocation")
    return ({"diagnosis": diagnosis, "claim": actual} if method == "decide" else {"state": actual["state"]})


def plan_response(action, method, reply):
    for field in ("port_return", "return"):
        if field not in action or "failure" in action[field]:
            continue
        answer = action[field]
        if method == "verify" and field == "return":
            answer = {k: v for k, v in answer.items() if k != "diagnosis"}
        require(same(answer, reply), f"{field} differs from actual plan publication")


def plan_read(action, method, before):
    inputs, oid = action["port_input"], action["candidate"]
    label = inputs["criterion" if method == "observe" else "open_run"]
    require(before.states[action["subject"]][1] == oid, "read-only plan operation used a different candidate")
    answer = {"criterion": label, "candidate": oid}
    if method == "observe":
        observed = inputs["observation"]
        require(observed["candidate"] == oid, "plan observer input candidate differs")
        answer["observed"] = observed["observed"]
    for field in ("port_return", "return"):
        if field in action and "failure" not in action[field]:
            actual = action[field]
            if method == "observe" and field == "return":
                actual = actual.get("response", actual)
            require(same(answer, actual), "read-only plan response differs from actual criterion and operand")


def revised(value):
    """Look for changed instructions actually released, excluding unselected decision alternatives."""
    if type(value) is list:
        return any(revised(v) for v in value)
    if type(value) is not dict:
        return False
    for key, item in value.items():
        if key in ("alternatives", "_publication", PLAN_VERSION):
            continue
        if key in ("retry_choice", "retry_schedule", "choice", "schedule") and item == policy.EXPONENTIAL:
            return True
        if key in ("statement", "rationale", "title") and item == policy.NEW:
            return True
        if key == "finding" and item == "contradicts" and value.get("assumption") == policy.ASSUMPTION:
            return True
        if revised(item):
            return True
    return False
