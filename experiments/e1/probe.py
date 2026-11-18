"""Produce queue observations. Run as a separate isolated interpreter with candidate paths."""

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


def main():
    specification = json.load(sys.stdin)
    root = Path(specification["root"])
    sys.path.insert(0, str(root))
    if specification["mode"] == "legacy":
        suite = unittest.defaultTestLoader.discover(specification["tests"])
        result = unittest.TestResult()
        suite.run(result)
        reply = {"tests": result.testsRun, "errors": [str(t) for t, _ in result.errors],
                 "failures": [str(t) for t, _ in result.failures],
                 "skipped": [str(t) for t, _ in result.skipped]}
    else:
        from notify.service import NotificationQueue

        reply = []
        start = datetime(2001, 1, 1, 12)
        for case in specification["cases"]:
            queue = NotificationQueue(start)
            count = queue.enqueue(case["channel"], case["ids"], disabled=case["disabled"],
                                  attempts=case["attempts"], base=case["base"])
            samples = []
            for offset in case["times"]:
                deliveries = queue.advance(start + timedelta(seconds=offset))
                samples.append([[d.event_id, d.channel, int((d.due_at - start).total_seconds())]
                                for d in deliveries])
            reply.append({"enqueued": count, "samples": samples,
                          "pending": len(queue.scheduler.get_jobs())})
    json.dump(reply, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
