"""Deliver a genuine observation to a nominally clean plan immediately before it returns done."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "repo"))
from experiments.e2 import suite
from experiments.e2.driver import Driver

execute = Driver.execute
def controlled_exit(driver):
    reason = execute(driver)
    if driver.j.arm == "loop" and driver.track == "clean" and reason == "done":
        oid = driver.adapter.context("W2")["candidate"]
        driver.receive(oid)
        row = driver.adapter.read()["W2"]
        (ROOT / "clean-public-receipt.json").write_text(json.dumps(row, indent=2)+"\n")
    return reason

with patch.object(Driver, "execute", controlled_exit):
    suite.collect_units(ROOT / "repo" / "private-clean-fault",
                        [u for u in suite.schedule() if u[0] == "clean" and u[3] != "loop_equal_verifier"])
