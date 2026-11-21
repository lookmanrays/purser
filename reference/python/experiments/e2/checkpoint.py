"""The private fault driver admits a change only on a measured old-task checkpoint."""

from pathlib import Path
from subprocess import SubprocessError
from tempfile import TemporaryDirectory

from experiments.e1 import capture
from experiments.e1.accounting import READ_ERRORS
from experiments.e1.artifacts import encoded, extract_tree
from experiments.e1.executor import LEAVES
from experiments.e1.measurement import MeasurementUnavailable
from experiments.e1.purser_arm import retained_kernel
from experiments.e1.report import GitPacks, Objects
from purser.kernel.workview import WorkView

from . import evidence, measure, plan_versions, policy
from .outcomes import checkpoint_eligibility, conjunction
from .public_history import read_participant

def required_cases(states, oid):
    cases = {oid: {LEAVES["W1"], LEAVES["W2"], LEAVES["W3"]}}
    for work in ("W1", "W3"):
        candidate = states[work][1]
        if candidate:
            cases.setdefault(candidate, set()).add(LEAVES[work])
    return cases


def unchanged_policy(blobs, work, prior_oid, checkpoint_oid):
    prior, now = blobs.get(prior_oid, {}), blobs.get(checkpoint_oid, {})
    if work not in prior or work not in now:
        return None
    return prior[work] is not None and prior[work] == now[work]


def eligible(states, oid, track, verdicts, blobs, submitted):
    unchanged = {work: unchanged_policy(blobs, work, states.get(work, (None, None))[1], oid)
                 for work in ("W1", "W3")}
    def passed(candidate, label):
        verdict = verdicts.get((candidate,label))
        return verdict == "pass" if verdict in ("pass", "fail") else None
    return checkpoint_eligibility(states, oid, track, passed, unchanged, submitted)


def participant_at(driver, public, checkpoint, objects):
    """Use the same original proposal/plan attribution as the cold reader, up to this head."""
    arm, adapter = driver.j.arm, driver.adapter
    if arm == "purser":
        revision = checkpoint["revision"]
        snapshot = adapter.store.snapshot(revision=revision)
        trace = retained_kernel(adapter.store, snapshot)
        trace["original_events"] = trace["original_events"][:revision]
    else:
        trace = {"plan": public, "plan_versions": plan_versions.retain(adapter.file.parent, driver.assets, encoded(public))}
    source = driver.assets.put(trace, "json")
    platform = dict(driver.j.platform, treatment_trace=source, final_public=checkpoint)
    issues = []
    history = read_participant(arm, list(driver.j.rows), platform, objects.item, issues.append, objects=objects,
                               seed=driver.seed, track=driver.track)
    return history, source


def accepted_submission(history, checkpoint, oid):
    for number, action in reversed(history.numbered.items()):
        if (action.get("publicly_submitted") is True
                and evidence.submitted_at(history, checkpoint, "W2", oid, number) is True):
            return True, number
    return (False if history.public_complete and history.actions_complete else None), None


def original_retry_policy(arm, source):
    """Read the operative A1 decision from the W2 plan row or kernel projection."""
    if arm != "purser":
        return source["criterion"] == LEAVES["W2"] and source["retry_choice"] == policy.LINEAR and source["premise"] is None
    view = WorkView(source)
    decisions = view.graph.decisions("W2")
    return (view.records.get(policy.ASSUMPTION, {}).get("fields", {}).get("state") == "held" and len(decisions) == 1
            and decisions[0]["id"] == policy.ORIGINAL and decisions[0]["choice"] == policy.LINEAR
            and decisions[0]["support"] == [policy.ASSUMPTION])


def old_assignment(history, checkpoint, submission_attempt=None):
    """Read the operative W2 law from the original head and from its accepted submission."""
    def selected(ck):
        if history.arm != "purser":
            try:
                return original_retry_policy(history.arm, history.source.context(ck, "W2"))
            except READ_ERRORS:
                return None
        index = history.source.position(ck)
        if index is None:
            return None
        return original_retry_policy(history.arm, history.source.publications[index].snapshot["projection"])
    values = [selected(checkpoint)]
    if submission_attempt is not None:
        publication = next((p.get("submission_publication") for p in evidence.published_submissions(history)
                            if p.get("subject") == "W2" and p.get("attempt") == submission_attempt), None)
        values.append(selected(publication))
    return conjunction(values)


