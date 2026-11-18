"""The private fault driver admits a change only on a measured old-task checkpoint."""

from pathlib import Path
from subprocess import SubprocessError
from tempfile import TemporaryDirectory

from experiments.e1 import capture
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
    history = read_participant(arm, list(driver.j.rows), platform, objects.item, issues.append,
                               seed=driver.seed, track=driver.track)
    return history, source


def accepted_submission(history, checkpoint, oid):
    for number, action in reversed(history.numbered.items()):
        if (action.get("publicly_submitted") is True
                and evidence.submitted_at(history, checkpoint, "W2", oid, number) is True):
            return True, number
    return (False if history.public_complete and history.actions_complete else None), None


def old_assignment(history, checkpoint, submission_attempt=None):
    """Read the operative W2 law from the original head and from its accepted submission."""
    def selected(ck):
        index = history.source.position(ck)
        if index is None:
            return None
        original = history.source.publications[index]
        if history.arm != "purser":
            row = original.rows["W2"]
            return (row.get("criterion") == LEAVES["W2"] and row.get("retry_choice") == policy.LINEAR
                    and "premise" in row and row["premise"] is None)
        decisions = WorkView(original.snapshot["projection"]).graph.decisions("W2")
        return (original.records.get(policy.ASSUMPTION, {}).get("state") == "held" and len(decisions) == 1
                and decisions[0]["id"] == policy.ORIGINAL and decisions[0]["choice"] == policy.LINEAR
                and decisions[0]["support"] == [policy.ASSUMPTION])
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


def validate(pause, states, track, seed, rank, objects, blobs, measured, *, complete, submitted, old_assignment):
    """Compare the retained admission witness to the independent post-clock Git observation."""
    if not pause:
        return
    if not states and pause.get("checkpoint") is None and not complete and pause.get("qualified") is None:
        return
    if not states:
        raise ValueError("missing public checkpoint state for the private measurement")
    oid = pause["candidate"]
    verdicts = {}
    required = {(candidate,label) for candidate,labels in required_cases(states,oid).items() for label in labels}
    for row in pause["checks"]:
        key = row["candidate"], row["criterion"]
        if key not in required or key in verdicts:
            raise ValueError("unexpected or repeated protected checkpoint check")
        report = objects.item(row["evidence"])
        verdict = measure.grade(report, *key, seed, rank, measure.OLD)
        if verdict != report["verdict"]:
            raise ValueError("pre-change verdict disagrees with its observation")
        other = measured.get((*key, measure.OLD))
        if (other is not None and report != other) or (complete and other is None):
            raise ValueError("pre-change measurement disagrees with the independent offline observation")
        verdicts[key] = verdict
    for candidate, value in pause["policy_blobs"].items():
        if candidate not in blobs or blobs[candidate] != value:
            raise ValueError("pre-change sibling policy differs from archived Git bytes")
    if submitted is not None and pause.get("submitted") is not submitted:
        raise ValueError("recorded old W2 submission differs from the original publication")
    if old_assignment is not None and pause.get("old_assignment") is not old_assignment:
        raise ValueError("recorded old W2 law differs from its operative assignment")
    if pause.get("qualified") is not None or complete:
        qualified = conjunction([eligible(states, oid, track, verdicts, blobs, submitted), old_assignment])
        if ((pause.get("qualified") is True and set(verdicts) != required)
                or qualified is not pause.get("qualified")
                or type(pause["qualified"]) is not bool):
            raise ValueError("checkpoint disposition differs from the old public state and queue measurements")
