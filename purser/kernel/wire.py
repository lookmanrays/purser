"""Canonical wire values and shared error codes for Purser v1."""
import hashlib
import json
from datetime import datetime, timezone

ZERO_HASH = "0" * 64
MAX_INTEGER = 2**53 - 1


class KernelError(Exception):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.code = code
        self.details = {} if details is None else details


def _check(value):
    if value is None or type(value) is bool:
        return
    if type(value) is int and -MAX_INTEGER <= value <= MAX_INTEGER:
        return
    if type(value) is str:
        value.encode("utf-8")
        return
    if type(value) is list:
        for item in value:
            _check(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("non-string key")
            key.encode("utf-8")
            _check(item)
        return
    raise ValueError("unsupported JSON value")


def canonical(value):
    try:
        _check(value)
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, RecursionError) as exc:
        raise KernelError("invalid_input") from exc


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _noninteger(token):
    raise ValueError("noninteger JSON number")


def decode(raw):
    try:
        if type(raw) is bytes:
            raw = raw.decode("utf-8")
        if type(raw) is not str:
            raise ValueError("expected JSON bytes or text")
        value = json.loads(raw, object_pairs_hook=_unique, parse_float=_noninteger,
                           parse_constant=_noninteger)
        _check(value)
        return value
    except (ValueError, RecursionError) as exc:
        raise KernelError("invalid_input") from exc


def digest(tag, value):
    prefix = b"purser/" + tag.encode("ascii") + b"/v1\0"
    return hashlib.sha256(prefix + canonical(value)).hexdigest()


def format_time(value):
    try:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("expected an aware datetime")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")[:-6] + "Z"
    except (ValueError, OverflowError) as exc:
        raise KernelError("invalid_input") from exc


def parse_time(value):
    try:
        if type(value) is not str or len(value) != 27 or not value.endswith("Z"):
            raise ValueError("invalid canonical time")
        instant = datetime.fromisoformat(value[:-1] + "+00:00")
        if format_time(instant) != value:
            raise ValueError("noncanonical time")
        return instant
    except (ValueError, OverflowError) as exc:
        raise KernelError("invalid_input") from exc
