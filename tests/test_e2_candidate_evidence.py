"""Actual Git custody across staged pack publication, progress and pooled replay."""

import tempfile
import unittest
from pathlib import Path

from experiments.e1.artifacts import START, Artifacts, Candidates
from experiments.e1.recovery import FileArchive
from experiments.e1.report import GitPacks, Objects
from experiments.e2.candidate_evidence import Catalog, observe_stage, preserve_damaged_objects, reconcile


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
