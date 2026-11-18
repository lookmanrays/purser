"""Protected observations for the old and changed task on exact retained Git commits."""

from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path

from experiments.e1 import scorer
from experiments.e1.accounting import digest
from experiments.e1.artifacts import extract_tree
from experiments.e1.recovery import FileArchive
from experiments.e1.report import Objects, parse

OLD = 1
NEW = 2
REVISED = ("W2/retry", "queue/composition")
GROUPS = ((OLD, scorer.LABELS), (NEW, REVISED))
EXPECTED = "all specified cases pass"
REPORT_FIELDS = {"criterion", "version", "candidate", "cases", "verdict"}


def exponential_offsets(count, base):
    return [0] + [base * 2 ** (k - 1) for k in range(1, count)]


def parameters(version):
    if type(version) is not int or version not in (OLD, NEW):
        raise ValueError("unknown protected E2 criterion version")
    return {"offsets": scorer.linear_offsets if version == OLD else exponential_offsets, "version": version}


def check(repo, oid, root, seed, rank, version, labels):
    return scorer.measure(repo, oid, root, seed, rank, labels=labels, **parameters(version))


def key(payload):
    if type(payload) is dict:
        oid, label, version = (payload.get(name) for name in ("candidate", "criterion", "version"))
        if digest(oid, 40) and type(label) is str and type(version) is int:
            return oid, label, version
    raise ValueError("malformed protected observation identity")


def grade(report, oid, label, seed, rank, version):
    if type(report) is not dict or report.keys() != REPORT_FIELDS or key(report) != (oid, label, version):
        raise ValueError("protected report identity or fields differ")
    if label not in (scorer.LABELS if version == OLD else REVISED):
        raise ValueError("unregistered protected E2 criterion")
    verdict = scorer.verify_report(report, oid, label, seed, rank, **parameters(version))
    if report["verdict"] != verdict:
        raise ValueError("retained protected verdict differs from its observations")
    return verdict


def journal_report(payload, objects, seed, rank):
    oid, label, version = key(payload)
    report = objects.item(payload["evidence"])
    verdict = grade(report, oid, label, seed, rank, version)
    if (payload.get("expected") != EXPECTED or payload.get("observed") != verdict
            or payload.get("verdict") != verdict or payload.get("source", "journal") not in ("journal", "case_stage")):
        raise ValueError("retained oracle row disagrees with its protected report")
    return report


def run_group(repo, oid, seed, rank, version, labels):
    """Return every obtainable registered report, identifying each unavailable probe."""
    labels = list(labels)
    reports, failures = {}, {}
    if not labels:
        return reports, failures
    with ExitStack() as cleanup:
        root = None
        if any(label != "scope/paths" for label in labels):
            try:
                root = Path(cleanup.enter_context(extract_tree(repo, oid)))
            except Exception as error:
                failures.update((label, error) for label in labels if label != "scope/paths")
                labels = [label for label in labels if label == "scope/paths"]

        def run(selected):
            try:
                measured = check(repo, oid, root, seed, rank, version, selected)
            except Exception as error:
                failures.update((label, error) for label in selected)
                return
            for label in selected:
                try:
                    report = measured[label]
                    grade(report, oid, label, seed, rank, version)
                    reports[label] = report
                    failures.pop(label, None)
                except Exception as error:
                    failures[label] = error

        if labels:
            run(labels)
        # A batch-level failure (e.g. a missing legacy runner) need not affect its peers.
        if len(labels) > 1:
            for label in labels:
                if label not in reports:
                    run([label])
    return reports, failures


def stage_reports(assets, seed, rank):
    """Recover uniquely addressed protected bytes from this attempt before ZIP pooling."""
    archive = assets.archive
    if not isinstance(archive, FileArchive):
        raise ValueError("protected object discovery requires a case-local staging directory")
    found = defaultdict(list)
    for name in archive.namelist():
        if not name.endswith(".json"):
            continue
        try:
            raw = archive.read(name)
            report = parse(raw)
            if type(report) is not dict or report.keys() != REPORT_FIELDS:
                continue
            oid, label, version = key(report)
            grade(report, oid, label, seed, rank, version)
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            # Unattributed or damaged stage bytes cannot establish an observation.
            continue
        ref = {"path": name, "sha256": Path(name).stem, "bytes": len(raw)}
        found[oid, label, version].append((ref, report))
    return found


def collect(repo, candidates, seed, rank, assets, references, events, emit, issue, *, stage_reports=None):
    objects = Objects(assets.archive)
    existing = set()
    for event in events:
        if event.get("kind") != "oracle":
            continue
        payload = event.get("payload")
        try:
            journal_report(payload, objects, seed, rank)
            existing.add(key(payload))
        except Exception as error:
            issue("retained protected observation", error)

    def record(report, source, ref=None):
        oid, label, version = key(report)
        try:
            if ref is None:
                ref = assets.put(report, "json")
            references.append(ref)
            payload = {"candidate": oid, "criterion": label, "version": version, "expected": EXPECTED,
                       "observed": report["verdict"], "verdict": report["verdict"], "evidence": ref}
            if source == "case_stage":
                payload["source"] = source
            emit("oracle", payload)
        except Exception as error:
            issue(f"record protected observation {oid} v{version} {label}", error)

    for oid in sorted(set(candidates)):
        for version, labels in GROUPS:
            pending = []
            for label in labels:
                slot = oid, label, version
                if slot in existing:
                    continue
                retained = (stage_reports or {}).get(slot, [])
                if len(retained) == 1:
                    ref, report = retained[0]
                    record(report, "case_stage", ref)
                else:
                    if retained:
                        references.extend(ref for ref, _ in retained)
                        issue(f"protected observation {oid} v{version} {label}", ValueError("conflicting case-stage reports"))
                    pending.append(label)
            if repo is None:
                continue  # Case-bound historical reports can survive loss of all Git copies.
            reports, failures = run_group(repo, oid, seed, rank, version, pending)
            for label, error in failures.items():
                issue(f"protected observation {oid} v{version} {label}", error)
            for report in reports.values():
                record(report, "journal")
