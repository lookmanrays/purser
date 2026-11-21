"""Retain original model turns across the collector/transport process boundary."""

import time
from pathlib import Path

from experiments.e1.artifacts import encoded, sha
from experiments.e1.recovery import write_atomic
from experiments.e1.report import Objects, parse, require
from experiments.model_budget import LIMITS, ModelBudget, ModelTurn
from experiments.model_costs import USAGE, measure_model_turns
from purser.adapters.model import LocalEndpoint, LocalModelAdapter, ModelIncomplete, verify_model_record
from purser.adapters.model_evidence import request_identity

from .registration import validate_conditions

def service(freeze):
    conditions = freeze["conditions"]
    authority = validate_conditions(conditions, freeze["population"])
    if freeze["population"] == "disabled_control":
        if freeze["budget_file"] is not None or conditions["service"] != {"disabled": True}:
            raise ValueError("disabled control must register a disabled service")
        return None, None
    registered = conditions["service"]
    endpoint = LocalEndpoint.from_profile(registered)
    binding = freeze["budget_file"]
    path = Path(binding["path"]).resolve(strict=True)
    info = path.stat()
    if (info.st_dev, info.st_ino) != (binding["device"], binding["inode"]):
        raise ValueError("registered study budget file was replaced")
    return endpoint, ModelBudget(path, authority)


class Session:
    def __init__(self, case, assets, freeze, identity, endpoint, budget):
        self.directory = case / "transport"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.assets, self.freeze, self.identity = assets, freeze, identity
        self.endpoint, self.budget = endpoint, budget
        self.adapter = LocalModelAdapter(endpoint, budget, self.directory)
        self.ordinal = 0

    def decide(self, prompt, *, deadline):
        self.ordinal += 1
        if self.ordinal > LIMITS["max_model_calls"]:
            raise ModelIncomplete("model_limit")
        path = self.directory / f"invoke-{self.ordinal:02d}.json"
        if path.exists():
            raise ValueError("a started model invocation must be recovered without resending")
        limit = self.freeze["parameters"]["output_limit"]
        request = None if self.endpoint is None else prompt.request(self.endpoint.model, limit)
        digest = None if request is None else request_identity(self.endpoint.profile(), request)
        entry = {"ordinal": self.ordinal, "issued_ns": time.monotonic_ns(),
                 "prompt": self.assets.put({"task": prompt.task, "observation": prompt.observation,
                             "actions": {name: spec.describe() for name, spec in prompt.actions.items()}}, "json"),
                 "request": self.assets.put(request, "json") if request is not None else None, "request_sha256": digest}
        write_atomic(path, encoded(entry))
        try:
            answer = self.adapter.decide(registration_sha256=self.identity["registration_sha256"],
                                         unit=tuple(self.identity["unit"]), ordinal=self.ordinal, prompt=prompt,
                                         output_limit=limit, deadline=deadline)
        except ModelIncomplete as error:
            entry.update(outcome=error.code, result_sha256=error.result_sha256, returned_ns=time.monotonic_ns())
            write_atomic(path, encoded(entry))
            raise
        entry.update(outcome="decision", result_sha256=answer.result_sha256, returned_ns=time.monotonic_ns())
        write_atomic(path, encoded(entry))
        return answer

    def retain(self):
        turns = []
        for number, path in enumerate(sorted(self.directory.glob("invoke-*.json")), 1):
            turn = parse(path.read_bytes())
            require(turn["ordinal"] == number <= LIMITS["max_model_calls"], "noncontiguous model session")
            settlement = record = None
            if self.budget is not None:
                key = ModelTurn(self.identity["registration_sha256"], tuple(self.identity["unit"]), number, turn["request_sha256"])
                try:
                    record = self.adapter.recover_released(key, deadline=time.monotonic() + 10)
                except ModelIncomplete as error:
                    if error.code not in ("unresolved_release", "recovery_deadline"):
                        raise
                    turn["recovery"] = error.code
                settlement = self.budget.inspect(key)
            turn["reservation"] = settlement
            if record is not None:
                sealed = self.assets.put(encoded(record), "bin")
                require(turn.get("result_sha256") in (None, sealed["sha256"]), "different original caller receipt")
                turn["record"] = sealed
                if turn.get("result_sha256") is None:
                    turn["recovery"] = "sealed_original"
            elif turn.get("result_sha256"):
                raise ValueError("returned model decision no longer has an attributable original")
            turns.append(turn)
        return {**self.identity, "turns": turns}


def read_session(trace, archive, frozen, identity):
    objects = Objects(archive)
    require({k: trace[k] for k in identity} == identity, "session was recorded for a different case")
    originals = []
    records = {}
    for number, turn in enumerate(trace["turns"], 1):
        require(number == turn["ordinal"] <= LIMITS["max_model_calls"], "model turn order differs")
        prompt = objects.item(turn["prompt"])
        reservation = turn["reservation"]
        request = objects.item(turn["request"]) if turn["request"] is not None else None
        if request is not None:
            require(request_identity(frozen["conditions"]["service"], request) == turn["request_sha256"]
                    and parse(request["messages"][1]["content"].encode()) == prompt, "original model input differs")
        if reservation is not None:
            require(reservation["registration"] == identity["registration_sha256"] and reservation["unit"] == identity["unit"]
                    and reservation["number"] == number and reservation["request_sha256"] == turn["request_sha256"],
                    "reservation differs from registered turn")
        if "record" not in turn:
            if reservation is not None or (frozen["population"] != "disabled_control"
                                          and turn.get("outcome") not in ("disabled", "budget_refused", "deadline")):
                originals.append(None)
            continue
        ref = turn["record"]
        r = verify_model_record(objects.blob(ref), expected_sha256=ref["sha256"])
        expected_turn = {"registration_sha256": identity["registration_sha256"], "unit": identity["unit"], "ordinal": number}
        require(r["turn"] == expected_turn and r["service"] == frozen["conditions"]["service"]
                and r["authority_sha256"] == frozen["conditions"]["authority"]["authority_sha256"]
                and r["request"] == request and r["request_sha256"] == turn["request_sha256"]
                and reservation is not None and reservation["result_sha256"] in (None, ref["sha256"])
                and turn.get("result_sha256") in (None, ref["sha256"])
                and (turn.get("outcome") in (None, r["outcome"], "evidence_unavailable")),
                "original model release, request or settlement differs")
        if reservation["result_sha256"] is not None:
            require(all(reservation[key] == r["usage"][key] for key in USAGE),
                    "settled usage differs from the original transport receipt")
        records[number] = r
        originals.append(r)
    exact, lower, first = measure_model_turns(originals)
    return records, exact, lower, first
