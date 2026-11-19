"""Run synthetic Python fixtures in a fresh, resource-bounded Wasmtime guest."""

from dataclasses import dataclass
import errno
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import tempfile
import threading
import time
import zipfile


HOST = ('wasmtime-50.0.1-py3-none-macosx_11_0_arm64.whl',
        'ff4fc58ed3cbdb919d056accd4d280be13962a542754aaefd8a9b4654efa9bd9', 8_096_649)
GUEST = ('python-3.15.2-wasi_sdk-33.zip',
         'b8a63559c56760bbdc310dedb74fd69d10bcbafae26b705bea0ad353ad256888', 15_528_008)
MEMORY = 64 * 1024 * 1024
MAX_INPUT = 2 * 1024 * 1024
MAX_OUTPUT = 256 * 1024
MAX_FUEL = 16_000_000_000
MAX_SECONDS = 20


class RuntimeUnavailable(RuntimeError):
    """The trusted archives or the host process could not supply a measurement."""


@dataclass(frozen=True)
class GuestResult:
    state: str
    stdout: bytes
    stderr: bytes
    fuel_used: int | None
    elapsed_ns: int
    exit_code: int | None = None


def _checked_archive(directory, spec):
    name, digest, size = spec
    try:
        with (directory / name).open('rb') as stream:
            data = stream.read(size + 1)
    except OSError as error:
        raise RuntimeUnavailable(f'cannot read the {name} runtime archive') from error
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeUnavailable(f'incorrect runtime archive identity for {name}')
    return zipfile.ZipFile(io.BytesIO(data))


def _mount_path(name):
    if not isinstance(name, str) or not name or len(name.encode()) > 512 or '\x00' in name:
        raise ValueError('fixture requires a short relative file name')
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or name == '.' or '..' in path.parts:
        raise ValueError('fixture path is outside its disposable image')
    return path


