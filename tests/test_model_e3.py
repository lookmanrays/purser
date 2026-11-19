"""Private loopback controls for the model-driven E3 case. No inference service is used."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import count
import json
from pathlib import Path
import select
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest

from experiments.e1.artifacts import Artifacts, FIXTURE, MANIFEST, REGISTRATION, ROOT, encoded, sha
from experiments.e1.recovery import FileArchive
from experiments.e1.report import Objects, wire_valid
from experiments.e1.run import Cutoff, Journal, _run_arm
from experiments.e3.fixture import TARGET
from experiments.model_budget import ModelBudget, SpendingAuthority
from experiments.model_e3 import reconstruct_case, recover_case, run_case
from purser.adapters.model import LocalEndpoint, LocalModelAdapter


def response_for(body, variant):
    data = json.loads(body["messages"][1]["content"])
    history = json.loads(data["observation"])
    intent = history[0]["intent"]
    if variant == "decline":
        return {"action": "defer", "arguments": {"reason": "Need more information", "next_action": "Inspect permission"}}
    if "dispatch" in data["actions"]:
        return {"action": "dispatch", "arguments": {"effect": intent["effect"], "target": intent["target"]["digest"]}}
    writes = [row for row in history if row["action"] == "effect"]
    write = writes[-1]
    attempt = write["attempt"]
    seen = [r for r in history if r["action"] == "reconcile" and r["attempt"] == attempt]
    origin = next((r["declaration"]["original_report"] for r in history if r["action"] == "decide"), None)
    if variant == "invalid":
        return {"action": "absent", "arguments": {}}
    if variant == "repeat_inspection" or (variant == "evidence" and not seen and write["acknowledgment"]["status"] == "timeout"):
        return {"action": "inspect", "arguments": {"attempt": attempt}}
    source = (seen or [write])[-1]
    if variant == "wrong_completion":
        report, choice, reason = "landed", "complete", "Assume the update landed"
    elif variant == "early_retry" and len(writes) == 1:
        report, choice, reason = "unlanded", "retry", "Assume the timed out write failed"
    elif source.get("observation"):
        observed = source["observation"]
        report = {"confirmed_happened": "landed", "confirmed_absent": "unlanded", "pending": "unknown"}[observed["state"]]
        choice = {"landed": "complete", "unlanded": "retry", "unknown": "pending"}[report]
        reason = observed["reason"]
    elif write["acknowledgment"]["status"] == "acknowledged":
        report, choice, reason = "landed", "complete", "guarded_write_acknowledged"
    else:
        report, choice, reason = "unknown", "pending", "Await an observation"
    args = {"attempt": attempt, "report": report, "original_report": origin or report,
            "reason": reason, "basis": source["source"]}
    if choice != "complete":
        args["next_action"] = "Request a fresh guarded attempt" if choice == "retry" else "Recheck the writer and receipt"
    return {"action": choice, "arguments": args}


@contextmanager
def canned_service():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.received.append(request)
            if self.server.block_at == len(self.server.received):
                self.server.reached.set()
                self.server.release.wait(timeout=25)
            choice = response_for(request, self.server.variant)
            reply = {"model": self.server.model, "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": encoded(choice).decode()}}]}
            if self.server.usage:
                reply["usage"] = {"prompt_tokens": 31, "completion_tokens": 17}
            body = encoded(reply)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.variant, server.usage, server.received, server.model = "evidence", True, [], "canned-e3-control"
        server.block_at, server.reached, server.release = None, Event(), Event()
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.release.set()
            server.shutdown()
            thread.join()


class CaseFixture:
    def __init__(self, root, server):
        self.root, self.server = root, server
        file_sha = sha(Path(__file__).read_bytes())
        self.endpoint = LocalEndpoint(server.server_port, server.model, file_sha, 4096)
        self.authority = SpendingAuthority(file_sha, self.endpoint.service_sha256, 0, 0, 0)
        self.budget = ModelBudget.enroll(root / "budget.sqlite", self.authority)
        self.adapter = LocalModelAdapter(self.endpoint, self.budget, root / "sealed")
        self.pin = {"fixture_tree": FIXTURE["tree_sha1"], "fixture_commit": FIXTURE["commit_sha1"],
                    **{key: file_sha for key in ("manifest_sha256", "protocol_sha256", "raw_schema_sha256",
                                                 "fault_driver_sha256", "executor_sha256", "scorer_sha256")},
                    "treatments": dict.fromkeys(MANIFEST["units"]["arm_order"], file_sha), "source_commit": "0" * 40}

    def run(self, label, arm="loop", track="primary", seed=3002, *, disabled=False, on_reply=None):
        folder = self.root / label
        folder.mkdir()
        assets = Artifacts(FileArchive(folder))
        unit = ("E3", track, seed, arm)
        reg = sha(encoded({"qualification": label, "unit": unit}))
        adapter = LocalModelAdapter(None, None, folder / "off") if disabled else self.adapter
        ordinals = count(1)

        def decide(prompt, *, deadline):
            ordinal = next(ordinals)
            reply = adapter.decide(registration_sha256=reg, unit=unit, ordinal=ordinal,
                                   prompt=prompt, output_limit=512, deadline=deadline)
            if on_reply:
                on_reply(ordinal, reply)
            return reply

        raw = run_case(folder, assets, self.pin, arm, track, seed, seed - 3000, decide=decide,
                       read_record=adapter.read_artifact, registration_sha256=reg)
        return raw, self.read(raw, assets.archive, reg), assets, reg

    def read(self, raw, archive, reg):
        return reconstruct_case(raw, archive, registration_sha256=reg, service_sha256=self.endpoint.service_sha256,
                                authority_sha256=self.authority.authority_sha256)


class ModelE3(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        service = canned_service()
        server = service.__enter__()
        self.addCleanup(service.__exit__, None, None, None)
        self.fixture = CaseFixture(Path(folder.name).resolve(), server)
        self.server = server

    def test_changed_model_choice_changes_native_result_under_identical_timeout(self):
        good, facts, _, _ = self.fixture.run("qualified")
        self.assertEqual(good["cost"]["model_calls"], len(self.server.received))
        self.assertEqual((good["mode"], good["validity"], good["cost"]["steps"]), ("model", "complete", 7))
        self.assertEqual((facts["original_truth"], facts["correct_reconciliation"], facts["effect_count"]),
                         ("unlanded", True, 1))
        self.assertEqual(facts["diagnostics"], [])
        self.assertEqual([d["action"] for d in facts["model_decisions"]], ["dispatch", "inspect", "retry", "complete"])
        first_messages = list(self.server.received[:2])
        self.server.received.clear()
        self.server.variant = "wrong_completion"
        wrong, facts, _, _ = self.fixture.run("counterfactual")
        self.assertEqual(self.server.received[:2], first_messages)
        self.assertFalse(wrong["score"]["correct_reconciliation"])
        self.assertEqual((facts["original_truth"], facts["original_report"], facts["effect_count"], facts["resolved_by_budget"]),
                         ("unlanded", "landed", 0, False))
        self.assertEqual((facts["stuck_requires_manual"], facts["manual_repair"]), (True, False))
        self.assertEqual(len(self.server.received), 2)

    def test_recovered_original_response_keeps_usage_without_publishing_its_proposal(self):
        folder = self.fixture.root / "post-response"
        folder.mkdir()
        script = """
