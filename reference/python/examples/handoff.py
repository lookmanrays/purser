"""Create a disposable coding-agent handoff and print its database path."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp

from purser.driver import tick
from purser.kernel.artifacts import Artifacts
from purser.kernel.gate import admit
from purser.kernel.store import Store
from purser.kernel.wire import format_time


def create_handoff():
    db = Path(mkdtemp(prefix="purser-handoff-")) / "work.db"
    store = Store(db)
    store.initialize()
    owner = {"role": "principal", "actor_id": "principal:local", "principal_id": "principal:local",
             "commitment": None, "assignment": None}
    commitment, work = "C-notifications", "W-retry-limit"

    def propose(proposal_id, *operations, expected="admitted"):
        proposal = {"v": 1, "proposal_id": proposal_id, "commitment": commitment,
                    "base_revision": store.snapshot()["revision"], "ops": list(operations), "justification": []}
        receipt = admit(store, owner, proposal)
        if receipt["disposition"] != expected:
            raise RuntimeError(f"{proposal_id}: {receipt['code']}")
        return receipt

    # The example owner activates a task in this new database.
    propose("p-create", {"op": "commitment.create", "id": commitment, "root": work,
                         "statement": "Stop sending a notification after three failed attempts."})
    request = propose("p-request", {"op": "commitment.request_acceptance", "id": commitment,
                                   "expires_at": format_time(datetime.now(timezone.utc) + timedelta(minutes=5))},
                      expected="pending")
    propose("p-activate", {"op": "approval.answer", "id": request["details"]["approval_id"],
                           "request_digest": request["digest"], "decision": "approve"})
    propose("p-assign",
            {"op": "obligation.set", "id": "O-three-attempts", "work": work, "version": 1,
             "title": "Three failures stop further delivery attempts", "observer": "observer:delivery-test",
             "verifier": "verifier:delivery-test", "definition_sha256": sha256(b"assert delivery_count == 3").hexdigest(),
             "environment_sha256": sha256(b"Local simulated notification sender").hexdigest()},
            {"op": "work.assign", "id": "AS-retry-limit", "work": work, "executor": "executor:example"})

    # A scripted agent submits a configuration. The required delivery test is still outstanding.
    def submit_configuration(_context):
        digest = Artifacts(db).put(b'{"max_attempts": 3}\n')
        return {"kind": "artifact.bytes", "algorithm": "sha256", "digest": digest}

    turn = tick(store, {"executor:example": submit_configuration})
    if turn["state"] != "dispatched" or turn["receipt"]["disposition"] != "admitted":
        raise RuntimeError(f"Could not submit the example configuration: {turn}")
    return db


if __name__ == "__main__":
    print(create_handoff())
