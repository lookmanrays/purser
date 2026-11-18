"""Exercise the real CLI against retained checkpoints and independently supplied originals."""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from docs.contracts.check_vectors import canonical, digest
from tests.test_cli import AS_OF, EXPECTED, M2_STATUS as STATUSES, ROOT, WIRE

TABLES = ("events", "head", "records", "receipts", "edges")
ZERO = "0" * 64


def seal(events):
    """Build an internally coherent independent original chain after changing fixture inputs."""
    previous = ZERO
    for event in events:
        event.pop("hash")
        event["prev_hash"] = previous
        previous = event["hash"] = digest("event", event)
    return events


class RetainedHistory(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.db = Path(workspace.name) / "workspace.sqlite"
        self.cli("init")
        self.replace_events(WIRE["events"])
        self.assertEqual(self.cli("replay", "--as-of", AS_OF), STATUSES[8])

    def cli(self, *arguments, proposal=None, exit_code=0):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "purser", "--db", str(self.db), *arguments],
            cwd=ROOT, input=None if proposal is None else canonical(proposal),
            capture_output=True, timeout=15,
        )
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.returncode, exit_code, result.stdout)
        self.assertTrue(result.stdout.endswith(b"\n"))
        self.assertEqual(result.stdout.count(b"\n"), 1)
        return json.loads(result.stdout.decode("utf-8"))

    def physical(self):
        # Include schema so failed replay cannot silently create an absent projection.
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            schema = list(db.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY type,name"))
            names = {name for typ, name, _ in schema if typ == "table"}
            rows = {table: sorted(db.execute(f"SELECT * FROM {table}"), key=repr)
                    for table in TABLES if table in names}
            return {"schema": schema, **rows}

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(statement, parameters)

    def replace_events(self, events):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DELETE FROM events")
            db.executemany("INSERT INTO events VALUES (?,?)",
                           ((e["revision"], canonical(e)) for e in events))

    def checkpoint(self, revision, hash_value):
        self.sql("UPDATE head SET revision=?, hash=?", (revision, hash_value))

    def status(self, revision):
        return self.cli("status", "--revision", str(revision), "--as-of", AS_OF)

    def reject(self, code, *arguments, at=None, proposal=None):
        with self.subTest(arguments=arguments, outcome=code, at=at):
            before = self.physical()
            exit_code = {"bad_revision": 4, "integrity_error": 7, "unsupported_version": 7}[code]
            reply = self.cli(*arguments, proposal=proposal, exit_code=exit_code)
            self.assertEqual(set(reply), {"v", "error"})
            self.assertEqual((reply["v"], reply["error"]["code"]), (1, code))
            details = reply["error"]["details"]
            self.assertIsInstance(details, dict)
            if "revision" in details:
                self.assertIs(type(details["revision"]), int)
                self.assertTrue(0 <= details["revision"] < 2**53)
            if at is not None:
                self.assertEqual(details, {"revision": at})
            self.assertEqual(self.physical(), before)

    def reject_live(self, *, at=None):
        for command in [("init",), ("status",), ("why", "absent")]:
            self.reject("integrity_error", *command, at=at)
        create = WIRE["proposals"][0]
        # The three requests exercise exact dedup, a fresh current base and a valid older base.
        for proposal in [create, dict(create, proposal_id="p-new", base_revision=8),
                         dict(create, proposal_id="p-old-base", base_revision=2)]:
            self.reject("integrity_error", "propose", "-", proposal=proposal, at=at)

    def test_two_coherent_histories_disagree_at_retained_checkpoint(self):
        events = deepcopy(WIRE["events"])
        proposal = deepcopy(WIRE["proposals"][0])
        statement = "Send an independently prepared notification."
        proposal["ops"][0]["statement"] = events[0]["data"]["statement"] = statement
        events[1]["data"]["digest"] = digest("proposal", {"actor_id": WIRE["actor"], "proposal": proposal})
        self.replace_events(seal(events))
        # At 2, the later retained hash cannot attest this historical answer.
        earlier = self.cli("why", "C-notify", "--revision", "2", "--as-of", AS_OF)
        self.assertEqual(earlier["record"]["fields"]["statement"], statement)
        self.reject_live(at=8)
        self.reject("integrity_error", "status", "--revision", "8", at=8)
        self.reject("integrity_error", "why", "absent", "--revision", "8", at=8)
        self.reject("integrity_error", "replay", at=8)
        self.checkpoint(4, STATUSES[4]["head_hash"])
        for n in ("3", "4", "8"):
            self.reject("integrity_error", "status", "--revision", n, at=4)

    def test_complete_suffix_loss_and_partial_frame_have_distinct_missing_revisions(self):
        self.reject("bad_revision", "status", "--revision", "9")
        for remaining, first_missing in [(6, 7), (4, 5), (0, 1)]:
            with self.subTest(remaining=remaining):
                self.sql("DELETE FROM events WHERE revision>?", (remaining,))
                boundary = 4 if remaining else 0
                self.assertEqual(self.status(boundary), STATUSES[boundary])
                # Reaching any portion of a truncated frame cannot yield an intact boundary.
                for n in sorted({boundary + 1, first_missing, 8, 9}):
                    self.reject("integrity_error", "status", "--revision", str(n), at=first_missing)
                self.reject("integrity_error", "why", "absent", "--revision", "8", at=first_missing)
                self.reject_live(at=first_missing)
                self.reject("integrity_error", "replay", at=first_missing)

    def test_checkpoint_inside_frame_precedes_rejection_of_an_interior_request(self):
        self.checkpoint(3, WIRE["events"][2]["hash"])
        self.assertEqual(self.status(2), STATUSES[2])
        for n in ("3", "4", "8"):
            self.reject("integrity_error", "status", "--revision", n, at=3)
        self.checkpoint(4, STATUSES[4]["head_hash"])
        self.reject("bad_revision", "status", "--revision", "3")
        self.assertEqual(self.status(8), STATUSES[8])
        self.reject_live()
        originals = self.physical()["events"]
        self.assertEqual(self.cli("replay", "--as-of", AS_OF), STATUSES[8])
        self.assertEqual(self.physical()["events"], originals)

    def test_malformed_checkpoint_is_visible_even_before_its_position(self):
        for rev, value in [
            (-1, ZERO), (2**53, ZERO), (3.5, ZERO), ("opaque", ZERO), (b"8", ZERO),
            (8, "A" * 64), (8, "g" * 64), (8, "a" * 63), (8, b"a" * 64),
            (0, "f" * 64),
        ]:
            with self.subTest(revision=rev, hash=value):
                self.checkpoint(rev, value)
                self.reject("integrity_error", "status", "--revision", "0")
                self.reject("integrity_error", "replay")
        self.checkpoint(0, ZERO)
        self.assertEqual(self.status(0), STATUSES[0])
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("PRAGMA ignore_check_constraints=ON")
            db.execute("INSERT INTO head VALUES (2,0,?)", (ZERO,))
        self.reject("integrity_error", "status", "--revision", "0")
        self.sql("DELETE FROM head WHERE id=1")
        self.reject("integrity_error", "replay")

    def test_current_cache_damage_does_not_rewrite_named_prefix(self):
        original = self.physical()
        self.sql("UPDATE records SET body=? WHERE id='C-notify'", (b"{}",))
        damaged = self.physical()
        self.assertEqual(self.status(8), STATUSES[8])
        self.assertEqual(self.cli("why", "C-notify", "--revision", "8", "--as-of", AS_OF),
                         EXPECTED["why_active_commitment"])
        self.assertEqual(self.physical(), damaged)
        self.reject_live()
        self.assertEqual(self.cli("replay", "--as-of", AS_OF), STATUSES[8])
        self.assertEqual(self.physical(), original)

    def test_absent_checkpoint_is_unanchored_for_row_and_log_only_restore(self):
        for remaining, remove_tables in [(8, False), (4, True), (0, True)]:
            with self.subTest(remaining=remaining, remove_tables=remove_tables):
                self.sql("DELETE FROM events WHERE revision>?", (remaining,))
                if remove_tables:
                    for table in ("head", "records", "receipts", "edges"):
                        self.sql(f"DROP TABLE {table}")
                else:
                    self.sql("DELETE FROM head")
                untouched = self.physical()
                self.assertEqual(self.status(remaining), STATUSES[remaining])
                self.reject("bad_revision", "status", "--revision", str(remaining + 1))
                self.assertEqual(self.physical(), untouched)
                self.reject_live()
                # No independent checkpoint exists in this fixture to certify a missing suffix.
                self.assertEqual(self.cli("replay", "--as-of", AS_OF), STATUSES[remaining])
                self.assertEqual(self.physical()["events"], untouched["events"])

    def test_mixed_legacy_current_and_unknown_version_after_named_prefix(self):
        events = deepcopy(WIRE["legacy"]["events"] + WIRE["events"][2:])
        self.replace_events(seal(events))
        self.sql("DELETE FROM head")
        legacy_hash = WIRE["legacy"]["expected_head_hash"]
        self.assertEqual(self.status(2)["head_hash"], legacy_hash)
        originals = self.physical()["events"]
        replayed = self.cli("replay", "--as-of", AS_OF)
        self.assertEqual(replayed["projection_hash"], STATUSES[8]["projection_hash"])
        self.assertEqual(self.physical()["events"], originals)
        self.checkpoint(2, legacy_hash)
        events[2]["event_version"] = 99
        self.replace_events(seal(events))
        self.assertEqual(self.status(2)["head_hash"], legacy_hash)
        for args in [("status",), ("status", "--revision", "4"), ("why", "absent"), ("replay",)]:
            self.reject("unsupported_version", *args, at=3)
