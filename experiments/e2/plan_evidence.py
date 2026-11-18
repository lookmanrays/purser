"""Case-local projections of atomically published plans and their canonical bytes."""
from collections import defaultdict
from functools import cache
import json
from pathlib import Path
import tempfile

from experiments.e1.accounting import READ_ERRORS, WORK, digest, require
from experiments.e1.artifacts import encoded, sha
from experiments.e1.executor import PARENT
from experiments.e1.measurement import same
from experiments.e1.outcomes import CLOSED

from . import attribution as attr
from . import plan_versions, policy
from .public_sources import identity, view_ref


def head(ck):
    key = identity("loop", ck)
    if key is None or ck.get("view") is None:
        return key
    ref = ck["view"]
    if (isinstance(ref, dict) and set(ref) == {"bytes", "path", "sha256"} and ref["sha256"] == key
            and ref["path"] == f"objects/{key}.json" and type(ref["bytes"]) is int and ref["bytes"] >= 0):
        return key
    return None


@cache
def setup_publications():
    # Both setup writes precede worker admission. Produce their bytes with the
    # same initializer and serialization, then require a case-owned digest.
    with tempfile.TemporaryDirectory(prefix="purser-e2-plan-setup-") as temp:
        policy.PlanArm(Path(temp))
        return {p.name: json.loads(p.read_bytes()) for p in (Path(temp) / "plan-versions").iterdir()}


def state_pair(state, candidate):
    require(type(state) is str and bool(state) and (candidate is None or digest(candidate, 40))
            and (candidate is not None or state not in (*CLOSED, "submitted")), "invalid scoped plan state")
    return state, candidate


def projection(work, row):
    require(type(row) is dict and row["work"]["id"] == work and type(row["verified"]) is dict
            and "_publication" not in row and plan_versions.KEY not in row, "wrong scoped plan row")
    state_pair(row["state"], row["candidate"])
    return row


def bounds(p, left, right):
    a, b = head(p.get(left)), head(p.get(right))
    return a if a and a == b else None


