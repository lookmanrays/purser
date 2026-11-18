"""Protected observations for the old and changed task on exact retained Git commits."""

from pathlib import Path

from experiments.e1 import scorer
from experiments.e1.artifacts import START, extract_tree

OLD = 1
NEW = 2
REVISED = ("W2/retry", "queue/composition")


def exponential_offsets(count, base):
    return [0] + [base * 2 ** (k - 1) for k in range(1, count)]


def parameters(version):
    if type(version) is not int or version not in (OLD, NEW):
        raise ValueError("unknown protected E2 criterion version")
    return {"offsets": scorer.linear_offsets if version == OLD else exponential_offsets, "version": version}


def check(repo, oid, root, seed, rank, version, labels):
    return scorer.measure(repo, oid, root, seed, rank, labels=labels, **parameters(version))


def grade(report, oid, label, seed, rank, version):
    if label not in (scorer.LABELS if version == OLD else REVISED):
        raise ValueError("unregistered protected E2 criterion")
    return scorer.verify_report(report, oid, label, seed, rank, **parameters(version))


def collect(repo, candidates, seed, rank, assets, references, events, emit, issue):
    existing = {(p["candidate"], p["criterion"], p["version"]) for event in events
                if event["kind"] == "oracle" for p in [event["payload"]]}
    for oid in sorted(set(candidates) - {START}):
        try:
            with extract_tree(repo, oid) as tree:
                for version, labels in ((OLD, scorer.LABELS), (NEW, REVISED)):
                    pending = [label for label in labels if (oid, label, version) not in existing]
                    if not pending:
                        continue
                    try:
                        reports = check(repo, oid, Path(tree), seed, rank, version, pending)
                    except Exception:
                        # One missing probe cannot erase a surviving independent measurement.
                        reports = {}
                        for label in pending:
                            try:
                                reports.update(check(repo, oid, Path(tree), seed, rank, version, [label]))
                            except Exception as error:
                                issue(f"protected observation {oid} v{version} {label}", error)
                    for label, report in reports.items():
                        ref = assets.put(report, "json")
                        references.append(ref)
                        emit("oracle", {"candidate": oid, "criterion": label, "version": version,
                                        "expected": "all specified cases pass", "observed": report["verdict"],
                                        "verdict": report["verdict"], "evidence": ref})
        except Exception as error:
            issue(f"export candidate {oid}", error)