def qualify(driver, oid):
    result = {"candidate": oid, "checkpoint": None, "checks": [], "policy_blobs": {}, "qualified": None,
              "submitted": None, "submission_attempt": None, "old_assignment": None, "missing_checks": []}
    verdicts = {}
    with driver.j.measuring_checkpoint(result):
        public, identity = driver.adapter.public_view()
        states = {work: capture.position(row) for work, row in public.items()}
        result["checkpoint"] = capture.checkpoint(driver.assets, public, identity)
        objects = Objects(driver.assets.archive)
        history, result["submission_source"] = participant_at(driver, public, result["checkpoint"], objects)
        result["submitted"], result["submission_attempt"] = accepted_submission(history, result["checkpoint"], oid)
        result["old_assignment"] = old_assignment(history, result["checkpoint"], result["submission_attempt"])
        driver.j.persist()
        with TemporaryDirectory(prefix="purser-e2-checkpoint-") as directory:
            packs = GitPacks(Path(directory))
            for candidate, labels in required_cases(states, oid).items():
                try:
                    repo = packs.restore(candidate, driver.candidates.entries[candidate], objects)
                    result["policy_blobs"][candidate] = evidence.file_ids(repo, candidate)
                    with extract_tree(repo, candidate) as root:
                        for label in sorted(labels):
                            try:
                                report = measure.check(repo, candidate, Path(root), driver.seed, driver.rank,
                                                       measure.OLD, [label])[label]
                            except MeasurementUnavailable as error:
                                result["missing_checks"].append({"candidate": candidate, "criterion": label, "reason": str(error)})
                                continue
                            result["checks"].append({"candidate": candidate, "criterion": label,
                                                     "evidence": driver.assets.put(report, "json")})
                            verdicts[candidate, label] = report["verdict"]
                            driver.j.persist()
                except (KeyError, OSError, ValueError, SubprocessError) as error:
                    result["missing_checks"].append({"candidate": candidate, "reason": str(error)})
        result["qualified"] = conjunction([eligible(states, oid, driver.track, verdicts, result["policy_blobs"],
                                                   result["submitted"]), result["old_assignment"]])
        if result["qualified"] is None:
            raise MeasurementUnavailable("protected checkpoint could not be observed")
    return result["qualified"]


def validate(pause, states, track, seed, rank, objects, blobs, measured, *, complete, submitted, old_assignment,
             issue, unavailable):
    """Adjudicate the original admission at its own public head, then audit the recorded answers."""
    if not states:
        if pause.get("checkpoint") is not None or complete:
            unavailable("old checkpoint public state unavailable")
        return None
    oid = pause["candidate"]
    required = {(candidate, label) for candidate, labels in required_cases(states, oid).items() for label in labels}
    verdicts, seen = {}, set()
    for row in pause["checks"]:
        try:
            key = row["candidate"], row["criterion"]
            if key not in required or key in seen:
                raise ValueError("unexpected or repeated protected checkpoint check")
            seen.add(key)
            ref = row["evidence"]
            other = measured.get((*key, measure.OLD))
            try:
                report = objects.item(ref)
            except READ_ERRORS:
                from .public_sources import view_ref
                if other is None or ref != view_ref(other):
                    raise
                report = other
            verdict = measure.grade(report, *key, seed, rank, measure.OLD)
            if other is not None and report != other:
                issue("pre-change measurement disagrees with the independent offline observation")
                continue
            if complete and other is None:
                issue("complete run lacks an independent offline checkpoint observation")
            verdicts[key] = verdict
        except READ_ERRORS as error:
            issue(f"protected checkpoint check: {error}")
    for candidate, value in pause["policy_blobs"].items():
        if candidate in blobs and blobs[candidate] != value:
            issue("pre-change sibling policy differs from archived Git bytes")
    qualified = conjunction([eligible(states, oid, track, verdicts, blobs, submitted), old_assignment])
    for field, found in (("submitted", submitted), ("old_assignment", old_assignment), ("qualified", qualified)):
        recorded = pause.get(field)
        if found is not None and recorded is not None and found is not recorded:
            issue(f"recorded checkpoint {field} differs from the authenticated observation")
        elif found is None and (recorded is not None or complete):
            unavailable(f"original checkpoint {field} cannot be established")
        elif recorded is None and complete:
            issue(f"complete run lacks recorded checkpoint {field}")
    if (pause.get("qualified") is True or complete) and seen != required:
        issue("original checkpoint measurements omit a required old criterion")
    return qualified
