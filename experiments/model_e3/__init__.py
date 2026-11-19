"""E3 model study over the original guarded Git effect and its independent witnesses."""

import tempfile
import time
from pathlib import Path

from experiments.e1.accounting import validity_for
from experiments.e1.report import GitPacks, Objects, require
from experiments.e1.recovery import input_identity
from experiments.e1.run import utc
from experiments.e3 import scoring, trial
from experiments.e3.fixture import read, write
from experiments.e3.report import inventory
from experiments.model_units import validate_unit

from . import turns
from .participant import Participant
from .readback import read_trace


def _validity(reason, track, facts):
    if reason == "provider_unavailable":
        return "infrastructure_invalid"
    if reason == "model_limit":
        return "arm_incomplete"
    if (track == "inconclusive-observation" and reason == "blocked" and facts["eligible"] is True
            and facts["decisions"]):
        last = facts["decisions"][-1]
        if last["original_report"] == "unknown" and last["choice"] == "pending":
            return "complete"
    return validity_for(reason)


def _facts(raw, objects, identity, *, service_sha256=None, authority_sha256=None):
    require(raw["mode"] == "model" and raw["experiment"] == "E3", "expected an E3 model case")
    platform = objects.item(raw["artifacts"]["platform"])
    trace = objects.item(raw["artifacts"]["model"])
    interrupted = raw["terminal"] in ("harness_failure", "time_limit", "step_limit")
    with tempfile.TemporaryDirectory(prefix="purser-model-e3-readback-") as folder:
        facts = scoring.reconstruct(raw, objects, GitPacks(Path(folder)))
    evidence = read_trace(identity, trace, objects, platform, service_sha256=service_sha256,
                          authority_sha256=authority_sha256, interrupted=interrupted)
    if evidence["last_model_ns"] is not None and platform.get("start_monotonic_ns") is not None:
        if platform.get("clock_stopped") is True:
            require(evidence["last_model_ns"] <= platform["end_monotonic_ns"], "model turn outside participant clock")
        facts["elapsed_lower_ns"] = max(facts["elapsed_lower_ns"] or 0,
                                        evidence["last_model_ns"] - platform["start_monotonic_ns"])
    for field, measured, minimum in (("steps", facts["steps"], facts["observed_steps"]),
                                     ("elapsed_ns", facts["elapsed_ns"], facts["elapsed_lower_ns"])):
        if measured is not None:
            evidence["cost_exact"][field] = measured
        elif minimum is not None:
            evidence["cost_lower_bounds"][field] = minimum
    return {**facts, **evidence}


def reconstruct_case(raw, archive, *, registration_sha256, population="qualification",
                     service_sha256=None, authority_sha256=None):
    """Rebuild scientific and per-field cost facts solely from original archived artifacts."""
    unit = tuple(raw[key] for key in ("experiment", "track", "seed", "arm"))
    validate_unit(unit)
    identity = turns.manifest(unit, registration_sha256, population)
    objects = Objects(archive)
    result = _facts(raw, objects, identity, service_sha256=service_sha256, authority_sha256=authority_sha256)
    inventory(raw, objects)
    platform = objects.item(raw["artifacts"]["platform"])
    trace_ref = raw["artifacts"]["model"]
    catalog = {r["path"]: r for r in platform["referenced_files"]}
    for ref in [trace_ref, *trial.files_in(objects.item(trace_ref))]:
        require(catalog.get(ref["path"]) == ref, "E3 model source absent from the platform's retained file manifest")
    reason = raw["terminal"]
    if reason == "harness_failure":
        require(bool(platform.get("failure")) and raw["validity"] == "infrastructure_invalid",
                "E3 infrastructure failure lacks the original failure observation")
    else:
        require(result["terminal"] == reason == platform["budget_reason"],
                "published E3 terminal differs from the original participant exit")
        validity = _validity(reason, raw["track"], result)
        require(raw["validity"] == validity, "published E3 validity differs from the measured terminal")
        if validity == "complete":
            require(result["eligible"] is True and result["public_complete"] and result["external_complete"]
                    and result["model_trace_complete"] and not result["diagnostics"],
                    "complete E3 case lacks the registered exposure, original history or native endpoint")
    require(raw["score"] == scoring.raw_score(raw, result), "published E3 score differs from its sources")
    for key in ("model_calls", *turns.USAGE):
        exact = result["cost_exact"].get(key)
        expected = result["cost_lower_bounds"].get(key, 0) if key == "model_calls" and exact is None else exact
        require(raw["cost"][key] == expected, f"published {key} differs from the original E3 turn receipts")
    for field in ("steps", "elapsed_ns"):
        if field in result["cost_exact"]:
            require(raw["cost"][field] == result["cost_exact"][field], f"published {field} differs from original E3 clock/calls")
    return result


