"""Run one E3 participant and archive its caller, native and writer sources before scoring."""

import json
import platform
import sqlite3
import sys
import time
from copy import deepcopy

from experiments.e1.accounting import validity_for
from experiments.e1.artifacts import MANIFEST, git, read_artifact
from experiments.e1.recovery import failure_record, input_identity
from experiments.e1.run import _run_arm, utc

from . import treatment
from .actions import Actions, Tools
from .fixture import E3, TARGET, WORK, provision, read, recover_candidates, write
from .participant import Participant
from .supervisor import Supervisor
from .witness import native, writer_history


def identity(arm, track, seed, rank):
    if (arm not in MANIFEST["units"]["arm_order"] or type(rank) is not int or not 1 <= rank <= len(E3["seeds"])
            or E3["seeds"][rank - 1] != seed or track not in ("primary", "clean", E3["stress"]["id"])
            or track == "clean" and seed != E3["clean_seed"]
            or track == E3["stress"]["id"] and seed != E3["stress"]["seed"]):
        raise ValueError("trial identity is outside the registered E3 schedule")


def files_in(value):
    if isinstance(value, dict):
        if value.keys() == {"path", "bytes", "sha256"}:
            yield value
        else:
            for child in value.values():
                yield from files_in(child)
    elif isinstance(value, list):
        for child in value:
            yield from files_in(child)


def retain(directory, assets, arm, saved, *, interrupted):
    """Only original on-disk observations can fill a journal gap after the collector exits."""
    facts = saved.setdefault("platform", {})
    rows = saved.setdefault("rows", [])
    refs = []
    archive = {"actions": [], "writers": {}, "declarations": [], "kernel": None}
    facts["e3"] = archive
    issues = facts.setdefault("evidence_issues", [])

    def issue(label, error):
        issues.append(f"{label}: {type(error).__name__}: {error}")

    def load(path):
        return read(path) if path.is_file() else None

    def store(data):
        return assets.put(data, "json") if data is not None else None

    def recovered(kind, data, at):
        start = saved.get("start_monotonic_ns")
        if start is None:
            return
        end = saved.get("end_monotonic_ns")
        if end is not None and at > end:
            raise ValueError("public source postdates the observed participant exit")
        rows.append({"kind": kind, "payload": data, "offset_ns": at - start, "seq": len(rows) + 1})

    def witnessed_until(at, wall):
        last = saved.get("last_monotonic_ns")
        if saved.get("end_monotonic_ns") is None and (last is None or last < at):
            saved.update(last_monotonic_ns=at, last_utc=wall)

    packet = load(directory / "task.json")
    if packet is not None:
        archive.update(intent=packet["intent"], receipt_ref=packet["receipt_ref"], task=store(packet))
    archive["binding"] = load(directory / "binding.json")
    for name in ("initial", "final", "teardown"):
        archive[name + "_native"] = store(load(directory / "native" / (name + ".json")))
    archive["native_snapshots"] = {p.stem: store(read(p)) for p in sorted((directory / "native").glob("after-*.json"))}

    archive["observations"] = {p.stem.removeprefix("observation-"): store(read(p))
                               for p in sorted((directory / "native").glob("observation-*.json"))}

    release = load(directory / "release-clock.json")
    if release:
        saved.update(start_monotonic_ns=release["at_ns"], start_utc=release["utc"])
    end = load(directory / "end-clock.json")
    if end:
        saved.update(end_monotonic_ns=end["at_ns"], end_utc=end["utc"])
    exit_source = load(directory / "participant-exit.json")
    facts["participant_exit"] = exit_source
    archive["release_clock"], archive["end_clock"], archive["exit"] = store(release), store(end), store(exit_source)

    by_action = {row["payload"]["action_id"]: row["payload"] for row in rows if row["kind"] == "action"}
    for path in sorted((directory / "calls").glob("*-request.json")):
        req = read(path)
        response = load(path.with_name(path.name.replace("-request.", "-response.")))
        pair = {"request": store(req), "response": store(response)}
        archive["actions"].append(pair)
        source = {**req, "request": pair["request"]}
        witnessed_until(req["issued_monotonic_ns"], req["issued_utc"])
        if response:
            source.update(response, response=pair["response"])
            witnessed_until(response["finished_monotonic_ns"], response["finished_utc"])
        existing = by_action.get(req["action_id"])
        if existing is None:
            if interrupted:
                recovered("action", source, req["issued_monotonic_ns"])
            else:
                raise ValueError("public release absent from the live journal")
        else:
            if any(existing[k] != v for k, v in source.items() if k in existing):
                issues.append("original caller release or response disagrees with journal: " + req["action_id"])
            for key, value in source.items():
                existing.setdefault(key, value)

    known_states = {row["payload"].get("action_id") for row in rows if row["kind"] == "public_state"}
    declarations = sorted((read(p) for p in (directory / "declarations").glob("*.json")),
                          key=lambda d: d["published_ns"])
    for declaration in declarations:
        witnessed_until(declaration["published_ns"], declaration["published_utc"])
        ref = store(declaration)
        archive["declarations"].append(ref)
        if declaration["action_id"] not in known_states:
            if interrupted:
                recovered("public_state", {"subject": WORK, "candidate": TARGET, "from": declaration["previous"],
                          "to": declaration["choice"], "original_report": declaration["original_report"],
                          "diagnosis": [], "action_id": declaration["action_id"], "declaration": ref},
                          declaration["published_ns"])
            else:
                raise ValueError("published decision absent from the live journal")

    for folder in sorted((directory / "writers").glob("*")):
        if folder.is_dir():
            stderr = folder / "stderr"
            proof = {"command": load(folder / "command.json"), "events": writer_history(folder),
                     "stderr": stderr.read_text(errors="replace") if stderr.is_file() else None}
            archive["writers"][folder.name] = store(proof)
    if arm == "purser" and (directory / "kernel.sqlite").is_file():
        try:
            trace = treatment.evidence(directory, assets)
            archive["kernel"] = store(trace)
            refs.extend(files_in(trace))
        except Exception as exc:
            issue("retained kernel", exc)
    if interrupted:
        try:
            saved["candidates"] = recover_candidates(directory, assets, saved.get("candidates", {}))
        except Exception as exc:
            issue("original candidate store", exc)
        if archive["binding"] and archive.get("receipt_ref") and not archive["final_native"]:
            try:
                observed = native(directory, archive["binding"], archive["receipt_ref"], "recovery")
                archive["recovery_native"] = store(observed)
            except Exception as exc:
                issue("post-interruption native read", exc)
        rows.sort(key=lambda row: (row["offset_ns"], row["seq"]))
        for seq, row in enumerate(rows, 1):
            row["seq"] = seq
    facts["public_history_complete"] = bool(exit_source and all(p["response"] for p in archive["actions"]) and not issues)
    scoring = load(directory / "score-clock.json")
    if scoring is not None:
        facts["offline_scoring"] = scoring
        refs.extend(files_in(scoring))
    refs.extend(files_in([archive, rows, saved.get("candidates", {})]))
    facts["referenced_files"] = sorted({ref["path"]: ref for ref in refs}.values(), key=lambda ref: ref["path"])
    return saved


