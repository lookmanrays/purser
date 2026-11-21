"""Observe a frozen notification candidate in WASI and grade its responses outside it."""

import base64
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from experiments.e1 import scorer
from experiments.e1.artifacts import FIXTURE, encoded, sha
from experiments.e1.measurement import MeasurementUnavailable, valid_legacy, valid_sample
from experiments.e1.report import parse
from experiments.wasi_runtime import GUEST, HOST, MAX_FUEL, MAX_INPUT, MAX_OUTPUT, MAX_SECONDS, MEMORY, RuntimeUnavailable

from .snapshot import Snapshot, trusted_fixture


HERE = Path(__file__).resolve().parent
QUEUE_ENTRY = HERE.parent / "e1/probe.py"
LEGACY_ENTRY = HERE / "legacy_guest.py"
PUBLIC_SUITE = HERE / "public_suite.py"


def _archive(data):
    return {"bytes": len(data), "sha256": sha(data), "base64": base64.b64encode(data).decode("ascii")}


def _read_frame(frame, limit):
    if type(frame) is not dict or frame.keys() != {"bytes", "sha256", "base64"}:
        raise MeasurementUnavailable("missing bounded stream")
    size = frame["bytes"]
    if (type(size) is not int or not 0 <= size <= limit or type(frame["base64"]) is not str
            or len(frame["base64"]) != 4 * ((size + 2) // 3)):
        raise MeasurementUnavailable("invalid bounded stream length")
    raw = base64.b64decode(frame["base64"], validate=True)
    if frame != _archive(raw):
        raise MeasurementUnavailable("bounded stream bytes or digest differ")
    return raw


def _identity(snapshot, legacy):
    if not isinstance(snapshot, Snapshot):
        raise MeasurementUnavailable("expected an immutable fixture snapshot")
    return {"protocol": "purser.m5.fixture-observation.v1",
            "candidate": snapshot.oid, "tree": snapshot.tree, "manifest_sha256": snapshot.manifest_sha256,
            "mode": "legacy" if legacy else "queue", "host_archive_sha256": HOST[1],
            "guest_archive_sha256": GUEST[1], "memory_bytes": MEMORY, "fuel_limit": MAX_FUEL,
            "output_limit": MAX_OUTPUT, "wall_limit_seconds": MAX_SECONDS}


def _completed(record):
    if record["guest_state"] != "completed" or type(record["exit_code"]) is not int or record["exit_code"] != 0:
        raise MeasurementUnavailable("isolated guest terminated: " + str(record["guest_state"]))
    if (type(record["fuel_used"]) is not int or not 0 <= record["fuel_used"] <= MAX_FUEL
            or type(record["elapsed_ns"]) is not int or not 0 < record["elapsed_ns"] <= MAX_SECONDS * 10**9):
        raise MeasurementUnavailable("completed guest lacks valid resource measurements")


def _queue_input(cases):
    def strings(value):
        return type(value) is list and all(type(item) is str for item in value)
    if type(cases) is not list or not 0 < len(cases) <= 64:
        raise MeasurementUnavailable("expected a concrete batch of queue inputs")
    for case in cases:
        if (type(case) is not dict or case.keys() != {"channel", "ids", "disabled", "attempts", "base", "times"}
                or type(case["channel"]) is not str or not strings(case["ids"]) or not strings(case["disabled"])
                or not all(type(case[key]) is int and case[key] > 0 for key in ("attempts", "base"))
                or type(case["times"]) is not list or not all(type(t) is int and t >= 0 for t in case["times"])):
            raise MeasurementUnavailable("queue input contains unsupported fields or values")
    return {"root": "/fixture", "mode": "queue", "cases": cases}


def _public_suite(originals, request):
    # The native interpreter sees only verified starting bytes. Never put a candidate
    # directory on this process's import path, even when its Git scope check passes.
    with tempfile.TemporaryDirectory(prefix="purser-m5-public-") as work:
        root = Path(work)
        for name, data in originals.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        proc = subprocess.run([sys.executable, "-I", "-B", "-S", str(PUBLIC_SUITE), str(root)],
                              cwd=root, env={"PATH": "/usr/bin:/bin", "TZ": "UTC", "LC_ALL": "C"},
                              input=encoded(request), capture_output=True, timeout=8)
        if proc.returncode:
            raise MeasurementUnavailable("trusted public suite could not interpret the observation")
        return parse(proc.stdout)


def _legacy_surface(snapshot):
    for change in snapshot.changes:
        name = change["path"]
        if (name not in FIXTURE["public_paths"] and not name.startswith("tests/")
                and name not in ("README.md", "THIRD_PARTY_LICENSES/schedule.txt")
                and re.fullmatch(r"notes/seed-[0-9]+\.txt", name) is None):
            raise MeasurementUnavailable("public Schedule tests require the registered native import surface")


class Observer:
    """A checkpoint callback can archive the start and terminal record of each probe."""

    def __init__(self, runtime, *, checkpoint=None):
        self.runtime = runtime
        self.checkpoint = checkpoint
        self.last_checkpoint = None
        self._originals = None
        self._legacy_plan = None

    def _record(self, event):
        self.last_checkpoint = event
        if self.checkpoint is not None:
            self.checkpoint(event)

    def _prepare(self, snapshot, cases, legacy):
        context = {}
        if legacy:
            if cases is not None:
                raise MeasurementUnavailable("public compatibility takes its inputs from the registered tests")
            _legacy_surface(snapshot)
            if self._originals is None:
                self._originals = trusted_fixture()
            if self._legacy_plan is None:
                self._legacy_plan = _public_suite(self._originals, None)
            request = self._legacy_plan
            entry = LEGACY_ENTRY.read_bytes()
            context["public_suite_sha256"] = sha(PUBLIC_SUITE.read_bytes())
        else:
            request = _queue_input(cases)
            entry = QUEUE_ENTRY.read_bytes()
        data = encoded(request)
        if len(data) > MAX_INPUT:
            raise MeasurementUnavailable("concrete observation inputs exceed the registered byte limit")
        context.update(entry_sha256=sha(entry), stdin=_archive(data))
        return context, entry, request, data

    def _decode(self, raw, request, legacy):
        reply = parse(raw)
        encoded(reply)  # Refuse non-finite values and unpaired surrogates at the boundary.
        if legacy:
            measured = _public_suite(self._originals, {"plan": request, "measured": reply})
            if not valid_legacy(measured):
                raise MeasurementUnavailable("incomplete external public test result")
            return measured
        if type(reply) is not dict or reply.keys() != {"measured"}:
            raise MeasurementUnavailable("invalid queue observation envelope")
        measured, cases = reply["measured"], request["cases"]
        if (type(measured) is not list or len(measured) != len(cases)
                or not all(valid_sample(row, case) for row, case in zip(measured, cases, strict=True))):
            raise MeasurementUnavailable("missing or invalid observed queue values")
        return measured

    def probe(self, snapshot, *, cases=None, legacy=False):
        receipt = {**_identity(snapshot, legacy), "invocation": uuid.uuid4().hex}
        try:
            context, entry, request, data = self._prepare(snapshot, cases, legacy)
            receipt.update(context)
            self._record({**receipt, "phase": "started"})
            result = self.runtime.run(entry.decode("utf-8"), snapshot.files, data)
            out, err = result.stdout, result.stderr
            receipt.update(guest_state=result.state, exit_code=result.exit_code, fuel_used=result.fuel_used,
                           elapsed_ns=result.elapsed_ns, stdout=_archive(out[:MAX_OUTPUT]),
                           stderr=_archive(err[:max(0, MAX_OUTPUT - len(out))]))
            if len(out) + len(err) > MAX_OUTPUT:
                raise MeasurementUnavailable("guest response exceeded the registered byte limit")
            _completed(receipt)
            measured = self._decode(out, request, legacy)
        except (RuntimeUnavailable, MeasurementUnavailable, OSError, ValueError, RecursionError,
                subprocess.SubprocessError) as error:
            reason = str(error) if isinstance(error, (MeasurementUnavailable, RuntimeUnavailable)) else type(error).__name__
            self._record({**receipt, "phase": "unavailable", "reason": reason})
            raise MeasurementUnavailable(reason) from error
        except BaseException as error:
            # A study deadline or user cancellation can interrupt the foreground process.
            self._record({**receipt, "phase": "interrupted", "reason": type(error).__name__})
            raise
        self._record({**receipt, "phase": "measured", "measured_sha256": sha(encoded(measured))})
        return measured

    def measure(self, snapshot, seed, rank, *, labels=None, offsets=scorer.linear_offsets, version=1):
        return protected_reports(snapshot, seed, rank, probe=self.probe, labels=labels, offsets=offsets, version=version)


def protected_reports(snapshot, seed, rank, *, probe, labels=None, offsets=scorer.linear_offsets, version=1):
    """Build the registered reports from the snapshot and a source of measured samples."""
    if snapshot.oid is None:
        raise MeasurementUnavailable("protected measurement requires the archived Git commit")
    selected = set(scorer.LABELS if labels is None else labels)
    if not selected.issubset(scorer.LABELS):
        raise ValueError("unknown protected criterion")
    checks = {label: [] for label in scorer.LABELS if label in selected}
    cases = [(label, case) for label, case in scorer.inputs(seed, rank, offsets=offsets) if label in selected]
    if cases:
        observations = probe(snapshot, cases=[case for _, case in cases])
        for (label, case), observed in zip(cases, observations, strict=True):
            checks[label].append({"input": case, "expected": scorer.expected(case, offsets=offsets),
                                  "observed": observed})
    if "scope/paths" in checks:
        delta = snapshot.changes
        outside = [row for row in delta if row["path"] not in FIXTURE["public_paths"]
                   or (row["old_mode"], row["new_mode"]) != ("100644", "100644")]
        checks["scope/paths"].append({"all_changes": delta, "observed": outside, "expected": []})
    if "legacy/compat" in checks:
        checks["legacy/compat"].append({"expected": {"tests": 28, "errors": [], "failures": [], "skipped": []},
                                        "observed": probe(snapshot, legacy=True)})
    reports = {}
    for label, rows in checks.items():
        report = {"candidate": snapshot.oid, "criterion": label, "version": version, "cases": rows}
        report["verdict"] = scorer.verify_report(report, snapshot.oid, label, seed, rank,
                                                 offsets=offsets, version=version)
        reports[label] = report
    return reports


def read_observation(snapshot, started, terminal, *, cases=None, legacy=False):
    """Recover the original measurement from two case-bound records, without a guest."""
    if type(started) is not dict or type(terminal) is not dict:
        raise MeasurementUnavailable("missing original observation checkpoint")
    if started.get("phase") != "started" or terminal.get("phase") != "measured":
        raise MeasurementUnavailable("original invocation has no completed measurement")
    invocation = started.get("invocation")
    if type(invocation) is not str or re.fullmatch(r"[0-9a-f]{32}", invocation) is None:
        raise MeasurementUnavailable("invalid invocation identity")
    controller = Observer(None)
    try:
        identity = _identity(snapshot, legacy)
        context, _, request, _ = controller._prepare(snapshot, cases, legacy)
        basis = {**identity, **context, "invocation": invocation}
        if started.keys() != basis.keys() | {"phase"} or terminal.keys() != basis.keys() | {
            "phase", "guest_state", "exit_code", "fuel_used", "elapsed_ns", "stdout", "stderr", "measured_sha256",
        }:
            raise MeasurementUnavailable("original checkpoint fields differ")
        for item in (started, terminal):
            if encoded({key: item[key] for key in basis}) != encoded(basis):
                raise MeasurementUnavailable("invocation, snapshot, runtime or concrete input differs")
        _completed(terminal)
        out = _read_frame(terminal["stdout"], MAX_OUTPUT)
        _read_frame(terminal["stderr"], MAX_OUTPUT - len(out))
        measured = controller._decode(out, request, legacy)
        if terminal["measured_sha256"] != sha(encoded(measured)):
            raise MeasurementUnavailable("original measured digest disagrees with bounded response")
        return measured
    except (OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError) as error:
        raise MeasurementUnavailable("cannot read the original fixture observation") from error
