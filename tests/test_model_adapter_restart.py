import base64
from multiprocessing import get_context
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from experiments.e1.artifacts import encoded, sha
from experiments.model_budget import ModelBudget, ModelTurn
from purser.adapters.model import LocalModelAdapter, ModelIncomplete, verify_model_record
from purser.adapters.model_evidence import request_identity
from purser.driver import tick
from purser.kernel.artifacts import Artifacts
from purser.kernel.store import Store
from purser.kernel.workview import WorkView
from tests import test_driver
from tests.test_model_adapter import (BINDING, CannedService, CHILD, MODEL, PROMPT, REGISTRATION, SOURCE, UNIT,
                                     candidate_builder, enroll, send, turn)


def interrupted_driver(db, root, endpoint, authority, cut, reached):
    # Cuts are outside the actual transport/capture. The operating system kills
    # this worker while the original socket service and release authority survive.
    budget = ModelBudget(root / 'budget.db', authority)
    adapter = LocalModelAdapter(endpoint, budget, root / 'evidence')
    put = Artifacts.put
    def pause(store, raw):
        if raw.startswith(b'{"authority_sha256":'):
            if cut == 'original':
                reached.set()
                time.sleep(30)
            digest = put(store, raw)
            if cut == 'content':
                reached.set()
                time.sleep(30)
            return digest
        return put(store, raw)
    with patch.object(Artifacts, 'put', pause):
        execute = adapter.executor(**BINDING, candidate_for=candidate_builder(db, []),
                                   **turn(deadline=time.monotonic() + 20))
        tick(Store(db), {'executor:first': execute})


