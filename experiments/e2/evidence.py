"""Bind the changed requirement and measured exit to the participant's durable bytes."""

from experiments.e1.artifacts import git
from experiments.e1.executor import PARENT, PATHS
from experiments.e1.outcomes import LABELS

from . import outcomes


def file_ids(repo, oid):
    paths = {PATHS[w]: w for w in ("W1", "W3")}
    result = dict.fromkeys(paths.values())
    entries = git(repo, "ls-tree", "-z", oid, "--", *paths).split(b"\0")
    for entry in filter(None, entries):
        identity, path = entry.split(b"\t", 1)
        mode, kind, blob = identity.decode().split()
        result[paths[path.decode()]] = f"{mode} {kind} {blob}"
    return result


def score(history, track, verdicts, platform, *, release, qualification, endpoint_known, step_boundaries, final_subject,
          known_candidates):
    journal, lookup_checkpoint = history.journal, history.checkpoint
    fault = next((e for e in journal if e["kind"] == "fault"), None)
    unchanged = {}
    if fault:
        p = fault["payload"]
        before = lookup_checkpoint(p.get("before")) or {}
        ids = platform.get("policy_blobs", {})
        for subject in ("W1", "W3"):
            prior = ids.get(before.get(subject, (None, None))[1], {}).get(subject)
            later = ids.get(p.get("candidate"), {}).get(subject)
            unchanged[subject] = prior == later if prior is not None and later is not None else None
    end = platform.get("exit_candidate") or {}
    oid = end.get("head")
    final_oid = oid if oid in known_candidates and end.get("dirty") is False else None
    facts = outcomes.assess(journal, track, lookup_checkpoint, verdicts, delivery=release, qualification=qualification,
                            unchanged=unchanged, final_oid=final_oid if final_subject else None, endpoint_known=endpoint_known,
                            history_complete=history.public_complete, action_sequence_complete=step_boundaries)
    facts["clean_exposure"] = history.clean_exposure
    facts["charged_attempts_observed"] = history.action_lower_bound
    latest, source, location = final_oid, None, None
    if latest:
        source = end.get("source", "stopped_worker_clean_HEAD")
    else:
        for event in reversed(journal):
            value = event["payload"]
            if event["kind"] not in ("fault","action","public_state"):
                continue
            ck = value.get("checkpoint") if event["kind"] == "public_state" else value.get("after")
            states = lookup_checkpoint(ck)
            if states is None:
                continue
            subject = value.get("subject")
            for work in dict.fromkeys([PARENT,subject]):
                candidate = states.get(work,(None,None))[1]
                if candidate in known_candidates:
                    latest, source, location = candidate, "last_public_candidate", event.get("seq")
                    break
            if latest:
                break
    version = 1 if track == "clean" else 2
    grades = [verdicts.get((latest,label,version if label in ("W2/retry","queue/composition") else 1)) for label in LABELS]
    facts.update(last_observed_candidate=latest, last_observed_source=source, last_observed_sequence=location,
                 last_observed_utc=end.get("observed_utc") if final_oid else None,
                 last_observed_pass=outcomes.conjunction(g=="pass" if g in ("pass","fail") else None for g in grades))
    return facts
