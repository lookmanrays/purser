"""Two real driver invocations acquire the same public snapshot before an intervening trigger."""
from datetime import datetime, timezone
import json
import multiprocessing
from pathlib import Path
import tempfile

from purser.driver import tick
from purser.kernel.gate import admit
from purser.kernel.store import Store

SCOPE = "C-order"
OWNER = "principal:local"
ACTOR = "executor:local"
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


def binding(role, actor, assignment=None, **extra):
    return {"actor_id": actor, "role": role, "principal_id": OWNER,
            "commitment": None if role == "principal" else SCOPE, "assignment": assignment, **extra}


def send(store, who, op, name):
    request = {"v": 1, "proposal_id": name, "commitment": SCOPE,
               "base_revision": store.snapshot()["revision"], "ops": [op], "justification": []}
    reply = admit(store, who, request, clock=lambda: NOW)
    if reply["disposition"] not in ("admitted", "pending"):
        raise ValueError((op, reply))
    return reply


def setup(store):
    owner = binding("principal", OWNER)
    plan = binding("planner", "planner:local")
    send(store, owner, {"op": "commitment.create", "id": SCOPE, "root": "W2", "statement": "Queue fixture."}, "p-create")
    held = send(store, owner, {"op": "commitment.request_acceptance", "id": SCOPE,
                               "expires_at": "2026-12-01T00:00:00.000000Z"}, "p-hold")
    send(store, owner, {"op": "approval.answer", "id": held["details"]["approval_id"],
                        "request_digest": held["digest"], "decision": "approve"}, "p-agree")
    send(store, plan, {"op": "assumption.create", "id": "A1", "statement": "linear",
                       "observer": "observer:clock"}, "p-premise")
    send(store, plan, {"op": "decision.record", "id": "D1", "choice": "linear", "alternatives": ["linear", "exp"],
                       "rationale": "current schedule", "support": ["A1"], "triggers": ["A1"], "supersedes": None}, "p-decision")
    send(store, plan, {"op": "dependency.add", "id": "W2", "support": "D1"}, "p-edge")
    send(store, plan, {"op": "work.assign", "id": "AS-worker", "work": "W2", "executor": ACTOR}, "p-assign")


def run_one(path, captured, release_snapshot, called, return_candidate, channel):
    class PausedStore(Store):
        first_read = True

        def snapshot(self, **options):
            current = super().snapshot(**options)
            if self.first_read:
                self.first_read = False
                captured.set()
                if not release_snapshot.wait(20):
                    raise TimeoutError("snapshot wait")
            return current

    def callback(pack):
        called.set()
        if not return_candidate.wait(20):
            raise TimeoutError("callback wait")
        return {"kind": "git.commit", "algorithm": "sha1", "digest": "f" * 40}

    try:
        channel.put(tick(PausedStore(path), {ACTOR: callback}, clock=lambda: NOW))
    except BaseException as exc:
        channel.put({"error": repr(exc)})


def main():
    ctx = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory() as dest:
        db = Path(dest) / "control.sqlite"
        store = Store(db)
        store.initialize()
        setup(store)
        barriers = [[ctx.Event() for _ in range(4)] + [ctx.Queue()] for _ in range(2)]
        procs = [ctx.Process(target=run_one, args=(db, *grp)) for grp in barriers]
        try:
            for proc in procs:
                proc.start()
            assert all(g[0].wait(15) for g in barriers)
            barriers[0][1].set()
            assert barriers[0][2].wait(15)
            change = send(store, binding("observer", "observer:clock", "A1", premise={"artifact": b"Revised."}),
                          {"op": "assumption.observe", "id": "Observed", "assumption": "A1",
                           "finding": "contradicts"}, "p-change")
            barriers[1][1].set()
            barriers[1][3].set()
            second = barriers[1][4].get(timeout=15)
            barriers[0][3].set()
            first = barriers[0][4].get(timeout=15)
            print(json.dumps({"invalidation": change["revision"], "started_after_invalidation": barriers[1][2].is_set(),
                              "first": first, "second": second}, sort_keys=True))
            assert first.get("receipt", {}).get("disposition") == "rejected"
            assert not barriers[1][2].is_set()
        finally:
            for grp in barriers:
                grp[1].set()
                grp[3].set()
            for proc in procs:
                proc.join(4)
                if proc.is_alive():
                    proc.terminate()
                    proc.join()


if __name__ == "__main__":
    main()
