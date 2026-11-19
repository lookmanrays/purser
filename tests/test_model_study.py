"""Exercise prospective model qualification and a cold report through the CLI."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from experiments.e1.artifacts import REGISTRATION, ROOT, encoded, sha
from experiments.model_budget import ModelBudget, SpendingAuthority
from experiments.model_costs import ModelCaseCost
from experiments.model_study.collector import case_path
from experiments.model_study.registration import schedule
from experiments.model_study.report import inspect, predicates, render

WASI = os.environ.get("PURSER_WASI_ARCHIVES")


def edit(work, expression, disposition="request_close", action="edit"):
    path = {"W1": "notify/preferences.py", "W2": "notify/retry.py", "W3": "notify/delivery.py"}[work]
    source = (REGISTRATION / "input" / path).read_text().rsplit("    return ", 1)[0]
    return {"action": action, "arguments": {"path": path, "source": source + "    return " + expression + "\n",
                                            "disposition": disposition}}


def choice(name, **operands):
    return {"action": name, "arguments": operands}


@contextmanager
def counted(replies):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(req)
            answer = replies[len(calls)-1]
            data = encoded({"model": req["model"], "choices": [{"index": 0, "finish_reason": "stop",
                           "message": {"role": "assistant", "content": encoded(answer).decode()}}],
                           "usage": {"prompt_tokens": 19, "completion_tokens": 7}})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def cli(*args):
    result = subprocess.run([sys.executable, "-m", "experiments.model_study", *map(str, args)], cwd=ROOT,
                            capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


def enroll(folder, port, *, observer):
    from purser.adapters.model import LocalEndpoint
    endpoint = LocalEndpoint(port, "counted-notification-control", sha(b"disposable counted HTTP control"), 8000,
                             provider="fixture-provider", provider_version="1.0", model_revision="local-revision-7")
    policy = SpendingAuthority(sha(b"synthetic local qualification"), endpoint.service_sha256, 0, 0, 0)
    budget = folder / "budget.sqlite"
    ModelBudget.enroll(budget, policy)
    spec = {"service": endpoint.profile(), "authority": asdict(policy), "observer": {"status": observer},
            "data": "synthetic-notification-and-arm-public-context"}
    config = folder / "conditions.json"
    config.write_bytes(encoded(spec))
    series = folder / "series"
    cli("freeze", series, "--conditions", config, "--population", "canned_control", "--budget", budget)
    return series


class ModelStudy(unittest.TestCase):
    def test_frozen_population_missing_originals_and_zero_denominators(self):
        expected = json.loads((ROOT / "docs/evidence/M5/observer/expected-matrix-v1.json").read_bytes())
        units = list(schedule())
        self.assertEqual(len(set(units)), expected["population"]["total"])
        for exp, wanted in expected["experiments"].items():
            self.assertEqual(sum(u[0] == exp for u in units), wanted["registered_cells"])
        self.assertEqual(units[:3], [("E1","primary",1001,arm) for arm in ("loop","purser","loop_equal_verifier")])
        self.assertIn("0 / 0 / 0 / 195", render())
        cases = {u: {"facts": {"eligible": True, "unaffected_both_retained": True, "rework_steps": 0,
                               "final_pass": True}, "cost": ModelCaseCost("complete", {})}
                 for u in units if u[:2] == ("E2", "primary")}
        self.assertEqual(predicates(cases)["E2"], (True, True))

    def test_unavailable_observer_and_e3_unexposed_defer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with counted([choice("defer", reason="need an observation", next_action="inspect the intended update")]) as (port, calls):
                series = enroll(root, port, observer="unavailable")
                args = ("collect", series, "--unit", "E1/primary/1001/loop", "--unit", "E3/primary/3001/loop")
                cli(*args)
                cli(*args)
                self.assertEqual(len(calls), 1)
            first = cli("report", series)
            self.assertEqual(cli("report", series), first)
            freeze, cases = inspect(series)
            a, b = cases[("E1","primary",1001,"loop")], cases[("E3","primary",3001,"loop")]
            self.assertEqual(a["cost"].validity, "infrastructure_invalid", a["errors"])
            self.assertIn("isolated candidate observer unavailable", str(a["errors"]))
            self.assertEqual(a["cost"].exact["model_calls"], 0)
            self.assertEqual(b["cost"].validity, "arm_incomplete", b["errors"])
            self.assertIsNot(b["facts"]["eligible"], True)
            self.assertEqual(b["cost"].exact["input_tokens"], 19)
            self.assertEqual(b["facts"]["effect_count"], 0)
            self.assertIn("0 of 195 original", first)

    def test_cli_recovers_returned_bytes_before_settlement_without_releasing_a_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unit = "E3", "primary", 3001, "loop"
            with counted([choice("defer", reason="check the scope", next_action="inspect")]) as (port, calls):
                series = enroll(root, port, observer="unavailable")
                target = "/".join(map(str, unit))
                cut = """
import os, runpy
from experiments.model_budget import ModelBudget
def terminated(*args, **kwargs):
    os._exit(49)
