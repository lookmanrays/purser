"""Pin all study code before collection and retain each scheduled attempt."""

import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .artifacts import FIXTURE, MANIFEST, REGISTRATION, ROOT, Artifacts, encoded, sha
from .run import attempt

ARMS = MANIFEST["units"]["arm_order"]


def source_pin():
    paths = [p for base in (ROOT / "experiments/e1", ROOT / "purser") for p in base.rglob("*.py")]
    names = sorted(p.relative_to(ROOT).as_posix() for p in paths)
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all",
                                      "--", *names, "experiments/manifests/v1"], cwd=ROOT)
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
        "scorer_sha256": group(selected("scorer") + support), "fault_driver_sha256": group(selected("executor", "run")),
        "executor_sha256": group(selected("executor") + support), "controller_sha256": group(code),
        "treatments": {arm: group(code + (kernel if arm == "purser" else [])) for arm in ARMS},
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


def freeze_series(directory):
    directory.mkdir(exist_ok=True, parents=True)
    pin = source_pin()
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


def collect(directory):
    pinned = json.loads((directory / "freeze.json").read_bytes())
    actual = source_pin()
    if {k: v for k, v in actual.items() if k != "source_commit"} != {k: v for k, v in pinned.items() if k != "source_commit"}:
        raise RuntimeError("study or treatment bytes differ from the precollection pin")
    # A persisted git commit containing this exact pin precedes every collected outcome.
    freeze_name = (directory / "freeze.json").resolve().relative_to(ROOT)
    committed = subprocess.check_output(["git", "show", "HEAD:" + freeze_name.as_posix()], cwd=ROOT)
    if json.loads(committed) != pinned:
        raise RuntimeError("the registration is not committed")
    archive_file = directory / "raw.zip"
    for track, seed, rank, arm in schedule():
        name = f"runs/{track}/{seed}/{arm}.json"
        marker = name.replace("runs/", "started/")
        with zipfile.ZipFile(archive_file, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            if "freeze.json" not in archive.namelist():
                archive.writestr("freeze.json", committed)
            if archive.read("freeze.json") != committed:
                raise RuntimeError("existing archive has a different precollection pin")
            if name in archive.namelist():
                continue
            if marker in archive.namelist():
                raise RuntimeError("previously interrupted attempt retained: " + marker)
            archive.writestr(marker, encoded({"arm": arm, "track": track, "seed": seed, "rank": rank}))
        with zipfile.ZipFile(archive_file, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            with tempfile.TemporaryDirectory(prefix="purser-e1-") as tmp:
                raw = attempt(Path(tmp), Artifacts(archive), pinned, arm, track, seed, rank)
            archive.writestr(name, encoded(raw))
        print(f"{track} {seed} {arm} {raw['terminal']} {raw['cost']['steps']} steps", flush=True)
