"""Chronological precedence among observed plan publications in one case."""

from collections import defaultdict

from experiments.e1.accounting import READ_ERRORS

from . import attribution
from .public_sources import identity


def publication_bounds(arm, actions, faults, platform):
    bounds = {}
    released, stopped = platform.get("start_monotonic_ns"), platform.get("end_monotonic_ns")

    def observed(checkpoint, *, lower=None, upper=None):
        key = identity(arm, checkpoint)
        if key is None:
            return
        first, last = bounds.get(key, (None, None))
        if lower is not None:
            first = lower if first is None else max(first, lower)
        if upper is not None:
            last = upper if last is None else min(last, upper)
        bounds[key] = first, last

    def times(call):
        first, last = call.get("issued_monotonic_ns"), call.get("finished_monotonic_ns")
        if (type(first) is not int or first < 0 or type(released) is int and first < released
                or type(stopped) is int and first > stopped):
            return None, None
        if last is not None and (type(last) is not int or last < first or type(stopped) is int and last > stopped):
            return first, None
        return first, last

    for call in actions:
        start, end = times(call)
        if start is None:
            continue
        try:
            method = attribution.expected(call)
            attribution.work_request(call, method)
        except READ_ERRORS:
            continue
        observed(call.get("pre_issue_head"), upper=start)
        # PublicActions takes before and both inner port samples during the
        # call. Its outer after sample is taken *after* finish_activity.
        observed(call.get("before"), upper=end)
        if call.get("port_method") == method:
            observed(call.get("port_before"), upper=end)
            observed(call.get("port_after"), upper=end)
            if "work_started" in call:
                observed(call.get("work_publication"), lower=start, upper=end)
        if call.get("prepared_verified") is True:
            upper = call.get("delivery_observed_ns") if call.get("intended_context_received") is True else None
            observed(call.get("context_source"), upper=upper if upper is not None else end)

    for _, notice in faults:
        start, end = times(notice)
        if start is not None and notice.get("publicly_delivered") is True:
            # The dated event bounds the premise write. Its normalized before
            # is the predecessor of the write, possibly after the initial sample.
            observed(notice.get("after"), lower=start, upper=end)
    return bounds


def reachable_heads(keys, edges):
    result = {}
    for key in keys:
        seen, pending = set(), list(edges.get(key, ()))
        while pending:
            other = pending.pop()
            if other not in seen:
                seen.add(other)
                pending.extend(edges.get(other, ()))
        result[key] = seen
    return result


def physical_edges(bounds):
    edges = defaultdict(set)
    for earlier, (_, finish) in bounds.items():
        if finish is not None:
            edges[earlier].update(following for following, (start, _) in bounds.items()
                                  if following != earlier and start is not None and finish < start)
    return edges


class PlanOrder:
    def __init__(self, stamps, bounds, issue, *, trusted=None):
        # A missing predecessor's address is known from its child. Its own
        # predecessor and its contents still require independent evidence.
        later = defaultdict(set)
        for child, stamp in stamps.items():
            parent = stamp["previous"]
            if parent and (parent not in stamps or stamps[parent]["number"] + 1 == stamp["number"]):
                later[parent].add(child)

        physical = physical_edges(bounds)
        keys = set(stamps) | bounds.keys() | later.keys()
        edges = {key: later[key] | physical[key] for key in keys}
        self.reachable = reachable_heads(keys, edges)
        inconsistent = {key for key, (first, last) in bounds.items()
                        if first is not None and last is not None and last < first}
        cycles = {key for key in keys if key in self.reachable[key]}
        if inconsistent or cycles:
            issue("contradictory physical and predecessor order for plan publications: "
                  + ", ".join(sorted(inconsistent | cycles)))
        if inconsistent or cycles or trusted is not None and trusted != bounds:
            # A disputed pair cannot supply an intermediate step in a proof.
            # A different write can still demonstrably follow both of them.
            if trusted is not None and trusted != bounds:
                physical = physical_edges(trusted)
            for key in keys:
                if key in inconsistent:
                    edges[key] = later[key]
                else:
                    edges[key] = later[key] | (physical[key] - inconsistent)
                edges[key] = {other for other in edges[key] if key not in self.reachable[other]}
            self.reachable = reachable_heads(keys, edges)

    def compare(self, left, right):
        if left not in self.reachable or right not in self.reachable:
            return None
        if left == right:
            return 0
        if right in self.reachable[left]:
            return -1
        if left in self.reachable[right]:
            return 1
        return None
