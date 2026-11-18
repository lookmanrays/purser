"""Original participant publications in commit order, with independently checkable public views."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field

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
    def __init__(self, arm, platform, read, issue):
        self.arm, self.read, self.issue = arm, read, issue
        self.publications, self.index, self.views = [], {}, {}
        self.trace, self.complete, self.initial, self.last = None, False, None, None
        try:
            self.trace = read(platform["treatment_trace"])
            if arm == "purser":
                self.kernel(self.trace)
            else:
                self.plan(self.trace)
            self.complete = True
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
        setup = (next((i for i, v in enumerate(self.publications) if v.states == expected), None)
                 if arm == "purser" else 1)
        if self.initial is None or self.initial != setup or self.publications[self.initial].states != expected:
            self.complete = False
            self.issue("initial public release does not match original completed participant setup")
        if self.last is None or end != self.last:
            self.complete = False
            self.issue("final public checkpoint lacks an identical original participant head")

    def append(self, ck, rows, **fields):
        key = identity(self.arm, ck)
        require(key is not None and key not in self.index, "duplicate or invalid publication identity")
        ck = dict(ck, view=view_ref(rows))
        self.index[key] = len(self.publications)
        self.publications.append(Publication(ck, rows, public_rows(rows, self.arm), **fields))

    def kernel(self, trace):
        raw = trace["original_events"]
        require(type(raw) is list and raw, "original SQLite event bytes missing")
        projection = empty_projection()
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE events (revision INTEGER PRIMARY KEY, body BLOB NOT NULL)")
            db.executemany("INSERT INTO events VALUES (?, ?)", ((i, event.encode()) for i, event in enumerate(raw, 1)))
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
                                events=tuple(batch), records=records)
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

    def lookup(self, ck):
        key = identity(self.arm, ck)
        if key is None:
            return None
        position = self.index.get(key)
        original = self.publications[position] if position is not None else None
        ref = ck.get("view")
        if ref is None:
            return original.states if original else None
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
                elif self.arm != "purser":
                    require(sha(encoded(rows)) == key, "public plan view disagrees with head")
                elif self.publications:
                    raise ValueError("checkpoint absent from original kernel history")
                states = public_rows(rows, self.arm)
        except READ_ERRORS as error:
            self.issue(f"invalid public checkpoint: {error}")
        self.views[cache_key] = states
        return states
