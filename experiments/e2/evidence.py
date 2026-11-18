"""Bind the changed requirement and measured exit to the participant's durable bytes."""

from experiments.e1.artifacts import START, git
from experiments.e1.executor import PATHS

from . import measure, outcomes


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
    boundary = history.source.position(ck)
    if boundary is None:
        return None
    for action in published_submissions(history):
        commit = history.source.position(action.get("submission_publication"))
        if (commit is not None and commit <= boundary and action.get("subject") == work
                and action.get("candidate") == oid and (attempt is None or action.get("attempt") == attempt)):
            return True
    return False if history.public_complete and history.actions_complete else None


def score(history, track, verdicts, platform, *, release, qualification, submitted, endpoint_known,
          step_boundaries, final_subject, known_candidates):
    from .checkpoint import unchanged_policy

    journal = history.journal
    fault = next((e for e in journal if e["kind"] == "fault" and e["payload"].get("publicly_delivered") is True), None)
    unchanged = {}
    if fault:
        p = fault["payload"]
        before = history.checkpoint(p.get("before")) or {}
        ids = platform.get("policy_blobs", {})
        for subject in ("W1", "W3"):
            prior = before.get(subject, (None, None))[1]
            unchanged[subject] = unchanged_policy(ids, subject, prior, p.get("candidate"))
    end = platform.get("exit_candidate") or {}
    head = end.get("head")
    observed_head = head if head in known_candidates and end.get("dirty") is False else None
    facts = outcomes.assess(journal, track, history.checkpoint, verdicts, delivery=release,
                            qualification=qualification, submitted=submitted, unchanged=unchanged,
                            final_oid=observed_head if final_subject else None, endpoint_known=endpoint_known,
                            history_complete=history.public_complete, action_sequence_complete=step_boundaries)
    facts["clean_exposure"] = history.clean_exposure
    facts["charged_attempts_observed"] = history.action_lower_bound
    latest, source, publication = observed_head, None, None
    if latest:
        source = end.get("source")
    else:
        submissions = [(history.source.position(p.get("submission_publication")), p)
                       for p in published_submissions(history) if p.get("candidate") in known_candidates]
        known_order = [(position, p) for position, p in submissions if position is not None]
        if known_order:
            publication, action = max(known_order, key=lambda item: item[0])
            latest, source = action["candidate"], "last_witnessed_public_submission"
        elif len(known_candidates) == 1:
            latest, = known_candidates
            source = "sealed_starting_fixture" if latest == START else "sole_surviving_sealed_candidate"
    phase = measure.OLD if track == "clean" else measure.NEW
    grades = outcomes.criteria(latest, verdicts, phase)
    facts.update(exit_git_head=head, exit_git_dirty=end.get("dirty"), last_observed_candidate=latest,
                 last_observed_source=source, last_observed_publication=publication,
                 last_observed_utc=end.get("observed_utc") if observed_head else None,
                 last_observed_pass=outcomes.all_pass(grades), last_observed_criteria=grades,
                 sealed_candidate_criteria={oid: outcomes.criteria(oid, verdicts, phase) for oid in known_candidates})
    return facts
