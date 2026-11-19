"""Serial once-only model collection and read-only recovery of a started original."""

import fcntl
import os
from contextlib import ExitStack
from pathlib import Path
import time

from experiments.e1.artifacts import MANIFEST, Artifacts, encoded, sha
from experiments.e1.recovery import FileArchive, sync_directory, write_atomic
from experiments.e1.report import Objects, parse, require
from experiments.e1.run import utc
from experiments.e1.measurement import MeasurementUnavailable
from experiments.e2.outcomes import FIELDS as E2_FIELDS

from .registration import load_registration, schedule

E1_FIELDS = ("false_parent_closure", "missing_named", "caught", "correct_parent_closure", "extra_diagnoses")


def case_path(directory, unit):
    experiment, track, seed, arm = unit
    return directory / "cases" / experiment / track / str(seed) / arm


def case_identity(unit, freeze, wire):
    return {"registration_sha256": sha(wire), "unit": list(unit), "replicate": freeze["replicate"],
            "population": freeze["population"]}


def e3_args(freeze, identity):
    return {"registration_sha256": identity["registration_sha256"],
            "population": "original" if freeze["population"] == "model" else "qualification"}


def coding_score(raw, facts):
    if raw["track"] != "primary" or raw["validity"] == "infrastructure_invalid" or facts.get("eligible") is not True:
        return None
    keys = E1_FIELDS if raw["experiment"] == "E1" else E2_FIELDS
    return {k: facts[k] for k in keys} if all(facts.get(k) is not None for k in keys) else None


def coding_validity(terminal, track, facts):
    if terminal in ("harness_failure", "provider_unavailable"):
        return "infrastructure_invalid"
    if terminal != "done" or track != "clean" and facts.get("eligible") is not True:
        return "arm_incomplete"
    return "complete"


def acquire_observer(freeze, runtime_directory, stack):
    if runtime_directory is None:
        return None, None
    from experiments.model_isolation import Observer, Snapshot
    from experiments.wasi_runtime import WasiRuntime
    runtime = stack.enter_context(WasiRuntime(runtime_directory))
    # The deployment publishes the observer qualification digest in the registration.
    if freeze["conditions"]["observer"].get("status") != "qualified":
        raise ValueError("registered observer is unavailable")
    return lambda checkpoint: Observer(runtime, checkpoint=checkpoint), Snapshot.from_git


def collect(directory, *, units=None, runtime_directory=None):
    from . import session as transport
    directory = Path(directory)
    freeze, wire = load_registration(directory, collecting=True)
    all_units = list(schedule())
    selected = list(units) if units is not None else all_units
    require(selected == [u for u in all_units if u in set(selected)], "invalid, repeated or out-of-order units")
    if freeze["population"] == "model" and freeze["replicate"] == 1:
        require(selected == all_units, "original replicate requires the complete prospective schedule")
    endpoint, budget = transport.service(freeze)
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        started = [u for u in all_units if case_path(directory, u).is_dir()]
        new = [u for u in selected if u not in started]
        if started and new:
            require(all_units.index(new[0]) > all_units.index(started[-1]), "new case precedes a previously released slot")
        with ExitStack() as stack:
            observe = snapshot = None
            if any(u[0] != "E3" for u in selected) and runtime_directory is not None:
                try:
                    observe, snapshot = acquire_observer(freeze, runtime_directory, stack)
                except (ImportError, OSError, RuntimeError, ValueError) as error:
                    observer_error = f"{type(error).__name__}: {error}"
                else:
                    observer_error = None
            else:
                observer_error = "isolated candidate observer unavailable"
            for unit in selected:
                collect_one(directory, unit, freeze, wire, endpoint, budget, observe, snapshot, observer_error)
    finally:
        os.close(fd)


def collect_one(directory, unit, freeze, wire, endpoint, budget, observer, snapshot, observer_error):
    from . import readback, session as transport, trial
    experiment, track, seed, arm = unit
    rank = MANIFEST["experiments"][experiment]["seeds"].index(seed) + 1
    case = case_path(directory, unit)
    identity = case_identity(unit, freeze, wire)
    case.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = case.exists()
    case.mkdir(mode=0o700, exist_ok=True)
    sync_directory(case.parent)
    marker = case / "started.json"
    if marker.exists():
        require(parse(marker.read_bytes())["identity"] == identity, "case marker belongs to another registration")
    else:
        write_atomic(marker, encoded({"identity": identity, "started_utc": utc(),
                                     "monotonic_ns": time.monotonic_ns(), "recovered_directory": previous}))
    if (case / "raw.json").is_file():
        return
    archive = FileArchive(case)
    assets = Artifacts(archive)
    session = transport.Session(case, assets, freeze, identity, endpoint, budget)
    working, saved = case / "trial", case / "progress.json"
    working.mkdir(mode=0o700, exist_ok=True)
    checkpoint = lambda data: write_atomic(saved, encoded(data))
    params = (working, assets, freeze, arm, track, seed, rank)
    staged = case / "observed.json"
    if staged.is_file():
        raw = parse(staged.read_bytes())
    else:
        if experiment == "E3":
            from experiments import model_e3
            opts = e3_args(freeze, identity)
            if previous:
                receipts = {t["ordinal"]: assets.archive.read(t["record"]["path"])
                            for t in session.retain()["turns"] if "record" in t}
                raw = model_e3.recover_case(*params, **opts, progress=parse(saved.read_bytes()) if saved.exists() else {},
                                            recovered_records=receipts)
            else:
                raw = model_e3.run_case(*params, **opts, decide=session.decide, read_record=session.adapter.read_artifact,
                                        progress=checkpoint)
        elif previous:
            raw = trial.recover_case(*params, experiment=experiment, progress=parse(saved.read_bytes()) if saved.exists() else {})
        else:
            raw = trial.run_case(*params, experiment=experiment, decide=session.decide, progress=checkpoint,
                                 observer=observer, snapshot_from_git=snapshot, observer_error=observer_error)
        write_atomic(staged, encoded(raw))
    trace = session.retain()
    trace_ref = assets.put(trace, "json")
    write_atomic(case / "session.json", encoded(trace_ref))
    records, exact, lower, _ = transport.read_session(trace, archive, freeze, identity)
    if experiment != "E3":
        raw["artifacts"]["model"] = trace_ref
        try:
            objects = Objects(archive)
            platform = objects.item(raw["artifacts"]["platform"])
            platform["referenced_files"] = readback.referenced_files(raw["artifacts"], platform, objects)
            raw["artifacts"]["platform"] = assets.put(platform, "json")
            facts = readback.reconstruct_case(raw, archive, records, registration_sha256=identity["registration_sha256"])
        except (OSError, ValueError, KeyError, TypeError, MeasurementUnavailable) as error:
            # Keep the sealed observation and all surviving objects for offline audit.
            write_atomic(case / "dispute.json", encoded({"error": f"{type(error).__name__}: {error}"}))
            raw.update(validity="infrastructure_invalid", score=None)
        else:
            raw["score"] = coding_score(raw, facts)
            raw["validity"] = coding_validity(raw["terminal"], track, facts)
        raw["cost"].update({key: exact.get(key) for key in transport.USAGE})
        raw["cost"]["model_calls"] = exact.get("model_calls", lower.get("model_calls", 0))
    # The raw row is the last publication. Re-entry after publication cannot re-expose the case.
    write_atomic(case / "raw.json", encoded(raw))
