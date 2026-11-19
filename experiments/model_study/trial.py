"""Start or recover a coding case. Protected observations never enter model context."""

import platform
import signal
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from experiments.e1 import capture
from experiments.e1.artifacts import MANIFEST, Candidates, encoded, git, sha
from experiments.e1.measurement import MeasurementUnavailable
from experiments.e1.outcomes import LABELS
from experiments.e1.recovery import input_identity
from experiments.e1.run import Cutoff, Journal, _run_arm, utc
from experiments.e2 import measure
from experiments.e2.trial import participant as retained_participant
from experiments.model_budget import LIMITS

from .observations import Checks
from .observer_receipts import ReceiptLog
from .public import make_arm


class ModelClock(Journal):
    def elapsed(self):
        now = self.end if self.end is not None else time.monotonic_ns()
        return active_ns(self.start, now, self.platform.get("private_intervals", []))

    def remaining_seconds(self):
        return LIMITS["max_elapsed_s"] - self.elapsed() / 10**9

    @contextmanager
    def private_measurement(self, oid, public):
        interval = {"candidate": oid, "checkpoint": public, "begin": time.monotonic_ns(),
                    "start_utc": utc(), "preceding_events": len(self.rows)}
        signal.setitimer(signal.ITIMER_REAL, 0)
        self.platform.setdefault("private_intervals", []).append(interval)
        self.persist()
        try:
            yield
        finally:
            interval.update(end=time.monotonic_ns(), end_utc=utc(), following_events=len(self.rows))
            self.persist()
            remaining = self.remaining_seconds()
            if remaining <= 0:
                raise Cutoff("time_limit")
            signal.setitimer(signal.ITIMER_REAL, remaining)


def active_ns(start, end, periods):
    if start is None or end is None:
        return 0
    previous, active = start, end - start
    for pause in periods:
        left, right = pause["begin"], pause.get("end", end)
        if not previous <= left <= right <= end:
            raise ValueError("invalid protected measurement interval")
        active -= right - left
        previous = right
    return active


def _raw(assets, freeze, experiment, arm, track, seed, saved, terminal, error):
    rows, meta = saved.get("rows", []), saved.get("platform", {})
    start, end = saved.get("start_monotonic_ns"), saved.get("end_monotonic_ns", saved.get("last_monotonic_ns"))
    elapsed = active_ns(start, end, meta.get("private_intervals", []))
    basis = "before_release" if start is None else "exact" if "end_monotonic_ns" in saved else "observed_lower_bound"
    meta.update(start_monotonic_ns=start, end_monotonic_ns=end, start_utc=saved.get("start_utc"),
                end_utc=saved.get("end_utc", saved.get("last_utc")), clock_basis=basis,
                failure=error, budget_reason=terminal)
    rows.append({"kind": "terminal", "seq": len(rows) + 1, "offset_ns": elapsed,
                 "payload": {"end_reason": terminal, "missing": error, "human_action": None,
                             "end_monotonic_ns": end, "end_utc": meta["end_utc"], "cost_basis": basis}})
    validity = ("infrastructure_invalid" if terminal == "harness_failure" else
                "complete" if terminal == "done" else "arm_incomplete")
    refs = {"journal": assets.put(rows, "json"), "candidates": assets.put(saved.get("candidates", {}), "json"),
            "platform": assets.put(meta, "json"), "model": None}
    return {"schema_version": 1, "suite": MANIFEST["id"], "experiment": experiment, "track": track,
            "seed": seed, "arm": arm, "mode": "model", "replicate": freeze["replicate"],
            "inputs": input_identity(freeze, arm), "artifacts": refs, "terminal": terminal, "validity": validity,
            "cost": {"steps": sum(e["kind"] == "action" for e in rows), "elapsed_ns": elapsed,
                     "model_calls": 0, "input_tokens": None, "output_tokens": None, "spend_usd_micros": None},
            "score": None}


def _retain(directory, assets, arm, meta):
    public, identity, trace = retained_participant(directory, assets, arm)
    meta["final_public"] = capture.checkpoint(assets, public, identity)
    meta["treatment_trace"] = assets.put(trace, "json")
    work = directory / "worker"
    meta["exit_git"] = {"head": git(work, "rev-parse", "HEAD").decode().strip(),
                        "status": git(work, "status", "--porcelain=v1", "-z", "--untracked-files=all").hex()}


def run_case(directory, assets, freeze, arm, track, seed, rank, *, experiment, decide, observer=None,
             snapshot_from_git=None, observer_error=None, progress=None):
    from .participant import Participant
    journal = ModelClock(arm, progress=progress, released=False, mode="model")
    meta = journal.platform
    meta.update(os=platform.platform(), python=sys.version, sqlite=sqlite3.sqlite_version,
                git=git(directory, "--version").decode().strip(), referenced_files=[])
    terminal, error = "harness_failure", None
    runner = checks = None
    try:
        if observer is None:
            raise MeasurementUnavailable(observer_error or "isolated candidate observer unavailable")
        candidates = Candidates(directory, assets)
        journal.candidates = candidates.entries
        defs = {label: encoded({"criterion": label, "scorer": freeze["scorer_sha256"]}) for label in LABELS}
        env = encoded(freeze["conditions"]["observer"])
        meta["definition_artifacts"] = [assets.put(v, "bin") for v in defs.values()]
        meta["environment_artifact"] = assets.put(env, "bin")
        adapter = make_arm(directory, arm, experiment, defs, env)
        log = ReceiptLog(directory, assets, journal, {"registration_sha256": sha(encoded(freeze)),
                         "unit": [experiment, track, seed, arm]})
        if observer is not None:
            observer = observer(log.record)
        checks = Checks(candidates, observer, snapshot_from_git, seed, rank, assets, log)
        runner = Participant(experiment, track, seed, candidates, adapter, journal, checks, decide)
        journal.persist()
        terminal = _run_arm(runner, journal)
        if journal.elapsed() >= LIMITS["max_elapsed_s"] * 10**9:
            terminal = "time_limit"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        journal.stop()
    meta["participant_exit"] = {"reason": terminal, "end_monotonic_ns": journal.end, "end_utc": journal.ended}
    journal.persist()
    if runner is not None:
        try:
            _retain(directory, assets, arm, meta)
            if checks.outer is None:
                raise MeasurementUnavailable(observer_error or "isolated candidate observer unavailable")
            for oid in dict.fromkeys([*(p["payload"]["candidate"] for p in journal.rows
                                         if p["kind"] in ("fault", "public_state") and p["payload"].get("candidate")),
                                      meta["exit_git"]["head"]]):
                checks.protect(oid, LABELS, measure.OLD, journal)
                if experiment == "E2" and track != "clean":
                    checks.protect(oid, measure.REVISED, measure.NEW, journal)
        except Exception as exc:
            if error is None:
                error = f"{type(exc).__name__}: {exc}"
            terminal = "harness_failure"
    meta["isolation_receipts"] = ReceiptLog.retain(directory)
    return _raw(assets, freeze, experiment, arm, track, seed, journal.checkpoint(), terminal, error)


def recover_case(directory, assets, freeze, arm, track, seed, rank, *, experiment, progress=None):
    """Only read the surviving public state and Git objects. Never reissue a turn or run code."""
    saved = progress or {}
    meta = saved.setdefault("platform", {})
    meta["isolation_receipts"] = ReceiptLog.retain(directory)
    failure = "interrupted original case"
    try:
        _retain(directory, assets, arm, meta)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        failure += f"; public readback: {error}"
    return _raw(assets, freeze, experiment, arm, track, seed, saved, "harness_failure", failure)
