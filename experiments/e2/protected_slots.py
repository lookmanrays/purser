"""Read the registered protected slots from original reports and independently available Git bytes."""

from collections import defaultdict
from dataclasses import dataclass, field

from experiments.e1.measurement import same

from . import measure


@dataclass
class Slot:
    rows: int = 0
    # Keep distinct origins, including a checkpoint whose bytes also appeared after the clock.
    recordings: list = field(default_factory=list)
    issues: list = field(default_factory=list)

    def add(self, source, ref, report):
        self.recordings.append((source, ref, report))

    def original(self):
        if self.rows == 0:
            return "missing"
        return "recorded" if self.rows == 1 and not self.issues else "disputed"


def retained(journal, platform, objects, candidates, seed, rank, issue):
    slots = {(oid, label, version): Slot() for oid in candidates for version, labels in measure.GROUPS for label in labels}
    known_refs = set()

    def find(payload):
        try:
            key = measure.key(payload)
        except ValueError as error:
            issue(str(error))
            return None
        slot = slots.get(key)
        if slot is None:
            issue(f"unregistered or unbound protected observation {key}")
        return slot

    for event in journal:
        if event["kind"] != "oracle":
            continue
        payload = event["payload"]
        slot = find(payload)
        if slot is None:
            continue
        slot.rows += 1
        if slot.rows == 2:
            slot.issues.append("duplicate protected journal row")
        try:
            report = measure.journal_report(payload, objects, seed, rank)
            ref = payload["evidence"]
            slot.add(payload.get("source", "journal"), ref, report)
            known_refs.add(ref["sha256"])
        except Exception as error:
            slot.issues.append(f"invalid protected journal row: {error}")

    checks = (platform.get("checkpoint_measurement") or {}).get("checks", [])
    if type(checks) is not list:
        issue("malformed protected checkpoint checks")
        checks = []
    for check in checks:
        slot = find(dict(check, version=measure.OLD) if type(check) is dict else {})
        if slot is None:
            continue
        try:
            ref = check["evidence"]
            report = objects.item(ref)
            measure.grade(report, check["candidate"], check["criterion"], seed, rank, measure.OLD)
            slot.add("checkpoint", ref, report)
            known_refs.add(ref["sha256"])
        except Exception as error:
            slot.issues.append(f"invalid protected checkpoint report: {error}")

    # These references were published for this case. A free object elsewhere in a pooled
    # ZIP is not evidence of an additional protected run or of candidate membership.
    for ref in objects.references(platform.get("referenced_files")):
        path = ref.get("path")
        if type(path) is not str or not path.endswith(".json") or str(ref.get("sha256")) in known_refs:
            continue
        try:
            report = objects.item(ref)
        except Exception:
            continue  # The outer artifact reader reports damaged or missing references.
        if type(report) is not dict or report.keys() != measure.REPORT_FIELDS:
            continue
        slot = find(report)
        if slot is None:
            continue
        try:
            oid, label, version = measure.key(report)
            measure.grade(report, oid, label, seed, rank, version)
            slot.add("case_stage", ref, report)
            known_refs.add(ref["sha256"])
        except Exception as error:
            slot.issues.append(f"invalid case-bound protected report: {error}")
    for slot in slots.values():
        if slot.rows == 0:
            slot.issues.append("missing protected journal row")
        if len({ref["sha256"] for _, ref, _ in slot.recordings}) > 1:
            slot.issues.append("conflicting original protected reports")
    return slots


def adjudicate(slot, available, requested, measured, error):
    """Preserve the original measurement separately from what can be reproduced."""
    original = slot.original()
    grades = {report["verdict"] for _, _, report in slot.recordings}
    historical = next(iter(grades)) if len(grades) == 1 else None
    source, _, selected = slot.recordings[0] if slot.recordings else (None, None, None)
    replay = "unrequested"
    if not available or requested and measured is None:
        replay = "unavailable"
        reason = "authenticated Git bytes unavailable" if not available else str(error)
        slot.issues.append(f"independent replay unavailable: {reason}")
        selected = None
    elif requested:
        if any(not same(report, measured) for _, _, report in slot.recordings):
            replay = "different"
            source = "replay"
            slot.issues.append("reexecuted protected evidence differs from retained observations")
            if original == "recorded":
                original = "disputed"
        elif selected is None:
            replay, source = "measured", "replay"
        else:
            replay = "matched"
        selected = measured
    observations = [{"source": origin, "sha256": reference["sha256"], "verdict": report["verdict"]}
                    for origin, reference, report in slot.recordings]
    result = {"verdict": selected["verdict"] if selected else None, "source": source, "original": original,
              "historical_verdict": historical, "recordings": observations, "replay": replay, "issues": slot.issues}
    # Checkpoint validation needs an independent post-clock observation for corroboration.
    witness = selected if measured is not None or source == "journal" else None
    return result, witness


def read_slots(journal, platform, objects, candidates, repos, seed, rank, *, rerun, measurements, issue):
    slots = retained(journal, platform, objects, candidates, seed, rank, issue)
    cache = {} if measurements is None else measurements
    verdicts, witnesses, details = {}, {}, defaultdict(list)
    for oid in sorted(candidates):
        repo = repos.get(oid)
        for version, labels in measure.GROUPS:
            requested = [label for label in labels if rerun or slots[oid, label, version].issues]
            measured, failures = {}, {}
            if repo is not None and requested:
                group = cache.setdefault((oid, seed, rank, version), {})
                pending = [label for label in requested if label not in group]
                found, failures = measure.run_group(repo, oid, seed, rank, version, pending)
                group.update(found)
                measured = {label: group[label] for label in requested if label in group}
            for label in labels:
                key = oid, label, version
                result, witness = adjudicate(slots[key], repo is not None, label in requested,
                                              measured.get(label), failures.get(label))
                result.update(criterion=label, version=version)
                details[oid].append(result)
                for problem in result["issues"]:
                    issue(f"protected {oid} v{version} {label}: {problem}")
                if result["verdict"] is not None:
                    verdicts[key] = result["verdict"]
                if witness is not None:
                    witnesses[key] = witness
    return verdicts, witnesses, dict(details)
