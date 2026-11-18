"""Shared semantic admission failures and identifier grammar."""
import re

from .wire import KernelError

ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,95}")
HASH = re.compile(r"[0-9a-f]{64}")


class Refusal(KernelError):
    """A valid first delivery whose semantic rejection must survive restart."""


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def candidate(value):
    if type(value) is not dict or value.keys() != {"kind", "algorithm", "digest"}:
        return False
    size = {("git.commit", "sha1"): 40, ("git.commit", "sha256"): 64, ("artifact.bytes", "sha256"): 64}
    return (all(type(v) is str for v in value.values())
            and re.fullmatch(r"[0-9a-f]+", value["digest"]) is not None
            and len(value["digest"]) == size.get((value["kind"], value["algorithm"])))
