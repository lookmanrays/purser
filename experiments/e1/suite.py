"""Pin all study code before collection and retain each scheduled attempt."""

import fcntl
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from .artifacts import FIXTURE, MANIFEST, REGISTRATION, ROOT, Artifacts, encoded, sha
from . import run
from .recovery import FileArchive, failure_record, sync_directory, write_atomic

ARMS = MANIFEST["units"]["arm_order"]


def source_pin():
    paths = [p for base in (ROOT / "experiments/e1", ROOT / "purser") for p in base.rglob("*.py")]
    names = sorted(p.relative_to(ROOT).as_posix() for p in paths)
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all",
                                      "--", ":(glob)experiments/e1/**/*.py", ":(glob)purser/**/*.py", "experiments/manifests/v1"], cwd=ROOT)
    if status:
        raise RuntimeError("study source or frozen manifest has uncommitted edits")
    files = {name: sha((ROOT / name).read_bytes()) for name in names}
    group = lambda names: sha(encoded({name: files[name] for name in sorted(names)}))
    code = [n for n in names if n.startswith("experiments/e1/")]
    kernel = [n for n in names if n.startswith("purser/")]
    def selected(*files):
        return ["experiments/e1/" + name + ".py" for name in files]
    support = selected("artifacts", "measurement", "probe")
    return {
        "suite": MANIFEST["id"], "source_files": files,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "fixture_tree": FIXTURE["tree_sha1"], "fixture_commit": FIXTURE["commit_sha1"],
        "manifest_sha256": sha((REGISTRATION / "manifest.json").read_bytes()),
        "protocol_sha256": sha((REGISTRATION / "protocol.md").read_bytes()),
        "raw_schema_sha256": sha((REGISTRATION / "raw.schema.json").read_bytes()),
        "scorer_sha256": group(selected("scorer", "outcomes") + support), "fault_driver_sha256": group(selected("executor", "run")),
        "executor_sha256": group(selected("executor") + support), "controller_sha256": group(code),
        "treatments": {arm: sha(encoded({"selected_arm": arm,
                                           "implementation": group(code + (kernel if arm == "purser" else []))}))
                       for arm in ARMS},
        "action_mapping": {"setup": "Before release, provision common fixture, scoped assignments and criteria.",
          "plan": "Release shared order, reassign after an actual diagnosis, or open one criterion run.",
          "context": "Request the assigned subject's current public state.",
          "edit": "Implement one scoped policy or remove identified extraneous paths in the private candidate.",
          "check": "Execute worker tests or review the sealed candidate using the public plan checks.",
          "submit": "Submit one immutable Git candidate for one assigned subject.",
          "observe": "Measure one enrolled criterion and record its independently produced observation.",
          "decide": "Decide one verdict or attempt one work/parent closure.",
          "example": "Hand-counted clean plan: 1 initial plan + 3 leaves x 6 + 5 parent actions = 24; "
                     "the two verifier-equipped arms add 3 operations for each of 6 criteria, total 42."},
    }


