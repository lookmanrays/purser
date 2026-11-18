"""Durable E2 participant invocation, exit and independent post-clock observations."""

import json
import platform
import sqlite3
import sys
import time
import tempfile
from copy import deepcopy
from pathlib import Path

from purser.kernel.artifacts import Artifacts as KernelArtifacts
from purser.kernel.store import Store
from purser.kernel.wire import KernelError

from experiments.e1 import capture
from experiments.e1.accounting import digest, kernel_rows, validity_for
from experiments.e1.artifacts import FIXTURE, MANIFEST, Candidates, encoded, git, sha
from experiments.e1.outcomes import LABELS
from experiments.e1.purser_arm import retained_kernel
from experiments.e1.recovery import failure_record, input_identity
from experiments.e1.report import GitPacks, Objects
from experiments.e1.run import _run_arm, utc

from . import candidate_evidence, clock, evidence, measure, outcomes, plan_versions, policy
from .driver import Driver
from .public_sources import Originals


def definitions():
    here = Path(__file__).parent
    files = [here / "policy.py", *(here.parent / "e1" / f for f in ("observer.py", "probe.py", "measurement.py"))]
    sources = {str(p.relative_to(here.parent)): sha(p.read_bytes()) for p in files}
    defs = {label: encoded({"criterion": label, "version": 1, "public_verifier": sources,
                            "retry_basis": "use the publicly assigned schedule",
                            "fixture": FIXTURE["tree_sha1"]}) for label in LABELS}
    env = encoded({"python": sys.version, "platform": platform.platform(), "git": git(here, "--version").decode().strip()})
    return defs, env


def kernel_witnesses(snapshot):
    """Observation admission installs these bytes. Initial study enrollment only pins digests."""
    records = {record["id"]: record for record in snapshot["projection"]["records"]}
    required = set()
    for record in records.values():
        if record["kind"] != "Observation":
            continue
        fields = record["fields"]
        required.add(fields["artifact_sha256"])
        if "run" in fields:
            run = records[fields["run"]]["fields"]
            required.update((run["definition_sha256"], run["environment_sha256"]))
    return required


def participant(directory, assets, arm):
    if arm != "purser":
        data = (directory / "plan.json").read_bytes()
        rows = json.loads(data)
        return rows, {"plan_sha256": sha(data)}, {"plan": rows, "plan_versions": plan_versions.retain(directory, assets, data)}
    store = Store(directory / "kernel.sqlite")
    snap = store.snapshot()
    trace = retained_kernel(store, snap)
    kernel_files = KernelArtifacts(store.path)
    names = kernel_witnesses(snap)
    missing, retained = {}, {}
    try:
        names.update(p.name for p in kernel_files.directory.iterdir() if len(p.name) == 64)
    except FileNotFoundError:
        pass  # The kernel creates this directory on its first stored witness.
    except OSError as error:
        missing["directory"] = str(error)
    for digest in sorted(names):
        try:
            retained[digest] = assets.put(kernel_files.read(digest), "bin")
        except (OSError, KernelError) as error:
            missing[digest] = str(error)
    trace["kernel_artifacts"] = retained
    if missing:
        trace["missing_kernel_artifacts"] = missing
    return kernel_rows(snap["projection"]), {"revision": snap["revision"], "head_hash": snap["head_hash"]}, trace


