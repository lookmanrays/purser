"""Execute and authenticate the public worker's three scoped Git inspections."""

import subprocess
import zipfile

from experiments.e1.accounting import digest
from experiments.e1.artifacts import START, git
from experiments.e1.capture import response_evidence
from experiments.e1.executor import PARENT, PATHS
from experiments.e1.measurement import same
from experiments.e1.report import require

from . import attribution

ERRORS = (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile)
TOOLS = ("git", "git_diff", "git_history")


def result(repo, tool, work, oid):
    paths = [] if work == PARENT else [PATHS[work]]
    if tool == "git_history":
        return git(repo, "log", "--format=%H", oid, "--", *paths).decode().splitlines()
    if tool == "git_diff":
        return git(repo, "diff", "--no-ext-diff", START, oid, "--", *paths).decode()
    require(tool == "git", "unknown public Git inspection")
    if paths:
        return git(repo, "cat-file", "blob", f"{oid}:{paths[0]}").decode()
    return git(repo, "ls-tree", "-r", oid).decode()


def binding(action, seed, start):
    return {"seed": seed, "arm": action["arm"], "start_monotonic_ns": start,
            "issued_monotonic_ns": action["issued_monotonic_ns"], "attempt": action["attempt"],
            "request": action["work_started"]}


def record(actions, oid, value, *, default_receiver):
    """Persist the successful command before invoking the possibly transforming receiver."""
    driver, action = actions.driver, actions.current
    body = dict(binding(action, driver.seed, driver.j.start), selected=oid, value=value,
                receiver="default" if default_receiver else "callback")
    action["git_read"] = driver.assets.put(body, "json")
    driver.j.persist()


def wire_type(tool, packet):
    if type(packet) is not dict or packet.keys() != {"value"}:
        return False
    value = packet["value"]
    if tool == "git_history":
        return type(value) is list and all(digest(oid, 40) for oid in value)
    return type(value) is str


def compatible(tool, work, oid, packet, actual):
    if not wire_type(tool, packet):
        return False
    value = packet["value"]
    if tool == "git_history":
        # All four registered paths exist at START in the single-parent fixture.
        if not value or value[-1] != START or len(set(value)) != len(value):
            return False
        if (oid is not None and work == PARENT and value[0] != oid) or (oid == START and value != [START]):
            return False
    return actual is None or same(value, actual)


def received(action, objects):
    state, packet = attribution.delivered(action, objects.item)
    if state is not True:
        return None
    receipt = action["delivery"]
    require(receipt["subject"] == action["subject"], "receiver identifies a different work subject")
    if "observed_monotonic_ns" in receipt:
        at, issued = receipt["observed_monotonic_ns"], action.get("issued_monotonic_ns")
        end = action.get("finished_monotonic_ns")
        require(type(at) is int and type(issued) is int and issued <= at
                and (end is None or type(end) is int and at <= end),
                "receiver observation is outside the issued Git read")
    require(type(packet) is dict, "malformed receiver packet")
    return packet


def returned(action, objects, dispute):
    inline = action.get("return")
    if "response_evidence" not in action:
        require(inline is None or type(inline) is dict and "failure" in inline,
                "successful outer return lacks its original envelope")
        return None
    envelope = objects.item(action["response_evidence"])
    reply = envelope["return"]
    require(same(envelope, response_evidence(dict(action, **{"return": reply}))),
            "outer response envelope identifies a different invocation")
    require(type(reply) is dict, "malformed outer response")
    if not same(reply, inline):
        dispute("outer response envelope differs from the inline return")
    return reply if "failure" not in reply else None


def native(action, objects, seed, start):
    body = objects.item(action["git_read"])
    expected = binding(action, seed, start)
    require(type(start) is int and type(expected["issued_monotonic_ns"]) is int
            and expected["issued_monotonic_ns"] >= start >= 0 and type(seed) is int,
            "command observation lacks its case clock or seed")
    require(type(body) is dict and set(body) == {*expected, "selected", "receiver", "value"}
            and same({k: body[k] for k in expected}, expected),
            "command observation identifies a different case or invocation")
    require(body["receiver"] in ("default", "callback") and digest(body["selected"], 40)
            and action["candidate"] in (None, body["selected"]), "command target or receiver conflicts with request")
    return body


