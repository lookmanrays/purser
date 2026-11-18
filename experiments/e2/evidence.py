"""Bind the changed requirement and measured exit to the participant's durable bytes."""

from experiments.e1.artifacts import START, git
from experiments.e1.executor import PATHS

from . import measure, outcomes
from .public_sources import identity


def file_ids(repo, oid):
    paths = {PATHS[w]: w for w in ("W1", "W3")}
    result = dict.fromkeys(paths.values())
    entries = git(repo, "ls-tree", "-z", oid, "--", *paths).split(b"\0")
    for entry in filter(None, entries):
        identity, path = entry.split(b"\t", 1)
        mode, kind, blob = identity.decode().split()
        result[paths[path.decode()]] = f"{mode} {kind} {blob}"
    return result


def published_submissions(history):
    """An issued or refused submit has no published candidate of its own."""
    return [p for e in history.journal if e["kind"] == "action" for p in (e["payload"],)
            if p.get("publicly_submitted") is True]


def submitted_at(history, ck, work, oid, attempt=None):
    for action in published_submissions(history):
        order = history.source.compare(action.get("submission_publication"), ck)
        if (order in (-1, 0) and action.get("subject") == work
                and action.get("candidate") == oid and (attempt is None or action.get("attempt") == attempt)):
            return True
    refused = history.numbered.get(attempt, {}).get("submission_refused_at")
    if refused and history.source.compare(refused, ck) in (-1, 0):
        return False
    return False if history.public_complete and history.actions_complete else None


def score(history, track, verdicts, platform, *, release, qualification, submitted, endpoint_known,
          step_boundaries, final_subject, known_candidates, available_candidates):
    from .checkpoint import unchanged_policy

    fault = history.first_delivered
    unchanged = {}
    if fault:
        p = fault
        before = history.checkpoint(p.get("before")) or {}
        ids = platform.get("policy_blobs", {})
        for subject in ("W1", "W3"):
            prior = before.get(subject, (None, None))[1]
            unchanged[subject] = unchanged_policy(ids, subject, prior, p.get("candidate"))
    end = platform.get("exit_candidate") or {}
    head = end.get("head")
    observed_head = head if head in available_candidates and end.get("dirty") is False else None
    facts = outcomes.assess(history, track, verdicts, delivery=release,
                            qualification=qualification, submitted=submitted, unchanged=unchanged,
                            final_oid=observed_head if final_subject else None, endpoint_known=endpoint_known,
                            action_sequence_complete=step_boundaries)
    for observation in facts["reopen_observations"] + facts["closure_observations"]:
        key = observation["publication"]
        attempt = history.claimed.get(history.source.index.get(key))
        if attempt is None and history.arm != "purser":
            edge = history.source.plan_edge({"plan_sha256": key})
            attempt = edge[1].attempt if edge else None
        observation["source_action"] = attempt
    facts["clean_exposure"] = history.clean_exposure
    facts["charged_attempts_observed"] = history.action_lower_bound
    latest, source, publication, subject, attempt = observed_head, None, None, None, None
    if latest:
        source = end.get("source")
    else:
        submissions = [p for p in published_submissions(history) if p.get("candidate") in known_candidates]
        for action in reversed(submissions):
            head_at_submit = action["submission_publication"]
            if all(history.source.compare(p["submission_publication"], head_at_submit) in (-1, 0)
                   for p in submissions):
                latest, source = action["candidate"], "last_witnessed_public_submission"
                publication = identity(history.arm, head_at_submit)
                subject, attempt = action["subject"], action["attempt"]
                break
        if not submissions and len(known_candidates) == 1:
            latest, = known_candidates
            if latest in available_candidates:
                source = "sealed_starting_fixture" if latest == START else "sole_surviving_sealed_candidate"
            else:
                source = "recorded_candidate_with_unavailable_git"
    phase = measure.OLD if track == "clean" else measure.NEW
    grades = outcomes.criteria(latest, verdicts, phase)
    facts.update(exit_git_head=head, exit_git_dirty=end.get("dirty"), last_observed_candidate=latest,
                 last_observed_source=source, last_observed_publication=publication,
                 last_observed_subject=subject, last_observed_attempt=attempt,
                 last_observed_utc=end.get("observed_utc") if observed_head else None,
                 last_observed_pass=outcomes.all_pass(grades), last_observed_criteria=grades,
                 sealed_candidate_criteria={oid: outcomes.criteria(oid, verdicts, phase) for oid in known_candidates})
    return facts
