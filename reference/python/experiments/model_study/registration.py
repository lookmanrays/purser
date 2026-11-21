"""One prospective identity for source, population, model conditions and spending authority."""

import os
import platform
import re
import sqlite3
import subprocess
import sys
from dataclasses import fields

from experiments.e1.artifacts import FIXTURE, MANIFEST, ROOT, encoded, sha
from experiments.e1.recovery import sync_directory
from experiments.e1.report import parse
from experiments.e3.suite import check_replicate
from experiments.model_budget import LIMITS, SpendingAuthority

ARMS = tuple(MANIFEST["units"]["arm_order"])
M5 = "docs/evidence/M5/observer"
REG = "experiments/manifests/v1"
SOURCES = ("purser", "experiments", M5, "docs/implementation-plan-v0.5.md")
DOCS = {"manifest_sha256": "manifest.json", "protocol_sha256": "protocol.md", "raw_schema_sha256": "raw.schema.json"}
MAPPING = {
    "turn": "One counted model choice and its first requested public work action share one step. "
            "A failed decision still costs one step. Expand additional public operations individually.",
    "edit": "A model supplies the path, complete replacement source and submission disposition. "
            "Check the pre-overlay Git snapshot, submit the candidate, then expose ordinary public review. "
            "An explicit close disposition requests closure after submission.",
    "review": "The two verifier-equipped arms open, observe and verify each public criterion. "
              "The plain plan can inspect its public reviews and declare closure independently.",
    "timing": "Charge active monotonic time from task release to participant exit. Pause for protected "
              "checkpoint qualification and record its original interval. Final protected scoring is post-exit.",
    "example": "A plain edit turn with a current assignment issues edit, worker check, submit and self-review "
               "(4 steps, 1 model call). An equipped leaf additionally opens, observes and verifies (7 steps).",
}


def schedule():
    for number, (experiment, spec) in enumerate(sorted(MANIFEST["experiments"].items()), 1):
        slots = [("primary", seed) for seed in spec["seeds"]]
        slots.append(("clean", spec["clean_seed"]))
        if spec.get("stress"):
            slots.append((spec["stress"]["id"], spec["stress"]["seed"]))
        for track, seed in slots:
            rotation = (spec["seeds"].index(seed) + number - 1) % len(ARMS)
            for arm in ARMS[rotation:] + ARMS[:rotation]:
                yield experiment, track, seed, arm


def _git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def _relevant(path):
    if path.startswith("experiments/results/"):
        return False
    return (path.startswith((REG + "/", M5 + "/")) or path == SOURCES[-1]
            or path.endswith((".py", ".wasm", ".sh", ".toml", ".lock", ".json", ".txt")))


def source_pin(revision=None):
    current = revision is None
    revision = revision or _git("rev-parse", "HEAD").decode().strip()
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("registration requires the full immutable source revision")
    table = _git("ls-tree", "-rz", revision, "--", *SOURCES)
    files = {}
    for line in table.split(b"\0"):
        if line:
            metadata, path = line.split(b"\t", 1)
            name = path.decode()
            if _relevant(name):
                mode, kind, oid = metadata.decode().split()
                if kind != "blob" or mode not in ("100644", "100755"):
                    raise ValueError("study source must be an ordinary file")
                files[name] = [mode, oid]
    if current:
        changed = _git("diff", "--name-only", "-z", revision, "--", *SOURCES)
        extra = _git("ls-files", "--others", "--exclude-standard", "-z", "--", *SOURCES)
        if any(_relevant(p.decode()) for p in (changed + extra).split(b"\0") if p):
            raise ValueError("commit the complete joined study source before registration or collection")
    def digest_file(path):
        return sha(_git("show", f"{revision}:{path}"))
    def entry(name):
        return sha(encoded([name, files]))
    return {"source_commit": revision, "source_files": files, "suite": MANIFEST["id"],
            "fixture_tree": FIXTURE["tree_sha1"], "fixture_commit": FIXTURE["commit_sha1"],
            **{k: digest_file(f"{REG}/{v}") for k, v in DOCS.items()},
            "criterion_sha256": digest_file(f"{M5}/criterion-v1.json"),
            "matrix_sha256": digest_file(f"{M5}/expected-matrix-v1.json"),
            "scorer_sha256": entry("model_study.report / reconstruct_case"),
            "executor_sha256": entry("model_study.participant / model_e3"),
            "fault_driver_sha256": entry("E1 overlay / E2 qualified notice / E3 supervisor"),
            "treatments": {arm: entry(arm) for arm in ARMS}}