def retain_participant(directory, assets, arm, progress, emit, issue):
    rows, platform = progress["rows"], progress["platform"]
    refs = platform["referenced_files"]

    try:
        public, identity, trace = participant(directory, assets, arm)
        platform["treatment_trace"] = assets.put(trace, "json")
        refs.append(platform["treatment_trace"])
        for digest, reason in trace.get("missing_kernel_artifacts", {}).items():
            issue(f"kernel artifact {digest}", ValueError(reason))
        latest = next((e for e in reversed(rows) if e["kind"] in ("action", "fault")), None)
        last = latest["payload"] if latest else None
        final = capture.checkpoint(assets, public, identity)
        platform["final_public"] = final
        if last is not None:
            last.setdefault("after", final)
        prior = capture.published(rows, progress["initial_states"])
        snapshot = trace.get("snapshot")
        decision = None
        if latest and latest["kind"] == "action":
            released = any(e["kind"] == "public_state" and (e["payload"].get("evidence") or {}).get("source_action") == last["attempt"]
                           for e in rows)
            if not released:
                decision = capture.returned_decision(last) or capture.durable_decision(public, last, snapshot)
        capture.states(public, final, prior, emit, action=last, decision=decision, recovered=True)
        platform["public_history_complete"] = bool(platform.get("initial_public")) and all(
            e["payload"].get("before") and e["payload"].get("after") for e in rows if e["kind"] in ("action", "fault"))
    except Exception as ex:
        issue("participant readback", ex)
    try:
        work = directory / "worker"
        if not (work / ".git").is_dir():
            raise FileNotFoundError("original working Git repository unavailable")
        status = git(work, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        platform["exit_candidate"] = {"head": git(work, "rev-parse", "HEAD").decode().strip(),
                                      "dirty": bool(status), "status_hex": status.hex(),
                                      "observed_utc": utc(), "source": "after_participant_exit" if
                                      platform.get("participant_exit") else "stopped_worker_readback"}
    except Exception as ex:
        issue("exiting worktree", ex)


def retain(directory, assets, arm, progress, seed, rank, issue, *, interrupted=False):
    rows = progress.setdefault("rows", [])
    platform = progress.setdefault("platform", {})
    refs = platform.setdefault("referenced_files", [])
    start = progress.get("start_monotonic_ns")

    def emit(kind, payload):
        offset = clock.active_ns(start, time.monotonic_ns(), platform) if start is not None else 0
        rows.append({"seq": len(rows) + 1, "offset_ns": max(rows[-1]["offset_ns"] if rows else 0, offset),
                     "kind": kind, "payload": payload})

    def git_issue(message, error=None):
        message = f"{message}: {error}" if error is not None else message
        issues = platform.setdefault("git_evidence_issues", [])
        if message not in issues:
            issues.append(message)
            issue("case Git evidence", ValueError(message))

    def git_gap(message):
        issue("case Git source unavailable", FileNotFoundError(message))

    if interrupted:
        damaged = candidate_evidence.preserve_damaged_objects(assets)
        if damaged:
            platform["damaged_objects"] = damaged
            for name in damaged:
                git_issue(f"original bytes disagreed with object address {name}")
    if start is not None:
        retain_participant(directory, assets, arm, progress, emit, issue)
    mapping = progress.get("candidates", {})
    original_files = set(assets.archive.namelist())
    if type(mapping) is dict:
        for oid, entry in mapping.items():
            if candidate_evidence.valid_entry(oid, entry) and entry["pack"]["path"] not in original_files:
                git_issue(f"original candidate {oid} was missing its staged pack {entry['pack']['path']}")
    objects = Objects(assets.archive)
    originals = Originals(arm, platform, objects.item, lambda _: None, journal=rows, objects=objects)
    platform["policy_blobs"] = {}
    with tempfile.TemporaryDirectory(prefix="purser-e2-observe-packs-") as temp:
        catalog = candidate_evidence.Catalog(objects, GitPacks(Path(temp)),
                                             local_repos=(directory / "private-objects", directory / "worker"))
        named = candidate_evidence.original_oids(arm, platform, objects, originals, rows, start, git_issue,
                                                 lookup=catalog.repository_for, seed=seed, native_producer=True)
        original = mapping
        if platform.get("original_candidate_map") is not None:
            try:
                original = objects.item(platform["original_candidate_map"])
            except candidate_evidence.ERRORS as error:
                git_issue("original candidate map", error)
                original = {}
        seals = candidate_evidence.observe_stage(directory, assets, catalog, named, git_issue, original_map=original)
        platform["case_git_packs"] = seals
        members, entries, repos = candidate_evidence.reconcile(catalog, mapping, seals, named, git_issue,
                                                                capture=True, original_map=original, unavailable=git_gap)
        if (platform.get("git_evidence_issues") or members - repos.keys()) and "original_candidate_map" not in platform:
            platform["original_candidate_map"] = assets.put(mapping, "json")
        if members - repos.keys():
            platform["unavailable_candidate_oids"] = sorted(members - repos.keys())
        progress["candidates"] = entries
        refs.extend(seal["pack"] for seal in seals)
        stage_reports = measure.stage_reports(assets, seed, rank) if interrupted else None
        # The scorer may have published a report before it journaled the observation.
        # Retain case-local originals even if later losses erased all participant bindings.
        if stage_reports:
            known_refs = {ref.get("path") for ref in refs if isinstance(ref, dict)}
            for (oid, _, _), reports in stage_reports.items():
                if oid not in members:
                    for ref, _ in reports:
                        if ref["path"] not in known_refs:
                            refs.append(ref)
                            known_refs.add(ref["path"])
        for oid in sorted(members):
            repo = repos.get(oid)
            if repo is not None:
                try:
                    platform["policy_blobs"][oid] = evidence.file_ids(repo, oid)
                except Exception as ex:
                    issue(f"policy Git objects {oid}", ex)
            measure.collect(repo, [oid], seed, rank, assets, refs, rows, emit, issue, stage_reports=stage_reports)
    return progress


def recover(directory, assets, arm, progress, *, seed, rank):
    saved = deepcopy(progress or {})
    saved.setdefault("platform", {})
    saved.setdefault("initial_states", {})
    def issue(stage, error):
        saved["platform"].setdefault("recovery_issues", []).append({"stage": stage, "error": str(error)})
    return retain(directory, assets, arm, saved, seed, rank, issue, interrupted=True)


def failure(assets, freeze, arm, track, seed, rank, *, reason, progress=None):
    progress = progress or {}
    platform = progress.setdefault("platform", {})
    platform.setdefault("case_git_packs", [])
    mapping = progress.get("candidates", {})
    if type(mapping) is not dict or any(not candidate_evidence.valid_entry(oid, entry) for oid, entry in mapping.items()):
        platform["original_candidate_map"] = assets.put(mapping, "json")
        platform.setdefault("git_evidence_issues", []).append("unreconciled malformed candidate map at failure")
        entries = mapping.items() if type(mapping) is dict else ()
        progress["candidates"] = {oid: entry for oid, entry in entries if candidate_evidence.valid_entry(oid, entry)}
    return dict(failure_record(assets, freeze, arm, track, seed, rank, reason=reason, progress=progress,
                               duration=clock.active_ns), experiment="E2")


def attempt(directory, assets, freeze, arm, track, seed, rank, *, progress=None):
    j = clock.ParticipantClock(arm, progress=progress, released=False)
    facts = j.platform
    facts.update(definition_artifacts=[], environment_artifact=None, referenced_files=[], treatment_trace=None,
                 public_history_complete=False)
    errors = []
    def issue(stage, error):
        errors.append(f"{stage}: {type(error).__name__}: {error}")
    reason = None
    try:
        j.persist()
        candidates = Candidates(directory, assets)
        j.candidates = candidates.entries
        defs, env = definitions()
        facts.update(definition_artifacts=[assets.put(v, "bin") for v in defs.values()],
                     environment_artifact=assets.put(env, "bin"), python=sys.version, os=platform.platform(),
                     sqlite=sqlite3.sqlite_version, git=git(directory, "--version").decode().strip())
        j.persist()
        arm_session = policy.KernelArm(directory, defs, env) if arm == "purser" else policy.PlanArm(directory)
        driver = Driver(candidates, arm_session, j, assets, seed, track, rank)
        reason = _run_arm(driver, j)
    except Exception as error:
        issue("participant", error)
    finally:
        j.stop()
    if reason in ("blocked", "idle", "done", "step_limit") and clock.active_ns(j.start,j.end,facts) > clock.LIMIT:
        reason = "time_limit"
    if reason is not None:
        facts["participant_exit"] = {"reason": reason, "end_monotonic_ns": j.end, "end_utc": j.ended}
        j.persist()

    retained = retain(directory, assets, arm, j.checkpoint(), seed, rank, issue, interrupted=bool(errors))
    j.candidates = retained["candidates"]
    if not errors:
        try:
            from .report import derive_case
            facts.update(start_utc=j.started, end_utc=j.ended, start_monotonic_ns=j.start, end_monotonic_ns=j.end,
                         budget_reason=reason, cost_basis="exact", failure=None, clock_stopped=True)
            j.event("terminal", {"end_reason": reason, "last_observation": len(j.rows) or None,
                                 "end_monotonic_ns": j.end, "end_utc": j.ended, "missing": None,
                                 "human_action": None, "cost_basis": "exact"})
            raw = {"schema_version": 1, "suite": MANIFEST["id"], "experiment": "E2", "track": track, "seed": seed,
                    "arm": arm, "mode": "scripted", "replicate": freeze["replicate"], "inputs": input_identity(freeze, arm),
                    "artifacts": {"journal": assets.put(j.rows, "json"), "candidates": assets.put(j.candidates, "json"),
                                  "platform": assets.put(facts, "json"), "model": None},
                    "terminal": reason, "validity": validity_for(reason), "cost": {"steps": j.steps,
                        "elapsed_ns": clock.active_ns(j.start,j.end,facts), "model_calls": 0, "input_tokens": 0, "output_tokens": 0,
                        "spend_usd_micros": 0}, "score": None}
            with tempfile.TemporaryDirectory(prefix="purser-e2-issued-score-") as temp:
                observed = derive_case(raw, Objects(assets.archive), GitPacks(Path(temp)), rank)
            raw["score"] = outcomes.raw_score(observed.facts) if track != "clean" else None
            if track == "primary" and reason == "done" and raw["score"] is None:
                raise ValueError(f"completed case lacks measurable data: {observed.issues}")
            return raw
        except Exception as error:
            issue("finalization", error)
    return failure(assets, freeze, arm, track, seed, rank, reason=" | ".join(errors), progress=j.checkpoint())
