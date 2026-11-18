"""Capture the M2 development commitment, or check its original bytes in an empty local database."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from purser.kernel.artifacts import Artifacts
from purser.kernel.store import Store
from purser.kernel.wire import canonical


def cli(database, *args):
    return json.loads(subprocess.check_output([sys.executable, "-B", "-m", "purser", "--db", str(database),
                                               *args], cwd=ROOT))


def capture(database, archive_path):
    snapshot = Store(database).snapshot()
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        rows = [raw for (raw,) in source.execute("SELECT body FROM events WHERE revision<=? ORDER BY revision",
                                                 (snapshot["revision"],))]
    events = [json.loads(raw) for raw in rows]
    if len(rows) != snapshot["revision"] or events[-1]["hash"] != snapshot["head_hash"]:
        raise ValueError("the original retained log differs from the verified head")
    if {event["data"]["id"] for event in events if event["type"] == "CommitmentCreated"} != {"C-m2-explain-work"}:
        raise ValueError("the export is scoped to the M2 development commitment")
    status = cli(database, "status", "--revision", str(snapshot["revision"]), "--as-of", events[-1]["at"])
    keys = {value for event in events for key, value in event["data"].items()
            if key in ("definition_sha256", "environment_sha256", "artifact_sha256")}
    artifacts = Artifacts(database)
    blocks = {"events.jsonl": b"\n".join(rows) + b"\n", "status.json": canonical(status)}
    for key in keys:
        blocks["objects/" + key] = artifacts.read(key)
    fixture = (Path(__file__).parent / "observer/fixture-v1.json").read_bytes()
    fixture_key = hashlib.sha256(fixture).hexdigest()
    if not any(fixture_key.encode() in blocks["objects/" + key] for key in keys):
        raise ValueError("the supplementary fixture does not belong to this evidence")
    blocks["objects/" + fixture_key] = fixture
    manifest = {"format": 1, "commitment": "C-m2-explain-work", "revision": snapshot["revision"],
                "head_hash": snapshot["head_hash"], "projection_hash": status["projection_hash"],
                "members": {path: hashlib.sha256(data).hexdigest() for path, data in sorted(blocks.items())}}
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted({**blocks, "manifest.json": canonical(manifest)}.items()):
            archive.writestr(name, data)
    return verify(archive_path)


def verify(archive_path):
    with tempfile.TemporaryDirectory(prefix="purser-m2-history-") as folder, zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != {"manifest.json", *manifest["members"]}:
            raise ValueError("incomplete or ambiguous evidence archive")
        database = Path(folder) / "restored.sqlite3"
        cli(database, "init")
        artifacts = Artifacts(database)
        for name, digest in manifest["members"].items():
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("changed archive member " + name)
            if name.startswith("objects/") and name != "objects/" + artifacts.put(data):
                raise ValueError("incorrectly addressed artifact")
        original = archive.read("events.jsonl").splitlines()
        for event in map(json.loads, original):
            for key, value in event["data"].items():
                if key in ("definition_sha256", "environment_sha256", "artifact_sha256"):
                    data = artifacts.read(value)
                    if key == "definition_sha256":
                        artifacts.read(json.loads(data)["fixture_sha256"])
        with closing(sqlite3.connect(database)) as destination:
            with destination:
                destination.executemany("INSERT INTO events(revision,body) VALUES(?,?)", enumerate(original, 1))
        expected = json.loads(archive.read("status.json"))
        observed = cli(database, "replay", "--as-of", expected["as_of"])
        if observed != cli(database, "status", "--as-of", expected["as_of"]):
            raise ValueError("cold and live views disagree")
        if any(observed[k] != manifest[k] for k in ("revision", "head_hash", "projection_hash")):
            raise ValueError("restored checkpoint differs from the captured one")
        if any(observed["answers"].get(k) != v for k, v in expected["answers"].items()):
            raise ValueError("a historical continuity answer changed")
        with closing(sqlite3.connect(database)) as destination:
            after = [raw for (raw,) in destination.execute("SELECT body FROM events ORDER BY revision")]
        if original != after:
            raise ValueError("original event bytes changed during restore")
    return {"revision": observed["revision"], "head_hash": observed["head_hash"],
            "projection_hash": observed["projection_hash"],
            "artifacts": sum(name.startswith("objects/") for name in manifest["members"]),
            "answers": sorted(expected["answers"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--capture", metavar="M2_DATABASE", type=Path)
    args = parser.parse_args()
    result = capture(args.capture, args.archive) if args.capture else verify(args.archive)
    print(json.dumps(result, sort_keys=True))
