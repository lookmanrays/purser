"""One real supervised physical writer. Faults end or suspend this process inside the broker guard."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

from purser.effects.git import GitRepository
from purser.kernel.wire import digest

from .fixture import read
from .witness import note

LOST = 76


def authorize_loop(packet, repository, intent, attempt, facts):
    grant = packet["grant"]
    if (intent != packet["intent"] or facts["repository_instance"] != repository.binding["repository_instance"]
            or facts["intent_digest"] != digest("effect", intent)
            or not set(facts["changed_paths"]) <= set(grant["paths"])
            or datetime.now(timezone.utc) >= datetime.fromisoformat(grant["expires_at"])):
        raise ValueError("scoped fixture permission does not cover this physical release")
    return {"disposition": "admitted", "details": {"effect": intent["effect"], "attempt": attempt,
            "intent_digest": facts["intent_digest"], "state": "dispatching"}}, True


def run(directory, attempt):
    folder = directory / "writers" / attempt
    command = read(folder / "command.json")
    packet = read(directory / "task.json")
    intent = command["intent"]
    repository = GitRepository(read(directory / "binding.json"))
    note(folder, "started", writer_pid=os.getpid(), parent_pid=os.getppid())

    def after_authorization():
        note(folder, "authorized", effect=intent["effect"], attempt=attempt)
        if command["cut"] == "unlanded_writer_finished":
            os._exit(LOST)
        if command["cut"] == "inconclusive-observation":
            # The writer still owns the exclusive lease while the participant observes.
            # If the controller dies, EOF ends the suspended writer before Git can run.
            print(json.dumps({"held": True}), flush=True)
            sys.stdin.buffer.read(1)
            os._exit(LOST)

    def after_write():
        note(folder, "written", effect=intent["effect"], attempt=attempt)
        if command["cut"] == "landed_ack_lost":
            os._exit(LOST)

    if command["arm"] == "purser":
        from purser.kernel.store import Store
        from .treatment import dispatch
        receipt, fresh = dispatch(Store(directory / "kernel.sqlite"), repository, intent, attempt, command["action_id"],
                                  after_authorization=after_authorization, after_write=after_write)
    else:
        if read(directory / "bound.json") != intent:
            raise ValueError("physical release is for a different submitted intention")
        with repository.broker() as broker:
            def admit(facts):
                cutoff = authorize_loop(packet, repository, intent, attempt, facts)
                note(folder, "permission_checked", grant=packet["grant"], preparation=facts, authorization=cutoff[0])
                return cutoff
            receipt, fresh = broker.dispatch(intent, attempt, admit, after_authorization=after_authorization,
                                             after_write=after_write)
    public = {"status": "acknowledged", "attempt": attempt, "effect": intent["effect"],
              "target": intent["target"]["digest"], "receipt_ref": packet["receipt_ref"]} if fresh else {
                  "status": "refused", "receipt": receipt}
    note(folder, "reply", public=public, authorization=receipt)
    print(json.dumps(public), flush=True)


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2])
