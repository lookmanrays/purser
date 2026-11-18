"""Read the E2 archive independently of the participant and, on request, reexecute its Git witnesses."""

import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from experiments.e1.accounting import READ_ERRORS, check_exit, journal_prefix, utc_time
from experiments.e1.artifacts import MANIFEST, REGISTRATION, START, encoded, extract_tree, sha
from experiments.e1.executor import LEAVES, PARENT, PARENT_CHECKS
from experiments.e1.measurement import same
from experiments.e1.outcomes import CLOSED, LABELS
from experiments.e1.recovery import input_identity
from experiments.e1.report import GitPacks, Objects, parse, require, wire_valid

from . import checkpoint, clock, evidence, measure, outcomes, policy
from .public_history import read_participant
from .suite import schedule, validate_freeze
from .views import CorroboratedViews

ERRORS = (*READ_ERRORS, OSError, subprocess.SubprocessError, zipfile.BadZipFile)


@dataclass
class Case:
    raw: dict
    facts: dict
    basis: str
    issues: tuple
    comparable_steps: bool = False
    comparable_elapsed: bool = False
    healthy_clean: bool = False

    @property
    def comparable_cost(self):
        return self.comparable_steps and self.comparable_elapsed


def inspect_case(raw, schema, objects, packs, rank, *, rerun, measurements):
    require(wire_valid(raw, schema, schema), "raw record violates frozen schema")
    case = derive_case(raw, objects, packs, rank, rerun=rerun, measurements=measurements)
    expected = outcomes.raw_score(case.facts) if raw["validity"] != "infrastructure_invalid" and raw["track"] != "clean" else None
    if not same(expected,raw["score"]):
        case.issues += ("frozen raw score differs from actual participant and protected observations",)
    return case


def derive_case(raw, objects, packs, rank, *, rerun=False, measurements=None):
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

    arm, seed, track = raw["arm"], raw["seed"], raw["track"]
    artifacts = raw["artifacts"]
    journal, ordered = journal_prefix(read("journal", lambda: objects.item(artifacts["journal"])), arm, issue)
    platform = read("platform", lambda: objects.item(artifacts["platform"]), {})
    candidates = read("candidates", lambda: objects.item(artifacts["candidates"]), {})
    require(isinstance(platform, dict) and isinstance(candidates, dict), "unreadable platform or candidate manifest")
    for ref in objects.references([platform, journal]):
        read("referenced bytes", lambda: objects.blob(ref))
    views = CorroboratedViews(arm,objects,journal,platform,issue)
    journal = views.events(journal)
    platform = dict(platform)
    for key in ("initial_public","final_public"):
        if key in platform:
            platform[key] = views.checkpoint(platform[key])
    if platform.get("checkpoint_measurement"):
        measurement = dict(platform["checkpoint_measurement"])
        measurement["checkpoint"] = views.checkpoint(measurement.get("checkpoint"))
        platform["checkpoint_measurement"] = measurement
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
    pause_ok = read("participant scoring pause", lambda: (clock.validate(platform, journal, raw), True)[1], False)
    duration = clock.active_ns if pause_ok else lambda start, end, facts: None
    basis, endpoint_ok = check_exit(raw, journal, platform, True, issue, duration=duration)
    history = read_participant(arm, journal, platform, views.item, issue)
    events = history.journal
    quiet = read("physical scoring interval", lambda: clock.quiescence(platform, history.timeline,
                ordered and history.public_complete and history.actions_complete)) if pause_ok else None
    if quiet is not True:
        issue("participant activity overlaps the protected scoring interval" if quiet is False
              else "quiescent scoring interval cannot be proven")
    receipt = platform.get("participant_exit") or {}
    observed_exit = (receipt.get("reason") in ("done","blocked","idle","time_limit","step_limit")
                     and type(receipt.get("end_monotonic_ns")) is int
                     and receipt["end_monotonic_ns"] == platform.get("end_monotonic_ns")
                     and utc_time(receipt.get("end_utc")) is not None and receipt["end_utc"] == platform.get("end_utc"))
    exact = basis == "exact" and endpoint_ok and observed_exit
    boundaries = exact and ordered and history.actions_complete and history.public_complete
    timed = quiet is True
    in_budget = timed or clock.budget_bounded(platform, receipt.get("reason"))
    comparable_steps = boundaries and in_budget
    comparable_elapsed = boundaries and timed
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
    if track == "clean" and history.clean_exposure is not True:
        issue("clean member received a changed premise or instructions" if history.clean_exposure is False
              else "clean assignment throughout the participant history cannot be proven")
    verdicts, witnesses, ambiguous = {}, {}, set()
    for event in journal:
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

    pause = platform.get("checkpoint_measurement")
    admitted = None
    if pause:
        checked = read("old checkpoint qualification", lambda: (
            checkpoint.validate(pause, history.checkpoint(pause["checkpoint"]), track, seed, rank, objects, blobs,
                                witnesses, complete=raw["validity"] == "complete"), True)[1], False)
        if checked:
            admitted = pause.get("qualified")
    delivered = history.fault_receipt
    if faults:
        incoming = faults[0]["payload"]
        bulletin = policy.notice(seed)
        exact_input = same(incoming.get("released"), bulletin) and incoming.get("released_sha256") == sha(encoded(bulletin))
        if not exact_input or incoming.get("seed") != seed:
            issue("incoming content does not establish the registered premise and seed")
            delivered = None
        if delivered is True and track != "clean":
            actual = incoming.get("before")
            expected = (pause or {}).get("checkpoint")
            if not expected or not same(expected, incoming.get("required_before")) or not same(actual,expected):
                issue("actual public premise did not follow the privately qualified predecessor")
                admitted = None
    whole = history.public_complete and history.final is not None
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
    final_subject = (observed_exit and end.get("source") == "after_participant_exit"
                     and utc_time(end.get("observed_utc")) is not None
                     and utc_time(end["observed_utc"]) >= utc_time(receipt["end_utc"]))
    facts = evidence.score(history, track, verdicts, platform, release=delivered, qualification=admitted,
                           endpoint_known=observed_exit, step_boundaries=comparable_steps, final_subject=final_subject,
                           known_candidates=repos)
    facts.update(physical_exit=observed_exit, clock_pause_quiescent=quiet, complete_action_sequence=history.actions_complete,
                 complete_public_history=whole, receipt_complete=history.receipt_complete)
    expected = outcomes.raw_score(facts) if raw["validity"] != "infrastructure_invalid" and track != "clean" else None
    if raw["validity"] == "complete":
        if (not whole or not exact or not history.final or history.final[PARENT][0] not in CLOSED
                or facts["final_pass"] is None or track != "clean" and (expected is None or facts["intervention_reached"] is not True)):
            issue("completed run lacks its registered endpoint or checkpoint evidence")
        for oid in candidates:
            if any((oid, label, version) not in verdicts for version, labels in ((measure.OLD, LABELS), (measure.NEW, measure.REVISED))
                   for label in labels):
                issue(f"completed run lacks a protected candidate observation for {oid}")
    healthy = (track == "clean" and history.clean_exposure is True and whole and observed_exit and raw["terminal"] == "done"
               and history.final[PARENT][0] in CLOSED and history.final[PARENT][1] == end.get("head")
               and facts["final_pass"] is True)
    return Case(raw, facts, basis, tuple(issues), comparable_steps, comparable_elapsed, healthy)


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