from pathlib import Path
from types import SimpleNamespace
import sys
from tests.test_model_e3 import CaseFixture
fixture = CaseFixture(Path(sys.argv[1]), SimpleNamespace(server_port=int(sys.argv[2]), model='canned-e3-control'))
def cut(number, decision):
    if number == 2:
        print(decision.result_sha256, flush=True)
        sys.stdin.buffer.read(1)
fixture.run('cut', on_reply=cut)
"""
        proc = subprocess.Popen([sys.executable, "-B", "-c", script, str(folder), str(self.server.server_port)],
                                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 20)[0], "no original sealed response")
            digest = proc.stdout.readline().decode().strip()
            self.assertRegex(digest, "^[0-9a-f]{64}$")
        finally:
            proc.kill()
            proc.communicate(timeout=10)
        record = LocalModelAdapter(None, None, folder / "sealed").read_artifact(digest)
        stage = folder / "cut"
        assets = Artifacts(FileArchive(stage))
        reg = sha(encoded({"qualification": "cut", "unit": ["E3", "primary", 3002, "loop"]}))
        raw = recover_case(stage, assets, self.fixture.pin, "loop", "primary", 3002, 2,
                           registration_sha256=reg, recovered_records={2: record})
        facts = self.fixture.read(raw, assets.archive, reg)
        self.assertEqual((raw["cost"]["model_calls"], raw["cost"]["input_tokens"], raw["cost"]["output_tokens"],
                          raw["cost"]["spend_usd_micros"]), (2, 62, 34, 0))
        self.assertEqual((len(self.server.received), raw["cost"]["steps"]), (2, 4))
        self.assertEqual(facts["cost_lower_bounds"]["elapsed_ns"], raw["cost"]["elapsed_ns"])
        self.assertEqual([d["action"] for d in facts["model_decisions"]], ["dispatch"])
        self.assertEqual(facts["decisions"], [])
        self.assertEqual(facts["original_truth"], "unlanded")
        self.assertIsNone(raw["score"])

    def test_public_disclosure_comes_from_its_actual_released_arm(self):
        for arm in ("loop", "purser", "loop_equal_verifier"):
            with self.subTest(arm=arm):
                self.server.received.clear()
                raw, facts, assets, _ = self.fixture.run("disclosure-" + arm, arm, seed=3001)
                prompt = json.loads(self.server.received[1]["messages"][1]["content"])
                history = json.loads(prompt["observation"])
                self.assertNotIn(str(self.fixture.root), encoded(self.server.received).decode())
                self.assertNotIn("landed_ack_lost", encoded(self.server.received).decode())
                self.assertEqual([k["acknowledgment"] for k in history if k["action"] == "effect"], [{"status": "timeout"}])
                obs = [k for k in history if k["action"] == "reconcile"]
                self.assertEqual([k["delivery"] for k in obs], [] if arm == "loop" else ["automatic"])
                self.assertTrue(facts["correct_reconciliation"])
                self.assertEqual(facts["effect_count"], 1)
                self.assertEqual(facts["publications"][0]["new"], TARGET)
                e3 = Objects(assets.archive).item(raw["artifacts"]["platform"])["e3"]
                self.assertEqual(e3["kernel"] is not None, arm == "purser")

    def test_stress_clean_and_guard_refusal_are_observed(self):
        _, pending, _, _ = self.fixture.run("unresolved", "purser", "inconclusive-observation", 3001)
        self.assertEqual((pending["stress_held_pending"], pending["effect_count"]), (True, 0))
        self.server.variant = "decline"
        raw, unexposed, _, _ = self.fixture.run("before-stress", "loop", "inconclusive-observation", 3001)
        self.assertEqual(raw["validity"], "arm_incomplete")
        self.assertIsNot(unexposed["eligible"], True)
        self.server.variant = "evidence"
        clean, done, _, _ = self.fixture.run("clean", "loop", "clean", 3001)
        self.assertEqual((clean["cost"]["steps"], done["effect_count"], done["resolved_by_budget"]), (4, 1, True))
        self.server.variant = "early_retry"
        wrong, facts, assets, _ = self.fixture.run("refused", "loop", "primary", 3001)
        self.assertNotEqual(wrong["terminal"], "harness_failure")
        self.assertEqual((facts["original_truth"], facts["effect_count"], facts["duplicate_count"]), ("landed", 1, 0))
        self.assertFalse(facts["correct_reconciliation"])
        objects = Objects(assets.archive)
        platform = objects.item(wrong["artifacts"]["platform"])
        writer = objects.item(platform["e3"]["writers"]["T2"])
        self.assertIn("guard_refusal", [event["event"] for event in writer["events"]])

    def test_disabled_invalid_and_exhausted_turns_preserve_the_actual_outcome(self):
        raw, facts, _, _ = self.fixture.run("disabled", disabled=True)
        self.assertEqual((raw["terminal"], raw["validity"], raw["cost"]["model_calls"], raw["score"]),
                         ("provider_unavailable", "infrastructure_invalid", 0, None))
        self.assertFalse(self.server.received)
        self.assertIsNone(facts["first_model_exposure"])
        self.server.variant = "invalid"
        invalid, _, _, _ = self.fixture.run("invalid")
        self.assertEqual((invalid["terminal"], invalid["validity"], invalid["cost"]["model_calls"]),
                         ("blocked", "arm_incomplete", 2))
        self.server.variant, self.server.usage = "repeat_inspection", False
        exhausted, fees, _, _ = self.fixture.run("exhausted")
        self.assertEqual((exhausted["terminal"], exhausted["cost"]["model_calls"], exhausted["cost"]["input_tokens"]),
                         ("model_limit", 8, None))
        self.assertEqual(fees["cost_exact"]["spend_usd_micros"], 0)
        schema = json.loads((REGISTRATION / "raw.schema.json").read_bytes())
        self.assertTrue(wire_valid(exhausted, schema, schema))

    def test_cold_reader_detects_foreign_turn_or_unjustified_report(self):
        raw, _, assets, reg = self.fixture.run("challenged")
        with self.assertRaisesRegex(ValueError, "registration"):
            self.fixture.read(raw, assets.archive, "a" * 64)
        with self.assertRaisesRegex(ValueError, "score differs"):
            self.fixture.read({**raw, "score": {**raw["score"], "original_truth": "landed"}}, assets.archive, reg)
        with self.assertRaisesRegex(ValueError, "validity differs"):
            self.fixture.read({**raw, "validity": "infrastructure_invalid", "score": None}, assets.archive, reg)
        objects = Objects(assets.archive)
        trace = objects.item(raw["artifacts"]["model"])
        trace["turns"][1]["result"] = trace["turns"][0]["result"]
        tampered = {**raw, "artifacts": {**raw["artifacts"], "model": assets.put(trace, "json")}}
        with self.assertRaisesRegex(ValueError, "different registered case or turn"):
            self.fixture.read(tampered, assets.archive, reg)

    def test_recovery_reads_killed_transport_without_original_request_repetition(self):
        folder = self.fixture.root / "process"
        folder.mkdir()
        self.server.block_at = 2
        child = """
