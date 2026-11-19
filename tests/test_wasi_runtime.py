from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments.e1.artifacts import FIXTURE, REGISTRATION
from experiments.wasi_runtime import HOST, MAX_INPUT, MAX_SECONDS, MEMORY, RuntimeUnavailable, WasiRuntime


class GuestAvailability(unittest.TestCase):
    def test_absent_or_replaced_host_archive_never_opens_a_guest(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            runtime = WasiRuntime(directory)
            for state in ['missing', 'wrong bytes']:
                with self.subTest(state=state), self.assertRaises(RuntimeUnavailable):
                    with runtime:
                        self.fail('runtime accepted unavailable archives')
                (directory / HOST[0]).write_bytes(b'an unrelated file')
            with self.assertRaises(RuntimeUnavailable):
                runtime.run("print('example')", {})


ARCHIVES = os.environ.get('PURSER_WASI_ARCHIVES')


@unittest.skipUnless(ARCHIVES and (sys.platform, platform.machine()) == ('darwin', 'arm64'),
                     'set PURSER_WASI_ARCHIVES to the two pinned archives on arm64 macOS')
class GuestBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = WasiRuntime(ARCHIVES)
        cls.runtime.__enter__()
        cls.addClassCleanup(cls.runtime.__exit__, None, None, None)

    def test_read_only_fixture_and_absence_of_host_file_environment_and_socket(self):
        with tempfile.TemporaryDirectory() as name:
            canary = Path(name) / 'private-fixture-marker'
            canary.write_bytes(b'outside the public image')
            entry = """import json, os, sys
private = sys.stdin.readline().strip()
facts = dict(public=open('/fixture/public.txt').read(), marker=os.environ.get('PURSER_WASI_CANARY'))
for key, path, mode in [('private',private,'r'), ('modify','/fixture/public.txt','w'),
                        ('new','/fixture/new.txt','w')]:
    try:
        with open(path, mode): facts[key] = 'opened'
    except OSError: facts[key] = 'denied'
try:
    import socket
    with socket.socket(): facts['socket'] = 'opened'
except (ImportError, OSError): facts['socket'] = 'denied'
print(json.dumps(facts))
"""
            with patch.dict(os.environ, PURSER_WASI_CANARY='outside'):
                result = self.runtime.run(entry, {'public.txt': b'hello'}, str(canary).encode())
            self.assertEqual(result.state, 'completed', result)
            self.assertEqual(json.loads(result.stdout), {'public': 'hello', 'marker': None, 'private': 'denied',
                                                        'modify': 'denied', 'new': 'denied', 'socket': 'denied'})
            self.assertEqual(canary.read_bytes(), b'outside the public image')

    def test_memory_fuel_process_exit_and_combined_output(self):
        entry = f"""print(len(bytearray(1024)))
try: value = bytearray({MEMORY + 1})
except MemoryError: print('allocation refused')
"""
        memory = self.runtime.run(entry, {})
        self.assertEqual((memory.state, memory.stdout), ('completed', b'1024\nallocation refused\n'))
        fuel = self.runtime.run("print('entered', flush=True)\nprint(sum(i*i for i in range(1000000)))",
                                {}, fuel=160_000_000)
        self.assertEqual((fuel.state, fuel.fuel_used, fuel.stdout, fuel.exit_code),
                         ('fuel_limit', 160_000_000, b'entered\n', None))
        failed = self.runtime.run('raise SystemExit(7)', {})
        self.assertEqual((failed.state, failed.exit_code), ('guest_exit', 7))
        output = self.runtime.run("import sys; print('abc', flush=True); print('q' * 600, file=sys.stderr)", {},
                                  output_limit=160)
        self.assertEqual(output.state, 'output_limit')
        self.assertEqual(len(output.stderr) + len(output.stdout), 160)

    def test_startup_fuel_and_subsequent_success(self):
        entry = "print('ready', flush=True)"
        for limit in (1, 30_000):
            with self.subTest(fuel=limit):
                result = self.runtime.run(entry, {}, fuel=limit)
                self.assertEqual((result.state, result.fuel_used, result.exit_code, result.stdout, result.stderr),
                                 ('fuel_limit', limit, None, b'', b''))
        fresh = self.runtime.run(entry, {})
        self.assertEqual((fresh.state, fresh.exit_code, fresh.stdout), ('completed', 0, b'ready\n'))

    def test_unresolved_host_import_is_a_configuration_failure(self):
        # Change only the temporary interpreter copy for this host configuration control.
        with WasiRuntime(ARCHIVES) as unlinked:
            (unlinked.directory / 'guest/python.wasm').write_text('(module (import "fixture" "missing" (func)))')
            with self.assertRaisesRegex(RuntimeUnavailable, 'isolated host process exited'):
                unlinked.run('pass', {})

    def test_wall_limit_retains_original_partial_output_and_next_process_runs(self):
        result = self.runtime.run("import time; print('entered', flush=True); time.sleep(25)", {}, timeout=6)
        self.assertEqual((result.state, result.stdout, result.fuel_used), ('wall_limit', b'entered\n', None))
        self.assertLess(result.elapsed_ns, 10_000_000_000)
        cold = self.runtime.run("print('separate case')", {})
        self.assertEqual((cold.state, cold.stdout), ('completed', b'separate case\n'))

    def test_rejects_unsafe_fixture_names_and_unbounded_requests_before_execution(self):
        for bad in ['/host.txt', '../outside', 'src/../outside', 'src//file', '.', '']:
            with self.subTest(name=bad), self.assertRaises(ValueError):
                self.runtime.run('pass', {bad: b''})
        for bounds in [{'timeout': MAX_SECONDS + 1}, {'timeout': float('nan')}, {'fuel': True}, {'output_limit': 0}]:
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                self.runtime.run('pass', {}, **bounds)
        with self.assertRaises(ValueError):
            self.runtime.run('pass', {}, b'a' * MAX_INPUT)

    def test_frozen_notification_service_returns_changed_candidate_values(self):
        files = {}
        for member in FIXTURE['files']:
            name = member['path']
            if name.startswith(('notify/', 'schedule/')):
                body = (REGISTRATION / 'input' / name).read_bytes()
                self.assertEqual(sha256(body).hexdigest(), member['sha256'])
                files[name] = body
        entry = """import json, sys
from datetime import datetime
sys.path.insert(0, '/fixture')
from notify.service import NotificationQueue
start = datetime(2001, 1, 1, 12)
queue = NotificationQueue(start)
count = queue.enqueue('mail', json.load(sys.stdin))
print(json.dumps([count, [d.event_id for d in queue.advance(start)]]))
"""
        expected = [[3, ['a', 'a', 'b']], [2, ['a', 'b']]]
        observed = []
        for _ in expected:
            result = self.runtime.run(entry, files, b'["a","a","b"]')
            self.assertEqual(result.state, 'completed', result)
            observed.append(json.loads(result.stdout))
            files['notify/delivery.py'] = b'def select(event_ids):\n    return tuple(dict.fromkeys(event_ids))\n'
        self.assertEqual(observed, expected)
