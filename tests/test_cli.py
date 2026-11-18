"""A fresh interpreter for every public command and frozen expected observations."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path

from purser.kernel.store import Store

ROOT = Path(__file__).resolve().parents[1]
WIRE = json.loads((ROOT / "docs/contracts/wire-v1.json").read_bytes())
EXPECTED = WIRE["expected"]
AS_OF = EXPECTED["replay_as_of"]
M2_STATUS = {s["revision"]: {**s, "answers": {**s["answers"], "q03": [], "q04": []}}
             for s in json.loads((ROOT / "docs/contracts/m2-legacy-status-v1.json").read_bytes())}


def published_digest(path):
    # Read the three installed SQLite views in one snapshot, outside the product API.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        projection = {}
        for table, sort_keys in (
            ("records", "id"), ("receipts", "actor_id,proposal_id"),
            ("edges", "kind,from_id,to_id"),
        ):
            rows = db.execute(f"SELECT body FROM {table} ORDER BY {sort_keys}")
            projection[table] = [json.loads(body) for (body,) in rows]
    raw = json.dumps(projection, ensure_ascii=False, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b"purser/projection/v1\x00" + raw).hexdigest()


class PublicCLI(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "workspace.sqlite"

    def command(self, *args, stdin=None, code=0):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "purser", "--db", str(self.db), *args],
            cwd=ROOT, input=stdin, capture_output=True, timeout=15,
        )
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.returncode, code, result.stdout)
        self.assertTrue(result.stdout.endswith(b"\n"))
        self.assertEqual(result.stdout.count(b"\n"), 1)
        return json.loads(result.stdout.decode("utf-8"))

    def propose(self, proposal, code=0):
        return self.command("propose", "-", stdin=json.dumps(proposal).encode(), code=code)

    def error(self, error, exit_code, *args, stdin=None):
        reply = self.command(*args, stdin=stdin, code=exit_code)
        self.assertEqual(set(reply), {"v", "error"})
        self.assertEqual(reply["v"], 1)
        self.assertEqual(reply["error"]["code"], error)
        self.assertIsInstance(reply["error"]["details"], dict)

    def test_frozen_replies_on_historical_and_rebuilt_store(self):
        self.assertEqual(self.command("init"), EXPECTED["init"])
        self.assertEqual(self.command("status", "--as-of", AS_OF), M2_STATUS[0])

        # Install original fixture batches by public store API, independent of CLI and gate.
        store = Store(self.db)
        for _, events in groupby(WIRE["events"], key=lambda e: e["batch_start"]):
            batch = list(events)
            first = batch[0]
            at = datetime.fromisoformat(first["at"])
            descriptions = [{"type": e["type"], "data": e["data"]} for e in batch]
            store.transact(first["actor_id"], first["proposal_id"], lambda: at,
                           lambda snapshot, time: (descriptions, None))
        for original in [EXPECTED["initial_status"], *EXPECTED["status_by_revision"]]:
            with self.subTest(revision=original["revision"]):
                actual = self.command("status", "--revision", str(original["revision"]), "--as-of", AS_OF)
                self.assertEqual(actual, M2_STATUS[original["revision"]])
                self.assertEqual(dict(actual, answers={"q01": actual["answers"]["q01"]}), original)

        self.assertEqual(self.command("why", "C-notify", "--as-of", AS_OF),
                         EXPECTED["why_active_commitment"])
        pending = next(row for row in EXPECTED["snapshot_at_4"]["projection"]["records"]
                       if row["kind"] == "Approval")
        historical = self.command("why", pending["id"], "--revision", "4",
                                  "--as-of", "2040-01-01T00:00:00.000000Z")
        self.assertEqual(historical["record"], pending)
        self.assertEqual(historical["reasons"],
                         [{"code": "awaiting_activation", "refs": [pending["id"]]}])
        self.assertEqual(self.command("replay", "--as-of", AS_OF),
                         M2_STATUS[8])
        head = M2_STATUS[8]
        self.assertEqual(self.command("init"), {k: head[k] for k in ("v", "revision", "head_hash")})
        for revision in ("1", "9", "-1"):
            self.error("bad_revision", 4, "status", "--revision", revision)
        self.error("not_found", 4, "why", "missing")

    def test_public_proposals_bind_owner_and_survive_restart(self):
        self.command("init")
        self.assertEqual(self.db.stat().st_mode & 0o077, 0)
        create, hold, answer = WIRE["proposals"]
        proposal_file = self.db.parent / "proposal with spaces.json"
        proposal_file.write_text(json.dumps(create))
        receipt = self.command("propose", str(proposal_file))
        self.assertEqual(receipt, EXPECTED["admission_replies"][0]["stdout"])
        self.assertEqual(self.propose(create), receipt)
        changed = dict(create, justification=["an unrelated reference"])
        self.error("id_conflict", 4, "propose", "-", stdin=json.dumps(changed).encode())

        expiry = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        hold = dict(hold, ops=[dict(hold["ops"][0], expires_at=expiry)])
        pending = self.propose(hold, 3)["receipt"]
        self.assertEqual(pending["disposition"], "pending")
        self.assertEqual(pending["actor_id"], "principal:local")
        approval = pending["details"]["approval_id"]
        answer = dict(answer, ops=[dict(answer["ops"][0], id=approval,
                                        request_digest=pending["digest"])])
        approved = self.propose(answer)["receipt"]
        self.assertEqual(approved["details"], {"approval_id": approval, "state": "approved"})
        final = self.propose(hold)["receipt"]
        self.assertEqual((final["disposition"], final["revision"]), ("admitted", 8))
        self.assertEqual(self.command("status", "--as-of", AS_OF)["answers"],
                         M2_STATUS[8]["answers"])

        unsupported = dict(create, proposal_id="p-unsupported", base_revision=8, ops=[{"op": "unknown"}])
        rejected = self.propose(unsupported, 4)["receipt"]
        self.assertEqual((rejected["disposition"], rejected["code"]), ("rejected", "unknown_op"))
        live = self.command("status", "--as-of", AS_OF)
        self.assertEqual(live["projection_hash"], published_digest(self.db))
        self.assertEqual(self.command("replay", "--as-of", AS_OF), live)
        self.assertEqual(published_digest(self.db), live["projection_hash"])

    def test_errors_do_not_create_or_change_a_store(self):
        for args in (("status",), ("why", "missing"), ("replay",), ("propose", "-")):
            self.error("unavailable", 6, *args, stdin=b"{}")
            self.assertFalse(self.db.exists())
        self.command("init")
        proposal = WIRE["proposals"][0]
        raw = json.dumps(proposal).encode()
        for invalid in (b'{"v":1,' + raw[1:], raw.replace(b'"v": 1', b'"v": 1.0'),
                        raw.replace(b"Add notification retries.", br'\ud800'), b"\xff",
                        json.dumps(dict(proposal, actor_id="principal:other")).encode()):
            self.error("invalid_input", 2, "propose", "-", stdin=invalid)
        self.error("invalid_input", 2, "propose", str(self.db.parent / "missing.json"))
        self.error("invalid_input", 2, "propose", "-", "--as-of", AS_OF)
        self.error("invalid_input", 2, "--actor", "principal:other", "status")
        self.error("invalid_input", 2, "status", "--as-of", "2026-11-31T00:00:00.000000Z")
        self.error("invalid_input", 2, "replay", "--revision", "0")
        self.assertEqual(self.command("status", "--as-of", AS_OF), M2_STATUS[0])
        before = datetime.now(timezone.utc)
        read = self.command("status")
        after = datetime.now(timezone.utc)
        self.assertLessEqual(before, datetime.fromisoformat(read["as_of"]))
        self.assertLessEqual(datetime.fromisoformat(read["as_of"]), after)

    @unittest.skipIf(os.geteuid() == 0, "foreign OS owner check runs as a non-root user")
    def test_rejects_foreign_posix_file_owner_before_storage_access(self):
        self.db = Path("/etc/passwd")
        self.assertNotEqual(self.db.stat().st_uid, os.geteuid())
        self.error("unauthenticated", 5, "status")