def recover(directory, assets, arm, progress, *, seed, rank):
    return retain(directory, assets, arm, deepcopy(progress or {}), interrupted=True)


def failure(assets, freeze, arm, track, seed, rank, *, reason, progress=None):
    return {**failure_record(assets, freeze, arm, track, seed, rank, reason=reason, progress=progress),
            "experiment": "E3"}


def run_participant(directory, assets, freeze, arm, track, seed, rank, *, progress=None):
    return _run_participant(directory, assets, freeze, arm, track, seed, rank, progress=progress,
                            mode="scripted", participant_factory=Participant)


def _run_participant(directory, assets, freeze, arm, track, seed, rank, *, progress, mode, participant_factory):
    identity(arm, track, seed, rank)
    directory = directory.resolve()
    journal = Actions(directory, assets, arm, progress, mode=mode)
    facts = journal.platform
    facts.update(os=platform.platform(), python=sys.version, sqlite=sqlite3.sqlite_version,
                 git=git(directory, "--version").decode().strip(), setup={"start_ns": time.monotonic_ns(), "start_utc": utc()})
    supervisor = repository = packet = reason = None
    errors = []
    try:
        journal.persist()

        def checkpoint(entries):
            journal.candidates = entries
            journal.persist()

        packet, repository = provision(directory, assets, arm, seed, checkpoint)
        native(directory, repository.binding, packet["receipt_ref"], "initial")
        supervisor = Supervisor(directory, repository, packet, arm, track, seed, rank, journal, assets, mode=mode)
        tools = Tools(directory, arm, repository, journal, supervisor)
        participant = participant_factory(tools)
        facts["setup"].update(end_ns=time.monotonic_ns(), end_utc=utc())
        reason = _run_arm(participant, journal)
        if (reason in ("done", "blocked", "idle")
                and journal.end - journal.start > journal.limits["max_elapsed_s"] * 10**9):
            reason = "time_limit"
        result = {"reason": reason, "end_monotonic_ns": journal.end, "end_utc": journal.ended}
        write(directory / "participant-exit.json", result)
        facts["participant_exit"] = result
        journal.persist()
    except Exception as exc:
        errors.append(f"participant: {type(exc).__name__}: {exc}")
    finally:
        journal.stop()
        facts["offline_evidence"] = {"start_ns": time.monotonic_ns(), "start_utc": utc()}
        live = supervisor.observe_exit() if supervisor is not None else False
        try:
            if repository is not None and packet is not None:
                native(directory, repository.binding, packet["receipt_ref"], "final")
        except Exception as exc:
            errors.append(f"native exit read: {type(exc).__name__}: {exc}")
        finally:
            if supervisor is not None:
                supervisor.stop()
        if live:
            try:
                native(directory, repository.binding, packet["receipt_ref"], "teardown")
            except Exception as exc:
                errors.append(f"post-teardown native read: {type(exc).__name__}: {exc}")

    saved = retain(directory, assets, arm, journal.checkpoint(),
                   interrupted=bool(errors) or reason in ("time_limit", "step_limit"))
    facts["offline_evidence"].update(end_ns=time.monotonic_ns(), end_utc=utc())
    errors.extend(facts["evidence_issues"])
    if errors:
        return failure(assets, freeze, arm, track, seed, rank, reason="; ".join(errors), progress=saved)
    elapsed = journal.end - journal.start
    facts.update(start_monotonic_ns=journal.start, end_monotonic_ns=journal.end, start_utc=journal.started,
                 end_utc=journal.ended, clock_stopped=True, cost_basis="exact", budget_reason=reason)
    rows = saved["rows"]
    last = next((r["seq"] for r in reversed(rows) if r["kind"] in ("public_state", "action")), None)
    rows.append({"seq": len(rows) + 1, "offset_ns": elapsed, "kind": "terminal",
                 "payload": {"end_reason": reason, "last_observation": last, "missing": [], "human_action": None,
                             "end_monotonic_ns": journal.end, "end_utc": journal.ended}})
    if mode == "model" and reason in ("model_limit", "provider_unavailable"):
        validity = "arm_incomplete" if reason == "model_limit" else "infrastructure_invalid"
    else:
        validity = validity_for(reason)
    if track == E3["stress"]["id"] and reason == "blocked":
        last_state = next((r["payload"] for r in reversed(rows) if r["kind"] == "public_state"), {})
        if last_state.get("original_report") == "unknown" and last_state.get("to") == "pending":
            validity = "complete"
    return {"schema_version": 1, "suite": MANIFEST["id"], "experiment": "E3", "track": track,
            "seed": seed, "arm": arm, "mode": mode, "replicate": freeze.get("replicate", 1),
            "inputs": input_identity(freeze, arm), "terminal": reason, "validity": validity, "score": None,
            "artifacts": {"journal": assets.put(rows, "json"), "candidates": assets.put(saved["candidates"], "json"),
                          "platform": assets.put(facts, "json"), "model": None},
            "cost": {"steps": journal.steps, "elapsed_ns": elapsed, "model_calls": 0, "input_tokens": 0,
                     "output_tokens": 0, "spend_usd_micros": 0}}


def attempt(directory, assets, freeze, arm, track, seed, rank, *, progress=None):
    from .report import derive_case
    raw = run_participant(directory, assets, freeze, arm, track, seed, rank, progress=progress)
    input_platform = raw["artifacts"]["platform"]
    timing = {"input_platform": input_platform, "start_ns": time.monotonic_ns(), "start_utc": utc(),
              "end_ns": None, "end_utc": None}
    clock_file = directory / "score-clock.json"
    write(clock_file, timing)
    try:
        raw["score"] = derive_case(raw, assets.archive)
    finally:
        timing.update(end_ns=time.monotonic_ns(), end_utc=utc())
        write(clock_file, timing)
    facts = json.loads(read_artifact(assets.archive, input_platform))
    facts["offline_scoring"] = timing
    facts["referenced_files"].append(input_platform)
    raw["artifacts"]["platform"] = assets.put(facts, "json")
    return raw