def legacy_subject(tool, work, operand, receiver, reply, actual_at, dispute):
    """Only parent history names an implicit Git head. A callback can transform its outer return."""
    if receiver is not None and not compatible(tool, work, None, receiver, None):
        dispute("received bytes cannot be a result of this scoped Git command")
        receiver = None
    if operand is None:
        conflict = receiver is not None and wire_type(tool, reply) and not same(receiver, reply)
        if receiver is None or tool != "git_history" or work != PARENT:
            if conflict:
                dispute("receiver and authenticated outer return differ; the implicit subject is uncertain")
            return None
        oid = receiver["value"][0]
        if not compatible(tool, work, oid, receiver, actual_at(oid)):
            dispute(f"received history contradicts the Git ancestry of {oid}")
            return None
        if conflict:
            other = reply["value"]
            if other and other[0] != oid and compatible(tool, work, other[0], reply, actual_at(other[0])):
                dispute(f"received Git head {oid} conflicts with authenticated return {other[0]}; "
                        "the implicit subject is uncertain")
                return None
            dispute("outer return differs from the self-identifying received Git history")
        return oid

    if receiver is None and reply is None:
        return None
    actual = actual_at(operand)
    received_match = receiver is not None and compatible(tool, work, operand, receiver, actual)
    if receiver is not None and not received_match:
        dispute("receiver differs from the selected Git result")
    if received_match:
        if actual is None and wire_type(tool, reply) and not same(receiver, reply):
            dispute("receiver and authenticated outer return differ; original result is uncertain")
        return operand
    if reply is None:
        return None
    if wire_type(tool, reply):
        if compatible(tool, work, operand, reply, actual):
            return operand
        dispute("authenticated outer return does not corroborate the selected Git result")
        return None
    # The worker resolves its explicit operand before invoking a receiver that can
    # return an arbitrary acknowledgement. An admitted but failed lookup cannot do this.
    return operand if receiver is None else None


def selected(action, arm, objects, report, *, lookup, seed, start, native_producer):
    tool, work, operand = action["input"]["tool"], action["subject"], action["candidate"]
    require(action["arm"] == arm and type(action["attempt"]) is int and action["attempt"] > 0
            and work in (PARENT, *PATHS) and (operand is None or digest(operand, 40))
            and action["input"] == {"work": work, "tool": tool} and attribution.expected(action) == "inspect",
            "invalid scoped Git request")
    attribution.work_request(action, "inspect")
    if "work_started" not in action:
        if "git_read" in action or "delivery" in action or "response_evidence" in action:
            report("Git observation lacks a physical work entry")
        return None

    def observe(operation):
        try:
            return operation()
        except ERRORS as error:
            report(str(error))
            return None

    def actual_at(oid):
        repo = lookup(oid) if lookup else None
        return result(repo, tool, work, oid) if repo is not None else None

    receiver = observe(lambda: received(action, objects))
    reply = observe(lambda: returned(action, objects, report))
    claimed = None
    if "git_read" in action and not native_producer:
        report("command observation is outside the registered producer")
    if native_producer and "git_read" in action:
        source = observe(lambda: native(action, objects, seed, start))
        if source is not None:
            claimed = oid = source["selected"]
            packet = {"value": source["value"]}
            if compatible(tool, work, oid, packet, actual_at(oid)):
                if receiver is not None and not same(receiver, packet):
                    report("receiver payload differs from the original Git command result")
                if source["receiver"] == "default":
                    disposition = (action.get("delivery") or {})
                    if type(disposition) is dict and disposition.get("state") in ("not_delivered", "unknown"):
                        report("default Git receiver has a contradictory handoff disposition")
                    if reply is not None:
                        if receiver is None:
                            report("default Git return lacks its original receipt")
                        if not same(reply, packet):
                            report("default return differs from the original Git command result")
                return oid
            report("recorded command result contradicts its selected Git object")
    elif native_producer and (receiver is not None or reply is not None):
        report("original successful Git command observation unavailable")
    observed = legacy_subject(tool, work, operand, receiver, reply, actual_at, report)
    if claimed is not None and observed is not None and observed != claimed:
        report("the handoff names a different commit than the bound command observation")
        return None
    return observed
