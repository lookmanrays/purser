"""Pin and register the E2 source and schedule, then publish each started case once."""

import fcntl
import json
import os
import subprocess

from experiments.e1.artifacts import FIXTURE, MANIFEST, REGISTRATION, ROOT, encoded, sha
from experiments.e1.suite import ARMS, collect_cases

from . import trial

DIRECTORIES = ("purser", "experiments/e1", "experiments/e2")
MAPPING = {
    "setup": "Provision identical fixture, initial assignments, old decision and scoped authority before release.",
    "plan": "Release one schedule, adopt the identified change, assign its work, or open one verification run.",
    "context": "Release one assignment's public work and current decision.",
    "edit": "Apply the current scoped task to its one policy file.",
    "check": "Perform worker checks or review the immutable submission.",
    "submit": "Publish one sealed candidate on its assignment.",
    "observe": "Run one public independent criterion on the sealed candidate.",
    "decide": "Publish one verifier verdict or request one subject closure.",
    "fault": "Deliver the incoming observation at the recorded checkpoint. This intervention adds zero worker steps.",
    "rework": "Count each subsequent charged plan/context/edit/check/submit/observe/decide on W1/W2/W3 or W-notify, "
              "including attempted calls at cutoff. Each has one observed subject. Reads by the retrospective scorer add zero.",
    "hand_count": "Clean loop: plan 1 + 3 leaves x 6 + parent 5 = 24. Clean equipped: 24 + 6 criteria x 3 = 42. "
                  "Ordinary changed loop after receipt: adopt and reassign 2 + W2 6 + parent 5 = 13. "
                  "Equipped after receipt: 13 + 4 criteria x 3 = 25.",
}


def repo(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def schedule():
    experiment = MANIFEST["experiments"]["E2"]
    units = [("primary", seed, rank) for rank, seed in enumerate(experiment["seeds"], 1)]
    for track, seed in (("clean", experiment["clean_seed"]),
                        (experiment["stress"]["id"], experiment["stress"]["seed"])):
        units.append((track, seed, experiment["seeds"].index(seed) + 1))
    for track, seed, rank in units:
        shift = rank % len(ARMS)
        for arm in ARMS[shift:] + ARMS[:shift]:
            yield track, seed, rank, arm


def source_pin(rev=None):
    if rev is None:
        if repo("status", "--porcelain", "--untracked-files=all", "--", *DIRECTORIES, "experiments/manifests/v1"):
            raise ValueError("uncommitted study, kernel or fixture files")
        rev = repo("rev-parse", "HEAD").decode().strip()
    names = [p for p in repo("ls-tree", "-r", "--name-only", rev, "--", *DIRECTORIES).decode().splitlines()
             if p.endswith(".py")]
    files = {p: sha(repo("show", f"{rev}:{p}")) for p in names}
    def group(paths):
        return sha(encoded({k: files[k] for k in sorted(paths)}))
    study = [p for p in files if p.startswith("experiments/")]
    kernel = [p for p in files if p.startswith("purser/")]
    def modules(area, *names):
        return [f"experiments/{area}/{n}.py" for n in names]
    common = modules("e1", "artifacts", "measurement", "probe")
    protected = (common + modules("e1", "scorer", "outcomes", "accounting", "report", "capture", "recovery")
                 + modules("e2", "measure", "outcomes", "evidence", "policy", "report", "summary", "suite") + kernel)
    executor = (common + modules("e1", "executor", "arms", "purser_arm", "observer", "run", "capture")
                + modules("e2", "driver", "policy", "trial"))
    return {"suite": MANIFEST["id"], "experiment": "E2", "source_files": files, "source_commit": rev,
            "fixture_tree": FIXTURE["tree_sha1"], "fixture_commit": FIXTURE["commit_sha1"],
            "manifest_sha256": sha((REGISTRATION / "manifest.json").read_bytes()),
            "protocol_sha256": sha((REGISTRATION / "protocol.md").read_bytes()),
            "raw_schema_sha256": sha((REGISTRATION / "raw.schema.json").read_bytes()),
            "scorer_sha256": group(protected), "fault_driver_sha256": group(executor),
            "executor_sha256": group(executor), "controller_sha256": group(study),
            "treatments": {a: sha(encoded({"arm": a, "source": group(executor + (kernel if a == "purser" else []))}))
                           for a in ARMS}, "action_mapping": MAPPING, "schedule": [list(unit) for unit in schedule()]}


def freeze_series(directory, *, replicate=1, reason=None):
    if type(replicate) is not int or replicate < 1 or (replicate > 1 and not (reason or "").strip()):
        raise ValueError("a repeat requires a new number and a reason")
    data = dict(source_pin(), replicate=replicate, replication_reason=reason)
    directory.mkdir(exist_ok=True, parents=True)
    with (directory / "freeze.json").open("x") as output:
        json.dump(data, output, sort_keys=True, ensure_ascii=False, indent=2)
        output.write("\n")


def validate_freeze(freeze):
    rev = freeze["source_commit"]
    wanted = source_pin(rev)
    if {k: v for k, v in freeze.items() if k not in ("replicate", "replication_reason")} != wanted:
        raise ValueError("registration differs from the recorded source or frozen protocol")
    rep = freeze.get("replicate")
    if type(rep) is not int or rep <= 0 or (rep > 1 and not (freeze.get("replication_reason") or "").strip()):
        raise ValueError("invalid replicate identity")
    if any(sha((ROOT / p).read_bytes()) != digest for p, digest in wanted["source_files"].items()):
        raise ValueError("use a checkout with the registered source bytes")
    return rep


def collect_units(directory, units=None):
    units = list(schedule() if units is None else units)
    data = (directory / "freeze.json").read_bytes()
    registration = json.loads(data)
    rep = validate_freeze(registration)
    if source_pin()["source_files"] != registration["source_files"]:
        raise ValueError("unregistered source files in collector")
    if rep == 1 and units != list(schedule()):
        raise ValueError("replicate 1 must use the full frozen schedule")
    if len(units) != len(set(units)) or not set(units) <= set(schedule()):
        raise ValueError("unregistered trial identities")
    path = (directory / "freeze.json").resolve().relative_to(ROOT).as_posix()
    if repo("show", "HEAD:" + path) != data:
        raise ValueError("registration must be committed before collection")
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        collect_cases(directory, registration, data, units, trial.attempt, recover=trial.recover, failure=trial.failure)
    finally:
        os.close(fd)