class WasiRuntime:
    """A context owns the verified runtime files. Each run has its own guest and mounts."""

    def __init__(self, archive_directory):
        self.archives = Path(archive_directory)
        self._temporary = None

    def __enter__(self):
        if (sys.platform, platform.machine()) != ('darwin', 'arm64'):
            raise RuntimeUnavailable('the enrolled runtime requires an arm64 macOS host')
        if self._temporary is not None:
            raise RuntimeError('the runtime context is already open')
        self._temporary = tempfile.TemporaryDirectory(prefix='purser-wasi-runtime-')
        self.directory = Path(self._temporary.name)
        try:
            for spec, folder in [(HOST, 'host'), (GUEST, 'guest')]:
                with _checked_archive(self.archives, spec) as archive:
                    for member in archive.infolist():
                        name = member.filename
                        selected = (name.startswith('wasmtime/') if folder == 'host'
                                    else name == 'python.wasm' or name.startswith('lib/'))
                        if selected and not member.is_dir():
                            dest = self.directory / folder / _mount_path(name)
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(archive.read(member))
            # The static guest has no extension modules. CPython checks this landmark at startup.
            (self.directory / 'guest/lib/python3.15/lib-dynload').mkdir(exist_ok=True)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        self._temporary.cleanup()
        self._temporary = None

    def run(self, entry: str, files: dict[str, bytes], input_data: bytes = b'', *,
            fuel: int = MAX_FUEL, timeout: float = MAX_SECONDS, output_limit: int = MAX_OUTPUT) -> GuestResult:
        """The entry reads its input on stdin and can import selected files under /fixture."""
        if self._temporary is None:
            raise RuntimeUnavailable('open the verified runtime before requesting a fixture')
        if (type(fuel) is not int or not 0 < fuel <= MAX_FUEL
                or type(timeout) not in (int, float) or not 0 < timeout <= MAX_SECONDS
                or type(output_limit) is not int or not 0 < output_limit <= MAX_OUTPUT):
            raise ValueError('invalid guest resource bounds')
        if (type(entry) is not str or type(input_data) is not bytes or len(files) > 128
                or any(type(content) is not bytes for content in files.values())):
            raise ValueError('invalid guest program, stdin or fixture image')
        entry_bytes = entry.encode('utf-8')
        if len(input_data) + len(entry_bytes) + sum(map(len, files.values())) > MAX_INPUT:
            raise ValueError('guest inputs exceed the enrolled byte limit')
        with tempfile.TemporaryDirectory(prefix='purser-wasi-case-') as case_dir:
            case = Path(case_dir)
            public = case / 'fixture'
            public.mkdir()
            for name, contents in files.items():
                dest = public / _mount_path(name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Refuse two supplied names that resolve to one file on the host volume.
                with dest.open('xb') as stream:
                    stream.write(contents)
            (case / 'entry').mkdir()
            (case / 'entry/main.py').write_bytes(entry_bytes)
            (case / 'stdin').write_bytes(input_data)
            for stream_name in ('out', 'err'):
                (case / stream_name).touch()
            cmd = [sys.executable, '-I', '-B', '-S', str(Path(__file__).resolve()),
                   'child', str(self.directory), str(case), str(fuel), str(output_limit)]
            start = time.monotonic_ns()
            try:
                process = subprocess.run(cmd, cwd=case, env={'PATH': '/usr/bin:/bin', 'TZ': 'UTC', 'LC_ALL': 'C'},
                                         stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return GuestResult('wall_limit', (case / 'out').read_bytes(), (case / 'err').read_bytes(),
                                   None, time.monotonic_ns() - start)
            elapsed = time.monotonic_ns() - start
            if process.returncode:
                raise RuntimeUnavailable(f'isolated host process exited with status {process.returncode}')
            try:
                report = json.loads(process.stdout)
                return GuestResult(report['state'], (case / 'out').read_bytes(), (case / 'err').read_bytes(),
                                   report['fuel'], elapsed, report['code'])
            except (KeyError, ValueError, TypeError) as error:
                raise RuntimeUnavailable('isolated host did not return its measurement envelope') from error


def _child(runtime, case, fuel, output_limit):
    # Imported only in the isolated host subprocess from the digest-verified wheel.
    sys.path.insert(0, str(runtime / 'host'))
    import wasmtime

    config = wasmtime.Config()
    config.consume_fuel = True
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.from_file(engine, str(runtime / 'guest/python.wasm'))
    link = wasmtime.Linker(engine)
    link.define_wasi()
    wasi = wasmtime.WasiConfig()
    wasi.argv = ['python', '-I', '-B', '-S', '/entry/main.py']
    wasi.env = [('TZ', 'UTC')]
    wasi.preopen_dir(str(runtime / 'guest/lib'), '/lib', False)
    wasi.preopen_dir(str(runtime / 'guest/lib'), '/usr/local/lib', False)
    wasi.preopen_dir(str(case / 'fixture'), '/fixture', False)
    wasi.preopen_dir(str(case / 'entry'), '/entry', False)
    wasi.stdin_file = case / 'stdin'
    overflow, write_failed = False, False
    remaining = output_limit
    lock = threading.Lock()
    with (
        (case / 'out').open('wb', buffering=0) as stdout,
        (case / 'err').open('wb', buffering=0) as stderr,
        wasmtime.Store(engine) as store,
    ):
        def append(stream, data):
            nonlocal remaining, overflow, write_failed
            with lock:
                count = min(len(data), remaining)
                try:
                    if stream.write(data[:count]) != count:
                        raise OSError('incomplete guest output write')
                except OSError:
                    write_failed = True
                    return -errno.EIO
                remaining -= count
                if count < len(data):
                    overflow = True
                    return -errno.EFBIG
                return count

        wasi.stdout_custom = lambda data: append(stdout, data)
        wasi.stderr_custom = lambda data: append(stderr, data)
        store.set_limits(memory_size=MEMORY, table_elements=100_000, instances=1, tables=1, memories=1)
        store.set_fuel(fuel)
        store.set_wasi(wasi)
        guest = link.instantiate(store, module)
        state, code = 'completed', 0
        try:
            guest.exports(store)['_start'](store)
        except wasmtime.ExitTrap as error:
            code = error.code
            state = 'guest_exit' if code else 'completed'
        except wasmtime.Trap as error:
            code = None
            state = 'fuel_limit' if error.trap_code == wasmtime.TrapCode.OUT_OF_FUEL else 'guest_trap'
        used = fuel - store.get_fuel()
    if write_failed:
        raise RuntimeUnavailable('cannot retain bounded guest output')
    if overflow:
        state = 'output_limit'
    print(json.dumps({'state': state, 'code': code, 'fuel': used}))


if __name__ == '__main__' and len(sys.argv) == 6 and sys.argv[1] == 'child':
    _child(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
