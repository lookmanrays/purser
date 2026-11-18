"""Observe candidate calls in a separate interpreter and preserve their exact return values."""

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

START = datetime(2001, 1, 1, 12)


def invalid(value):
    kind = type(value).__name__
    result = {"invalid_type": kind}
    if type(value) in (str, int, bool, float, bytes) or value is None:
        result["representation"] = ascii(value)
    return result


def identity(value):
    if type(value) is str:
        try:
            value.encode("utf-8")
            return value
        except UnicodeEncodeError:
            pass
    return invalid(value)


def due_time(value):
    if type(value) is not datetime or value.tzinfo is not None:
        return invalid(value)
    delta = value - START
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def one(queue_type, case):
    queue = queue_type(START)
    count = queue.enqueue(case["channel"], case["ids"], disabled=case["disabled"],
                          attempts=case["attempts"], base=case["base"])
    samples = []
    for offset in case["times"]:
        deliveries = queue.advance(START + timedelta(seconds=offset))
        samples.append([[identity(d.event_id), identity(d.channel), due_time(d.due_at)] for d in deliveries])
    return {"enqueued": count if type(count) is int else invalid(count),
            "samples": samples, "pending": len(queue.scheduler.get_jobs())}


def main():
    spec = json.load(sys.stdin)
    sys.path.insert(0, str(Path(spec["root"])))
    if spec["mode"] == "legacy":
        suite = unittest.defaultTestLoader.discover(spec["tests"])
        result = unittest.TestResult()
        suite.run(result)
        if not result.testsRun:
            raise RuntimeError("protected suite produced no executed tests")
        reply = {"tests": result.testsRun, "errors": [str(t) for t, _ in result.errors],
                 "failures": [str(t) for t, _ in result.failures],
                 "skipped": [str(t) for t, _ in result.skipped]}
    else:
        try:
            from notify.service import NotificationQueue
        except Exception as error:
            reply = [{"candidate_exception": type(error).__name__, "stage": "import"} for _ in spec["cases"]]
        else:
            reply = []
            for case in spec["cases"]:
                try:
                    reply.append(one(NotificationQueue, case))
                except Exception as error:
                    reply.append({"candidate_exception": type(error).__name__, "stage": "queue"})
    json.dump({"measured": reply}, sys.stdout, separators=(",", ":"), allow_nan=False)


if __name__ == "__main__":
    main()
