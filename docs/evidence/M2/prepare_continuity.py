"""Prepare the controlled observer fixture on the checked-out Purser candidate.

From the checkout root, run python3.11 -B -m docs.evidence.M2.prepare_continuity OUT.
OUT must be new. The live development workspace is never an input.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from purser.kernel.gate import admit
from purser.kernel.store import Store

SOURCE = Path(__file__).resolve().parents[3]
OBSERVERS = Path(__file__).parent / "observer"
AS_OF = "2026-11-18T12:10:00.000000Z"
C, ROOT, CHILD, OBLIGATION = "C-controlled", "W-controlled-root", "W-controlled-child", "O-readme"
PRINCIPAL = "principal:local"


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=SOURCE)


def candidate(commit):
    actual = git("rev-parse", "--verify", commit + "^{commit}").decode().strip()
    return {"kind": "git.commit", "algorithm": git("rev-parse", "--show-object-format").decode().strip(),
            "digest": actual}


def binding(actor, role, assignment=None, **frozen):
    return {"actor_id": actor, "role": role, "principal_id": PRINCIPAL,
            "commitment": None if role == "principal" else C, "assignment": assignment, **frozen}


class Fixture:
    """Records actual gate replies, subprocess observations and SQLite checkpoints."""

    def __init__(self, directory):
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.directory = directory
        self.path = directory / "controlled.sqlite3"
        self.store = Store(self.path)
        self.store.initialize()
        self.journal = []
        self.checkpoints = {}
        self.moment = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)

    def submit(self, who, op, *, expected="admitted"):
        n = len(self.journal) + 1
        proposal = {"v": 1, "proposal_id": f"p-fixture-{n}", "commitment": C,
                    "base_revision": self.store.snapshot()["revision"], "ops": [op], "justification": []}
        reply = admit(self.store, who, proposal, clock=lambda: self.moment + timedelta(seconds=n))
        public_binding = {key: value for key, value in who.items() if key not in ("witness", "criterion")}
        self.journal.append({"binding": public_binding, "proposal": proposal, "receipt": reply})
        if reply["disposition"] != expected:
            raise ValueError(f"{op['op']} returned {reply}, expected {expected}")
        return reply

    def checkpoint(self, name):
        snapshot = self.store.snapshot()
        destination = self.directory / (name + ".sqlite3")
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(destination)) as copied:
                source.backup(copied)
        views = {}
        cli = [sys.executable, "-B", "-m", "purser", "--db", str(destination)]
        environment = {"PATH": os.defpath, "LC_ALL": "C", "TZ": "UTC"}
        for key, args in (("status", ["status"]), ("why-child", ["why", CHILD]), ("why-root", ["why", ROOT])):
            args += ["--revision", str(snapshot["revision"]), "--as-of", AS_OF]
            views[key] = json.loads(subprocess.check_output([*cli, *args], cwd=SOURCE, env=environment))
        self.checkpoints[name] = {"revision": snapshot["revision"], "head_hash": snapshot["head_hash"],
                                  "database": destination.name, "queries": views}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    directory = parser.parse_args().output.resolve()
    fixture_config = json.loads((OBSERVERS / "fixture-v1.json").read_bytes())
    earlier, current = candidate(fixture_config["base_commit"]), candidate("HEAD")
    if earlier == current or git("status", "--porcelain"):
        raise ValueError("Use a clean, distinct Git candidate")
    test = Fixture(directory)
    owner = binding(PRINCIPAL, "principal")
    planner = binding("planner:controlled", "planner")
    closer = binding("closure:controlled", "closure")
    observer, verifier = "observer:controlled-git", "verifier:controlled-git"
    definition = b"Check git show <candidate>:README.md for the exact first line # Purser.\n"
    environment = encoded({"git": git("--version").decode().strip(), "TZ": "UTC", "LC_ALL": "C"})
    for name, data in (("definition.txt", definition), ("environment.json", environment)):
        (directory / name).write_bytes(data)
    test.submit(owner, {"op": "commitment.create", "id": C, "root": ROOT,
                        "statement": "Controlled test of fresh CLI evidence explanations."})
    hold = test.submit(owner, {"op": "commitment.request_acceptance", "id": C,
                       "expires_at": "2026-11-19T12:00:00.000000Z"}, expected="pending")
    test.submit(owner, {"op": "approval.answer", "id": hold["details"]["approval_id"],
                        "request_digest": hold["digest"], "decision": "approve"})
    test.submit(planner, {"op": "work.create", "id": CHILD, "parent": ROOT, "title": "Inspect the README"})
    test.submit(planner, {"op": "work.coverage", "id": ROOT, "kind": "all", "k": None})
    test.submit(owner, {"op": "obligation.set", "id": OBLIGATION, "work": CHILD, "version": 1,
                        "title": "The README identifies Purser", "observer": observer, "verifier": verifier,
                        "definition_sha256": sha(definition), "environment_sha256": sha(environment)})

    def assign_and_submit(label, subject):
        assignment, actor = "AS-" + label, "executor:controlled-" + label
        test.submit(planner, {"op": "work.assign", "id": assignment, "work": CHILD, "executor": actor})
        test.submit(binding(actor, "executor", assignment), {"op": "result.submit", "id": "Result-" + label,
                    "assignment": assignment, "candidate": subject})
        test.submit(planner, {"op": "run.open", "id": "Run-" + label, "obligation": OBLIGATION,
                              "result": "Result-" + label})

    def observe_and_verify(label, subject):
        run = "Run-" + label
        readme = git("show", subject["digest"] + ":README.md")
        artifact = encoded({"candidate": subject, "readme_sha256": sha(readme),
                            "first_line": readme.splitlines()[0].decode()})
        artifact_file = directory / ("observation-" + label + ".json")
        artifact_file.write_bytes(artifact)
        witness = {"candidate": subject, "definition": definition, "environment": environment, "artifact": artifact}
        test.submit(binding(observer, "observer", run, witness=witness),
                    {"op": "observation.record", "id": "Obs-" + label, "run": run})
        verdict = "pass" if json.loads(artifact_file.read_bytes())["first_line"] == "# Purser" else "fail"
        test.submit(binding(verifier, "verifier", run), {"op": "evidence.record", "id": "E-" + label,
                    "run": run, "observation": "Obs-" + label, "verdict": verdict})

    assign_and_submit("A", earlier)
    assign_and_submit("B", current)
    test.checkpoint("missing")
    observe_and_verify("A", earlier)
    test.submit(closer, {"op": "work.close", "id": CHILD}, expected="rejected")
    test.checkpoint("late")
    observe_and_verify("B", current)
    test.submit(closer, {"op": "work.close", "id": CHILD})
    test.checkpoint("qualified")
    output = {"fixture": fixture_config["id"], "as_of": AS_OF, "A": earlier, "B": current,
              "subjects": {"commitment": C, "root": ROOT, "child": CHILD, "obligation": OBLIGATION},
              "checkpoints": test.checkpoints, "journal": test.journal}
    (directory / "observations.json").write_bytes(encoded(output))
    print(json.dumps({label: value["revision"] for label, value in test.checkpoints.items()}))


if __name__ == "__main__":
    main()