class ObservedPlans:
    def __init__(self, source, journal):
        self.source = source
        self.writes = []
        # A transferred projection can authenticate proposed whole bytes by
        # their case digest without independently proving the row at its source.
        self.proposals = defaultdict(dict)
        self.direct = defaultdict(dict)
        self.pairs = defaultdict(set)
        self.contexts, self.states = {}, {}
        self.replies = {}
        for event in journal:
            try:
                p = event["payload"]
                work = p.get("subject")
                if work not in WORK:
                    continue
                if event["kind"] == "public_state":
                    key = head(p.get("checkpoint"))
                    if key is not None:
                        self.pairs[key, work].add(state_pair(p["to"], p["candidate"]))
                elif event["kind"] == "fault":
                    if (p.get("actor") == "fixture_observer" and work == "W2" and isinstance(p.get("released"), dict)
                            and p.get("attempt") == p["released"].get("id")
                            and p.get("released_sha256") == sha(encoded(p["released"]))):
                        self.writes.append((p, "receive", head(p.get("before")), head(p.get("after"))))
                elif event["kind"] == "action":
                    self.action(p, work)
            except READ_ERRORS as error:
                source.read_failure("invalid scoped plan observation", error)

    def artifact(self, p, name):
        ref = p.get(name)
        if ref is None:
            return None
        try:
            return self.source.read(ref)
        except READ_ERRORS as error:
            self.source.read_failure(f"plan {name} at attempt {p.get('attempt')}", error)
            return None

    def original_reply(self, p):
        envelope = self.artifact(p, "response_evidence")
        if envelope is None:
            return None
        fields = ("attempt", "actor", "subject", "candidate")
        if (not isinstance(envelope, dict) or not all(k in envelope and same(envelope[k], p[k]) for k in fields)
                or not isinstance(envelope.get("return"), dict)):
            self.source.issue(f"plan response envelope has different invocation at attempt {p['attempt']}")
            return None
        return envelope["return"]

    def row(self, key, work, value, *, direct=False):
        if key is None or not isinstance(value, dict):
            return
        try:
            value = projection(work, value)
        except READ_ERRORS as error:
            if direct:
                self.source.read_failure(f"invalid original {work} plan row at {key}", error)
            return
        serial = encoded(value)
        self.proposals[key, work][serial] = value
        if direct:
            self.direct[key, work][serial] = value
            self.pairs[key, work].add((value["state"], value["candidate"]))

    def action(self, p, work):
        if type(p.get("attempt")) is not int or p["attempt"] < 1 or p.get("arm") != self.source.arm:
            return
        method = attr.expected(p)
        if method in attr.PLAN_WRITES:
            if p.get("port_method", method) != method:
                return
            a = head(p.get("port_before")) or head(p.get("before"))
            b = head(p.get("port_after")) or head(p.get("after"))
            self.writes.append((p, method, a, b))
            if method == "decide" and p.get("port_method") == method:
                self.replies[p["attempt"]] = self.original_reply(p)
                for reply in (p.get("port_return"), p.get("return"), self.replies[p["attempt"]]):
                    if isinstance(reply, dict):
                        self.row(b, work, reply.get("claim"))
            return
        if method not in ("context", "inspect") or method == "inspect" and p["input"].get("tool") != "plan":
            return
        if "work_request" in p:
            attr.work_request(p, method)
        if method == "context" and (p.get("port_method") != "context" or
                                    (p.get("port_input") or {}).get("work") != work):
            return
        if method == "inspect" and p.get("work_started") is None:
            return
        origin = head(p.get("context_source"))
        stable = bounds(p, "port_before", "port_after") if "port_before" in p else bounds(p, "before", "after")
        left = p.get("port_before", p.get("before"))
        right = p.get("port_after", p.get("after"))
        src = p.get("context_source")
        conflict = origin is not None and (stable is not None and stable != origin
                   or self.source.compare(left, src) == 1 or self.source.compare(src, right) == 1)
        if conflict:
            self.source.issue(f"scoped plan read {p['attempt']} has conflicting source and interval")
        prepared = self.artifact(p, "prepared_context")
        key = origin or stable
        if key and prepared is not None:
            self.row(key, work, prepared, direct=not conflict)
            if conflict:
                self.row(stable, work, prepared)
        if key is None:
            return
        try:
            received, packet = attr.delivered(p, self.source.read)
            if received and p["delivery"]["subject"] == work:
                self.row(key, work, packet)
        except READ_ERRORS as error:
            self.source.read_failure(f"plan receiver at attempt {p['attempt']}", error)
        self.row(key, work, self.original_reply(p))
        self.row(key, work, p.get("port_return"))

    def footprint(self, p, method, pub):
        """Identify a known one-work writer before borrowing its unchanged sibling rows."""
        if method == "receive":
            if p.get("disposition") == "head_moved" or type(p.get("issued_monotonic_ns")) is not int:
                return False
        elif (p.get("port_method") != method or not isinstance(p.get("port_input"), dict)
              or head(p.get("port_after")) != head(pub.checkpoint)
              or head(p.get("port_before")) != self.source._stamps[head(pub.checkpoint)]["previous"]):
            return False
        work = p["subject"]
        marker = pub.rows[work].get("_publication", {})
        if not isinstance(marker, dict) or type(marker.get("diagnosis")) is not list:
            return False
        if not same(pub.attempt, p["attempt"]) or not same(marker, attr.plan_marker(p, pub.states[work][1], marker["diagnosis"])):
            return False
        try:
            if "work_request" in p:
                attr.work_request(p, method)
            # Every native update is idempotent on its own published row.
            # This checks its touched work and operands without asserting a
            # particular prior state at the missing predecessor.
            attr.plan_operation(p, method, pub, pub)
        except READ_ERRORS:
            return False
        return True

    def diagnoses(self, p, method):
        if method in ("assign_again", "submit", "revise", "receive"):
            return [[]]
        if method == "verify":
            inputs = p.get("port_input") or p.get("input") or {}
            if isinstance(inputs, dict):
                observation = inputs.get("observation") or {}
                if isinstance(observation, dict):
                    verdict = observation.get("verdict", p["input"].get("verdict"))
                    if verdict in ("pass", "fail", "inconclusive") and isinstance(inputs.get("criterion"), str):
                        return [[] if verdict == "pass" else [inputs["criterion"]]]
        values = []
        for answer in (p.get("port_return"), p.get("return"), self.replies.get(p["attempt"])):
            if isinstance(answer, dict) and type(answer.get("diagnosis")) is list:
                values.append(answer["diagnosis"])
        return values

    def effects(self, p, method, before):
        inputs = [p]
        if "port_input" in p:
            inputs.append({k: v for k, v in p.items() if k != "port_input"})
        for name in ("work_started", "work_request"):
            request = p.get(name)
            if (isinstance(request, dict) and request.get("method") == method
                    and all(same(request.get(k), p[k]) for k in ("subject", "actor", "candidate"))
                    and isinstance(request.get("input"), dict)):
                inputs.append(dict(p, port_input=request["input"]))
        for request in inputs:
            try:
                yield attr.plan_effect(request, method, before)
            except READ_ERRORS:
                continue

    def candidates(self, p, method, prior, target):
        s = self.source
        work = p["subject"]
        guesses = [(row, diagnosis) for row in self.proposals.get((target, work), {}).values()
                   for diagnosis in self.diagnoses(p, method)]
        before_keys = {prior} if prior in s._stamps else set()
        if target and target not in s.plan_heads:
            # A known following child supplies the missing generation. Its
            # actual predecessor hash still has to match the proposed bytes.
            for child in s._children.get(target, ()):
                generation = s._stamps[child]["number"] - 2
                before_keys.update(s._generations.get(generation, ()))
        for key in before_keys:
            before = s.plan_heads[key]
            for row, diagnosis in (*self.effects(p, method, before), *guesses):
                yield before, s._stamps[key]["number"] + 1, key, row, diagnosis
        if prior and prior not in s.plan_heads and target and target not in s.plan_heads:
            # The next actual write to the same subject preserves all other
            # rows, including their original markers, across the lost file.
            for nxt, op, left, right in self.writes:
                if left != target or right not in s._stamps or nxt["subject"] != work:
                    continue
                pub = s._plan_point(right)
                stamp = s._stamps[right]
                if stamp["previous"] == target and self.footprint(nxt, op, pub):
                    for row, diagnosis in guesses:
                        yield pub.rows, stamp["number"] - 1, prior, row, diagnosis

    def recover(self, allowed, references, initial):
        s = self.source
        def admit(rows):
            ref = view_ref(rows)
            key = ref["sha256"]
            if key not in allowed or key in s.plan_heads:
                return False
            sizes = references.get(key, ())
            if sizes and ref["bytes"] not in sizes:
                s.issue(f"reconstructed plan length disagrees with captured view {key}")
                if not s._children.get(key):
                    return False
            s._remember(rows, key)
            return True

        missing = allowed - s.plan_heads.keys()
        if missing:
            setup = setup_publications()
            # The released V2 can independently name the hash of V1.
            for key in missing.intersection(setup):
                if key == head(initial) or s._children.get(key) or key in references:
                    admit(setup[key])
            for key, rows in setup.items():
                if key in s._children and key not in s.plan_heads:
                    allowed.add(key)
                    admit(rows)

        # The changed row and its administrative receipt are written together.
        # Case head equality, never a guess about the most likely operation,
        # authenticates a proposed serialization. Each pass must add a named head.
        while allowed - s.plan_heads.keys():
            count = len(s.plan_heads)
            for p, method, prior, target in self.writes:
                if prior is not None and prior == target:
                    continue
                work = p["subject"]
                for donor, number, previous, row, diagnosis in self.candidates(p, method, prior, target):
                    rows = dict(donor)
                    rows[work] = {**row, "_publication": attr.plan_marker(p, row["candidate"], diagnosis)}
                    rows[PARENT] = dict(rows[PARENT])
                    plan_versions.stamp(rows, number, previous, p["attempt"])
                    admit(rows)
            if len(s.plan_heads) == count:
                break

        # A witnessed successor preserves every other public row across its
        # actual one-work write even if the preceding file itself was lost.
        for p, method, prior, target in self.writes:
            if not prior or prior in s.plan_heads or target not in s._stamps:
                continue
            stamp = s._stamps[target]
            pub = s._plan_point(target)
            if stamp["previous"] != prior or not self.footprint(p, method, pub):
                continue
            for work, row in pub.rows.items():
                if work != p["subject"]:
                    self.row(prior, work, attr.plan_row(row), direct=True)

        for key, work in self.pairs:
            if key not in allowed:
                continue
            pairs = self.pairs[key, work]
            rows = self.direct.get((key, work), {})
            if key in s.plan_heads:
                canonical = attr.plan_row(s.plan_heads[key][work])
                if (any(pair != (canonical["state"], canonical["candidate"]) for pair in pairs)
                        or any(not same(canonical, row) for row in rows.values())):
                    s.issue(f"scoped plan observation differs from authenticated {work} at {key}")
            elif len(pairs) == 1:
                self.states.setdefault(key, {})[work] = next(iter(pairs))
                if len(rows) == 1:
                    self.contexts[key, work] = next(iter(rows.values()))
            if len(pairs) > 1 or len(rows) > 1:
                s.issue(f"conflicting scoped plan observations for {work} at {key}")