def freeze_series(directory, *, replicate=1, reason=None):
    if type(replicate) is not int or replicate < 1 or (replicate > 1 and not (reason or "").strip()):
        raise ValueError("a replication requires a positive identifier and a reason")
    directory.mkdir(exist_ok=True, parents=True)
    pin = {**source_pin(), "replicate": replicate, "replication_reason": reason}
    with (directory / "freeze.json").open("x") as output:
        json.dump(pin, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write("\n")


def schedule():
    e = MANIFEST["experiments"]["E1"]
    for rank, seed in enumerate(e["seeds"], start=1):
        j = (rank - 1) % len(ARMS)
        for arm in ARMS[j:] + ARMS[:j]:
            yield "primary", seed, rank, arm
    rank = e["seeds"].index(e["clean_seed"]) + 1
    j = (rank - 1) % len(ARMS)
    for arm in ARMS[j:] + ARMS[:j]:
        yield "clean", e["clean_seed"], rank, arm


def committed_registration(directory):
    pinned = json.loads((directory / "freeze.json").read_bytes())
    actual = source_pin()
    excluded = {"source_commit", "replicate", "replication_reason"}
    if {k: v for k, v in actual.items() if k not in excluded} != {k: v for k, v in pinned.items() if k not in excluded}:
        raise RuntimeError("study or treatment bytes differ from the precollection pin")
    replicate = pinned.get("replicate", 1)
    if type(replicate) is not int or replicate < 1 or (replicate > 1 and not pinned.get("replication_reason")):
        raise ValueError("invalid precollection replicate identity")
    subprocess.run(["git", "diff", "--quiet", pinned["source_commit"], "--",
                    *pinned["source_files"], "experiments/manifests/v1"], cwd=ROOT, check=True)
    freeze_name = (directory / "freeze.json").resolve().relative_to(ROOT)
    committed = subprocess.check_output(["git", "show", "HEAD:" + freeze_name.as_posix()], cwd=ROOT)
    if committed != (directory / "freeze.json").read_bytes():
        raise RuntimeError("the registration is not committed")
    return pinned, committed


def archive_members(path, registration):
    if not path.exists():
        return set()
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise ValueError("duplicate members in existing trial archive")
        if archive.read("freeze.json") != registration:
            raise ValueError("existing archive has a different registration")
        return set(names)


def publish(path, registration, members, key, marker, staged, raw):
    # ZIP writes change its central directory. Never modify a previously published copy.
    temp = path.with_name(".raw-next.zip")
    temp.unlink(missing_ok=True)
    if path.exists():
        shutil.copyfile(path, temp)
    name = f"runs/{key}.json"
    started = f"started/{key}.json"
    with zipfile.ZipFile(temp, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        if "freeze.json" not in members:
            archive.writestr("freeze.json", registration)
        if started in members:
            if json.loads(archive.read(started)) != marker:
                raise ValueError("existing archive marker disagrees with scheduled case")
        else:
            archive.writestr(started, encoded(marker))
        for obj in staged.namelist():
            data = staged.read(obj)
            if obj not in members:
                archive.writestr(obj, data)
            elif archive.read(obj) != data:
                raise ValueError("an existing archive object disagrees with the staged copy")
        archive.writestr(name, encoded(raw))
    with temp.open("rb") as file:
        os.fsync(file.fileno())
    os.replace(temp, path)
    sync_directory(path.parent)
    members.update(staged.namelist())
    members.update(("freeze.json", name, started))


def collect_cases(directory, pinned, registration, units, run_case, *, recover=None, failure=None):
    recover = recover or run.reconcile_interrupted
    failure = failure or failure_record
    path = directory / "raw.zip"
    members = archive_members(path, registration)
    for track, seed, rank, arm in units:
        key = f"{track}/{seed}/{arm}"
        stage = directory / f".attempt-{track}-{seed}-{arm}"
        if f"runs/{key}.json" in members:
            if stage.exists():
                shutil.rmtree(stage)
            continue
        marker = {"track": track, "seed": seed, "rank": rank, "arm": arm}
        started = stage / "started.json"
        progress_file = stage / "progress.json"
        raw_file = stage / "raw.json"
        working = stage / "trial"
        previous = stage.exists() or f"started/{key}.json" in members
        stage.mkdir(mode=0o700, exist_ok=True)
        sync_directory(directory)
        identity = {**marker, "registration_sha256": sha(registration)}
        if started.exists() and json.loads(started.read_bytes()) != identity:
            raise ValueError("staged attempt belongs to a different registration")
        staged = FileArchive(stage)
        assets = Artifacts(staged)
        if raw_file.exists():
            raw = json.loads(raw_file.read_bytes())
        else:
            def checkpoint(data):
                write_atomic(progress_file, encoded(data))

            try:
                if previous:
                    raise InterruptedError("collector exited before recording the complete trial")
                write_atomic(started, encoded(identity))
                working.mkdir()
                raw = run_case(working, assets, pinned, arm, track, seed, rank, progress=checkpoint)
            except Exception as error:
                reason = f"{type(error).__name__}: {error}"
                progress = json.loads(progress_file.read_bytes()) if progress_file.exists() else {}
                if progress or working.exists():
                    try:
                        progress = recover(working, assets, arm, progress, seed=seed, rank=rank)
                    except Exception as inspection:
                        reason += f" (recovery inspection failed: {type(inspection).__name__}: {inspection})"
                raw = failure(assets, pinned, arm, track, seed, rank, reason=reason, progress=progress)
            write_atomic(raw_file, encoded(raw))
        publish(path, registration, members, key, marker, staged, raw)
        shutil.rmtree(stage)
        print(f"{track} {seed} {arm} {raw['terminal']} {raw['cost']['steps']} steps", flush=True)


def collect(directory):
    pinned, registration = committed_registration(directory)
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        collect_cases(directory, pinned, registration, schedule(), run.attempt)
    finally:
        os.close(fd)
