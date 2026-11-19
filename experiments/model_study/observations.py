"""The only behavioral execution port used by the model coding participant."""

from experiments.e1 import arms, executor, observer
from experiments.e1.artifacts import changes
from experiments.e1.measurement import MeasurementUnavailable
from experiments.e2 import checkpoint, evidence, measure, policy


def public_check(category, experiment, repo, oid, snapshot, selector, probe):
    phase = selector["phase"]
    if category == "worker":
        work = selector["work"]
        if experiment == "E1":
            return executor.local_check(snapshot, work, selector["skip"], run_probe=probe)
        return policy.worker_check(snapshot, work, phase, run_probe=probe)
    if category == "review":
        work = selector["work"]
        if experiment == "E1":
            return arms.self_review(repo, oid, snapshot, work, run_probe=probe)
        return policy.review(repo, oid, snapshot, work, phase, run_probe=probe)
    label = selector["criterion"]
    if experiment == "E1":
        return observer.inspect(repo, oid, snapshot, label, run_probe=probe)
    return policy.inspect(repo, oid, snapshot, label, phase, run_probe=probe)


class Checks:
    def __init__(self, candidates, outer, snapshot_from_git, seed, rank, assets, log):
        self.log = log
        self.repo = candidates.archive
        self.outer = outer
        self.snapshot_from_git = snapshot_from_git
        self.seed, self.rank, self.assets = seed, rank, assets
        self.reports = {}

    def snapshot(self, oid):
        if self.outer is None:
            raise MeasurementUnavailable("isolated candidate observer unavailable")
        return self.snapshot_from_git(self.repo, oid)

    def public_check(self, category, experiment, oid, selector):
        snapshot = self.snapshot(oid)
        with self.log.observing(category, oid, selector):
            return public_check(category, experiment, self.repo, oid, snapshot, selector, self.outer.probe)

    def protect(self, oid, labels, version, journal):
        missing = [label for label in labels if (oid, label, version) not in self.reports]
        if missing:
            snapshot = self.snapshot(oid)
            with self.log.observing("protected", oid, {"labels": missing, "version": version}) as group:
                reports = self.outer.measure(snapshot, self.seed, self.rank, labels=missing,
                                             **measure.parameters(version))
            if reports.keys() != set(missing):
                raise MeasurementUnavailable("isolated observer omitted a protected criterion")
            for label, report in reports.items():
                verdict = measure.grade(report, oid, label, self.seed, self.rank, version)
                if label == "scope/paths" and report["cases"][0]["all_changes"] != changes(self.repo, oid):
                    raise MeasurementUnavailable("scope observation differs from the archived tree")
                ref = self.assets.put(report, "json")
                journal.event("oracle", {"candidate": oid, "criterion": label, "version": version,
                                         "observed": verdict, "expected": measure.EXPECTED, "verdict": verdict,
                                         "evidence": ref, "probe_group": group["first_record"],
                                         "probe_receipts": self.assets.put(self.log.refs, "json")})
                self.reports[oid, label, version] = verdict

    def checkpoint(self, states, oid, track, journal):
        blobs = {}
        for candidate, labels in checkpoint.required_cases(states, oid).items():
            blobs[candidate] = evidence.file_ids(self.repo, candidate)
            self.protect(candidate, sorted(labels), measure.OLD, journal)
        grades = {(c, label): verdict for (c, label, v), verdict in self.reports.items() if v == measure.OLD}
        return checkpoint.eligible(states, oid, track, grades, blobs, states["W2"][1] == oid)