def host():
    return {"python": sys.version, "platform": platform.platform(), "git": _git("--version").decode().strip(),
            "sqlite": sqlite3.sqlite_version, "machine": platform.machine()}


def validate_conditions(conditions, population):
    required = {"service", "authority", "observer", "data"}
    if type(conditions) is not dict or set(conditions) != required or population not in ("model", "canned_control", "disabled_control"):
        raise ValueError("unrecognized registration conditions or population")
    if conditions["data"] != "synthetic-notification-and-arm-public-context":
        raise ValueError("only the synthetic task and the current arm's public context may be released")
    if not isinstance(conditions["observer"], dict) or not conditions["observer"]:
        raise ValueError("register the observer/runtime identity or its unavailability")
    service = conditions["service"]
    if not isinstance(service, dict) or not service or set(conditions["authority"]) != {f.name for f in fields(SpendingAuthority)}:
        raise ValueError("register the complete service and spending authority")
    authority = SpendingAuthority(**conditions["authority"])
    if authority.service_sha256 != sha(encoded(service)):
        raise ValueError("authorization names a different service configuration")
    return authority


def registration(directory, conditions, *, population, budget_path=None, replicate=1, reason=None):
    check_replicate(replicate, reason)
    validate_conditions(conditions, population)
    pinned = source_pin()
    if population == "model":
        required = ("experiments/model_e3", "purser/adapters/model.py", "experiments/model_isolation")
        for prefix in required:
            if not any(p.startswith(prefix) for p in pinned["source_files"]):
                raise ValueError("join the complete model study before registering originals: " + prefix)
    budget_file = None
    if population != "disabled_control":
        path = budget_path.resolve(strict=True)
        info = path.stat()
        budget_file = {"path": str(path), "device": info.st_dev, "inode": info.st_ino}
    frozen = {**pinned, "format": 1, "mode": "model", "population": population, "replicate": replicate,
              "replication_reason": reason, "conditions": conditions, "budget_file": budget_file, "host": host(), "limits": LIMITS,
              "parameters": {"temperature": 0, "output_limit": 1500, "stream": False},
              "schedule": list(map(list, schedule())), "action_mapping": MAPPING}
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "freeze.json").open("xb") as stream:
        stream.write(encoded(frozen))
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(directory)
    return frozen


def load_registration(directory, *, collecting=False):
    wire = (directory / "freeze.json").read_bytes()
    frozen = parse(wire)
    if encoded(frozen) != wire:
        raise ValueError("registration bytes are not canonical")
    expected = source_pin(frozen["source_commit"])
    if any(frozen.get(k) != value for k, value in expected.items()):
        raise ValueError("source, scorer or frozen input differs from registration")
    if (frozen["format"] != 1 or frozen["mode"] != "model" or frozen["limits"] != LIMITS
            or frozen["schedule"] != list(map(list, schedule())) or frozen["action_mapping"] != MAPPING
            or frozen["parameters"] != {"temperature": 0, "output_limit": 1500, "stream": False}):
        raise ValueError("model registration differs from frozen protocol")
    check_replicate(frozen["replicate"], frozen["replication_reason"])
    validate_conditions(frozen["conditions"], frozen["population"])
    if source_pin()["source_files"] != expected["source_files"]:
        raise ValueError("use the registered source checkout for collection and offline reconstruction")
    if collecting:
        if frozen["host"] != host():
            raise ValueError("actual study source or host differs from prospective registration")
        if frozen["population"] == "model":
            path = (directory / "freeze.json").resolve().relative_to(ROOT).as_posix()
            if _git("show", "HEAD:" + path) != wire:
                raise ValueError("commit the original registration before releasing any trial")
    return frozen, wire
