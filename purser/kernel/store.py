"""The local event log and its transactionally published SQLite projection."""
import os
import sqlite3
from contextlib import closing, contextmanager
from copy import deepcopy
from pathlib import Path

from .projector import apply_event, empty_projection
from .wire import MAX_INTEGER, ZERO_HASH, KernelError, canonical, decode, digest, format_time, parse_time

_APPLICATION_ID = 0x50555253  # PURS
_KEYS = {"records": ("id",), "receipts": ("actor_id", "proposal_id"),
         "edges": ("kind", "from_id", "to_id")}
_BATCH = ("batch_start", "batch_size", "at", "actor_id", "proposal_id")
_ENVELOPE = {"v", "event_version", "revision", "type", "data", "prev_hash", "hash", *_BATCH}


def _schema(db):
    db.execute("CREATE TABLE events (revision INTEGER PRIMARY KEY, body BLOB NOT NULL)")
    db.execute("CREATE TABLE head (id INTEGER PRIMARY KEY CHECK(id=1), "
               "revision INTEGER NOT NULL, hash TEXT NOT NULL)")
    for table, keys in _KEYS.items():
        columns = ", ".join(f"{key} TEXT NOT NULL" for key in keys)
        db.execute(f"CREATE TABLE {table} ({columns}, body BLOB NOT NULL, "
                   f"PRIMARY KEY ({', '.join(keys)})) WITHOUT ROWID")
    db.execute("CREATE INDEX edges_by_support ON edges(kind, to_id)")
    db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
    db.execute("PRAGMA user_version=1")


def _identify(db):
    identity = db.execute("PRAGMA application_id").fetchone()[0]
    if identity == _APPLICATION_ID:
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise KernelError("unsupported_version")
        return True
    if identity or db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone():
        raise KernelError("integrity_error")
    return False


def _event(raw, revision, previous_hash):
    location = {"revision": revision}
    try:
        event = decode(raw)
        if (type(raw) is not bytes or type(event) is not dict or event.keys() != _ENVELOPE
                or canonical(event) != raw):
            raise ValueError("noncanonical event envelope")
        if any(type(event[k]) is not int for k in ("revision", "batch_start", "batch_size", "v", "event_version")):
            raise ValueError("invalid event integer")
        if any(type(event[k]) is not str or not event[k] for k in ("actor_id", "proposal_id", "type")):
            raise ValueError("invalid event identity")
        if (event["revision"] != revision or event["prev_hash"] != previous_hash
                or type(event["data"]) is not dict or event["batch_size"] < 1):
            raise ValueError("invalid event link or payload")
        parse_time(event["at"])
        body = {k: v for k, v in event.items() if k != "hash"}
        if digest("event", body) != event["hash"]:
            raise ValueError("incorrect event hash")
        return event
    except (ValueError, KernelError) as exc:
        raise KernelError("integrity_error", location) from exc


def _batches(db):
    previous = ZERO_HASH
    pending = []
    sequence = 1
    for number, raw in db.execute("SELECT revision,body FROM events ORDER BY revision"):
        if number != sequence:
            raise KernelError("integrity_error", {"revision": min(number, sequence)})
        event = _event(raw, sequence, previous)
        if pending:
            if any(event[k] != pending[0][k] for k in _BATCH):
                raise KernelError("integrity_error", {"revision": sequence})
        elif event["batch_start"] != sequence or sequence + event["batch_size"] - 1 > MAX_INTEGER:
            raise KernelError("integrity_error", {"revision": sequence})
        pending.append(event)
        previous = event["hash"]
        sequence += 1
        if len(pending) == event["batch_size"]:
            yield pending
            pending = []
    if pending:
        raise KernelError("integrity_error", {"revision": sequence})


def _fold(db, requested=None):
    current = {"revision": 0, "head_hash": ZERO_HASH, "projection": empty_projection()}
    historical = current if requested == 0 else None
    for events in _batches(db):
        end = events[-1]["revision"]
        projection = current["projection"]
        for e in events:
            if e["v"] != 1:
                raise KernelError("unsupported_version", {"revision": e["revision"]})
            projection = apply_event(projection, e["type"], e["data"], e["revision"], end,
                                     event_version=e["event_version"])
        current = {"revision": end, "head_hash": events[-1]["hash"], "projection": projection}
        if requested == end:
            historical = current
    if requested is not None and historical is None:
        raise KernelError("bad_revision")
    return current, historical


def _rows(projection, table, keys):
    return {tuple(row[k] for k in keys): row for row in projection[table]}


