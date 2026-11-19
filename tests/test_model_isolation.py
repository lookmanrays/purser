"""Controller attribution, exact frozen legacy tests, and ordinary WASI observations."""

import base64
import json
import os
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from experiments.e1 import scorer
from experiments.e1.artifacts import Artifacts, Candidates, FIXTURE, START, changes, encoded, read_artifact, sha
from experiments.e1.executor import ORDER, implement
from experiments.e1.measurement import MeasurementUnavailable
from experiments.e1.recovery import FileArchive
from experiments.e2.measure import exponential_offsets
from experiments.e3.fixture import E3, TARGET
from experiments.model_isolation import Observer, Snapshot
from experiments.wasi_runtime import GuestResult, WasiRuntime


class Fixture(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix="purser-test-model-measure-")
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.candidates = Candidates(self.root, Artifacts(FileArchive(self.root)))

    def original(self):
        return Snapshot.from_git(self.candidates.archive, START)


class FrozenImage(Fixture):
    def test_transient_and_submitted_images_bind_identical_file_bytes(self):
        baseline = self.original()
        worker = self.candidates.work
        self.assertEqual(baseline.tree, FIXTURE["tree_sha1"])
        implement(worker, "W3")
        transient = Snapshot.from_worktree(worker)
        self.assertIsNone(transient.oid)
        self.assertNotEqual(transient.tree, baseline.tree)
        self.assertFalse(any(".git" in path.split("/") for path in transient.files))
        oid = self.candidates.commit("controlled edit")
        submitted = Snapshot.from_git(self.candidates.archive, oid)
        self.assertEqual((submitted.tree, submitted.manifest_sha256), (transient.tree, transient.manifest_sha256))
        self.assertEqual(submitted.changes, changes(self.candidates.archive, oid))
        (worker / "notify/delivery.py").write_text("other bytes\n")
        submitted.files.clear()
        self.assertEqual(submitted.files, transient.files)
        with self.assertRaisesRegex(MeasurementUnavailable, "archived Git commit"):
            Observer(None).measure(transient, 1001, 1)

    def test_git_and_working_image_refuse_links_and_unrelated_roots(self):
        exterior = self.root / "outside-canary"
        exterior.write_bytes(b"outside fixture")
        link = self.candidates.work / "extra"
        link.symlink_to(exterior)
        with self.assertRaisesRegex(MeasurementUnavailable, "linked"):
            Snapshot.from_worktree(self.candidates.work)
        linked_oid = self.candidates.commit("controlled link")
        with self.assertRaisesRegex(MeasurementUnavailable, "non-regular"):
            Snapshot.from_git(self.candidates.archive, linked_oid)
        link.unlink()
        os.link(exterior, link)
        with self.assertRaisesRegex(MeasurementUnavailable, "linked"):
            Snapshot.from_worktree(self.candidates.work)
        with self.assertRaises(MeasurementUnavailable):
            Snapshot.from_worktree(self.root)
        for bad in ("HEAD", "1" * 40):
            with self.subTest(oid=bad), self.assertRaises(MeasurementUnavailable):
                Snapshot.from_git(self.candidates.archive, bad)
        self.assertEqual(exterior.read_bytes(), b"outside fixture")

    def test_changed_native_core_refuses_legacy_but_preserves_scope(self):
        path = self.candidates.work / "schedule/__init__.py"
        path.write_bytes(path.read_bytes() + b"\n# local control\n")
        oid = self.candidates.commit("changed Schedule bytes")
        image = Snapshot.from_git(self.candidates.archive, oid)
        records = []
        observer = Observer(Mock(), checkpoint=records.append)
        with self.assertRaisesRegex(MeasurementUnavailable, "registered native import surface"):
            observer.probe(image, legacy=True)
        observer.runtime.run.assert_not_called()
        self.assertEqual(records[0]["phase"], "unavailable")
        scope = observer.measure(image, 1001, 1, labels=["scope/paths"])["scope/paths"]
        self.assertEqual(scope["verdict"], "fail")
        self.assertEqual(scope["cases"][0]["all_changes"], changes(self.candidates.archive, oid))


class ControllerFrames(Fixture):
    def test_wrong_envelopes_output_shape_and_termination_never_supply_a_grade(self):
        image = self.original()
        case = scorer.inputs(1001, 1)[0][1]
        row = scorer.expected(case)
        wrong = [
            ("completed", b'{"measured":[],"measured":[]}'),
            ("completed", encoded({"measured": [row, row]})),
            ("completed", encoded({"measured": [dict(row, pending=True)]})),
            ("completed", b"short incomplete response"),
            ("completed", encoded({"measured": [], "verdict": "pass"})),
            ("wall_limit", b"partial public data"),
        ]
        for state, raw in wrong:
            with self.subTest(state=state, raw=raw):
                events = []
                runtime = Mock()
                runtime.run.return_value = GuestResult(state, raw, b"", 12, 50, 0)
                observer = Observer(runtime, checkpoint=events.append)
                with self.assertRaises(MeasurementUnavailable):
                    observer.probe(image, cases=[case])
                self.assertEqual([e["phase"] for e in events], ["started", "unavailable"])
                self.assertEqual(events[0]["invocation"], events[1]["invocation"])
                self.assertEqual(events[1]["tree"], FIXTURE["tree_sha1"])
                self.assertEqual(base64.b64decode(events[1]["stdout"]["base64"]), raw)
                self.assertEqual(events[1]["stdout"]["sha256"], sha(raw))
        runtime = Mock()
        with self.assertRaises(MeasurementUnavailable):
            Observer(runtime).probe(image, cases=[dict(case, expected=row)])
        runtime.run.assert_not_called()

    def test_legacy_rejects_guest_awarded_results_and_incomplete_public_operations(self):
        image = self.original()
        runtime = Mock()
        observer = Observer(runtime)
        for payload in ({"measured": {"tests": 28, "errors": [], "failures": [], "skipped": []}},
                        {"observations": []}, {"observations": [[{"value": None}]] * 6}):
            with self.subTest(payload=payload):
                runtime.run.return_value = GuestResult("completed", encoded(payload), b"", 15, 72, 0)
                with self.assertRaises(MeasurementUnavailable):
                    observer.probe(image, legacy=True)
                self.assertEqual(observer.last_checkpoint["phase"], "unavailable")

        def extra(entry, files, data):
            calls = json.loads(data)
            rows = [[{"value": None} for _ in group] for group in calls]
            rows[0].append({"value": None})
            return GuestResult("completed", encoded({"observations": rows}), b"", 15, 72, 0)
        runtime.run.side_effect = extra
        with self.assertRaises(MeasurementUnavailable):
            observer.probe(image, legacy=True)


