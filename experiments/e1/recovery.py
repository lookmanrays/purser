"""Recover the observations that survived an interrupted scripted attempt."""

import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from .artifacts import MANIFEST, sha


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path, data):
    """Keep a complete old or new file even if this process dies."""
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


class FileArchive:
    """Stage individual immutable objects until the trial can be published."""

    def __init__(self, directory):
        self.directory = directory
        self.objects = directory / "objects"
        self.objects.mkdir(exist_ok=True)

    def namelist(self):
        return sorted("objects/" + p.name for p in self.objects.iterdir() if not p.name.startswith("."))

    def writestr(self, name, raw, *, compress_type=None):
        if Path(name).parent != Path("objects") or Path(name).stem != sha(raw):
            raise ValueError("trial object does not match its content address")
        write_atomic(self.directory / name, raw)

    def read(self, name):
        if Path(name).parent != Path("objects"):
            raise ValueError("invalid trial object reference")
        raw = (self.directory / name).read_bytes()
        if Path(name).stem != sha(raw):
            raise ValueError("trial object changed after capture")
        return raw


def input_identity(freeze, arm):
    keys = ("fixture_tree", "fixture_commit", "manifest_sha256", "protocol_sha256", "raw_schema_sha256",
            "scorer_sha256", "fault_driver_sha256", "executor_sha256")
    return {**{key: freeze[key] for key in keys}, "treatment_sha256": freeze["treatments"][arm],
            "worktree_head": freeze["source_commit"]}


def failure_record(assets, freeze, arm, track, seed, rank, *, reason, progress=None, duration=None):
    progress = progress or {}
    rows = [dict(row) for row in progress.get("rows", []) if row["kind"] != "terminal"]
    for seq, row in enumerate(rows, 1):
        row["seq"] = seq
    steps = sum(row["kind"] == "action" for row in rows)
    start = progress.get("start_monotonic_ns")
    end = progress.get("end_monotonic_ns", progress.get("last_monotonic_ns"))
    elapsed = 0
    if start is not None and end is not None:
        elapsed = end - start if duration is None else duration(start, end, progress.get("platform", {}))
    if elapsed < 0:
        raise ValueError("trial progress clock regressed")
    stopped = progress.get("end_monotonic_ns") is not None
    basis = "before_release" if start is None else "exact" if stopped else "observed_lower_bound"
    end_utc = progress.get("end_utc", progress.get("last_utc"))
    rows.append({"seq": len(rows) + 1, "offset_ns": max(elapsed, rows[-1]["offset_ns"] if rows else 0),
                 "kind": "terminal", "payload": {"end_reason": "harness_failure", "last_observation": len(rows) or None,
                        "cost_basis": basis, "end_monotonic_ns": end, "end_utc": end_utc,
                        "missing": reason, "human_action": None}})
    try:
        git_version = subprocess.check_output(["git", "--version"], text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        git_version = None
    candidates = progress.get("candidates", {})
    facts = {"os": platform.platform(), "python": sys.version, "sqlite": sqlite3.sqlite_version,
             "git": git_version, "definition_artifacts": [], "environment_artifact": None,
             "referenced_files": [entry["pack"] for entry in candidates.values()], "treatment_trace": None,
             **progress.get("platform", {})}
    facts.update(start_monotonic_ns=start, end_monotonic_ns=end, start_utc=progress.get("start_utc"),
                 end_utc=end_utc, clock_stopped=stopped,
                 budget_reason="harness_failure", failure=reason,
                 cost_basis=basis)
    facts.setdefault("public_history_complete", False)
    facts.setdefault("participant_exit", None)
    return {"schema_version": 1, "suite": MANIFEST["id"], "experiment": "E1", "track": track,
            "seed": seed, "arm": arm, "mode": "scripted", "replicate": freeze.get("replicate", 1),
            "inputs": input_identity(freeze, arm),
            "artifacts": {"journal": assets.put(rows, "json"), "candidates": assets.put(candidates, "json"),
                          "platform": assets.put(facts, "json"), "model": None},
            "terminal": "harness_failure", "validity": "infrastructure_invalid", "score": None,
            "cost": {"steps": steps, "elapsed_ns": elapsed, "model_calls": 0, "input_tokens": 0,
                     "output_tokens": 0, "spend_usd_micros": 0}}
