import base64
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest

from docs.evidence.M2.prepare_continuity import C, CHILD
from experiments.e1.artifacts import encoded, sha
from experiments.model_budget import ModelBudget, ModelTurn, SpendingAuthority
from purser.adapters.model import (ActionSpec, LocalEndpoint, LocalModelAdapter, ModelIncomplete,
                                   ModelPrompt, OperandSpec, verify_model_record)
from purser.adapters.model_http import MAX_RESPONSE_BYTES
from purser.driver import tick
from purser.kernel.artifacts import Artifacts
from purser.kernel.workview import WorkView
from tests import test_driver


REGISTRATION = '9' * 64
UNIT = ('E1', 'primary', 1001, 'purser')
MODEL = 'canned-synthetic-v1'
RUNTIME = sha(b'Purser canned fixture: UTF-8 byte tokens over BOS, role, content, EOT, assistant prefix')
SOURCE = 'def retry_delays(attempts, base):\n    return tuple(k * base for k in range(attempts))\n'
PROMPT = ModelPrompt('Edit the assigned policy in the synthetic notification fixture.',
                     'The public work requests linear retry offsets.',
                     {'edit': ActionSpec('Supply the complete policy source.', {
                         'path': OperandSpec(choices=('notify/retry.py',)), 'source': OperandSpec(max_bytes=4096)})})
BINDING = dict(commitment=C, work=CHILD, assignment='AS-first', generation=1)


def typed_reply(source=SOURCE):
    return {'action': 'edit', 'arguments': {'path': 'notify/retry.py', 'source': source}}


