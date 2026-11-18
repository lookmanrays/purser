"""Durable local SHA-256 evidence bytes. All paths are derived from digests."""
import hashlib
import os
import tempfile
from pathlib import Path

from .rules import HASH, matches
from .wire import KernelError


class Artifacts:
    def __init__(self, db_path):
        self.directory = Path(str(db_path) + ".artifacts")

    def read(self, sha256):
        if not matches(HASH, sha256):
            raise KernelError("invalid_input")
        try:
            raw = (self.directory / sha256).read_bytes()
        except OSError as exc:
            raise KernelError("unavailable", {"artifact": sha256}) from exc
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise KernelError("integrity_error", {"artifact": sha256})
        return raw

    def put(self, raw):
        if type(raw) is not bytes or len(raw) > 8 * 1024 * 1024:
            raise KernelError("invalid_input")
        key = hashlib.sha256(raw).hexdigest()
        try:
            self.directory.mkdir(mode=0o700, exist_ok=True)
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
                        descriptor = os.open(self.directory, os.O_RDONLY)
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                    finally:
                        temporary.unlink(missing_ok=True)
            self.read(key)
        except OSError as exc:
            raise KernelError("unavailable", {"artifact": key}) from exc
        return key
