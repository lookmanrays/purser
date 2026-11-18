"""Launch protected instrumentation. Only a measured candidate return is behavioral evidence."""

import json
import subprocess
import sys
from pathlib import Path

from .artifacts import FIXTURE, REGISTRATION, encoded, sha


class MeasurementUnavailable(RuntimeError):
    """The measurement process could not produce an attributable candidate observation."""


def same(observed, expected):
    # JSON encodings distinguish 1, true and 1.0 despite ordinary Python equality.
    return encoded(observed) == encoded(expected)


def valid_sample(row, case):
    if type(row) is not dict:
        return False
    if row.keys() == {"candidate_exception", "stage"}:
        return type(row["candidate_exception"]) is str and row["stage"] in ("import", "queue")
    if row.keys() != {"enqueued", "samples", "pending"}:
        return False
    def observed(value, expected_type):
        return type(value) is expected_type or (
            type(value) is dict and value.keys() in ({"invalid_type"}, {"invalid_type", "representation"})
            and all(type(v) is str for v in value.values()))
    samples = row["samples"]
    return (observed(row["enqueued"], int) and type(row["pending"]) is int and row["pending"] >= 0
            and type(samples) is list and len(samples) == len(case["times"])
            and all(type(batch) is list and all(type(d) is list and len(d) == 3
                    and observed(d[0], str) and observed(d[1], str) and observed(d[2], int) for d in batch)
                    for batch in samples))


def valid_legacy(data):
    return (type(data) is dict and data.keys() == {"tests", "errors", "failures", "skipped"}
            and type(data["tests"]) is int and data["tests"] > 0
            and all(type(data[k]) is list and all(type(v) is str for v in data[k])
                    for k in ("errors", "failures", "skipped")))


def probe(root, *, cases=None, legacy=False):
    tests = REGISTRATION / "input/tests"
    try:
        if not Path(root).is_dir():
            raise MeasurementUnavailable("candidate checkout unavailable")
        if legacy:
            expected = {r["path"]: r["sha256"] for r in FIXTURE["files"] if r["path"].startswith("tests/")}
            actual = {"tests/" + p.relative_to(tests).as_posix(): sha(p.read_bytes())
                      for p in tests.rglob("*") if p.is_file()}
            if expected != actual:
                raise MeasurementUnavailable("frozen public test bytes unavailable")
        spec = {"root": str(root), "mode": "legacy" if legacy else "queue", "tests": str(tests), "cases": cases}
        result = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).with_name("probe.py"))],
                                input=encoded(spec), capture_output=True, timeout=5)
        if result.returncode:
            raise MeasurementUnavailable(f"measurement process exited {result.returncode}")
        envelope = json.loads(result.stdout)
        if type(envelope) is not dict or envelope.keys() != {"measured"}:
            raise MeasurementUnavailable("invalid measurement response")
        data = envelope["measured"]
        if legacy:
            if not valid_legacy(data):
                raise MeasurementUnavailable("incomplete protected test response")
        elif (type(data) is not list or len(data) != len(cases)
              or not all(valid_sample(row, case) for row, case in zip(data, cases, strict=True))):
            raise MeasurementUnavailable("incomplete queue measurement response")
        return data
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError) as error:
        raise MeasurementUnavailable(f"measurement protocol unavailable: {type(error).__name__}") from error
