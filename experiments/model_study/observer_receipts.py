"""Case-owned original guest input/output and offline interpretation of its bounded data."""

from contextlib import contextmanager

from experiments.e1.artifacts import encoded
from experiments.e1.measurement import same
from experiments.e1.recovery import write_atomic
from experiments.e1.report import parse, require


class ReceiptLog:
    def __init__(self, directory, assets, journal):
        self.path = directory / "isolation"
        self.path.mkdir()
        self.assets, self.journal = assets, journal
        self.number, self.group = 0, None

    @contextmanager
    def observing(self, category, oid, selector):
        if self.group is not None:
            raise ValueError("overlapping isolated observation groups")
        self.group = {"first_record": self.number + 1, "use": category, "candidate": oid,
                      "selector": selector, "preceding_actions": self.journal.steps}
        try:
            yield self.group
        finally:
            self.group = None

    def record(self, event):
        if self.group is None or self.group["candidate"] != event["candidate"]:
            raise ValueError("isolated observer reported a different candidate or operation")
        self.number += 1
        entry = {"group": self.group, "receipt": self.assets.put(event, "json")}
        write_atomic(self.path / f"{self.number:05}.json", encoded(entry))

    @staticmethod
    def retain(directory):
        entries = []
        for i, path in enumerate(sorted((directory / "isolation").glob("*.json")), 1):
            require(path.name == f"{i:05}.json", "missing original isolation callback")
            entries.append(parse(path.read_bytes()))
        return entries


class Observed:
    def __init__(self, entries, objects, repos, complete):
        from experiments.model_isolation import Snapshot
        self.groups, self.snapshots, self.protected = {}, {}, {}
        self.by_action = {}
        starts = {}
        for index, row in enumerate(entries, 1):
            group, receipt = row["group"], objects.item(row["receipt"])
            n, oid = group["first_record"], group["candidate"]
            require(0 < n <= index and receipt["candidate"] == oid and oid in repos, "wrong isolated candidate")
            if n not in self.groups:
                require(n == index, "unbound original isolation group")
                self.groups[n] = group, []
                if group["use"] != "protected":
                    attempt = group["preceding_actions"]
                    require(attempt not in self.by_action, "duplicate public observer group")
                    self.by_action[attempt] = n
            require(self.groups[n][0] == group, "observer grouping changed")
            if oid not in self.snapshots:
                self.snapshots[oid] = Snapshot.from_git(repos[oid], oid)
            key = receipt["invocation"]
            if receipt["phase"] == "started":
                require(key not in starts, "duplicate observer start")
                starts[key] = n, receipt
            elif receipt["phase"] in ("measured", "unavailable"):
                start_group, started = starts.pop(key, (n, None))
                require(start_group == n and (started or receipt["phase"] == "unavailable"),
                        "guest result lacks its original start")
                self.groups[n][1].append((started, receipt))
            else:
                raise ValueError("unknown observer callback phase")
        require(not starts or not complete, "original isolated invocation has no terminal receipt")

    @contextmanager
    def reader(self, group_number, oid):
        from experiments.model_isolation import read_observation
        pairs = iter(self.groups.get(group_number, ({}, []))[1])
        def probe(snapshot, *, cases=None, legacy=False):
            pair = next(pairs, None)
            require(pair and snapshot.oid == oid, "missing original isolated invocation")
            return read_observation(snapshot, *pair, cases=cases, legacy=legacy)
        yield probe
        require(next(pairs, None) is None, "extra isolated observations in registered group")

    def public(self, category, experiment, repo, action, selector):
        from .observations import public_check
        oid = action["candidate"]
        n = self.by_action.get(action["attempt"])
        if n is not None:
            group = self.groups[n][0]
            require(group["use"] == category and group["candidate"] == oid and group["selector"] == selector,
                    "public observer ran for another action, phase or subject")
        with self.reader(n, oid) as probe:
            return public_check(category, experiment, repo, oid, self.snapshots.get(oid), selector, probe)

    def validate(self, group_number, oid, label, version, report, seed, rank):
        from experiments.e1 import scorer
        from experiments.e2 import measure
        if label == "scope/paths":
            return
        if group_number not in self.protected:
            group, _ = self.groups[group_number]
            selector = group["selector"]
            require(group["use"] == "protected" and group["candidate"] == oid and selector["version"] == version,
                    "protected result lacks its original isolated group")
            labels = selector["labels"]
            offsets = measure.parameters(version)["offsets"]
            cases = [(name, case) for name, case in scorer.inputs(seed, rank, offsets=offsets) if name in labels]
            observed = {name: [] for name in labels}
            snapshot = self.snapshots[oid]
            with self.reader(group_number, oid) as probe:
                if cases:
                    values = probe(snapshot, cases=[case for _,case in cases])
                    for (name, _), value in zip(cases, values, strict=True):
                        observed[name].append(value)
                if "legacy/compat" in labels:
                    observed["legacy/compat"].append(probe(snapshot, legacy=True))
            self.protected[group_number] = (oid, version, observed)
        original, law, observed = self.protected[group_number]
        require(original == oid and law == version and same(observed[label], [r["observed"] for r in report["cases"]]),
                "graded samples differ from original isolated observations")
