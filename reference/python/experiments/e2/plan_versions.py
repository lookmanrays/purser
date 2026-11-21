"""Immutable bytes behind the plan's atomically published head; invisible to the executor."""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import json

from experiments.e1.artifacts import encoded, sha
from experiments.e1.executor import PARENT
from experiments.e1.recovery import write_atomic


KEY = "_e2_plan_version"
_expected = ContextVar("expected_plan_head", default=None)
_writer_paths = ContextVar("plan_writers", default=frozenset())
_delivery = ContextVar("plan_delivery", default=None)


class HeadMoved(Exception):
    """The participant changed after the fixture independently qualified its precondition."""


@contextmanager
def expect_head(digest):
    token = _expected.set(digest)
    try:
        yield
    finally:
        _expected.reset(token)


@contextmanager
def writer(path):
    held = _writer_paths.get()
    if path in held:
        yield
        return
    with (path.parent / "plan.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        expected = _expected.get()
        if expected is not None and (not path.exists() or sha(path.read_bytes()) != expected):
            raise HeadMoved("the qualified plan version changed")
        token = _writer_paths.set(held | {path})
        try:
            yield
        finally:
            _writer_paths.reset(token)


@contextmanager
def delivery(identity):
    token = _delivery.set({"attempt": identity, "actor": "fixture_observer"})
    try:
        yield
    finally:
        _delivery.reset(token)


def invocation(actions):
    if _delivery.get() is not None:
        return _delivery.get()
    action = actions.current if actions is not None else None
    if action is not None:
        return {"attempt": action["attempt"], "actor": action["actor"]}
    return None


def publish(path, rows, attempt):
    versions = path.parent / "plan-versions"
    versions.mkdir(exist_ok=True)
    prior = path.read_bytes() if path.exists() else None
    number = json.loads(prior)[PARENT][KEY]["number"] + 1 if prior else 1
    stamp(rows, number, sha(prior) if prior else None, attempt)
    contents = encoded(rows)
    write_atomic(versions / sha(contents), contents)
    write_atomic(path, contents)


def stamp(rows, number, previous, attempt):
    rows[PARENT][KEY] = {"number": number, "previous": previous, "attempt": attempt}


def retain(directory, assets, head):
    cursor = sha(head)
    history = []
    while cursor:
        if not isinstance(cursor, str) or len(cursor) != 64 or any(c not in "0123456789abcdef" for c in cursor):
            raise ValueError("invalid published plan digest")
        data = (directory / "plan-versions" / cursor).read_bytes()
        if sha(data) != cursor or not history and data != head:
            raise ValueError("published plan version disagrees with its content address")
        row = json.loads(data)
        stamp = row[PARENT][KEY]
        if type(stamp["number"]) is not int or stamp["number"] < 1:
            raise ValueError("invalid plan publication number")
        if history and stamp["number"] != history[-1][0] - 1:
            raise ValueError("missing or repeated plan publication")
        history.append((stamp["number"], assets.put(row, "json")))
        cursor = stamp["previous"]
    if history[-1][0] != 1:
        raise ValueError("missing initial plan publication")
    return [ref for number, ref in reversed(history)]
