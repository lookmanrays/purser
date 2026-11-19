"""Trusted controller subprocess. The only importable fixture bytes are manifest originals."""

import json
import sys
import unittest
from functools import partial
from pathlib import Path
from types import ModuleType

# The parent launches this trusted file with Python's isolated mode and no site imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from legacy_guest import pack, unpack


def exception_class(description):
    if (type(description) is not dict or description.keys() != {"type", "value_error"}
            or type(description["type"]) is not str or type(description["value_error"]) is not bool):
        raise ValueError("invalid observed exception")
    return ValueError if description["value_error"] else RuntimeError


def result_groups(plan, observations, delivery):
    if type(observations) is not list or len(observations) != len(plan):
        raise ValueError("missing or extra public queue observations")
    groups = []
    for calls, rows in zip(plan, observations, strict=True):
        if type(rows) is not list or len(rows) != len(calls):
            raise ValueError("missing or extra public queue operations")
        group = []
        for row in rows:
            if type(row) is dict and row.keys() == {"value"}:
                group.append((None, unpack(row["value"], delivery)))
            elif type(row) is dict and row.keys() == {"exception"}:
                group.append((exception_class(row["exception"]), None))
            else:
                raise ValueError("invalid public queue response")
        groups.append(group)
    return groups


class QueuePort:
    def __init__(self, call):
        self._call = call

    def __getattr__(self, name):
        if name not in ("enqueue", "advance", "cancel"):
            raise AttributeError(name)
        return partial(self._call, name)


def operands(name, args, kwargs):
    return [name, [pack(item) for item in args], {key: pack(value) for key, value in kwargs.items()}]


def main():
    root = Path(sys.argv[1])
    request = json.load(sys.stdin)
    sys.path.insert(0, str(root))
    import notify.service as trusted

    original_queue = trusted.NotificationQueue
    preparing = request is None
    plan = [] if preparing else request["plan"]
    used_groups = []
    protocol_errors = []

    def record(*args, **kwargs):
        group = [operands("new", args, kwargs)]
        plan.append(group)
        queue = original_queue(*args, **kwargs)

        def call(name, *args, **kwargs):
            group.append(operands(name, args, kwargs))
            return getattr(queue, name)(*args, **kwargs)
        return QueuePort(call)

    def replay(*args, **kwargs):
        index = len(used_groups)
        used_groups.append(index)
        events = iter(zip(plan[index], groups[index], strict=True))

        def call(name, *args, **kwargs):
            event = next(events, None)
            if event is None or event[0] != operands(name, args, kwargs):
                protocol_errors.append("original public test issued a different queue call")
                raise RuntimeError(protocol_errors[-1])
            kind, value = event[1]
            if kind:
                raise kind("observed candidate exception")
            return value

        call("new", *args, **kwargs)
        return QueuePort(call)

    import_error = False
    if preparing:
        trusted.NotificationQueue = record
    else:
        measured = request["measured"]
        if type(measured) is not dict:
            raise ValueError("invalid public response")
        if measured.keys() == {"import_exception"}:
            exception_class(measured["import_exception"])
            # Let the original unittest discovery record its own module-import failure.
            sys.modules["notify.service"] = ModuleType("notify.service")
            import_error = True
        elif measured.keys() == {"observations"}:
            groups = result_groups(plan, measured["observations"], trusted.Delivery)
            trusted.NotificationQueue = replay
        else:
            raise ValueError("expected observed public operations")

    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    result = unittest.TestResult()
    suite.run(result)
    result = {"tests": result.testsRun, "errors": [str(t) for t, _ in result.errors],
              "failures": [str(t) for t, _ in result.failures], "skipped": [str(t) for t, _ in result.skipped]}
    if protocol_errors or (not preparing and not import_error and len(used_groups) != len(plan)):
        raise ValueError("original public suite differs from the captured operation plan")
    if preparing and (result != {"tests": 28, "errors": [], "failures": [], "skipped": []} or len(plan) != 6):
        raise ValueError("original public suite is unavailable on this controller")
    json.dump(plan if preparing else result, sys.stdout, allow_nan=False)


if __name__ == "__main__":
    main()
