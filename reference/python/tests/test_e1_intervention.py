"""Private, off-study controls for the registered E1 trigger and immutable candidate identity."""

import io
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from experiments.e1 import scorer
from experiments.e1.arms import Plan
from experiments.e1.artifacts import Artifacts, Candidates, REGISTRATION, START, git, read_artifact
from experiments.e1.executor import ORDER, PATHS
from experiments.e1.intervention import validate_fault, verify_fault_candidate
from experiments.e1.run import Driver, Journal


class PrivateCase:
    def __init__(self, directory, assets, seed):
        self.seed, self.rank = seed, seed - 9001
        directory.mkdir()
        candidates = Candidates(directory, assets)
        journal = Journal("loop")
        driver = Driver(candidates, Plan(directory), journal, assets, seed, "primary")
        for work in ORDER:
            self.previous = candidates.head
            driver.one(work, [])
            if driver.used_fault:
                break
        self.rows = journal.rows
        self.position = next(i for i, row in enumerate(self.rows) if row["kind"] == "fault")
        self.fault = self.rows[self.position]["payload"]
        self.oid = self.fault["candidate"]
        self.entries = candidates.entries
        self.repo = directory / "restored"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "--bare", "--object-format=sha1")
        pack = read_artifact(assets.archive, self.entries[self.oid]["pack"])
        git(self.repo, "index-pack", "--stdin", data=pack)

    def check(self, *, rows=None, replay=False):
        verify_fault_candidate(self.repo, self.entries, self.rows if rows is None else rows,
                               self.seed, self.rank, replay_check=replay)

    def replacement(self, directory, mutate, *, parent=None):
        git(directory, "clone", "-q", "--shared", "--no-checkout", "--", str(self.repo), "replacement")
        root = Path(directory) / "replacement"
        git(root, "checkout", "-q", "--detach", self.oid)
        mutate(root)
        git(root, "add", "-f", "--all", "--", ".")
        tree = git(root, "write-tree").decode().strip()
        parent = self.previous if parent is None else parent
        oid = git(root, "commit-tree", tree, "-p", parent, data=b"Independent negative control\n").decode().strip()
        entries = {**self.entries, oid: {"start": START, "tree": tree, "parent": parent}}
        rows = deepcopy(self.rows)
        rows[self.position]["payload"]["candidate"] = oid
        submit = next(r["payload"] for r in rows[self.position + 1:] if r["kind"] == "action")
        submit["candidate"] = submit["input"]["candidate"] = oid
        # The substituted pack stands on its own. All original public labels could still be identical.
        repo = Path(directory) / "restored"
        repo.mkdir()
        git(repo, "init", "-q", "--bare", "--object-format=sha1")
        pack = git(root, "pack-objects", "--stdout", "--revs", data=(oid + "\n").encode())
        git(repo, "index-pack", "--stdin", data=pack)
        target = self.fault["target"]
        verdict = scorer.measure(repo, oid, root, self.seed, self.rank, labels=(target,))[target]["verdict"]
        return repo, entries, rows, verdict


class InterventionIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        archive = zipfile.ZipFile(io.BytesIO(), "w")
        cls.addClassCleanup(archive.close)
        assets = Artifacts(archive)
        cls.cases = {s: PrivateCase(Path(temporary.name) / str(s), assets, s) for s in (9002, 9003, 9004, 9007, 9010)}

    def test_all_registered_overlays_from_real_worker_actions_and_restored_packs(self):
        expected = {9002: ("skipped_retry_check", "W2", "W2/retry"), 9003: ("outside_scope", "W1", "scope/paths"),
                    9004: ("unchanged_module", "W1", "W1/preferences"), 9007: ("unchanged_module", "W2", "W2/retry"),
                    9010: ("unchanged_module", "W3", "W3/delivery")}
        for seed, case in self.cases.items():
            with self.subTest(seed=seed):
                fault = validate_fault(case.rows, "primary", seed, case.rank)
                self.assertEqual((fault["intervention"], fault["work"], fault["target"]), expected[seed])
                with patch("experiments.e1.intervention.local_check", side_effect=AssertionError("probe during report")):
                    case.check()
                case.check(replay=True)
                path = PATHS[fault["work"]]
                original = (REGISTRATION / "input" / path).read_bytes()
                if seed == 9002:
                    expected_bytes = original.replace(b"return (0,)", b"return (0,) + tuple(base * k for k in range(1, attempts - 1))")
                    self.assertEqual(git(case.repo, "show", f"{case.oid}:{path}"), expected_bytes)
                elif seed == 9003:
                    self.assertEqual(git(case.repo, "show", f"{case.oid}:notes/seed-9003.txt"), b"out of scope\n")
                else:
                    self.assertEqual(git(case.repo, "show", f"{case.oid}:{path}"), original)
                    self.assertEqual(case.oid, case.previous)
                    self.assertEqual(case.oid == START, seed == 9004)

    def test_different_fault_bytes_rejected_even_with_the_same_failing_target(self):
        for seed, case in self.cases.items():
            with self.subTest(seed=seed), tempfile.TemporaryDirectory() as directory:
                path = PATHS[case.fault["work"]]
                def substitute(root):
                    if seed == 9002:
                        (root / path).write_bytes((REGISTRATION / "input" / path).read_bytes())
                    elif seed == 9003:
                        (root / "notes/seed-9003.txt").rename(root / "notes/seed-9004.txt")
                    else:
                        with (root / path).open("ab") as file:
                            file.write(b"\n# Still omits the required behavior.\n")
                repo, entries, rows, verdict = case.replacement(directory, substitute)
                self.assertEqual(verdict, "fail")
                with self.assertRaisesRegex(ValueError, "intervention bytes"):
                    verify_fault_candidate(repo, entries, rows, seed, case.rank)

    def test_exact_outside_bytes_file_modes_and_unchanged_commit_identity(self):
        outside = self.cases[9003]
        unchanged = self.cases[9007]
        for case, mutate, message in (
            (outside, lambda root: (root / "notes/seed-9003.txt").write_bytes(b"out of scope"), "intervention bytes"),
            (outside, lambda root: (root / "notes/seed-9003.txt").chmod(0o755), "intervention bytes"),
            (unchanged, lambda root: None, "reuse the preceding commit"),
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                repo, entries, rows, verdict = case.replacement(directory, mutate)
                self.assertEqual(verdict, "fail")
                with self.assertRaisesRegex(ValueError, message):
                    verify_fault_candidate(repo, entries, rows, case.seed, case.rank)
        different = deepcopy(outside.entries)
        different[outside.oid]["tree"] = different[START]["tree"]
        with self.assertRaisesRegex(ValueError, "retained Git object"):
            verify_fault_candidate(outside.repo, different, outside.rows, outside.seed, outside.rank)

    def test_trigger_check_assignment_and_public_claim_are_all_checked(self):
        case = self.cases[9002]
        position = case.position
        action_ids = [i for i, e in enumerate(case.rows[:position]) if e["kind"] == "action"]
        edit, check = action_ids[-2:]
        submit = next(i for i in range(position + 1, len(case.rows)) if case.rows[i]["kind"] == "action")
        for idx, field, wrong in (
            (position, "seed", 9003), (position, "intervention", "unchanged_module"),
            (position, "work", "W3"), (position, "target", "W3/delivery"),
            (position, "trigger", "second submission of W2"), (position, "released", "no claim"),
            (edit, "subject", "W3"), (edit, "return", {"failure": "unavailable"}),
            (check, "subject", "W1"), (check, "actor", "plan_reviewer"),
            (check, "return", {**case.rows[check]["payload"]["return"], "extended": []}),
            (check, "return", {**case.rows[check]["payload"]["return"], "worker_claim": "no claim"}),
            (submit, "candidate", START), (submit, "subject", "W3"),
            (submit, "input", {"candidate": case.oid, "claim": "unverified"}),
        ):
            with self.subTest(index=idx, field=field):
                altered = deepcopy(case.rows)
                altered[idx]["payload"][field] = wrong
                with self.assertRaises(ValueError):
                    validate_fault(altered, "primary", case.seed, case.rank)
        for earlier in (case.rows[submit], case.rows[check], case.rows[position]):
            with self.subTest(earlier=earlier["kind"]), self.assertRaises(ValueError):
                validate_fault([earlier, *case.rows], "primary", case.seed, case.rank)
        for rows, track in ((case.rows[:position] + case.rows[position + 1:], "primary"), (case.rows, "clean")):
            with self.assertRaises(ValueError):
                validate_fault(rows, track, case.seed, case.rank)
        with self.assertRaisesRegex(ValueError, "registered E1 seed"):
            validate_fault([], "primary", 1001, 2)
        self.assertIsNone(validate_fault(case.rows[:position], "primary", case.seed, case.rank))
        self.assertIsNone(validate_fault([], "clean", 9002, 1))

    def test_private_fault_at_a_reused_oid_needs_no_submit_or_acknowledgment(self):
        case = self.cases[9007]
        private = case.rows[:case.position + 1]
        preceding = [r["payload"] for r in private if r["kind"] == "action" and r["payload"]["action"] == "submit"]
        self.assertEqual((preceding[-1]["subject"], preceding[-1]["candidate"]), ("W3", case.oid))
        submit = next(r for r in case.rows[case.position + 1:] if r["kind"] == "action")
        for reply in (None, {}, {"return": {"failure": "acknowledgment unavailable"}}):
            with self.subTest(reply=reply):
                rows = deepcopy(private)
                if reply is not None:
                    intent = deepcopy(submit)
                    del intent["payload"]["return"]
                    intent["payload"].update(reply)
                    rows.append(intent)
                self.assertEqual(validate_fault(rows, "primary", case.seed, case.rank), case.fault)
                case.check(rows=rows)

    def test_strict_verification_reexecutes_the_actual_pre_injection_worker_check(self):
        case = self.cases[9003]
        rows = deepcopy(case.rows)
        check = next(r["payload"] for r in reversed(rows[:case.position]) if r["kind"] == "action")
        check["return"]["extended"][0]["samples"][0].append(["wrong", "inbox", 0])
        with self.assertRaisesRegex(ValueError, "worker check differs"):
            case.check(rows=rows, replay=True)
