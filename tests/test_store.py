import hashlib
import json
import multiprocessing
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from purser.kernel import store as storage
from purser.kernel.projector import apply_event, empty_projection
from purser.kernel.store import Store
from purser.kernel.wire import KernelError, ZERO_HASH, canonical, decode, digest, format_time, parse_time

ROOT = Path(__file__).resolve().parents[1]
WIRE = json.loads((ROOT / "docs/contracts/wire-v1.json").read_bytes())
ACTOR = WIRE["actor"]


def append_batch(store, start, end):
    events = WIRE["events"][start:end]
    entries = [{"type": e["type"], "data": e["data"]} for e in events]
    return store.transact(ACTOR, events[0]["proposal_id"], lambda: parse_time(events[0]["at"]),
                          lambda *_: (entries, events[0]["proposal_id"]))[0]


def replace_log(path, rows):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM events")
        db.executemany("INSERT INTO events VALUES (?,?)", rows)


def reseal(events):
    previous = ZERO_HASH
    for event in events:
        event["prev_hash"] = previous
        event.pop("hash", None)
        previous = event["hash"] = digest("event", event)
    return [(e["revision"], canonical(e)) for e in events]


def cache_rows(path):
    with closing(sqlite3.connect(path)) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
                for table in ("head", "records", "receipts", "edges")}


def crash_writer(path, inserted, committed, release, checkpoint):
    # A signal after the first real SQLite INSERT, before its batch can commit.
    original = storage.apply_event

    def observed(projection, event_type, data, revision, end, **kwargs):
        result = original(projection, event_type, data, revision, end, **kwargs)
        if checkpoint == "during_append" and revision == 3:
            inserted.set()
            if not release.wait(15):
                raise TimeoutError("parent did not release the writer")
        return result

    with patch.object(storage, "apply_event", observed):
        append_batch(Store(path), 2, 4)
    committed.set()
    if not release.wait(15):
        raise TimeoutError("parent did not release the writer")


def concurrent_writer(path, identity, attempting, deciding, release, result):
    store = Store(path)
    attempting.set()

    def decide(snapshot, _):
        revision = snapshot["revision"]
        assert store.snapshot(revision=revision) == snapshot
        deciding.set()
        if not release.wait(15):
            raise TimeoutError("parent did not release the writer")
        receipt = {"actor_id": ACTOR, "proposal_id": identity, "digest": digest("proposal", identity),
                   "disposition": "rejected", "code": "unknown_op", "details": {}}
        return [{"type": "ProposalReceipted", "data": receipt}], revision

    snap, start = store.transact(ACTOR, identity, lambda: parse_time(WIRE["clock_by_batch"][0]), decide)
    result.put((identity, start, snap["revision"]))


class WireTests(unittest.TestCase):
    def test_canonical_exact_bytes_hashes_and_scalar_order(self):
        vector = WIRE["encoding_vector"]
        raw = canonical(vector["value"])
        self.assertEqual(raw, vector["canonical_utf8"].encode())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), vector["sha256_canonical_utf8"])
        self.assertEqual(decode(raw), vector["value"])
        self.assertEqual(canonical({"🧭": 0, "\ue000": 1}), '{"\ue000":1,"🧭":0}'.encode())
        self.assertNotEqual(canonical("é"), canonical("e\u0301"))
        self.assertEqual(canonical(decode("-0")), b"0")
        self.assertEqual(decode(' {"a":0} \n'), {"a": 0})
        for proposal, expected in zip(WIRE["proposals"], WIRE["request_digests"], strict=True):
            self.assertEqual(digest("proposal", {"actor_id": ACTOR, "proposal": proposal}), expected)

    def test_rejects_ambiguous_bytes_and_unsupported_python_values(self):
        for bad in [b'{"a":0,"a":1}', b'{"x":[{"a":0,"\\u0061":1}]}', b"1.0", b"1e0",
                    b"NaN", b"-Infinity", b"Infinity", b"9007199254740992", b"-9007199254740992",
                    b'"\xff"', b"\xef\xbb\xbf{}", br'{"\ud800":0}', br'"\udfff"', b"{}{}", b""]:
            with self.subTest(raw=bad), self.assertRaises(KernelError) as error:
                decode(bad)
            self.assertEqual(error.exception.code, "invalid_input")
        for value in [(1, 2), b"text", {1: "key"}, {"bad": "\ud800"}, 2**53, 1.0, float("nan")]:
            with self.subTest(value=repr(value)), self.assertRaises(KernelError) as error:
                canonical(value)
            self.assertEqual(error.exception.code, "invalid_input")

    def test_timestamp_has_one_wire_spelling(self):
        east = timezone(timedelta(hours=5, minutes=30))
        source = datetime(1, 1, 2, 6, 30, 0, 42, tzinfo=east)
        stamp = format_time(source)
        self.assertEqual(stamp, "0001-01-02T01:00:00.000042Z")
        self.assertEqual(parse_time(stamp), source)
        for invalid in ["2026-11-18T12:00:00Z", "2026-11-18T12:00:00.000000+00:00",
                        "2026-02-29T12:00:00.000000Z", "2026-11-18 12:00:00.000000Z", {}, None]:
            with self.subTest(invalid=invalid), self.assertRaises(KernelError):
                parse_time(invalid)
        with self.assertRaises(KernelError):
            format_time(datetime(2026, 11, 18))


class StoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name, "Purser ?# 🧭.sqlite")
        self.store = Store(self.path)

    def assert_error(self, code, operation, revision=None):
        with self.assertRaises(KernelError) as error:
            operation()
        self.assertEqual(error.exception.code, code)
        if revision is not None:
            self.assertEqual(error.exception.details, {"revision": revision})

    def test_reading_missing_and_initializing_foreign_databases(self):
        self.assertFalse(self.path.exists())
        for operation in (self.store.snapshot, self.store.replay):
            self.assert_error("unavailable", operation)
            self.assertFalse(self.path.exists())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE personal (notes TEXT)")
            db.execute("INSERT INTO personal VALUES ('private note')")
        self.assert_error("integrity_error", self.store.initialize)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone(), ("delete",))
            self.assertEqual(db.execute("SELECT notes FROM personal").fetchone(), ("private note",))

    def test_wire_vectors_through_real_sqlite_and_historical_reads(self):
        genesis = self.store.initialize()
        self.assertEqual(genesis, {"revision": 0, "head_hash": ZERO_HASH, "projection": empty_projection()})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        expected = WIRE["expected"]
        for offset, end in ((0, 2), (2, 4), (4, 8)):
            snap = append_batch(self.store, offset, end)
            self.assertEqual(snap["head_hash"], WIRE["events"][end - 1]["hash"])
            if end in (4, 8):
                self.assertEqual(snap, expected[f"snapshot_at_{end}"])
        for status in expected["status_by_revision"]:
            snap = self.store.snapshot(revision=status["revision"])
            self.assertEqual((snap["head_hash"], digest("projection", snap["projection"])),
                             (status["head_hash"], status["projection_hash"]))
        for bad in (1, 3, 5, 6, 7, 9, -1, True, "8", 8.0):
            self.assert_error("bad_revision", lambda: self.store.snapshot(revision=bad))
        self.assertEqual(self.store.snapshot(revision=0), genesis)
        self.assertEqual(self.store.initialize(), expected["snapshot_at_8"])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertEqual([r[0] for r in db.execute("SELECT body FROM events ORDER BY revision")],
                             [canonical(e) for e in WIRE["events"]])
            self.assertTrue(db.execute("PRAGMA index_info(edges_by_support)").fetchall())

    def test_legacy_upcasts_verified_originals_and_continues_in_v1(self):
        self.store.initialize()
        legacy = WIRE["legacy"]
        old = legacy["events"]
        replace_log(self.path, [(e["revision"], canonical(e)) for e in old])
        snap = self.store.replay()
        self.assertEqual(snap["head_hash"], legacy["expected_head_hash"])
        self.assertEqual(digest("projection", snap["projection"]), legacy["expected_projection_hash"])
        newer = append_batch(self.store, 2, 4)
        self.assertEqual(newer["projection"], WIRE["expected"]["snapshot_at_4"]["projection"])
        self.assertEqual(self.store.replay(), newer)
        with closing(sqlite3.connect(self.path)) as db:
            raw = [r[0] for r in db.execute("SELECT body FROM events ORDER BY revision")]
        self.assertEqual(raw[:2], [canonical(e) for e in old])
        self.assertEqual([decode(row)["event_version"] for row in raw[2:]], [1, 1])

    def test_log_only_rebuild_and_materialized_corruption(self):
        self.store.initialize()
        for start, end in ((0, 2), (2, 4), (4, 8)):
            live = append_batch(self.store, start, end)
        expected_cache = cache_rows(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE records SET body=? WHERE id='C-notify'", (b"{}",))
        self.assert_error("integrity_error", self.store.snapshot)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as reader:
            reader.execute("BEGIN")
            query = "SELECT body FROM records WHERE id='C-notify'"
            self.assertEqual(reader.execute(query).fetchone(), (b"{}",))
            self.assertEqual(self.store.replay(), live)
            self.assertEqual(reader.execute(query).fetchone(), (b"{}",))
            reader.commit()
            self.assertNotEqual(reader.execute(query).fetchone(), (b"{}",))
        self.assertEqual(cache_rows(self.path), expected_cache)
        with closing(sqlite3.connect(self.path)) as db, db:
            for table in expected_cache:
                db.execute(f"DROP TABLE {table}")
        self.assertEqual(self.store.replay(), live)
        self.assertEqual(cache_rows(self.path), expected_cache)

    def test_integrity_and_versions_fail_closed_without_partial_publication(self):
        self.store.initialize()
        append_batch(self.store, 0, 2)
        original = deepcopy(WIRE["events"][:2])
        original_rows = [(e["revision"], canonical(e)) for e in original]
        initial = cache_rows(self.path)
        cases = [
            ("noncanonical raw bytes", [(1, original_rows[0][1] + b" "), original_rows[1]], "integrity_error", 1),
            ("partial final batch", original_rows[:1], "integrity_error", 2),
            ("missing first row", original_rows[1:], "integrity_error", 1),
            ("retained anchor missing suffix", [], "integrity_error", 1),
        ]
        for label, index, key, value, code in [
            ("invalid event chain", 1, "prev_hash", ZERO_HASH, "integrity_error"),
            ("unknown event version", 0, "event_version", 99, "unsupported_version"),
            ("unknown envelope version", 0, "v", 99, "unsupported_version"),
            ("unknown type", 0, "type", "Unrecognized", "unsupported_version"),
            ("receipt already in admitted format", 1, "event_version", 0, "unsupported_version"),
            ("wrong batch identity", 1, "proposal_id", "other", "integrity_error"),
            ("wrong batch size", 1, "batch_size", 3, "integrity_error"),
            ("wrong batch timestamp", 1, "at", WIRE["clock_by_batch"][1], "integrity_error"),
            ("boolean revision", 0, "revision", True, "integrity_error"),
            ("invalid admitted payload", 0, "data", {"id": "C-notify"}, "integrity_error"),
        ]:
            events = deepcopy(original)
            events[index][key] = value
            if key == "prev_hash":
                event = events[index]
                event.pop("hash")
                event["hash"] = digest("event", event)
                rows = [(e["revision"], canonical(e)) for e in events]
            else:
                rows = reseal(events)
            cases.append((label, rows, code, index + 1))
        changed = deepcopy(original)
        changed[0]["event_version"] = 99
        rows = reseal(changed)
        rows[1] = (2, rows[1][1] + b" ")
        cases.append(("verify entire original batch before upcast", rows, "integrity_error", 2))
        rewritten = deepcopy(original)
        rewritten[0]["data"]["statement"] = "Rewritten after the commit."
        cases.append(("recomputed divergent head", reseal(rewritten), "integrity_error", 2))
        for label, rows, code, revision in cases:
            with self.subTest(label=label):
                replace_log(self.path, rows)
                self.assert_error(code, self.store.replay, revision)
                self.assert_error(code, self.store.snapshot, revision)
                self.assertEqual(cache_rows(self.path), initial)

    def test_verified_history_remains_readable_before_a_damaged_later_batch(self):
        self.store.initialize()
        old = append_batch(self.store, 0, 2)
        append_batch(self.store, 2, 4)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE events SET body=? WHERE revision=3", (b"{}",))
        self.assertEqual(self.store.snapshot(revision=2), old)
        self.assert_error("integrity_error", self.store.snapshot, 3)
        self.assert_error("integrity_error", self.store.replay, 3)

    def test_callback_failure_and_late_invalid_event_roll_back_all_writes(self):
        old = self.store.initialize()
        clock = lambda: parse_time(WIRE["clock_by_batch"][0])
        first = WIRE["events"][0]
        clock_read = []

        def abort(snapshot, at):
            clock_read.append(at)
            snapshot["projection"]["records"].append({"arbitrary": True})
            raise RuntimeError("callback interrupted")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.store.transact(ACTOR, "p-abort", clock, abort)
        self.assertEqual(clock_read, WIRE["clock_by_batch"][:1])
        self.assertEqual(self.store.snapshot(), old)
        first_entry = {"type": first["type"], "data": first["data"]}
        self.assert_error("unsupported_version", lambda: self.store.transact(
            ACTOR, "p-bad", clock, lambda *_: ([first_entry, {"type": "Unrecognized", "data": {}}], 0)), 2)
        self.assertEqual(cache_rows(self.path)["records"], [])
        self.assertEqual(self.store.replay(), old)

    def test_reducer_preserves_its_inputs(self):
        genesis = empty_projection()
        first = deepcopy(WIRE["events"][0])
        created = apply_event(genesis, first["type"], first["data"], 1, 2)
        changed = apply_event(created, "CommitmentEdited", {"id": "C-notify", "statement": "New goal."}, 2, 2)
        self.assertEqual(genesis, empty_projection())
        self.assertEqual(first, WIRE["events"][0])
        self.assertNotEqual(created["records"][0]["fields"], changed["records"][0]["fields"])
        self.assertEqual(created["records"][0]["fields"]["statement"], first["data"]["statement"])

    def test_process_death_before_commit_and_after_commit_before_acknowledgment(self):
        self.store.initialize()
        start = append_batch(self.store, 0, 2)
        proc = multiprocessing.get_context("spawn")
        for checkpoint, expected in (("during_append", start), ("after_commit", WIRE["expected"]["snapshot_at_4"])):
            inserted, committed, release = (proc.Event() for _ in range(3))
            child = proc.Process(target=crash_writer, args=(self.path, inserted, committed, release, checkpoint))
            child.start()
            try:
                marker = inserted if checkpoint == "during_append" else committed
                self.assertTrue(marker.wait(10), checkpoint)
                self.assertEqual(self.store.snapshot(), expected)
                with closing(sqlite3.connect(self.path)) as reader:
                    self.assertEqual(reader.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                                     expected["revision"])
            finally:
                child.kill()
                child.join(10)
            self.assertEqual(child.exitcode, -9)
            # Open SQLite after death in a fresh interpreter with no shared objects.
            reopened = subprocess.check_output([sys.executable, "-B", "-c",
                "import json,sys; from purser.kernel.store import Store; "
                "print(json.dumps(Store(sys.argv[1]).replay()))", str(self.path)], cwd=ROOT)
            self.assertEqual(json.loads(reopened), expected)

    def test_separate_writers_serialize_and_independent_reader_stays_live(self):
        self.store.initialize()
        proc = multiprocessing.get_context("spawn")
        result = proc.Queue()
        writers = []
        for name in ("first", "second"):
            attempting, deciding, release = (proc.Event() for _ in range(3))
            child = proc.Process(target=concurrent_writer,
                                 args=(self.path, name, attempting, deciding, release, result))
            writers.append((child, attempting, deciding, release))
        first, second = writers
        try:
            first[0].start()
            self.assertTrue(first[2].wait(10))
            second[0].start()
            self.assertTrue(second[1].wait(10))
            self.assertFalse(second[2].is_set())
            self.assertEqual(self.store.snapshot()["revision"], 0)
            first[3].set()
            self.assertTrue(second[2].wait(10))
            self.assertEqual(self.store.snapshot()["revision"], 1)
            second[3].set()
            self.assertEqual(sorted(result.get(timeout=10) for _ in range(2)),
                             [("first", 0, 1), ("second", 1, 2)])
        finally:
            for child, _, _, release in writers:
                release.set()
                if child.pid:
                    child.join(10)
                    if child.is_alive():
                        child.kill()
                        child.join()
            result.close()
        self.assertEqual([w[0].exitcode for w in writers], [0, 0])
        self.assertEqual(self.store.replay()["revision"], 2)


if __name__ == "__main__":
    unittest.main()
