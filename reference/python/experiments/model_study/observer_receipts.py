"""Case-owned original guest input/output and offline interpretation of its bounded data."""

from contextlib import contextmanager

from experiments.e1.artifacts import encoded, sha
from experiments.e1.measurement import MeasurementUnavailable, same
from experiments.e1.recovery import write_atomic
from experiments.e1.report import parse, require


class ReceiptLog:
    def __init__(self, directory, assets, journal, identity):
        self.path = directory / "isolation"
        self.path.mkdir()
        self.assets, self.journal = assets, journal
        self.number, self.group = 0, None
        self.identity = identity
        self.refs = []

    @contextmanager
    def observing(self, category, oid, selector):
        if self.group is not None:
            raise ValueError("overlapping isolated observation groups")
        self.refs = []
        self.group = {**self.identity, "first_record": self.number + 1, "use": category, "candidate": oid,
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
        self.refs.append(entry["receipt"])
        if self.group["use"] != "protected":
            action = next(r["payload"] for r in reversed(self.journal.rows) if r["kind"] == "action")
            action.setdefault("isolation_receipts", []).append(entry["receipt"])
            self.journal.persist()

    @staticmethod
    def retain(directory):
        entries = []
        for i, path in enumerate(sorted((directory / "isolation").glob("*.json")), 1):
            require(path.name == f"{i:05}.json", "missing original isolation callback")
            entries.append(parse(path.read_bytes()))
        return entries


class Observed:
    def __init__(self, entries, objects, repos, complete, identity, *, missing=()):
        from experiments.e2 import measure
        from experiments.model_isolation import Snapshot
        self.groups, self.snapshots, self.protected = {}, {}, {}
        self.by_action, self.refs = {}, {}
        self.repos, self.missing = repos, missing
        starts, ended, gaps = {}, set(), set()
        previous = None
        for index, row in enumerate(entries, 1):
            group, ref = row["group"], row["receipt"]
            require(all(same(group.get(k), v) for k, v in identity.items()), "observer callback belongs to another case")
            n, oid = group["first_record"], group["candidate"]
            require(type(n) is int and 0 < n <= index and oid in repos, "unbound isolated candidate")
            require(group["use"] in ("protected", "worker", "review", "verifier"), "unregistered observer purpose")
            if n != previous:
                require(n == index, "unbound original isolation group")
                self.groups[n] = group, []
                if group["use"] == "protected":
                    selector = group["selector"]
                    require(type(selector) is dict and selector.keys() == {"labels", "version"},
                            "invalid protected group selector")
                    version, labels = selector["version"], selector["labels"]
                    require(type(version) is int and version in dict(measure.GROUPS) and type(labels) is list
                            and labels and all(type(label) is str for label in labels)
                            and len(set(labels)) == len(labels) and set(labels) <= set(dict(measure.GROUPS)[version]),
                            "unregistered protected group criteria")
                else:
                    attempt = group["preceding_actions"]
                    require(type(attempt) is int and attempt not in self.by_action, "invalid public observer action")
                    self.by_action[attempt] = n
                previous = n
            require(same(self.groups[n][0], group), "observer grouping changed")
            self.refs.setdefault(n, []).append(ref)
            if ref["path"] in missing:
                gaps.add(n)
                continue
            receipt = objects.item(ref)
            require(receipt["candidate"] == oid, "wrong isolated candidate")
            if group["use"] == "protected":
                labels = group["selector"]["labels"]
                require((receipt["mode"] == "legacy" and "legacy/compat" in labels) or
                        (receipt["mode"] == "queue" and set(labels) - {"scope/paths", "legacy/compat"}),
                        "isolated mode differs from the registered protected group")
            if oid not in self.snapshots:
                self.snapshots[oid] = Snapshot.from_git(repos[oid], oid)
            key, phase = receipt["invocation"], receipt["phase"]
            require(key not in ended, "repeated completed observer invocation")
            if phase == "started":
                require(key not in starts, "duplicate observer start")
                starts[key] = n, receipt
            elif phase in ("measured", "unavailable", "interrupted"):
                start_group, started = starts.pop(key, (n, None))
                require(start_group == n and (started or phase != "measured" or n in gaps),
                        "guest result lacks its original start")
                self.groups[n][1].append((started, receipt))
                ended.add(key)
            else:
                raise ValueError("unknown observer callback phase")
        for n, started in starts.values():
            require(not complete or n in gaps, "original isolated invocation has no terminal receipt")
            self.groups[n][1].append((started, None))

    @contextmanager
    def reader(self, group_number, oid, *, mode=None):
        from experiments.model_isolation import read_observation
        pairs = iter(pair for pair in self.groups.get(group_number, ({}, []))[1]
                     if mode is None or (pair[0] or pair[1])["mode"] == mode)
        def probe(snapshot, *, cases=None, legacy=False):
            pair = next(pairs, None)
            require(snapshot.oid == oid, "wrong snapshot for isolated observation")
            if not pair or not all(pair):
                raise MeasurementUnavailable("missing original isolated invocation")
            return read_observation(snapshot, *pair, cases=cases, legacy=legacy)
        yield probe
        require(next(pairs, None) is None, "extra isolated observations in registered group")

    def public(self, category, experiment, repo, action, selector):
        from .observations import public_check
        oid = action["candidate"]
        n = self.by_action.get(action["attempt"])
        require(action.get("isolation_receipts", []) == self.refs.get(n, []),
                "isolated invocation differs from original public action receipts")
        if n is not None:
            group = self.groups[n][0]
            require(group["use"] == category and group["candidate"] == oid and group["selector"] == selector,
                    "public observer ran for another action, phase or subject")
        with self.reader(n, oid) as probe:
            return public_check(category, experiment, repo, oid, self.snapshots.get(oid), selector, probe)

    def protected_report(self, n, oid, label, version, seed, rank):
        from experiments.e2 import measure
        from experiments.model_isolation import Snapshot
        from experiments.model_isolation.observer import protected_reports
        law = measure.parameters(version)
        if label == "scope/paths":
            # Scope has no guest invocation. Its independent authority is the exact Git tree.
            labels, mode = [label], "scope"
        else:
            selected = self.groups[n][0]["selector"]["labels"]
            mode = "legacy" if label == "legacy/compat" else "queue"
            labels = [label] if mode == "legacy" else [name for name in selected if name not in ("scope/paths", "legacy/compat")]
        key = oid, version, n, mode
        if key not in self.protected:
            if oid not in self.snapshots:
                self.snapshots[oid] = Snapshot.from_git(self.repos[oid], oid)
            with self.reader(n, oid, mode=mode) as probe:
                self.protected[key] = protected_reports(self.snapshots[oid], seed, rank, labels=labels, probe=probe, **law)
        return self.protected[key][label]

    def grades(self, rows, seed, rank, *, evidence_issues):
        from experiments.e2 import measure
        grades, seen = {}, set()
        attempts = 0
        for row in rows:
            p = row["payload"]
            if row["kind"] == "action":
                attempts += 1
            if row["kind"] != "oracle":
                continue
            slot = oid, label, version = measure.key(p)
            require(slot not in seen and oid in self.repos, "duplicate or unbound protected observation")
            seen.add(slot)
            n = p["probe_group"]
            require(type(n) is int and n > 0, "invalid protected group number")
            refs = self.refs.get(n, [])
            if label == "scope/paths" and matches_json([], p["probe_receipts"]):
                # A scope-only measurement does not allocate a callback number.
                refs = []
            else:
                group = self.groups[n][0]
                require(group["use"] == "protected" and group["candidate"] == oid
                        and same(group["selector"]["version"], version) and label in group["selector"]["labels"]
                        and same(group["preceding_actions"], attempts), "protected group differs from its oracle publication")
            require(matches_json(refs, p["probe_receipts"]), "protected checkpoint receipts differ")
            try:
                report = self.protected_report(n, oid, label, version, seed, rank)
            except MeasurementUnavailable as error:
                if evidence_issues is None:
                    raise
                evidence_issues.append(f"unmeasured protected criterion {oid} v{version} {label}: {error}")
                continue
            # Both the retained report and a reconstruction must authenticate against the
            # same original address. Its claimed verdict alone is never an observation.
            require(matches_json(report, p["evidence"]), "protected report differs from original isolated observations")
            verdict = measure.grade(report, oid, label, seed, rank, version)
            require(p["expected"] == measure.EXPECTED and p["observed"] == p["verdict"] == verdict,
                    "protected oracle disagrees with independently measured verdict")
            grades[slot] = verdict
            if p["evidence"]["path"] in self.missing:
                evidence_issues.append(f"reconstructed protected criterion {oid} v{version} {label} "
                                       f"at original SHA-256 {p['evidence']['sha256']}")
        return grades


def matches_json(value, ref):
    data = encoded(value)
    checksum = sha(data)
    return same(ref, {"path": f"objects/{checksum}.json", "sha256": checksum, "bytes": len(data)})
