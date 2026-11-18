"""Bind the changed requirement and measured exit to the participant's durable bytes."""

import json

from experiments.e1 import capture
from experiments.e1.artifacts import encoded, git, sha
from experiments.e1.executor import PATHS
from experiments.e1.measurement import same
from experiments.e1.purser_arm import OBSERVER

from . import outcomes, policy


def public_input(event, arm, seed, read, trace):
    if event is None:
        return None
    p = event["payload"]
    message = policy.notice(seed)
    if (not same(p.get("released"), message) or p.get("released_sha256") != sha(encoded(message))
            or p.get("observation_id") != message["id"] or p.get("seed") != seed):
        return None
    if not p.get("after"):
        return None
    if arm != "purser":
        before = read(p["before"]["view"])["W2"].get("premise")
        after = read(p["after"]["view"])["W2"].get("premise")
        return (same(after, message) if before is None else None)
    proposal = p.get("proposal")
    if not proposal or not trace:
        return None
    op = {"op": "assumption.observe", "id": message["id"], "assumption": message["assumption"],
          "finding": message["finding"]}
    if proposal["actor_id"] != OBSERVER or not same(proposal["body"]["ops"], [op]):
        return None
    receipts = trace["snapshot"]["projection"]["receipts"]
    receipt = next((r for r in receipts if r["actor_id"] == OBSERVER and r["proposal_id"] == proposal["proposal_id"]
                    and r["digest"] == proposal["digest"]), None)
    if not receipt:
        return False if p["before"] == p["after"] else None
    if receipt["disposition"] != "admitted":
        return False
    events = [json.loads(e) for e in trace["original_events"][p["before"]["revision"]:p["after"]["revision"]]]
    events = [e for e in events if e["actor_id"] == OBSERVER and e["proposal_id"] == proposal["proposal_id"]]
    checksum = p["released_sha256"]
    expected = {"id": message["id"], "commitment": "C-notify", "assumption": message["assumption"],
                "finding": message["finding"], "observer": OBSERVER, "artifact_sha256": checksum}
    observed = [e for e in events if e["type"] == "PremiseObserved" and e["event_version"] == 1]
    if len(observed) != 1 or not same(observed[0]["data"], expected):
        return None
    ref = trace["kernel_artifacts"].get(checksum)
    return bool(ref and ref["sha256"] == checksum and same(read(ref), message))


def file_ids(repo, oid):
    paths = {PATHS[w]: w for w in ("W1", "W3")}
    result = dict.fromkeys(paths.values())
    entries = git(repo, "ls-tree", "-z", oid, "--", *paths).split(b"\0")
    for entry in filter(None, entries):
        identity, path = entry.split(b"\t", 1)
        mode, kind, blob = identity.decode().split()
        result[paths[path.decode()]] = f"{mode} {kind} {blob}"
    return result


def score(journal, track, arm, seed, lookup_checkpoint, read, trace, verdicts, platform, *,
          endpoint_known, history_complete, action_sequence_complete, known_candidates, issue=None):
    fault = next((e for e in journal if e["kind"] == "fault"), None)
    unchanged = {}
    try:
        delivered = public_input(fault, arm, seed, read, trace)
    except (KeyError, ValueError, TypeError, IndexError) as error:
        delivered = None
        if issue is not None:
            issue(f"public intervention witness: {error}")
    if fault:
        p = fault["payload"]
        before = lookup_checkpoint(p.get("before")) or {}
        ids = platform.get("policy_blobs", {})
        for subject in ("W1", "W3"):
            prior = ids.get(before.get(subject, (None, None))[1], {}).get(subject)
            later = ids.get(p.get("candidate"), {}).get(subject)
            unchanged[subject] = prior == later if prior and later else None
    end = platform.get("exit_candidate") or {}
    oid = end.get("head")
    final_oid = oid if oid in known_candidates and end.get("dirty") is False else None
    facts = outcomes.assess(journal, track, lookup_checkpoint, verdicts, delivery=delivered,
                            unchanged=unchanged, final_oid=final_oid, endpoint_known=endpoint_known,
                            history_complete=history_complete, action_sequence_complete=action_sequence_complete)
    return facts


def public_checkpoint(read):
    def load(ck):
        if not ck:
            return None
        return {k: capture.position(v) for k, v in read(ck["view"]).items()}
    return load
