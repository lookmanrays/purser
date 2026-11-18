"""Read the E2 archive independently of the participant and, on request, reexecute its Git witnesses."""

import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from experiments.e1.accounting import EXITS, READ_ERRORS, check_exit, digest, journal_prefix, utc_time
from experiments.e1.artifacts import MANIFEST, REGISTRATION, START, encoded, sha
from experiments.e1.executor import PARENT
from experiments.e1.measurement import same
from experiments.e1.outcomes import CLOSED
from experiments.e1.recovery import input_identity
from experiments.e1.report import GitPacks, Objects, parse, require, wire_valid

from . import candidate_evidence, checkpoint, clock, evidence, measure, operations, outcomes, policy
from .public_history import read_participant
from .public_sources import identity
from .suite import schedule, validate_freeze
from .trial import kernel_witnesses
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
    unavailable: tuple = ()

    @property
    def comparable_cost(self):
        return self.comparable_steps and self.comparable_elapsed


def inspect_case(raw, schema, objects, packs, rank, *, rerun, measurements, producer_files=None):
    case = derive_case(raw, objects, packs, rank, rerun=rerun, measurements=measurements, producer_files=producer_files)
    expected = outcomes.raw_score(case.facts) if raw.get("validity") != "infrastructure_invalid" and raw["track"] != "clean" else None
    if not wire_valid(raw, schema, schema):
        case.issues += ("raw record violates frozen schema",)
    if not same(expected, raw.get("score")):
        case.issues += ("frozen raw score differs from actual participant and protected observations",)
    return case


def measured_exit(platform):
    receipt = platform.get("participant_exit")
    if type(receipt) is not dict or receipt.get("reason") not in ("done", "blocked", "idle", "time_limit", "step_limit"):
        return None
    end = receipt.get("end_monotonic_ns")
    if type(end) is not int or end < 0 or utc_time(receipt.get("end_utc")) is None:
        return None
    return receipt


def cost_evidence(raw, journal, platform, history, pause_valid, exclusion, issue):
    """Adjudicate each cost by its own physical boundaries and the observed participant exit."""
    receipt = measured_exit(platform)
    cost = raw.get("cost") if type(raw.get("cost")) is dict else {}
    declared = raw.get("terminal")
    reason = receipt["reason"] if receipt else None
    terminal = [row for row in journal if row["kind"] == "terminal"]
    last = terminal[0]["payload"] if len(terminal) == 1 and terminal[0] is journal[-1] else {}
    if declared == "harness_failure":
        missing = last.get("missing")
        explained = type(missing) is str and bool(missing.strip()) and missing == platform.get("failure")
    else:
        explained = (reason == declared and last.get("missing", True) is None
                     and platform.get("failure", True) is None and not platform.get("recovery_issues"))
    reported_exit = (type(declared) is str and declared in EXITS and EXITS[declared] == raw.get("validity")
                     and declared == platform.get("budget_reason") == last.get("end_reason")
                     and explained and last.get("human_action", True) is None)
    start, end = platform.get("start_monotonic_ns"), platform.get("end_monotonic_ns")
    began, ended = utc_time(platform.get("start_utc")), utc_time(platform.get("end_utc"))
    stopped = (receipt is not None and platform.get("clock_stopped") is True
               and platform.get("cost_basis") == last.get("cost_basis") == "exact"
               and type(end) is int and same(receipt["end_monotonic_ns"], end)
               and same(last.get("end_monotonic_ns"), end)
               and platform.get("end_utc") == last.get("end_utc") == receipt["end_utc"])
    interval = bool(type(start) is int and type(end) is int and 0 <= start <= end and began and ended and began <= ended)
    physical_conflict = interval and any(type(moment) is int and not start <= moment <= end
                                         for action in history.timeline
                                         for moment in (action.get("issued_monotonic_ns"), action.get("finished_monotonic_ns")))
    if physical_conflict:
        issue("witnessed public work lies outside the recorded physical release and stop")

    elapsed = clock.active_ns(start, end, platform) if interval and pause_valid and exclusion is True else None
    lower = elapsed
    if lower is None and interval and pause_valid:
        pause = platform.get("checkpoint_measurement") or {}
        left, right = pause.get("start_monotonic_ns", end), pause.get("end_monotonic_ns")
        lower = end - start - max(0, min(end, right if right is not None else end) - max(start, left))
    limit = MANIFEST["budgets"]["scripted"]["max_steps"]
    count = history.action_lower_bound
    within_steps = count <= limit and (reason != "step_limit" or count == limit)
    if count > limit:
        issue("charged public work exceeded the registered admission ceiling")
    timed = elapsed is not None and (elapsed >= clock.LIMIT if reason == "time_limit" else elapsed <= clock.LIMIT)
    credible_bounds = bool(interval and clock.budget_bounded(platform, reason)) if pause_valid else (
        bool(interval and reason in ("done", "blocked", "idle", "step_limit") and end - start <= clock.LIMIT))
    issued = same(platform.get("work_admission_version"), 2) and history.issued_coverage
    event_conflict = False
    if elapsed is not None:
        for entry in journal:
            offline = (entry["kind"] in ("terminal", "oracle")
                       or entry["kind"] == "public_state" and entry["payload"].get("recovery_read_utc"))
            if entry["kind"] != "public_gap" and (entry["offset_ns"] < elapsed if offline else entry["offset_ns"] > elapsed):
                event_conflict = True
                break
    endpoint = bool(stopped and reported_exit and within_steps and not physical_conflict and not event_conflict)
    work_cutoff = bool(endpoint and (timed or credible_bounds))
    exact_steps = bool(work_cutoff and issued and same(cost.get("steps"), count))
    exact_elapsed = bool(endpoint and timed and same(elapsed, cost.get("elapsed_ns")))
    return {"step_comparable": exact_steps, "elapsed_comparable": exact_elapsed,
            "witnessed_elapsed_ns": elapsed if not physical_conflict and not event_conflict else None,
            "active_lower_ns": lower if not physical_conflict and not event_conflict else None,
            "physical_wall_ns": end - start if interval else None,
            "admission_ceiling_verified": bool(issued and count <= limit), "physical_exit": receipt is not None,
            "work_cutoff": work_cutoff}