ModelBudget.settle = terminated
runpy.run_module('experiments.model_study', run_name='__main__')
"""
                proc = subprocess.run([sys.executable, "-c", cut, "collect", str(series), "--unit", target],
                                      cwd=ROOT, capture_output=True)
                self.assertEqual(proc.returncode, 49, proc.stderr)
                cli("collect", series, "--unit", target)
                cli("collect", series, "--unit", target)
                self.assertEqual(len(calls), 1)
            _, cases = inspect(series)
            result = cases[unit]
            self.assertEqual(result["cost"].validity, "infrastructure_invalid")
            self.assertEqual([result["cost"].exact.get(k) for k in ("model_calls","input_tokens","output_tokens","spend_usd_micros")],
                             [1,19,7,0], result["errors"])
            self.assertEqual(result["facts"]["model_decisions"], [])
            self.assertEqual(result["facts"]["model_receipts"], 1)

    @unittest.skipUnless(WASI, "requires the enrolled disposable macOS WASI fixture")
    def test_same_first_request_accepts_different_source_in_separate_replicates(self):
        unit = ("E1", "clean", 1001, "loop")
        target = "/".join(map(str, unit))
        withheld = choice("stop", reason="leave integration open")
        replies = [edit("W1", "True", "submit"), withheld,
                   edit("W1", "channel not in disabled", "submit"), withheld]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with counted(replies) as (port, calls):
                first = enroll(root, port, observer="qualified")
                second = root / "replicate-2"
                cli("freeze", second, "--conditions", root / "conditions.json", "--population", "canned_control",
                    "--budget", root / "budget.sqlite", "--replicate", "2", "--reason", "vary only the first reply")
                for series in (first, second):
                    cli("collect", series, "--unit", target, "--wasi-archives", WASI)
                self.assertEqual(len(calls), 4)
                self.assertEqual(calls[0], calls[2])
            results = [inspect(series)[1][unit] for series in (first, second)]
            self.assertNotEqual(results[0]["facts"]["final_candidate"], results[1]["facts"]["final_candidate"])
            for case in results:
                self.assertEqual(case["cost"].validity, "arm_incomplete", case["errors"])
                self.assertEqual(case["cost"].exact["model_calls"], 2)
                self.assertFalse(case["facts"]["final_parent_closed"])

    @unittest.skipUnless(WASI, "requires the enrolled disposable macOS WASI fixture")
    def test_closed_affected_stress_keeps_transient_sibling_reopening(self):
        unit = ("E2", "closed-affected", 2001, "loop_equal_verifier")
        replies = [edit("W1", "channel not in disabled"), edit("W3", "tuple(dict.fromkeys(event_ids))"),
                   edit("W2", "tuple(k * base for k in range(attempts))"), choice("reopen", work="W1"),
                   choice("close", work="W1"),
                   edit("W2", "(0,) + tuple(base * 2 ** (k - 1) for k in range(1, attempts))", action="revise_retry"),
                   choice("close", work="W-notify")]
        with tempfile.TemporaryDirectory() as temp:
            with counted(replies) as (port, calls):
                series = enroll(Path(temp), port, observer="qualified")
                cli("collect", series, "--unit", "/".join(map(str, unit)), "--wasi-archives", WASI)
                self.assertEqual(len(calls), 7)
                turn = json.loads(calls[1]["messages"][1]["content"])
                self.assertIn("independent_verifier", json.loads(turn["observation"])["last_public_actions"][-2]["actor"])
            case = inspect(series)[1][unit]
            self.assertEqual(case["cost"].validity, "complete", case["errors"])
            facts = case["facts"]
            self.assertTrue(facts["intervention_reached"] and facts["affected_invalid_closure"])
            self.assertFalse(facts["stale_at_delivery"] or facts["unaffected_both_retained"])
            self.assertEqual(facts["unaffected_reopened"], 1)
            self.assertTrue(facts["final_parent_closed"] and facts["final_pass"])

    @unittest.skipUnless(WASI, "requires the enrolled disposable macOS WASI fixture")
    def test_e1_wrong_source_false_parent_and_e2_real_premise(self):
        controls = [
            edit("W1", "True"), choice("close", work="W-notify"),
            edit("W1", "channel not in disabled"), edit("W3", "tuple(dict.fromkeys(event_ids))"),
            edit("W2", "tuple(k * base for k in range(attempts))", "submit"),
            edit("W2", "(0,) + tuple(base * 2 ** (k - 1) for k in range(1, attempts))", action="revise_retry"),
            choice("close", work="W-notify"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            e1, e2 = ("E1","primary",1002,"loop"), ("E2","primary",2001,"purser")
            with counted(controls) as (port, calls):
                series = enroll(root, port, observer="qualified")
                cli("collect", series, "--unit", "/".join(map(str,e1)), "--unit", "/".join(map(str,e2)),
                    "--wasi-archives", WASI)
                self.assertEqual(len(calls), len(controls))
                self.assertNotIn("linear_to_exponential_after", str(calls))
            frozen, cases = inspect(series)
            for u in (e1, e2):
                self.assertEqual(cases[u]["cost"].validity, "complete", cases[u]["errors"])
            a, b = cases[e1]["facts"], cases[e2]["facts"]
            self.assertTrue(a["false_parent_closure"])
            self.assertFalse(a["final_pass"])
            self.assertTrue(b["intervention_reached"] and b["unaffected_both_retained"] and b["final_pass"])
            self.assertGreater(b["rework_steps"], 0)
            self.assertEqual(cases[e2]["cost"].exact["model_calls"], 5)
            before = cli("report", series)
            self.assertEqual(cli("report", series), before)
            path = case_path(series, e1)
            raw = json.loads((path / "raw.json").read_bytes())
            raw["score"]["false_parent_closure"] = False
            (path / "raw.json").write_bytes(encoded(raw))
            self.assertEqual(inspect(series)[1][e1]["cost"].validity, "infrastructure_invalid")
