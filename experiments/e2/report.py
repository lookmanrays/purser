"""Read the E2 archive independently of the participant and, on request, reexecute its Git witnesses."""

import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from experiments.e1.accounting import PublicHistory, READ_ERRORS, check_exit, journal_prefix
from experiments.e1.artifacts import MANIFEST, REGISTRATION, START, extract_tree, sha
from experiments.e1.executor import LEAVES, PARENT, PARENT_CHECKS
from experiments.e1.measurement import same
from experiments.e1.outcomes import CLOSED, LABELS
from experiments.e1.recovery import input_identity
from experiments.e1.report import GitPacks, Objects, parse, require, wire_valid

from . import evidence, measure, outcomes, policy
from .suite import schedule, validate_freeze

ERRORS = (*READ_ERRORS, OSError, subprocess.SubprocessError, zipfile.BadZipFile)


@dataclass
class Case:
    raw: dict
    facts: dict
    basis: str
    issues: tuple
    comparable_cost: bool = False
    healthy_clean: bool = False


def inspect_case(raw, schema, objects, packs, rank, *, rerun, measurements):
    issues = []
    def issue(message):
        if message not in issues:
            issues.append(message)
    def read(label, getter, default=None):
        try:
            return getter()
        except ERRORS as error:
            issue(f"{label}: {error}")
            return default

    require(wire_valid(raw, schema, schema), "raw record violates frozen schema")
    arm, seed, track = raw["arm"], raw["seed"], raw["track"]
    artifacts = raw["artifacts"]
    journal, ordered = journal_prefix(read("journal", lambda: objects.item(artifacts["journal"])), arm, issue)
    platform = read("platform", lambda: objects.item(artifacts["platform"]), {})
    candidates = read("candidates", lambda: objects.item(artifacts["candidates"]), {})
    require(isinstance(platform, dict) and isinstance(candidates, dict), "unreadable platform or candidate manifest")
    for ref in objects.references([platform, journal]):
        read("referenced bytes", lambda: objects.blob(ref))
    repos = {}
    blobs = {}
    for oid, entry in candidates.items():
        repo = read(f"Git {oid}", lambda: packs.restore(oid, entry, objects))
        if repo is not None:
            repos[oid] = repo
            actual = read(f"Git policy blobs {oid}", lambda: evidence.file_ids(repo, oid))
            if actual is not None:
                blobs[oid] = actual
                declared = platform.get("policy_blobs", {}).get(oid)
                if (declared is not None or raw["validity"] == "complete") and not same(actual, declared):
                    issue(f"retained policy blob identities differ for {oid}")
            if oid != START and entry["parent"] not in candidates:
                issue(f"missing archived parent of {oid}")
    basis, endpoint_ok = check_exit(raw, journal, platform, True, issue)
    exact = basis == "exact" and endpoint_ok and platform.get("participant_exit") is not None
    # A fixture delivery has its own before/after transition and can race its ack in
    # exactly the same way as a worker action. It is never in the charged action count.
    boundaries = [dict(e, kind="action") if e["kind"] == "fault" else e for e in journal]
    history = PublicHistory(arm, True, objects.item, issue)
    audited = history.reconcile(boundaries, platform, incomplete=raw["validity"] != "complete")
    events = [dict(e, kind="fault") if e["kind"] == "action" and e["payload"].get("actor") == "fixture_observer" else e
              for e in audited]
    actual_trace = read("participant", lambda: objects.item(platform["treatment_trace"])) if platform.get("treatment_trace") else None
    if arm == "purser" and actual_trace:
        for digest, ref in actual_trace.get("kernel_artifacts", {}).items():
            if ref.get("sha256") != digest or read("kernel witness", lambda: sha(objects.blob(ref))) != digest:
                issue("kernel witness checksum mismatch")
    faults = [e for e in events if e["kind"] == "fault"]
    if track == "clean" and faults or len(faults) > 1:
        issue("unregistered or duplicate intervention")
    for e in faults:
        p = e["payload"]
        if p.get("trigger") != track or p.get("intervention") != "linear_to_exponential":
            issue("wrong registered intervention")
            p["released"] = None
    for e in events:
        if e["kind"] != "action":
            continue
        p = e["payload"]
        data = p.get("input", {})
        label = data.get("criterion", data.get("open_run"))
        allowed = (LEAVES[p["subject"]],) if p.get("subject") in LEAVES else PARENT_CHECKS
        if label is not None and label not in allowed:
            issue("charged action subject disagrees with criterion operand")
            p["subject"] = None
            ordered = False
        if data.get("assign", p.get("subject")) != p.get("subject") or "adopt" in data and p.get("subject") != "W2":
            issue("charged action subject disagrees with planning operand")
            p["subject"] = None
            ordered = False

    verdicts, witnesses, ambiguous = {}, {}, set()
    for event in events:
        if event["kind"] != "oracle":
            continue
        p = event["payload"]
        key = p.get("candidate"), p.get("criterion"), p.get("version")
        def inspect():
            oid, label, version = key
            require(oid in repos, "oracle lacks authenticated Git candidate")
            report = objects.item(p["evidence"])
            grade = measure.grade(report, oid, label, seed, rank, version)
            require(p["expected"] == "all specified cases pass"
                    and grade == report["verdict"] == p["verdict"] == p["observed"], "retained verdict differs")
            return grade, report
        result = read(f"oracle {key}", inspect)
        if key in witnesses:
            ambiguous.add(key)
            issue(f"duplicate oracle {key}")
        if result is not None:
            verdicts[key], witnesses[key] = result
    for key in ambiguous:
        verdicts.pop(key, None)

    if rerun:
        for oid, repo in repos.items():
            keys = [k for k in witnesses if k[0] == oid]
            for version in sorted({k[2] for k in keys}):
                labels = sorted(k[1] for k in keys if k[2] == version)
                cache_key = (oid, seed, rank, version, tuple(labels))
                if cache_key not in measurements:
                    with extract_tree(repo, oid) as root:
                        measurements[cache_key] = measure.check(repo, oid, Path(root), seed, rank, version, labels)
                for label in labels:
                    key = (oid, label, version)
                    if not same(witnesses[key], measurements[cache_key][label]):
                        issue(f"reexecuted protected evidence differs for {key}")
                        verdicts.pop(key, None)

    if type(platform.get("public_history_complete")) is not bool:
        issue("public history certainty missing")
    whole = ordered and history.complete and history.final is not None and platform.get("public_history_complete") is True
    end = platform.get("exit_candidate") or {}
    if end:
        tips = set(repos) - {c["parent"] for c in candidates.values()}
        status = end.get("status_hex")
        if (tips != {end.get("head")} or type(status) is not str or re.fullmatch(r"(?:[0-9a-f]{2})*", status) is None
                or type(end.get("dirty")) is not bool or end["dirty"] != bool(status)):
            issue("exit candidate differs from the sealed Git tip or measured worktree state")
            end = {}
    if raw["terminal"] == "done" and history.final and history.final[PARENT][1] != end.get("head"):
        issue("done exit disagrees with the participant's final parent candidate")
        end = {}
    platform = dict(platform, policy_blobs=blobs, exit_candidate=end)
    facts = evidence.score(events, track, arm, seed, history.checkpoint, objects.item, actual_trace, verdicts, platform,
                           endpoint_known=exact, history_complete=whole, action_sequence_complete=ordered,
                           known_candidates=repos, issue=issue)
    facts["charged_attempts_observed"] = sum(e["kind"] == "action" for e in journal)
    expected = outcomes.raw_score(facts) if raw["validity"] != "infrastructure_invalid" and track != "clean" else None
    if not same(expected, raw["score"]):
        issue("frozen raw score differs from actual participant and protected observations")
    if raw["validity"] == "complete":
        if (not whole or not exact or not history.final or history.final[PARENT][0] not in CLOSED
                or facts["final_pass"] is None or track != "clean" and (expected is None or facts["intervention_reached"] is not True)):
            issue("completed run lacks its registered endpoint or checkpoint evidence")
        for oid in set(candidates) - {START}:
            if any((oid, label, version) not in verdicts for version, labels in ((measure.OLD, LABELS), (measure.NEW, measure.REVISED))
                   for label in labels):
                issue(f"completed run lacks a protected candidate observation for {oid}")
    healthy = (track == "clean" and exact and ordered and whole and raw["terminal"] == "done"
               and history.final[PARENT][0] in CLOSED and history.final[PARENT][1] == end.get("head")
               and facts["final_pass"] is True)
    return Case(raw, facts, basis, tuple(issues), comparable_cost=exact and ordered, healthy_clean=healthy)


