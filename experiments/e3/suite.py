"""Pin the complete scripted E3 implementation, then publish the registered schedule exactly once."""

import fcntl
import json
import os
import re
import subprocess

from experiments.e1.artifacts import FIXTURE, MANIFEST, REGISTRATION, ROOT, encoded, sha
from experiments.e1.suite import ARMS, collect_cases

from . import trial
from .fixture import E3

DIRECTORIES = ("purser", "experiments/e1", "experiments/e2", "experiments/e3")
MANIFEST_PATH = "experiments/manifests/v1"
DOCUMENTS = {"manifest_sha256": "manifest.json", "protocol_sha256": "protocol.md", "raw_schema_sha256": "raw.schema.json"}
MAPPING = {
    "setup": "Provision an identical registered candidate, fixed-parent Git start, task packet and scoped permission "
             "on a fresh private repository. Supply the prepared result to its assignment. This precedes release.",
    "context": "Release the assigned task packet once (including the ordinary assignment context for Purser).",
    "submit": "Bind the supplied logical intention and candidate under the fixture permission.",
    "effect": "Request a fresh guarded physical attempt. The single dispatch boundary includes its own cutoff and "
              "Git transaction. Its caller release counts even when the child dies before replying.",
    "reconcile": "Inspect the named physical attempt. Plain explicitly inspects a timeout. Both equipped arms receive "
                 "the independent Git report after every physical call. Purser admits that exact report.",
    "decide": "Publish one actual executor decision for the attempt with its original disposition, observation basis "
              "and next action. The final decision can complete the task or leave it pending.",
    "timing": "Charge from the first task release to exit/limit. Setup precedes release. Independent offline scoring "
              "and final evidence acquisition follow exit. In-run contemporaneous observation is charged.",
    "hand_count": "The clean plain loop uses 4 calls (context, submit, effect and decide). Equipped arms add "
                  "reconcile for 5. Every landed timeout takes 5. After an unlanded timeout and fresh attempt "
                  "the plain loop uses 7 and equipped arms use 8. All live-writer controls use 5.",
}


def repo(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def schedule():
    units = [("primary", seed, rank) for rank, seed in enumerate(E3["seeds"], 1)]
    for track, seed in (("clean", E3["clean_seed"]), (E3["stress"]["id"], E3["stress"]["seed"])):
        units.append((track, seed, E3["seeds"].index(seed) + 1))
    for track, seed, rank in units:
        shift = (rank + 1) % len(ARMS)
        for arm in ARMS[shift:] + ARMS[:shift]:
            yield track, seed, rank, arm


def source_pin(rev=None):
    if rev is None:
        if repo("status", "--porcelain", "--untracked-files=all", "--", *DIRECTORIES, MANIFEST_PATH):
            raise ValueError("commit the complete study, kernel and fixture before registration")
        rev = repo("rev-parse", "HEAD").decode().strip()
    if not isinstance(rev, str) or not re.fullmatch("[0-9a-f]{40}", rev):
        raise ValueError("source registration requires a full Git commit ID")
    paths = repo("ls-tree", "-r", "--name-only", rev, "--", *DIRECTORIES).decode().splitlines()
    files = {p: sha(repo("show", f"{rev}:{p}")) for p in paths if p.endswith(".py")}
    required = {"experiments/e3/report.py", "experiments/e3/participant.py", "experiments/e3/supervisor.py",
                "purser/effects/foreground.py"}
    if not required <= files.keys():
        raise ValueError("registration requires the joined participant, foreground broker and offline scorer")
    closure = sha(encoded(files))

    def entry(name):
        return sha(encoded({"entrypoint": name, "source_sha256": closure}))

    docs = {key: sha(repo("show", f"{rev}:{MANIFEST_PATH}/{name}")) for key, name in DOCUMENTS.items()}
    return {"suite": MANIFEST["id"], "experiment": "E3", "source_files": files, "source_commit": rev, **docs,
            "fixture_tree": FIXTURE["tree_sha1"], "fixture_commit": FIXTURE["commit_sha1"],
            "scorer_sha256": entry("experiments.e3.report.derive_case"),
            "fault_driver_sha256": entry("experiments.e3.supervisor.Supervisor.dispatch"),
            "executor_sha256": entry("experiments.e3.participant.Participant.execute"),
            "controller_sha256": entry("experiments.e3.suite.collect_units"),
            "treatments": {arm: entry("experiments.e3.trial.attempt/" + arm) for arm in ARMS},
            "action_mapping": MAPPING, "schedule": [list(unit) for unit in schedule()]}


def check_replicate(replicate, reason):
    if type(replicate) is not int or replicate <= 0 or (replicate > 1 and not (reason or "").strip()):
        raise ValueError("a repeat requires its positive identifier and disclosed reason")


def freeze_series(directory, *, replicate=1, reason=None):
    check_replicate(replicate, reason)
    data = {**source_pin(), "replicate": replicate, "replication_reason": reason}
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "freeze.json").open("x") as stream:
        json.dump(data, stream, sort_keys=True, ensure_ascii=False, indent=2)
        stream.write("\n")


def validate_freeze(freeze):
    wanted = source_pin(freeze["source_commit"])
    check_replicate(freeze["replicate"], freeze["replication_reason"])
    if {k: v for k, v in freeze.items() if k not in ("replicate", "replication_reason")} != wanted:
        raise ValueError("registration differs from its recorded source and schedule")
    if (repo("diff", "--name-only", freeze["source_commit"], "--", MANIFEST_PATH)
            or any(sha((ROOT / p).read_bytes()) != checksum for p, checksum in wanted["source_files"].items())
            or any(sha((REGISTRATION / name).read_bytes()) != wanted[key] for key, name in DOCUMENTS.items())):
        raise ValueError("use a checkout containing the registered study, treatment and protocol")
    return freeze["replicate"]


def collect_units(directory, units=None):
    data = (directory / "freeze.json").read_bytes()
    freeze = json.loads(data)
    rep = validate_freeze(freeze)
    if source_pin()["source_files"] != freeze["source_files"]:
        raise ValueError("unregistered source files in collector")
    complete = list(schedule())
    units = complete if units is None else list(units)
    if rep == 1 and units != complete:
        raise ValueError("replicate 1 requires the complete ordered schedule")
    if len(set(units)) != len(units) or not set(units) <= set(complete):
        raise ValueError("unregistered trial identities")
    filename = (directory / "freeze.json").resolve().relative_to(ROOT).as_posix()
    if repo("show", "HEAD:" + filename) != data:
        raise ValueError("commit the registration before collection")
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        collect_cases(directory, freeze, data, units, trial.attempt, recover=trial.recover, failure=trial.failure)
    finally:
        os.close(fd)
