"""Freeze the bytes of one disposable fixture, independent of its working directory."""

import hashlib
import os
import re
import stat
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from experiments.e1.artifacts import FIXTURE, REGISTRATION, START, encoded, git, sha
from experiments.e1.measurement import MeasurementUnavailable
from experiments.wasi_runtime import MAX_INPUT


MAX_FILES = 128
REGISTERED = {row["path"]: row["sha256"] for row in FIXTURE["files"]}


def _object_id(kind, data):
    return hashlib.sha1(kind + b" " + str(len(data)).encode("ascii") + b"\0" + data).digest()


class _Image:
    def __init__(self):
        self.files = {}
        self.names = {}
        self.size = 0

    def add(self, name, mode, data):
        path = PurePosixPath(name)
        if (not path.parts or path.is_absolute() or path.as_posix() != name or ".." in path.parts
                or any(part.casefold() == ".git" for part in path.parts) or len(name.encode()) > 512):
            raise MeasurementUnavailable("fixture contains an unsupported path")
        if mode not in ("100644", "100755"):
            raise MeasurementUnavailable("fixture contains a non-regular Git entry")
        # Different spellings may resolve to the same file or directory on this host.
        for n in range(1, len(path.parts) + 1):
            spelling = "/".join(path.parts[:n])
            alias = unicodedata.normalize("NFD", spelling).casefold()
            component = spelling, n == len(path.parts)
            if self.names.setdefault(alias, component) != component:
                raise MeasurementUnavailable("fixture contains aliased path components")
        self.size += len(data)
        if name in self.files or len(self.files) >= MAX_FILES or self.size > MAX_INPUT:
            raise MeasurementUnavailable("fixture exceeds the distinct-file or byte limit")
        self.files[name] = (mode, data)

    def snapshot(self, oid=None):
        hierarchy = {}
        rows = []
        for name, (mode, data) in sorted(self.files.items()):
            node = hierarchy
            *dirs, leaf = name.split("/")
            for part in dirs:
                node = node.setdefault(part, {})
            node[leaf] = (mode.encode("ascii"), _object_id(b"blob", data))
            rows.append((name, mode, data))

        def tree(node):
            entries = []
            for name, child in node.items():
                key = name.encode("utf-8")
                directory = isinstance(child, dict)
                mode, blob = (b"40000", tree(child)) if directory else child
                entries.append((key + (b"/" if directory else b""), mode + b" " + key + b"\0" + blob))
            return _object_id(b"tree", b"".join(body for _, body in sorted(entries)))

        manifest = [[name, mode, len(data), sha(data)] for name, mode, data in rows]
        return Snapshot(oid, tree(hierarchy).hex(), sha(encoded(manifest)), tuple(rows))


def _fixture_git(repo, *args):
    return git(repo, "--no-replace-objects", *args)