class ModelRecoveryTests(unittest.TestCase):
    def exercise_cut(self, cut):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CannedService()
            self.addCleanup(service.close)
            release_server = Event()
            if cut == 'wire':
                def hold(h, q):
                    if release_server.wait(15):
                        service.reply(h, q)
                service.server.respond = hold
            driver = test_driver.ForegroundDriver()
            driver.setUp()
            self.addCleanup(driver.doCleanups)
            endpoint = service.endpoint
            budget = enroll(endpoint, root)
            identity = ModelTurn(REGISTRATION, UNIT, 1, request_identity(endpoint.profile(), PROMPT.request(MODEL, 1500)))
            initial = driver.store.snapshot()['revision']
            mp = get_context('spawn')
            reached = mp.Event()
            worker = mp.Process(target=interrupted_driver, args=(driver.fixture.path, root, endpoint,
                                                                  budget.authority, cut, reached))
            worker.start()
            try:
                barrier = service.server.seen if cut == 'wire' else reached
                self.assertTrue(barrier.wait(7), (worker.exitcode, cut))
                worker.kill()
                worker.join(5)
                self.assertIsNotNone(worker.exitcode)
                self.assertNotEqual(worker.exitcode, 0)
            finally:
                release_server.set()
                if worker.is_alive():
                    worker.kill()
                    worker.join(5)
            self.assertEqual(len(service.server.records), 1)
            self.assertIsNone(budget.inspect(identity)['result_sha256'])
            self.assertEqual(driver.store.snapshot()['revision'], initial + 1)
            adapter = LocalModelAdapter(endpoint, ModelBudget(budget.path, budget.authority), root / 'evidence')
            captured = []
            callback = adapter.executor(**BINDING, candidate_for=candidate_builder(driver.fixture.path, captured), **turn())
            if cut == 'wire':
                with self.assertRaises(ModelIncomplete) as error:
                    tick(driver.store, {'executor:first': callback})
                self.assertEqual(error.exception.code, 'unresolved_release')
                self.assertFalse(captured)
                self.assertIsNone(WorkView(driver.store.replay()['projection']).fields(CHILD)['candidate'])
                self.assertEqual(driver.store.snapshot()['revision'], initial + 2)
                row = budget.inspect(identity)
                self.assertEqual((row['result_sha256'], row['input_tokens'], row['output_tokens']), (None, None, None))
                self.assertEqual((row['input_limit'], row['output_limit']), (8000, 1500))
            else:
                original, = (root / 'evidence').glob('turn-*')
                raw = original.read_bytes()
                digest = sha(raw)
                record = verify_model_record(raw, expected_sha256=digest)
                self.assertEqual((adapter.objects.directory / digest).exists(), cut == 'content')
                self.assertEqual(tick(driver.store, {'executor:first': callback})['receipt']['disposition'], 'admitted')
                self.assertEqual(captured[0].result_sha256, digest)
                self.assertEqual(captured[0].arguments['source'], SOURCE)
                row = budget.inspect(identity)
                self.assertEqual((row['result_sha256'], row['input_tokens'], row['output_tokens']),
                                 (digest, record['usage']['input_tokens'], record['usage']['output_tokens']))
                cid = WorkView(driver.store.replay()['projection']).fields(CHILD)['candidate']['digest']
                self.assertEqual(Artifacts(driver.fixture.path).read(cid), SOURCE.encode())
            self.assertEqual(len(service.server.records), 1)

    def test_worker_lost_after_native_request_before_response(self):
        self.exercise_cut('wire')

    def test_worker_lost_after_durable_original_before_addressed_copy(self):
        self.exercise_cut('original')

    def test_worker_lost_after_addressed_copy_before_settlement(self):
        self.exercise_cut('content')

    def test_predecessor_orphan_and_missing_addressed_copy_recover_without_io(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            service = CannedService()
            self.addCleanup(service.close)
            endpoint = service.endpoint
            fresh = LocalModelAdapter(endpoint, enroll(endpoint, root), root / 'new')
            decision = fresh.decide(**turn())
            record = fresh.read_record(decision.result_sha256)
            for field in ('response_complete', 'json_content', 'identity_encoding'):
                del record['transport'][field]
            # Reconstruct the v1 capture cut with the exact original HTTP request
            # and reply: released SQLite row and addressed result, no turn link.
            archived = encoded(record)
            old_root = root / 'old'
            old_root.mkdir()
            budget = enroll(endpoint, old_root)
            old = LocalModelAdapter(endpoint, budget, old_root / 'evidence')
            old.result_dir.mkdir()
            identity = ModelTurn(REGISTRATION, UNIT, 1, decision.request_sha256)
            budget.reserve(identity, input_limit=endpoint.context_window, output_limit=1500)
            old_digest = old.objects.put(archived)
            self.assertFalse(list(old.result_dir.glob('turn-*')))
            self.assertIsNone(budget.inspect(identity)['result_sha256'])
            got = old.decide(**turn())
            self.assertEqual(got.result_sha256, old_digest)
            self.assertEqual(got.arguments['source'], SOURCE)
            self.assertEqual(budget.inspect(identity)['input_tokens'], record['usage']['input_tokens'])
            # A settled new-layout result retains the deterministic original if a
            # copy is missing. Recover its content-addressed name from that source.
            (fresh.objects.directory / decision.result_sha256).unlink()
            self.assertEqual(fresh.decide(**turn()), decision)
            self.assertEqual(sha(fresh.read_artifact(decision.result_sha256)), decision.result_sha256)
            self.assertEqual(len(service.server.records), 1)


class ModelHTTPFailureEvidence(unittest.TestCase):
    def test_full_error_partial_diagnostic_and_non_json_body(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            service = CannedService()
            self.addCleanup(service.close)
            adapter = LocalModelAdapter(service.endpoint, enroll(service.endpoint, root), root / 'evidence')
            diagnostic = b'{"error":{"code":"context_limit"},"usage":{"prompt_tokens":17,"completion_tokens":0}}'
            def truncated(handler, _):
                handler.send_response(400)
                handler.send_header('Content-Type', 'application/json')
                handler.send_header('Content-Length', str(len(diagnostic) + 5))
                handler.end_headers()
                handler.wfile.write(diagnostic)
            cases = [(lambda h, _: send(h, diagnostic, 400), 'http_status', diagnostic, True, 17, 0),
                     (truncated, 'invalid_transport', diagnostic, False, None, None),
                     (lambda h, _: send(h, b'incomplete-json {', 503), 'http_status', b'incomplete-json {', True, None, None)]
            for index, (reply, code, observed, complete, incoming, outgoing) in enumerate(cases):
                service.server.respond = reply
                args = turn(registration_sha256=sha(str(index).encode()))
                seals = []
                for _ in range(2):
                    with self.assertRaises(ModelIncomplete) as error:
                        adapter.decide(**args)
                    self.assertEqual(error.exception.code, code)
                    self.assertEqual((error.exception.input_tokens, error.exception.output_tokens), (incoming, outgoing))
                    seals.append(error.exception.result_sha256)
                self.assertEqual(*seals)
                record = adapter.read_record(seals[0])
                self.assertIsNone(record['proposal'])
                tr = record['transport']
                self.assertEqual((tr['response_complete'], tr['response_sha256']), (complete, sha(observed)))
                self.assertEqual(base64.b64decode(tr['response_base64']), observed)
                self.assertEqual(len(service.server.records), index + 1)
