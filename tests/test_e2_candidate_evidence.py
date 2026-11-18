"""Actual Git custody across staged pack publication, progress and pooled replay."""

import tempfile
import unittest
from pathlib import Path

from experiments.e1.artifacts import START, Artifacts, Candidates
from experiments.e1.capture import response_evidence
from experiments.e1.recovery import FileArchive
from experiments.e1.report import GitPacks, Objects
from experiments.e2.candidate_evidence import (Catalog, checkout_observation, inspected_oids, observe_stage,
                                               preserve_damaged_objects, reconcile)


class CandidateCustody(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.stage = self.directory / "stage"
        self.trial = self.directory / "trial"
        self.stage.mkdir()
        self.trial.mkdir()
        self.assets = Artifacts(FileArchive(self.stage))
        self.candidates = Candidates(self.trial, self.assets)
        (self.candidates.work / "note").write_text("one")
        self.edit = self.candidates.commit("edit")
        self.entries = self.candidates.entries
        self.issues = []
        self.catalog = self.reader("restore")

    def reader(self, name):
        directory = self.directory / name
        directory.mkdir()
        return Catalog(Objects(self.assets.archive), GitPacks(directory))

    def seals(self):
        return observe_stage(self.trial, self.assets, self.catalog, set(), lambda *e: self.issues.append(str(e)))

    def seal(self, oid):
        return {"candidate": oid, "pack": self.entries[oid]["pack"]}

    def test_stage_pack_proves_its_tip_without_an_installed_map_or_disposable_repo(self):
        self.trial.rename(self.directory / "set-aside")
        seals = self.seals()
        members, entries, _ = reconcile(self.catalog, {}, seals, set(), self.issues.append, capture=True)
        self.assertEqual(self.issues, [])
        self.assertEqual(members, {START, self.edit})
        self.assertEqual(entries, self.entries)

    def test_content_ancestry_and_pool_membership_are_separate(self):
        edit_seal = [self.seal(self.edit)]
        mapping = {self.edit: self.entries[self.edit]}
        members, _, _ = reconcile(self.catalog, mapping, edit_seal, set(), self.issues.append)
        self.assertEqual(members, {self.edit})
        self.assertEqual(self.issues, [])
        members, _, _ = reconcile(self.catalog, self.entries, [self.seal(START)], set(), self.issues.append)
        self.assertEqual(members, {START})
        self.assertTrue(any(self.edit in s and "unsupported" in s for s in self.issues))

    def test_original_candidate_with_missing_own_pack_uses_descendant_bytes(self):
        (self.stage / self.entries[START]["pack"]["path"]).unlink()
        members, entries, repos = reconcile(self.catalog, self.entries, [self.seal(START), self.seal(self.edit)],
                                             set(), self.issues.append)
        self.assertEqual(members, {START, self.edit})
        self.assertEqual(set(repos), members)
        self.assertEqual(entries[START]["pack"], self.entries[self.edit]["pack"])
        self.assertTrue(any(START in s and "unavailable" in s for s in self.issues))

    def test_referenced_tips_survive_each_omission_and_old_producer(self):
        refs = [entry["pack"] for entry in self.entries.values()]
        for omit in (START, self.edit):
            self.issues.clear()
            other = self.edit if omit == START else START
            members, _, repos = reconcile(self.catalog, self.entries, [self.seal(other)], set(), self.issues.append,
                                           references=refs)
            self.assertEqual(members, set(self.entries))
            self.assertEqual(set(repos), members)
            self.assertEqual(len(self.issues), 1)
            self.assertIn(f"lacks referenced pack for {omit}", self.issues[0])
        for modern in (False, True):
            self.issues.clear()
            members, _, _ = reconcile(self.catalog, self.entries, None, set(), self.issues.append,
                                      references=refs, expect_seals=modern)
            self.assertEqual(members, set(self.entries))
            self.assertEqual(bool(self.issues), modern)
        self.issues.clear()
        members, _, _ = reconcile(self.catalog, self.entries, [self.seal(self.edit)], set(), self.issues.append,
                                   references=[self.entries[START]["pack"]], expect_seals=False)
        self.assertEqual(members, {START})
        self.assertTrue(any("outside this registered producer" in e for e in self.issues))

    def test_broken_seal_map_or_pool_alone_cannot_establish_a_candidate(self):
        start = self.seal(START)
        wrong = dict(start["pack"], bytes=start["pack"]["bytes"] + 1)
        for inventory in (None, [], [start], [start, {"candidate": self.edit, "pack": wrong}],
                          [start, {"candidate": self.edit, "pack": start["pack"]}]):
            members, entries, repos = reconcile(self.catalog, self.entries, inventory, {START}, self.issues.append,
                                                 references=[start["pack"]])
            self.assertEqual(members, {START})
            self.assertEqual(set(entries), {START})
            self.assertEqual(set(repos), {START})
            self.assertTrue(any("unsupported" in s and self.edit in s for s in self.issues))

    def test_authenticated_original_map_keeps_missing_member_and_checks_available_provenance(self):
        original = {self.edit: self.entries[self.edit]}
        (self.stage / self.entries[self.edit]["pack"]["path"]).unlink()
        absent = []
        members, entries, _ = reconcile(self.catalog, {}, [self.seal(START)], set(), self.issues.append,
                                        original_map=original, unavailable=absent.append)
        self.assertEqual(members, {START, self.edit})
        self.assertEqual(set(entries), {START})
        self.assertTrue(any(self.edit in s and "unavailable" in s for s in absent))
        # A pack produced for another target cannot independently corroborate an old map.
        original = {self.edit: dict(self.entries[self.edit], pack=self.entries[START]["pack"])}
        members, _, _ = reconcile(self.catalog, {}, [self.seal(START)], set(), self.issues.append, original_map=original)
        self.assertEqual(members, {START})
        self.assertTrue(any("different original tip" in s for s in self.issues))

    def test_scoped_git_inspection_requires_actual_work_and_received_or_returned_bytes(self):
        work = "W-notify"
        action = {"arm": "loop", "action": "context", "actor": "planner", "subject": work, "attempt": 1,
                  "input": {"tool": "git_history", "work": work}, "candidate": self.edit}
        requested = {k: action[k] for k in ("action", "actor", "subject", "candidate", "input")}
        action["work_request"] = dict(requested, method="inspect")
        def observed():
            return inspected_oids([{"kind": "action", "payload": action}], "loop", Objects(self.assets.archive),
                                  self.issues.append)
        self.assertFalse(observed())
        action["work_started"] = action["work_request"].copy()
        self.assertFalse(observed())
        action["return"] = {"value": [self.edit, START]}
        action["response_evidence"] = self.assets.put(response_evidence(action), "json")
        self.assertEqual(observed(), {self.edit})
        action["candidate"] = None
        for field in ("work_started", "work_request"):
            action[field]["candidate"] = None
        self.assertFalse(observed())  # A callback may transform the outer return.
        ref = self.assets.put(action["return"], "json")
        action["delivery"] = {"attempt": 1, "state": "received", "subject": work, "payload": ref, "sha256": ref["sha256"]}
        self.assertEqual(observed(), {self.edit})
        action["delivery"]["subject"] = "W3"
        self.assertFalse(observed())

    def test_untyped_exit_hint_cannot_establish_a_git_head(self):
        observed = {"head": self.edit, "observed_utc": "2026-01-01T00:00:00Z", "source": "stopped_worker_readback",
                    "dirty": False, "status_hex": ""}
        self.assertEqual(checkout_observation(observed), observed)
        rename = dict(observed, dirty=True, status_hex=b"R  new name\x00old name\x00".hex())
        self.assertEqual(checkout_observation(rename), rename)
        for changed in ({"dirty": None}, {"status_hex": "bad"}, {"dirty": True}, {"source": "stopped"},
                        {"dirty": True, "status_hex": "ff"}, {"dirty": True, "status_hex": b"R  new\x00".hex()}):
            with self.assertRaises(ValueError):
                checkout_observation(dict(observed, **changed))

    def test_bare_and_worker_evidence_after_staged_copy_loss(self):
        for entry in self.entries.values():
            (self.stage / entry["pack"]["path"]).unlink()
        self.assets = Artifacts(FileArchive(self.stage))
        self.catalog = self.reader("fresh")
        seals = self.seals()
        self.assertEqual({s["candidate"] for s in seals}, {START, self.edit})
        self.assertFalse(self.issues)
        self.candidates.archive.rename(self.directory / "original-bare")
        for seal in seals:
            (self.stage / seal["pack"]["path"]).unlink()
        self.assets = Artifacts(FileArchive(self.stage))
        self.catalog = self.reader("worker-only")
        self.assertEqual([s["candidate"] for s in self.seals()], [self.edit])

    def test_broken_map_cannot_hide_independent_seals_and_replaced_bytes_are_preserved(self):
        path = self.stage / self.entries[self.edit]["pack"]["path"]
        damaged = b"damaged pack"
        path.write_bytes(damaged)
        quarantine = preserve_damaged_objects(self.assets)
        self.assertEqual(self.assets.archive.read(quarantine[self.entries[self.edit]["pack"]["path"]]["path"]), damaged)
        self.assertEqual(preserve_damaged_objects(self.assets), quarantine)
        seals = self.seals()
        members, _, _ = reconcile(self.catalog, {START: None, self.edit: []}, seals, set(), self.issues.append)
        self.assertEqual(members, {START, self.edit})
        self.assertEqual(sum("malformed candidate entry" in s for s in self.issues), 2)

    def test_no_candidate_is_enrolled_from_the_registered_fixture_alone(self):
        self.assertEqual(reconcile(self.catalog, {}, [], set(), self.issues.append), (set(), {}, {}))
        self.assertEqual(self.issues, [])
