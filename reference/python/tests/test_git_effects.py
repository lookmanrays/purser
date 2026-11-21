"""Real protected refs, independent native readback, and broker/child loss."""
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments.e1.artifacts import Artifacts, Candidates, START, git
from experiments.e1.recovery import FileArchive
from purser.effects.git import GitError, GitRepository
from purser.kernel.wire import digest

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "refs/heads/purser-demo"


def identity(value):
    return {"kind": "git.commit", "algorithm": "sha1", "digest": value}


def authorization(intent, attempt):
    def admit(preparation):
        assert preparation["intent_digest"] == digest("effect", intent)
        details = {"effect": intent["effect"], "attempt": attempt, "intent_digest": preparation["intent_digest"],
                   "state": "dispatching"}
        return {"disposition": "admitted", "details": details}, True
    return admit


def worker(operation, path, *extra):
    binding, intent, attempt = json.loads(Path(path).read_text())
    repo = GitRepository(binding)
    if operation == "observe":
        print(json.dumps(repo.observe(intent, attempt)), flush=True)
        return

    def wait_for_kill():
        print("cut", flush=True)
        signal.pause()

    with repo.broker() as broker:
        if operation in ("after_authorization", "after_write"):
            broker.dispatch(intent, attempt, authorization(intent, attempt), **{operation: wait_for_kill})
            return
        # The harness controls only the actual writer's input stream. The
        # executable, cwd pin, environment and inherited lock come from dispatch.
        inherited = int(extra[0])
        run = subprocess.run

        def hold_native_stdin(cmd, **kwargs):
            if "update-ref" not in cmd:
                return run(cmd, **kwargs)
            print(json.dumps(kwargs["input"].decode()), flush=True)
            child = subprocess.Popen(cmd, stdin=inherited, stdout=sys.stdout, stderr=sys.stderr,
                                     pass_fds=kwargs["pass_fds"], env=kwargs["env"])
            child.wait()
            return subprocess.CompletedProcess(cmd, child.returncode, b"", b"")

        with patch("purser.effects._git_store.subprocess.run", hold_native_stdin):
            broker.dispatch(intent, attempt, authorization(intent, attempt))


