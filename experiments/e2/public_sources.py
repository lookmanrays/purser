"""Original participant publications in commit order, with independently checkable public views."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field

from purser.driver import context_pack
from purser.kernel.projector import apply_event, empty_projection
from purser.kernel.store import _batches
from experiments.e1.accounting import READ_ERRORS, WORK, digest, kernel_rows, public_rows, require
from experiments.e1.artifacts import encoded, sha
from experiments.e1.executor import PARENT
from experiments.e1.measurement import same


PLAN_VERSION = "_e2_plan_version"


@dataclass
class Publication:
    checkpoint: dict
    rows: dict
    states: dict
    receipt: dict | None = None
    events: tuple = ()
    records: dict = field(default_factory=dict)
    snapshot: dict | None = None
    attempt: int | str | None = None


def identity(arm, checkpoint):
    if type(checkpoint) is not dict:
        return None
    if arm == "purser":
        revision, head = checkpoint.get("revision"), checkpoint.get("head_hash")
        if type(revision) is int and revision > 0 and digest(head, 64):
            return revision, head
    elif digest(checkpoint.get("plan_sha256"), 64):
        return checkpoint["plan_sha256"]
    return None


def view_ref(rows):
    data = encoded(rows)
    checksum = sha(data)
    return {"path": f"objects/{checksum}.json", "bytes": len(data), "sha256": checksum}


class Originals:
    def __init__(self, arm, platform, read, issue, *, unavailable=None):
        self.arm, self.read, self.issue = arm, read, issue
        unavailable = unavailable or issue
        self.publications, self.index, self.views = [], {}, {}
        self.plan_heads = {}
        self.trace, self.complete, self.initial, self.last = None, False, None, None
        self.initial_linear = None
        try:
            if platform.get("treatment_trace") is None:
                unavailable("original participant trace unavailable")
            else:
                self.trace = read(platform["treatment_trace"])
            if self.trace is not None and arm == "purser":
                self.kernel(self.trace)
                self.complete = True
            elif self.trace is not None:
                # Even a pre-versioned plan has an original published head.
                self.remember_plan(self.trace["plan"])
                if "plan_versions" in self.trace:
                    self.plan(self.trace)
                    self.complete = True
                else:
                    unavailable("original versioned plan history unavailable")
        except READ_ERRORS as error:
            self.issue(f"original participant history unavailable: {error}")
        if self.publications:
            self.last = len(self.publications) - 1
        self.initial = self.index.get(identity(arm, platform.get("initial_public")))
        end = self.index.get(identity(arm, platform.get("final_public")))
        start_state = "assigned" if arm == "purser" else "open"
        expected = dict.fromkeys(WORK, (start_state, None))
        # The first point at which all four registered assignments exist ends kernel setup.
        # The plan's second publication installs the agreed linear W2 task.
        self.setup = (next((i for i, v in enumerate(self.publications) if v.states == expected), None)
                      if arm == "purser" else 1 if len(self.publications) >= 2 else None)
        if self.initial is None or self.initial != self.setup or self.publications[self.initial].states != expected:
            self.complete = False
            (self.issue if self.publications and self.initial is not None else unavailable)(
                "initial public release does not match original completed participant setup")
        if self.last is None or end != self.last:
            self.complete = False
            (self.issue if self.publications and platform.get("final_public") else unavailable)(
                "final public checkpoint lacks an identical original participant head")
        if self.initial is not None:
            from . import policy
            try:
                packet = self.context(self.publications[self.initial].checkpoint, "W2")
                if self.arm == "purser":
                    choice, = packet["decisions"]
                    self.initial_linear = (choice["choice"] == policy.LINEAR and choice["id"] == policy.ORIGINAL
                                           and choice["support"] == [policy.ASSUMPTION])
                else:
                    self.initial_linear = (packet["retry_choice"] == policy.LINEAR and packet["criterion"] == "W2/retry"
                                           and packet["premise"] is None)
                require(self.initial_linear, "initial W2 basis differs from registered A1")
            except READ_ERRORS as error:
                self.issue(f"initial public retry instructions: {error}")

    def append(self, ck, rows, **fields):
        key = identity(self.arm, ck)
        require(key is not None and key not in self.index, "duplicate or invalid publication identity")
        ck = dict(ck, view=view_ref(rows))
        publication = Publication(ck, rows, public_rows(rows, self.arm), **fields)
        self.index[key] = len(self.publications)
        self.publications.append(publication)
        if self.arm != "purser":
            self.plan_heads[key] = rows

    def remember_plan(self, rows):
        public_rows(rows, self.arm)
        self.plan_heads[sha(encoded(rows))] = rows

    def kernel(self, trace):
        raw = trace["original_events"]
        require(type(raw) is list and raw, "original SQLite event bytes missing")
        projection = empty_projection()
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE events (revision INTEGER PRIMARY KEY, body BLOB NOT NULL)")
            db.executemany("INSERT INTO events VALUES (?, ?)",
                           ((i, event.encode() if type(event) is str else b"") for i, event in enumerate(raw, 1)))
            for batch in _batches(db):
                tail = batch[-1]
                end = tail["revision"]
                for event in batch:
                    require(event["v"] == 1, "unknown SQLite envelope version")
                    projection = apply_event(projection, event["type"], event["data"], event["revision"], end,
                                             event_version=event["event_version"])
                require(tail["type"] == "ProposalReceipted", "public batch has no terminal receipt")
                receipt = dict(tail["data"], revision=end)
                require((tail["actor_id"], tail["proposal_id"]) == (receipt["actor_id"], receipt["proposal_id"]),
                        "batch and receipt identities differ")
                rows = kernel_rows(projection)
                if set(rows) == set(WORK):
                    records = {r["id"]: r["fields"] for r in projection["records"]}
                    self.append({"revision": end, "head_hash": tail["hash"]}, rows, receipt=receipt,
                                events=tuple(batch), records=records,
                                snapshot={"revision": end, "head_hash": tail["hash"], "projection": projection})
        require(self.publications and same(trace["snapshot"], {"revision": end, "head_hash": tail["hash"],
                                                                "projection": projection}),
                "final SQLite snapshot disagrees with original replay")

    def plan(self, trace):
        refs = trace["plan_versions"]
        require(type(refs) is list and refs, "original plan publications missing")
        previous = None
        for number, ref in enumerate(refs, 1):
            rows = self.read(ref)
            actual = view_ref(rows)
            require(same(ref, actual), "plan version content address differs")
            stamp = rows[PARENT][PLAN_VERSION]
            require(type(stamp["number"]) is int and stamp["number"] == number and stamp["previous"] == previous,
                    "missing, reordered or disconnected plan publication")
            self.append({"plan_sha256": actual["sha256"]}, rows, attempt=stamp["attempt"])
            previous = actual["sha256"]
        require(same(rows, trace["plan"]), "original plan history does not terminate at participant head")

    def position(self, ck):
        return self.index.get(identity(self.arm, ck))

    def states_after(self, receipt, reported):
        """Yield each public head once, with its actual predecessor when recoverable."""
        first = self.position(receipt)
        if first is not None:
            for index in range(first, len(self.publications)):
                point = self.publications[index]
                key = identity(self.arm, point.checkpoint)
                prior = self.publications[index - 1].states if index else None
                yield key, reported.get(key), prior, point.states, index == first
        if self.complete or self.arm == "purser":
            # Kernel transition identity and adjacency come from replayed SQLite batches.
            return

        # A retained plan view carries the content address of its own predecessor.
        # Missing generations can leave locally provable pairs outside the original prefix.
        start = identity(self.arm, receipt)
        def generation(rows):
            stamp = rows[PARENT].get(PLAN_VERSION) if rows else None
            if isinstance(stamp, dict) and type(stamp.get("number")) is int and stamp["number"] > 0:
                return stamp["number"]
            return None

        start_number = generation(self.plan_heads.get(start))
        detached = []
        for key, rows in self.plan_heads.items():
            if key in self.index:
                continue
            number = generation(rows)
            if number is not None and number <= len(self.publications):
                continue
            if start_number is not None and number is not None:
                if number < start_number or number == start_number and key != start:
                    continue
            elif key not in reported:
                continue
            detached.append((key, number, rows))
        for key, number, rows in sorted(detached, key=lambda item: (item[0] != start, item[1] or 0)):
            prior = None
            if number is not None:
                previous = rows[PARENT][PLAN_VERSION].get("previous")
                parent_rows = self.plan_heads.get(previous) if digest(previous, 64) else None
                if generation(parent_rows) == number - 1:
                    prior = public_rows(parent_rows, self.arm)
            yield key, reported.get(key), prior, public_rows(rows, self.arm), key == start

    def context(self, ck, work):
        key = identity(self.arm, ck)
        require(work in WORK and key is not None, "unidentified worker context source")
        if self.arm == "purser":
            pub = self.publications[self.index[key]]
            return context_pack(pub.snapshot, pub.records[work]["assignment"])
        # A complete source plan is independently addressable even if another
        # generation is unavailable.
        if key not in self.plan_heads:
            self.lookup(ck)
        return {k: v for k, v in self.plan_heads[key][work].items() if k not in (PLAN_VERSION, "_publication")}

    def lookup(self, ck):
        key = identity(self.arm, ck)
        if key is None:
            return None
        position = self.index.get(key)
        original = self.publications[position] if position is not None else None
        ref = ck.get("view")
        if ref is None:
            if original is not None:
                return original.states
            if key in self.plan_heads:
                return public_rows(self.plan_heads[key], self.arm)
            return None
        cache_key = encoded(ck)
        if cache_key in self.views:
            return self.views[cache_key]
        states = original.states if original else None
        try:
            if original and same(original.checkpoint["view"], ref):
                # This exact byte string can be regenerated from its independent original.
                states = original.states
            else:
                rows = self.read(ref)
                require(same(view_ref(rows), ref), "unmatched view content address")
                if original:
                    require(same(rows, original.rows), "public view disagrees with replay")
                elif self.complete:
                    raise ValueError("checkpoint absent from complete published participant history")
                elif self.arm != "purser":
                    require(sha(encoded(rows)) == key, "public plan view disagrees with head")
                elif self.publications:
                    raise ValueError("checkpoint absent from original kernel history")
                states = public_rows(rows, self.arm)
                if self.arm != "purser":
                    self.plan_heads[key] = rows
        except READ_ERRORS as error:
            self.issue(f"invalid public checkpoint: {error}")
        self.views[cache_key] = states
        return states
