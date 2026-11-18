"""Shared semantic admission failures and identifier grammar."""
import re

from .wire import KernelError

ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,95}")
HASH = re.compile(r"[0-9a-f]{64}")


class Refusal(KernelError):
    """A valid first delivery whose semantic rejection must survive restart."""


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None
