"""Restore the M3-A control from original event bytes, then compare cold public CLI answers."""
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

from purser.kernel.artifacts import Artifacts
from purser.kernel.store import Store


def verify(source):
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(source) as archive:
        db = Path(tmp) / "restored.sqlite"
        Store(db).initialize()
        manifest = json.loads(archive.read("manifest.json"))
        if sorted(archive.namelist()) != sorted(["manifest.json", *manifest["members"]]):
            raise ValueError("unexpected archive membership")
        objects = Artifacts(db)
        for name, expected in manifest["members"].items():
            raw = archive.read(name)
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("member digest mismatch " + name)
            if name.startswith("objects/") and objects.put(raw) != name.removeprefix("objects/"):
                raise ValueError("incorrectly addressed evidence")
        events = archive.read("events.jsonl").splitlines()
        with closing(sqlite3.connect(db)) as dest, dest:
            dest.executemany("INSERT INTO events VALUES (?,?)", enumerate(events, 1))
        calls = json.loads(archive.read("cli.json"))
        for saved in calls:
            result = subprocess.run([sys.executable, "-B", "-m", "purser", "--db", str(db), *saved["argv"]],
                                    env={"PATH": "/usr/bin:/bin", "TZ": "UTC", "LC_ALL": "C"},
                                    check=True, capture_output=True, timeout=20)
            if result.stderr or json.loads(result.stdout) != saved["reply"]:
                raise ValueError("CLI answer changed " + repr(saved["argv"]))
        with closing(sqlite3.connect(db)) as dest:
            if [r[0] for r in dest.execute("SELECT body FROM events ORDER BY revision")] != events:
                raise ValueError("event bytes changed on replay")
        snapshot = Store(db).snapshot()
        if snapshot["head_hash"] != manifest["head_hash"] or snapshot["revision"] != manifest["revision"]:
            raise ValueError("archive head mismatch")
        return {"revision": snapshot["revision"], "head_hash": snapshot["head_hash"], "cli_processes": len(calls),
                "artifacts": sum(n.startswith("objects/") for n in manifest["members"]),
                "checkpoints": manifest["checkpoints"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    print(json.dumps(verify(parser.parse_args().archive), sort_keys=True))
