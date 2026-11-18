"""Measure the released participant clock and audit the private scoring interval."""

import signal
import time
from contextlib import contextmanager
from threading import RLock

from experiments.e1.accounting import utc_time
from experiments.e1.artifacts import MANIFEST
from experiments.e1.report import require
from experiments.e1.run import Cutoff, Journal, utc

LIMIT = MANIFEST["budgets"]["scripted"]["max_elapsed_s"] * 10**9


def active_ns(start, end, platform):
    pause = platform.get("checkpoint_measurement")
    if pause is None:
        return end - start
    right = pause.get("end_monotonic_ns")
    overlap = pause.get("first_public_activity_ns")
    # Once a public operation enters the private interval the runtime conservatively
    # resumes its deadline. The reader independently tests physical overlap.
    until = min(t for t in (end, right, overlap) if t is not None)
    return end - start - max(0, until - max(start, pause["start_monotonic_ns"]))


class ParticipantClock(Journal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.activity_lock = RLock()

    def elapsed(self):
        return active_ns(self.start, time.monotonic_ns(), self.platform)

    def persist(self):
        with self.activity_lock:
            super().persist()

    def event(self, kind, payload):
        with self.activity_lock:
            if kind in ("action", "fault"):
                payload["issued_monotonic_ns"] = self._issue()
            super().event(kind, payload)

    def stop(self):
        with self.activity_lock:
            super().stop()

    def resume_deadline(self):
        remaining = LIMIT - self.elapsed()
        if remaining <= 0:
            raise Cutoff("time_limit")
        signal.setitimer(signal.ITIMER_REAL, remaining / 1e9)

    def _issue(self):
        if not self.released or self.end is not None:
            raise RuntimeError("participant work issued outside the released session")
        issued = time.monotonic_ns()
        if active_ns(self.start, issued, self.platform) >= LIMIT:
            raise Cutoff("time_limit")
        pause = self.platform.get("checkpoint_measurement")
        if pause and pause["end_monotonic_ns"] is None and pause.get("first_public_activity_ns") is None:
            pause["first_public_activity_ns"] = issued
            self.resume_deadline()
        return issued

    def begin_action(self, *args, metadata=None, **kwargs):
        with self.activity_lock:
            return super().begin_action(*args, metadata=metadata, **kwargs)

    @contextmanager
    def measuring_checkpoint(self, observation):
        with self.activity_lock:
            start = time.monotonic_ns()
            if start - self.start >= LIMIT:
                raise Cutoff("time_limit")
            if self.platform.get("checkpoint_measurement") is not None:
                raise ValueError("repeated checkpoint measurement")
            already_active = any(e["kind"] in ("action", "fault") and
                                 "finished_monotonic_ns" not in e["payload"] for e in self.rows)
            observation.update(start_monotonic_ns=start, start_utc=utc(), end_monotonic_ns=None, end_utc=None,
                               preceding_events=len(self.rows), preceding_actions=self.steps,
                               first_public_activity_ns=start if already_active else None)
            self.platform["checkpoint_measurement"] = observation
            if not already_active:
                signal.setitimer(signal.ITIMER_REAL, 0)
            self.persist()
        try:
            yield
        finally:
            with self.activity_lock:
                observation.update(end_monotonic_ns=time.monotonic_ns(), end_utc=utc())
                try:
                    self.resume_deadline()
                finally:
                    self.persist()


def begin_notice(journal, payload):
    if not isinstance(journal, ParticipantClock):
        payload["issued_monotonic_ns"] = time.monotonic_ns()
    journal.event("fault", payload)


def finish_activity(journal, payload):
    if isinstance(journal, ParticipantClock):
        with journal.activity_lock:
            payload["finished_monotonic_ns"] = time.monotonic_ns()
    else:
        payload["finished_monotonic_ns"] = time.monotonic_ns()


def validate(platform, journal, raw):
    """Validate the original recorded interval and journal anchor, independently of cohort admission."""
    pause = platform.get("checkpoint_measurement")
    if pause is None:
        return
    require(raw["track"] != "clean" and type(pause) is dict, "unregistered checkpoint scoring pause")
    start, end = platform["start_monotonic_ns"], platform["end_monotonic_ns"]
    left, right = pause["start_monotonic_ns"], pause["end_monotonic_ns"]
    require(all(type(n) is int for n in (start, end, left)) and start <= left <= end,
            "scoring pause starts outside the released participant interval")
    require(left - start < LIMIT, "an expired participant cannot enter protected scoring")
    a, b = utc_time(platform["start_utc"]), utc_time(platform["end_utc"])
    began, ended = utc_time(pause["start_utc"]), utc_time(pause["end_utc"])
    require(a and b and began and a <= began <= b, "scoring pause UTC is outside the participant interval")
    if right is None:
        require(pause["end_utc"] is None and raw["terminal"] == "harness_failure"
                and platform.get("participant_exit") is None, "unfinished scoring pause has a released exit")
    else:
        require(type(right) is int and left <= right <= end and ended and began <= ended <= b,
                "scoring pause endpoint outside physical interval")
    charged = pause.get("first_public_activity_ns")
    require(charged is None or type(charged) is int and left <= charged <= (end if right is None else right),
            "physical public activity marker outside scoring interval")
    count, actions = pause["preceding_events"], pause["preceding_actions"]
    require(type(count) is int and 0 < count <= len(journal) and type(actions) is int and actions > 0,
            "invalid measurement journal anchor")
    before = journal[:count]
    live = [e for e in before if e["kind"] == "action"]
    required_action = "decide" if raw["track"] == "closed-affected" else "submit"
    require(len(live) == actions and any(e["payload"].get("action") == required_action
            and e["payload"].get("subject") == "W2" and e["payload"].get("candidate") == pause["candidate"]
            for e in live), "scoring pause lacks the old W2 attempt")
    moment = left - start
    require(all(e["offset_ns"] <= moment for e in before), "participant history crosses into pause")
    require(all(e["offset_ns"] >= moment for e in journal[count:]), "post-measurement event predates checkpoint")


def first_overlap(platform, timeline, complete):
    """Return the first proven instant of overlap and whether its ordering is complete."""
    pause = platform["checkpoint_measurement"]
    start, end = platform["start_monotonic_ns"], platform["end_monotonic_ns"]
    left, right = pause["start_monotonic_ns"], pause["end_monotonic_ns"]
    observed_until = end if right is None else right
    first = None
    for entry in timeline:
        issued, finished = entry.get("issued_monotonic_ns"), entry.get("finished_monotonic_ns")
        if type(issued) is not int or issued < start or issued > end:
            preceding = entry.get("preceding_issue_ns")
            if type(preceding) is not int or preceding < observed_until:
                complete = False
            continue
        if issued >= observed_until:
            continue
        if issued < left:
            if type(finished) is not int or not issued <= finished <= end:
                complete = False
                continue
            if finished <= left:
                continue
        overlap = max(left, issued)
        first = overlap if first is None else min(first, overlap)
    return first, complete


def quiescence(platform, timeline, complete):
    """Report physical work in the protected scoring period, including actively charged work."""
    pause = platform.get("checkpoint_measurement")
    if pause is None:
        return True
    first, complete = first_overlap(platform, timeline, complete)
    if first is not None:
        return False
    return True if complete and pause["end_monotonic_ns"] is not None else None


def exclusion_accounted(platform, timeline, complete):
    """An exact credit ends when the first independently witnessed public activity begins."""
    pause = platform.get("checkpoint_measurement")
    if pause is None:
        return True
    first, complete = first_overlap(platform, timeline, complete)
    charged = pause.get("first_public_activity_ns")
    if first is not None and (charged is None or first < charged):
        return False
    if first == charged == pause["start_monotonic_ns"]:
        return True  # The whole interval was active from entry.
    if not complete or pause["end_monotonic_ns"] is None:
        return None
    return first == charged


def budget_bounded(platform, reason):
    """When exclusion is disputed, can all possible active durations still satisfy this exit?"""
    start, end = platform.get("start_monotonic_ns"), platform.get("end_monotonic_ns")
    if type(start) is not int or type(end) is not int or start > end:
        return False
    high = end - start
    if reason in ("done", "blocked", "idle", "step_limit"):
        return high <= LIMIT
    if reason != "time_limit":
        return False
    pause = platform.get("checkpoint_measurement")
    if pause is None:
        return high >= LIMIT
    left, right = pause.get("start_monotonic_ns"), pause.get("end_monotonic_ns")
    return (type(left) is int and type(right) is int and start <= left <= right <= end
            and high - (right - left) >= LIMIT)
