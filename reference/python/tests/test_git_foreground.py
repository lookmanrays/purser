"""Real SQLite and Git through the foreground interface and a newly started CLI."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import unittest

from purser.effects.foreground import dispatch_effect, reconcile_effect, request_effect
from purser.effects.git import GitRepository
from purser.kernel.store import Store
from purser.kernel.wire import KernelError
from tests import test_effects as authority, test_git_effects as native


def at_cut():
    print("at-cut", flush=True)
    signal.pause()


def child(mode, config):
    database, binding, proposal = json.loads(Path(config).read_bytes())
    store, repo = Store(database), GitRepository(binding)
    if mode == "after_reconciliation":
        reconcile_effect(store, repo, proposal)
        at_cut()  # The transport has not returned the already committed receipt.
    elif mode == "before_dispatch":
        at_cut()
        dispatch_effect(store, repo, proposal)
    else:
        dispatch_effect(store, repo, proposal, **{mode: at_cut})


class ForegroundGit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native.GitEffects.setUpClass()
        cls.addClassCleanup(native.GitEffects.doClassCleanups)

    def setUp(self):
        self.git = native.GitEffects()
        self.git.setUp()
        self.addCleanup(self.git.doCleanups)
        self.domain = authority.EffectAuthority()
        self.domain.setUp()
        self.addCleanup(self.domain.doCleanups)
        self.store, self.repo = self.domain.store, self.git.repo
        self.intent = self.git.intent
        paths = ["notify/delivery.py", "notify/preferences.py", "notify/retry.py"]
        self.domain.declare(slot={**authority.SLOT, "repository_instance": self.repo.binding["repository_instance"],
                                  "paths": paths})
        self.domain.issue({**authority.GRANT, "paths": paths, "expires_at": "2099-01-01T00:00:00.000000Z"})
        self.domain.h.submit("W2", "B", self.intent["target"], "executor:beta")
        self.bound = self.proposal("request", {"op": "effect.request", "id": "F-publish", "intent": self.intent})
        self.request = request_effect(self.store, self.repo, self.domain.b, self.bound)
        self.assertEqual(self.request["disposition"], "admitted")
        self.dispatch = self.proposal("dispatch", {"op": "effect.authorize", "id": "T1", "effect": "F-publish"})
        self.binding_file = self.git.directory / "binding.json"
        self.binding_file.write_text(json.dumps(self.repo.binding))

    def proposal(self, key, op):
        return authority.frame("fg-" + key, self.store.snapshot()["revision"], op)

    def observation(self, key="original"):
        return self.proposal(key, {"op": "effect.reconcile", "id": "Obs-" + key,
                                   "effect": "F-publish", "attempt": "T1"})

    def cut(self, mode, proposal, before_kill=None):
        config = self.git.directory / (mode + ".json")
        config.write_text(json.dumps([str(self.store.path), self.repo.binding, proposal]))
        with subprocess.Popen([sys.executable, "-B", "-m", "tests.test_git_foreground", mode, str(config)],
                              cwd=authority.SOURCE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                self.assertTrue(select.select([process.stdout], [], [], 15)[0], "cut not reached")
                self.assertEqual(process.stdout.readline(), b"at-cut\n")
                if before_kill:
                    before_kill()
            finally:
                process.kill()
                _, err = process.communicate(timeout=10)
        self.assertEqual((process.returncode, err), (-signal.SIGKILL, b""))

    def run_git(self):
        return self.domain.cli("run", "--git-binding", str(self.binding_file))

    def native_count(self):
        history = self.git.run_git("reflog", "show", "--format=%gs", self.intent["resource"])
        return sum(line.startswith("purser.effect ") for line in history.splitlines())

    def test_before_dispatch_leaves_intent_and_new_cli_applies_exactly_once(self):
        self.cut("before_dispatch", self.dispatch)
        self.assertEqual(self.native_count(), 0)
        self.assertEqual(self.domain.cli("status")["answers"]["q06"], [])
        self.assertEqual(self.domain.cli("run")["state"], "idle")
        self.assertTrue(self.run_git()["issued"])
        self.assertEqual(self.run_git()["observation"], "confirmed_happened")
        self.assertEqual(self.native_count(), 1)
        self.assertEqual(self.domain.cli("status")["answers"]["q06"], [])

    def test_surviving_writer_pending_then_reaped_absence_requires_a_fresh_cutoff(self):
        def observe_live():
            self.assertEqual(self.run_git()["observation"], "pending")
            self.assertEqual(self.native_count(), 0)

        self.cut("after_authorization", self.dispatch, observe_live)
        self.assertEqual(self.run_git()["observation"], "confirmed_absent")
        receipt, issued = dispatch_effect(self.store, None, self.dispatch)
        self.assertEqual((receipt["details"]["attempt"], issued), ("T1", False))
        retry = self.run_git()
        self.assertTrue(retry["issued"])
        self.assertNotEqual(retry["receipt"]["details"]["attempt"], "T1")
        self.assertEqual(self.run_git()["observation"], "confirmed_happened")
        self.assertEqual(self.native_count(), 1)

    def test_lost_git_and_reconciliation_replies_are_recoverable_offline(self):
        self.cut("after_write", self.dispatch)
        self.assertEqual(self.native_count(), 1)
        self.assertEqual(self.domain.cli("status")["answers"]["q06"],
                         [{"effect": "F-publish", "attempt": "T1", "state": "dispatching"}])
        observation = self.observation()
        self.cut("after_reconciliation", observation)
        before = self.store.snapshot()["revision"]
        self.assertEqual(reconcile_effect(self.store, None, observation)["details"]["state"], "confirmed_happened")
        self.assertFalse(dispatch_effect(self.store, None, self.dispatch)[1])
        self.assertEqual(request_effect(self.store, None, self.domain.b, self.bound), self.request)
        self.assertEqual(self.store.snapshot()["revision"], before)
        redelivery = self.proposal("redeliver", self.bound["ops"][0])
        current = request_effect(self.store, None, self.domain.b, redelivery)
        self.assertEqual(current["details"]["state"], "confirmed_happened")
        self.assertEqual(self.native_count(), 1)

    def revoke(self):
        op = {"op": "grant.revoke", "id": self.intent["grant"]}
        raw = json.dumps(self.proposal("revoke", op)).encode()
        command = [sys.executable, "-B", "-m", "purser", "--db", str(self.store.path), "propose", "-"]
        return json.loads(subprocess.check_output(command, input=raw, cwd=authority.SOURCE))["receipt"]

    def test_revocation_before_the_cutoff_blocks_the_actual_git_mutation(self):
        self.assertEqual(self.revoke()["disposition"], "admitted")
        proposal = self.proposal("after-revoke", self.dispatch["ops"][0])
        r, issued = dispatch_effect(self.store, self.repo, proposal)
        self.assertEqual((r["code"], issued, self.native_count()), ("grant_revoked", False, 0))

    def test_revocation_after_the_cutoff_retains_one_in_flight_native_write(self):
        revoke = []
        r, issued = dispatch_effect(self.store, self.repo, self.dispatch,
                                    after_authorization=lambda: revoke.append(self.revoke()))
        self.assertTrue(issued)
        self.assertLess(r["revision"], revoke[0]["revision"])
        self.assertEqual(self.run_git()["observation"], "confirmed_happened")
        self.assertEqual(self.native_count(), 1)

    def test_worker_cannot_supply_preparation_and_shared_writable_config_is_rejected(self):
        forged = {**self.domain.b, "git_preparation": {}}
        with self.assertRaises(KernelError) as exc:
            request_effect(self.store, None, forged, self.bound)
        self.assertEqual(exc.exception.code, "unauthenticated")
        os.chmod(self.binding_file, 0o666)
        cmd = [sys.executable, "-m", "purser", "--db", str(self.store.path), "run", "--git-binding", str(self.binding_file)]
        result = subprocess.run(cmd, cwd=authority.SOURCE, capture_output=True)
        self.assertEqual((result.returncode, json.loads(result.stdout)["error"]["code"]), (5, "unauthenticated"))
        self.assertEqual(self.native_count(), 0)


if __name__ == "__main__":
    child(*sys.argv[1:])