def body_for(request, proposal=None):
    content = encoded(typed_reply() if proposal is None else proposal).decode()
    rendered = ('<BOS>' + ''.join(f"<{m['role']}>{m['content']}<EOT>" for m in request['messages'])
                + '<assistant>')
    return {'model': request['model'], 'choices': [
        {'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}],
        'usage': {'prompt_tokens': len(rendered.encode()), 'completion_tokens': len(content.encode())}}


def send(handler, raw, status=200, *, headers=None):
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(raw)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(raw)


class CannedHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        self.server.records.append((self.path, dict(self.headers), raw))
        self.server.seen.set()
        try:
            self.server.respond(self, json.loads(raw))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


class CannedService:
    def __init__(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), CannedHandler)
        self.server.records = []
        self.server.seen = Event()
        self.window = 8000
        self.source = SOURCE
        self.server.respond = self.reply
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()

    def reply(self, handler, request):
        body = body_for(request, typed_reply(self.source))
        usage = body['usage']
        if usage['prompt_tokens'] > self.window or usage['completion_tokens'] > request['max_tokens']:
            send(handler, b'{}', 400)
        else:
            send(handler, encoded(body))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    @property
    def endpoint(self):
        return LocalEndpoint(self.server.server_port, MODEL, RUNTIME, self.window,
                             provider='Purser canned fixture', provider_version='1', model_revision='synthetic-v1')


def enroll(endpoint, root):
    authority = SpendingAuthority('a' * 64, endpoint.service_sha256, 0, 0, 0)
    return ModelBudget.enroll(root / 'budget.db', authority)


def turn(**changes):
    return dict(registration_sha256=REGISTRATION, unit=UNIT, ordinal=1, prompt=PROMPT,
                output_limit=1500, deadline=time.monotonic() + 5) | changes


def candidate_builder(db_path, captured):
    def build(decision):
        captured.append(decision)
        # The consumer owns the one registered location. Model text never runs as host code.
        if decision.action != 'edit' or decision.arguments['path'] != 'notify/retry.py':
            raise ValueError('wrong policy')
        target = db_path.parent / 'private-retry.py'
        target.write_bytes(decision.arguments['source'].encode())
        return {'kind': 'artifact.bytes', 'algorithm': 'sha256', 'digest': Artifacts(db_path).put(target.read_bytes())}
    return build


class ModelAdapterTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.service = CannedService()
        self.addCleanup(self.service.close)
        self.budget = enroll(self.service.endpoint, self.root)
        self.adapter = LocalModelAdapter(self.service.endpoint, self.budget, self.root / 'evidence')

    def driver(self):
        driver = test_driver.ForegroundDriver()
        driver.setUp()
        self.addCleanup(driver.doCleanups)
        return driver

    def failure(self, code, **options):
        with self.assertRaises(ModelIncomplete) as cm:
            self.adapter.decide(**turn(**options))
        self.assertEqual(cm.exception.code, code)
        return cm.exception

    def test_native_http_driver_causality_and_offline_evidence(self):
        hashes = []
        for number, source in enumerate((SOURCE, '# different source chosen by the canned participant\n')):
            driver = self.driver()
            captured = []
            self.service.source = source
            registry = str(number + 1) * 64
            executor = self.adapter.executor(**BINDING, candidate_for=candidate_builder(driver.fixture.path, captured),
                                             **turn(registration_sha256=registry))
            result = tick(driver.store, {'executor:first': executor})
            self.assertEqual(result['receipt']['disposition'], 'admitted')
            candidate = WorkView(driver.store.replay()['projection']).fields(CHILD)['candidate']
            self.assertEqual(Artifacts(driver.fixture.path).read(candidate['digest']), source.encode())
            self.assertEqual((driver.fixture.path.parent / 'private-retry.py').read_text(), source)
            hashes.append(candidate['digest'])
            decision, = captured
            path, headers, raw = self.service.server.records[-1]
            self.assertEqual(path, '/v1/chat/completions')
            self.assertEqual(raw, encoded(PROMPT.request(MODEL, 1500)))
            self.assertNotIn(b'Exercise a fresh driver', raw)
            self.assertNotIn(b'W-sibling', raw)
            self.assertNotIn('Authorization', headers)
            self.assertEqual(decision.arguments['source'], source)
            record = verify_model_record(self.adapter.read_artifact(decision.result_sha256),
                                         expected_sha256=decision.result_sha256)
            self.assertEqual(record['request'], json.loads(raw))
            self.assertEqual(record['turn']['registration_sha256'], registry)
            self.assertEqual((record['service']['provider_version'], record['service']['model_revision']), ('1', 'synthetic-v1'))
            self.assertEqual(record['transport']['wire_state'], 'sent')
            response = json.loads(base64.b64decode(record['transport']['response_base64']))
            self.assertEqual(response['usage']['prompt_tokens'], decision.input_tokens)
            self.assertEqual(response['usage']['completion_tokens'], decision.output_tokens)
            self.assertEqual(decision.spend_usd_micros, 0)
            self.assertEqual(record['limits']['input_tokens'], self.service.window)
            self.assertEqual(tick(driver.store, {'executor:first': executor})['state'], 'idle')
            # A newly constructed adapter recovers this original result despite an elapsed case clock.
            cold = LocalModelAdapter(self.service.endpoint, ModelBudget(self.budget.path, self.budget.authority),
                                     self.adapter.result_dir)
            self.assertEqual(cold.decide(**turn(registration_sha256=registry, deadline=time.monotonic()-1)), decision)
        self.assertNotEqual(*hashes)
        self.assertEqual(len(self.service.server.records), 2)

    def test_disabled_idle_replacement_and_wrong_assignment(self):
        driver = self.driver()
        absent = self.root / 'disabled'
        disabled = LocalModelAdapter(None, None, absent)
        callback = candidate_builder(driver.fixture.path, [])
        self.assertEqual(tick(driver.store)['state'], 'idle')
        for adapter, generation, code in ((disabled, 1, 'disabled'), (self.adapter, 9, 'wrong_assignment')):
            executor = adapter.executor(**(BINDING | {'generation': generation}), candidate_for=callback, **turn())
            with self.assertRaises(ModelIncomplete) as failure:
                tick(driver.store, {'executor:first': executor})
            self.assertEqual(failure.exception.code, code)
        self.assertFalse(absent.exists())
        self.assertFalse(self.adapter.result_dir.exists())
        self.assertEqual(self.service.server.records, [])
        self.assertIsNone(WorkView(driver.store.replay()['projection']).fields(CHILD)['candidate'])
        driver.assign('AS-replacement', 'executor:replacement')
        self.assertEqual(tick(driver.store, {'executor:replacement': lambda _: driver.candidate})['receipt']['disposition'],
                         'admitted')
        self.assertEqual(self.service.server.records, [])
        cold = subprocess.run([sys.executable, '-B', '-c',
                               "import sys, purser.driver; print('purser.adapters.model' in sys.modules)"],
                              capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(cold.stdout.strip(), 'False')

    def test_unavailable_listener_is_visible_and_replay_keeps_unknowns(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as vacant:
            vacant.bind(('127.0.0.1', 0))
            port = vacant.getsockname()[1]
        endpoint = replace(self.service.endpoint, port=port)
        alternate = self.root / 'vacant'
        alternate.mkdir()
        self.adapter = LocalModelAdapter(endpoint, enroll(endpoint, alternate), alternate / 'evidence')
        original = self.failure('transport_unavailable')
        duplicate = self.failure('transport_unavailable')
        self.assertEqual(original.result_sha256, duplicate.result_sha256)
        record = self.adapter.read_record(original.result_sha256)
        self.assertEqual(record['transport']['wire_state'], 'not_sent')
        self.assertIsNone(record['proposal'])
        self.assertEqual(record['usage'], {'input_tokens': None, 'output_tokens': None, 'spend_usd_micros': None})

    def test_redirect_and_process_proxy_never_select_another_destination(self):
        trap = CannedService()
        self.addCleanup(trap.close)
        trap_url = f'http://127.0.0.1:{trap.server.server_port}'
        self.service.server.respond = lambda h, _: send(h, b'{}', 307, headers={'Location': trap_url + '/other'})
        code = """
from pathlib import Path
import sys
from tests.test_model_adapter import *
root=Path(sys.argv[1]); endpoint=LocalEndpoint(int(sys.argv[2]), MODEL, RUNTIME, 8000,
    provider='Purser canned fixture', provider_version='1', model_revision='synthetic-v1')
adapter=LocalModelAdapter(endpoint, enroll(endpoint,root), root/'evidence')
try: adapter.decide(**turn())
except ModelIncomplete as error: print(error.code)
"""
        env = {'http_proxy': trap_url, 'HTTP_PROXY': trap_url, 'ALL_PROXY': trap_url, 'NO_PROXY': ''}
        process = subprocess.run([sys.executable, '-B', '-c', code, str(self.root), str(self.service.server.server_port)],
                                 env=env, timeout=10, capture_output=True, text=True, check=True)
        self.assertEqual(process.stdout.strip(), 'http_status')
        self.assertEqual(len(self.service.server.records), 1)
        self.assertEqual(trap.server.records, [])
        refused = self.failure('http_status')
        self.assertEqual(self.adapter.read_record(refused.result_sha256)['transport']['http_status'], 307)

    def test_invalid_operand_model_and_usage_keep_their_original_evidence(self):
        cases = [
            ('invalid_proposal', lambda r: r['choices'][0]['message'].update(content=encoded(
                {'action': 'edit', 'arguments': {'path': '../unassigned.py', 'source': 'benign'}}).decode())),
            ('invalid_proposal', lambda r: r['choices'][0]['message'].update(
                content='{"action":"edit","action":"edit","arguments":{}}')),
            ('invalid_proposal', lambda r: r['choices'][0]['message'].update(
                content=encoded(typed_reply('🌿' * 1100)).decode())),
            ('unexpected_model', lambda r: r.update(model='another-model')),
            ('invalid_usage', lambda r: r['usage'].update(prompt_tokens=True)),
            ('unfinished_response', lambda r: r['choices'][0].update(finish_reason='length')),
        ]
        for index, (code, modify) in enumerate(cases):
            with self.subTest(code=code, index=index):
                def reply(h, q):
                    body = body_for(q)
                    modify(body)
                    send(h, encoded(body))
                self.service.server.respond = reply
                error = self.failure(code, registration_sha256=sha(str(index).encode()))
                record = self.adapter.read_record(error.result_sha256)
                self.assertIsNone(record['proposal'])
                self.assertGreater(error.output_tokens, 0)
                self.assertEqual(error.spend_usd_micros, 0)
                if code == 'invalid_usage':
                    self.assertIsNone(error.input_tokens)
                self.assertIsNotNone(record['transport']['response_sha256'])

    def test_service_and_prepared_request_bounds_precede_io(self):
        with self.assertRaises(ValueError):
            LocalModelAdapter(replace(self.service.endpoint, model='unselected'), self.budget, self.root)
        paid = ModelBudget(self.budget.path, replace(self.budget.authority, output_price_ceiling=100))
        with self.assertRaises(ValueError):
            LocalModelAdapter(self.service.endpoint, paid, self.root)
        for bad in (0, '65535', True, 65536):
            with self.assertRaises(ValueError):
                replace(self.service.endpoint, port=bad)
        self.failure('deadline', deadline=time.monotonic()-1)
        for options in (dict(output_limit=True), dict(output_limit=12001), dict(deadline=float('inf')),
                        dict(prompt=replace(PROMPT, observation='x' * 65536))):
            with self.assertRaises(ValueError):
                self.adapter.decide(**turn(**options))
        self.assertEqual(self.service.server.records, [])
        self.assertFalse(self.adapter.result_dir.exists())

    def test_byte_framing_and_absolute_header_and_body_deadlines(self):
        def excessive(h, _):
            h.send_response(200)
            h.send_header('Content-Type', 'application/json')
            h.send_header('Content-Length', str(MAX_RESPONSE_BYTES + 1))
            h.end_headers()
        self.service.server.respond = excessive
        self.failure('invalid_transport')
        for index, slow_header in enumerate((True, False)):
            def drip(h, _):
                head = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 64\r\n'
                h.connection.sendall(head + (b'X-Note: ' if slow_header else b'\r\n'))
                for _ in range(32):
                    h.connection.sendall(b'a')
                    time.sleep(.03)
            self.service.server.respond = drip
            started = time.monotonic()
            error = self.failure('deadline', registration_sha256=sha(f'slow{index}'.encode()), deadline=started+.2)
            self.assertLess(time.monotonic() - started, 1.0)
            record = self.adapter.read_record(error.result_sha256)
            duration = (record['returned_monotonic_ns'] - record['started_monotonic_ns']) / 1e9
            self.assertGreater(duration, .1)
            self.assertLess(duration, .7)
            self.assertIsNone(error.input_tokens)

    def test_undisclosed_usage_keeps_entire_template_inclusive_envelope(self):
        self.service.window = 40000
        root = self.root / 'large'
        root.mkdir()
        budget = enroll(self.service.endpoint, root)
        self.adapter = LocalModelAdapter(self.service.endpoint, budget, root / 'evidence')
        def undisclosed(h, q):
            body = body_for(q)
            del body['usage']
            send(h, encoded(body))
        self.service.server.respond = undisclosed
        decision = self.adapter.decide(**turn())
        self.assertIsNone(decision.input_tokens)
        self.assertIsNone(decision.output_tokens)
        row = budget.inspect(ModelTurn(REGISTRATION, UNIT, 1, decision.request_sha256))
        self.assertEqual((row['input_limit'], row['input_tokens']), (40000, None))
        self.failure('budget_refused', ordinal=2)
        self.failure('budget_refused', prompt=replace(PROMPT, task='Different request for the same turn'))
        self.assertEqual(len(self.service.server.records), 1)

    def test_usage_overrun_is_retained_and_blocks_other_units(self):
        def overrun(h, q):
            body = body_for(q)
            body['usage']['prompt_tokens'] = self.service.window + 1
            send(h, encoded(body))
        self.service.server.respond = overrun
        error = self.failure('usage_overrun')
        self.assertEqual(error.input_tokens, self.service.window + 1)
        row = self.budget.inspect(ModelTurn(REGISTRATION, UNIT, 1, error.request_sha256))
        self.assertEqual(row['overrun'], 1)
        self.failure('budget_refused', unit=('E1', 'primary', 1001, 'loop'))
        self.assertEqual(len(self.service.server.records), 1)

    def test_offline_verifier_rejects_even_rehashed_false_action_cost_or_request(self):
        decision = self.adapter.decide(**turn())
        for alter in (lambda r: r['proposal']['arguments'].update(source='another text'),
                      lambda r: r['usage'].update(input_tokens=0),
                      lambda r: r['request']['messages'][1].update(content='other private prompt')):
            record = self.adapter.read_record(decision.result_sha256)
            alter(record)
            raw = encoded(record)
            with self.assertRaises(ValueError):
                verify_model_record(raw, expected_sha256=sha(raw))
        self.assertEqual(len(self.service.server.records), 1)


if __name__ == '__main__':
    unittest.main()
