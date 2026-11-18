"""The private fault driver admits a change only on a measured old-task checkpoint."""

from pathlib import Path

from experiments.e1 import capture
from experiments.e1.executor import LEAVES
from experiments.e1.measurement import MeasurementUnavailable

from . import evidence, measure
from .outcomes import checkpoint_eligibility


def required_cases(states, oid):
    cases = {oid: {LEAVES["W1"], LEAVES["W2"], LEAVES["W3"]}}
    for work in ("W1", "W3"):
        candidate = states[work][1]
        if candidate:
            cases.setdefault(candidate, set()).add(LEAVES[work])
    return cases


def eligible(states, oid, track, verdicts, blobs):
    unchanged = {}
    for work in ("W1", "W3"):
        _, candidate = states.get(work, (None,None))
        prior, now = blobs.get(candidate, {}), blobs.get(oid, {})
        unchanged[work] = prior[work] == now[work] if work in prior and work in now else None
    def passed(candidate, label):
        verdict = verdicts.get((candidate,label))
        return verdict == "pass" if verdict in ("pass", "fail") else None
    # The driver passes the return of the normal W2 submission. The reader separately
    # authenticates that same action and candidate in the retained participant history.
    return checkpoint_eligibility(states, oid, track, passed, unchanged, True)


def qualify(driver, oid):
    public, identity = driver.adapter.public_view()
    states = {work:capture.position(row) for work,row in public.items()}
    ck = capture.checkpoint(driver.assets, public, identity)
    result = {"candidate":oid, "checkpoint":ck, "checks":[], "policy_blobs":{}, "qualified":None}
    verdicts = {}
    with driver.j.measuring_checkpoint(result):
        for candidate, labels in required_cases(states, oid).items():
            result["policy_blobs"][candidate] = evidence.file_ids(driver.candidates.archive, candidate)
            with driver.candidates.export(candidate) as directory:
                # Retain each completed measurement even if the next one cannot run.
                for label in sorted(labels):
                    report = measure.check(driver.candidates.archive, candidate, Path(directory), driver.seed,
                                           driver.rank, measure.OLD, [label])[label]
                    result["checks"].append({"candidate":candidate, "criterion":label,
                                             "evidence":driver.assets.put(report,"json")})
                    verdicts[candidate,label] = report["verdict"]
                    driver.j.persist()
        result["qualified"] = eligible(states, oid, driver.track, verdicts, result["policy_blobs"])
        if result["qualified"] is None:
            raise MeasurementUnavailable("protected checkpoint could not be observed")
    return result["qualified"]


def validate(pause, states, track, seed, rank, objects, blobs, measured, *, complete):
    """Compare the retained admission witness to the independent post-clock Git observation."""
    if not pause:
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
    if pause.get("qualified") is not None or complete:
        if (set(verdicts) != required
                or eligible(states, oid, track, verdicts, blobs) is not pause.get("qualified")
                or type(pause["qualified"]) is not bool):
            raise ValueError("checkpoint disposition differs from the old public state and queue measurements")