def _finish(directory, assets, raw, trace, identity, *, recovered=False):
    raw["mode"] = "model"
    raw["artifacts"]["model"] = assets.put(trace, "json")
    objects = Objects(assets.archive)
    platform = objects.item(raw["artifacts"]["platform"])
    refs = [*platform.get("referenced_files", []), raw["artifacts"]["model"], *trial.files_in(trace)]
    platform["referenced_files"] = sorted({r["path"]: r for r in refs}.values(), key=lambda r: r["path"])
    input_platform = raw["artifacts"]["platform"] = assets.put(platform, "json")
    timing = {"input_platform": input_platform, "start_ns": time.monotonic_ns(), "start_utc": utc(),
              "end_ns": None, "end_utc": None}
    clock = directory / ("model-e3-recovery-score.json" if recovered else "model-e3-score.json")
    write(clock, timing)
    try:
        facts = _facts(raw, objects, identity)
        raw["validity"] = _validity(raw["terminal"], raw["track"], facts)
        raw["score"] = scoring.raw_score(raw, facts)
        exact, lower = facts["cost_exact"], facts["cost_lower_bounds"]
        raw["cost"].update({field: exact.get(field) for field in turns.USAGE},
                           model_calls=exact.get("model_calls", lower.get("model_calls", 0)))
    finally:
        timing.update(end_ns=time.monotonic_ns(), end_utc=utc())
        write(clock, timing)
    factored = {**platform, "offline_scoring": timing,
                "referenced_files": [*platform["referenced_files"], input_platform]}
    raw["artifacts"]["platform"] = assets.put(factored, "json")
    write(directory / "model-e3-result.json", raw)
    return raw


def run_case(directory, assets, freeze, arm, track, seed, rank, *, decide, read_record,
             registration_sha256, population="qualification", progress=None):
    """Run one original case. The caller binds decide to this unit, registration and successive ordinals."""
    trial.identity(arm, track, seed, rank)
    directory = Path(directory).resolve()
    identity = turns.manifest(("E3", track, seed, arm), registration_sha256, population)
    session = turns.Turns(directory, assets, identity, decide, read_record)

    def checkpoint(saved):
        write(directory / "model-e3-progress.json", saved)
        if progress is not None:
            progress(saved)

    raw = trial._run_participant(directory, assets, freeze, arm, track, seed, rank, progress=checkpoint,
                                 mode="model", participant_factory=lambda tools: Participant(tools, session))
    write(directory / "model-e3-native-result.json", raw)
    trace = turns.retain(directory, assets, identity)
    return _finish(directory, assets, raw, trace, identity)


def recover_case(directory, assets, freeze, arm, track, seed, rank, *, registration_sha256, population="qualification",
                 progress=None, recovered_records=()):
    """Read a previously started case without authorizing a model call, writer or participant."""
    trial.identity(arm, track, seed, rank)
    directory = Path(directory).resolve()
    identity = turns.manifest(("E3", track, seed, arm), registration_sha256, population)
    for name in ("model-e3-result.json", "model-e3-native-result.json"):
        candidate = directory / name
        if not candidate.exists():
            continue
        original = read(candidate)
        require(original["inputs"] == input_identity(freeze, arm)
                and [original[k] for k in ("experiment", "track", "seed", "arm", "replicate")] ==
                ["E3", track, seed, arm, freeze.get("replicate", 1)],
                "sealed E3 result belongs to another input or case")
        if name == "model-e3-result.json":
            reconstruct_case(original, assets.archive, registration_sha256=registration_sha256, population=population)
            return original
        trace = turns.retain(directory, assets, identity, recovered=recovered_records)
        return _finish(directory, assets, original, trace, identity, recovered=True)
    path = directory / "model-e3-progress.json"
    saved = trial.recover(directory, assets, arm, progress or (read(path) if path.is_file() else {}),
                          seed=seed, rank=rank)
    trace = turns.retain(directory, assets, identity, recovered=recovered_records)
    if saved.get("end_monotonic_ns") is None and saved.get("start_monotonic_ns") is not None:
        for entry in trace["turns"]:
            at = entry.get("returned_ns", entry["issued_ns"])
            wall = entry.get("returned_utc", entry["issued_utc"])
            if at > (saved.get("last_monotonic_ns") or 0):
                saved.update(last_monotonic_ns=at, last_utc=wall)
    raw = trial.failure(assets, freeze, arm, track, seed, rank, reason="interrupted E3 model case", progress=saved)
    return _finish(directory, assets, raw, trace, identity, recovered=True)