@dataclass(frozen=True)
class Snapshot:
    oid: str | None
    tree: str
    manifest_sha256: str
    _entries: tuple

    @property
    def files(self):
        return {name: data for name, _, data in self._entries}

    @property
    def changes(self):
        current = {name: (mode, sha(data)) for name, mode, data in self._entries}
        rows = []
        for name in sorted(current.keys() | REGISTERED.keys()):
            old = ("100644", REGISTERED[name]) if name in REGISTERED else ("000000", None)
            new = current.get(name, ("000000", None))
            if old != new:
                status = "M" if old[1] and new[1] else ("A" if new[1] else "D")
                rows.append({"path": name, "old_mode": old[0], "new_mode": new[0], "status": status})
        return rows

    @classmethod
    def from_git(cls, repo, oid):
        if type(oid) is not str or re.fullmatch(r"[0-9a-f]{40}", oid) is None:
            raise MeasurementUnavailable("expected a full SHA-1 fixture commit")
        image = _Image()
        try:
            _fixture_git(repo, "merge-base", "--is-ancestor", START, oid)
            if int(_fixture_git(repo, "cat-file", "-s", oid)) > MAX_INPUT:
                raise MeasurementUnavailable("archived Git commit exceeds the byte limit")
            commit = _fixture_git(repo, "cat-file", "commit", oid)
            if _object_id(b"commit", commit).hex() != oid:
                raise MeasurementUnavailable("archived Git commit identity differs")
            raw = _fixture_git(repo, "ls-tree", "-rlz", oid)
            entries = raw.split(b"\0")[:-1]
            if len(entries) > MAX_FILES:
                raise MeasurementUnavailable("fixture exceeds the file limit")
            for entry in entries:
                header, raw_name = entry.split(b"\t", 1)
                mode, kind, blob, size = header.split()
                if kind != b"blob" or mode not in (b"100644", b"100755"):
                    raise MeasurementUnavailable("fixture contains a non-regular Git entry")
                if not 0 <= int(size) <= MAX_INPUT - image.size:
                    raise MeasurementUnavailable("fixture exceeds the byte limit")
                data = _fixture_git(repo, "cat-file", "blob", blob.decode("ascii"))
                if len(data) != int(size) or _object_id(b"blob", data).hex() != blob.decode("ascii"):
                    raise MeasurementUnavailable("archived Git blob identity differs")
                image.add(raw_name.decode("utf-8"), mode.decode("ascii"), data)
            snapshot = image.snapshot(oid)
            if commit.split(b"\n", 1)[0] != b"tree " + snapshot.tree.encode("ascii"):
                raise MeasurementUnavailable("archived Git tree differs from the captured bytes")
            return snapshot
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise MeasurementUnavailable("cannot freeze the registered Git candidate") from error

    @classmethod
    def from_worktree(cls, directory):
        root = Path(directory).resolve()
        image = _Image()
        try:
            if Path(os.fsdecode(_fixture_git(root, "rev-parse", "--show-toplevel").rstrip(b"\n"))).resolve() != root:
                raise MeasurementUnavailable("expected the root of the disposable fixture checkout")
            _fixture_git(root, "merge-base", "--is-ancestor", START, "HEAD")

            def walk_error(error):
                raise error

            seen_dirs = 0
            for parent, dirs, names, parent_fd in os.fwalk(root, onerror=walk_error):
                prefix = Path(parent).relative_to(root)
                if prefix == Path("."):
                    dirs[:] = [name for name in dirs if name != ".git"]
                    names = [name for name in names if name != ".git"]
                seen_dirs += 1
                if seen_dirs > MAX_FILES * 4:
                    raise MeasurementUnavailable("working fixture exceeds the directory limit")
                for name in dirs:
                    if not stat.S_ISDIR(os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode):
                        raise MeasurementUnavailable("working fixture contains a linked directory")
                for name in names:
                    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise MeasurementUnavailable("working fixture contains a linked or non-regular file")
                    if info.st_size > MAX_INPUT - image.size:
                        raise MeasurementUnavailable("working fixture exceeds the byte limit")
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
                    with os.fdopen(fd, "rb") as stream:
                        opened = os.fstat(fd)
                        if not os.path.samestat(info, opened) or opened.st_nlink != 1:
                            raise MeasurementUnavailable("working fixture file identity changed")
                        data = stream.read(MAX_INPUT - image.size + 1)
                        end = os.fstat(fd)
                    if (len(data), end.st_mtime_ns, end.st_mode) != (info.st_size, info.st_mtime_ns, info.st_mode):
                        raise MeasurementUnavailable("working fixture file changed during capture")
                    image.add((prefix / name).as_posix(), "100755" if info.st_mode & stat.S_IXUSR else "100644", data)
            return image.snapshot()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise MeasurementUnavailable("cannot freeze the disposable fixture worktree") from error


def trusted_fixture():
    """Read only manifest-named originals. The local public controller imports these copies."""
    files = {}
    for name, digest in REGISTERED.items():
        with (REGISTRATION / "input" / name).open("rb") as stream:
            data = stream.read(MAX_INPUT + 1)
        if sha(data) != digest:
            raise MeasurementUnavailable("registered public fixture bytes differ")
        files[name] = data
    return files
