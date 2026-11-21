"""Execute the registered intervention and retain the actual process and native ref outcomes."""

import json
import subprocess
import sys

from experiments.e1.artifacts import ROOT
from .fixture import E3, read, write
from .witness import native, note, transitions
from .writer import LOST

TIMEOUT = {"status": "timeout"}


def intervention(track, rank):
    if track == "primary":
        return E3["fault_cycle"][(rank - 1) % len(E3["fault_cycle"])]
    return None if track == "clean" else E3["stress"]["id"]


class Supervisor:
    def __init__(self, directory, repository, packet, arm, track, seed, rank, journal, assets, *, mode="scripted"):
        self.directory, self.binding, self.packet = directory, repository.binding, packet
        self.arm, self.track, self.seed, self.rank = arm, track, seed, rank
        self.journal, self.assets = journal, assets
        self.mode = mode
        self.children = {}
        self.previous = read(directory / "native" / "initial.json")

    def dispatch(self, intent, attempt, action_id):
        folder = self.directory / "writers" / attempt
        folder.mkdir()
        cut = intervention(self.track, self.rank) if not self.children else None
        command = {"intent": intent, "attempt": attempt, "action_id": action_id, "arm": self.arm, "cut": cut}
        if self.mode == "model":
            command["mode"] = "model"
        write(folder / "command.json", command)
        with (folder / "stderr").open("xb") as errors:
            proc = subprocess.Popen([sys.executable, "-B", "-m", "experiments.e3.writer", str(self.directory), attempt],
                                    cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        self.children[attempt] = proc
        note(folder, "launched", writer_pid=proc.pid, action_id=action_id)
        line = proc.stdout.readline()
        if cut == "inconclusive-observation" and line and json.loads(line) == {"held": True}:
            if proc.poll() is not None:
                raise RuntimeError("stress writer did not remain active at the timeout")
            note(folder, "held_at_return", writer_pid=proc.pid)
            answer = TIMEOUT.copy()
        else:
            code = proc.wait()
            note(folder, "reaped", writer_pid=proc.pid, returncode=code)
            if code == LOST and not line and cut in ("landed_ack_lost", "unlanded_writer_finished"):
                answer = TIMEOUT.copy()
            elif code == 0 and line:
                answer = json.loads(line)
            else:
                raise RuntimeError("physical writer failed: " + (folder / "stderr").read_text()[-2000:])
        current = native(self.directory, self.binding, self.packet["receipt_ref"], "after-" + attempt)
        before = transitions(self.previous)
        after = transitions(current)
        if after[:len(before)] != before:
            raise ValueError("independent native chronology lost its captured prefix")
        facts = {"intent": intent["effect"], "attempt": attempt, "action_id": action_id, "writer": proc.pid,
                 "expected_old": intent["expected_old"]["digest"], "target": intent["target"]["digest"],
                 "transitions": after[len(before):], "writer_quiescent": proc.returncode is not None,
                 "prior_native": self.assets.put(self.previous, "json"), "native": self.assets.put(current, "json")}
        self.journal.event("external", facts)
        self.previous = current
        if cut:
            self.journal.event("fault", {"seed": self.seed, "intervention": cut, "trigger": "first physical dispatch",
                               "candidate": intent["target"]["digest"], "attempt": attempt, "action_id": action_id,
                               "external": facts["native"], "released": answer})
        return answer

    def observe_exit(self):
        running = False
        for attempt, proc in self.children.items():
            code = proc.poll()
            note(self.directory / "writers" / attempt, "exit_check", writer_pid=proc.pid, returncode=code)
            running |= code is None
        return running

    def stop(self):
        # Only the owner reaps its own process. Teardown runs after the original participant clock and end snapshot.
        for attempt, proc in self.children.items():
            folder = self.directory / "writers" / attempt
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            if proc.returncode is None:
                try:
                    code = proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    code = proc.wait(timeout=5)
                note(folder, "teardown_reaped", writer_pid=proc.pid, returncode=code)
            proc.stdout.close()
