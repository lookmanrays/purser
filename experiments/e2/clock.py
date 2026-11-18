"""Measure the released participant clock and audit the private scoring interval."""

import signal
import time
from contextlib import contextmanager

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
    def elapsed(self):
        return active_ns(self.start, time.monotonic_ns(), self.platform)

    def resume_deadline(self):
        remaining = LIMIT - self.elapsed()
        if remaining <= 0:
            raise Cutoff("time_limit")
        signal.setitimer(signal.ITIMER_REAL, remaining / 1e9)

    def public_activity(self, issued):
        pause = self.platform.get("checkpoint_measurement")
        if pause and pause["end_monotonic_ns"] is None and pause.get("first_public_activity_ns") is None:
            pause["first_public_activity_ns"] = issued
            self.resume_deadline()
            self.persist()

    def begin_action(self, *args, metadata=None, **kwargs):
        self.public_activity(metadata["issued_monotonic_ns"])
        return super().begin_action(*args, metadata=metadata, **kwargs)

    @contextmanager
    def measuring_checkpoint(self, observation):
        start = time.monotonic_ns()
        if start - self.start >= LIMIT:
            raise Cutoff("time_limit")
        if self.platform.get("checkpoint_measurement") is not None:
            raise ValueError("repeated checkpoint measurement")
        signal.setitimer(signal.ITIMER_REAL, 0)
        observation.update(start_monotonic_ns=start, start_utc=utc(), end_monotonic_ns=None, end_utc=None,
                           preceding_events=len(self.rows), preceding_actions=self.steps)
        self.platform["checkpoint_measurement"] = observation
        try:
            self.persist()
            yield
        finally:
            observation.update(end_monotonic_ns=time.monotonic_ns(), end_utc=utc())
            self.persist()
            self.resume_deadline()


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
    require(len(live) == actions and live[-1]["payload"].get("action") == required_action
            and live[-1]["payload"].get("subject") == "W2"
            and live[-1]["payload"].get("candidate") == pause["candidate"]
            and live[-1]["payload"].get("after") == pause["checkpoint"], "scoring pause is not at old W2 publication")
    moment = left - start
    require(all(e["offset_ns"] <= moment for e in before), "participant history crosses into pause")
    require(all(e["offset_ns"] >= moment for e in journal[count:]), "post-measurement event predates checkpoint")


def quiescence(platform, timeline, complete):
    """Prove or disprove that all physically issued work and incoming observations missed the exclusion."""
    pause = platform.get("checkpoint_measurement")
    if pause is None:
        return True
    left, right = pause["start_monotonic_ns"], pause["end_monotonic_ns"]
    start, end = platform["start_monotonic_ns"], platform["end_monotonic_ns"]
    available = complete and right is not None
    for entry in timeline:
        issued, finished = entry.get("issued_monotonic_ns"), entry.get("finished_monotonic_ns")
        if type(issued) is not int or issued < start or issued > end:
            available = False
            continue
        if type(finished) is not int or not issued <= finished <= end:
            # An issued but unreturned call can still prove overlap from its issue.
            finished = None
            available = False
        if left <= issued < (right if right is not None else end) or issued < left and finished is not None and finished > left:
            return False
    if pause.get("first_public_activity_ns") is not None:
        return False
    return True if available else None


def budget_bounded(platform, reason):
    """When exclusion is disputed, can all possible active durations still satisfy this exit?"""
    start, end = platform.get("start_monotonic_ns"), platform.get("end_monotonic_ns")
    if type(start) is not int or type(end) is not int or start > end:
        return False
    high = end - start
    if reason in ("done", "blocked", "idle", "step_limit"):
        return high <= LIMIT
    pause = platform.get("checkpoint_measurement") or {}
    left, right = pause.get("start_monotonic_ns",start), pause.get("end_monotonic_ns",end)
    return reason == "time_limit" and type(right) is int and high - (right-left) >= LIMIT