from pathlib import Path
from types import SimpleNamespace
import sys
from tests.test_model_e3 import CaseFixture
fixture = CaseFixture(Path(sys.argv[1]), SimpleNamespace(server_port=int(sys.argv[2]), model='canned-e3-control'))
fixture.run('cut')
"""
        proc = subprocess.Popen([sys.executable, "-B", "-c", child, str(folder), str(self.server.server_port)],
                                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            self.assertTrue(self.server.reached.wait(20), "second original HTTP call was not observed")
        finally:
            proc.kill()
            proc.communicate(timeout=10)
            self.server.release.set()
        stage = folder / "cut"
        assets = Artifacts(FileArchive(stage))
        unit = ("E3", "primary", 3002, "loop")
        reg = sha(encoded({"qualification": "cut", "unit": unit}))
        raw = recover_case(stage, assets, self.fixture.pin, "loop", "primary", 3002, 2, registration_sha256=reg)
        facts = self.fixture.read(raw, assets.archive, reg)
        self.assertEqual((raw["terminal"], raw["score"], len(self.server.received)), ("harness_failure", None, 2))
        self.assertEqual([d["action"] for d in facts["model_decisions"]], ["dispatch"])
        self.assertNotIn("model_calls", facts["cost_exact"])
        self.assertEqual(facts["cost_lower_bounds"]["model_calls"], 1)
        self.assertIsNone(raw["cost"]["input_tokens"])
        self.assertEqual(facts["cost_lower_bounds"]["input_tokens"], 31)
        self.assertEqual((raw["cost"]["steps"], facts["cost_lower_bounds"]["steps"]), (4, 4))
        public = Objects(assets.archive).item(raw["artifacts"]["platform"])["e3"]["actions"]
        self.assertIsNone(public[-1]["response"])
        self.assertEqual(Objects(assets.archive).item(public[-1]["request"])["input"], {"request": "choose", "turn": 2})
        self.assertEqual(facts["original_truth"], "unlanded")
        self.assertEqual(raw, recover_case(stage, assets, self.fixture.pin, "loop", "primary", 3002, 2,
                                           registration_sha256=reg))
        self.assertEqual(len(self.server.received), 2)

    def test_frozen_per_mode_clock_and_public_step_limits(self):
        for mode, seconds in (("scripted", 60), ("model", 900)):
            with self.subTest(mode=mode):
                journal = Journal("loop", mode=mode, released=False)
                armed = []

                class Driver:
                    def execute(self):
                        armed.append(signal.getitimer(signal.ITIMER_REAL)[0])
                        return "done"

                self.assertEqual(_run_arm(Driver(), journal), "done")
                self.assertLessEqual(armed[0], seconds)
                self.assertGreater(armed[0], seconds - 2)
                journal = Journal("loop", mode=mode)
                journal.start -= 61 * 10**9
                if mode == "scripted":
                    with self.assertRaisesRegex(Cutoff, "time_limit"):
                        journal.begin_action("context", "W-notify", "executor", TARGET, {})
                else:
                    for _ in range(96):
                        journal.begin_action("context", "W-notify", "executor", TARGET, {})
                    with self.assertRaisesRegex(Cutoff, "step_limit"):
                        journal.begin_action("context", "W-notify", "executor", TARGET, {})