def registered_ingress(incoming, track, seed, pause, history):
    message = policy.notice(seed)
    expected = (pause or {}).get("checkpoint")
    actual = incoming.get("before")
    expected_id = identity(history.arm, expected)
    return (same(incoming.get("released"), message) and incoming.get("released_sha256") == sha(encoded(message))
            and same(incoming.get("seed"), seed) and incoming.get("observation_id") == incoming.get("attempt") == message["id"]
            and incoming.get("subject") == "W2" and incoming.get("actor") == "fixture_observer"
            and incoming.get("trigger") == track and incoming.get("intervention") == "linear_to_exponential"
            and expected_id is not None and expected_id == identity(history.arm, actual)
            and expected_id == identity(history.arm, incoming.get("required_before"))
            and (pause or {}).get("candidate") == incoming.get("candidate"))


def derive_case(raw, objects, packs, rank, *, rerun=False, measurements=None, producer_files=None):
    issues, unavailable = [], []
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
    def absence(message):
        if message not in unavailable:
            unavailable.append(message)
    missing = absence if raw.get("validity") == "infrastructure_invalid" and raw.get("terminal") == "harness_failure" else issue
    artifacts = raw.get("artifacts")
    artifacts = artifacts if type(artifacts) is dict else {}
    def mapping(key):
        obj = read(key, lambda: objects.item(artifacts[key]))
        if type(obj) is not dict:
            issue(f"unreadable {key} manifest")
            return {}
        return obj
    retained_journal = read("journal", lambda: objects.item(artifacts["journal"]))
    journal, _ = journal_prefix(retained_journal, arm, issue, allowed_actions=operations.ACTIONS)
    platform = mapping("platform")
    candidates = mapping("candidates")
    if platform.get("checkpoint_measurement") is not None and type(platform["checkpoint_measurement"]) is not dict:
        issue("malformed private checkpoint measurement")
        platform = dict(platform, checkpoint_measurement={})
    cost = raw.get("cost")
    if type(cost) is not dict:
        issue("unavailable raw cost fields")
    elif any(not same(cost.get(key), 0) for key in ("model_calls", "input_tokens", "output_tokens", "spend_usd_micros")):
        issue("scripted cost includes unavailable or nonzero model resources")
    if artifacts.get("model") is not None:
        issue("unexpected model artifact on a scripted case")
    views = CorroboratedViews(arm, objects, journal, platform, issue, unavailable=missing)
    known_members = set(objects.archive.namelist())
    for ref in objects.references([platform, journal]):
        # A recorded unavailable view can be restored byte-for-byte from the same original head.
        if (type(ref.get("path")) is str and ref["path"] not in known_members and encoded(ref) in views.derived):
            continue
        read("referenced bytes", lambda: objects.blob(ref))
    journal = views.events(journal)
    platform = dict(platform)
    for key in ("initial_public","final_public"):
        if key in platform:
            platform[key] = views.checkpoint(platform[key])
    if platform.get("checkpoint_measurement"):
        measurement = dict(platform["checkpoint_measurement"])
        measurement["checkpoint"] = views.checkpoint(measurement.get("checkpoint"))
        platform["checkpoint_measurement"] = measurement
    problems = platform.get("git_evidence_issues", [])
    if type(problems) is list:
        for problem in problems:
            issue(f"captured Git evidence: {problem}")
    else:
        issue("malformed captured Git evidence issues")
    sealed_producer = producer_files is None or "experiments/e2/candidate_evidence.py" in producer_files
    lost = platform.get("unavailable_candidate_oids", [])
    if type(lost) is not list or any(not digest(oid, 40) for oid in lost):
        issue("malformed captured unavailable candidate identities")
        lost = []
    original = None
    if platform.get("original_candidate_map") is not None:
        if sealed_producer and ((type(problems) is list and problems) or lost):
            original = read("original candidate map", lambda: objects.item(platform["original_candidate_map"]))
        else:
            issue("original candidate map lacks registered pre-recovery provenance")
    catalog = candidate_evidence.Catalog(objects, packs)
    named = candidate_evidence.original_oids(
        arm, platform, objects, views.originals, journal, platform.get("start_monotonic_ns"), issue,
        lookup=catalog.repository_for, seed=seed,
        native_producer=producer_files is None or "experiments/e2/git_reads.py" in producer_files)
    case_candidates, _, repos = candidate_evidence.reconcile(
        catalog, candidates, platform.get("case_git_packs"), named, issue, original_map=original,
        references=platform.get("referenced_files"), unavailable=missing,
        expect_seals=sealed_producer)
    for oid in lost:
        if oid not in case_candidates:
            issue(f"captured unavailable candidate {oid} lacks an independent case observation")
    blobs = {}
    for oid, repo in repos.items():
        actual = read(f"Git policy blobs {oid}", lambda: evidence.file_ids(repo, oid))
        if actual is not None:
            blobs[oid] = actual
            declared = read("declared policy blobs", lambda: platform.get("policy_blobs", {}).get(oid))
            if (declared is not None or raw.get("validity") == "complete") and not same(actual, declared):
                issue(f"retained policy blob identities differ for {oid}")
    def validate_pause():
        clock.validate(platform, journal, raw)
        return True
    pause_ok = read("participant scoring pause", validate_pause, False)
    duration = clock.active_ns if pause_ok else lambda start, end, facts: None
    basis, _ = read("terminal accounting", lambda: check_exit(raw, journal, platform, True, issue, duration=duration,
                                                              before_release_oracles=True),
                    ("unavailable", False))
    history = read_participant(arm, journal, platform, views.item, issue, originals=views.originals,
                               track=track, seed=seed, unavailable=missing)
    events = history.journal
    quiet = accounted = None
    if pause_ok:
        credit_end = (platform.get("checkpoint_measurement") or {}).get("end_monotonic_ns")
        pause_issues = history.issues_through(credit_end)
        quiet = read("physical scoring interval", lambda: clock.quiescence(platform, history.timeline, pause_issues))
        accounted = read("active scoring interval", lambda: clock.exclusion_accounted(platform, history.timeline,
                                                                                      pause_issues))
        if accounted is False:
            issue("participant activity occurred inside the deducted scoring interval")
        elif accounted is None:
            absence("physical evidence for the deducted scoring interval is incomplete")
    costs = cost_evidence(raw, journal, platform, history, pause_ok, accounted, issue)
    observed_exit = costs["physical_exit"]
    receipt = measured_exit(platform) or {}
    actual_trace = history.source.trace
    if arm == "purser" and type(actual_trace) is dict:
        retained = read("kernel artifacts", lambda: dict(actual_trace["kernel_artifacts"]), {})
        disclosed = read("missing kernel artifacts", lambda: dict(actual_trace.get("missing_kernel_artifacts", {})), {})
        for checksum, ref in retained.items():
            try:
                require(sha(objects.blob(ref)) == checksum, "kernel witness checksum mismatch")
            except ERRORS as error:
                history.source.read_failure("kernel witness", error)
        if history.public_complete:
            expected = read("original kernel witness digests", lambda: kernel_witnesses(actual_trace["snapshot"]), set())
            recovered = {key[1] for key in objects.content}
            for checksum in sorted(expected - retained.keys()):
                if checksum not in disclosed:
                    issue(f"original kernel witness lacks its retained object or missing-evidence reason: {checksum}")
                elif checksum not in recovered:
                    missing(f"original kernel witness unavailable: {checksum}")
    faults = [e for e in events if e["kind"] == "fault"]
    if track == "clean" and faults or len(faults) > 1:
        issue("unregistered or duplicate intervention")
    if track == "clean" and history.clean_exposure is not True:
        if history.clean_exposure is False:
            issue("clean member received a changed premise or instructions")
        else:
            missing("clean assignment throughout the participant history cannot be proven")
    from .protected_slots import read_slots
    historical_protected = []
    verdicts, witnesses, protected_slots = read_slots(
        journal, platform, objects, case_candidates, repos, seed, rank,
        rerun=rerun, measurements=measurements, issue=issue, historical_only=historical_protected, unavailable=missing)

    pause = platform.get("checkpoint_measurement")
    admitted = submitted = None
    if pause:
        submitted = evidence.submitted_at(history, pause.get("checkpoint"), "W2", pause.get("candidate"),
                                          pause.get("submission_attempt"))
        old = checkpoint.old_assignment(history, pause.get("checkpoint"), pause.get("submission_attempt"))
        def validate_checkpoint():
            return checkpoint.validate(pause, history.checkpoint(pause["checkpoint"]), track, seed, rank, objects,
                                       blobs, witnesses, complete=raw.get("validity") == "complete",
                                       submitted=submitted, old_assignment=old, issue=issue, unavailable=missing)
        admitted = read("old checkpoint qualification", validate_checkpoint)
    old_qualified = admitted
    delivered = history.fault_receipt
    if history.first_delivered is not None and track != "clean":
        if not registered_ingress(history.first_delivered, track, seed, pause, history):
            issue("actual premise lacks its registered identity, trigger or privately qualified predecessor")
            admitted = None
        if history.prior_changed_exposure is True:
            issue("the participant saw the revised task before the registered premise")
            admitted = False
        elif history.prior_changed_exposure is None:
            missing("old-task exposure before the registered premise cannot be established")
            admitted = None
    elif delivered is True:
        admitted = None
    whole = history.public_complete and history.final is not None
    end = platform.get("exit_candidate")
    if end is not None:
        def exiting_worktree():
            return candidate_evidence.checkout_observation(end)
        end = read("exit candidate", exiting_worktree, {})
    end = end or {}
    final_subject = bool(observed_exit and end.get("source") == "after_participant_exit"
                         and utc_time(end["observed_utc"]) >= utc_time(receipt["end_utc"]))
    closed_parent = bool(whole and history.final[PARENT][0] in CLOSED and history.final[PARENT][1] == end.get("head"))
    if receipt.get("reason") == "done" and not closed_parent:
        if whole and end:
            issue("done exit disagrees with the participant's final parent candidate")
        else:
            missing("done exit lacks a matching closed parent on the exiting Git candidate")
    platform = dict(platform, policy_blobs=blobs, exit_candidate=end)
    facts = evidence.score(history, track, verdicts, platform, release=delivered, qualification=admitted,
                           submitted=submitted, endpoint_known=costs["work_cutoff"],
                           step_boundaries=history.issued_coverage,
                           final_subject=final_subject, known_candidates=case_candidates, available_candidates=repos)
    facts["protected_slots"] = protected_slots
    facts["historical_protected_observations"] = historical_protected
    facts["kernel_transmissions"] = history.kernel_transmissions
    if arm == "purser" and actual_trace and "original_prefix_revision" in actual_trace:
        facts["kernel_recovery"] = {field: actual_trace[field]
                                    for field in ("original_prefix_revision", "recovered_through_revision")}
    facts.update(checkpoint_qualified=old_qualified, old_submission_observed=submitted, premise_delivered=delivered)
    if basis == "before_release" and (case_candidates - {START} or history.action_lower_bound or observed_exit):
        issue("before-release accounting conflicts with evidence from a later stage of this case")
        basis = "unavailable"
        if not history.action_lower_bound:
            facts["charged_attempts_observed"] = None
    elif (basis == "before_release" and all(e["kind"] in ("oracle", "terminal") for e in journal)
            and not history.action_lower_bound and not observed_exit):
        if track != "clean":
            facts["intervention_reached"] = False
        costs.update(active_lower_ns=0, witnessed_elapsed_ns=0)
    elif retained_journal is None and not history.source.publications:
        facts["charged_attempts_observed"] = None
    reached = facts["intervention_reached"]
    if track == "clean":
        cohort = history.clean_exposure
    elif reached is True:
        cohort = True
    elif not faults and delivered is False and history.clean_exposure is True:
        # A real budget or blocked exit before the change retains its registered final-code test.
        cohort = observed_exit
    else:
        cohort = None
    facts.update(costs, cohort_eligible=cohort, clock_pause_quiescent=quiet, exclusion_accounted=accounted,
                 complete_action_sequence=history.actions_complete, complete_public_history=whole,
                 receipt_complete=history.receipt_complete, complete_read_exposure=history.reads_complete,
                 received_contexts=history.received_contexts, prior_changed_exposure=history.prior_changed_exposure)
    expected = outcomes.raw_score(facts) if raw.get("validity") != "infrastructure_invalid" and track != "clean" else None
    if raw.get("validity") == "complete":
        if (not whole or not observed_exit or not closed_parent or facts["final_pass"] is None
                or track != "clean" and (expected is None or reached is not True)):
            issue("completed run lacks its registered endpoint or checkpoint evidence")
    healthy = bool(track == "clean" and cohort is True and observed_exit and receipt["reason"] == raw.get("terminal") == "done"
                   and closed_parent and facts["final_pass"] is True)
    return Case(raw, facts, basis, tuple(issues), costs["step_comparable"], costs["elapsed_comparable"], healthy,
                tuple(unavailable))



