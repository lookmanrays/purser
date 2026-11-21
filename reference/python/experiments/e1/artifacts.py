"""Content-addressed observation artifacts and private immutable Git candidates."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = ROOT / "experiments/manifests/v1"
MANIFEST = json.loads((REGISTRATION / "manifest.json").read_bytes())
FIXTURE = MANIFEST["fixture"]
START = FIXTURE["commit_sha1"]
GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Purser fixture", "GIT_COMMITTER_NAME": "Purser fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": FIXTURE["git"]["timestamp"], "GIT_COMMITTER_DATE": FIXTURE["git"]["timestamp"],
}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(path, *args, data=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.autocrlf=false", *args],
        cwd=path, env={**env, **GIT_ENV}, input=data, capture_output=True, check=True, timeout=15,
    ).stdout


class Artifacts:
    def __init__(self, archive):
        self.archive = archive
        self.present = set(archive.namelist())

    def put(self, raw, suffix):
        if suffix == "json":
            raw = encoded(raw)
        checksum = sha(raw)
        name = f"objects/{checksum}.{suffix}"
        if name not in self.present:
            self.archive.writestr(name, raw, compress_type=zipfile.ZIP_DEFLATED)
            self.present.add(name)
        return {"path": name, "sha256": checksum, "bytes": len(raw)}


def read_artifact(archive, reference):
    raw = archive.read(reference["path"])
    if len(raw) != reference["bytes"] or sha(raw) != reference["sha256"]:
        raise ValueError("artifact identity mismatch: " + reference["path"])
    return raw


class Candidates:
    def __init__(self, directory, artifacts):
        self.work = directory / "worker"
        self.archive = directory / "private-objects"
        self.artifacts = artifacts
        self.entries = {}
        shutil.copytree(REGISTRATION / "input", self.work)
        expected = {r["path"]: r["sha256"] for r in FIXTURE["files"]}
        actual = {p.relative_to(self.work).as_posix(): sha(p.read_bytes())
                  for p in self.work.rglob("*") if p.is_file()}
        if actual != expected:
            raise ValueError("starting bytes differ from preregistration")
        git(self.work, "init", "-q", "--object-format=sha1")
        self.archive.mkdir()
        git(self.archive, "init", "-q", "--bare", "--object-format=sha1")
        self.head = None
        if self.commit(FIXTURE["git"]["commit_message"]) != START or self.entries[START]["tree"] != FIXTURE["tree_sha1"]:
            raise ValueError("starting Git identity mismatch")

    def commit(self, message):
        git(self.work, "add", "-f", "--all", "--", ".")
        tree = git(self.work, "write-tree").decode().strip()
        if self.head and self.entries[self.head]["tree"] == tree:
            return self.head
        parent = self.head
        args = ["commit-tree", tree] + (["-p", parent] if parent else [])
        oid = git(self.work, *args, data=(message.rstrip("\n") + "\n").encode()).decode().strip()
        git(self.work, "update-ref", "HEAD", oid)
        pack = git(self.work, "pack-objects", "--stdout", "--revs", data=(oid + "\n").encode())
        git(self.archive, "index-pack", "--stdin", data=pack)
        self.entries[oid] = {"tree": tree, "parent": parent, "start": START,
                             "pack": self.artifacts.put(pack, "pack")}
        self.head = oid
        return oid

    def export(self, oid):
        return extract_tree(self.archive, oid)


def extract_tree(repo, oid):
    location = tempfile.TemporaryDirectory(prefix="purser-candidate-")
    raw = git(repo, "archive", "--format=tar", oid)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        tar.extractall(location.name, filter="data")
    return location


def changes(repo, oid):
    raw = git(repo, "diff", "--raw", "--no-renames", "-z", START, oid)
    fields = raw.split(b"\0")[:-1]
    return [{"path": path.decode(), "old_mode": status.split()[0][1:].decode(),
             "new_mode": status.split()[1].decode(), "status": status.split()[-1].decode()}
            for status, path in zip(fields[::2], fields[1::2], strict=True)]