ARCHIVES = os.environ.get("PURSER_WASI_ARCHIVES")


@unittest.skipUnless(ARCHIVES and (sys.platform, platform.machine()) == ("darwin", "arm64"),
                     "set PURSER_WASI_ARCHIVES for the registered arm64 macOS guest")
class ActualGuest(Fixture):
    @classmethod
    def setUpClass(cls):
        cls.runtime = WasiRuntime(ARCHIVES).__enter__()
        cls.addClassCleanup(cls.runtime.__exit__, None, None, None)

    def test_start_prepared_and_revised_reports_equal_original_benign_scorer(self):
        candidates = self.candidates
        for work in ORDER:
            implement(candidates.work, work)
        self.assertEqual(candidates.commit(E3["prepared_candidate"]["commit_message"]), TARGET)
        references = []
        def checkpoint(event):
            references.append(candidates.artifacts.put(event, "json"))
        observer = Observer(self.runtime, checkpoint=checkpoint)
        for oid, seed, options in (
            (START, 1001, {}),
            (TARGET, 1001, {}),
            (TARGET, 2001, {"labels": ["W2/retry", "queue/composition"], "offsets": exponential_offsets, "version": 2}),
        ):
            with self.subTest(oid=oid, seed=seed):
                snap = Snapshot.from_git(candidates.archive, oid)
                observed = observer.measure(snap, seed, 1, **options)
                # Native parity is explicitly limited to these two known byte-pinned fixtures.
                with candidates.export(oid) as root:
                    native = scorer.measure(candidates.archive, oid, Path(root), seed, 1, **options)
                self.assertEqual(observed, native)
        events = [json.loads(read_artifact(candidates.artifacts.archive, ref)) for ref in references]
        complete = [e for e in events if e["phase"] == "measured"]
        self.assertEqual(len(complete), 5)
        self.assertTrue(all(e["guest_state"] == "completed" and e["elapsed_ns"] > 0 for e in complete))
        for event in complete:
            args = json.loads(base64.b64decode(event["stdin"]["base64"]))
            stdout = base64.b64decode(event["stdout"]["base64"])
            self.assertEqual((sha(stdout), len(stdout)), (event["stdout"]["sha256"], event["stdout"]["bytes"]))
            if event["mode"] == "legacy":
                self.assertEqual((len(args), sum(map(len, args))), (6, 22))
            else:
                self.assertEqual(args.keys(), {"mode", "root", "cases"})
                self.assertTrue(all("expected" not in item for item in args["cases"]))

    def test_writable_policy_changes_original_queue_assertions_and_candidate_tests_cannot_replace_them(self):
        worker = self.candidates.work
        (worker / "notify/preferences.py").write_bytes(b"def allowed(channel, disabled):\n    return False\n")
        (worker / "tests/test_service.py").write_bytes(b"# unrelated candidate test text\n")
        candidate = Snapshot.from_worktree(worker)
        observer = Observer(self.runtime)
        legacy = observer.probe(candidate, legacy=True)
        self.assertEqual((legacy["tests"], len(legacy["failures"]), legacy["errors"], legacy["skipped"]), (28, 3, [], []))
        self.assertEqual(observer.last_checkpoint["tree"], candidate.tree)
        self.assertIsNone(observer.last_checkpoint["candidate"])
        core = self.candidates.work / "notify/preferences.py"
        core.write_bytes(b"print('public demonstration output')\ndef allowed(channel, disabled):\n    return True\n")
        with self.assertRaises(MeasurementUnavailable):
            observer.probe(Snapshot.from_worktree(worker), legacy=True)
        self.assertIn(b"public demonstration output", base64.b64decode(observer.last_checkpoint["stdout"]["base64"]))

    def test_import_failure_comes_from_original_suite_and_closed_runtime_is_unavailable(self):
        (self.candidates.work / "notify/preferences.py").write_bytes(b"raise ValueError('controlled import result')\n")
        candidate = Snapshot.from_worktree(self.candidates.work)
        observer = Observer(self.runtime)
        case = scorer.inputs(1001, 1)[0][1]
        self.assertEqual(observer.probe(candidate, cases=[case]), [{"candidate_exception": "ValueError", "stage": "import"}])
        legacy = observer.probe(candidate, legacy=True)
        self.assertEqual((legacy["tests"], len(legacy["errors"]), legacy["failures"]), (23, 1, []))
        observer = Observer(WasiRuntime(ARCHIVES))
        with self.assertRaisesRegex(MeasurementUnavailable, "open the verified runtime"):
            observer.probe(candidate, cases=[case])
        self.assertEqual(observer.last_checkpoint["phase"], "unavailable")
