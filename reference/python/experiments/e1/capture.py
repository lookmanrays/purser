"""Controller observations of the public participant and interrupted invocations."""

import subprocess
from datetime import datetime, timezone

from .artifacts import git, sha
from .scorer import action_diagnosis, public_diagnosis


def position(row):
    candidate = row["candidate"]
    return row["state"], candidate["digest"] if isinstance(candidate, dict) else candidate


def checkpoint(assets, rows, identity):
    return {**identity, "view": assets.put(rows, "json")}


def published(events, initial):
    previous = dict(initial)
    for item in events:
        if item["kind"] == "public_state":
            row = item["payload"]
            previous[row["subject"]] = (row["to"], row["candidate"])
    return previous


def states(rows, checkpoint, previous, emit, *, action=None, decision=None, recovered=False):
    for work, row in sorted(rows.items()):
        state, oid = position(row)
        prior = previous.get(work)
        # Early checkpoints retained the state alone.
        old, old_oid = prior if isinstance(prior, (tuple, list)) else (prior, oid)
        release = decision if action and work == action["subject"] else None
        if (old, old_oid) == (state, oid) and release is None:
            continue
        event = {"subject": work, "from": old, "to": state, "candidate": oid,
                 "source_action": action["attempt"] if action else None,
                 "checkpoint": checkpoint, "diagnosis": release["diagnosis"] if release else [],
                 "evidence": release, "effect_disposition": None}
        if recovered:
            event.update(recovery_read_utc=datetime.now(timezone.utc).isoformat(), transition_offset_ns=None)
        emit("public_state", event)
        previous[work] = (state, oid)


def response_evidence(action):
    return {key: action[key] for key in ("attempt", "actor", "subject", "candidate", "return")}


def returned_decision(action):
    reply = action.get("return")
    if not isinstance(reply, dict) or "failure" in reply:
        return None
    labels = sorted(action_diagnosis(action))
    if labels or action["actor"] in ("closure", "independent_verifier"):
        return {"diagnosis": labels, "source_action": action["attempt"]}
    return None


def durable_decision(rows, action, snapshot):
    """Identify the actual write using the participant's receipt for this invocation."""
    actor = action["actor"]
    work, oid = action["subject"], action["candidate"]
    if actor not in ("independent_verifier", "closure") or work not in rows or position(rows[work])[1] != oid:
        return None
    if snapshot is None:
        receipt = rows[work].get("_publication", {})
        identity = {"attempt": action["attempt"], "actor": actor, "subject": work, "candidate": oid}
        if not all(receipt.get(k) == v for k, v in identity.items()):
            return None
        return {"source_action": action["attempt"], "diagnosis": receipt["diagnosis"], "publication": receipt}
    proposal = action.get("proposal")
    if not proposal:
        return None
    receipts = snapshot["projection"]["receipts"]
    receipt = next((r for r in receipts if all(r[k] == proposal[k] for k in
                                                ("actor_id", "proposal_id", "digest"))), None)
    if receipt is None:
        return None
    labels = set()
    if actor == "closure":
        details = receipt["details"]
        if receipt["code"] == "closure_blocked" and details.get("work") == work:
            labels = public_diagnosis(details)
    elif receipt["disposition"] == "admitted":
        # The receipt binds the exact submitted evidence op. Read its actual persisted run and subject.
        op, = proposal["body"]["ops"]
        records = {r["id"]: r["fields"] for r in snapshot["projection"]["records"]}
        evidence = records.get(op["id"], {})
        run = records.get(evidence.get("run"), {})
        if run.get("work") != work or run.get("candidate", {}).get("digest") != oid:
            return None
        if evidence.get("verdict") in ("fail", "inconclusive"):
            labels = {records[run["obligation"]]["title"]}
    return {"source_action": action["attempt"], "diagnosis": sorted(labels), "receipt": receipt}


def repair_basis(root):
    # The scripted repair modifies or removes paths tracked by the previous sealed submission.
    return sha(git(root, "diff", "--no-ext-diff", "--binary", "HEAD", "--"))


def reconcile(assets, rows, identity, snapshot, events, initial, facts, emit, worker):
    final = checkpoint(assets, rows, identity)
    facts["final_public"] = final
    action = next((e["payload"] for e in reversed(events) if e["kind"] == "action"), None)
    decision = None
    if action is not None:
        action.setdefault("after", final)
        if "repair_before" in action and (not action.get("return") or "failure" in action["return"]):
            if worker.is_dir():
                try:
                    if repair_basis(worker) != action["repair_before"]:
                        action["repair_applied"] = True
                except (OSError, subprocess.SubprocessError):
                    pass
        captured = any(e["kind"] == "public_state" and e["payload"]["subject"] == action["subject"]
                       and (e["payload"].get("evidence") or {}).get("source_action") == action["attempt"]
                       for e in events)
        if not captured:
            decision = returned_decision(action) or durable_decision(rows, action, snapshot)
    states(rows, final, published(events, initial), emit, action=action, decision=decision, recovered=True)
    actions = [e["payload"] for e in events if e["kind"] == "action"]
    facts["public_history_complete"] = bool(facts.get("initial_public")) and all(
        a.get("before") and a.get("after") for a in actions)
