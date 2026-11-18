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
from experiments.e1.accounting import kernel_rows, validity_for
from experiments.e1.artifacts import FIXTURE, MANIFEST, Candidates, encoded, git, sha
from experiments.e1.outcomes import LABELS
from experiments.e1.purser_arm import retained_kernel
from experiments.e1.recovery import failure_record, input_identity
from experiments.e1.report import GitPacks, Objects
from experiments.e1.run import _run_arm, utc

from . import clock, evidence, measure, outcomes, plan_versions, policy
from .driver import Driver


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


def retain(directory, assets, arm, progress, seed, rank, issue):
    rows, platform = progress["rows"], progress["platform"]
    refs = platform["referenced_files"]

    def emit(kind, payload):
        offset = max(rows[-1]["offset_ns"] if rows else 0, clock.active_ns(progress["start_monotonic_ns"], time.monotonic_ns(), platform))
        rows.append({"seq": len(rows) + 1, "offset_ns": offset, "kind": kind, "payload": payload})

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
        status = git(work, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        platform["exit_candidate"] = {"head": git(work, "rev-parse", "HEAD").decode().strip(),
                                      "dirty": bool(status), "status_hex": status.hex(),
                                      "observed_utc": utc(), "source": "after_participant_exit" if
                                      platform.get("participant_exit") else "stopped_worker_readback"}
    except Exception as ex:
        issue("exiting worktree", ex)
    candidates = progress["candidates"]
    refs.extend(entry["pack"] for entry in candidates.values())
    platform["policy_blobs"] = {}
    # The canonical retained objects survive loss of the participant's disposable
    # working or bare repositories. The same byte/ancestry checks govern both paths.
    with tempfile.TemporaryDirectory(prefix="purser-e2-observe-packs-") as temp:
        packs, objects = GitPacks(Path(temp)), Objects(assets.archive)
        for oid, entry in candidates.items():
            try:
                repo = packs.restore(oid, entry, objects)
            except Exception as ex:
                issue(f"sealed Git candidate {oid}", ex)
                continue
            try:
                platform["policy_blobs"][oid] = evidence.file_ids(repo, oid)
            except Exception as ex:
                issue(f"policy Git objects {oid}", ex)
            measure.collect(repo, [oid], seed, rank, assets, refs, rows, emit, issue)
    return progress


def recover(directory, assets, arm, progress, *, seed, rank):
    if not progress or progress["cost_basis"] == "before_release":
        return progress
    saved = deepcopy(progress)
    saved["platform"].setdefault("recovery_issues", [])
    def issue(stage, error):
        saved["platform"].setdefault("recovery_issues", []).append({"stage": stage, "error": str(error)})
    return retain(directory, assets, arm, saved, seed, rank, issue)


def failure(*args, **kwargs):
    return dict(failure_record(*args, **kwargs, duration=clock.active_ns), experiment="E2")


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

    if j.released:
        retain(directory, assets, arm, j.checkpoint(), seed, rank, issue)
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
