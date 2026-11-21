"""Publish and independently observe real adverse transitions within a measured RPC, private replicate only."""
import copy
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "repo"))
from experiments.e1.artifacts import extract_tree
from experiments.e1 import observer
from experiments.e2 import policy, suite
from purser.kernel.store import Store
from purser.kernel.workview import WorkView

seen = []
def cold_arm(arm, phase):
    snap = Store(arm.store.path).snapshot()
    w3 = next(row for row in WorkView(snap["projection"]).answers()["q02"] if row["work"] == "W3")
    seen.append({"phase":phase,"revision":snap["revision"],"head_hash":snap["head_hash"],"W3":w3})
    return w3

original = policy.KernelArm.revise
def multiple_public_actions(arm, message):
    response = original(arm, message)
    candidate = cold_arm(arm, "adopted")["candidate"]["digest"]
    extra = copy.copy(arm)
    # A second adapter port accesses the same SQLite participant under the already scoped roles.
    # The measured primary action and its response remain the genuine W2 adoption.
    extra.on_proposal = None
    extra.assign_again("W3")
    cold_arm(arm, "W3 reassigned")
    extra.submit("W3", candidate)
    cold_arm(arm, "W3 re-submitted")
    label = "W3/delivery"
    extra.opening("W3", label)
    archive = arm.store.path.parent / "private-objects"
    with extract_tree(archive, candidate) as root:
        observed = policy.inspect(archive, candidate, Path(root), label, policy.LINEAR)
    extra.observe("W3", label, observed)
    extra.verify("W3", label, dict(observed, verdict=observer.verdict(observed, label, candidate)))
    close = extra.decide("W3", {})
    assert close["receipt"]["disposition"] == "admitted"
    assert cold_arm(arm, "W3 reclosed")["candidate"]["digest"] == candidate
    arm.count = extra.count
    return response

with patch.object(policy.KernelArm, "revise", multiple_public_actions):
    suite.collect_units(ROOT / "repo" / "private-transient", [("primary", 2001, 1, "purser")])
(ROOT / "transient-cold-observations.json").write_text(json.dumps(seen, indent=2)+"\n")