class GitEffects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        fixture = Candidates(Path(tmp.name), Artifacts(FileArchive(Path(tmp.name))))
        cls.stage = fixture.work / ".git"
        replacements = {"preferences": "channel not in disabled", "retry": "tuple(k * base for k in range(attempts))",
                        "delivery": "tuple(dict.fromkeys(event_ids))"}
        for name, expression in replacements.items():
            path = fixture.work / "notify" / (name + ".py")
            prefix, _ = path.read_bytes().rsplit(b"    return ", 1)
            path.write_bytes(prefix + b"    return " + expression.encode() + b"\n")
        cls.target = fixture.commit("Prepare notification policy v1")
        (fixture.work / "unrelated.txt").write_text("later\n")
        cls.later = fixture.commit("Later legitimate advance")
        # A net tree diff alone misses the intermediate outside path. A rename
        # adds both its source and destination to the authorized publication.
        (fixture.work / "unrelated.txt").rename(fixture.work / "renamed.txt")
        (fixture.work / "transient.txt").write_text("published in history\n")
        fixture.commit("Rename and expose intermediate path")
        (fixture.work / "transient.txt").unlink()
        cls.multi = fixture.commit("Revert intermediate path")
        tree = git(fixture.work, "rev-parse", cls.target + "^{tree}").strip()
        cls.foreign = git(fixture.work, "commit-tree", tree, data=b"disconnected\n").decode().strip()
        cls.blob = git(fixture.work, "rev-parse", START + ":README.md").decode().strip()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name).resolve()
        self.path = self.directory / "canonical.git"
        self.repo = GitRepository.initialize(self.path, "R-notify", BRANCH, self.stage, START,
                                             candidates=(self.target, self.later, self.multi, self.foreign))
        self.intent = {"repository": "R-notify", "commitment": "C-notify", "work": "W2", "assignment": "AS-B",
                       "generation": 2, "slot": "publish", "effect": "F-publish", "grant": "G-publish",
                       "capability": "git.commit", "resource": BRANCH, "expected_old": identity(START),
                       "target": identity(self.target), "purpose": "Publish the verified notification policy."}
        self.checksum = digest("effect", self.intent)
        self.receipt = "refs/purser/receipts/" + self.checksum

    def run_git(self, *args, data=None):
        return git(self.path, *args, data=data).decode().strip()

    def cold(self, attempt="T1"):
        path = self.directory / "input.json"
        path.write_text(json.dumps([self.repo.binding, self.intent, attempt]))
        return [sys.executable, "-m", "tests.test_git_effects"], str(path)

    def observe(self, attempt="T1"):
        command, path = self.cold(attempt)
        return json.loads(subprocess.check_output([*command, "observe", path], cwd=ROOT, timeout=15))

    def publish(self, attempt="T1", **options):
        with self.repo.broker() as broker:
            return broker.dispatch(self.intent, attempt, authorization(self.intent, attempt), **options)

    def test_frozen_candidate_touch_set_real_transition_later_tip_and_original_attribution(self):
        initial = self.observe()
        self.assertEqual((initial["state"], initial["receipt_target"], initial["matching_transitions"]),
                         ("confirmed_absent", None, 0))
        with self.repo.broker() as broker:
            self.assertEqual(broker.prepare(self.intent), {"repository_instance": self.repo.binding["repository_instance"],
                             "intent_digest": self.checksum, "changed_paths": [
                                 "notify/delivery.py", "notify/preferences.py", "notify/retry.py"]})
        self.publish()
        # Exercise the Git transaction independently too. A conflicting repeat
        # cannot update either ref, including when issued with a fresh transport.
        repeat = f"start\nupdate {BRANCH} {self.target} {START}\ncreate {self.receipt} {self.target}\nprepare\ncommit\n"
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_git("update-ref", "--stdin", data=repeat.encode())
        self.run_git("update-ref", "-m", "legitimate later advance", BRANCH, self.later, self.target)
        facts = self.observe()
        self.assertEqual((facts["state"], facts["reason"], facts["branch_tip"], facts["receipt_target"],
                          facts["matching_transitions"], facts["writer_quiescent"], facts["history_intact"]),
                         ("confirmed_happened", "protected_receipt_verified", self.later, self.target, 1, True, True))
        self.assertEqual(self.run_git("reflog", "show", "--format=%H", BRANCH).splitlines(),
                         [self.later, self.target, START])
        self.assertEqual(self.observe("T2")["reason"], "receipt_attempt_mismatch")
        self.assertEqual(self.run_git("reflog", "show", "--format=%gs", self.receipt),
                         "purser.effect " + self.checksum + " T1")

    def test_paths_objects_binding_and_used_lease(self):
        with self.repo.broker() as broker:
            multi = dict(self.intent, expected_old=identity(self.target), target=identity(self.multi))
            with self.assertRaisesRegex(GitError, "branch_precondition_changed"):
                broker.prepare(multi)
            actual = broker.prepare(dict(multi, expected_old=identity(START)))["changed_paths"]
            self.assertEqual(actual, ["notify/delivery.py", "notify/preferences.py", "notify/retry.py",
                                      "renamed.txt", "transient.txt", "unrelated.txt"])
            mutations = ({"resource": "refs/heads/different"}, {"repository": "R-other"},
                         {"target": identity(self.blob)}, {"target": identity(self.foreign)},
                         {"target": identity(START)}, {"target": identity("f" * 40)})
            for change in mutations:
                with self.subTest(change=change), self.assertRaises(GitError):
                    broker.prepare(dict(self.intent, **change))
        with self.assertRaisesRegex(GitError, "broker_lease_ended"):
            broker.prepare(self.intent)
        with self.assertRaisesRegex(GitError, "repository_already_exists"):
            GitRepository.initialize(self.path, "R-notify", BRANCH, self.stage, START)
        self.assertEqual(self.run_git("show-ref", "--verify", "--hash", BRANCH), START)

    def test_prepare_is_bounded_by_real_merge_reachability(self):
        stage = self.directory / "merge-stage"
        git(self.directory, "clone", "-q", "--no-local", str(self.stage), str(stage))
        git(stage, "checkout", "-q", "-b", "left", self.target)
        (stage / "left").write_text("left\n")
        git(stage, "add", "left")
        git(stage, "commit", "-qm", "left")
        git(stage, "checkout", "-q", "-b", "right", self.target)
        (stage / "right").write_text("right\n")
        git(stage, "add", "right")
        git(stage, "commit", "-qm", "right")
        (stage / "right").unlink()
        git(stage, "add", "-u")
        git(stage, "commit", "-qm", "consume right")
        git(stage, "checkout", "-q", "left")
        git(stage, "merge", "-qm", "join", "right")
        merge = git(stage, "rev-parse", "HEAD").decode().strip()
        repo = GitRepository.initialize(self.directory / "merge.git", "R-notify", BRANCH, stage / ".git",
                                        self.target, candidates=[merge])
        with repo.broker() as broker:
            facts = broker.prepare(dict(self.intent, expected_old=identity(self.target), target=identity(merge)))
            self.assertEqual(facts["changed_paths"], ["left", "right"])

    def test_sha256_repository_and_a_real_empty_tree_delta(self):
        source = self.directory / "sha256-source"
        source.mkdir()
        git(source, "init", "-q", "--object-format=sha256", "--template=")
        git(source, "commit", "-qm", "old", "--allow-empty")
        old = git(source, "rev-parse", "HEAD").decode().strip()
        git(source, "commit", "-qm", "next empty commit", "--allow-empty")
        new = git(source, "rev-parse", "HEAD").decode().strip()
        repo = GitRepository.initialize(self.directory / "sha256.git", "R-notify", BRANCH,
                                        source / ".git", old, candidates=[new])
        intent = dict(self.intent, expected_old=dict(identity(old), algorithm="sha256"),
                      target=dict(identity(new), algorithm="sha256"))
        with repo.broker() as broker:
            self.assertEqual(broker.prepare(intent)["changed_paths"], [])
            broker.dispatch(intent, "T1", authorization(intent, "T1"))
        facts = repo.observe(intent, "T1")
        self.assertEqual((len(new), facts["state"], facts["receipt_target"]), (64, "confirmed_happened", new))

    def test_damaged_receipt_is_pending_and_readback_changes_no_repository_bytes(self):
        def files():
            return {p.relative_to(self.path): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in self.path.rglob("*") if p.is_file()}
        self.publish()
        valid = files()
        self.assertEqual(self.observe()["state"], "confirmed_happened")
        self.assertEqual(files(), valid)
        self.run_git("symbolic-ref", self.receipt, "refs/heads/missing")
        damaged = files()
        facts = self.observe()
        self.assertEqual((facts["state"], facts["reason"]), ("pending", "symbolic_git_ref"))
        self.assertEqual(files(), damaged)

    def test_gate_cutoff_is_fresh_and_exact_and_stale_cas_cannot_apply(self):
        admitted, _ = authorization(self.intent, "T1")({"intent_digest": self.checksum})
        with self.repo.broker() as broker:
            self.assertEqual(broker.dispatch(self.intent, "T1", lambda _: (admitted, False)), (admitted, False))
            with self.assertRaisesRegex(GitError, "authorization_mismatch"):
                broker.dispatch(self.intent, "T2", lambda _: (admitted, True))
        self.assertEqual(self.run_git("reflog", "show", "--format=%H", BRANCH), START)
        with self.assertRaisesRegex(GitError, "git_command_failed"):
            self.publish(after_authorization=lambda: self.run_git("update-ref", "-m", "other",
                                                                  BRANCH, self.later, START))
        facts = self.observe()
        self.assertEqual((facts["state"], facts["reason"], facts["receipt_target"], facts["matching_transitions"]),
                         ("pending", "branch_precondition_changed", None, 0))

    def test_receipt_removal_rewrite_and_plausible_tip_do_not_establish_an_outcome(self):
        self.publish()
        self.run_git("update-ref", "-m", "alter receipt", self.receipt, START, self.target)
        self.assertEqual(self.observe()["reason"], "receipt_target_mismatch")
        self.run_git("update-ref", "-m", "restore value", self.receipt, self.target, START)
        self.assertEqual(self.observe()["reason"], "receipt_history_inconsistent")
        self.run_git("update-ref", "-d", self.receipt)
        self.assertEqual(self.observe()["reason"], "receipt_history_inconsistent")
        self.run_git("update-ref", "-m", "rewind", BRANCH, START, self.target)
        facts = self.observe()
        self.assertEqual((facts["state"], facts["branch_tip"], facts["receipt_target"], facts["history_intact"]),
                         ("pending", START, None, False))

    def test_substituted_resource_and_unfinished_git_transaction(self):
        original = self.directory / "moved.git"
        self.path.rename(original)
        GitRepository.initialize(self.path, "R-notify", BRANCH, self.stage, START, candidates=(self.target,))
        self.assertEqual(self.observe()["reason"], "repository_instance_mismatch")
        new = self.directory / "new.git"
        self.path.rename(new)
        original.rename(self.path)
        # Cut after qualification and replace the pathname itself. The actual
        # child must update the directory descriptor retained by the broker.
        def swap_paths():
            self.path.rename(original)
            new.rename(self.path)
        self.publish(after_authorization=swap_paths)
        self.assertEqual(self.run_git("show-ref", "--verify", "--hash", BRANCH), START)
        self.path.rename(new)
        original.rename(self.path)
        self.assertEqual(self.observe()["state"], "confirmed_happened")
        (self.path / "reftable" / "tables.list.lock").touch()
        self.assertEqual(self.observe()["reason"], "native_transaction_unfinished")

    def test_process_kills_before_and_after_write_and_reauthorization(self):
        for point, attempt, expect in (("after_authorization", "T1", "confirmed_absent"),
                                       ("after_write", "T2", "confirmed_happened")):
            command, path = self.cold(attempt)
            with subprocess.Popen([*command, point, path], cwd=ROOT, stdout=subprocess.PIPE) as process:
                try:
                    self.assertTrue(select.select([process.stdout], [], [], 15)[0])
                    self.assertEqual(process.stdout.readline().strip(), b"cut")
                    self.assertEqual(self.observe(attempt)["reason"], "writer_may_still_apply")
                finally:
                    process.kill()
                    process.wait(timeout=5)
            self.assertEqual(self.observe(attempt)["state"], expect)
        self.assertEqual(self.run_git("reflog", "show", "--format=%H", BRANCH).splitlines(), [self.target, START])

    def test_surviving_native_writer_owns_fence_after_broker_sigkill(self):
        command, path = self.cold()
        reader, writer = os.pipe()
        with os.fdopen(writer, "wb", buffering=0) as controller:
            process = subprocess.Popen([*command, "native_input", path, str(reader)], cwd=ROOT,
                                       stdout=subprocess.PIPE, pass_fds=(reader,))
            os.close(reader)
            try:
                self.assertTrue(select.select([process.stdout], [], [], 15)[0])
                transaction = json.loads(process.stdout.readline())
                prefix, commit = transaction.rsplit("commit\n", 1)
                self.assertEqual(commit, "")
                controller.write(prefix.encode())
                self.assertEqual(process.stdout.readline().strip(), b"start: ok")
                self.assertEqual(process.stdout.readline().strip(), b"prepare: ok")
                process.kill()
                process.wait(timeout=5)
                facts = self.observe()
                self.assertEqual((facts["state"], facts["writer_quiescent"]), ("pending", False))
                controller.write(b"commit\n")
                controller.close()
                self.assertEqual(process.stdout.read().strip(), b"commit: ok")
            finally:
                process.kill()
                process.wait(timeout=5)
                process.stdout.close()
        self.assertEqual(self.observe()["state"], "confirmed_happened")


if __name__ == "__main__":
    worker(*sys.argv[1:])
