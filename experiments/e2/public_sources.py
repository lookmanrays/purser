"""Case-bound participant publications, their store identities and provable intervals."""

import json
import sqlite3
import zipfile
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field

from experiments.e1.accounting import READ_ERRORS, WORK, digest, kernel_rows, public_rows, require
from experiments.e1.artifacts import encoded, sha
from experiments.e1.executor import PARENT
from experiments.e1.measurement import same
from purser.driver import context_pack
from purser.kernel.projector import apply_event, empty_projection
from purser.kernel.store import _batches

PLAN_VERSION = "_e2_plan_version"
CHECKPOINT_FIELDS = ("before", "after", "checkpoint", "required_before", "pre_issue_head",
                     "port_before", "port_after", "context_source")


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


def checkpoints(platform, journal):
    """Only heads the case captured may start a search for original bytes."""
    yield platform.get("initial_public")
    yield platform.get("final_public")
    yield (platform.get("checkpoint_measurement") or {}).get("checkpoint")
    for event in journal:
        fields = event.get("payload") if isinstance(event, dict) else None
        if isinstance(fields, dict) and event.get("kind") in ("action", "fault", "public_state"):
            yield from (fields[k] for k in CHECKPOINT_FIELDS if k in fields)


def kernel_publications(raw):
    """Yield only validated whole SQLite transactions of the registered public workspace."""
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
                ck = {"revision": end, "head_hash": tail["hash"], "view": view_ref(rows)}
                snap = {"revision": end, "head_hash": tail["hash"], "projection": projection}
                yield Publication(ck, rows, public_rows(rows, "purser"), receipt, tuple(batch), records, snap)