def _verify_projection(db, snapshot):
    if db.execute("SELECT revision,hash FROM head WHERE id=1").fetchone() != (
        snapshot["revision"], snapshot["head_hash"]
    ):
        raise KernelError("integrity_error")
    for table, keys in _KEYS.items():
        columns = ", ".join(keys)
        expected = _rows(snapshot["projection"], table, keys)
        for *identity, raw in db.execute(f"SELECT {columns},body FROM {table}"):
            row = expected.pop(tuple(identity), None)
            if row is None or canonical(row) != raw:
                raise KernelError("integrity_error")
        if expected:
            raise KernelError("integrity_error")


def _publish(db, snapshot, previous=None):
    for table, keys in _KEYS.items():
        if previous is None:
            db.execute(f"DELETE FROM {table}")
            old = {}
        else:
            old = _rows(previous["projection"], table, keys)
        new = _rows(snapshot["projection"], table, keys)
        where = " AND ".join(f"{k}=?" for k in keys)
        db.executemany(f"DELETE FROM {table} WHERE {where}", ((k) for k in old.keys() - new.keys()))
        changed = (key + (canonical(row),) for key, row in new.items() if row != old.get(key))
        placeholders = ", ".join("?" for _ in range(len(keys) + 1))
        db.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({placeholders})", changed)
    db.execute("INSERT OR REPLACE INTO head VALUES (1,?,?)", (snapshot["revision"], snapshot["head_hash"]))


class Store:
    def __init__(self, path):
        self.path = Path(path).absolute()

    @contextmanager
    def _connection(self, *, write=False):
        mode = "rw" if write else "ro"
        try:
            with closing(sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True,
                                         isolation_level=None, timeout=10)) as db:
                if write:
                    db.execute("PRAGMA synchronous=FULL")
                yield db
        except sqlite3.Error as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 255
            corrupt = code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_SCHEMA,
                               sqlite3.SQLITE_CONSTRAINT, sqlite3.SQLITE_ERROR)
            raise KernelError("integrity_error" if corrupt else "unavailable") from exc

    @contextmanager
    def _transaction(self, *, write=False):
        with self._connection(write=write) as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if not _identify(db) or db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                raise KernelError("unavailable")
            yield db
            db.commit()

    def initialize(self):
        try:
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            pass
        except OSError as exc:
            raise KernelError("unavailable") from exc
        with self._connection(write=True) as db:
            db.execute("BEGIN")
            _identify(db)  # Leave an unrelated database untouched.
            db.commit()
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise KernelError("unavailable")
            db.execute("BEGIN IMMEDIATE")
            existing = _identify(db)
            if not existing:
                _schema(db)
            snapshot, _ = _fold(db)
            if existing:
                _verify_projection(db, snapshot)
            else:
                _publish(db, snapshot)
            db.commit()
            return snapshot

    def snapshot(self, *, revision=None):
        if revision is not None and (type(revision) is not int or not 0 <= revision <= MAX_INTEGER):
            raise KernelError("bad_revision")
        with self._transaction() as db:
            current, historical = _fold(db, revision)
            _verify_projection(db, current)
            return current if revision is None else historical

    def replay(self):
        with self._transaction(write=True) as db:
            current, _ = _fold(db)
            _publish(db, current)
            return current

    def transact(self, actor_id, proposal_id, clock, decide):
        with self._transaction(write=True) as db:
            before, _ = _fold(db)
            _verify_projection(db, before)
            at = format_time(clock())
            descriptions, reply = decide(deepcopy(before), at)
            if type(descriptions) is not list:
                raise KernelError("invalid_input")
            if not descriptions:
                return before, reply
            start = before["revision"] + 1
            end = start + len(descriptions) - 1
            projection, previous = before["projection"], before["head_hash"]
            for revision, entry in enumerate(descriptions, start):
                if type(entry) is not dict or entry.keys() != {"type", "data"}:
                    raise KernelError("invalid_input")
                event = {"v": 1, "event_version": 1, "revision": revision, "batch_start": start,
                         "batch_size": len(descriptions), "actor_id": actor_id, "proposal_id": proposal_id,
                         "at": at, "type": entry["type"], "data": entry["data"], "prev_hash": previous}
                previous = event["hash"] = digest("event", event)
                raw = canonical(event)
                _event(raw, revision, event["prev_hash"])
                db.execute("INSERT INTO events VALUES (?,?)", (revision, raw))
                projection = apply_event(projection, entry["type"], entry["data"], revision, end)
            after = {"revision": end, "head_hash": previous, "projection": projection}
            _publish(db, after, before)
            return after, reply