def inspect_series(directory, *, strict=False, rerun=False):
    result, measurements = {}, {}
    disputes = []
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
                    try:
                        require(sha(archive.read(name)) == tail.split(".")[0], "incorrect object address")
                    except ERRORS as error:
                        disputes.append(f"{name}: {error}")
        starts = [n.removeprefix("started/") for n in names if n.startswith("started/")]
        require(starts == [s for s in suffixes if s in starts], "registered cases started out of order")
        objects, packs = Objects(archive), GitPacks(Path(tmp))
        for (track, seed, rank, arm), suffix in zip(units, suffixes, strict=True):
            marker, name = "started/" + suffix, "runs/" + suffix
            if marker not in names and name not in names:
                continue
            try:
                require(name in names, "registered start has no result")
                raw = parse(archive.read(name))
                require(same([raw[k] for k in ("suite", "experiment", "mode", "track", "seed", "arm", "replicate")],
                             [MANIFEST["id"], "E2", "scripted", track, seed, arm, replicate]), "raw unit identity mismatch")
                binding = []
                try:
                    require(marker in names and same(parse(archive.read(marker)),
                            {"track": track, "arm": arm, "rank": rank, "seed": seed}), "unavailable or incorrect start marker")
                except ERRORS as error:
                    binding.append(str(error))
                if not same(raw.get("inputs"), input_identity(freeze, arm)):
                    binding.append("raw input pins differ from registration")
                case = inspect_case(raw, schema, objects, packs, rank, rerun=rerun, measurements=measurements,
                                    producer_files=freeze["source_files"])
                case.facts["registration_confirmed"] = not binding
                if binding:
                    case.healthy_clean = False
                    case.issues += tuple(binding)
            except ERRORS as error:
                case = Case({"terminal": "unavailable", "validity": "unavailable", "cost": {}},
                            dict.fromkeys(outcomes.FIELDS), "unavailable", (str(error),))
            if strict and case.issues:
                disputes.append(f"{suffix}: {' | '.join(case.issues)}")
            result[track, seed, arm] = case
        record_count = sum("runs/" + suffix in names for suffix in suffixes)
        if strict and replicate == 1:
            require(record_count == len(units), f"official E2 replicate 1 requires all {len(units)} registered records")
        stats = {"records": record_count, "started": len(starts), "registered": len(units), "replicate": replicate, "git_packs": len(packs.repos),
                 "reexecuted_seed_candidate_versions": len(measurements),
                 "reexecuted_seed_candidate_criteria": sum(len(group) for group in measurements.values()),
                 "missing_registered": len(units) - record_count}
        if disputes:
            raise ValueError(f"reexecuted {stats['reexecuted_seed_candidate_criteria']} protected criteria; "
                             + " | ".join(disputes))
    return result, stats


def render(directory):
    from .summary import render_summary
    cases, stats = inspect_series(directory)
    return render_summary(cases, stats=stats)


def verify_candidates(directory):
    _, stats = inspect_series(directory, strict=True, rerun=True)
    return stats
