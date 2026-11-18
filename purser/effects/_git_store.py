"""Native Git access for a launcher-owned, instance-bound disposable repository."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys

from ..kernel.rules import HASH, ID, candidate, matches
from ..kernel.wire import KernelError, digest


class GitError(KernelError):
    """The broker cannot use this resource or establish the requested Git fact."""


def require(condition, code):
    if not condition:
        raise GitError(code)


def oid(value, algorithm):
    return candidate({"kind": "git.commit", "algorithm": algorithm, "digest": value})


_GIT = shutil.which("git", path=os.defpath)
_ENV = {
    "PATH": os.defpath, "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0",
    "GIT_ALLOW_PROTOCOL": "", "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_COMMITTER_NAME": "Purser local broker", "GIT_COMMITTER_EMAIL": "broker@purser.invalid",
}
# Darwin's /dev/fd/N cannot serve as cwd. fchdir before exec pins every Git
# process to the already opened directory, even if its former path is replaced.
_EXEC = "import os,sys; os.fchdir(int(sys.argv[1])); os.execv(sys.argv[2],sys.argv[2:])"


class Native:
    def __init__(self, fd):
        self.fd = fd

    def run(self, *args, data=None, statuses=(0,)):
        require(_GIT is not None, "git_unavailable")
        command = [sys.executable, "-IS", "-c", _EXEC, str(self.fd), _GIT, "--git-dir=.",
                   "-c", "core.hooksPath=" + os.devnull, "-c", "maintenance.auto=false",
                   "-c", "gc.auto=0", *args]
        try:
            result = subprocess.run(command, pass_fds=(self.fd,), env=_ENV, input=data,
                                    capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError("git_unavailable") from exc
        # In particular, a warning about an omitted/broken ref is not a full observation.
        require(result.returncode in statuses, "git_command_failed")
        require(result.returncode != 0 or not result.stderr, "git_command_failed")
        return result

    def output(self, *args, data=None):
        return self.run(*args, data=data).stdout

    def object_is_commit(self, value):
        require(self.output("cat-file", "-t", value) == b"commit\n", "not_a_commit")

    def ancestor(self, old, target):
        return self.run("merge-base", "--is-ancestor", old, target, statuses=(0, 1)).returncode == 0

    def changed_paths(self, old, target):
        # Every commit newly made reachable participates, including roots and all
        # merge parents. A modification reverted before the final tree still counts.
        commits = self.output("rev-list", target, "^" + old)
        raw = self.output("diff-tree", "--stdin", "--root", "-m", "-r", "--no-commit-id",
                          "--name-only", "-z", "--no-renames", "--no-ext-diff",
                          "--no-textconv", "--ignore-submodules=none", data=commits)
        try:
            return sorted({item.decode("utf-8") for item in raw.split(b"\0") if item})
        except UnicodeError as exc:
            raise GitError("unrepresentable_git_path") from exc

    def log(self, ref):
        # The native reflog enumerates newest first. Reverse it to recover the
        # predecessor of each transition from the protected bootstrap onward.
        raw = self.output("reflog", "show", "--format=%H%x09%gs", "--no-color", ref, "--")
        rows = []
        for line in raw.splitlines():
            value, tab, message = line.partition(b"\t")
            require(tab, "history_unreadable")
            rows.append((value.decode("ascii"), message.decode("utf-8")))
        return list(reversed(rows))

    def ref(self, ref):
        exists = self.run("show-ref", "--exists", ref, statuses=(0, 2)).returncode
        if exists == 2:
            return None
        direct = self.run("symbolic-ref", "--quiet", "--no-recurse", ref, statuses=(0, 1))
        require(direct.returncode == 1, "symbolic_git_ref")
        return self.output("show-ref", "--verify", "--hash", ref).decode("ascii").strip()


def _config_hash(fd):
    config = os.open("config", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    with os.fdopen(config, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def _exists(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _identity(binding):
    return digest("git-repository", {k: v for k, v in binding.items() if k != "repository_instance"})


def validate_binding(binding):
    keys = {"v", "repository", "git_dir", "device", "inode", "resource", "algorithm",
            "initial_oid", "nonce", "config_sha256", "repository_instance"}
    require(type(binding) is dict and binding.keys() == keys and type(binding["v"]) is int
            and binding["v"] == 1 and matches(ID, binding["repository"])
            and type(binding["git_dir"]) is str and Path(binding["git_dir"]).is_absolute()
            and type(binding["device"]) is int and type(binding["inode"]) is int
            and type(binding["resource"]) is str and binding["resource"].startswith("refs/heads/")
            and oid(binding["initial_oid"], binding["algorithm"])
            and matches(HASH, binding["nonce"]) and matches(HASH, binding["config_sha256"])
            and binding["repository_instance"] == _identity(binding), "invalid_repository_binding")


@contextmanager
def lease(binding, *, write):
    validate_binding(binding)
    path = Path(binding["git_dir"])
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            require((st.st_dev, st.st_ino) == (binding["device"], binding["inode"])
                    and st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) == 0o700
                    and path.resolve(strict=True) == path, "repository_instance_mismatch")
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise GitError("repository_busy" if write else "writer_may_still_apply") from exc
            require(_config_hash(fd) == binding["config_sha256"]
                    and not any(_exists(fd, p) for p in
                                ("commondir", "shallow", "info/grafts", "objects/info/alternates",
                                 "objects/info/http-alternates")), "repository_configuration_changed")
            native = Native(fd)
            fmt = native.output("rev-parse", "--is-bare-repository", "--show-object-format", "--show-ref-format")
            require(fmt.splitlines() == [b"true", binding["algorithm"].encode("ascii"), b"reftable"],
                    "repository_configuration_changed")
            require(not _exists(fd, "reftable/tables.list.lock"), "native_transaction_unfinished")
            yield native
        finally:
            # No explicit LOCK_UN. A possibly surviving Git child shares this
            # open-file description, and remains fenced until its last close.
            os.close(fd)
    except OSError as exc:
        raise GitError("repository_unavailable") from exc


def initialize(git_dir, repository_id, branch, source_git_dir, initial_oid, candidates):
    require(matches(ID, repository_id) and type(branch) is str and branch.startswith("refs/heads/"),
            "invalid_repository_binding")
    target = Path(git_dir).absolute()
    try:
        path = target.parent.resolve(strict=True) / target.name
        source_path = Path(source_git_dir).resolve(strict=True)
        source_fd = os.open(source_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            source = Native(source_fd)
            algorithm = source.output("rev-parse", "--show-object-format").decode("ascii").strip()
            objects = (initial_oid, *candidates)
            require(all(oid(item, algorithm) for item in objects), "invalid_git_object")
            source.run("check-ref-format", branch)
            for item in objects:
                source.object_is_commit(item)
            pack = source.output("pack-objects", "--stdout", "--revs",
                                 data=("\n".join(objects) + "\n").encode("ascii"))
        finally:
            os.close(source_fd)
        # Exclusive creation is the only bootstrap entry point. Never adopt a
        # populated Git directory or redirect the staging repository's refs.
        path.mkdir(mode=0o700)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            native = Native(fd)
            native.run("init", "-q", "--bare", "--template=", "--ref-format=reftable",
                       "--object-format=" + algorithm, ".")
            for key, value in (("core.logAllRefUpdates", "always"), ("gc.reflogExpire", "never"),
                               ("gc.reflogExpireUnreachable", "never")):
                native.run("config", "--local", key, value)
            native.run("index-pack", "--strict", "--stdin", data=pack)
            binding = {"v": 1, "repository": repository_id, "git_dir": str(path),
                       "device": st.st_dev, "inode": st.st_ino, "resource": branch, "algorithm": algorithm,
                       "initial_oid": initial_oid, "config_sha256": _config_hash(fd), "nonce": secrets.token_hex(32)}
            binding["repository_instance"] = _identity(binding)
            native.run("update-ref", "--create-reflog", "--no-deref", "-m",
                       "purser.init " + binding["repository_instance"], branch, initial_oid, "0" * len(initial_oid))
            return binding
        except BaseException:
            if os.path.samestat(st, path.stat(follow_symlinks=False)):
                shutil.rmtree(path)
            raise
        finally:
            os.close(fd)
    except FileExistsError as exc:
        raise GitError("repository_already_exists") from exc
    except OSError as exc:
        raise GitError("repository_unavailable") from exc
