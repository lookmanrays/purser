"""Durable local SHA-256 evidence bytes. All paths are derived from digests."""
import hashlib
import os
import tempfile
from pathlib import Path

from .rules import HASH, matches
from .wire import KernelError


MAX_WITNESS_BYTES = 8 * 1024 * 1024


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Artifacts:
    def __init__(self, db_path):
        self.directory = Path(str(db_path) + ".artifacts")

    def read(self, sha256):
        if not matches(HASH, sha256):
            raise KernelError("invalid_input")
        try:
            with (self.directory / sha256).open("rb") as stream:
                raw = stream.read(MAX_WITNESS_BYTES + 1)
        except OSError as exc:
            raise KernelError("unavailable", {"artifact": sha256}) from exc
        if len(raw) > MAX_WITNESS_BYTES or hashlib.sha256(raw).hexdigest() != sha256:
            raise KernelError("integrity_error", {"artifact": sha256})
        return raw

    def put(self, raw):
        if type(raw) is not bytes or len(raw) > MAX_WITNESS_BYTES:
            raise KernelError("invalid_input")
        key = hashlib.sha256(raw).hexdigest()
        try:
            try:
                self.directory.mkdir(mode=0o700)
                _sync_directory(self.directory.parent)
            except FileExistsError:
                pass
            if not (self.directory / key).exists():
                with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as out:
                    temporary = Path(out.name)
                    try:
                        out.write(raw)
                        out.flush()
                        os.fsync(out.fileno())
                        try:
                            os.link(temporary, self.directory / key)
                        except FileExistsError:
                            pass
                        _sync_directory(self.directory)
                    finally:
                        temporary.unlink(missing_ok=True)
            self.read(key)
        except OSError as exc:
            raise KernelError("unavailable", {"artifact": key}) from exc
        return key