class Originals:
    def __init__(self, arm, platform, read, issue, *, unavailable=None, journal=(), objects=None):
        self.arm, self.read, self.issue = arm, read, issue
        self.unavailable = unavailable or issue
        self.publications, self.index, self.views = [], {}, {}
        self.plan_heads, self._stamps, self._children = {}, {}, defaultdict(set)
        self._generations, self._forks = defaultdict(set), set()
        self._detached_kernel, self._ambiguous_kernel = {}, set()
        self.trace, self.complete, self.initial, self.last = None, False, None, None
        self.initial_linear = None
        journal = journal if isinstance(journal, (list, tuple)) else ()
        observed = {encoded(ck): ck for ck in checkpoints(platform, journal) if identity(arm, ck) is not None}
        pause = platform.get("checkpoint_measurement") or {}
        sources = []
        for label, reference, ck in (("checkpoint", pause.get("submission_source"), pause.get("checkpoint")),
                                     ("terminal", platform.get("treatment_trace"), platform.get("final_public"))):
            if reference is None:
                if label == "terminal":
                    self.unavailable("original participant trace unavailable")
                continue
            try:
                trace = read(reference)
                require(type(trace) is dict, "invalid trace object")
                key = identity(arm, ck)
                if label == "checkpoint":
                    require(key is not None, "checkpoint capture lacks an observed head")
                sources.append((label, trace, key))
            except READ_ERRORS as error:
                self.read_failure(f"original {label} history", error)

        if arm == "purser":
            self._kernel(sources, platform, journal, objects, observed.values())
        else:
            self._plans(sources, observed.values(), objects, platform.get("initial_public"))

        self.index = {identity(arm, p.checkpoint): i for i, p in enumerate(self.publications)}
        self.last = len(self.publications) - 1 if self.publications else None
        self.initial = self.position(platform.get("initial_public"))
        end = self.position(platform.get("final_public"))
        start_state = "assigned" if arm == "purser" else "open"
        expected = dict.fromkeys(WORK, (start_state, None))
        # Setup finishes with four assignments in SQLite or the plan's A1 publication.
        if arm == "purser":
            self.setup = next((i for i, point in enumerate(self.publications) if point.states == expected), None)
        else:
            self.setup = next((i for i, point in enumerate(self.publications)
                               if self._stamps[identity(arm, point.checkpoint)]["number"] == 2), None)
        began = self.initial is not None and self.initial == self.setup and self.publications[self.initial].states == expected
        if not began:
            (self.issue if self.initial is not None else self.unavailable)(
                "initial public release does not match original completed participant setup")
        if self.last is None or end != self.last:
            (self.issue if end is not None and end != self.last else self.unavailable)(
                "final public checkpoint lacks an identical original participant head")
        extra = False
        if end is not None and end == self.last:
            if arm == "purser":
                final_revision = self.publications[end].snapshot["revision"]
                extra = any(ck["revision"] > final_revision for ck in observed.values())
            else:
                extra = any(key not in self.index for key in self.plan_heads)
            if extra:
                self.issue("final checkpoint differs from other captured participant heads")
        self.complete = began and end is not None and end == self.last and not (extra or self._forks)
        if arm == "purser":
            for ck in observed.values():
                if self.position(ck) is None:
                    self.lookup(ck)
        if self.initial is not None:
            from . import policy
            try:
                packet = self.context(self.publications[self.initial].checkpoint, "W2")
                if arm == "purser":
                    choice, = packet["decisions"]
                    self.initial_linear = (choice["choice"] == policy.LINEAR and choice["id"] == policy.ORIGINAL
                                           and choice["support"] == [policy.ASSUMPTION])
                else:
                    self.initial_linear = (packet["retry_choice"] == policy.LINEAR and packet["criterion"] == "W2/retry"
                                           and packet["premise"] is None)
                require(self.initial_linear, "initial W2 basis differs from registered A1")
            except READ_ERRORS as error:
                self.issue(f"initial public retry instructions: {error}")

    def _kernel(self, sources, platform, journal, objects, observed):
        anchors = {identity(self.arm, ck) for ck in observed}
        selected, trace = [], None
        terminal = conflict = False
        for label, candidate, expected in sources:
            points = []
            raw = candidate.get("original_events")
            try:
                if selected and type(raw) is list and len(raw) >= selected[-1].snapshot["revision"]:
                    # Canonical bytes shared with an already checked prefix need no separate admission.
                    if same(raw, trace["original_events"]) and same(candidate.get("snapshot"), trace["snapshot"]):
                        points = list(selected)
                if not points:
                    points.extend(kernel_publications(raw))
                require(points and same(points[-1].snapshot, candidate.get("snapshot")),
                        "SQLite snapshot disagrees with its original replay")
                key = identity(self.arm, points[-1].checkpoint)
                require(expected is None or key == expected, "SQLite original ends at a different observed head")
                if label == "terminal":
                    terminal = expected is not None and all(ck[0] <= key[0] for ck in anchors)
            except READ_ERRORS as error:
                self.read_failure(f"original {label} SQLite history", error)
                # The last independently observed matching hash authenticates every preceding envelope.
                cut = next((i for i in reversed(range(len(points)))
                            if identity(self.arm, points[i].checkpoint) in anchors), None)
                points = points[:cut+1] if cut is not None else []
            if not points:
                continue
            common = min(len(points), len(selected))
            match = next((i for i in range(common) if points[i].checkpoint != selected[i].checkpoint), common)
            if match != common:
                self.issue("original SQLite sources disagree on the same publication history")
                selected = selected[:match]
                conflict = True
                self._forks.add(selected[-1].checkpoint["revision"] + 1 if selected else 1)
                break
            if len(points) >= len(selected):
                selected, trace = points, candidate
        if not selected:
            return
        snapshot = selected[-1].snapshot
        trace = dict(trace, original_events=trace["original_events"][:snapshot["revision"]], snapshot=snapshot)
        if objects is not None and journal and not terminal and not conflict:
            from .kernel_history import recover_suffix
            restored = recover_suffix(trace, journal, platform, objects, self.issue, self.unavailable)
            if len(restored["original_events"]) > len(trace["original_events"]):
                selected = list(kernel_publications(restored["original_events"]))
                require(same(selected[-1].snapshot, restored["snapshot"]),
                        "recovered SQLite snapshot disagrees with its canonical events")
            trace = restored
        self.publications, self.trace = selected, trace

    def read_failure(self, label, error):
        # Objects wraps a missing archive member so readers can keep their own
        # distinction between an interrupted capture and contradictory bytes.
        missing = isinstance(error, FileNotFoundError) or isinstance(error.__cause__, (KeyError, FileNotFoundError))
        (self.unavailable if missing else self.issue)(f"{label}: {error}")

    def _remember(self, rows, key):
        require(view_ref(rows)["sha256"] == key, "public plan content disagrees with its observed head")
        if key in self.plan_heads:
            return
        public_rows(rows, self.arm)
        self.plan_heads[key] = rows
        stamp = rows[PARENT].get(PLAN_VERSION)
        if stamp is None:
            return
        require(type(stamp) is dict and type(stamp.get("number")) is int and stamp["number"] > 0
                and "previous" in stamp and "attempt" in stamp
                and (stamp["previous"] is None if stamp["number"] == 1 else digest(stamp["previous"], 64)),
                "invalid versioned plan publication")
        require(stamp["attempt"] is None or type(stamp["attempt"]) in (int, str), "invalid plan publication attempt")
        self._stamps[key] = stamp
        number, parent = stamp["number"], stamp["previous"]
        self._generations[number].add(key)
        if len(self._generations[number]) == 2 and number not in self._forks:
            self._forks.add(number)
            self.issue(f"conflicting public plan heads at generation {number}")
        if parent is not None:
            self._children[parent].add(key)
            if parent in self._stamps and self._stamps[parent]["number"] != number - 1:
                self.issue(f"nonconsecutive plan publication {key}")
        for child in self._children.get(key, ()):
            if self._stamps[child]["number"] != number + 1:
                self.issue(f"nonconsecutive plan publication {child}")

    def _plans(self, sources, observed, objects, initial):
        # A list can supply missing original bytes. Its entries belong to this
        # history only if a case head reaches them by its own predecessor hashes.
        copies, anchors = {}, set()
        for label, trace, expected in sources:
            try:
                rows = trace["plan"]
                head = view_ref(rows)["sha256"]
                require(expected is None or head == expected, "original plan ends at a different observed head")
                self._remember(rows, head)
                anchors.add(head)
                self.trace = trace
                if "plan_versions" not in trace:
                    self.unavailable("original versioned plan history unavailable")
                    continue
                refs = trace["plan_versions"]
                require(type(refs) is list and refs, "original plan publications missing")
                previous = None
                for number, ref in enumerate(refs, 1):
                    try:
                        rows = self.read(ref)
                        require(same(ref, view_ref(rows)), "plan version content address differs")
                        copies[ref["sha256"]] = rows
                        stamp = rows[PARENT][PLAN_VERSION]
                        require(stamp["number"] == number and (previous is None and number > 1
                                or stamp["previous"] == previous), "missing, reordered or disconnected plan publication")
                    except READ_ERRORS as error:
                        self.read_failure(f"original {label} plan version {number}", error)
                    previous = ref.get("sha256") if type(ref) is dict else None
                require(previous == head, "original plan history does not terminate at the observed head")
            except READ_ERRORS as error:
                self.read_failure(f"original {label} plan history", error)

        for ck in observed:
            key, ref = identity(self.arm, ck), ck.get("view")
            if ref is None:
                # The plan digest itself names the originally captured bytes.
                anchors.add(key)
            elif isinstance(ref, dict) and ref.get("sha256") == key and ref.get("path") == f"objects/{key}.json":
                anchors.add(key)
                if key in self.plan_heads and same(view_ref(self.plan_heads[key]), ref):
                    continue
                try:
                    rows = self.read(ref)
                    require(same(ref, view_ref(rows)), "unmatched view content address")
                    self._remember(rows, key)
                except READ_ERRORS as error:
                    self.read_failure("invalid public plan observation", error)
            else:
                self.issue("public plan view reference differs from its captured head")

        visited, pending = set(), list(anchors)
        while pending:
            key = pending.pop()
            if key in visited:
                continue
            visited.add(key)
            if key not in self.plan_heads:
                try:
                    if key in copies:
                        rows = copies[key]
                    elif objects is not None:
                        # Fetch one name supplied by a case publication. The archive
                        # can also contain participant plans from unrelated cases.
                        name = f"objects/{key}.json"
                        try:
                            size = len(objects.archive.read(name))
                        except (KeyError, FileNotFoundError):
                            self.unavailable(f"original plan bytes unavailable for {key}")
                            continue
                        rows = objects.item({"path": name, "sha256": key, "bytes": size})
                    else:
                        continue
                    self._remember(rows, key)
                except (*READ_ERRORS, zipfile.BadZipFile) as error:
                    self.read_failure(f"invalid content for observed plan {key}", error)
            parent = self._stamps.get(key, {}).get("previous")
            if parent is not None:
                pending.append(parent)

        roots = self._generations.get(1, ())
        released = identity(self.arm, initial)
        if len(roots) == 1:
            root, = roots
        elif not roots and self._stamps.get(released, {}).get("number") == 2:
            # V1 predates release to the worker. The captured A1 setup can anchor
            # the measured interval even if this pre-release object was lost.
            root = released
        else:
            return
        self.publications = [self._plan_point(key) for key in self._linked_plans(root)]

    def _plan_point(self, key):
        rows = self.plan_heads[key]
        return Publication({"plan_sha256": key, "view": view_ref(rows)}, rows, public_rows(rows, self.arm),
                           attempt=self._stamps.get(key, {}).get("attempt"))

    def position(self, ck):
        return self.index.get(identity(self.arm, ck))

    def plan_edge(self, ck):
        """Return two observed plan publications when the latter names the former."""
        key = identity(self.arm, ck)
        stamp = self._stamps.get(key)
        parent = stamp["previous"] if stamp else None
        if parent in self._stamps and self._stamps[parent]["number"] == stamp["number"] - 1:
            return self._plan_point(parent), self._plan_point(key)
        return None

    def _linked_plans(self, key):
        while key in self.plan_heads:
            yield key
            children = self._children.get(key, ())
            if len(children) != 1:
                break
            child, = children
            number = self._stamps[child]["number"]
            if number in self._forks or number != self._stamps.get(key, {}).get("number", -1) + 1:
                break
            key = child

    def through(self, ck):
        """Last head in the unambiguous, gap-free public interval beginning at ck."""
        if self.arm == "purser":
            return self.publications[-1].checkpoint if self.position(ck) is not None else None
        last = None
        for key in self._linked_plans(identity(self.arm, ck)):
            last = key
        return self._plan_point(last).checkpoint if last is not None else None

    def compare(self, first, second):
        """Order comparable store heads. Missing links and divergent branches are indeterminate."""
        left, right = self.position(first), self.position(second)
        if left is not None and right is not None:
            return (left > right) - (left < right)
        key, other = identity(self.arm, first), identity(self.arm, second)
        if key is not None and key == other and (key in self.plan_heads or key in self._detached_kernel):
            return 0
        if key not in self._stamps or other not in self._stamps:
            return None
        delta = self._stamps[key]["number"] - self._stamps[other]["number"]
        younger, older = (key, other) if delta > 0 else (other, key)
        for _ in range(abs(delta)):
            parent = self._stamps[younger]["previous"]
            if parent not in self._stamps or self._stamps[parent]["number"] != self._stamps[younger]["number"] - 1:
                return None
            younger = parent
        return (delta > 0) - (delta < 0) if younger == older else None

    def candidate_oids(self):
        """Git identities actually present in case original publications or captured public views."""
        oids = {oid for point in self.publications for _, oid in point.states.values() if oid}
        if self.arm == "purser" and self.trace:
            # An accepted batch can record an intermediate candidate that its
            # final q02 rows no longer carry. Only verified canonically replayed
            # envelopes enter self.trace, including the original setup batches.
            for raw in self.trace["original_events"]:
                data = json.loads(raw)["data"]
                candidate = data.get("candidate")
                if (data.get("work") in WORK and isinstance(candidate, dict)
                        and candidate.get("kind") == "git.commit" and candidate.get("algorithm") == "sha1"
                        and digest(candidate.get("digest"), 40)):
                    oids.add(candidate["digest"])
        for rows in self.plan_heads.values():
            oids.update(oid for _, oid in public_rows(rows, self.arm).values() if oid)
        for rows in self._detached_kernel.values():
            oids.update(oid for _, oid in public_rows(rows, self.arm).values() if oid)
        return oids

    def states_after(self, receipt, reported):
        """Yield actual public observations once, with adjacency only where the store proves it."""
        start = identity(self.arm, receipt)
        if self.arm == "purser":
            first = self.position(receipt)
            if first is not None:
                for index in range(first, len(self.publications)):
                    point = self.publications[index]
                    key = identity(self.arm, point.checkpoint)
                    prior = self.publications[index - 1].states if index else None
                    yield key, reported.get(key), prior, point.states, index == first
            for key, rows in self._detached_kernel.items():
                if key in reported and (start is None or key == start or key[0] > start[0]):
                    yield key, reported[key], None, public_rows(rows, self.arm), key == start
            return

        # Consumers can snapshot exact interval facts at through(receipt).
        # Unordered historical observations are yielded only after that boundary.
        contiguous = set()
        for key in self._linked_plans(start):
            contiguous.add(key)
            point = self._plan_point(key)
            edge = self.plan_edge(point.checkpoint)
            yield key, reported.get(key), edge[0].states if edge else None, point.states, key == start
        if self.complete:
            return
        post = set(reported) | contiguous
        start_number = self._stamps.get(start, {}).get("number")
        for key in sorted(self.plan_heads, key=lambda k: self._stamps.get(k, {}).get("number", 0)):
            if key in contiguous:
                continue
            stamp = self._stamps.get(key, {})
            number = stamp.get("number")
            if key != start and number is not None and start_number is not None and number <= start_number:
                continue
            edge = self.plan_edge({"plan_sha256": key})
            if key not in post and (not edge or stamp["previous"] not in post):
                continue
            post.add(key)
            prior = edge[0].states if edge and number not in self._forks else None
            yield key, reported.get(key), prior, public_rows(self.plan_heads[key], self.arm), key == start

    def context(self, ck, work):
        key = identity(self.arm, ck)
        require(work in WORK and key is not None, "unidentified worker context source")
        if self.arm == "purser":
            pub = self.publications[self.index[key]]
            return context_pack(pub.snapshot, pub.records[work]["assignment"])
        if key not in self.plan_heads:
            self.lookup(ck)
        return {k: v for k, v in self.plan_heads[key][work].items() if k not in (PLAN_VERSION, "_publication")}

    def lookup(self, ck):
        key = identity(self.arm, ck)
        if key is None:
            return None
        position = self.position(ck)
        original = self.publications[position] if position is not None else None
        ref = ck.get("view")
        if key in self._ambiguous_kernel:
            return None
        known_rows = self.plan_heads.get(key) if self.arm != "purser" else self._detached_kernel.get(key)
        states = original.states if original else public_rows(known_rows, self.arm) if known_rows is not None else None
        if ref is None:
            return states
        cache_key = encoded(ck)
        if cache_key in self.views:
            return self.views[cache_key]
        try:
            if original and same(original.checkpoint["view"], ref):
                states = original.states
            else:
                rows = self.read(ref)
                require(same(view_ref(rows), ref), "unmatched view content address")
                if original:
                    require(same(rows, original.rows), "public view disagrees with replay")
                elif self.complete:
                    raise ValueError("checkpoint absent from complete published participant history")
                elif self.arm != "purser":
                    self._remember(rows, key)
                else:
                    # A later transmitted q02 view can establish these four rows.
                    # Its hash claim alone does not authenticate omitted SQLite batches.
                    require(not self.publications or key[0] > self.publications[-1].checkpoint["revision"],
                            "checkpoint disagrees with the original kernel prefix")
                    public_rows(rows, self.arm)
                    if key in self._detached_kernel and not same(self._detached_kernel[key], rows):
                        self._ambiguous_kernel.add(key)
                        del self._detached_kernel[key]
                        states = None
                        raise ValueError("different transmitted views at the same SQLite head")
                    self._detached_kernel[key] = rows
                states = public_rows(rows, self.arm)
        except READ_ERRORS as error:
            self.read_failure("invalid public checkpoint", error)
        self.views[cache_key] = states
        return states