def inspect_series(directory, *, strict=False, rerun=False):
    result, measurements = {}, {}
    schema = parse((REGISTRATION / "raw.schema.json").read_bytes())
    with zipfile.ZipFile(directory / "raw.zip") as archive, tempfile.TemporaryDirectory(prefix="purser-e2-readback-") as tmp:
        names = archive.namelist()
        require(len(names) == len(set(names)), "duplicate archive member")
        registration = archive.read("freeze.json")
        if (directory / "freeze.json").exists():
            require((directory / "freeze.json").read_bytes() == registration, "detached registration differs")
        freeze = parse(registration)
        replicate = validate_freeze(freeze)
        units = list(schedule())
        suffixes = [f"{t}/{s}/{a}.json" for t, s, _, a in units]
        for name in names:
            if name == "freeze.json":
                continue
            prefix, _, tail = name.partition("/")
            if prefix in ("runs", "started"):
                require(tail in suffixes, "unregistered run identity")
            else:
                require(re.fullmatch(r"objects/[0-9a-f]{64}\.(json|bin|pack)", name), "invalid object member name")
                if strict:
                    require(sha(archive.read(name)) == tail.split(".")[0], "incorrect object address")
        starts = [n.removeprefix("started/") for n in names if n.startswith("started/")]
        require(starts == [s for s in suffixes if s in starts], "registered cases started out of order")
        objects, packs = Objects(archive), GitPacks(Path(tmp))
        for (track, seed, rank, arm), suffix in zip(units, suffixes, strict=True):
            marker, name = "started/" + suffix, "runs/" + suffix
            if marker not in names and name not in names:
                continue
            try:
                require(marker in names and name in names, "started case or result missing")
                require(same(parse(archive.read(marker)), {"track": track, "arm": arm, "rank": rank, "seed": seed}),
                        "incorrect start marker")
                raw = parse(archive.read(name))
                require(same([raw[k] for k in ("suite", "experiment", "mode", "track", "seed", "arm", "replicate")],
                             [MANIFEST["id"], "E2", "scripted", track, seed, arm, replicate]), "raw unit identity mismatch")
                require(same(raw["inputs"], input_identity(freeze, arm)), "raw input pins differ from registration")
                case = inspect_case(raw, schema, objects, packs, rank, rerun=rerun, measurements=measurements)
            except ERRORS as error:
                if strict:
                    raise ValueError(f"{suffix}: {error}") from error
                case = Case({"terminal": "unavailable", "validity": "unavailable", "cost": {}},
                            dict.fromkeys(outcomes.FIELDS), "unavailable", (str(error),))
            if strict and case.issues:
                raise ValueError(f"{suffix}: {' | '.join(case.issues)}")
            result[track, seed, arm] = case
        stats = {"records": len(result), "git_packs": len(packs.repos),
                 "reexecuted_seed_candidate_versions": len(measurements),
                 "missing_registered": len(units) - len(result)}
    return result, stats


def render(directory):
    from .summary import render_summary
    return render_summary(inspect_series(directory)[0])


def verify_candidates(directory):
    _, stats = inspect_series(directory, strict=True, rerun=True)
    return stats
