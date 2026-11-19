"""Retain each original provider decision before applying its public action."""

import re
import time

from experiments.e1.artifacts import MANIFEST, encoded
from experiments.e1.run import utc
from experiments.e3.fixture import read, write


LIMIT = MANIFEST["budgets"]["model"]["max_model_calls"]
USAGE = ("input_tokens", "output_tokens", "spend_usd_micros")
TRACE_KIND = "purser.model.e3.turns.v1"


def failure_terminal(code):
    if code == "budget_refused":
        return "model_limit"
    if code in ("invalid_proposal", "unfinished_response", "unusable_response"):
        return "blocked"
    return "provider_unavailable"


def manifest(unit, registration_sha256, population):
    if not isinstance(registration_sha256, str) or not re.fullmatch("[a-f0-9]{64}", registration_sha256):
        raise ValueError("the E3 case requires a frozen registration SHA-256")
    if population not in ("qualification", "original"):
        raise ValueError("model cases require a registered qualification or original population")
    return {"kind": TRACE_KIND, "registration_sha256": registration_sha256, "unit": list(unit), "population": population}


def check_record(raw, result_sha256, identity, ordinal, prompt):
    from purser.adapters.model import verify_model_record

    record = verify_model_record(raw, expected_sha256=result_sha256)
    turn = {"registration_sha256": identity["registration_sha256"], "unit": identity["unit"], "ordinal": ordinal}
    if record["turn"] != turn:
        raise ValueError("model result belongs to a different registered case or turn")
    expected = prompt.request(record["service"]["model"], record["limits"]["output_tokens"])
    if encoded(record["request"]) != encoded(expected):
        raise ValueError("model request differs from the participant's released public context and menu")
    return record


class Turns:
    def __init__(self, directory, assets, identity, decide, read_record):
        self.folder = directory / "model-turns"
        self.folder.mkdir()
        self.assets, self.identity = assets, identity
        self.decide, self.read_record = decide, read_record
        self.ordinal = 0
        write(self.folder / "identity.json", identity)

    @property
    def remaining(self):
        return self.ordinal < LIMIT

    def choose(self, prompt, journal):
        from purser.adapters.model import ModelIncomplete

        if not self.remaining:
            raise RuntimeError("registered model turn limit exhausted")
        self.ordinal += 1
        path = self.folder / f"turn-{self.ordinal:02d}.json"
        entry = {"ordinal": self.ordinal, "before_actions": journal.steps,
                 "issued_ns": time.monotonic_ns(), "issued_utc": utc()}
        write(path, entry)
        deadline = journal.start / 1e9 + journal.limits["max_elapsed_s"]

        def receive(result, outcome):
            result_sha256 = result.result_sha256
            ref = None
            if result_sha256 is not None:
                original = self.read_record(result_sha256)
                raw = original if isinstance(original, bytes) else encoded(original)
                record = check_record(raw, result_sha256, self.identity, self.ordinal, prompt)
                if not entry["issued_ns"] <= record["started_monotonic_ns"] <= record["returned_monotonic_ns"] <= time.monotonic_ns():
                    raise ValueError("foreign or replayed model response outside the released E3 turn")
                proposal = {"action": result.action, "arguments": result.arguments} if outcome == "decision" else None
                if (record["proposal"] != proposal or record["outcome"] != outcome
                        or record["request_sha256"] != result.request_sha256
                        or any(record["usage"][name] != getattr(result, name) for name in USAGE)):
                    raise ValueError("adapter decision or cost disagrees with the original provider record")
                ref = self.assets.put(raw, "bin")
            elif outcome == "decision":
                raise ValueError("unsealed positive model decision")
            entry.update(outcome=outcome, result=ref, request_sha256=result.request_sha256,
                         returned_ns=time.monotonic_ns(), returned_utc=utc())
            write(path, entry)

        try:
            result = self.decide(prompt, deadline=deadline)
        except ModelIncomplete as error:
            receive(error, error.code)
            raise
        receive(result, "decision")
        return result

def retain(directory, assets, identity, *, recovered=()):
    """Never call a model during recovery. A surviving settlement can fill an interrupted read."""
    folder = directory / "model-turns"
    if (folder / "identity.json").exists() and read(folder / "identity.json") != identity:
        raise ValueError("original E3 model case identity differs")
    recovered = dict(recovered)
    if any(type(key) is not int or not 1 <= key <= LIMIT for key in recovered):
        raise ValueError("unregistered recovered turn")
    entries = []
    for ordinal, path in enumerate(sorted(folder.glob("turn-*.json")), 1):
        entry = read(path)
        if path.name != f"turn-{ordinal:02d}.json" or entry.get("ordinal") != ordinal or ordinal > LIMIT:
            raise ValueError("original E3 model turn sequence is inconsistent")
        if ordinal in recovered:
            original = recovered.pop(ordinal)
            raw = original if isinstance(original, bytes) else encoded(original)
            ref = assets.put(raw, "bin")
            if entry.get("result") is not None and entry["result"] != ref:
                raise ValueError("recovered settlement differs from original model decision")
            if not entry.get("result"):
                entry["recovered_result"] = ref
        entries.append(entry)
    if recovered:
        raise ValueError("recovered model receipt has no preceding E3 caller release")
    return {**identity, "turns": entries}
