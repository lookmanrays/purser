"""Run a notification queue against a caller-supplied local clock."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import schedule

from .delivery import select
from .preferences import allowed
from .retry import delays


@dataclass(frozen=True)
class Delivery:
    event_id: str
    channel: str
    due_at: datetime


class NotificationQueue:
    def __init__(self, start: datetime):
        if start.tzinfo is not None:
            raise ValueError("start must be a naive local datetime")
        self.now = start
        self.scheduler = schedule.Scheduler()
        self._outbox = []

    def enqueue(self, channel, event_ids, disabled=(), attempts=1, base=1):
        if not channel or attempts < 1 or base < 1:
            raise ValueError("channel, positive attempts and base are required")
        if not allowed(channel, disabled):
            return 0

        selected = select(event_ids)
        count = 0
        for seconds in delays(attempts, base):
            due_at = self.now + timedelta(seconds=seconds)
            for event_id in selected:
                delivery = Delivery(event_id, channel, due_at)
                job = self.scheduler.every().second.do(self._emit, delivery)
                job.tag(channel)
                job.next_run = due_at
                count += 1
        return count

    def cancel(self, channel):
        """Cancel every callback associated with the channel."""
        self.scheduler.clear(channel)

    def advance(self, until: datetime):
        """Collect one-shot deliveries due through the given instant."""
        if until < self.now:
            raise ValueError("queue time must advance")
        self.now = until
        jobs = sorted(
            job for job in self.scheduler.get_jobs() if job.next_run <= until
        )
        for job in jobs:
            outcome = job.run()
            if outcome is schedule.CancelJob:
                self.scheduler.cancel_job(job)
        delivered = tuple(self._outbox)
        self._outbox.clear()
        return delivered

    def _emit(self, delivery):
        self._outbox.append(delivery)
        return schedule.CancelJob
