"""Exercise archival recovery on the retained real kernel proposals, heads and attachments."""

import copy
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from experiments.e1.accounting import kernel_rows
from experiments.e1.artifacts import encoded, sha
from experiments.e1.report import Objects
from experiments.e2.kernel_history import recover_suffix, restore_copy
from experiments.e2.public_history import read_participant
from experiments.e2.public_sources import view_ref
from experiments.e2.views import CorroboratedViews
from purser.kernel.gate import admit
from purser.kernel.store import Store
from purser.kernel.wire import digest


class KernelSuffix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        packet = Path(__file__).resolve().parents[1] / "docs/evidence/M3/precollection-controls/e2-82e8807-private-controls.zip"
        with zipfile.ZipFile(packet) as outer, zipfile.ZipFile(io.BytesIO(outer.read("broad-controls/broad-private-evidence.zip"))) as broad:
            cls.archive = zipfile.ZipFile(io.BytesIO(broad.read("ordinary/raw.zip")))
        cls.addClassCleanup(cls.archive.close)
        obj = Objects(cls.archive)
        raw = json.loads(cls.archive.read("runs/primary/2001/purser.json"))
        cls.raw_platform = obj.item(raw["artifacts"]["platform"])
        cls.raw_journal = obj.item(raw["artifacts"]["journal"])
        cls.prefix = obj.item(cls.raw_platform["checkpoint_measurement"]["submission_source"])
        cls.original = obj.item(cls.raw_platform["treatment_trace"])

    def setUp(self):
        self.journal = copy.deepcopy(self.raw_journal)
        self.platform = dict(self.raw_platform, treatment_trace=None, final_public=None, participant_exit=None)
        self.issues, self.missing = [], []

    def act(self, number):
        return next(e["payload"] for e in self.journal if e["kind"] == "action" and e["payload"]["attempt"] == number)

    def recover(self):
        return recover_suffix(self.prefix, self.journal, self.platform, Objects(self.archive),
                              self.issues.append, self.missing.append)

    def participant(self):
        objects = Objects(self.archive)
        views = CorroboratedViews("purser", objects, self.journal, self.platform,
                                  self.issues.append, unavailable=self.missing.append)
        return read_participant("purser", views.events(self.journal), self.platform, views.item,
                                self.issues.append, originals=views.originals, track="primary",
                                seed=2001, unavailable=self.missing.append)

    def test_hash_matched_batches_equal_every_original_sqlite_byte(self):
        fault = next(e["payload"] for e in self.journal if e["kind"] == "fault")
        result = self.recover()
        self.assertEqual(self.issues + self.missing, [])
        self.assertEqual(result["original_prefix_revision"], 56)
        self.assertEqual(result["recovered_through_revision"], 94)
        self.assertEqual(result["original_events"], self.original["original_events"])
        self.assertEqual(result["snapshot"], self.original["snapshot"])
        self.assertEqual(result["recovered_bindings"][fault["released_sha256"]], fault["released"])
        self.assertEqual(len(self.prefix["original_events"]), 56)
        self.assertEqual(self.participant().kernel_transmissions, [])

    def test_wrong_premise_reproduces_receipt_but_cannot_cross_original_r58(self):
        fault = next(e["payload"] for e in self.journal if e["kind"] == "fault")
        fault["released"] = dict(fault["released"], seed=2099)
        fault["released_sha256"] = sha(encoded(fault["released"]))
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "ledger.sqlite")
            restore_copy(store, self.prefix)
            req = fault["proposal"]
            bound = dict(role="observer", actor_id=req["actor_id"], commitment="C-notify",
                         principal_id="principal:local", assignment="A1", premise={"artifact":encoded(fault["released"])})
            receipt = admit(store, bound, req["body"], clock=lambda: datetime(2026,1,1,tzinfo=timezone.utc))
            self.assertEqual(receipt, fault["return"])
            self.assertEqual(view_ref(kernel_rows(store.snapshot()["projection"])), fault["after"]["view"])
            self.assertNotEqual(store.snapshot()["head_hash"], fault["after"]["head_hash"])
        result = self.recover()
        self.assertEqual(result["original_events"], self.prefix["original_events"])
        self.assertTrue(any("kernel head differs" in e and "r58" in e for e in self.issues))

    def test_missing_redundant_ack_observer_operand_and_last_view(self):
        closing = self.act(48)
        for key in ("after", "return", "response_evidence", "finished_monotonic_ns"):
            closing.pop(key)
        # Original inner head and its independently captured publication still identify P-40.
        self.act(32)["port_input"].pop("observation")
        self.act(32).pop("return")  # The case's independent archived response carries the original witness.
        closing["port_after"] = {k:v for k,v in closing["port_after"].items() if k != "view"}
        result = self.recover()
        self.assertEqual(self.issues + self.missing, [])
        self.assertEqual(result["original_events"], self.original["original_events"])

    def test_lost_middle_proposal_and_later_snapshots_cannot_fill_gap(self):
        parent = self.act(37)
        for key in ("requests", "proposal"):
            parent.pop(key)
        result = self.recover()
        self.assertEqual(result["original_events"], self.original["original_events"][:72])
        self.assertEqual(self.issues, [])
        self.assertTrue(self.missing)

    def test_lost_middle_batch_retains_later_transmission_and_inner_closure_reply(self):
        opening = self.act(31)
        opening.pop("requests")
        opening.pop("proposal")
        for key in ("return", "response_evidence", "after", "finished_monotonic_ns"):
            self.act(48).pop(key)
        history = self.participant()
        self.assertEqual(history.source.publications[-1].checkpoint["revision"], 64)
        received = {t["attempt"]: t for t in history.kernel_transmissions}
        self.assertNotIn(29, received)
        parent = received[37]
        self.assertEqual((parent["actor"], parent["operation"], parent["subject"], parent["candidate"]),
                         ("executor:script", "result.submit", "W-notify", self.act(37)["port_input"]["candidate"]))
        self.assertEqual((parent["proposal_id"], parent["receipt_revision"], parent["disposition"]),
                         ("P-30", 74, "admitted"))
        self.assertEqual(parent["observed_head"]["head_hash"], self.act(37)["port_after"]["head_hash"])
        self.assertFalse(history.numbered[37]["publicly_submitted"])
        self.assertEqual(received[48]["receipt_sources"], ["port_return"])
        self.assertEqual(received[48]["closure_row"], self.act(48)["port_return"]["row"])
        self.assertFalse(history.public_complete)

    def test_changed_later_inner_receipt_cannot_displace_the_original_outer_return(self):
        self.act(31).pop("requests")
        self.act(31).pop("proposal")
        self.act(37)["port_return"] = dict(self.act(37)["port_return"], digest="f" * 64)
        history = self.participant()
        parent = next(t for t in history.kernel_transmissions if t["attempt"] == 37)
        self.assertEqual(parent["receipt_sources"], ["archived_return"])
        self.assertEqual(parent["receipt_revision"], 74)
        self.assertTrue(any("P-30" in message and "does not identify" in message for message in self.issues))
        self.assertFalse(history.numbered[37]["publicly_submitted"])

    def test_malformed_archived_envelope_preserves_the_independent_port_reply(self):
        self.act(31).pop("requests")
        self.act(31).pop("proposal")
        raw = json.loads(self.archive.read("runs/primary/2001/purser.json"))
        self.act(37)["response_evidence"] = raw["artifacts"]["journal"]
        history = self.participant()
        parent = next(t for t in history.kernel_transmissions if t["attempt"] == 37)
        self.assertEqual(parent["receipt_sources"], ["port_return"])
        self.assertEqual(parent["receipt_revision"], 74)
        self.assertTrue(any("another invocation" in message for message in self.issues))

    def test_wrong_public_hash_preserves_the_last_matching_batch(self):
        for row in self.journal:
            for checkpoint in row["payload"].values():
                if isinstance(checkpoint, dict) and checkpoint.get("revision") == 72 and "head_hash" in checkpoint:
                    checkpoint["head_hash"] = "f" * 64
        result = self.recover()
        self.assertEqual(result["original_events"], self.original["original_events"][:70])
        self.assertTrue(any("kernel head differs" in issue and "r72" in issue for issue in self.issues))

    def test_lost_redundant_platform_attachments_and_contradictory_return(self):
        self.platform.pop("definition_artifacts")
        self.platform.pop("environment_artifact")
        self.act(37)["port_return"] = dict(self.act(37)["port_return"], disposition="rejected")
        result = self.recover()
        self.assertEqual(result["original_events"], self.original["original_events"])
        self.assertTrue(any("receipt differs from port_return" in issue for issue in self.issues))
        self.assertEqual(self.missing, [])

    def test_rejected_proposal_then_transport_replay_is_one_committed_batch(self):
        terminal = self.original["snapshot"]
        before = dict(revision=94, head_hash=terminal["head_hash"], view=view_ref(kernel_rows(terminal["projection"])))
        p = copy.deepcopy(self.act(37))
        for key in ("response_evidence", "return", "requests"):
            p.pop(key)
        # Reusing an assignment with an accepted result is a genuine rejected operation.
        proposal = copy.deepcopy(p["proposal"])
        body = proposal["body"]
        body.update(proposal_id="P-rejected-replay-test", base_revision=94)
        body["ops"][0]["id"] = "R-extra"
        proposal.update(proposal_id=body["proposal_id"], digest=digest("proposal", {"actor_id":proposal["actor_id"],"proposal":body}))
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "control.sqlite")
            restore_copy(store, self.original)
            binding = dict(role="executor", actor_id=proposal["actor_id"], principal_id="principal:local",
                           commitment="C-notify", assignment=body["ops"][0]["assignment"])
            receipt = admit(store, binding, body, clock=lambda: datetime(2026,1,1,tzinfo=timezone.utc))
            self.assertEqual(receipt["disposition"], "rejected")
            snapshot = store.snapshot()
            after = dict(revision=95, head_hash=snapshot["head_hash"], view=view_ref(kernel_rows(snapshot["projection"])))
            self.assertEqual(admit(store, binding, body, clock=lambda: datetime(2026,1,1,tzinfo=timezone.utc)), receipt)
            with sqlite3.connect(store.path) as db:
                expected = db.execute("SELECT body FROM events WHERE revision=95").fetchone()[0].decode()
        p.update(attempt=49, before=before, port_before=before, after=after, port_after=after, proposal=proposal,
                 port_return=receipt)
        self.journal.extend([dict(kind="action", payload=p),
                             dict(kind="action", payload=dict(p, attempt=50, port_before=after, before=after))])
        result = self.recover()
        self.assertEqual(result["original_events"], [*self.original["original_events"], expected])
        self.assertEqual(self.issues + self.missing, [])
